"""Convert-screen output-folder visibility + run/deliver gates — the path-BEARING trust logic.

COUNTED (not coverage-omitted): the questions the Convert surface must answer honestly —
"where will my files go?", "may this run fire?", "may I deliver what's on disk, and how
fresh is it?", and "open that folder" — are decided here, unit-tested and single-sourced.
The button's ``disabled`` state and the pre-run caption both read these predicates, so no
silent fallback can creep back in (D9/D10): no district → no alphabetical ``configs[0]``
guess; no output folder → no quiet write into the *input* folder.

**Deliver-from-disk (0034 Slice 2):** the standalone "Deliver the files in your output
folder" action gates through :func:`standalone_deliver_state` (hidden with nothing to
deliver / no delivery setup; a calm route-to-Setup state when only the credential is
missing) and carries the honest vintage line from :func:`freshness_fact` over
:func:`newest_output_csv_mtime_iso` — the admin always knows how old the files that
would ship are.

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
from datetime import datetime
from enum import Enum
from pathlib import Path

from src.ui_flet.humanize import friendly_timestamp

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


class DeliverReadiness(Enum):
    """The standalone deliver-from-disk affordance's gated state (0034 Slice 2).

    - ``HIDDEN`` — nothing to offer: delivery isn't set up, or there are no committed
      output CSVs to send (an action with nothing to act on hides, mirroring the
      post-run deliver card's show-only-when-deliverable pattern).
    - ``NEEDS_CREDENTIAL`` — delivery is set up and files exist, but no password is
      stored/readable for this account → the calm route-to-Setup card (the existing
      ``_delivery_not_ready_card`` precedent), never a button that would instantly fail.
    - ``READY`` — the deliver action shows, freshness-labelled.
    """

    HIDDEN = "hidden"
    NEEDS_CREDENTIAL = "needs_credential"
    READY = "ready"


def standalone_deliver_state(
    *,
    sftp_configured: bool,
    credential_present: bool,
    csvs_present: bool,
) -> DeliverReadiness:
    """The standalone deliver gate: SFTP configured AND files on disk AND a readable credential.

    Single-sources the decision the Convert screen renders (pure — the view supplies the
    three facts). Ordering matters for honesty: with no delivery setup or nothing to
    deliver the affordance HIDES entirely; only a missing credential earns the explanatory
    not-ready state (it is one Setup visit away from working).
    """
    if not sftp_configured or not csvs_present:
        return DeliverReadiness.HIDDEN
    if not credential_present:
        return DeliverReadiness.NEEDS_CREDENTIAL
    return DeliverReadiness.READY


def _top_level_csvs(output_dir: str | None) -> list[Path]:
    """The committed top-level ``*.csv`` files in the output folder (TOTAL — never raises).

    Top-level only (non-recursive), mirroring ``SFTPUploader.upload_csvs``'s glob exactly —
    so this predicate can never claim files that wouldn't ship (``archive_<ts>/`` /
    ``.bak_<ts>/`` contents are invisible to both). A blank/unreadable folder → ``[]``.
    """
    target = (output_dir or "").strip()
    if not target:
        return []
    try:
        return [p for p in Path(target).glob("*.csv") if p.is_file()]
    except OSError:
        return []


def output_csvs_present(output_dir: str | None) -> bool:
    """Whether the output folder holds at least one committed top-level CSV to deliver."""
    return bool(_top_level_csvs(output_dir))


def newest_output_csv_mtime_iso(output_dir: str | None) -> str:
    """The newest top-level output CSV's mtime as an ISO string (``""`` when none/unreadable).

    The honest "when were these files last built" fact for deliver-from-disk — derived
    from what is actually on disk, never from a run record (the on-disk set is whatever
    the LAST committed build wrote, which may predate any record). TOTAL: a missing/
    unreadable folder or an unstattable file degrades to ``""`` (→ "recently" downstream).
    """
    newest = 0.0
    for path in _top_level_csvs(output_dir):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    if newest <= 0:
        return ""
    return datetime.fromtimestamp(newest).isoformat(timespec="seconds")


def freshness_fact(mtime_iso: str, *, now: datetime | None = None) -> str:
    """The plain-language vintage line for the deliver card + confirm dialog.

    "Files last built 2 hours ago." — built on ``humanize.friendly_timestamp`` (TOTAL:
    an empty/unparseable ``mtime_iso`` reads "recently", never a raw string or a crash).
    ``now`` is the test seam, threaded straight through.
    """
    return f"Files last built {friendly_timestamp(mtime_iso, now=now)}."


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
