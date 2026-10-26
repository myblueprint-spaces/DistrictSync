"""The SCHEDULE CHECK — prove Windows can start DistrictSync as the nightly's account (DSYNC-33).

**Why.** Registering the nightly under a managed service account (gMSA) succeeds even when
that account cannot run it: a missing 'Log on as a batch job' right even comes back as a
SUCCESS code (``SCHED_S_BATCH_LOGON_PROBLEM``, which pywin32 cannot surface — see
``task_com.RESULT_BATCH_LOGON_PROBLEM``), and a computer that may not retrieve the account's
password registers just as cleanly. In the field the task registered, and then every 3 AM run
silently never started DistrictSync. The app validated only the account name's SHAPE.

**What.** Inside the SAME elevated operation that registered the nightly (the ``provision``
op of ``elevated_apply`` — one UAC prompt, never a second), :func:`run_for_payload`:

1. reads two ADVISORY facts that explain a failure but never decide one —
   :func:`batch_right_state` (the account's own LSA rights) and :func:`install_state`
   (``NetQueryServiceAccount``). Either answers :attr:`RightState.UNKNOWN` /
   :attr:`InstallState.UNKNOWN` on any error: an API that could not answer is never a
   reported problem;
2. registers a one-shot CHECK task (``<task_name>_Check``) through the nightly's own
   ``RegisterParams`` — identical principal, program, start-in folder and run level — whose
   action runs the hidden :data:`CHECK_MODE_FLAG` mode with a random token;
3. starts it (``IRegisteredTask.Run``), polls ``State`` + ``LastTaskResult`` for at most
   :data:`POLL_TIMEOUT_S` (clock and sleep injected), reads the marker the check process
   leaves in the shared ``runs`` folder, and classifies the outcome into a
   :class:`CheckVerdict`;
4. ALWAYS deletes the check task and the marker (``finally``), whatever happened.

The verdict is REPORTED, never used to roll the nightly back (DECISIONS 2026-10-23, DSYNC-33):
the nightly stays registered either way.

**The hidden mode** (:func:`run_check_mode`, ``DistrictSync --schedule-check <token>``) runs AS
the account. It pins the data dir exactly as a real headless run does — so a refused shared
folder or a per-user resolution is caught — writes one tiny marker into ``runs\\`` and exits
with a code of its own. It runs no ETL, touches no delivery, writes no ``history.db``, reads no
input and opens no log sink. ``src/main.py`` recognises it in the argv pre-check beside
``--elevated-apply`` / ``--diagnose``.

**The channel back to the parent is bounded** (:meth:`ScheduleCheck.to_wire`): three enum
values and one integer — never free text, a path, stderr or an account name. ``from_wire``
re-validates it on the parent side, because the result file is plaintext.

**Scope choice:** :func:`applies_to` is the MANAGED_SERVICE_ACCOUNT kind only. A password
principal rides the identical code path (the check task would carry the password exactly as
the nightly does), but its registration already validates the credential, every existing
service-account district would gain up to a minute on each Schedule press without asking for
it, and the result surfaces' prerequisite hints are written for a gMSA. Widening it is this
one predicate plus copy.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from src.scheduler import task_com
from src.scheduler.task_com import (
    RESULT_ACCESS_DENIED,
    RESULT_BAD_DIRECTORY,
    RESULT_BATCH_LOGON_PROBLEM,
    RESULT_FILE_NOT_FOUND,
    RESULT_HAS_NOT_RUN,
    RESULT_LOGON_FAILURE,
    RESULT_PASSWORD_EXPIRED,
    RESULT_RUNNING,
    PrincipalKind,
)
from src.utils import paths

# --------------------------------------------------------------------------- #
# The hidden CLI mode's contract                                              #
# --------------------------------------------------------------------------- #

#: The argv flag ``src/main.py`` dispatches on, ABOVE its preamble (no migration, no console,
#: no log sink, no orphan sweep). Deliberately absent from ``--help``: it has no human caller.
CHECK_MODE_FLAG = "--schedule-check"

# The hidden mode's exit codes — what Task Scheduler records as the check task's
# LastTaskResult. Deliberately DISTINCT from the real run's 0-3 (a plain 0 from some other
# program at the registered path proves nothing) and from every RESULT_* HRESULT.
CHECK_EXIT_OK = 81  # the profile pinned to the shared folder and the marker was written
CHECK_EXIT_PROFILE_REFUSED = 82  # pinning the data dir refused (MachineScopeRefused, an unusable dir)
CHECK_EXIT_NOT_SHARED = 83  # it pinned, but NOT to the shared folder — the nightly would miss its settings
CHECK_EXIT_MARKER_UNWRITABLE = 84  # the shared runs folder refused the marker
CHECK_EXIT_BAD_REQUEST = 85  # the token argument was missing or malformed

#: The token is 32 lowercase hex characters (``secrets.token_hex(16)``) — it names the marker
#: file, so anything else is refused before it can reach a path.
_TOKEN_RE = re.compile(r"[0-9a-f]{32}")

CHECK_TASK_SUFFIX = "_Check"
MARKER_PREFIX = "schedule-check-"
_MARKER_SUFFIX = ".json"
_MAX_MARKER_BYTES = 4096
#: A marker left behind by a child killed mid-check is swept by the NEXT check — only files
#: carrying :data:`MARKER_PREFIX`, and only once they are this old.
_STALE_MARKER_AGE_S = 3600.0

# --- The time budget (review fix, DSYNC-33) -----------------------------------------
# The check runs inside the elevated child that ``windows.run_elevated`` KILLS at its bounded
# wait — and a killed child runs no ``finally``, so the check task and the marker would be
# left behind and a successful provision reported UNCONFIRMED. So the whole check has ONE
# deadline that every step respects, and the parent's wait is sized from these numbers
# (``windows._ELEV_TIMEOUT_S`` = provision budget + TOTAL_BUDGET_S + margin, pinned by test).
#
#: The whole of :func:`run_check` — leftover lookup, register, run, poll, delete — from its
#: first clock read. No COM call may START unless its own bound ends inside the deadline.
CHECK_DEADLINE_S = 90.0
#: Held back from that deadline for the ``finally``'s delete: the poll stops early enough
#: that the cleanup ALWAYS has its full bound.
DELETE_RESERVE_S = 15.0
#: The longest the poll itself may run (it also ends earlier if the deadline needs it to).
POLL_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 2.0
#: Per-call bound for the leftover lookup / register / run (capped by what is left).
_COM_CALL_TIMEOUT_S = 15.0
#: Per-sample bound inside the poll — short, so a trailing read cannot overrun the deadline.
POLL_READ_TIMEOUT_S = 5.0
#: Each advisory read (SID lookup + LSA rights; ``NetQueryServiceAccount``) may reach a
#: domain controller, so each runs on a bounded worker and is UNKNOWN when it runs out.
ADVISORY_CALL_TIMEOUT_S = 10.0
ADVISORY_BUDGET_S = 2 * ADVISORY_CALL_TIMEOUT_S
#: Everything :func:`run_for_payload` can spend after the nightly registered.
TOTAL_BUDGET_S = ADVISORY_BUDGET_S + CHECK_DEADLINE_S
#: The check task's one trigger STARTS in the past (so it never fires on its own) and ENDS
#: this far ahead — the expiry ``task_com.CHECK_DELETE_EXPIRED_AFTER`` deletes it after.
_TRIGGER_LOOKBACK = timedelta(days=1)
_TRIGGER_LIFETIME = timedelta(minutes=15)
_BOUNDARY_FORMAT = "%Y-%m-%dT%H:%M:%S"


def applies_to(kind: PrincipalKind) -> bool:
    """Whether a registration of ``kind`` gets the schedule check (see the module docstring)."""
    return kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT


# --------------------------------------------------------------------------- #
# The bounded vocabulary                                                      #
# --------------------------------------------------------------------------- #


class CheckVerdict(StrEnum):
    """What the check run proved. The wire value is the member VALUE — stable strings."""

    OK = "ok"  # DistrictSync started as the account, pinned the shared folder and left the marker
    # --- Windows could not start DistrictSync (a RESULT_* code; same constants as the nightly's verdict)
    LOGON_FAILURE = "logon_failure"
    BATCH_LOGON = "batch_logon"
    PASSWORD_EXPIRED = "password_expired"  # nosec B105 - an enum tag naming a Windows result, not a credential
    ACCESS_DENIED = "access_denied"
    PROGRAM_NOT_FOUND = "program_not_found"
    BAD_START_FOLDER = "bad_start_folder"
    # --- DistrictSync started, and its own check failed (one of the CHECK_EXIT_* codes)
    PROFILE_REFUSED = "profile_refused"
    NOT_SHARED = "not_shared"
    MARKER_UNWRITABLE = "marker_unwritable"
    MARKER_MISSING = "marker_missing"  # the success exit code, but no matching marker
    OTHER_RESULT = "other_result"  # any other result — carried as a code, never guessed at
    # --- The check itself could not reach an answer (INCONCLUSIVE — not a finding)
    TIMEOUT = "timeout"
    CHECK_NOT_REGISTERED = "check_not_registered"
    CHECK_NOT_STARTED = "check_not_started"
    UNAVAILABLE = "unavailable"


#: The verdicts that are NOT a finding about the account: the check could not reach an answer.
INCONCLUSIVE: frozenset[CheckVerdict] = frozenset(
    {CheckVerdict.TIMEOUT, CheckVerdict.CHECK_NOT_REGISTERED, CheckVerdict.CHECK_NOT_STARTED, CheckVerdict.UNAVAILABLE}
)


class RightState(StrEnum):
    """The account's OWN 'Log on as a batch job' rights, read from the local LSA policy."""

    GRANTED = "granted"  # SeBatchLogonRight is assigned to the account directly
    DENIED = "denied"  # SeDenyBatchLogonRight is assigned directly — definitive, it overrides any grant
    NOT_DIRECT = "not_direct"  # neither directly — it may still come through a group or policy
    UNKNOWN = "unknown"  # the policy could not be read — never reported as a problem


