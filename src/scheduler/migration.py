"""``--migrate``: what moving to the machine-wide install involves (plan 0055 D1; this is D1a, DSYNC-101).

D1a is READ-ONLY: it finds what is on the computer and prints the report, and changes nothing. Moving the live nightly
(delete the old task, confirm it is gone, register ``DistrictSync_Daily`` with the same facts and the installed
program, re-register the old one if that fails) is D1b: it depends on facts the lab confirms (L1: whether a
stored-password principal's password can be avoided, the schedule check's probe against the new path), and migration
is optional for existing partners (owner decision R3-3), so it is never pushed and must be safe at any time.

What it reads: the install kind, the nightly's own task (``task_com.read_task``: the program it runs, its account kind),
whether a copy installed for this Windows account is also present, whether this computer uses the shared profile, and
whether delivery has a password in the machine store. Every reader is guarded; the report never stops on a line.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: What the report says when the move itself is asked for (D1b is not built). Settings and delivery first, the same run
#: time and account, and the one risk said plainly (review G7).
MOVE_NOT_BUILT = (
    "Moving the nightly to this copy is not automatic yet. Before you move it, make sure this copy uses the shared "
    "settings and that DistrictSync.exe --sftp-test passes from it. Then remove the old schedule "
    "(DistrictSync.exe --schedule-remove) and schedule it again from this copy at the same time and for the same "
    "account (DistrictSync.exe --schedule-register --time HH:MM --principal ... [--wake]). Until that register succeeds "
    "there is no nightly, so do it well away from the run time."
)
#: The old program, by KIND: its path can name a person (review G3).
OTHER_COPY_PER_USER = "a copy installed for one Windows account"
OTHER_COPY_ELSEWHERE = "a copy in another folder (for example the one-file DistrictSync.exe)"


@dataclass(frozen=True)
class Findings:
    machine_wide: bool
    nightly_found: bool | None  # True / False (no task) / None (could not be read)
    nightly_program: str | None  # compared, never printed: it can name a person
    nightly_uses_this_copy: bool | None
    per_user_copy: bool
    shared_profile: bool
    delivery_in_machine_store: bool | None


def _this_program() -> Path:
    return Path(sys.executable).resolve()


def _same_program(path: str | None) -> bool | None:
    if not path:
        return None
    try:
        return Path(path.strip('"')).resolve() == _this_program()
    except OSError:
        return None


def _settings() -> object | None:
    """The settings, read WITHOUT creating a profile: before machine setup the per-user ``config.json`` is parsed
    directly (``AppConfig``'s own reader), otherwise ``AppConfig.load()``. ``None`` when unreadable."""
    try:
        from src.config.app_config import CONFIG_FILENAME, AppConfig, _config_from_bytes
        from src.scheduler.machine_setup import needs_machine_setup
        from src.utils import paths

        if needs_machine_setup():
            return _config_from_bytes((paths.per_user_data_dir() / CONFIG_FILENAME).read_bytes())
        return AppConfig.load()
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return None


def discover(task_name: str | None = None) -> Findings:
    """Read what is there. Never raises; never writes. The task name is the settings' own (review G5)."""
    settings = _settings()
    if task_name is None:
        task_name = str(getattr(settings, "schedule_task_name", "") or "DistrictSync_Daily")
    from src.utils import installation
    from src.utils.install_kind import InstallKind, install_kind

    try:
        machine = install_kind() is InstallKind.MACHINE
    except Exception:  # noqa: BLE001 - a report line, never a stop
        machine = False
    found, program = _read_nightly(task_name)
    return Findings(
        machine_wide=machine,
        nightly_found=found,
        nightly_program=program,
        nightly_uses_this_copy=_same_program(program) if found else None,
        per_user_copy=installation.per_user_copy_present(),
        shared_profile=_shared_profile(),
        delivery_in_machine_store=_delivery_in_machine_store(settings),
    )


def _read_nightly(task_name: str) -> tuple[bool | None, str | None]:
    """``(found, program)``: ``(False, None)`` only when Task Scheduler ANSWERED "no such task"; anything else unread
    is ``(None, None)``, never "absent"."""
    try:
        from src.scheduler import task_com

        facts = task_com.read_task(task_name)
    except Exception as exc:  # noqa: BLE001 - a report line, never a stop
        from src.scheduler import task_com

        return (False, None) if getattr(exc, "scode", None) == task_com.HR_NOT_FOUND else (None, None)
    return True, facts.action_path


def _shared_profile() -> bool:
    try:
        from src.scheduler import provision_session

        return provision_session.switch_state() is True
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return False


def _delivery_in_machine_store(settings: object | None) -> bool | None:
    """``None`` when delivery is not set up or this CANNOT be told (review G6: a read error is not "missing"); otherwise
    whether the shared store holds its password."""
    if settings is None or not getattr(settings, "sftp_enabled", False) or not getattr(settings, "sftp_host", ""):
        return None
    try:
        from src.sftp.secret_store import MachineSecretStore

        return MachineSecretStore().get_password(settings.sftp_host, settings.sftp_username or "") is not None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - "could not tell" is not "missing"
        return None


def report_lines(f: Findings) -> list[str]:
    """The plain report: what was found and the next step. Names no account."""
    lines = ["DistrictSync migration report (nothing was changed)."]
    if not f.machine_wide:
        lines.append(
            "This copy is not the machine-wide DistrictSync. Install the machine-wide DistrictSync and run this from it."
        )
        return lines
    lines.append(
        "Shared settings on this computer: " + ("yes" if f.shared_profile else "no (run --machine-setup first)")
    )
    if f.nightly_found is None:
        lines.append("The nightly could not be read from Task Scheduler (run as an administrator to see it).")
    elif not f.nightly_found:
        lines.append("No nightly is scheduled. Schedule it from this copy when you are ready.")
    elif f.nightly_uses_this_copy:
        lines.append("The nightly already runs this copy.")
    else:
        lines.append(f"The nightly still runs another copy: {_other_copy_kind(f.nightly_program)}.")
        lines.append(MOVE_NOT_BUILT)
    if f.per_user_copy:
        lines.append(
            "A copy installed for this Windows account is also here. Once the nightly runs this copy, remove it from "
            "Windows Settings > Apps (it is not removed for you)."
        )
    if f.delivery_in_machine_store is None and f.shared_profile:
        lines.append("Whether delivery's password is in the shared settings could not be told; run --sftp-test.")
    if f.delivery_in_machine_store is False:
        lines.append(
            "Delivery is set up, but its password is not in the shared settings yet. Run DistrictSync.exe "
            "--sftp-configure from this copy, then --sftp-test."
        )
    return lines


def needs_action(f: Findings) -> bool:
    """Whether anything is left to move (the exit code: 0 nothing left, 1 something to do)."""
    return (
        not f.machine_wide
        or not f.shared_profile
        or f.nightly_found is None  # could not be read: never "done" (review G4)
        or (f.nightly_found is True and not f.nightly_uses_this_copy)
        or f.per_user_copy
        or f.delivery_in_machine_store is False
    )


def _other_copy_kind(program: str | None) -> str:
    try:
        from src.scheduler.task_com import runs_from_per_user_install

        if program and runs_from_per_user_install(program.strip('"')):
            return OTHER_COPY_PER_USER
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return OTHER_COPY_ELSEWHERE
    return OTHER_COPY_ELSEWHERE
