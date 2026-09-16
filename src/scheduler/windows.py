"""Windows Task Scheduler integration — in-process COM, zero child processes (plan 0041).

Creates a daily scheduled task that runs the DistrictSync CLI at a specified time.
Every steady-state operation — register, read-back, delete — drives the Task Scheduler
COM API through ``src/scheduler/task_com.py`` (the why, the wrapped-HRESULT trap, and
the apartment-lifetime rules live there): **no ``powershell.exe``, no ``schtasks.exe``,
no ``-EncodedCommand``, no console-flash risk** — the transport chain Bitdefender ATC
flagged live on 2026-08-04 is gone end to end (S1a moved read/delete; S1b moved
registration + the elevated child). The retired PS transport's hard-won lessons
(the stdin no-op, CLIXML stderr, parameter-set S4U inference) are preserved in
DECISIONS 2026-06-25 — consult git history for the scripts themselves.

**Secure invocation contract:**

  - Registration parameters travel as a ``task_com.RegisterParams`` (``repr=False`` —
    the password can never leak through a formatted params object) into
    ``RegisterTaskDefinition``: an in-process BSTR argument. The password appears on
    NO argv, in NO process environment, in NO log, and in NO returned message.
  - The logon type is an **explicit constant** — ``TASK_LOGON_PASSWORD`` (unattended)
    or ``TASK_LOGON_INTERACTIVE_TOKEN`` + Limited (logged-on-only) — never
    parameter-set inference, and ``TASK_LOGON_S4U`` is deliberately not even defined
    (no network token → breaks SFTP egress; the 2026-06-25 regression class).
    *run_highest* is honoured only WITH a password; without one the task is always
    Limited.
  - The PRINCIPAL is never substituted (plan 0046 A1). An explicit ``run_as_user`` that
    differs from the account running setup registers only WITH that account's password,
    or the call is REFUSED — it used to fall back to the current user and return
    ``(True, "Schedule registered.")``, i.e. a wrong principal behind a green banner.
    ``""`` and ``None`` are normalised to one meaning at entry, so a blank password can
    no longer reach ``RegisterTaskDefinition`` as a TASK_LOGON_PASSWORD registration.
  - The settings quintet (no catch-up, IgnoreNew, PT2H, both battery flags) is set
    explicitly in ``task_com.apply_definition`` — COM defaults differ on all five.
  - Failure messages are the ``task_com`` HRESULT-keyed canonicals
    (``task_com.MSG_ACCESS_DENIED`` / ``MSG_LOGON_FAILURE`` / ``MSG_ACCOUNT_INFO_NOT_SET``
    / ``MSG_NO_LOGON_SESSION`` / ``MSG_NOT_FOUND``, or Windows' own description plus its
    hex status for an unmapped one) — locale-independent, injective, and marker-guarded
    (plan 0047; ``docs/claugentic-INVARIANTS.md``). ``setup_errors.classify_schedule_error``
    IMPORTS these constants and branches on them by EXACT equality (as it already did for the
    ``windows._MSG_*`` elevation canonicals), so an edit to a canonical here silently moves a
    branch there — mirror it. Only the defensive access-denied fallback still matches by
    substring, and it deliberately shows no code.
  - **Every** ``(False, message)`` return in this module goes through :func:`_fail`, which
    writes ONE anchored log line carrying the verb, the task name, the message and
    ``[HRESULT 0x… | n/a]`` — so a district's ``etl_tool.log`` carries ONE greppable anchor
    per failure, with the status Windows returned and its HRESULT. That is the STATUS, not a
    cause: the hedged cause copy is the classifier's, and for a policy-blocked registration no
    app-side change makes the sync run. The one documented silence is an already-absent task
    on the remove path.

**Self-elevation (Plan 0029 D5; re-targeted at 0041 S1b):** the unattended
(password / RunLevel Highest) registration genuinely requires an elevated caller.
When the process is NOT already elevated, :func:`register_task` runs the operation
behind ONE normal UAC prompt — the elevated child is **DistrictSync itself** in the
dispatch-first ``--elevated-apply`` mode (``src/scheduler/elevated_apply.py``),
executing the SAME ``task_com`` functions as the direct path: the single-source
property the old PS ``_register_body`` text-sharing protected is now structural.
The password crosses the elevation boundary ONLY inside a DPAPI-CurrentUser-sealed
request file (never argv, any env, or a log); a cross-SID (different-admin) consent
fails closed with the ``DSYNC_DIFFERENT_ACCOUNT`` sentinel. Success is CONFIRMED via
:func:`read_schedule` — never assumed from the child's exit code.
:func:`delete_task_elevated` rides the same child. :func:`is_elevated` lets the
wizard tell an un-elevated "Access is denied" (run as administrator) apart from an
elevated one (a credential / batch-logon-right problem).

**Schedule read-back (Plan 0029, D4; COM since 0041):** :func:`read_schedule` returns
the typed frozen :class:`ScheduleReadback`. It is deliberately **tri-state**, now
HRESULT-keyed: the definitive not-found status (``0x80070002``, unwrapped from
``excepinfo`` — the outer ``hresult`` is just ``DISP_E_EXCEPTION``) → ``found=False``;
ANY other failure (access denied — e.g. an elevated-registered task unreadable by a
filtered token — RPC failure, a timed-out bounded worker, pywin32 missing) →
``found=None`` (query itself failed, never "absent"). The pure
``ui_flet.schedule_status`` module maps this to the honest LIVE / MISSING / UNKNOWN
contract — only ``found=False`` may claim "not scheduled".

Usage::

    from src.scheduler.windows import register_task, read_schedule, delete_task

    ok, msg = register_task(
        task_name="DistrictSync_Daily",
        exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
        sis_type="myedbc",
        input_dir=Path("C:/GDE2Data/input"),
        output_dir=Path("C:/GDE2Data/output"),
        run_time="03:00",
        sftp=True,
    )
"""

from __future__ import annotations