class InstallState(StrEnum):
    """``NetQueryServiceAccount``'s answer about the account on THIS computer."""

    INSTALLED = "installed"  # MsaInfoInstalled
    NOT_INSTALLED = "not_installed"  # MsaInfoCanInstall — Install-ADServiceAccount has not been run here
    NOT_PERMITTED = "not_permitted"  # MsaInfoCannotInstall — this computer may not use the account
    UNKNOWN = "unknown"  # any error, or an answer that could be a name-form artefact


def _member(enum_type: type[StrEnum], raw: object, fallback: StrEnum) -> StrEnum:
    """``enum_type(raw)`` for a known value, else ``fallback`` — total."""
    if isinstance(raw, str):
        with contextlib.suppress(ValueError):
            return enum_type(raw)
    return fallback


@dataclass(frozen=True)
class ScheduleCheck:
    """The check's whole result — the ONLY shape that crosses the UAC boundary for it.

    ``code`` is the check task's ``LastTaskResult`` (unsigned), or the scode of the COM call
    that failed; ``None`` when there was none. ``batch_right`` / ``installed`` default to
    UNKNOWN because "we could not find out" is the honest default for an advisory fact.
    """

    verdict: CheckVerdict
    code: int | None = None
    batch_right: RightState = RightState.UNKNOWN
    installed: InstallState = InstallState.UNKNOWN

    @property
    def passed(self) -> bool:
        return self.verdict is CheckVerdict.OK

    @property
    def inconclusive(self) -> bool:
        return self.verdict in INCONCLUSIVE

    def to_wire(self) -> dict[str, object]:
        """The bounded result-file form: three enum values and one integer, nothing else."""
        return {
            "verdict": self.verdict.value,
            "code": self.code,
            "batch_right": self.batch_right.value,
            "installed": self.installed.value,
        }

    @classmethod
    def from_wire(cls, raw: object) -> ScheduleCheck | None:
        """Re-validate the child's plaintext result. ``None`` = no check ran. TOTAL.

        An unrecognised verdict is :attr:`CheckVerdict.UNAVAILABLE`, never a guess; a code
        that is not a plain 32-bit unsigned int is dropped; unknown advisory values are UNKNOWN.
        """
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return cls(verdict=CheckVerdict.UNAVAILABLE)
        code = raw.get("code")
        valid_code = isinstance(code, int) and not isinstance(code, bool) and 0 <= code <= 0xFFFFFFFF
        return cls(
            verdict=CheckVerdict(_member(CheckVerdict, raw.get("verdict"), CheckVerdict.UNAVAILABLE)),
            code=code if valid_code else None,  # type: ignore[arg-type]
            batch_right=RightState(_member(RightState, raw.get("batch_right"), RightState.UNKNOWN)),
            installed=InstallState(_member(InstallState, raw.get("installed"), InstallState.UNKNOWN)),
        )


