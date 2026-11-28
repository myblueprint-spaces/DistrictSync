"""``--migrate --apply``: move the live nightly to the machine-wide install (plan 0055 D1b, DSYNC-116).

The unelevated PARENT half. ``--migrate`` alone stays D1a's read-only report (:mod:`src.scheduler.migration`); this
command is the explicit way in, and it follows plan section 5 step by step:

1. **Find** with D1a's discovery (:func:`migration.discover`), then read the nightly's own facts off Task Scheduler
   (``task_com.read_task``: program, command line, start-in folder, run time, account, logon type, wake). This covers
   a nightly made with raw ``schtasks`` and no ``AppConfig.schedule_*`` record too.
2. **Profile:** the shared profile must already be in use. ``--machine-setup`` is the ONE path that sets it up (and
   migrates the running administrator's per-user profile, ``migrate_profile``); on a machine-wide install that is
   not set up yet, ``main``'s M2b gate refuses this command before it gets here. A service account's own per-user
   profile is out of reach by design: it is reported, never copied.
3. **Delivery:** when the nightly delivers (``--sftp`` on its command line), its settings and password must be in the
   machine store (the ``--sftp-configure`` routes, or "already there"), and the ``--sftp-test`` logic must pass from
   this process, which is pinned to the shared profile. Nothing touches the nightly until it does.
4. **The nightly:** refuse within :data:`NEAR_RUN_MINUTES` of its run time; collect everything first (a stored-password
   principal's password, by ``DISTRICTSYNC_TASK_PASSWORD``, ``--task-password-stdin`` or a prompt, through
   ``main``); then ONE elevated child (:mod:`src.scheduler.migration_move`) probes, deletes, confirms missing,
   registers with the same facts and this program, or puts the old one back. The parent reads the task back after
   ANY answer (or none), and that read is what the report and the exit code follow.
5. **Old copies:** only once the nightly runs this copy. A copy installed for THIS account is removed with its own
   ``Update.exe uninstall --silent``; one installed for another account is reported with the one command; a one-file
   exe is never deleted (the district's own scripts may use it).
6. **Report**, printed and logged, naming no account and no path: Found / Moved / Left / Next, and the way back.

Exit 0 only when the nightly runs this copy and delivery passed its test (or is off); 1 otherwise (``main`` returns 2
for misuse).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.scheduler import migration, migration_move
from src.scheduler.migration_move import MoveStage
from src.scheduler.schedule_check import ScheduleCheck
from src.scheduler.task_com import (
    TASK_LOGON_INTERACTIVE_TOKEN,
    TASK_LOGON_PASSWORD,
    TASK_STATE_RUNNING,
    PrincipalKind,
    TaskFacts,
)

if TYPE_CHECKING:
    from src.config.app_config import AppConfig
    from src.scheduler.headless import PrincipalSpec

logger = logging.getLogger(__name__)

#: Refuse to start this close to the nightly's run time, either side of it (plan section 5, step 4).
NEAR_RUN_MINUTES = 30
#: The per-user uninstaller's own bound: it removes one folder and a Start menu entry.
UNINSTALL_TIMEOUT_S = 120

HEADER = "DistrictSync migration: moving the nightly to this copy."

# --- Copy. Plain words; no account name and no path from this computer (N1, review G3). ---------------------------
NOT_MACHINE_WIDE = (
    "This copy is not the machine-wide DistrictSync, so nothing was changed. Install the machine-wide DistrictSync and "
    "run this from it."
)
NEEDS_MACHINE_SETUP = (
    "This computer does not use the shared settings yet, so nothing was changed. Run DistrictSync.exe --machine-setup "
    "first (it also moves your own settings into the shared folder), then run this again."
)
NO_NIGHTLY = (
    "No nightly is scheduled, so there is nothing to move. Schedule it from this copy with DistrictSync.exe "
    "--schedule-register."
)
NIGHTLY_UNREADABLE = (
    "The nightly could not be read from Task Scheduler, so nothing was changed. Run this from an administrator "
    "command prompt."
)
UNREADABLE_FACTS = (
    "The nightly's program, command line, start-in folder, run time or account could not all be read (or it does not "
    "run once a day), so it was not moved. Remove it and schedule it again from this copy (--schedule-register)."
)
UNKNOWN_COMMAND = (
    "The nightly's command line is not one DistrictSync writes, so it was not moved. Remove it and schedule it again "
    "from this copy (--schedule-register)."
)
NEEDS_PRINCIPAL = (
    "The nightly runs as an account whose kind this computer's settings do not record, so it was not moved. Run this "
    "again with --principal password:DOMAIN\\user or --principal gmsa:DOMAIN\\name$ naming the account the nightly "
    "runs as."
)
PRINCIPAL_MISMATCH = "The --principal given is not the account the nightly runs as, so nothing was changed."
ACCOUNT_SHAPE = (
    "The nightly's account name is not in the form that kind of account needs, so nothing was changed. Check "
    "--principal (a managed service account ends with $, an account with a password does not)."
)
OTHER_SIGNED_IN = (
    "The nightly runs only while a different Windows account is signed in, so it was not moved. Run this signed in as "
    "that account, or schedule the nightly again from this copy for an account that runs while nobody is signed in."
)
UNSUPPORTED_LOGON = (
    "The nightly runs under a kind of Windows logon DistrictSync does not schedule, so it was not moved. Remove it and "
    "schedule it again from this copy (--schedule-register)."
)
NEAR_RUN = (
    "The nightly runs at {time}, within 30 minutes of now, so nothing was changed. Run this again at least 30 minutes "
    "away from that time."
)
DELIVERY_OFF = "Delivery is off for the nightly, so no delivery test was needed."
DELIVERY_FLAGS_UNUSED = "Delivery is off for the nightly, so the delivery settings given were not saved."
DELIVERY_PASSED = "The delivery test passed from this copy, with the shared settings."
DELIVERY_FAILED = (
    "The delivery test failed from this copy, so the nightly was not changed. Run DistrictSync.exe --sftp-test to see "
    "why, fix it (--sftp-configure), then run this again."
)
ALREADY_HERE = "The nightly already runs this copy."
MOVED = "The nightly now runs this copy every day at {time} as {kind}, with the same district, folders and delivery."
PROBE_FAILED = (
    "Windows could not start this copy as the nightly's account (check result: {verdict}), so the nightly was not "
    "changed. See the schedule check in the managed service accounts guide, fix it, then run this again."
)
DELETE_FAILED = "The old nightly could not be removed, so it was left exactly as it was."
ROLLED_BACK = (
    "Scheduling the nightly for this copy failed, so the old nightly was registered again: the same time, account, "
    "program and command line, with DistrictSync's own run level and limits."
)
NO_NIGHTLY_NOW = (
    "There is NO nightly now: the old one was removed and the new one could not be confirmed or registered. Put it "
    "back as the way back below says, before the next run time."
)
STILL_OLD = "The nightly was not moved; the old nightly is still in place."
ELSEWHERE_NOW = (
    "The nightly now runs a program that is neither this copy nor the old one. Open Task Scheduler to see it."
)
UNKNOWN_NOW = (
    "Whether the nightly was moved could not be read back from Task Scheduler. Run DistrictSync.exe --migrate to see "
    "which program it runs."
)
NOT_RUN = "The change did not start ({reason}), so nothing was changed."
NOT_RUN_REASONS: dict[str, str] = {
    "declined": "the Windows permission prompt was declined",
    "launch_failed": "Windows did not show the permission prompt",
    "different_account": "the permission prompt was answered by a different administrator",
    "unavailable": "the request could not be prepared; see the log",
    "refused": "the elevated step refused the request; see the log",
}
OLD_COPIES_KEPT = "Old copies were left as they are, because the nightly does not run this copy."
ONE_FILE_KEPT = (
    "The old program ({copy}) was left where it is: DistrictSync never deletes it, because your own scripts may use "
    "it. Delete it yourself once nothing uses it."
)
OWN_COPY_REMOVED = "The copy installed for this Windows account was removed with its own uninstaller."
OWN_COPY_NOT_REMOVED = (
    "The copy installed for this Windows account could not be removed (uninstaller exit code {code}). Remove it from "
    "Windows Settings > Apps."
)
OTHER_ACCOUNT_COPY = (
    "The old copy is installed for another Windows account, so it was not removed. Signed in as that account, run: "
    '"%LOCALAPPDATA%\\{pack}\\Update.exe" uninstall --silent'
)
SERVICE_PROFILE_NOT_COPIED = (
    "The nightly's account may keep its own DistrictSync settings in its own profile; they are not copied (another "
    "account's profile is out of reach by design). This copy uses the shared settings."
)
NEXT_DONE = "Run DistrictSync.exe --diagnose to confirm, and check the next nightly in Run History."
WAY_BACK = (
    "the old nightly ran {copy} every day at {time} as {kind}{wake}. To put it back by hand, schedule it again at that "
    "time for that account: from the old copy, in its Setup > Schedule; or from this copy, DistrictSync.exe "
    "--schedule-register --time {time} --principal {principal}{wake_flag}."
)

DISABLED = (
    "The nightly is switched off in Task Scheduler, so it was not moved (scheduling it again would switch it on). "
    "Switch it on, or remove it and schedule from this copy when you want it."
)
RUNNING_NOW = "The nightly is running now, so nothing was changed. Run this again once it has finished."
INPUT_MISSING = (
    "The nightly's extract folder cannot be found from this account, so nothing was changed. Check the folder, or "
    "set it again with --configure, then run this again."
)
OUTPUT_PARENT_MISSING = (
    "The folder that holds the nightly's output folder cannot be found from this account, so nothing was changed."
)
DISTRICT_UNRESOLVED = (
    "The nightly's district settings ({sis}) cannot be loaded from the shared settings, so nothing was changed. A "
    "mapping saved only in the nightly account's own profile is not copied: add it to this copy (Mapping) first."
)
OLD_PER_USER_FOREIGN = (
    "The old nightly runs a copy installed for one Windows account as a different account, so it could not be put "
    "back if the move failed, and nothing was changed. Remove it (DistrictSync.exe --schedule-remove from the old "
    "copy) and schedule it again from this copy (--schedule-register)."
)
GRANT_FAILED = (
    "The nightly's account could not be given access to the shared settings, so nothing was changed. See the log."
)
TOO_LATE = "It got too close to the nightly's run time before the old nightly was removed, so nothing was changed."
UNSETTLED_NOW = (
    "A step in Task Scheduler did not answer in time and may still finish, so what it holds now is not certain: "
    "check Task Scheduler (or run DistrictSync.exe --migrate) for the nightly before its run time, and if there is "
    "none, put it back as the way back below says."
)
UNINSTALL_SKIPPED_RUNNING = (
    "The copy installed for this Windows account was not removed, because the nightly is running now. Remove it from "
    "Windows Settings > Apps later."
)

#: After an UNSETTLED answer the parent waits this long before its read-back: the longest a timed-out step can run.
UNSETTLED_WAIT_S = 120.0

#: The kind, in words, and the ``--principal`` form a person types, with the account as a placeholder (N1).
KIND_WORDS: dict[PrincipalKind, tuple[str, str]] = {
    PrincipalKind.INTERACTIVE_TOKEN: ("the signed-in account (only while it is signed in)", "current"),
    PrincipalKind.PASSWORD: ("an account with a stored password", "password:<that account>"),
    PrincipalKind.MANAGED_SERVICE_ACCOUNT: ("a managed service account", "gmsa:<that account>"),
}


# --------------------------------------------------------------------------- #
# Pure pieces                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NightlyCommand:
    """The facts a DistrictSync nightly's command line carries (``windows._build_action_args``' shape)."""

    sis: str
    input_dir: str
    output_dir: str
    sftp: bool


_VALUE_FLAGS = ("--sis", "--input", "--output")


def parse_arguments(text: str | None) -> NightlyCommand | None:
    """The nightly's command line -> its facts, or ``None`` when it is not one this app writes. Pure and total.

    Accepted: ``[-m src.main] --sis S --input "I" --output "O" [--sftp] [--source scheduled]``, each at most once. Any
    other token is a nightly someone changed by hand, and moving it would silently drop what they added.
    """
    from src.scheduler.windows import split_command_line
    from src.utils.validators import validate_sis_type

    try:
        # CommandLineToArgvW's rules, which is how the nightly's own program reads them (review H1): POSIX shlex ate a
        # UNC path's leading backslash and every backslash of an unquoted path.
        tokens = split_command_line(text or "")
    except ValueError:
        return None
    if tokens[:2] == ["-m", "src.main"]:
        tokens = tokens[2:]
    values: dict[str, str] = {}
    sftp = source = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _VALUE_FLAGS and token not in values and i + 1 < len(tokens):
            values[token] = tokens[i + 1]
            i += 2
        elif token == "--sftp" and not sftp:  # nosec B105 - a command-line flag, not a credential
            sftp = True
            i += 1
        elif token == "--source" and not source and i + 1 < len(tokens) and tokens[i + 1] == "scheduled":  # nosec B105
            source = True
            i += 2
        else:
            return None
    if set(values) != set(_VALUE_FLAGS) or not values["--input"] or not values["--output"]:
        return None
    try:
        sis = validate_sis_type(values["--sis"])
    except ValueError:
        return None
    return NightlyCommand(sis=sis, input_dir=values["--input"], output_dir=values["--output"], sftp=sftp)


def _minutes(run_time: str) -> int:
    hour, minute = run_time.split(":")
    return int(hour) * 60 + int(minute)


def near_run_time(run_time: str, now: datetime, *, minutes: int = NEAR_RUN_MINUTES) -> bool:
    """Whether ``now`` is less than ``minutes`` from ``run_time`` on the 24-hour clock, either side, across midnight."""
    gap = abs(_minutes(run_time) - (now.hour * 60 + now.minute))
    return min(gap, 24 * 60 - gap) < minutes


def _same_account(a: str | None, b: str | None) -> bool:
    """One account, however Task Scheduler spelled it (``DOMAIN\\name`` or the bare ``name``). Case-insensitive."""
    left, right = (a or "").strip().casefold(), (b or "").strip().casefold()
    if not left or not right:
        return False
    if left == right:
        return True
    if "\\" in left and "\\" in right:
        return False
    return left.rsplit("\\", 1)[-1] == right.rsplit("\\", 1)[-1]


@dataclass(frozen=True)
class OldNightly:
    """Everything read off the old task before anything changes: what is re-registered, or put back."""

    program: str
    arguments: str
    working_dir: str
    run_time: str
    run_as: str
    logon_type: int
    wake_to_run: bool
    command: NightlyCommand

    @staticmethod
    def from_facts(facts: TaskFacts) -> OldNightly | str:
        """The facts, or the refusal copy when they are not all there."""
        if not (
            facts.action_path
            and facts.arguments
            and facts.working_dir
            and facts.run_time
            and facts.run_as
            and facts.logon_type is not None
            and facts.wake_to_run is not None
            and facts.enabled is not None
        ):
            return UNREADABLE_FACTS
        if not facts.enabled:
            return DISABLED
        command = parse_arguments(facts.arguments)
        if command is None:
            return UNKNOWN_COMMAND
        return OldNightly(
            program=facts.action_path,
            arguments=facts.arguments,
            working_dir=facts.working_dir,
            run_time=facts.run_time,
            run_as=facts.run_as,
            logon_type=facts.logon_type,
            wake_to_run=facts.wake_to_run,
            command=command,
        )


def resolve_principal(
    old: OldNightly, *, declared: PrincipalSpec | None, recorded: tuple[str, PrincipalKind] | None, current: str
) -> tuple[PrincipalKind, str] | str:
    """``(kind, account)`` to register, or the refusal copy. DECLARED, never inferred (CLAUDE.md "Scheduler").

    The logon type is Windows' own answer for the signed-in kind. A stored-password logon is a password account OR a
    managed service account, and only a RECORD (``AppConfig.schedule_*`` naming this account) or ``--principal`` may
    say which: never the ``$`` on the name (plan 0049 S-4).
    """
    if old.logon_type == TASK_LOGON_INTERACTIVE_TOKEN:
        if declared is not None and declared.kind is not PrincipalKind.INTERACTIVE_TOKEN:
            return PRINCIPAL_MISMATCH
        if not _same_account(old.run_as, current):
            return OTHER_SIGNED_IN
        return PrincipalKind.INTERACTIVE_TOKEN, current
    if old.logon_type != TASK_LOGON_PASSWORD:
        return UNSUPPORTED_LOGON
    if declared is not None:
        if declared.kind is PrincipalKind.INTERACTIVE_TOKEN or not _same_account(declared.account, old.run_as):
            return PRINCIPAL_MISMATCH
        kind = declared.kind
    elif (
        recorded is not None
        and recorded[1] is not PrincipalKind.INTERACTIVE_TOKEN
        and _same_account(recorded[0] or current, old.run_as)
    ):
        kind = recorded[1]
    else:
        return NEEDS_PRINCIPAL
    from src.scheduler.task_com import validate_principal_account

    try:
        return kind, validate_principal_account(kind, old.run_as)
    except ValueError:
        return ACCOUNT_SHAPE


@dataclass
class Report:
    """What was found, what moved, what was left and why, the next step, and the way back. Names no account."""

    found: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    next: list[str] = field(default_factory=list)
    way_back: list[str] = field(default_factory=list)

    def lines(self) -> tuple[str, ...]:
        sections = (
            ("Found", self.found),
            ("Moved", self.moved),
            ("Left", self.left),
            ("Next", self.next),
            ("Way back", self.way_back),
        )
        return (HEADER, *(f"{name}: {text}" for name, items in sections for text in items))


@dataclass(frozen=True)
class ApplyResult:
    code: int
    lines: tuple[str, ...]


class Collect(Protocol):
    """The input routes ``main`` owns (it has the argv, stdin and the console)."""

    def task_password(self, account: str) -> str:
        """The nightly account's Windows password: env, ``--task-password-stdin`` or a prompt. Never logged."""
        ...

    def prepare_delivery(self) -> str | None:
        """Gather delivery's settings and password (nothing saved yet); ``None`` when ready, else why not."""
        ...

    def save_delivery(self) -> None:
        """Save what :meth:`prepare_delivery` gathered into the shared settings and the machine store."""
        ...