import contextlib
import getpass
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# NOTE (plan 0041 S1b): `subprocess`, `base64`, `re`, `system_binary` and
# `subprocess_no_window_flags` all left this module WITH the PowerShell transport —
# the scheduler spawns no child process at all now (the elevated child is launched by
# elevation.py's ShellExecuteExW, not subprocess). Pinned by the transport-absence
# tests in tests/test_schedulers.py.
from src.scheduler import elevation, task_com
from src.scheduler.elevated_apply import DIFFERENT_ACCOUNT_SENTINEL as _DIFFERENT_ACCOUNT_SENTINEL
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.scheduler.messages import SECRET_SENTINEL_PREFIX
from src.utils.validators import (
    validate_run_as_user,
    validate_run_time,
    validate_sis_type,
    validate_task_name,
)

logger = logging.getLogger(__name__)

# Bounded wait for the elevated child (D5) — never INFINITE. WaitForSingleObject waits
# for the elevated DistrictSync child to finish registering (the UAC-consent delay happens
# inside ShellExecuteEx, which the OS bounds by its own prompt timeout), so 120s is
# generous headroom for a slow RegisterTaskDefinition without ever freezing the flow.
_ELEV_TIMEOUT_S = 120.0

# Elevation outcome message contract (D5) — the canonical, secret-free strings
# register_task returns on the self-elevated register/unregister path. The wizard's
# pure classifier (src/ui_flet/setup_errors.classify_schedule_error) keys off these
# EXACT values, so any change here must be mirrored there (it imports these constants).
_MSG_UAC_DECLINED = "The Windows permission prompt was declined."
# TIMEOUT is only reachable AFTER UAC consent (a runas process handle exists only once the
# user accepts), so the terminated child may have already registered/removed the task. The
# marker is therefore neutral ("timed out"), NEVER "before it was answered / nothing changed"
# — and the register/delete flows resolve it with a read-back before surfacing it.
_MSG_ELEVATION_TIMEOUT = "The elevated schedule change timed out before it finished."
_MSG_ELEVATION_NO_RESULT = "The schedule change could not be confirmed."
_MSG_ELEVATION_REMOVE_UNCONFIRMED = "The schedule removal could not be confirmed."
_MSG_DIFFERENT_ACCOUNT = "The permission prompt ran as a different account."
_MSG_ELEVATION_LAUNCH_FAILED = "Windows could not show the permission prompt."

# A PRE-FLIGHT refusal, not an elevation outcome (plan 0046 A1): an explicit run-as
# account that is NOT the account running setup can only be registered WITH its
# password — Windows stores no credential for an interactive-token task. register_task
# used to silently substitute the current user here and return success, so the nightly
# ran under the wrong identity with a green banner over it. Canonical + secret-free
# (it never echoes the account) so setup_errors can key off it by exact equality.
# (B105 is a false positive here, as on main.SFTP_PASSWORD_ENV_VAR: the NAME carries the
# word, the value is user-facing refusal copy and no credential is involved.)
_MSG_ACCOUNT_NEEDS_PASSWORD = (  # nosec B105
    "A password is required to schedule the task for a different account."
)

# The sentinel the elevated child writes to its result file when it cannot READ the request
# (the owner-only DACL refuses a DIFFERENT administrator) or when the DPAPI unprotect FAILS
# (a cross-SID / different-admin UAC consent) — fail closed either way. The parent detects it
# BEFORE sanitizing (it deliberately carries the DSYNC_ prefix a normal message never would)
# and maps it to the bounded _MSG_DIFFERENT_ACCOUNT category, on BOTH the register and the
# remove path. IMPORTED from its producer (plan 0047) rather than re-spelled here — a second
# spelling is exactly how the two halves of an IPC contract drift apart.

# An access-denied the ELEVATED CHILD reported — i.e. Windows refused the change AFTER the
# UAC prompt was approved (plan 0047 A2). It exists because `classify_schedule_error`'s
# `elevated` flag is the PARENT process's token, which is False on this path: without its own
# canonical, a UAC-approved child refusal classified as "right-click and Run as administrator"
# — the exact loop SD60 ran on 2026-09-14. Provenance rides the MESSAGE, not the parent's bit.
_MSG_ELEVATED_ACCESS_DENIED = "Windows refused the elevated schedule change."

# Two child-result floors, named so the classifier can decide each one explicitly.
_MSG_CHILD_DETAIL_UNAVAILABLE = "The schedule change failed (error detail unavailable)."
_MSG_CHILD_NO_DETAIL = "The schedule change failed with no detail."

# The direct (non-elevated) delete's bounded-worker timeout — the OUTCOME is unknown.
_MSG_REMOVAL_TIMED_OUT = "The schedule removal timed out."

# --- The ONE failure log line (plan 0047, G2) ---------------------------------
# SD60 (2026-09-14) hit a registration failure, re-ran the app as administrator, hit the
# same failure, and reported "I didn't see anything informative in the log": several
# `(False, msg)` arms logged nothing, and NONE of them logged the HRESULT. `_fail` is the
# single funnel every failure return goes through, so a district's `etl_tool.log` has ONE
# grep anchor — the partner troubleshooting page quotes the "[HRESULT " prefix.
# The trailing `%s` is the OPTIONAL detail suffix (empty on almost every arm), so there is
# still exactly ONE format string to grep for and the no-detail line is byte-identical to
# the pre-detail one. It exists because `com_error_scode` recovers nothing from a non-COM
# exception: on the two generic arms `[HRESULT n/a]` would otherwise be the whole payload,
# strictly LESS than the class name those arms logged before the funnel existed.
_FAIL_LOG_FORMAT = "Failed to %s task '%s': %s [HRESULT %s]%s"
_FAIL_DETAIL_SUFFIX = " (%s)"

# The run-history store's source tag for the nightly scheduled run (Plan 0029, D2c).
# Carried on the registered task's action command line (``--source scheduled``) so the
# store labels the nightly run correctly from day one. Mirrors ``history.store``'s
# ``VALID_SOURCES`` value without importing the store into the scheduler layer.
_SCHEDULED_SOURCE = "scheduled"

# The "PowerShell not found" / "ScheduledTasks module not available" canonical messages,
# the CLIXML decoder and its regexes all RETIRED at plan 0041 S1b with their transport —
# the COM analogue of an unavailable engine is task_com.MSG_COM_UNAVAILABLE (row 10), and
# it is produced HERE (the two ImportError arms) and by the elevated child.

