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
  - The settings quintet (no catch-up, IgnoreNew, PT2H, both battery flags) is set
    explicitly in ``task_com.apply_definition`` — COM defaults differ on all five.
  - Failure messages are the ``task_com`` HRESULT-keyed canonicals ("Access is
    denied." / "The user name or password is incorrect." / Windows' own description
    for unmapped statuses), so ``setup_errors.classify_schedule_error`` keeps matching
    exactly the strings it always matched — now locale-independent.

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
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.utils.validators import (
    validate_run_as_user,
    validate_run_time,
    validate_sis_type,
    validate_task_name,
)

logger = logging.getLogger(__name__)

# Bounded wait for the elevated child (D5) — never INFINITE. WaitForSingleObject waits
# for the elevated PowerShell to finish registering (the UAC-consent delay happens
# inside ShellExecuteEx, which the OS bounds by its own prompt timeout), so 120s is
# generous headroom for a slow Register-ScheduledTask without ever freezing the flow.
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

# The sentinel the elevated child writes to its result file when the DPAPI unprotect
# FAILS (a cross-SID / different-admin UAC consent — fail closed). The parent detects
# it BEFORE sanitizing (it deliberately carries the DSYNC_ prefix a normal message
# never would) and maps it to the bounded _MSG_DIFFERENT_ACCOUNT category.
_DIFFERENT_ACCOUNT_SENTINEL = "DSYNC_DIFFERENT_ACCOUNT"

# The run-history store's source tag for the nightly scheduled run (Plan 0029, D2c).
# Carried on the registered task's action command line (``--source scheduled``) so the
# store labels the nightly run correctly from day one. Mirrors ``history.store``'s
# ``VALID_SOURCES`` value without importing the store into the scheduler layer.
_SCHEDULED_SOURCE = "scheduled"

# The "PowerShell not found" / "ScheduledTasks module not available" canonical messages,
# the CLIXML decoder and its regexes all RETIRED at plan 0041 S1b with their transport —
# the COM analogue of an unavailable engine is task_com.MSG_COM_UNAVAILABLE (row 10).

# --- Schedule read-back (D4) --------------------------------------------------
# In-process COM since plan 0041 Slice 1a (src/scheduler/task_com.py) — the DSYNC_FOUND /
# DSYNC_ABSENT stdout protocol and the subprocess timeout retired with the PowerShell
# transport; tri-state classification is HRESULT-keyed at the task_com boundary, and the
# 10s bound lives at task_com.READ_TIMEOUT_S (same budget, same UNKNOWN-on-timeout rule).

# Honest platform note surfaced when read-back is requested off Windows (Linux/macOS
# schedule read-back is out of scope — the pure module renders this as UNKNOWN).
_MSG_NOT_WINDOWS = "Schedule read-back is only available on Windows."


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
        run_as_user: Windows account the task runs as. Defaults to the current
                   interactive user (:func:`current_run_as_user`) when a
                   password is supplied. Validated via
                   :func:`validate_run_as_user`.
        run_as_password: The ``run_as_user`` account's Windows password. When
                   provided, the task is registered to run **whether the user is
                   logged on or not** (explicit ``TASK_LOGON_PASSWORD`` — never
                   parameter-set inference, never S4U). When omitted, the task
                   runs only while the user is logged on
                   (``TASK_LOGON_INTERACTIVE_TOKEN``) and no credential is stored.
        run_highest: When True and a password is supplied, run with highest
                   privileges (``TASK_RUNLEVEL_HIGHEST``). Ignored without a
                   password (the logged-on-only path is always Limited).

    Returns:
        (success, message). Failure messages are the ``task_com`` canonical
        strings — "Access is denied." / "The user name or password is
        incorrect." / Windows' own description for unmapped statuses — so the
        wizard classifier keeps matching exactly what it matched before. A
        registration that TIMES OUT resolves through :func:`_confirm_registration`
        (the worker cannot be cancelled and may still complete — a bare "failed"
        over a task that now exists would be a lie; row 14).
    """
    # Validate all user-supplied values before touching the OS.
    task_name = validate_task_name(task_name)
    sis_type = validate_sis_type(sis_type)
    validate_run_time(run_time)

    has_password = bool(run_as_password)
    if has_password:
        user = validate_run_as_user(run_as_user or current_run_as_user())
    else:
        user = current_run_as_user()

    arguments, working_dir = _build_action_args(exe_path, sis_type, input_dir, output_dir, sftp)

    # Self-elevation (D5): an unattended (password / RunLevel Highest) registration
    # genuinely requires an elevated caller. When we are NOT already elevated, run the
    # registration behind ONE normal UAC prompt — the child is DistrictSync itself in
    # --elevated-apply mode since S1b — while the app itself stays non-admin.
    if has_password and sys.platform == "win32" and not is_elevated():
        assert run_as_password is not None  # has_password == bool(run_as_password)  # nosec B101
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
        return _confirm_registration(task_name, on_unconfirmed=_MSG_ELEVATION_TIMEOUT)
    except ImportError:
        logger.error(f"Failed to register task '{task_name}': {task_com.MSG_COM_UNAVAILABLE}")
        return False, task_com.MSG_COM_UNAVAILABLE
    except task_com.TaskComError as exc:
        # Canonical, secret-free text (task_com never formats argv/env/password into
        # messages) — surfaced as-is so the wizard classifier matches its known strings.
        logger.error(f"Failed to register task '{task_name}': {exc.message}")
        return False, exc.message
    except Exception as exc:  # noqa: BLE001 - (ok, message) contract: classify, never propagate
        logger.warning("Task registration failed unexpectedly: %s", type(exc).__name__)
        return False, "The schedule operation failed."

    logger.info(f"Task '{task_name}' registered successfully")
    return True, "Schedule registered."


def delete_task(task_name: str) -> tuple[bool, str]:
    """Remove a scheduled task by name. Never raises — ``(success, message)``.

    **In-process COM since plan 0041 Slice 1a** (``Folder.DeleteTask``), retiring the
    last ``schtasks.exe`` call — after which ``schtasks.exe`` left the ``system_binary``
    allowlist entirely. Two message contracts are LOAD-BEARING and pinned:

    - an already-absent task (``0x80070002``) returns its canonical text "The system
      cannot find the file specified." — the "cannot find" marker keeps
      ``schedule_status.interpret_unregister`` idempotent-success-shaped, exactly as the
      schtasks stderr did;
    - access denied returns "Access is denied." — the substring the
      ``WindowsTaskScheduler.delete`` adapter's elevated-retry predicate matches
      (contract row 13). Mapped by HRESULT, never by locale text.
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
        return _confirm_removal(task_name, on_unconfirmed="The schedule removal timed out.")
    except ImportError:
        return False, task_com.MSG_COM_UNAVAILABLE
    except task_com.TaskComError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001 - (ok, message) contract: classify, don't propagate
        logger.warning("Schedule delete failed unexpectedly: %s", type(exc).__name__)
        return False, "The schedule operation failed."
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
    if "DSYNC_" in cleaned:
        return "The schedule change failed (error detail unavailable)."
    return cleaned or "The schedule change failed with no detail."


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