# --------------------------------------------------------------------------- #
# Classification (pure)                                                       #
# --------------------------------------------------------------------------- #

#: LastTaskResult -> verdict. The Windows rows key on the SAME ``task_com.RESULT_*`` constants
#: ``ui_flet.schedule_status.run_result_verdict`` classifies the nightly's own result with
#: (a parity test pins the two together); the CHECK_EXIT rows are this module's own exit codes.
_RESULT_VERDICTS: dict[int, CheckVerdict] = {
    RESULT_LOGON_FAILURE: CheckVerdict.LOGON_FAILURE,
    RESULT_BATCH_LOGON_PROBLEM: CheckVerdict.BATCH_LOGON,
    RESULT_PASSWORD_EXPIRED: CheckVerdict.PASSWORD_EXPIRED,
    RESULT_ACCESS_DENIED: CheckVerdict.ACCESS_DENIED,
    RESULT_FILE_NOT_FOUND: CheckVerdict.PROGRAM_NOT_FOUND,
    RESULT_BAD_DIRECTORY: CheckVerdict.BAD_START_FOLDER,
    CHECK_EXIT_PROFILE_REFUSED: CheckVerdict.PROFILE_REFUSED,
    CHECK_EXIT_NOT_SHARED: CheckVerdict.NOT_SHARED,
    CHECK_EXIT_MARKER_UNWRITABLE: CheckVerdict.MARKER_UNWRITABLE,
}