# --- Schedule read-back (D4) --------------------------------------------------
# In-process COM since plan 0041 Slice 1a (src/scheduler/task_com.py) — the DSYNC_FOUND /
# DSYNC_ABSENT stdout protocol and the subprocess timeout retired with the PowerShell
# transport; tri-state classification is HRESULT-keyed at the task_com boundary, and the
# 10s bound lives at task_com.READ_TIMEOUT_S (same budget, same UNKNOWN-on-timeout rule).

# Honest platform note surfaced when read-back is requested off Windows (Linux/macOS
# schedule read-back is out of scope — the pure module renders this as UNKNOWN).
_MSG_NOT_WINDOWS = "Schedule read-back is only available on Windows."


def _fail(
    task_name: str,
    message: str,
    *,
    verb: str,
    scode: int | None = None,
    level: int = logging.ERROR,
    detail: str | None = None,
) -> tuple[bool, str]:
    """Log ONE anchored failure line and return the ``(False, message)`` contract (G2).

    ``verb`` is a REQUIRED keyword (``"register"`` / ``"remove"``): a default would let a
    failed REMOVAL log "Failed to register task", and that line is the one an IT reader is
    told to grep for. ``level`` is ``WARNING`` only for the two *unconfirmed-outcome* arms
    (``_confirm_registration`` / ``_confirm_removal``), where the task may well exist — the
    outcome is UNKNOWN, not failed, and WARNING is the honest level.

    The line carries a verb, the task name, a message and an int. ``_fail`` itself does not
    verify what the message is — that property holds because every call site today passes
    either a ``task_com`` canonical, one of this module's bounded ``_MSG_*`` categories, or a
    ``DSYNC_``-stripped child message (never a password, an account name, argv or an
    environment value). ``task_com``'s guard strips any escape carrying the ``DSYNC_``
    sentinel prefix before it reaches here — that is a MARKER check, not a secret scrubber,
    so it would not catch a bare credential Windows had somehow echoed. The no-secret
    property is upheld at the call sites and by ``task_com``'s stated assumption that Task
    Scheduler's own descriptions never echo a credential; it is not enforced by this
    function.

    ``detail`` is an OPTIONAL, BOUNDED, secret-free diagnostic token appended after the
    code on the SAME line (one failure still means one anchored line). Today its only
    callers are the two generic ``except Exception`` arms, which pass
    ``type(exc).__name__``: ``com_error_scode`` recovers a status from a ``com_error`` but
    nothing at all from, say, a ``TypeError`` raised by a pywin32 shape change, and
    ``[HRESULT n/a]`` alone would be LESS diagnostic than the class name those arms logged
    before this funnel existed. A funnel may add context; it may never net-delete it.
    **Never pass an exception's ``str()``** — that is uncontrolled text which can carry a
    path, an account name or a secret, the very reason messages are canonicalised.

    **The one documented silence:** an already-absent task on the REMOVE path
    (``task_com.MSG_NOT_FOUND``) is the idempotent desired end state, not an incident;
    logging it at ERROR on every Unregister would train a district to ignore the anchor.
    Keeping that rule INSIDE the funnel is what lets the AST guard stay absolute (no
    ``return False,`` anywhere in this module outside this function).
    """
    if not (verb == "remove" and message == task_com.MSG_NOT_FOUND):
        logger.log(
            level,
            _FAIL_LOG_FORMAT,
            verb,
            task_name,
            message,
            task_com.format_hresult(scode),
            _FAIL_DETAIL_SUFFIX % detail if detail else "",
        )
    return False, message


def current_run_as_user() -> str:
    """Resolve the account the scheduled task should run as.

    Returns ``DOMAIN\\user`` from ``%USERDOMAIN%`` / ``%USERNAME%`` when both
    environment variables are present and non-empty, otherwise falls back to
    :func:`getpass.getuser`. This is the interactive user who runs setup — the
    same account whose Windows Credential Manager holds the SFTP password.
    """
    domain = os.environ.get("USERDOMAIN", "")
    username = os.environ.get("USERNAME", "")
    if domain and username:
        return f"{domain}\\{username}"
    return getpass.getuser()


