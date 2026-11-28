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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

# The folder rules (``ValidationResult``, ``validate_input_dir``, ``validate_output_dir``) live in
# ``src.config.setup_rules`` since DSYNC-109, so the headless ``--configure`` applies the same ones without
# flet; re-exported here for ``setup_state``, ``PickerField`` and the screens that import them from this module.
from src.config.setup_rules import ValidationResult as ValidationResult
from src.config.setup_rules import validate_input_dir as validate_input_dir
from src.config.setup_rules import validate_output_dir as validate_output_dir

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dialog-input sanitizing — PURE-ish (read-only stat), COUNTED, TESTED         #
# The native dialog is the one consumer that CANNOT take an arbitrary stored   #
# path: it crashes the whole Flet session on a malformed initial folder.       #
# --------------------------------------------------------------------------- #
def sanitize_initial_directory(path: str | None) -> str | None:
    """Reduce a stored path to one the native dialog provably accepts, else ``None``.

    The native Windows dialog CRASHES the Flet session on a bad
    ``initial_directory``: ``SHCreateItemFromParsingName`` rejects forward-slash
    paths with ``0x80070057`` (E_INVALIDARG) and nonexistent paths with
    ``0x80070002``, and the exception escapes the event handler. So:
    ``Path.resolve()`` first (normalizes ``/`` → ``\\`` on Windows and collapses
    ``.``/``..``; UNC ``\\\\server\\share`` paths survive ``resolve()`` — district
    servers use them, see the module docstring), then require an existing
    DIRECTORY (a missing path or a file → ``None``, so the dialog opens at the
    OS default — never crashes). Never raises: any ``OSError``/``ValueError``
    from ``resolve()``/``is_dir()`` also → ``None``.
    """
    if not path or not path.strip():
        return None
    try:
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            return None
        return str(resolved)
    except (OSError, ValueError):
        return None


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
    initial_directory = sanitize_initial_directory(initial_directory)
    try:
        return await fp.get_directory_path(  # pragma: no cover - async glue; needs a live Flet loop
            dialog_title=dialog_title,
            initial_directory=initial_directory,
        )
    except Exception as exc:
        # Defense-in-depth: a Dart-side dialog failure degrades to the cancel
        # contract ("no folder chosen"), never a session crash.
        logger.warning("Native file dialog failed: %s", exc)
        return None


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
    initial_directory = sanitize_initial_directory(initial_directory)
    try:
        files = await fp.pick_files(  # pragma: no cover - async glue; needs a live Flet loop
            dialog_title=dialog_title,
            initial_directory=initial_directory,
            allowed_extensions=allowed_extensions,
            allow_multiple=allow_multiple,
        )
    except Exception as exc:
        # Defense-in-depth: a Dart-side dialog failure degrades to the cancel
        # contract ("no files chosen"), never a session crash.
        logger.warning("Native file dialog failed: %s", exc)
        return []
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