def classify(last_result: int | None, *, finished: bool, marker_ok: bool) -> tuple[CheckVerdict, int | None]:
    """The check task's outcome -> ``(verdict, code)``. Pure and TOTAL.

    * not finished inside the bound -> TIMEOUT (with whatever code was last read);
    * the success exit -> OK only when the marker with THIS token is there, else MARKER_MISSING
      (an exit code alone is never proof — the D5 rule the elevation handshake follows too);
    * a known code -> its verdict; anything else -> OTHER_RESULT, carrying the code.
    """
    code = None if last_result is None else last_result & 0xFFFFFFFF
    if not finished:
        return CheckVerdict.TIMEOUT, code
    if code == CHECK_EXIT_OK:
        return (CheckVerdict.OK if marker_ok else CheckVerdict.MARKER_MISSING), code
    if code is None:
        return CheckVerdict.OTHER_RESULT, None
    return _RESULT_VERDICTS.get(code, CheckVerdict.OTHER_RESULT), code


def is_finished(facts: task_com.CheckTaskFacts | None) -> bool:
    """Whether the check task has run to an end: not queued/running, and a real last result.

    ``SCHED_S_TASK_HAS_NOT_RUN`` (the state right after registration, and for a moment after
    ``Run``) and ``SCHED_S_TASK_RUNNING`` are not ends.
    """
    if facts is None or facts.last_result is None:
        return False
    if facts.state in (task_com.TASK_STATE_QUEUED, task_com.TASK_STATE_RUNNING):
        return False
    return facts.last_result not in (RESULT_HAS_NOT_RUN, RESULT_RUNNING)


# --------------------------------------------------------------------------- #
# Names and paths                                                             #
# --------------------------------------------------------------------------- #


def check_task_name(task_name: str) -> str:
    """``<task_name>_Check`` — validated through the same rule as every task name."""
    from src.utils.validators import validate_task_name  # noqa: PLC0415 - keeps import graph light

    return validate_task_name(f"{task_name}{CHECK_TASK_SUFFIX}")


def check_arguments(exe_path: Path, token: str) -> str:
    """The check task's action arguments — the nightly's own self-invocation rule + the mode."""
    from src.scheduler.windows import module_prefix  # noqa: PLC0415 - lazy: windows imports heavier deps

    return f"{module_prefix(exe_path)}{CHECK_MODE_FLAG} {token}"


def marker_path(runs_dir: Path, token: str) -> Path:
    """``runs/schedule-check-<token>.json``. The token is validated first — it names a file."""
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("The schedule-check token is not valid.")
    return runs_dir / f"{MARKER_PREFIX}{token}{_MARKER_SUFFIX}"


def _trigger_window(now: datetime) -> tuple[str, str]:
    """``(start, end)`` boundaries, naive-local invariant ISO (Task Scheduler's own form)."""
    return (now - _TRIGGER_LOOKBACK).strftime(_BOUNDARY_FORMAT), (now + _TRIGGER_LIFETIME).strftime(_BOUNDARY_FORMAT)


# --------------------------------------------------------------------------- #
# The hidden mode — runs AS the nightly's account                             #
# --------------------------------------------------------------------------- #


def run_check_mode(args: list[str]) -> int:
    """``DistrictSync --schedule-check <token>`` — returns one of the CHECK_EXIT_* codes.

    Runs as the account under test, launched by the check task. The data dir is pinned
    exactly as ``main._cli`` pins it for a real nightly — so whatever would stop the nightly
    from resolving the shared folder stops this too — and the ONLY write is the marker. No
    ETL, no delivery, no ``history.db``, no input, no log sink.
    """
    if len(args) != 1 or not _TOKEN_RE.fullmatch(args[0]):
        return CHECK_EXIT_BAD_REQUEST
    token = args[0]
    try:
        root = paths.pin_data_dir()
    except (OSError, RuntimeError, ValueError):
        # MachineScopeRefused is a RuntimeError; an unusable/relative override is RuntimeError/
        # ValueError. Each is exactly what would stop the nightly before it ran.
        return CHECK_EXIT_PROFILE_REFUSED
    if not paths.is_machine_scope():
        return CHECK_EXIT_NOT_SHARED
    try:
        _write_marker(marker_path(root / paths.MACHINE_RUNS_SUBDIR, token), token)
    except OSError:
        return CHECK_EXIT_MARKER_UNWRITABLE
    return CHECK_EXIT_OK