class AnswerKind(Enum):
    NOT_RUN = "not_run"  # the child never ran (or refused before any change)
    UNCONFIRMED = "unconfirmed"  # timed out, or no readable result: the read-back decides
    ANSWERED = "answered"  # a stage


@dataclass(frozen=True)
class ChildAnswer:
    kind: AnswerKind
    stage: MoveStage | None = None
    check: ScheduleCheck | None = None
    reason: str = ""


class Final(Enum):
    NEW = "new"
    OLD = "old"
    MISSING = "missing"
    ELSEWHERE = "elsewhere"
    UNKNOWN = "unknown"


#: What the parent's read-back found, in words (for the UNSETTLED report; it is never claimed as the result).
FINAL_WORDS: dict[Final, str] = {
    Final.NEW: "the nightly runs this copy.",
    Final.OLD: "the old nightly is in place.",
    Final.MISSING: "there is no nightly.",
    Final.ELSEWHERE: "the nightly runs a program that is neither this copy nor the old one.",
    Final.UNKNOWN: "the nightly could not be read.",
}


# --------------------------------------------------------------------------- #
# Seams (one OS call each)                                                    #
# --------------------------------------------------------------------------- #


def _in_machine_scope() -> bool:
    from src.utils import paths

    return paths.is_machine_scope()


