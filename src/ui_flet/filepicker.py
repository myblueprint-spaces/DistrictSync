"""Flet ``ft.FilePicker`` async-service wrapper + boundary path-validation.

This module is **COUNTED** (not in the coverage-omit list): the trust-critical
logic — boundary validation that mirrors the CLI/``run_pipeline`` checks, the
idempotent service-registration guard, and the pure ``setup_state`` decision
helper — all live here and are unit-tested. Only the thin ``await``-the-dialog
glue is ``# pragma: no cover`` (it needs a live Flet event loop / native window).

**Why a boundary, not a passthrough (security):** a path returned from
``ft.FilePicker`` is **untrusted input to the core**. ``run_pipeline`` validates
``input_dir.exists()/is_dir()`` and ``sys.exit(1)``s otherwise
(``src/etl/pipeline.py:292-294``); persisting an invalid path to ``AppConfig``
would flip a *false* ``is_complete()`` and feed the pipeline a path it rejects.
So every surface validates here before persist/forward — it mirrors, never
bypasses, the core's own checks.

**FilePicker is an async SERVICE** (Flet 0.85.3 — see
``docs/FLET_1.0_CONVENTIONS.md``): register ``ft.FilePicker()`` on
``page.services`` once, then ``await get_directory_path()`` / ``pick_files()``
which *return* the result (NOT the 0.2x ``on_result`` callback). Cancel / no
service → ``None`` / ``[]`` (mirrors ``src/ui/folder_picker.py``'s tkinter
``None``-on-cancel contract — the surface degrades to manual entry, never
crashes).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Validation result — a small typed value consumed uniformly by PickerField /  #
# setup_state (so a UI line + a save-gate read the same shape).                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a picked path: ``ok`` + a plain-language ``message``."""

    ok: bool
    message: str


# --------------------------------------------------------------------------- #
# Boundary validation — split by PURITY (RC3)                                  #
#   * validate_input_dir   — PURE (read-only stat: exists + is_dir)            #
#   * validate_output_dir  — PURE-STRUCTURAL (parent exists + is_dir)          #
#   * check_writable       — EFFECTFUL (probes the filesystem; NOT "pure")     #
# Paths are resolved first (``Path.resolve()``) to normalize. UNC paths        #
# (``\\\\server\\share``) and symlinks are ACCEPTED — district servers use     #
# them, and ``resolve()`` follows symlinks to a real target before the stat.   #
# --------------------------------------------------------------------------- #
def validate_input_dir(path: str) -> ValidationResult:
    """PURE: an input GDE folder must EXIST and be a DIRECTORY.

    Mirrors ``run_pipeline``'s ``input_dir.exists() and input_dir.is_dir()``
    boundary (``src/etl/pipeline.py:292``). Rejects a missing path AND a path
    that points at a file. Read-only stat — no side effects.
    """
    if not path or not path.strip():
        return ValidationResult(False, "Choose the folder that holds your MyEd BC extract files.")
    resolved = Path(path).resolve()
    if not resolved.exists():
        return ValidationResult(False, "That folder doesn't exist. Pick an existing folder.")
    if not resolved.is_dir():
        return ValidationResult(False, "That's a file, not a folder. Pick the folder it lives in.")
    return ValidationResult(True, "Looks good — this folder is ready to read.")


def validate_output_dir(path: str) -> ValidationResult:
    """PURE-STRUCTURAL: the output folder's PARENT must exist and be a directory.

    The output folder itself may not exist yet (the loader creates it), so we
    validate the *parent* is a real directory — which rejects a path whose
    parent is a file (an impossible location). This is read-only stat; the real
    write guarantee is the loader's backup-and-restore atomic ``save_all`` (see
    ``check_writable``'s TOCTOU note), not a probe here.
    """
    if not path or not path.strip():
        return ValidationResult(False, "Choose where DistrictSync should write the output CSV files.")
    resolved = Path(path).resolve()
    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        return ValidationResult(False, "That location isn't valid — its parent folder doesn't exist.")
    if resolved.exists() and not resolved.is_dir():
        return ValidationResult(False, "That's a file, not a folder. Pick a folder to write into.")
    return ValidationResult(True, "Looks good — output will be written here.")


