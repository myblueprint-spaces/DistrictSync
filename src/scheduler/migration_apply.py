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
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from src.scheduler import migration, migration_move
from src.scheduler.migration_move import MoveStage
from src.scheduler.schedule_check import ScheduleCheck
from src.scheduler.task_com import (
    TASK_LOGON_INTERACTIVE_TOKEN,
    TASK_LOGON_PASSWORD,
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
ROLLED_BACK = "Scheduling the nightly for this copy failed, so the old nightly was put back as it was."
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
    from src.utils.validators import validate_sis_type

    try:
        tokens = shlex.split(text or "", posix=True)
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
        ):
            return UNREADABLE_FACTS
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

    def delivery(self) -> str | None:
        """Put delivery's settings and password in the shared settings; ``None`` when done, else why not."""
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


def _uninstall(update_exe: Path) -> int:  # pragma: no cover - runs the real per-user uninstaller (lab T8)
    """``Update.exe uninstall --silent``, as THIS account, on its own copy. The exit code; -1 when it could not run."""
    from src.utils.helpers import subprocess_no_window_flags

    try:
        completed = subprocess.run(  # nosec B603 - this account's own installer, list-form, shell=False
            [str(update_exe), "uninstall", "--silent"],
            capture_output=True,
            timeout=UNINSTALL_TIMEOUT_S,
            check=False,
            creationflags=subprocess_no_window_flags(),
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
            # A refusal before the sequence (an invalid request, no COM): the read-back still decides.
            logger.warning("The elevated move refused before it started.")
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


def _delivery_step(
    report: Report, cfg: AppConfig, *, delivers: bool, collect: Collect, flags_given: bool
) -> ApplyResult | None:
    """Step 3. ``None`` when delivery is fine (or off); the refusal otherwise. Nothing here touches the nightly."""
    from src.config.app_config import AppConfig

    if not delivers:
        report.found.append(DELIVERY_OFF)
        if flags_given:
            report.left.append(DELIVERY_FLAGS_UNUSED)
        return None
    if flags_given or not cfg.sftp_is_configured() or migration._delivery_in_machine_store(cfg) is not True:
        problem = collect.delivery()
        if problem is not None:
            return _refuse(report, problem)
        report.moved.append("Delivery's settings and password were saved in the shared settings.")
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


def _old_copies(report: Report, old_program: str | None) -> None:
    """Step 5, only once the nightly runs this copy."""
    from src.update.package import PACK_ID

    own = _own_update_exe()
    if own is not None:
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
        refused = _delivery_step(report, cfg, delivers=delivers, collect=collect, flags_given=delivery_flags_given)
        if refused is not None:
            return refused
        _old_copies(report, None)
        report.next.append(NEXT_DONE)
        return _done(0, report)

    old = OldNightly.from_facts(facts)
    if isinstance(old, str):
        return _refuse(report, old)
    report.found.append(
        f"The nightly runs {migration._other_copy_kind(old.program)} every day at {old.run_time}"
        + (", waking the computer to run." if old.wake_to_run else ".")
    )
    resolved = resolve_principal(old, declared=declared, recorded=_recorded(cfg), current=_current_account())
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
    if near_run_time(old.run_time, now()):
        return _refuse(report, NEAR_RUN.format(time=old.run_time))

    # 3. Delivery, tested before the nightly is touched.
    refused = _delivery_step(report, cfg, delivers=old.command.sftp, collect=collect, flags_given=delivery_flags_given)
    if refused is not None:
        return refused

    # 4. Everything collected first, then ONE elevated child.
    password = collect.task_password(account) if kind is PrincipalKind.PASSWORD else None
    from src.scheduler.windows import _build_action_args

    new_exe = migration._this_program()
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
    )
    answer = _elevate(payload)
    del password, payload
    if answer.stage is not None or answer.check is not None:
        logger.info(
            "Migration: the elevated move ended at %s (check %s).",
            answer.stage.name if answer.stage else "unknown",
            answer.check.verdict.name if answer.check else "none",
        )
    final = _final(task_name, old.program)
    way_back = _way_back(old, kind)

    if final is Final.NEW:
        report.moved.append(MOVED.format(time=old.run_time, kind=KIND_WORDS[kind][0]))
        _record(old, kind, account)
        _old_copies(report, old.program)
        report.next.append(NEXT_DONE)
        report.way_back.append(way_back)
        return _done(0, report)

    report.left.append(OLD_COPIES_KEPT)
    if final is Final.OLD:
        if answer.kind is AnswerKind.NOT_RUN:
            report.left.insert(0, NOT_RUN.format(reason=NOT_RUN_REASONS.get(answer.reason, answer.reason)))
        elif answer.stage is MoveStage.PROBE_FAILED:
            verdict = answer.check.verdict.value if answer.check is not None else "unavailable"
            report.left.insert(0, PROBE_FAILED.format(verdict=verdict))
        elif answer.stage is MoveStage.DELETE_FAILED:
            report.left.insert(0, DELETE_FAILED)
        elif answer.stage is MoveStage.ROLLED_BACK:
            report.left.insert(0, ROLLED_BACK)
        else:
            report.left.insert(0, STILL_OLD)
        report.next.append("Fix what is named above, then run DistrictSync.exe --migrate --apply again.")
        return _done(1, report)

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