def _current_account() -> str:
    from src.scheduler.windows import current_run_as_user

    return current_run_as_user()


def _read_task(task_name: str) -> TaskFacts:
    """Raises ``TaskComError`` (``HR_NOT_FOUND`` = no such task) or anything else when it could not be read."""
    from src.scheduler import task_com

    facts: TaskFacts = task_com.bounded(
        lambda: task_com.read_task(task_name), timeout_s=task_com.READ_TIMEOUT_S, label="read"
    )
    return facts


def _test_delivery(cfg: AppConfig) -> tuple[bool, str]:
    """The ``--sftp-test`` logic, in THIS process (pinned to the shared profile, so the machine store)."""
    from src.sftp.uploader import SFTPUploader

    if not cfg.sftp_is_configured():
        return False, "not configured"
    uploader = SFTPUploader(
        host=cfg.sftp_host, port=cfg.sftp_port, username=cfg.sftp_username, remote_path=cfg.sftp_remote_path
    )
    return uploader.test_connection()


def _own_update_exe() -> Path | None:
    from src.utils.installation import per_user_update_exe

    return per_user_update_exe()


def _old_is_per_user_install(program: str) -> bool:
    from src.scheduler.task_com import runs_from_per_user_install

    try:
        return runs_from_per_user_install(program.strip('"'))
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return False