def check_writable(path: str) -> bool:
    """EFFECTFUL: best-effort writability probe (``os.access``). NOT pure.

    Touches the filesystem, so it is deliberately kept OUT of the "pure"
    validators. **TOCTOU caveat:** writable at validate-time is not a guarantee
    of writable at run-time (permissions/disk can change between the check and
    the write). The real, durable safety net is the loader's backup-and-restore
    atomic ``save_all`` (``src/etl/loader.py``), which never leaves the output
    dir torn on a mid-write failure — this probe is only an early UX signal.

    Returns ``True`` if the path (or its nearest existing ancestor) appears
    writable; ``False`` otherwise. Never raises.
    """
    try:
        resolved = Path(path).resolve()
        probe = resolved if resolved.exists() else resolved.parent
        return os.access(probe, os.W_OK)
    except OSError as exc:
        logger.warning("Writability probe failed for %r: %s", path, exc)
        return False


# --------------------------------------------------------------------------- #
# Service registration — idempotent, NON-async, UNIT-TESTED (RC5)              #
# Kept OUTSIDE the `# pragma: no cover` glue: this is the one behavioural       #
# contract worth pinning without a live window (mock page.services as a list). #
# --------------------------------------------------------------------------- #
def _ensure_picker(page: ft.Page) -> ft.FilePicker:
    """Register a module-managed ``ft.FilePicker`` on ``page.services`` ONCE.

    Idempotent: re-uses the already-registered picker on subsequent calls
    (re-appending the same service would duplicate it). Returns the picker so
    the async wrappers can ``await`` its dialog methods.
    """
    services = page.services
    for service in services:
        if isinstance(service, ft.FilePicker):
            return service
    fp = ft.FilePicker()
    if fp not in services:
        services.append(fp)
    return fp


async def pick_directory(
    page: ft.Page,
    dialog_title: str | None = None,
    initial_directory: str | None = None,
) -> str | None:
    """Open the native folder dialog; return the chosen path or ``None`` on cancel.

    The async replacement for ``src/ui/folder_picker.pick_directory()`` — returns
    a real server-side path (the UI runs on the district server). ``None`` means
    "cancelled or no picker" so the surface falls back to manual entry.
    """
    fp = _ensure_picker(page)
    return await fp.get_directory_path(  # pragma: no cover - async glue; needs a live Flet loop
        dialog_title=dialog_title,
        initial_directory=initial_directory,
    )


async def pick_files(
    page: ft.Page,
    dialog_title: str | None = None,
    initial_directory: str | None = None,
    allowed_extensions: list[str] | None = None,
    allow_multiple: bool = False,
) -> list[str]:
    """Open the native file dialog; return chosen file paths (``[]`` on cancel).

    Async, returns the files (no callback). Used by later surfaces (Convert /
    Mapping) to pick sample/extract files.
    """
    fp = _ensure_picker(page)
    files = await fp.pick_files(  # pragma: no cover - async glue; needs a live Flet loop
        dialog_title=dialog_title,
        initial_directory=initial_directory,
        allowed_extensions=allowed_extensions,
        allow_multiple=allow_multiple,
    )
    if not files:  # pragma: no cover - async glue
        return []
    return [f.path for f in files if f.path]  # pragma: no cover - async glue


# --------------------------------------------------------------------------- #
# Setup decision helper — PURE, COUNTED, TESTED                                #
# Given the three picked values + the path validators, decides: can we save?   #
# what gets persisted? It is the structural save-gate (RC3): an invalid path   #
# can NEVER reach AppConfig.save() because `can_save` is False, so the Save     #
# button is structurally disabled and `is_complete()` can't flip on bad input. #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SetupState:
    """Resolved Setup-folders state derived purely from the current selections."""

    input_result: ValidationResult
    output_result: ValidationResult
    sis_type: str
    can_save: bool


def setup_state(
    input_dir: str,
    output_dir: str,
    sis_type: str,
    *,
    validate_input: Callable[[str], ValidationResult] = validate_input_dir,
    validate_output: Callable[[str], ValidationResult] = validate_output_dir,
) -> SetupState:
    """PURE: decide whether the Setup folders selection may be saved.

    ``can_save`` is True only when BOTH paths validate AND an ``sis_type`` is
    chosen — this is the block-not-flag invariant: the Save affordance is gated
    on this, so an invalid ``input_dir``/``output_dir`` can never be persisted to
    ``AppConfig`` (which would flip a false ``is_complete()``).
    """
    input_result = validate_input(input_dir)
    output_result = validate_output(output_dir)
    can_save = input_result.ok and output_result.ok and bool(sis_type and sis_type.strip())
    return SetupState(
        input_result=input_result,
        output_result=output_result,
        sis_type=sis_type,
        can_save=can_save,
    )
