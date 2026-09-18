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
PII (the read-back itself carries none into the log). Plan 0046 C adds a Windows ACCOUNT NAME to
this module's inputs and that posture is UNCHANGED: ``_log_divergence`` must never gain the
account name, which is rendered ON SCREEN only.
"""

from __future__ import annotations

import logging

from src.config.app_config import AppConfig
from src.scheduler import get_scheduler
from src.scheduler.windows import read_schedule
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus, derive_schedule_status
from src.ui_flet.setup_flow import registered_schedule
from src.ui_flet.setup_gates import principal_key

logger = logging.getLogger(__name__)


def foreign_task_account(app_config: AppConfig) -> str:
    """The RECORDED task principal when it is not the account now running; ``""`` otherwise.

    THE one resolver for plan 0046 C / A5, and the reason ``schedule_status`` and ``home_status``
    stay pure. It reads the ATOMIC record (``setup_flow.registered_schedule``) rather than
    ``AppConfig.schedule_run_as_user`` directly, so a torn ``schedule_task_args`` makes the
    principal unknown too — and unknown is ``""``, which ALARMS. Reading one facet of an atomic
    triple in isolation is exactly the drift ``RegisteredSchedule`` exists to prevent.

    It can only ever be a fact the app WROTE at a confirmed registration. Since plan 0049 S-3
    the read-back DOES carry the live task's ``run_as`` / ``logon_type``
    (``task_com.TaskFacts``), but that is a DISPLAY fact and this resolver deliberately does not
    move to it: the live read legitimately answers ``None`` (an elevated-registered task under a
    filtered token, a timed-out probe), and a suppression that flickers with a probe result is
    worse than one keyed on a value the app wrote itself. The ROADMAP item about the record being
    the only principal source is narrowed by S-3, not closed.

    **FAILS TO ``""`` ON EVERYTHING**: no record, a blank record, a case-insensitive match with the
    signed-in account, an unreadable ``AppConfig``, or a raising ``get_scheduler().run_as_user()``.
    That direction is the whole safety argument (see ``schedule_status._is_contradiction``).

    Deliberately NOT ``screens/setup.py::_keyring_owner_account``, whose ``"this account"`` fallback
    would make EVERY recorded name compare foreign and fire the suppression in the unsafe direction
    on any machine where the account resolution fails.

    ``supports_unattended`` is left at its default: only the ``run_as_user`` facet is read, and its
    ``None``-iff-``args is None`` rule does not depend on that flag.
    """
    try:
        record = registered_schedule(
            raw_task_args=app_config.schedule_task_args,
            unattended_flag=bool(app_config.schedule_unattended),
            raw_run_as_user=app_config.schedule_run_as_user,
        )
        recorded = record.run_as_user
        if not recorded:
            return ""
        current = get_scheduler().run_as_user()
        # `principal_key` is the ONE reduction every principal comparison goes through — it
        # restates `register_task`'s own equivalence, so the view and the engine cannot disagree
        # about what "a different account" IS. Blank key ⇒ the signed-in account ⇒ not foreign.
        if not principal_key(recorded, current):
            return ""
        return recorded
    except Exception:  # noqa: BLE001 - advisory: any failure means UNKNOWN, and unknown ALARMS
        logger.debug("Could not resolve the recorded task principal; treating it as not foreign.")
        return ""


def probe_schedule(
    task_name: str,
    *,
    hint_registered: bool,
    foreign_account: str,
    shared_records: bool,
    latest_record_ts: str | None = None,
    surface: str = "home",
) -> ScheduleStatus:
    """Read the real schedule, derive the tri-state status, and log any contradiction.

    Runs the bounded PowerShell read-back (``read_schedule``) and maps it to the honest
    tri-state via the pure ``derive_schedule_status``. Never raises — a failed read is UNKNOWN.
    ``surface`` (``"home"``/``"setup"``) de-circularizes the MISSING copy (finding #3).

    ``foreign_account`` is REQUIRED keyword-only and passed straight through. It stays a PARAMETER
    rather than an internal :func:`foreign_task_account` call: this probe fires on nearly every nav
    click, and a disk read per click plus an untestable seam is a worse trade than one explicit
    argument. Each caller resolves it inside the worker thread it already owns.

    ``shared_records`` (plan 0049 S-2a.1) is REQUIRED keyword-only and passed straight through too.
    Every view call site sources it from ``paths.is_machine_scope()`` — the pinned, once-per-process
    answer — for the same reason: a defaulted ``False`` would silently keep Slice C's suppressions
    (and their now-false copy) on exactly the installs machine scope exists to fix.
    """
    readback = read_schedule(task_name)
    status = derive_schedule_status(
        readback,
        hint_registered=hint_registered,
        latest_record_ts=latest_record_ts,
        foreign_account=foreign_account,
        shared_records=shared_records,
        surface=surface,
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