def _install_root(program: str) -> Path | None:
    from src.update.startup import install_root

    try:
        return install_root(program.strip('"'))
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return None


def _new_program() -> Path:
    """The program the moved nightly runs: this executable as Windows started it, NOT resolved (review L3), exactly
    as ``--schedule-register`` registers it (``…\\current\\DistrictSync.exe``)."""
    return Path(sys.executable)


def _load_district(sis: str) -> object:
    """Load the nightly's district config the way the nightly will: under the shared profile (review M6). Raises."""
    from src.config.loader import load_config

    return load_config(sis)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _uninstall(update_exe: Path) -> int:
    """``Update.exe uninstall --silent``, as THIS account, on its own copy. The exit code; -1 when it could not run.

    Its environment has no password variable in it (review M5).
    """
    from src.utils.helpers import subprocess_no_window_flags
    from src.utils.secret_env import scrubbed_environ

    try:
        completed = subprocess.run(  # nosec B603 - this account's own installer, list-form, shell=False
            [str(update_exe), "uninstall", "--silent"],
            capture_output=True,
            timeout=UNINSTALL_TIMEOUT_S,
            check=False,
            creationflags=subprocess_no_window_flags(),
            env=scrubbed_environ(),
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    return completed.returncode


def _elevate(payload: dict[str, object]) -> ChildAnswer:
    """ONE elevated ``migrate_nightly`` op through the existing handshake (``elevation`` + ``windows``). Never raises.

    The password rides only the DPAPI-sealed request file; the child's argv is the two handshake paths.
    """
    from src.scheduler import elevation, windows
    from src.scheduler.elevated_apply import DIFFERENT_ACCOUNT_SENTINEL
    from src.scheduler.elevation import ElevationResult

    req_path: Path | None = None
    res_path: Path | None = None
    try:
        logger.info("Moving the nightly to this copy via one-time elevation (UAC).")
        req_path = elevation.write_request(payload)
        res_path = req_path.with_suffix(".res")
        outcome = windows.run_elevated_child(req_path, res_path, timeout_s=migration_move.CHILD_BUDGET_S)
        if outcome.result is ElevationResult.DECLINED:
            return ChildAnswer(AnswerKind.NOT_RUN, reason="declined")
        if outcome.result is ElevationResult.LAUNCH_FAILED:
            return ChildAnswer(AnswerKind.NOT_RUN, reason="launch_failed")
        if outcome.result is ElevationResult.TIMEOUT:
            return ChildAnswer(AnswerKind.UNCONFIRMED)
        result = elevation.read_result(res_path)
        if result is None:
            return ChildAnswer(AnswerKind.UNCONFIRMED)
        message = str(result.get("message", ""))
        if DIFFERENT_ACCOUNT_SENTINEL in message:
            return ChildAnswer(AnswerKind.NOT_RUN, reason="different_account")
        try:
            stage = MoveStage(message)
        except ValueError:
            from src.scheduler.elevated_apply import _MSG_CHILD_FLOOR, CHILD_REFUSALS
            from src.scheduler.task_com import MSG_COM_UNAVAILABLE

            # The floor can be written AFTER the sequence (a result-file write that failed), so it is never "not run".
            if message in (*CHILD_REFUSALS, MSG_COM_UNAVAILABLE) and message != _MSG_CHILD_FLOOR:
                # The child validates the whole request before it changes anything (review M3).
                logger.warning("The elevated move refused the request before it started.")
                return ChildAnswer(AnswerKind.NOT_RUN, reason="refused")
            logger.warning("The elevated move ended without a stage; reading the task back.")
            return ChildAnswer(AnswerKind.UNCONFIRMED)
        return ChildAnswer(AnswerKind.ANSWERED, stage=stage, check=ScheduleCheck.from_wire(result.get("check")))
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Could not prepare the move (%s).", type(exc).__name__)
        return ChildAnswer(AnswerKind.NOT_RUN, reason="unavailable")
    finally:
        windows._cleanup_handshake(req_path, res_path)  # the handshake files only; best-effort, swept within the hour


# --------------------------------------------------------------------------- #
# The ladder                                                                  #
# --------------------------------------------------------------------------- #


def _done(code: int, report: Report) -> ApplyResult:
    lines = report.lines()
    for line in lines:
        logger.info("Migration: %s", line)
    return ApplyResult(code=code, lines=lines)


def _refuse(report: Report, text: str) -> ApplyResult:
    report.left.append(text)
    return _done(1, report)


def _read(task_name: str) -> tuple[str, TaskFacts | None]:
    """``("found", facts)`` / ``("missing", None)`` (Task Scheduler ANSWERED) / ``("unknown", None)``."""
    from src.scheduler import task_com

    try:
        return "found", _read_task(task_name)
    except task_com.TaskComError as exc:
        return ("missing", None) if exc.scode == task_com.HR_NOT_FOUND else ("unknown", None)
    except Exception:  # noqa: BLE001 - unreadable is UNKNOWN, never "absent"
        return "unknown", None


def _final(task_name: str, old_program: str) -> Final:
    state, facts = _read(task_name)
    if state == "missing":
        return Final.MISSING
    if state == "unknown" or facts is None:
        return Final.UNKNOWN
    if migration._same_program(facts.action_path):
        return Final.NEW
    if migration_move._same(facts.action_path, old_program):
        return Final.OLD
    return Final.ELSEWHERE


def _delivery_needed(cfg: AppConfig, *, delivers: bool, flags_given: bool) -> bool:
    return delivers and (
        flags_given or not cfg.sftp_is_configured() or migration._delivery_in_machine_store(cfg) is not True
    )


def _delivery_test(report: Report) -> ApplyResult | None:
    """The ``--sftp-test`` logic; the refusal when it fails. Nothing here touches the nightly."""
    from src.config.app_config import AppConfig

    ok, _detail = _test_delivery(AppConfig.load())  # the detail can carry the SFTP account: printed by --sftp-test only
    if not ok:
        return _refuse(report, DELIVERY_FAILED)
    report.found.append(DELIVERY_PASSED)
    return None


def _recorded(cfg: AppConfig) -> tuple[str, PrincipalKind] | None:
    from src.scheduler.schedule_record import registered_schedule

    record = registered_schedule(
        raw_task_args=cfg.schedule_task_args,
        unattended_flag=cfg.schedule_unattended,
        raw_run_as_user=cfg.schedule_run_as_user,
        raw_run_as_kind=cfg.schedule_run_as_kind,
        raw_wake_to_run=cfg.schedule_wake_to_run,
    )
    if record.args is None or record.run_as_kind is None:
        return None
    return record.run_as_user or "", record.run_as_kind


def _old_copies(report: Report, old_program: str | None, task_name: str) -> None:
    """Step 5, only once the nightly runs this copy, and never while it is running (review M2)."""
    from src.update.package import PACK_ID

    own = _own_update_exe()
    if own is not None:
        _state, facts = _read(task_name)
        if facts is not None and facts.state == TASK_STATE_RUNNING:
            report.left.append(UNINSTALL_SKIPPED_RUNNING)
        else:
            code = _uninstall(own)
            if code == 0:
                report.moved.append(OWN_COPY_REMOVED)
            else:
                report.left.append(OWN_COPY_NOT_REMOVED.format(code=code))
    if old_program is None:
        return
    if _old_is_per_user_install(old_program):
        root = _install_root(old_program)
        if own is None or root is None or root.resolve() != own.parent.resolve():
            report.left.append(OTHER_ACCOUNT_COPY.format(pack=PACK_ID))
    else:
        report.left.append(ONE_FILE_KEPT.format(copy=migration._other_copy_kind(old_program)))


def _way_back(old: OldNightly, kind: PrincipalKind) -> str:
    words, principal = KIND_WORDS[kind]
    return WAY_BACK.format(
        copy=migration._other_copy_kind(old.program),
        time=old.run_time,
        kind=words,
        wake=", waking the computer to run" if old.wake_to_run else "",
        principal=principal,
        wake_flag=" --wake" if old.wake_to_run else "",
    )


def _deadline(run_time: str, now: datetime) -> float:
    """Epoch seconds after which the child refuses the delete: :data:`NEAR_RUN_MINUTES` before the next run."""
    hour, minute = (int(part) for part in run_time.split(":"))
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return (run_at - timedelta(minutes=NEAR_RUN_MINUTES)).timestamp()


def _folders_problem(command: NightlyCommand) -> str | None:
    """The nightly's folders, checked from this account before anything changes (review H1)."""
    try:
        if not Path(command.input_dir).is_dir():
            return INPUT_MISSING
        output = Path(command.output_dir)
        if not (output.is_dir() or output.parent.is_dir()):
            return OUTPUT_PARENT_MISSING
    except OSError:
        return INPUT_MISSING
    return None


def _not_ready(old: OldNightly, kind: PrincipalKind, account: str, current: str) -> str | None:
    """Every refusal that needs no prompt and no change, in one place (H1 folders, M6 district, M3 old copy)."""
    folders = _folders_problem(old.command)
    if folders is not None:
        return folders
    try:
        _load_district(old.command.sis)
    except Exception as exc:  # noqa: BLE001 - any failure to load is "not resolvable here"; the class is logged
        logger.warning("Migration: the nightly's district config did not load (%s).", type(exc).__name__)
        return DISTRICT_UNRESOLVED.format(sis=old.command.sis)
    foreign = kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT or not _same_account(account, current)
    if foreign and _old_is_per_user_install(old.program):
        # The child's own floor (``_refuse_per_user_install``) would refuse the put-back: said here, in words (M3).
        return OLD_PER_USER_FOREIGN
    return None


def run(
    *,
    declared: PrincipalSpec | None,
    collect: Collect,
    delivery_flags_given: bool,
    now: Callable[[], datetime] = datetime.now,
) -> ApplyResult:
    """Move the nightly to this copy (plan section 5). Returns the exit code and the report; never raises for an OS
    answer (a ``SystemExit`` from ``collect``'s password routes is ``main``'s misuse exit, before any change)."""
    from src.config.app_config import AppConfig

    report = Report()
    cfg = AppConfig.load()
    task_name = cfg.schedule_task_name

    # 1-2. Find; the shared profile must be in use.
    findings = migration.discover(task_name)
    if not findings.machine_wide:
        return _refuse(report, NOT_MACHINE_WIDE)
    if not findings.shared_profile or not _in_machine_scope():
        return _refuse(report, NEEDS_MACHINE_SETUP)
    state, facts = _read(task_name)
    if state == "missing":
        return _refuse(report, NO_NIGHTLY)
    if state != "found" or facts is None:
        return _refuse(report, NIGHTLY_UNREADABLE)

    if migration._same_program(facts.action_path):
        report.found.append(ALREADY_HERE)
        command = parse_arguments(facts.arguments)
        delivers = command.sftp if command is not None else bool(cfg.sftp_enabled)
        if not delivers:
            report.found.append(DELIVERY_OFF)
        elif _delivery_needed(cfg, delivers=delivers, flags_given=delivery_flags_given):
            problem = collect.prepare_delivery()
            if problem is not None:
                return _refuse(report, problem)
            collect.save_delivery()
            report.moved.append("Delivery's settings and password were saved in the shared settings.")
        if delivers:
            refused = _delivery_test(report)
            if refused is not None:
                return refused
        _old_copies(report, None, task_name)
        report.next.append(NEXT_DONE)
        return _done(0, report)

    old = OldNightly.from_facts(facts)
    if isinstance(old, str):
        return _refuse(report, old)
    report.found.append(
        f"The nightly runs {migration._other_copy_kind(old.program)} every day at {old.run_time}"
        + (", waking the computer to run." if old.wake_to_run else ".")
    )
    current = _current_account()
    resolved = resolve_principal(old, declared=declared, recorded=_recorded(cfg), current=current)
    if isinstance(resolved, str):
        return _refuse(report, resolved)
    kind, account = resolved
    report.found.append(f"It runs as {KIND_WORDS[kind][0]}.")
    if kind is not PrincipalKind.INTERACTIVE_TOKEN:
        report.left.append(SERVICE_PROFILE_NOT_COPIED)
    try:
        from src.utils.account_tag import account_tag

        logger.info("Migration: the nightly's account kind is %s (account tag %s).", kind.value, account_tag(account))
    except Exception:  # noqa: BLE001 - a diagnostic line, never a stop
        logger.info("Migration: the nightly's account kind is %s.", kind.value)
    if facts.state == TASK_STATE_RUNNING:
        return _refuse(report, RUNNING_NOW)
    if near_run_time(old.run_time, now()):
        return _refuse(report, NEAR_RUN.format(time=old.run_time))
    problem = _not_ready(old, kind, account, current)
    if problem is not None:
        return _refuse(report, problem)

    # 3-4. Collect EVERYTHING before anything is saved (review L4): delivery's settings and password, then the
    # account's password; then save delivery and test it. The nightly is not touched until that test passes.
    delivers = old.command.sftp
    needs_delivery = _delivery_needed(cfg, delivers=delivers, flags_given=delivery_flags_given)
    if needs_delivery:
        problem = collect.prepare_delivery()
        if problem is not None:
            return _refuse(report, problem)
    password = collect.task_password(account) if kind is PrincipalKind.PASSWORD else None
    if not delivers:
        report.found.append(DELIVERY_OFF)
        if delivery_flags_given:
            report.left.append(DELIVERY_FLAGS_UNUSED)
    else:
        if needs_delivery:
            collect.save_delivery()
            report.moved.append("Delivery's settings and password were saved in the shared settings.")
        refused = _delivery_test(report)
        if refused is not None:
            return refused

    # The prompts and the delivery test can take minutes: read the task and the clock again (review M2).
    state, again = _read(task_name)
    if state != "found" or again is None or not migration_move._same(again.action_path, old.program):
        return _refuse(report, NIGHTLY_UNREADABLE if state != "missing" else NO_NIGHTLY)
    if again.state == TASK_STATE_RUNNING:
        return _refuse(report, RUNNING_NOW)
    moment = now()
    if near_run_time(old.run_time, moment):
        return _refuse(report, NEAR_RUN.format(time=old.run_time))

    from src.scheduler.windows import _build_action_args

    new_exe = _new_program()
    arguments, working_dir = _build_action_args(
        new_exe, old.command.sis, Path(old.command.input_dir), Path(old.command.output_dir), old.command.sftp
    )
    payload = migration_move.build_payload(
        task_name=task_name,
        run_time=old.run_time,
        user=account,
        kind=kind,
        password=password,
        wake_to_run=old.wake_to_run,
        exe=str(new_exe),
        arguments=arguments,
        working_dir=str(working_dir),
        old_exe=old.program,
        old_arguments=old.arguments,
        old_working_dir=old.working_dir,
        deadline=_deadline(old.run_time, moment),
    )
    answer = _elevate(payload)
    del password, payload
    if answer.stage is not None or answer.check is not None:
        logger.info(
            "Migration: the elevated move ended at %s (check %s).",
            answer.stage.name if answer.stage else "unknown",
            answer.check.verdict.name if answer.check else "none",
        )
    way_back = _way_back(old, kind)

    if answer.stage is MoveStage.UNSETTLED:
        # A timed-out step may still land: wait out its bound, read again, and never claim a result (review M4).
        _sleep(UNSETTLED_WAIT_S)
        report.found.append("Task Scheduler, read again after waiting: " + FINAL_WORDS[_final(task_name, old.program)])
        report.left.extend((UNSETTLED_NOW, OLD_COPIES_KEPT))
        report.next.append("Check the nightly in Task Scheduler now; run --migrate --apply again once it is settled.")
        report.way_back.append(way_back)
        return _done(1, report)

    final = _final(task_name, old.program)
    if final is Final.NEW:
        report.moved.append(MOVED.format(time=old.run_time, kind=KIND_WORDS[kind][0]))
        _record(old, kind, account)
        _old_copies(report, old.program, task_name)
        report.next.append(NEXT_DONE)
        report.way_back.append(way_back)
        return _done(0, report)

    report.left.append(OLD_COPIES_KEPT)
    if final is Final.OLD:
        stage_copy = {
            MoveStage.DELETE_FAILED: DELETE_FAILED,
            MoveStage.ROLLED_BACK: ROLLED_BACK,
            MoveStage.GRANT_FAILED: GRANT_FAILED,
            MoveStage.TOO_LATE: TOO_LATE,
        }
        if answer.kind is AnswerKind.NOT_RUN:
            first = NOT_RUN.format(reason=NOT_RUN_REASONS.get(answer.reason, answer.reason))
        elif answer.stage is MoveStage.PROBE_FAILED:
            verdict = answer.check.verdict.value if answer.check is not None else "unavailable"
            first = PROBE_FAILED.format(verdict=verdict)
        else:
            first = stage_copy.get(answer.stage, STILL_OLD) if answer.stage is not None else STILL_OLD
        report.left.insert(0, first)
        report.next.append("Fix what is named above, then run DistrictSync.exe --migrate --apply again.")
        return _done(1, report)

    if final is Final.UNKNOWN and answer.stage is MoveStage.ROLLBACK_FAILED:
        final = Final.MISSING  # the child said neither registered; an unreadable read-back does not undo that (L1)
    report.left.insert(0, {Final.MISSING: NO_NIGHTLY_NOW, Final.ELSEWHERE: ELSEWHERE_NOW}.get(final, UNKNOWN_NOW))
    report.next.append("Put the nightly back now, as the way back below says, or run --migrate --apply again.")
    report.way_back.append(way_back)
    return _done(1, report)


def _record(old: OldNightly, kind: PrincipalKind, account: str) -> None:
    """The moved nightly's five-facet record (``schedule_record``'s ONE writer), so Setup describes the live task."""
    from src.config.app_config import AppConfig
    from src.scheduler.schedule_record import TaskArgs, write_registered_record

    try:
        write_registered_record(
            AppConfig.load(),
            run_time=old.run_time,
            args=TaskArgs.of(
                input_dir=old.command.input_dir,
                output_dir=old.command.output_dir,
                sis_type=old.command.sis,
                sftp_enabled=old.command.sftp,
                run_time=old.run_time,
            ),
            account="" if kind is PrincipalKind.INTERACTIVE_TOKEN else account,
            kind=kind,
            wake_to_run=old.wake_to_run,
        )
    except OSError as exc:
        logger.warning("Migration: the moved nightly's record could not be saved (%s).", type(exc).__name__)