def _write_marker(path: Path, token: str) -> None:
    """Write the marker atomically (temp + ``os.replace``), as BYTES — no newline translation.

    Contents: the token, the process account (sanitised to a file-name fragment — the same
    form the per-writer log name uses), a timestamp and the app version. No secret, no path.
    """
    from src.utils.accounts import process_account, sanitise_account_for_filename  # noqa: PLC0415
    from src.utils.version import app_version  # noqa: PLC0415

    body = {
        "token": token,
        "account": sanitise_account_for_filename(process_account()),
        "at": datetime.now().isoformat(timespec="seconds"),
        "version": app_version(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(json.dumps(body).encode("utf-8"))
    os.replace(tmp, path)


def marker_ok(path: Path, token: str) -> bool:
    """Is the marker there, bounded, and carrying THIS token? Total — never raises.

    The file sits in a folder the service account can write, so it is read as untrusted data:
    capped, parsed defensively, and compared on the token alone.
    """
    try:
        if path.stat().st_size > _MAX_MARKER_BYTES:
            return False
        body = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(body, dict) and body.get("token") == token


def sweep_stale_markers(runs_dir: Path, *, now: float, max_age_s: float = _STALE_MARKER_AGE_S) -> int:
    """Delete OLD ``schedule-check-*`` files a killed check left behind. Prefix-scoped; total."""
    removed = 0
    try:
        candidates = list(runs_dir.glob(f"{MARKER_PREFIX}*"))
    except OSError:
        return 0
    for candidate in candidates:
        try:
            if now - candidate.lstat().st_mtime > max_age_s:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --------------------------------------------------------------------------- #
# The check run — in the elevated child                                       #
# --------------------------------------------------------------------------- #


class CheckCom(Protocol):
    """The four Task Scheduler operations the check needs — a seam, so tests drive fakes.

    Every call takes the ``timeout_s`` the deadline allows it; the live implementation runs
    each on a bounded worker with exactly that bound.
    """

    def register(
        self,
        params: task_com.RegisterParams,
        *,
        check_name: str,
        arguments: str,
        start_boundary: str,
        end_boundary: str,
        timeout_s: float,
    ) -> None: ...

    def run(self, check_name: str, *, timeout_s: float) -> None: ...

    def read(self, check_name: str, *, timeout_s: float) -> task_com.CheckTaskFacts: ...

    def delete(self, check_name: str, *, timeout_s: float) -> None: ...


class _LiveCom:  # pragma: no cover - drives the real Task Scheduler (Windows, elevated)
    """:class:`CheckCom` over ``task_com``, each call on a bounded worker."""

    def register(
        self,
        params: task_com.RegisterParams,
        *,
        check_name: str,
        arguments: str,
        start_boundary: str,
        end_boundary: str,
        timeout_s: float,
    ) -> None:
        task_com.bounded(
            lambda: task_com.register_check_task(
                params,
                check_name=check_name,
                arguments=arguments,
                start_boundary=start_boundary,
                end_boundary=end_boundary,
            ),
            timeout_s=timeout_s,
            label="check-register",
        )

    def run(self, check_name: str, *, timeout_s: float) -> None:
        task_com.bounded(lambda: task_com.run_task_now(check_name), timeout_s=timeout_s, label="check-run")

    def read(self, check_name: str, *, timeout_s: float) -> task_com.CheckTaskFacts:
        facts: task_com.CheckTaskFacts = task_com.bounded(
            lambda: task_com.read_check_task(check_name), timeout_s=timeout_s, label="check-read"
        )
        return facts

    def delete(self, check_name: str, *, timeout_s: float) -> None:
        task_com.bounded(lambda: task_com.delete_task_by_name(check_name), timeout_s=timeout_s, label="check-delete")


def _scode(exc: BaseException) -> int | None:
    """The HRESULT a failed COM step carried, when it carried one."""
    scode = getattr(exc, "scode", None)
    return scode & 0xFFFFFFFF if isinstance(scode, int) else None


def is_own_check_task(facts: task_com.CheckTaskFacts) -> bool:
    """Is an existing task by the check's name one of OURS (a leftover)? Its action says so."""
    return CHECK_MODE_FLAG in (facts.arguments or "").split()


def _ran_since(last_run: str | None, run_at: datetime) -> bool:
    """Did Windows record a run at or after OUR ``Run`` call? Total — unparseable is ``False``."""
    if not last_run:
        return False
    try:
        return datetime.fromisoformat(last_run) >= run_at
    except ValueError:
        return False


def _sample(com: CheckCom, check_name: str) -> task_com.CheckTaskFacts | None:
    """One bounded poll sample, or ``None`` when the read failed. Total."""
    try:
        return com.read(check_name, timeout_s=POLL_READ_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - a failed read is a missed sample, not a verdict
        return None


def run_check(
    params: task_com.RegisterParams,
    *,
    runs_dir: Path,
    com: CheckCom,
    token: str,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
    sweep_stale: bool,
    deadline_s: float = CHECK_DEADLINE_S,
) -> tuple[CheckVerdict, int | None]:
    """Look for a leftover, register, run, poll, classify — inside ONE deadline. Never raises.

    **The deadline.** Every COM call gets ``min(its cap, time left before the delete
    reserve)`` and none starts without time to finish; the poll stops before a sleep plus a
    full sample would cross that line; and the ``finally``'s delete always gets
    :data:`DELETE_RESERVE_S`. So the whole call ends within ``deadline_s`` of its first clock
    read (plus the trivial file work), which is what the parent's elevation wait is sized on.

    **The name.** ``<task_name>_Check`` is looked up BEFORE registering, because
    ``TASK_CREATE_OR_UPDATE`` would otherwise overwrite whatever holds that name. A task whose
    action carries :data:`CHECK_MODE_FLAG` is our own leftover (a killed child) and is deleted
    first, so its stale ``LastTaskResult`` is gone; anything else is SOMEBODY ELSE'S and is not
    touched — not registered over, and not deleted by the ``finally`` — and the check reports
    CHECK_NOT_REGISTERED. A lookup that cannot answer is treated the same way: never overwrite
    a task we could not identify.

    **Fresh results only.** A sample counts once the task has been seen queued/running after
    our ``Run``, or its ``LastRunTime`` is at or after the ``Run`` call — a result recorded
    before it can never be read as this check's.

    ``sweep_stale`` is the caller's TRUST decision for the marker sweep (the folder passed the
    machine-dir trust check); the check itself runs either way.
    """
    try:
        check_name = check_task_name(params.task_name)
        marker = marker_path(runs_dir, token)
        start_boundary, end_boundary = _trigger_window(now())
        work_end = clock() + deadline_s - DELETE_RESERVE_S
    except Exception:  # noqa: BLE001 - total by contract; nothing was registered yet, so nothing to clean
        return CheckVerdict.UNAVAILABLE, None

    def _budget(cap: float) -> float:
        """The bound the next call may have — ``0`` when it would cross the delete reserve."""
        return max(0.0, min(cap, work_end - clock()))

    owned = False  # True once the name is OURS to delete — never for somebody else's task
    try:
        if sweep_stale:
            sweep_stale_markers(runs_dir, now=time.time())

        bound = _budget(_COM_CALL_TIMEOUT_S)
        if bound <= 0:
            return CheckVerdict.TIMEOUT, None
        try:
            existing: task_com.CheckTaskFacts | None = com.read(check_name, timeout_s=bound)
        except task_com.TaskComError as exc:
            if exc.scode != task_com.HR_NOT_FOUND:
                return CheckVerdict.CHECK_NOT_REGISTERED, _scode(exc)  # cannot tell whose it is
            existing = None
        except Exception as exc:  # noqa: BLE001 - cannot tell whose it is: never overwrite it
            return CheckVerdict.CHECK_NOT_REGISTERED, _scode(exc)
        if existing is not None:
            if not is_own_check_task(existing):
                return CheckVerdict.CHECK_NOT_REGISTERED, None  # somebody else's task: untouched
            owned = True  # our own leftover — the finally may delete it whatever happens next
            bound = _budget(_COM_CALL_TIMEOUT_S)
            if bound <= 0:
                return CheckVerdict.TIMEOUT, None
            try:
                com.delete(check_name, timeout_s=bound)
            except Exception as exc:  # noqa: BLE001 - a leftover we cannot clear blocks a clean check
                return CheckVerdict.CHECK_NOT_REGISTERED, _scode(exc)

        bound = _budget(_COM_CALL_TIMEOUT_S)
        if bound <= 0:
            return CheckVerdict.TIMEOUT, None
        owned = True  # from here the name is ours, even if registration half-happened
        try:
            com.register(
                params,
                check_name=check_name,
                arguments=check_arguments(Path(params.exe), token),
                start_boundary=start_boundary,
                end_boundary=end_boundary,
                timeout_s=bound,
            )
        except Exception as exc:  # noqa: BLE001 - every failure to register is ONE verdict, with its code
            return CheckVerdict.CHECK_NOT_REGISTERED, _scode(exc)

        bound = _budget(_COM_CALL_TIMEOUT_S)
        if bound <= 0:
            return CheckVerdict.TIMEOUT, None
        run_at = now().replace(microsecond=0)  # LastRunTime has whole-second precision
        try:
            com.run(check_name, timeout_s=bound)
        except Exception as exc:  # noqa: BLE001 - every failure to start is ONE verdict, with its code
            return CheckVerdict.CHECK_NOT_STARTED, _scode(exc)

        poll_end = min(clock() + POLL_TIMEOUT_S, work_end)
        fresh: task_com.CheckTaskFacts | None = None
        seen_running = False
        while clock() + POLL_INTERVAL_S + POLL_READ_TIMEOUT_S <= poll_end:
            sleep(POLL_INTERVAL_S)
            sample = _sample(com, check_name)
            if sample is None:
                continue  # a failed read is a missed sample, not a verdict
            if sample.state in (task_com.TASK_STATE_QUEUED, task_com.TASK_STATE_RUNNING):
                seen_running = True
            if not (seen_running or _ran_since(sample.last_run, run_at)):
                continue  # still describing a run from before ours
            fresh = sample
            if is_finished(sample):
                return classify(sample.last_result, finished=True, marker_ok=marker_ok(marker, token))
        return classify(fresh.last_result if fresh else None, finished=False, marker_ok=False)
    except Exception:  # noqa: BLE001 - total by contract: the check is advisory and may not break the op
        return CheckVerdict.UNAVAILABLE, None
    finally:
        if owned:
            with contextlib.suppress(Exception):
                com.delete(check_name, timeout_s=DELETE_RESERVE_S)
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# The advisory facts                                                          #
# --------------------------------------------------------------------------- #

_SE_BATCH_LOGON = "SeBatchLogonRight"
_SE_DENY_BATCH_LOGON = "SeDenyBatchLogonRight"
# LsaEnumerateAccountRights' "this account holds no rights directly" is
# STATUS_OBJECT_NAME_NOT_FOUND, which pywin32 reports through LsaNtStatusToWinError as
# ERROR_FILE_NOT_FOUND. Both spellings are accepted: which one surfaces is unmeasured here.
_NO_RIGHTS_ASSIGNED = frozenset({2, 0xC0000034, -1073741772})


class NoRightsAssigned(LookupError):
    """The LSA has NO rights assigned to the account directly (not an error — an answer)."""


def _read_account_rights(sid: str) -> Collection[str]:  # pragma: no cover - Windows-only LSA call
    """The account's DIRECTLY assigned rights. Raises :class:`NoRightsAssigned` or anything else."""
    if sys.platform != "win32":
        raise OSError("The local security policy is Windows-only.")
    import pywintypes  # noqa: PLC0415 - lazy: Windows-only
    import win32security  # noqa: PLC0415 - lazy: Windows-only

    access = getattr(win32security, "POLICY_LOOKUP_NAMES", 0x00000800)
    policy = win32security.LsaOpenPolicy(None, access)
    try:
        try:
            return tuple(win32security.LsaEnumerateAccountRights(policy, win32security.ConvertStringSidToSid(sid)))
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) in _NO_RIGHTS_ASSIGNED:
                raise NoRightsAssigned from None
            raise
    finally:
        win32security.LsaClose(policy)


def batch_right_state(sid: str, *, reader: Callable[[str], Collection[str]] = _read_account_rights) -> RightState:
    """The account's own batch-logon rights. ADVISORY and TOTAL: any error is UNKNOWN.

    A DENY assigned directly is definitive (it overrides every grant). No direct GRANT is not:
    the right is routinely given through a group or a domain policy, which this does not
    expand — the check run is what proves it either way.
    """
    try:
        rights = set(reader(sid))
    except NoRightsAssigned:
        return RightState.NOT_DIRECT
    except Exception:  # noqa: BLE001 - advisory: an API that could not answer is never a finding
        return RightState.UNKNOWN
    if _SE_DENY_BATCH_LOGON in rights:
        return RightState.DENIED
    if _SE_BATCH_LOGON in rights:
        return RightState.GRANTED
    return RightState.NOT_DIRECT


# MSA_INFO_STATE (lmaccess.h). NotExist (1) and NotService (2) are deliberately NOT mapped:
# the account was already resolved to a SID, so either answer more likely means this call was
# given a name form it does not expect than that the account is missing — UNKNOWN, never a
# false failure.
_MSA_INFO_STATES: dict[int, InstallState] = {
    3: InstallState.NOT_PERMITTED,  # MsaInfoCannotInstall
    4: InstallState.NOT_INSTALLED,  # MsaInfoCanInstall
    5: InstallState.INSTALLED,  # MsaInfoInstalled
}


def _query_service_account(account: str) -> int:  # pragma: no cover - Windows-only netapi32 call
    """``NetQueryServiceAccount(NULL, account, 0, &MSA_INFO_0)`` -> the ``MSA_INFO_STATE``."""
    if sys.platform != "win32":
        raise OSError("NetQueryServiceAccount is Windows-only.")
    import ctypes  # noqa: PLC0415 - lazy: Windows-only

    netapi32 = ctypes.WinDLL("netapi32")  # type: ignore[attr-defined]
    query = netapi32.NetQueryServiceAccount
    query.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    query.restype = ctypes.c_long
    netapi32.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.c_void_p()
    status = query(None, account, 0, ctypes.byref(buffer))
    if status != 0 or not buffer.value:
        raise OSError(f"NetQueryServiceAccount returned {status & 0xFFFFFFFF:#010x}.")
    try:
        return int(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32)).contents.value)
    finally:
        netapi32.NetApiBufferFree(buffer)