def _confirm_registration(task_name: str, *, on_unconfirmed: str) -> tuple[bool, str]:
    """Confirm a registration against the REAL task; unconfirmed → ``(False, on_unconfirmed)``.

    Success (exit code / child ``ok`` / a long-running TIMEOUT) is only ever asserted when
    ``read_schedule`` reports ``found=True``. An elevated-registered task a filtered token
    can't read yields ``found=None`` → honestly unconfirmed, never a false green.
    """
    readback = read_schedule(task_name)
    if readback.found is True:
        logger.info("Scheduled task '%s' registered and confirmed via read-back.", task_name)
        return True, "Schedule registered and confirmed."
    logger.warning("Elevated registration of '%s' could not be confirmed via read-back.", task_name)
    return False, on_unconfirmed


def _confirm_removal(task_name: str, *, on_unconfirmed: str) -> tuple[bool, str]:
    """Confirm a removal against the REAL task; only ``found=False`` is a confirmed removal.

    ``found=True`` (still there) or ``found=None`` (unreadable) → ``(False, on_unconfirmed)`` —
    the flow must not assert the schedule was removed when it couldn't be confirmed.
    """
    readback = read_schedule(task_name)
    if readback.found is False:
        logger.info("Scheduled task '%s' removal confirmed via read-back.", task_name)
        return True, "Schedule removed and confirmed."
    logger.warning("Elevated removal of '%s' could not be confirmed via read-back.", task_name)
    return False, on_unconfirmed


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
            return False, fail
        if outcome.result is ElevationResult.TIMEOUT:
            # Post-consent timeout: the terminated child may already have registered — confirm.
            logger.warning("Elevated registration of '%s' timed out; confirming via read-back.", task_name)
            return _confirm_registration(task_name, on_unconfirmed=_MSG_ELEVATION_TIMEOUT)

        result = elevation.read_result(res_path)
        if result is None:
            logger.error("Elevated registration of '%s' produced no readable result.", task_name)
            return _confirm_registration(task_name, on_unconfirmed=_MSG_ELEVATION_NO_RESULT)
        if not result.get("ok"):
            child_msg = str(result.get("message", ""))
            if _DIFFERENT_ACCOUNT_SENTINEL in child_msg:
                return False, _MSG_DIFFERENT_ACCOUNT
            return False, _sanitize_child_message(child_msg)
        # The child reported ok — CONFIRM against the real task (exit code alone is not success).
        return _confirm_registration(task_name, on_unconfirmed=_MSG_ELEVATION_NO_RESULT)
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
            return False, fail
        if outcome.result is ElevationResult.TIMEOUT:
            return _confirm_removal(task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED)

        result = elevation.read_result(res_path)
        if result is None:
            # The child wrote nothing — a read-back can still tell us whether it was removed.
            return _confirm_removal(task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED)
        if not result.get("ok"):
            return False, _sanitize_child_message(str(result.get("message", "")))
        # The child reported ok — CONFIRM the task is actually gone before claiming removal.
        return _confirm_removal(task_name, on_unconfirmed=_MSG_ELEVATION_REMOVE_UNCONFIRMED)
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
      - ``False`` — the task was definitively queried and is absent (the cmdlet's
        own ObjectNotFound error) → the honest "not scheduled" signal.
      - ``None``  — the query itself failed (PowerShell missing, timeout, access
        denied, an elevated-registered task unreadable by a filtered token, or a
        non-Windows host) → "we couldn't confirm right now", NEVER "absent".

    Datetimes are the raw ISO round-trip strings PowerShell emits
    (``.ToString("o", InvariantCulture)``); ``last_result`` is the task's
    ``LastTaskResult`` HRESULT (0 = last run ok). All fields are total — a field
    the query couldn't supply is ``None``. ``error`` carries a de-CLIXML'd,
    secret-free one-liner on the ``found=None`` path (diagnostic only).
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
