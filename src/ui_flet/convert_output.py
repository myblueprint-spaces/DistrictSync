"""Convert-screen output-folder visibility + run-gate — the path-BEARING trust logic.

COUNTED (not coverage-omitted): the three questions the Convert surface must answer
honestly — "where will my files go?", "may this run fire?", and "open that folder" —
are decided here, unit-tested and single-sourced. The button's ``disabled`` state and
the pre-run caption both read these predicates, so no silent fallback can creep back in
(D9/D10): no district → no alphabetical ``configs[0]`` guess; no output folder → no
quiet write into the *input* folder.

**Why a SEPARATE module from ``convert_result``:** ``ConvertResult`` is the PII-free
result model and must stay *path-free* (a roster path can never enter a summary object).
The output-folder path, by contrast, is app-owned config (never student PII) and belongs
at the view layer — so the path-bearing decisions live HERE, cleanly apart from the
path-free result model. No ``flet`` import; the OS-open helper is effectful-but-mockable
(mirrors ``filepicker.check_writable``).
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - launching the OS file browser; no shell, list-form args
import sys

logger = logging.getLogger(__name__)


def output_dir_is_set(output_dir: str | None) -> bool:
    """Whether a non-blank output folder is configured (the run-gate + caption read this)."""
    return bool((output_dir or "").strip())


def can_run_convert(*, district_chosen: bool, output_dir_set: bool, input_valid: bool) -> bool:
    """The Convert run-gate: an explicit district AND a set output folder AND a valid input.

    Single-sources the gate the Convert button encodes so no silent fallback can return
    (D9/D10). Runtime single-flight (the ``JobRunner``'s ``can_start``) is an orthogonal
    view concern layered on top of this input-completeness gate.
    """
    return bool(district_chosen) and bool(output_dir_set) and bool(input_valid)


def resolved_output_caption(output_dir: str | None) -> str:
    """The pre-run, read-only caption naming where files will be written (or the unset prompt).

    Set → "Files will be written to <dir> — change it in Settings." (pre-run visibility).
    Unset → the routed blocked message "Set your output folder in Settings first …" — the
    admin changes it on the graduated **Settings** surface (Slice 8), never a silent write
    into the input folder.
    """
    if output_dir_is_set(output_dir):
        return f"Files will be written to {(output_dir or '').strip()} — change it in Settings."
    return "Set your output folder in Settings first — DistrictSync doesn't know where to write yet."


def open_folder(path: str) -> bool:
    """Open the OS file browser at ``path`` (best-effort; NEVER raises).

    Per-OS dispatch: ``os.startfile`` on Windows, ``open`` on macOS, ``xdg-open`` on Linux.
    ``path`` is the app-owned output folder from config — never student PII, never
    shell-interpolated (list-form args / no ``shell=True``). Returns whether the open was
    dispatched (blank path or a dispatch failure → ``False``, logged at WARNING).
    """
    target = (path or "").strip()
    if not target:
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # nosec B606 - app-owned output dir, no shell, no user interpolation
        elif sys.platform == "darwin":
            subprocess.run(["open", target], check=False)  # nosec B603 B607 - fixed cmd, list-form, no shell
        else:
            subprocess.run(["xdg-open", target], check=False)  # nosec B603 B607 - fixed cmd, list-form, no shell
    except OSError as exc:
        logger.warning("Could not open the output folder: %s", exc)
        return False
    return True