def install_state(account: str, *, query: Callable[[str], int] = _query_service_account) -> InstallState:
    """Whether the managed service account is installed on THIS computer. ADVISORY and TOTAL.

    Asked with the account name after its domain prefix (``svc$`` for ``CORP\\svc$``) — which
    name form ``NetQueryServiceAccount`` expects for a gMSA is unmeasured here, and the state
    table above is built so that a wrong guess lands on UNKNOWN rather than a failure.
    """
    short = account.rsplit("\\", 1)[-1]
    try:
        state = query(short)
    except Exception:  # noqa: BLE001 - advisory: an API that could not answer is never a finding
        return InstallState.UNKNOWN
    return _MSA_INFO_STATES.get(state, InstallState.UNKNOWN)


# --------------------------------------------------------------------------- #
# The elevated child's entry point                                            #
# --------------------------------------------------------------------------- #


def _provisioning_account_sid(name: str) -> str:
    """``provisioning.account_sid`` — LAZY: ``provisioning`` imports ``windows`` at module level."""
    from src.scheduler.provisioning import account_sid  # noqa: PLC0415

    return account_sid(name)


def requested(payload: Mapping[str, object]) -> bool:
    """Did the parent ASK for the check, for a kind it applies to? Total — never raises."""
    if payload.get("schedule_check") is not True:
        return False
    try:
        return applies_to(PrincipalKind(str(payload.get("kind", ""))))
    except ValueError:
        return False


