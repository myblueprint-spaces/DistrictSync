"""Headless ``--schedule-register`` / ``--schedule-remove`` (plan 0055 M3, DSYNC-96).

Another CALLER of the functions the Setup/Settings Schedule press uses, never a second implementation:

* the gate is :func:`src.scheduler.register_gate.register_block` (the same rungs, the same order, G0 included);
* a foreign principal provisions this computer through :func:`provision_session.request_provision` and
  :func:`provision_session.complete_handover` (one UAC prompt), the signed-in account registers through the
  ``Scheduler`` protocol, exactly as the window does;
* the record is :func:`src.scheduler.schedule_record.write_registered_record` / ``clear_registered_record``.

The run uses the SAVED settings (district, folders, delivery): the command schedules what this install already converts,
the same as the window's Schedule button. It refuses, with what to do, when they are not complete.

The password arrives as an argument from ``main`` (``DISTRICTSYNC_TASK_PASSWORD``, ``--task-password-stdin`` or a prompt);
it goes only into the ``Principal`` handed to the engine, never into a message, a log line or ``AppConfig``.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from src.config.app_config import AppConfig
from src.scheduler import get_scheduler
from src.scheduler.messages import ABSENT_TASK_MARKERS
from src.scheduler.register_gate import RegisterBlock, ScheduleAccountFacts, principal_key, register_block
from src.scheduler.schedule_record import (
    TaskArgs,
    clear_registered_record,
    registered_schedule,
    write_registered_record,
)
from src.scheduler.task_com import Principal, PrincipalKind
from src.utils.validators import validate_run_time

logger = logging.getLogger(__name__)

PRINCIPAL_CURRENT = "current"
PRINCIPAL_GMSA = "gmsa"
PRINCIPAL_PASSWORD = "password"  # nosec B105 - the --principal keyword, not a secret


@dataclass(frozen=True)
class PrincipalSpec:
    """``--principal current | gmsa:<DOMAIN\\name$> | password:<DOMAIN\\user>``, parsed. ``account`` "" = signed in."""

    kind: PrincipalKind
    account: str

    @property
    def needs_password(self) -> bool:
        return self.kind is PrincipalKind.PASSWORD


def parse_principal(text: str) -> PrincipalSpec:
    """Parse ``--principal``. Raises ``ValueError`` with a message fit to print."""
    value = (text or "").strip()
    if value.lower() == PRINCIPAL_CURRENT:
        return PrincipalSpec(PrincipalKind.INTERACTIVE_TOKEN, "")
    kind_text, sep, account = value.partition(":")
    account = account.strip()
    if sep and account:
        if kind_text.strip().lower() == PRINCIPAL_GMSA:
            return PrincipalSpec(PrincipalKind.MANAGED_SERVICE_ACCOUNT, account)
        if kind_text.strip().lower() == PRINCIPAL_PASSWORD:
            return PrincipalSpec(PrincipalKind.PASSWORD, account)
    raise ValueError("--principal must be 'current', 'gmsa:DOMAIN\\name$' or 'password:DOMAIN\\user'.")


#: What each gate rung means on the command line. Every member has a line (pinned by a test).
GATE_COPY: dict[RegisterBlock, str] = {
    RegisterBlock.INCOMPLETE: (
        "The district and folders are not set up yet. Finish them in DistrictSync's Setup, then schedule."
    ),
    RegisterBlock.RUN_TIME: "Give a run time with --time HH:MM.",
    RegisterBlock.INSTALL_NOT_SHAREABLE: (
        "This copy is installed for one Windows account only, so another account cannot run it. Install the "
        "machine-wide DistrictSync and schedule from it."
    ),
    RegisterBlock.ACCOUNT_SHAPE: "That account name is not in the form this kind of account needs.",
    RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE: (
        "A nightly is already scheduled under a different account. Run --schedule-remove first, then schedule again."
    ),
    RegisterBlock.DELIVERY_SECRET_UNREADABLE: (
        "Delivery is set up but its saved password cannot be read here, so it could not be handed to the shared "
        "settings. Run --sftp-configure again, then schedule."
    ),
    RegisterBlock.ACCOUNT_NEEDS_PASSWORD: (
        "That account needs its Windows password: set DISTRICTSYNC_TASK_PASSWORD, pipe it with --task-password-stdin, "
        "or type it when asked."
    ),
}


def _install_not_shareable() -> bool:
    from src.scheduler.task_com import runs_from_per_user_install

    try:
        return runs_from_per_user_install(sys.executable)
    except Exception:  # noqa: BLE001 - a gate input never breaks the command; the engine floor still refuses
        return False


def _current_account() -> str:
    try:
        return get_scheduler().run_as_user()
    except Exception:  # noqa: BLE001 - the gate reads "" as the signed-in account
        return ""


def _record(cfg: AppConfig):
    return registered_schedule(
        raw_task_args=cfg.schedule_task_args,
        unattended_flag=cfg.schedule_unattended,
        raw_run_as_user=cfg.schedule_run_as_user,
        raw_run_as_kind=cfg.schedule_run_as_kind,
        raw_wake_to_run=cfg.schedule_wake_to_run,
        supports_unattended=get_scheduler().supports_unattended_password,
    )


def register(*, run_time: str, spec: PrincipalSpec, password: str | None, wake_to_run: bool) -> tuple[int, str]:
    """Register the nightly. Returns ``(exit_code, message)``: 0 registered, 1 refused or failed."""
    from src.scheduler import provision_session

    cfg = AppConfig.load()
    scheduler = get_scheduler()
    current = _current_account()
    typed = spec.account
    password = password or None
    if spec.kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
        password = None
    record = _record(cfg)
    facts = ScheduleAccountFacts(
        typed=typed,
        current=current,
        password_supplied=bool(password),
        recorded=record.run_as_user,
        schedule_registered=bool(cfg.schedule_registered),
        kind=spec.kind,
    )
    foreign = scheduler.supports_unattended_password and (
        principal_key(typed, current) != "" or spec.kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT
    )
    unreadable = foreign and provision_session.delivery_secret_unreadable(
        enabled=bool(cfg.sftp_enabled), host=cfg.sftp_host or "", username=cfg.sftp_username or ""
    )
    block = register_block(
        cfg.is_complete(),
        run_time,
        account=facts,
        delivery_secret_unreadable=unreadable,
        install_not_shareable=_install_not_shareable(),
    )
    if block is not RegisterBlock.NONE:
        logger.info("Headless schedule refused by the gate: %s", block.name)
        return 1, GATE_COPY[block]
    try:
        validate_run_time(run_time)
    except ValueError as exc:
        return 1, f"The run time is not valid: {exc}"

    args = TaskArgs.of(
        input_dir=cfg.input_dir,
        output_dir=cfg.output_dir,
        sis_type=cfg.sis_type,
        sftp_enabled=cfg.sftp_enabled,
        run_time=run_time,
    )
    try:
        principal = Principal(kind=spec.kind, user=typed, password=password)
    except ValueError:
        return 1, GATE_COPY[RegisterBlock.ACCOUNT_SHAPE]

    def _persist() -> None:
        write_registered_record(
            cfg if not foreign else AppConfig.load(),  # after a handover, the SHARED config.json
            run_time=run_time,
            args=args,
            account=typed,
            kind=spec.kind,
            wake_to_run=wake_to_run,
        )

    try:
        return _dispatch(cfg, scheduler, foreign, principal, run_time, wake_to_run, _persist)
    except Exception as exc:  # noqa: BLE001 - the window's contract: a fixed line, the exception TYPE logged, no traceback
        logger.error("Headless schedule failed: %s", type(exc).__name__)
        return 1, "The nightly was not scheduled because of an unexpected error; see the log."


#: The window's "Scheduled — logged-on only" fact, on the command line (an unattended task needs an account password).
LOGGED_ON_ONLY_NOTE = (
    "This nightly runs only while that Windows account is signed in. To run it when nobody is signed in, schedule it "
    "with --principal password:DOMAIN\\user or gmsa:DOMAIN\\name$."
)


def _dispatch(cfg, scheduler, foreign, principal, run_time, wake_to_run, persist) -> tuple[int, str]:
    from src.scheduler import provision_session

    if not foreign:
        ok, message = scheduler.register(
            task_name=cfg.schedule_task_name,
            exe_path=Path(sys.executable),
            sis_type=cfg.sis_type,
            input_dir=Path(cfg.input_dir),
            output_dir=Path(cfg.output_dir),
            run_time=run_time,
            sftp=bool(cfg.sftp_enabled),
            principal=principal,
            wake_to_run=wake_to_run,
        )
        if not ok:
            return 1, f"The nightly was not scheduled: {message}"
        persist()
        done = f"Scheduled: DistrictSync runs every day at {run_time}."
        if scheduler.supports_unattended_password and principal.kind is PrincipalKind.INTERACTIVE_TOKEN:
            done += "\n" + LOGGED_ON_ONLY_NOTE  # the window's downgrade warning: never a silent logged-on-only task
        return 0, done

    attempt = provision_session.request_provision(
        cfg.schedule_task_name,
        Path(sys.executable),
        cfg.sis_type,
        Path(cfg.input_dir),
        Path(cfg.output_dir),
        run_time,
        bool(cfg.sftp_enabled),
        sftp_host=cfg.sftp_host or "",
        sftp_username=cfg.sftp_username or "",
        principal=principal,
        wake_to_run=wake_to_run,
    )
    provisioned = attempt.outcome is provision_session.ProvisionOutcome.PROVISIONED
    handover = provision_session.complete_handover(
        persist=persist if provisioned else (lambda: None), reenter=lambda: None
    )
    if handover.refused is not None:
        return 1, f"The shared settings folder is not usable ({handover.refused.value}); see the log."
    if not provisioned:
        detail = attempt.outcome.value
        if attempt.step is not None:
            detail += f" at step '{attempt.step.value}'"
        if attempt.icacls_exit is not None:
            detail += f", Windows permissions code {attempt.icacls_exit}"
        return 1, f"The nightly was not scheduled ({detail}); see the log."
    if not handover.handed_over:
        # The window's SCOPE_SWITCH_UNCONFIRMED: the child said done, but this computer cannot be shown to use the shared
        # settings, so the record stayed in this account's settings.
        return 1, (
            f"The nightly was scheduled for {run_time}, but this computer could not be confirmed to use the shared "
            "settings. Run DistrictSync.exe --diagnose."
        )
    lines = [f"Scheduled: DistrictSync runs every day at {run_time} as the account given, using the shared settings."]
    if attempt.check is not None:
        # Advisory, as in the window: the nightly IS registered and recorded, so the exit code stays 0.
        verdict = "passed" if attempt.check.passed else f"did not pass ({attempt.check.verdict.value}); see the log"
        lines.append(
            f"Warning: the account's test run {verdict}."
            if not attempt.check.passed
            else f"The account's test run {verdict}."
        )
    return 0, "\n".join(lines)


def remove() -> tuple[int, str]:
    """Remove the nightly (and a retired gMSA's folder access). 0 when no nightly is left, 1 otherwise."""
    from src.scheduler import provision_session
    from src.utils import paths

    cfg = AppConfig.load()
    scheduler = get_scheduler()
    record = _record(cfg)
    kind = record.run_as_kind or PrincipalKind.PASSWORD
    revoke_account = (
        (record.run_as_user or "").strip()
        if kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT
        and scheduler.supports_unattended_password
        and paths.is_machine_scope()
        else ""
    )
    note = ""
    if revoke_account:
        attempt = provision_session.request_removal_with_revoke(cfg.schedule_task_name, user=revoke_account, kind=kind)
        if attempt.revoke is provision_session.RevokeOutcome.FALL_BACK:
            ok, message = scheduler.delete(cfg.schedule_task_name)
        else:
            ok, message = attempt.ok, attempt.message
            if attempt.revoke is provision_session.RevokeOutcome.NOT_REVOKED:
                note = " The account's access to the shared folder could not be removed; see the log."
    else:
        ok, message = scheduler.delete(cfg.schedule_task_name)
    absent = not ok and any(marker in (message or "").lower() for marker in ABSENT_TASK_MARKERS)
    if not ok and not absent:
        return 1, f"The nightly was not removed: {message}"
    clear_registered_record(cfg)
    return 0, ("Removed: no nightly is scheduled." if ok else "No nightly was scheduled; nothing changed.") + note