def is_elevated() -> bool:
    """Return True if the current process is running with administrator rights.

    On Windows, queries ``shell32.IsUserAnAdmin()`` (returns non-zero when the
    caller's token has the Administrators group enabled). Any failure — missing
    API, non-Windows ``ctypes.windll``, unexpected error — resolves to ``False``
    (treat unknown as "not elevated"). Off Windows there is no equivalent admin
    concept here, so it always returns ``False``.

    Used by the Setup Wizard to distinguish an *un*-elevated "Access is denied"
    (→ tell the user to run as administrator) from an elevated one (→ a
    credential / batch-logon-right problem, not an elevation problem), so the
    wizard stops sending an already-elevated admin in circles.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _build_action_args(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    sftp: bool,
) -> tuple[str, Path]:
    """Resolve the action command line + working directory for the two modes.

    Returns ``(arguments, working_dir)``:

      - Python interpreter (dev / source install): ``arguments`` is
        ``-m src.main --sis X --input "Y" --output "Z" [--sftp]`` and
        ``working_dir`` is the project root — so Python finds the ``src``
        package. Without ``-m`` Python would treat ``--sis`` as a script path
        and fail with 0x80070002.
      - Frozen PyInstaller binary (e.g. DistrictSync.exe): ``arguments`` omits
        ``-m src.main`` and ``working_dir`` is the exe's parent directory.

    Paths are wrapped in quotes inside the single ``arguments`` string so a
    space-bearing district path survives as one token; the string is passed to
    PowerShell via the ``DSYNC_ARGS`` env var (never interpolated into the
    script body).
    """
    is_python = exe_path.name.lower().startswith("python")

    if is_python:
        # Project root = two levels up from src/scheduler/windows.py
        working_dir = Path(__file__).resolve().parents[2]
        arg_parts = [
            "-m",
            "src.main",
            "--sis",
            sis_type,
            "--input",
            f'"{input_dir}"',
            "--output",
            f'"{output_dir}"',
        ]
    else:
        working_dir = exe_path.parent
        arg_parts = [
            "--sis",
            sis_type,
            "--input",
            f'"{input_dir}"',
            "--output",
            f'"{output_dir}"',
        ]
    if sftp:
        arg_parts.append("--sftp")
    # Tag the run as SCHEDULED so the run-history store labels the nightly run correctly
    # from its first day (Plan 0029, D2c). The ScheduledTasks module has no per-action
    # environment field (only a cmd wrapper could set a runtime env var, which would
    # change the action's Execute off the exe); carrying the source on the action's
    # command line here — the single action builder — is the minimal, exe-path-preserving
    # way. ``run_pipeline`` resolves ``--source`` ahead of the ``DSYNC_SOURCE`` env fallback.
    arg_parts += ["--source", _SCHEDULED_SOURCE]
    return " ".join(arg_parts), working_dir


def register_task(
    task_name: str,
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    run_time: str,
    sftp: bool = False,
    *,
    run_as_user: str | None = None,
    run_as_password: str | None = None,
    run_highest: bool = True,
) -> tuple[bool, str]:
    """Create or replace a Windows scheduled task — in-process COM (plan 0041 S1b).

    The retired transport handed a fixed PowerShell script to ``powershell.exe
    -EncodedCommand``; registration now drives ``task_com.register_task_definition``
    directly (the SAME function the elevated child runs — single source, structurally).
    The password is an in-process argument to ``RegisterTaskDefinition``: never argv,
    never any process environment, never logged, never in the returned message.

    Args:
        task_name: Name displayed in Task Scheduler (e.g. "DistrictSync_Daily").
        exe_path:  Absolute path to DistrictSync.exe *or* the python.exe
                   interpreter when running from source.
        sis_type:  SIS config identifier (e.g. "myedbc").
        input_dir: Directory containing GDE source files.
        output_dir: Directory to write CSV files.
        run_time:  Daily run time in "HH:MM" 24-hour format.
        sftp:      If True, appends ``--sftp`` flag to the task command.
        run_as_user: Windows account the task runs as. Omit (or pass the current
                   account) for today's behaviour — the task is registered to
                   :func:`current_run_as_user`. A DIFFERENT account is validated
                   via :func:`validate_run_as_user` and **requires**
                   ``run_as_password``: without one the call is REFUSED with
                   ``_MSG_ACCOUNT_NEEDS_PASSWORD`` and nothing is registered.
                   It is never silently replaced by the current user (plan 0046 A1).
        run_as_password: The ``run_as_user`` account's Windows password. Empty
                   string and ``None`` mean the same thing — normalised once, at
                   entry, so this half and ``task_com.apply_definition`` cannot
                   disagree about what "no password" is. When provided, the task
                   is registered to run **whether the user is logged on or not**
                   (explicit ``TASK_LOGON_PASSWORD`` — never parameter-set
                   inference, never S4U). When omitted, the task runs only while
                   the user is logged on (``TASK_LOGON_INTERACTIVE_TOKEN``) and no
                   credential is stored.
        run_highest: When True and a password is supplied, run with highest
                   privileges (``TASK_RUNLEVEL_HIGHEST``). Ignored without a
                   password (the logged-on-only path is always Limited).

    Returns:
        (success, message). Failure messages are the ``task_com`` canonical
        constants (``MSG_ACCESS_DENIED`` / ``MSG_LOGON_FAILURE`` /
        ``MSG_ACCOUNT_INFO_NOT_SET`` / ``MSG_NO_LOGON_SESSION`` /
        ``MSG_COM_UNAVAILABLE`` / ``MSG_OPERATION_FAILED``), this module's own
        ``_MSG_*`` elevation categories, a ``DSYNC_``-stripped child message off the
        elevated path (an ``elevated_apply.CHILD_REFUSALS`` refusal or a child-side
        ``task_com`` canonical), or Windows' own description plus its hex status for
        an unmapped one. The wizard classifier
        (``setup_errors.classify_schedule_error``) keys on this module's own
        ``_MSG_*`` elevation categories AND on the ``task_com`` canonicals above by
        exact equality — it imports both families, so a re-worded canonical moves a
        branch there; an unmapped message still reaches its details clause. **Every**
        failure return also writes the :func:`_fail` log line. A
        registration that TIMES OUT resolves through :func:`_confirm_registration`
        (the worker cannot be cancelled and may still complete — a bare "failed"
        over a task that now exists would be a lie; row 14).
    """
    # Validate all user-supplied values before touching the OS.
    task_name = validate_task_name(task_name)
    sis_type = validate_sis_type(sis_type)
    validate_run_time(run_time)

    # ONE spelling of "no password" (R2). ``windows`` used to read ``""`` as *absent*
    # (``bool``) while ``task_com.apply_definition`` reads it as *present* (``is not
    # None``) — so a blank string registered TASK_LOGON_PASSWORD with a blank credential.
    # Normalising here, at the single entry point, makes the two halves agree by
    # construction instead of by the caller remembering to pass ``password or None``.
    run_as_password = run_as_password or None
    has_password = run_as_password is not None

    requested = (run_as_user or "").strip()
    current = current_run_as_user()
    if requested and requested.casefold() != current.casefold():
        # A DIFFERENT account than the one running setup. Caller input, so it is ALWAYS
        # validated — and it can only be registered WITH its password: Windows stores no
        # credential for an interactive-token task, so "this other account, logged-on-only"
        # is not a thing Windows can do. We REFUSE rather than substitute the current user,
        # which is what this function used to do while returning (True, "Schedule
        # registered.") — a silently misregistered principal reported as success (A1/R1).
        user = validate_run_as_user(requested)
        if not has_password:
            return _fail(task_name, _MSG_ACCOUNT_NEEDS_PASSWORD, verb="register")
    elif has_password:
        # Unattended registration for the CURRENT account — unchanged from today,
        # including validating the machine-derived fallback on this branch.
        user = validate_run_as_user(requested or current)
    else:
        # Logged-on-only. The machine-derived account is deliberately NOT validated: a
        # legitimate local account can contain a space (``PC\John Smith``), which the
        # regex rejects, and that district must keep registering exactly as it does today.
        user = current

    arguments, working_dir = _build_action_args(exe_path, sis_type, input_dir, output_dir, sftp)

    # Self-elevation (D5): an unattended (password / RunLevel Highest) registration
    # genuinely requires an elevated caller. When we are NOT already elevated, run the
    # registration behind ONE normal UAC prompt — the child is DistrictSync itself in
    # --elevated-apply mode since S1b — while the app itself stays non-admin.
    if has_password and sys.platform == "win32" and not is_elevated():
        assert run_as_password is not None  # has_password is the same check  # nosec B101
        return _register_elevated(
            task_name=task_name,
            user=user,
            run_time=run_time,
            exe_path=exe_path,
            arguments=arguments,
            working_dir=working_dir,
            run_as_password=run_as_password,
            run_highest=run_highest,
        )

    logger.info(f"Registering Windows scheduled task: {task_name} at {run_time}")
    params = task_com.RegisterParams(
        task_name=task_name,
        exe=str(exe_path),
        arguments=arguments,
        working_dir=str(working_dir),
        run_time=run_time,
        user=user,
        password=run_as_password,
        run_highest=run_highest,
    )
    try:
        task_com.bounded(
            lambda: task_com.register_task_definition(params),
            timeout_s=task_com.REGISTER_TIMEOUT_S,
            label="register",
        )
    except task_com.BoundedTimeout:
        # The worker may still complete after the bound — the verdict comes from the
        # real task, with the hedged elevation-timeout copy (same classifier branch).
        return _confirm_registration(task_name, on_unconfirmed=_MSG_ELEVATION_TIMEOUT, path_label="Registration")
    except ImportError:
        return _fail(task_name, task_com.MSG_COM_UNAVAILABLE, verb="register")
    except task_com.TaskComError as exc:
        # Canonical, secret-free text (task_com never formats argv/env/password into
        # messages) — surfaced as-is so the wizard classifier matches its known strings.
        return _fail(task_name, exc.message, verb="register", scode=exc.scode)
    except Exception as exc:  # noqa: BLE001 - (ok, message) contract: classify, never propagate
        # A com_error raised at APARTMENT ENTRY (the Task Scheduler service stopped) never
        # became a TaskComError, so this arm used to discard its status and log only the
        # exception's class name, at WARNING. `com_error_scode` recovers the real HRESULT —
        # and `detail` KEEPS the class name, which is the whole payload when the exception
        # is not a com_error at all (a pywin32 shape change raising TypeError).
        return _fail(
            task_name,
            task_com.MSG_OPERATION_FAILED,
            verb="register",
            scode=task_com.com_error_scode(exc),
            detail=type(exc).__name__,
        )

    logger.info(f"Task '{task_name}' registered successfully")
    return True, "Schedule registered."


def delete_task(task_name: str) -> tuple[bool, str]:
    """Remove a scheduled task by name. Never raises — ``(success, message)``.

    **In-process COM since plan 0041 Slice 1a** (``Folder.DeleteTask``), retiring the
    last ``schtasks.exe`` call — after which ``schtasks.exe`` left the ``system_binary``
    allowlist entirely. Two message contracts are LOAD-BEARING and pinned:

    - an already-absent task (``0x80070002``) returns ``task_com.MSG_NOT_FOUND`` — its
      ``messages.ABSENT_TASK_MARKERS`` token keeps
      ``schedule_status.interpret_unregister`` idempotent-success-shaped, exactly as the
      schtasks stderr did. It is also the ONE failure :func:`_fail` does not log: that
      end state is the desired one, not an incident;
    - access denied returns ``task_com.MSG_ACCESS_DENIED`` — the
      ``messages.ACCESS_DENIED_MARKERS`` token the ``WindowsTaskScheduler.delete``
      adapter's elevated-retry predicate matches (contract row 13). Mapped by HRESULT,
      never by locale text — and since plan 0047 an UNMAPPED code whose Windows
      description merely *mentions* access denial no longer fires that retry, because
      ``task_com`` guards the description against markers it does not own.
    """
    task_name = validate_task_name(task_name)
    try:
        task_com.bounded(
            lambda: task_com.delete_task_by_name(task_name),
            timeout_s=task_com.DELETE_TIMEOUT_S,
            label="delete",
        )
    except task_com.BoundedTimeout:
        # The worker may still complete — resolve the ambiguity by reading back, the
        # same honesty rule the elevated path has always applied.
        return _confirm_removal(task_name, on_unconfirmed=_MSG_REMOVAL_TIMED_OUT, path_label="Removal")
    except ImportError:
        return _fail(task_name, task_com.MSG_COM_UNAVAILABLE, verb="remove")
    except task_com.TaskComError as exc:
        # `_fail` stays SILENT for MSG_NOT_FOUND: an already-absent task is the idempotent
        # desired end state, not an incident (the rule lives in the funnel, not here).
        return _fail(task_name, exc.message, verb="remove", scode=exc.scode)
    except Exception as exc:  # noqa: BLE001 - (ok, message) contract: classify, don't propagate
        # `detail` for the same reason as the register twin: a non-COM exception has no
        # status to recover, and its class name is then the only diagnostic there is.
        return _fail(
            task_name,
            task_com.MSG_OPERATION_FAILED,
            verb="remove",
            scode=task_com.com_error_scode(exc),
            detail=type(exc).__name__,
        )
    return True, "The scheduled task was removed."


# --- Per-operation elevation (D5) --------------------------------------------
# When the app is NOT already elevated, an unattended register (stored-password /
# RunLevel Highest) — and deleting an elevated-registered task — self-elevate behind
# ONE UAC prompt via src/scheduler/elevation.py. The register path carries the Windows
# password ONLY inside a DPAPI-CurrentUser-sealed request file (never argv / the parent
# env / logs); the elevated child FAILS CLOSED on a cross-SID unprotect. Success is
# CONFIRMED by reading the real task back (read_schedule) — never assumed from an exit code.


def _run_elevated_child(req_path: Path, res_path: Path) -> ElevationOutcome:
    """Launch OUR OWN exe elevated in ``--elevated-apply`` mode (S1b — no PowerShell child).

    The child is ``sys.executable``: the frozen DistrictSync.exe in production, the Python
    interpreter (+ ``-m src.main``) in dev. Its argv carries ONLY the mode flag and the two
    handshake paths — the payload (password included) rides the DPAPI-sealed request file,
    exactly as before; the child runs ``src/scheduler/elevated_apply`` (dispatch-first,
    minimal, fail-closed) which calls the SAME ``task_com`` functions as the direct path.

    Dev-mode note: ``-m src.main`` needs the project root as cwd; ShellExecuteEx inherits
    the parent's cwd, which IS the project root when developing. The frozen exe (the only
    shape districts run) has no cwd dependency.
    """
    exe = Path(sys.executable)
    prefix = "-m src.main " if exe.name.lower().startswith("python") else ""
    params = f'{prefix}--elevated-apply "{req_path}" "{res_path}"'
    return elevation.run_elevated(str(exe), params, timeout_s=_ELEV_TIMEOUT_S)


def _map_pre_consent_failure(outcome: ElevationOutcome) -> str | None:
    """Map a DECLINED / LAUNCH_FAILED outcome to its canonical message, else None.

    Deliberately does NOT handle TIMEOUT: a timeout is only reachable AFTER UAC consent, so
    the terminated child may already have registered/removed the task — the register/delete
    flows resolve TIMEOUT (and COMPLETED) with a read-back rather than assert failure.
    """
    if outcome.result is ElevationResult.DECLINED:
        return _MSG_UAC_DECLINED
    if outcome.result is ElevationResult.LAUNCH_FAILED:
        return _MSG_ELEVATION_LAUNCH_FAILED
    return None


def _sanitize_child_message(message: str) -> str:
    """``DSYNC_``-strip a child result message before it can surface (defense-in-depth).

    The child's messages are ``task_com`` canonicals or the fixed refusal strings — none
    carries a ``DSYNC_`` token, so one appearing means something unexpected leaked into
    the result and it collapses to a safe generic line rather than surface. (The CLIXML
    decoding step retired with the PowerShell child at S1b.) The password VALUE never
    reaches the result by construction; this guards even a sentinel/name leaking.
    """
    cleaned = (message or "").strip()
    # Case-INSENSITIVE on purpose (plan 0047 Stage 7 security finding): a lowercased leak
    # must collapse exactly like the canonical-cased one. Deliberately NOT
    # `messages.carries_foreign_marker` — that guard is case-insensitive too, but it also
    # fires on `task_com.MSG_ACCESS_DENIED` (which does not carry this prefix at all; the
    # guard's ANY-marker sweep is broader than this one prefix), which would collapse a
    # legitimate child-reported access-denied canonical into the no-detail floor and destroy
    # the message the elevated path depends on. Keep this check narrow and local.
    if SECRET_SENTINEL_PREFIX.lower() in cleaned.lower():
        return _MSG_CHILD_DETAIL_UNAVAILABLE
    return cleaned or _MSG_CHILD_NO_DETAIL


def _cleanup_handshake(*handshake_paths: Path | None) -> None:
    """Best-effort delete of the request/result handshake files (sweep_orphans is the backstop).

    ``None`` paths (a handshake that failed to materialize before an early error) are
    skipped — the caller passes its ``req_path``/``res_path`` sentinels straight through.
    """
    for path in handshake_paths:
        if path is None:
            continue
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _confirm_registration(task_name: str, *, on_unconfirmed: str, path_label: str) -> tuple[bool, str]:
    """Confirm a registration against the REAL task; unconfirmed → ``(False, on_unconfirmed)``.

    Success (exit code / child ``ok`` / a long-running TIMEOUT) is only ever asserted when
    ``read_schedule`` reports ``found=True``. An elevated-registered task a filtered token
    can't read yields ``found=None`` → honestly unconfirmed, never a false green.

    ``path_label`` ("Registration" / "Elevated registration") is REQUIRED: this function
    serves BOTH the direct bounded-timeout path and the elevated path, and the phase line
    used to say "Elevated registration" on both — sending a reader hunting a UAC prompt
    that never happened. The anchored ``_fail`` line is written BESIDE that phase line: the
    phase (pre-consent / post-consent / unconfirmed) is context ``_FAIL_LOG_FORMAT`` does
    not carry, and the funnel ADDS a line, it never deletes context.
    """
    readback = read_schedule(task_name)
    if readback.found is True:
        logger.info("Scheduled task '%s' registered and confirmed via read-back.", task_name)
        return True, "Schedule registered and confirmed."
    logger.warning("%s of '%s' could not be confirmed via read-back.", path_label, task_name)
    # WARNING, not ERROR: the task may well exist — the OUTCOME is unknown, not failed.
    return _fail(task_name, on_unconfirmed, verb="register", level=logging.WARNING)


def _confirm_removal(task_name: str, *, on_unconfirmed: str, path_label: str) -> tuple[bool, str]:
    """Confirm a removal against the REAL task; only ``found=False`` is a confirmed removal.

    ``found=True`` (still there) or ``found=None`` (unreadable) → ``(False, on_unconfirmed)`` —
    the flow must not assert the schedule was removed when it couldn't be confirmed.

    Reached from the DIRECT delete's bounded timeout as well as from both elevated arms, so
    the anchored line is written with ``verb="remove"`` (R2-1: a removal must never log
    "Failed to register task"). ``path_label`` ("Removal" / "Elevated removal") is REQUIRED
    for the same reason it is on :func:`_confirm_registration`: the bespoke phase line said
    "Elevated removal" on EVERY path, including the direct bounded timeout, sending a reader
    hunting a UAC prompt that never happened.
    """
    readback = read_schedule(task_name)
    if readback.found is False:
        logger.info("Scheduled task '%s' removal confirmed via read-back.", task_name)
        return True, "Schedule removed and confirmed."
    logger.warning("%s of '%s' could not be confirmed via read-back.", path_label, task_name)
    # WARNING + verb="remove": the task may still be gone — and a removal must never log
    # "Failed to register task", which is the line the partner doc points an IT reader at.
    return _fail(task_name, on_unconfirmed, verb="remove", level=logging.WARNING)


def _register_elevated(
    *,
    task_name: str,
    user: str,
    run_time: str,
    exe_path: Path,
    arguments: str,
    working_dir: Path,
    run_as_password: str,
    run_highest: bool,
) -> tuple[bool, str]:
    """Register the unattended task behind ONE UAC prompt; confirm via read-back.

    The password rides ONLY the DPAPI-sealed request file — never argv (ShellExecuteEx
    gets only ``--elevated-apply`` + the two handshake paths), never any process env,
    never a log. The child is DistrictSync ITSELF (S1b — ``src/scheduler/elevated_apply``),
    running the SAME ``task_com.register_task_definition`` the direct path calls: the
    single-source property is structural now, not a shared script string. Success requires
    BOTH the child's ``ok`` AND a positive ``read_schedule`` confirmation; the handshake
    files are deleted in ``finally``.
    """
    payload: dict[str, object] = {
        "op": "register",
        "task_name": task_name,
        "user": user,
        "run_time": run_time,
        "exe": str(exe_path),
        "arguments": arguments,
        "working_dir": str(working_dir),
        "password": run_as_password,
        "run_highest": run_highest,
    }
    logger.info("Registering scheduled task '%s' via one-time elevation (UAC).", task_name)
    # req_path/res_path are None until write_request succeeds so the finally cleans the
    # DPAPI-sealed file the moment it exists — a build/launch error can't strand it until
    # the 1h sweep (security F2).
    req_path: Path | None = None
    res_path: Path | None = None
    try:
        req_path = elevation.write_request(payload)
        res_path = req_path.with_suffix(".res")
        outcome = _run_elevated_child(req_path, res_path)

        fail = _map_pre_consent_failure(outcome)
        if fail is not None:
            logger.error("Elevated registration of '%s' did not start: %s", task_name, fail)
            return _fail(task_name, fail, verb="register")
        if outcome.result is ElevationResult.TIMEOUT:
            # Post-consent timeout: the terminated child may already have registered — confirm.
            logger.warning("Elevated registration of '%s' timed out; confirming via read-back.", task_name)
            return _confirm_registration(
                task_name, on_unconfirmed=_MSG_ELEVATION_TIMEOUT, path_label="Elevated registration"
            )

        result = elevation.read_result(res_path)
        if result is None:
            logger.error("Elevated registration of '%s' produced no readable result.", task_name)
            return _confirm_registration(
                task_name, on_unconfirmed=_MSG_ELEVATION_NO_RESULT, path_label="Elevated registration"
            )
        if not result.get("ok"):
            child_msg = str(result.get("message", ""))
            if _DIFFERENT_ACCOUNT_SENTINEL in child_msg:
                # Log the FIXED canonical, never child_msg — the sentinel carries the DSYNC_
                # prefix no admin-facing string (or log line) may republish.
                return _fail(task_name, _MSG_DIFFERENT_ACCOUNT, verb="register")
            msg = _sanitize_child_message(child_msg)
            # ORDER IS LOAD-BEARING: recover the code from the message the CHILD sent, before
            # the re-label. `hresult_for` is the inverse of task_com's table and knows nothing
            # about `_MSG_ELEVATED_ACCESS_DENIED`, so re-labelling first would log
            # `[HRESULT n/a]` for the one arm whose code we already have.
            scode = task_com.hresult_for(msg)
            if msg == task_com.MSG_ACCESS_DENIED:
                msg = _MSG_ELEVATED_ACCESS_DENIED
            return _fail(task_name, msg, verb="register", scode=scode)
        # The child reported ok — CONFIRM against the real task (exit code alone is not success).
        return _confirm_registration(
            task_name, on_unconfirmed=_MSG_ELEVATION_NO_RESULT, path_label="Elevated registration"
        )
    except (OSError, RuntimeError, ValueError):
        # The PRE-CONSENT handshake (DPAPI seal, profile dir, icacls) could raise straight
        # past the (ok, message) contract to the view's floor, with zero log lines — the
        # exact "nothing informative in the log" shape SD60 reported. `elevation.write_request`
        # -> `paths.user_data_dir()` can raise `RuntimeError` (unusable dir) or `ValueError`
        # (a relative `DISTRICTSYNC_DATA_DIR`) in addition to `OSError` — all three are the
        # same pre-consent shape, so all three are caught here. The detail is not
        # admin-actionable, so the canonical generic is returned; the log line is the record.
        return _fail(task_name, task_com.MSG_OPERATION_FAILED, verb="register")
    finally:
        _cleanup_handshake(req_path, res_path)


def delete_task_elevated(task_name: str) -> tuple[bool, str]:
    """Remove a scheduled task behind ONE UAC prompt, CONFIRMED against the real task.

    Used when the plain COM :func:`delete_task` fails with access-denied because
    the task was registered with ``RunLevel Highest``. The delete rides the SAME
    ``--elevated-apply`` child + DPAPI request as registration (uniform handshake; no
    secret in this payload, but sealing it costs nothing and keeps ONE request format).
    Removal is only reported as success when ``read_schedule`` confirms the task is gone
    (``found=False``) — the child's self-reported ``ok`` is never trusted on its own
    (security F4); an unconfirmed removal returns ``_MSG_ELEVATION_REMOVE_UNCONFIRMED``.
    A cross-account consent maps to ``_MSG_DIFFERENT_ACCOUNT`` here exactly as it does on
    the register path: the child's request-read rung serves BOTH ops (plan 0047, A8), and
    without the leg the sentinel would collapse into a detail-free floor.
    """
    task_name = validate_task_name(task_name)
    req_path: Path | None = None
    res_path: Path | None = None
    try:
        req_path = elevation.write_request({"op": "delete", "task_name": task_name})
        res_path = req_path.with_suffix(".res")
        logger.info("Removing scheduled task '%s' via one-time elevation (UAC).", task_name)
        outcome = _run_elevated_child(req_path, res_path)

        fail = _map_pre_consent_failure(outcome)
        if fail is not None:
            return _fail(task_name, fail, verb="remove")
        if outcome.result is ElevationResult.TIMEOUT:
            return _confirm_removal(
                task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED, path_label="Elevated removal"
            )

        result = elevation.read_result(res_path)
        if result is None:
            # The child wrote nothing — a read-back can still tell us whether it was removed.
            return _confirm_removal(
                task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED, path_label="Elevated removal"
            )
        if not result.get("ok"):
            child_msg = str(result.get("message", ""))
            if _DIFFERENT_ACCOUNT_SENTINEL in child_msg:
                # A8: the child's request-read rung serves BOTH ops, so a cross-account
                # consent reaches the REMOVE path too. Without this leg the sentinel is
                # collapsed by _sanitize_child_message into a no-detail floor and the admin
                # is told to "include the detail shown here" with no detail at all.
                return _fail(task_name, _MSG_DIFFERENT_ACCOUNT, verb="remove")
            msg = _sanitize_child_message(child_msg)
            return _fail(task_name, msg, verb="remove", scode=task_com.hresult_for(msg))
        # The child reported ok — CONFIRM the task is actually gone before claiming removal.
        return _confirm_removal(
            task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED, path_label="Elevated removal"
        )
    except (OSError, RuntimeError, ValueError):
        # The SAME pre-consent guard `_register_elevated` carries, on the REMOVE path: the
        # handshake (DPAPI seal, profile dir, icacls) could raise straight past the
        # (ok, message) contract to the view's floor with zero log lines. Half a funnel is
        # not a funnel — a failed removal is exactly the "nothing informative in the log"
        # shape this slice exists to close. `elevation.write_request` ->
        # `paths.user_data_dir()` can raise `RuntimeError`/`ValueError` too (see the
        # register-path twin above) — the detail is not admin-actionable, so the canonical
        # generic is returned and the anchored line is the record.
        return _fail(task_name, task_com.MSG_OPERATION_FAILED, verb="remove")
    finally:
        _cleanup_handshake(req_path, res_path)


@dataclass(frozen=True)
class ScheduleReadback:
    """The tri-state result of reading the real Windows scheduled task (D4).

    ``found`` is the load-bearing tri-state — the pure ``ui_flet.schedule_status``
    module maps it to LIVE / MISSING / UNKNOWN and NEVER asserts "scheduled" from a
    config hint when the query itself failed:

      - ``True``  — the task exists (``next_run`` / ``last_run`` / ``last_result`` /
        ``action_path`` populated as available).
      - ``False`` — the task was definitively queried and is absent
        (``task_com.HR_NOT_FOUND``) → the honest "not scheduled" signal.
      - ``None``  — the query itself failed (pywin32 missing, timeout, access
        denied, an elevated-registered task unreadable by a filtered token, or a
        non-Windows host) → "we couldn't confirm right now", NEVER "absent".

    Datetimes are NAIVE-LOCAL ISO strings from ``task_com`` (the COM layer strips
    pywin32's lying ``+00:00``); ``last_result`` is the task's ``LastTaskResult``
    HRESULT (0 = last run ok). All fields are total — a field the query couldn't
    supply is ``None``. ``error`` carries a canonical, secret-free one-liner on the
    ``found=None`` path (diagnostic only).
    """

    found: bool | None
    next_run: str | None = None
    last_run: str | None = None
    last_result: int | None = None
    action_path: str | None = None
    error: str | None = None


def read_schedule(task_name: str) -> ScheduleReadback:
    """Read the real Windows scheduled task, tri-state (D4). Never raises.

    **In-process COM since plan 0041 Slice 1a** — no ``powershell.exe`` child, no
    ``-EncodedCommand``, no console-flash risk: this probe fires on nearly every nav
    click, which made the retired subprocess the product's highest-frequency AV surface.
    The work runs on a bounded daemon worker (``task_com.bounded``, same 10s budget the
    subprocess had) with its own COM apartment.

    Classification (HRESULT-keyed — never Windows' locale-dependent message text):
      - the task reads back → ``found=True`` + facts (invariant-ISO datetimes, the
        never-run 1899-epoch nulled, ``LastTaskResult`` unsigned).
      - ``0x80070002`` (the definitive not-found; unwrapped from ``excepinfo`` — the
        outer ``hresult`` is just ``DISP_E_EXCEPTION``) → ``found=False``.
      - access denied, RPC failure, COM init failure, a timed-out worker, pywin32
        missing from the build → ``found=None`` (UNKNOWN, never "absent").
      - non-Windows host → ``found=None`` with the platform note.

    Args:
        task_name: the task name; validated via :func:`validate_task_name` first.
    """
    if sys.platform != "win32":
        return ScheduleReadback(found=None, error=_MSG_NOT_WINDOWS)

    # Guard validation so an invalid name degrades to UNKNOWN rather than raising — the probe
    # contract ("never raises") holds for every caller (the name is a config value, not PII).
    try:
        task_name = validate_task_name(task_name)
    except ValueError:
        return ScheduleReadback(found=None, error="The scheduled task name is not valid.")

    try:
        facts = task_com.bounded(
            lambda: task_com.read_task(task_name),
            timeout_s=task_com.READ_TIMEOUT_S,
            label="read",
        )
    except task_com.BoundedTimeout:
        return ScheduleReadback(found=None, error="The schedule query timed out.")
    except ImportError:
        # pywin32 missing (a frozen build that failed to bundle it) → the query could
        # not run → UNKNOWN, never "absent" (contract row 10).
        return ScheduleReadback(found=None, error=task_com.MSG_COM_UNAVAILABLE)
    except task_com.TaskComError as exc:
        if exc.scode == task_com.HR_NOT_FOUND:
            return ScheduleReadback(found=False)
        return ScheduleReadback(found=None, error=exc.message)
    except Exception as exc:  # noqa: BLE001 - the never-raises probe contract: classify, don't propagate
        logger.warning("Schedule read-back failed unexpectedly: %s", type(exc).__name__)
        return ScheduleReadback(found=None, error="The schedule query failed.")

    return ScheduleReadback(
        found=True,
        next_run=facts.next_run,
        last_run=facts.last_run,
        last_result=facts.last_result,
        action_path=facts.action_path,
    )