def _bounded_advisory(read: Callable[[], StrEnum], unknown: StrEnum, *, timeout_s: float, label: str) -> StrEnum:
    """One advisory read on a bounded worker — UNKNOWN when it runs out or raises."""
    try:
        answer: StrEnum = task_com.bounded(read, timeout_s=timeout_s, label=label)
    except Exception:  # noqa: BLE001 - advisory: a read that could not finish is never a finding
        return unknown
    return answer


def _sweep_allowed(root: Path) -> bool:
    """May the ELEVATED child sweep ``root/runs``? Only once the folder passes the trust check."""
    try:
        paths.assert_machine_dir_trusted(root)
    except Exception:  # noqa: BLE001 - the sweep is optional: ANY failure to prove trust skips it, never the check
        return False
    return True


def run_for_payload(
    payload: Mapping[str, object],
    *,
    com: CheckCom | None = None,
    account_sid: Callable[[str], str] | None = None,
    rights_reader: Callable[[str], Collection[str]] = _read_account_rights,
    install_query: Callable[[str], int] = _query_service_account,
    advisory_timeout_s: float = ADVISORY_CALL_TIMEOUT_S,
) -> ScheduleCheck:
    """The check for one ``provision`` payload, run AFTER the nightly registered. Never raises.

    The nightly's command is rebuilt from the payload through the SAME validators and the same
    ``RegisterParams`` the registration used (the child is the privileged half — nothing is
    taken on trust twice), so the check task cannot differ from the nightly in principal,
    program, start-in folder or run level.
    """
    from src.utils.validators import validate_run_time, validate_task_name  # noqa: PLC0415

    try:
        kind = PrincipalKind(str(payload["kind"]))
        user = task_com.validate_principal_account(kind, str(payload["user"]))
        run_time = str(payload["run_time"])
        validate_run_time(run_time)
        password = payload.get("password") or None
        params = task_com.RegisterParams(
            task_name=validate_task_name(str(payload["task_name"])),
            exe=str(payload["exe"]),
            arguments=str(payload["arguments"]),
            working_dir=str(payload["working_dir"]),
            run_time=run_time,
            user=user,
            password=str(password) if password is not None else None,
            kind=kind,
            run_highest=bool(payload["run_highest"]),
            wake_to_run=bool(payload["wake_to_run"]),
        )
    except (KeyError, ValueError, TypeError):
        return ScheduleCheck(verdict=CheckVerdict.UNAVAILABLE)

    resolve_sid = account_sid if account_sid is not None else _provisioning_account_sid

    def _rights() -> RightState:
        return batch_right_state(resolve_sid(user), reader=rights_reader)

    # Each advisory read may reach a domain controller, so each is bounded; together they are
    # ADVISORY_BUDGET_S of TOTAL_BUDGET_S, which the parent's elevation wait is sized on.
    batch_right = RightState(
        _bounded_advisory(_rights, RightState.UNKNOWN, timeout_s=advisory_timeout_s, label="check-rights")
    )
    installed = InstallState(
        _bounded_advisory(
            lambda: install_state(user, query=install_query),
            InstallState.UNKNOWN,
            timeout_s=advisory_timeout_s,
            label="check-install",
        )
    )

    try:
        root = paths.machine_data_dir()
        verdict, code = run_check(
            params,
            runs_dir=root / paths.MACHINE_RUNS_SUBDIR,
            com=com if com is not None else _LiveCom(),
            token=secrets.token_hex(16),
            clock=time.monotonic,
            sleep=time.sleep,
            now=datetime.now,
            sweep_stale=_sweep_allowed(root),
        )
    except Exception:  # noqa: BLE001 - total by contract (run_check is total; this guards the root)
        verdict, code = CheckVerdict.UNAVAILABLE, None
    return ScheduleCheck(verdict=verdict, code=code, batch_right=batch_right, installed=installed)
