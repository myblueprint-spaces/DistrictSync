"""Boundary seam: fetch the live schedule read-back → derive its status (D4).

The ONE place that bridges the scheduler's I/O read-back (``read_schedule`` — a bounded
PowerShell subprocess) to the pure ``schedule_status`` derivation, and logs the
config-vs-reality contradiction (the durable Event-141 trace: config says scheduled but the
OS task is gone, or the task fired without recording a run). It performs subprocess I/O +
logging, so it is deliberately NOT the pure module — but it holds NO ``flet`` / page
marshalling: each surface calls :func:`probe_schedule` OFF the UI thread (via
``page.run_thread``) and injects the returned ``ScheduleStatus`` into its render, so
``nav.py`` and the pure derivations stay subprocess-free.

The WARNING log names only the config-controlled task name plus Windows' result code and its
verdict name (DSYNC-32) — never a path, credential, or any PII (the read-back's ``action_path`` is
reduced to a boolean by :func:`registered_program_moved` and never reaches the log). Plan 0046 C
adds a Windows ACCOUNT NAME to this module's inputs and that posture is UNCHANGED: ``_log_divergence`` must never gain the
account name, which is rendered ON SCREEN only.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

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
    worse than one keyed on a value the app wrote itself. The backlog item about the record being
    the only principal source is narrowed by S-3, not closed.

    **FAILS TO ``""`` ON EVERYTHING**: no record, a blank record, a case-insensitive match with the
    signed-in account, an unreadable ``AppConfig``, or a raising ``get_scheduler().run_as_user()``.
    That direction is the whole safety argument (see ``schedule_status._is_contradiction``).

    Deliberately NOT ``screens/setup.py::_keyring_owner_account``, whose ``"this account"`` fallback
    would make EVERY recorded name compare foreign and fire the suppression in the unsafe direction
    on any machine where the account resolution fails.

    ``supports_unattended`` is left at its default: only the ``run_as_user`` facet is read, and its
    ``None``-iff-``args is None`` rule does not depend on that flag. ``raw_run_as_kind`` is supplied
    because it is REQUIRED rather than because this resolver reads it (plan 0049 S-4) — the record's
    facets are atomic, and a call site allowed to omit one is how the next facet stops being written.
    """
    try:
        record = registered_schedule(
            raw_task_args=app_config.schedule_task_args,
            unattended_flag=bool(app_config.schedule_unattended),
            raw_run_as_user=app_config.schedule_run_as_user,
            raw_run_as_kind=app_config.schedule_run_as_kind,
            raw_wake_to_run=app_config.schedule_wake_to_run,
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


def _running_frozen_exe() -> str | None:
    """This process's own exe, resolved — but ONLY when frozen. ``None`` from source.

    From source ``sys.executable`` is ``python.exe``, which says nothing about where DistrictSync
    lives, so no "is it this copy?" comparison is possible and none is made.
    """
    if not getattr(sys, "frozen", False):
        return None
    return str(Path(sys.executable).resolve())


def registered_program_moved(
    action_path: str | None,
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
    running_exe: Callable[[], str | None] = _running_frozen_exe,
    case_insensitive: bool = os.name == "nt",
) -> bool:
    """Whether the task's registered program is POSITIVELY gone or is not this copy (DSYNC-32).

    The ONE producer of ``ScheduleStatus.program_moved`` — the only fact that may put "moved or
    deleted … re-register" on screen. True iff the read-back carried an ``action_path`` AND (that
    file does not exist, OR this process is the frozen app and the registered path resolves to a
    different file than ``Path(sys.executable).resolve()``). Windows paths compare
    case-insensitively.

    **Fails to ``False`` on everything** — an unknown action path, a raising existence check or
    resolver: ``False`` claims nothing, while a wrong ``True`` walks an admin into re-registering a
    task whose real fault is elsewhere (the SD60 incident: a gMSA logon failure, read as a moved exe).

    ``path_exists`` / ``running_exe`` / ``case_insensitive`` are the injectable seams, so tests never
    depend on the real exe, the real filesystem or the host OS. The path is used for this boolean
    ONLY — it is never logged, displayed or copied (bounded diagnostic output).
    """
    registered = (action_path or "").strip().strip('"')
    if not registered:
        return False
    try:
        if not path_exists(registered):
            return True
        current = running_exe()
        if not current:
            return False

        def _key(text: str) -> str:
            normalised = os.path.normpath(str(Path(text).resolve()))
            return normalised.casefold() if case_insensitive else normalised

        return _key(registered) != _key(current)
    except Exception:  # noqa: BLE001 - advisory: an unprovable "moved" claims nothing
        logger.debug("Could not compare the registered program with this copy; making no claim.")
        return False


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

    ``program_moved`` (DSYNC-32) is resolved HERE, from the read-back's ``action_path`` through
    :func:`registered_program_moved`, so every view gets it without a new argument — and only for
    a task that exists (a MISSING/UNKNOWN status asserts nothing about a program it cannot see).
    """
    readback = read_schedule(task_name)
    status = derive_schedule_status(
        readback,
        hint_registered=hint_registered,
        latest_record_ts=latest_record_ts,
        foreign_account=foreign_account,
        shared_records=shared_records,
        surface=surface,
        program_moved=readback.found is True and registered_program_moved(readback.action_path),
    )
    _log_divergence(task_name, status, hint_registered=hint_registered)
    return status


def _log_divergence(task_name: str, status: ScheduleStatus, *, hint_registered: bool) -> None:
    """WARN when the read-back contradicts the config flag (the durable Event-141 trace).

    DSYNC-32: the contradiction line and the foreign-principal problem line carry Windows' result
    CODE (``0x%08X``) and the verdict NAME — the number an IT team needs, which used to be dropped.
    Bounded: the task name, a hex code, an enum name and a fixed phrase. Never an account name,
    never the registered path (``program_moved`` is logged as a fixed phrase, not the path).
    """
    verdict = status.run_result
    code = (verdict.code_hex if verdict is not None else None) or "not reported"
    name = verdict.result.name if verdict is not None else "UNREADABLE"
    moved = " The registered program is missing or is not this copy of DistrictSync." if status.program_moved else ""
    if status.state is ScheduleState.MISSING and hint_registered:
        logger.warning(
            "Scheduled task '%s' is marked registered in config but was NOT found in Windows "
            "Task Scheduler — the nightly sync will not run until it is re-registered.",
            task_name,
        )
    elif status.contradiction:
        logger.warning(
            "Scheduled task '%s' fired but DistrictSync did not record a completed run (Windows result %s, %s).%s",
            task_name,
            code,
            name,
            moved,
        )
    elif status.attention and status.state is ScheduleState.LIVE:
        logger.warning(
            "Scheduled task '%s' last run reported a problem (Windows result %s, %s).%s",
            task_name,
            code,
            name,
            moved,
        )
