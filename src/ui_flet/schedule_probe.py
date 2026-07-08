"""Boundary seam: fetch the live schedule read-back → derive its status (D4).

The ONE place that bridges the scheduler's I/O read-back (``read_schedule`` — a bounded
PowerShell subprocess) to the pure ``schedule_status`` derivation, and logs the
config-vs-reality contradiction (the durable Event-141 trace: config says scheduled but the
OS task is gone, or the task fired without recording a run). It performs subprocess I/O +
logging, so it is deliberately NOT the pure module — but it holds NO ``flet`` / page
marshalling: each surface calls :func:`probe_schedule` OFF the UI thread (via
``page.run_thread``) and injects the returned ``ScheduleStatus`` into its render, so
``nav.py`` and the pure derivations stay subprocess-free.

The WARNING log names only the config-controlled task name — never a path, credential, or any
PII (the read-back itself carries none into the log).
"""

from __future__ import annotations

import logging

from src.scheduler.windows import read_schedule
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus, derive_schedule_status

logger = logging.getLogger(__name__)


def probe_schedule(
    task_name: str,
    *,
    hint_registered: bool,
    latest_record_ts: str | None = None,
) -> ScheduleStatus:
    """Read the real schedule, derive the tri-state status, and log any contradiction.

    Runs the bounded PowerShell read-back (``read_schedule``) and maps it to the honest
    tri-state via the pure ``derive_schedule_status``. Never raises — a failed read is UNKNOWN.
    """
    readback = read_schedule(task_name)
    status = derive_schedule_status(
        readback,
        hint_registered=hint_registered,
        latest_record_ts=latest_record_ts,
    )
    _log_divergence(task_name, status, hint_registered=hint_registered)
    return status


def _log_divergence(task_name: str, status: ScheduleStatus, *, hint_registered: bool) -> None:
    """WARN when the read-back contradicts the config flag (the durable Event-141 trace)."""
    if status.state is ScheduleState.MISSING and hint_registered:
        logger.warning(
            "Scheduled task '%s' is marked registered in config but was NOT found in Windows "
            "Task Scheduler — the nightly sync will not run until it is re-registered.",
            task_name,
        )
    elif status.contradiction:
        logger.warning(
            "Scheduled task '%s' fired but DistrictSync did not record a completed run — "
            "the app may have been moved or deleted from its registered location.",
            task_name,
        )
