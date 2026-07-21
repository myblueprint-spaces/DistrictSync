"""Windows Task Scheduler integration via PowerShell ``Register-ScheduledTask``.

Creates a daily scheduled task that runs the DistrictSync CLI at a
specified time. No third-party dependencies — uses ``subprocess`` to drive
``powershell.exe`` and the in-box ``ScheduledTasks`` module (Win8+/Server
2012+), the modern Task Scheduler COM API. Registration has no command-length
cap (unlike ``schtasks /TR``'s 261-char limit) and performs robust credential
validation + "Log on as a batch job" grant for the unattended (stored-password)
path.

**Secure invocation contract:**

  - A **fixed, auditable PowerShell script** (:func:`_build_register_script`) is
    handed to ``powershell.exe -EncodedCommand <base64>`` (the script
    UTF-16LE-base64-encoded by :func:`register_task`) — **no script file ever
    touches disk**. ``-EncodedCommand`` is used rather than piping the script to
    ``-Command -`` over stdin because, observed live on this dev box (Win11, PS
    5.1), a multi-line ``try {…} catch {…}`` block read line-by-line from stdin
    silently no-ops (exit 0, no output, registers nothing); ``-EncodedCommand``
    parses the script as one unit, so the fail-loud ``try/catch`` works.
    The script references **only** ``$env:DSYNC_*`` variables; it never
    interpolates dynamic values into its own text, eliminating PowerShell
    string-injection from district paths.
  - All dynamic values (task name, user, run time, exe, args, working dir, and
    — password path only — the password) are passed via the **child process
    environment** (:func:`_build_env`), a **fresh copy** of ``os.environ``;
    ``os.environ`` itself is never mutated. The password therefore never
    appears on the command line / argv (a hardening win over the legacy
    ``schtasks /RP <pw>`` model, which was visible to all users via the process
    list) and is never logged. The script never echoes ``$env:DSYNC_TASK_PW``.

**Unattended path (password supplied):** the script forces
``TASK_LOGON_PASSWORD`` **explicitly** via
``New-ScheduledTaskPrincipal -LogonType Password -RunLevel Highest`` (or
``-RunLevel Limited`` when *run_highest* is False), then
``Register-ScheduledTask -InputObject $task -User -Password`` stores the
credential. The explicit principal is the documented way to *force* the
stored-password logon rather than rely on parameter-set inference (which can
silently degrade to ``S4U``/``Interactive`` on some PowerShell 5.1 builds —
the failure class the prior ``schtasks /XML`` regression taught). ``S4U`` is
never used: it runs logged-off but has **no network token**, which would break
the SFTP egress the daily run depends on.

**Logged-on-only path (no password):** the principal is
``-LogonType Interactive -RunLevel Limited`` and ``Register-ScheduledTask``
runs without ``-User/-Password`` → an interactive-token task, matching the
prior logged-on-only default. *run_highest* is **ignored** without a password
(today's semantics), so ``run_highest=True`` + no password still yields
``Limited``.

**Self-elevation (Plan 0029, D5):** the unattended (password / RunLevel Highest)
registration genuinely requires an elevated caller. When the process is NOT already
elevated (``is_elevated()`` False), :func:`register_task` runs the SAME fixed
:func:`_register_body` inside a child bootstrap behind ONE normal UAC prompt via
``src/scheduler/elevation.py`` — replacing the old "quit and re-open as
administrator" dead-end while the app itself stays non-admin. The password crosses the
elevation boundary ONLY inside a DPAPI-CurrentUser-sealed request file (never argv, the
parent env, or a log); a cross-SID (different-admin) consent makes the child's
``Unprotect`` throw → it **fails closed** with the ``DSYNC_DIFFERENT_ACCOUNT`` sentinel.
Success is CONFIRMED via :func:`read_schedule` — never assumed from the child's exit
code. :func:`delete_task_elevated` removes an elevated-registered task the same way (no
secret, so no DPAPI). Already-elevated / no-password / non-Windows callers keep the
direct :func:`subprocess.run` path unchanged.

**Readable errors:** the script's ``catch`` emits the bare exception message
via ``[Console]::Error.WriteLine`` (not ``Write-Error``, which PowerShell
CLIXML-serializes on a redirected stderr into a noisy ``#< CLIXML`` blob that
echoes the whole script). :func:`_clean_ps_stderr` is a defensive Python
fallback that decodes the human message out of any CLIXML that still arrives
(extracting only the ``<S S="Error">`` text, never the echoed script body), so
the caller and the Setup Wizard always get a clean one-liner. :func:`is_elevated`
lets the wizard tell an un-elevated "Access is denied" (run as administrator)
apart from an elevated one (a credential / batch-logon-right problem).

``delete_task`` deliberately remains on ``schtasks.exe`` — it is name-only, has
no command-length or credential surface, and is fully tested; migrating it would
expand blast radius for no benefit (possible ROADMAP consistency follow-up).

**Schedule read-back (Plan 0029, D4):** :func:`read_schedule` replaces the dead
``query_task``. It combines ``Get-ScheduledTask`` (existence / action path) and
``Get-ScheduledTaskInfo`` (NextRunTime / LastRunTime / LastTaskResult) via the SAME
fixed-script + ``-EncodedCommand`` hardening as registration (name through
:func:`validate_task_name` first, no dynamic interpolation, datetimes emitted
culture-invariantly, a bounded ``subprocess`` timeout), returning a typed frozen
:class:`ScheduleReadback`. It is deliberately **tri-state**: the cmdlet's specific
task-not-found error → ``found=False`` (definitively absent); ANY other failure
(PowerShell missing, timeout, access denied — e.g. an elevated-registered task
unreadable by a filtered token) → ``found=None`` (query itself failed, never
"absent"). The pure ``ui_flet.schedule_status`` module maps this to the honest
LIVE / MISSING / UNKNOWN contract — only ``found=False`` may claim "not scheduled".

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

import base64
import contextlib
import getpass
import json
import logging
import os
import re

# subprocess is required to invoke powershell.exe / schtasks.exe.
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

from src.scheduler import elevation
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.utils.helpers import subprocess_no_window_flags, system_binary
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

# Canonical failure-message substrings returned by register_task (the message
# contract the wizard's error classifier keys off — see Slice 2). Any change
# here must be reflected in the classifier and its tests.
_MSG_NO_POWERSHELL = "PowerShell not found"
_MSG_NO_MODULE = "ScheduledTasks module not available"

# A cmdlet-not-found PowerShell error (no ScheduledTasks module, pre-Win8)
# surfaces one of these phrasings in stderr.
_CMDLET_MISSING_MARKERS = (
    "is not recognized as the name of a cmdlet",
    "CommandNotFoundException",
)

# Marker that PowerShell CLIXML-serialized a stderr stream (the noisy
# ``#< CLIXML … <Objs>…`` blob produced when ``Write-Error`` writes to a
# redirected stderr). _clean_ps_stderr decodes the human message out of it.
_CLIXML_MARKER = "#< CLIXML"

# Bounded match for the human-readable error text inside a CLIXML <S> node.
# Only the error message is extracted — never the echoed script body — so the
# ``$env:DSYNC_TASK_PW`` literal and the rest of the program cannot leak out.
_CLIXML_ERROR_NODE_RE = re.compile(r'<S\s+S="Error">(.*?)</S>', re.DOTALL)

# --- Schedule read-back (D4) --------------------------------------------------
# The read-back script tags its two success shapes on stdout so the Python parse is
# unambiguous: an existing task emits ``DSYNC_FOUND:<json>``; a definitively-absent
# task (the cmdlet's own ObjectNotFound error, caught in the script) emits ``DSYNC_ABSENT``.
_READ_FOUND_PREFIX = "DSYNC_FOUND:"
_READ_ABSENT_MARKER = "DSYNC_ABSENT"

# Bounded timeout for the read-back subprocess — a hung PowerShell can never freeze the
# UI probe; a timeout is classified as UNKNOWN (query failed), never MISSING.
_READ_TIMEOUT_S = 10

# Honest platform note surfaced when read-back is requested off Windows (Linux/macOS
# schedule read-back is out of scope — the pure module renders this as UNKNOWN).
_MSG_NOT_WINDOWS = "Schedule read-back is only available on Windows."


def _clean_ps_stderr(text: str) -> str:
    """Return a clean, human-readable one-liner from PowerShell stderr.

    When ``Write-Error`` (the legacy ``catch``) writes to a redirected stderr,
    PowerShell 5.1 serializes it as a CLIXML blob — ``#< CLIXML`` followed by an
    ``<Objs>…</Objs>`` document whose **first** ``<S S="Error">`` node holds the
    human message and whose **subsequent** ``<S S="Error">`` nodes **echo the
    failing script** (the ``At line:N``, ``+   <code>``, ``CategoryInfo``,
    ``FullyQualifiedErrorId`` lines), with literal ``_x000D_`` / ``_x000A_`` for
    CR / LF. This is defensive: the script now emits plain text via
    ``[Console]::Error.WriteLine``, but a CLIXML blob can still arrive from a
    different cmdlet/host, so we decode it here too.

    Security: only the **first** error node — the message line — is returned;
    the later nodes that echo the script body (which contains the literal
    ``$env:DSYNC_TASK_PW`` reference, not its value, plus the rest of the
    program) are **never** returned. Parsing is a bounded regex over the error
    nodes, not an XML parser fed untrusted input.

    Non-CLIXML text is returned stripped, unchanged.
    """
    if _CLIXML_MARKER not in text:
        return text.strip()

    parts = _CLIXML_ERROR_NODE_RE.findall(text)
    if not parts:
        # CLIXML present but no error node we recognize — return a safe,
        # generic message rather than echo the raw blob (which carries the
        # script body). Fail loud without leaking.
        return "PowerShell registration failed (error detail unavailable)."

    # Only the FIRST error node is the message; the rest echo the script body.
    decoded = parts[0]
    # CLIXML encodes control chars as _x00NN_ entities; decode + drop the line
    # breaks (the message node is a single logical line).
    decoded = decoded.replace("_x000D_", "").replace("_x000A_", "")
    # Strip the leading positional prefix PowerShell prepends to a piped error
    # record (e.g. "Register-ScheduledTask : Access is denied." → "Access is
    # denied."). Only strip when a "<cmd> : " prefix is actually present so a
    # message that legitimately contains a colon is not truncated.
    decoded = re.sub(r"^\S.*?\s:\s", "", decoded, count=1).strip()
    # Defense-in-depth: if the first node still carries a DSYNC_* reference (the
    # script body collapsed into node 0 — not a shape PS 5.1 emits, but the CLIXML
    # branch is untrusted), drop it rather than echo it. The password VALUE never
    # reaches stderr by construction; this guards even the variable name leaking.
    if "DSYNC_" in decoded:
        return "PowerShell registration failed (error detail unavailable)."
    return decoded


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


# The FIXED PowerShell preamble + task-setup body, shared by BOTH the direct
# registration script and the self-elevated child bootstrap (D5) — single-sourced so
# the elevated path can never fork a second, drifting copy of the registration logic.
_PS_PREAMBLE = "$ErrorActionPreference = 'Stop'\n$ProgressPreference = 'SilentlyContinue'\n"
_PS_TASK_SETUP = (
    "  $act = New-ScheduledTaskAction -Execute $env:DSYNC_EXE "
    "-Argument $env:DSYNC_ARGS -WorkingDirectory $env:DSYNC_WORKDIR\n"
    "  $at = [DateTime]::ParseExact($env:DSYNC_RUNTIME,'HH:mm',"
    "[System.Globalization.CultureInfo]::InvariantCulture)\n"
    "  $trg = New-ScheduledTaskTrigger -Daily -At $at\n"
    "  $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew "
    "-ExecutionTimeLimit (New-TimeSpan -Hours 2) "
    "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
    "-StartWhenAvailable:$false\n"
)


def _register_body(has_password: bool) -> str:
    """The FIXED, inside-``try`` registration body (task setup + principal + register).

    References ONLY ``$env:DSYNC_*`` — never interpolates a dynamic value into its own
    text, so there is no PowerShell string-injection surface and it never echoes
    ``$env:DSYNC_TASK_PW`` (it is passed to ``-Password`` but never written out).

    Two variants:

      - *has_password* True → unattended: an **explicit**
        ``New-ScheduledTaskPrincipal -LogonType Password`` forces the
        stored-credential (``TASK_LOGON_PASSWORD``) logon, then
        ``Register-ScheduledTask -InputObject $task -User -Password`` stores it.
        ``-RunLevel`` is chosen by ``$env:DSYNC_RUNLEVEL`` (``Highest`` / ``Limited``).
      - *has_password* False → logged-on-only: ``-LogonType Interactive
        -RunLevel Limited`` and ``Register-ScheduledTask`` with no
        ``-User/-Password`` (interactive-token task; never ``S4U``).
    """
    if has_password:
        principal_and_register = (
            "  $prn = New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER "
            "-LogonType Password -RunLevel $env:DSYNC_RUNLEVEL\n"
            "  $task = New-ScheduledTask -Action $act -Trigger $trg "
            "-Settings $set -Principal $prn\n"
            "  Register-ScheduledTask -TaskName $env:DSYNC_TASKNAME "
            "-InputObject $task -User $env:DSYNC_USER "
            "-Password $env:DSYNC_TASK_PW -Force | Out-Null\n"
        )
    else:
        principal_and_register = (
            "  $prn = New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER "
            "-LogonType Interactive -RunLevel Limited\n"
            "  $task = New-ScheduledTask -Action $act -Trigger $trg "
            "-Settings $set -Principal $prn\n"
            "  Register-ScheduledTask -TaskName $env:DSYNC_TASKNAME "
            "-InputObject $task -Force | Out-Null\n"
        )
    return _PS_TASK_SETUP + principal_and_register


def _build_register_script(*, has_password: bool) -> str:
    """Return the FIXED direct-registration script (used when already elevated / no password).

    The run time is parsed with ``InvariantCulture`` so a non-en-US district locale
    cannot break ``'HH:mm'`` parsing. ``-StartWhenAvailable:$false`` preserves the
    no-catch-up-run guarantee; the battery flags / ``PT2H`` execution time limit /
    ``IgnoreNew`` multiple-instances policy preserve parity with the prior XML
    registration. ``$ProgressPreference = 'SilentlyContinue'`` suppresses the cmdlets'
    progress stream so it cannot pollute the captured stderr the caller surfaces.

    The catch writes the bare exception message to stderr via ``[Console]::Error.WriteLine``
    — NOT ``Write-Error`` (which PowerShell CLIXML-serializes on a redirected stderr,
    burying the human message under the echoed script + XML noise). ``exit 1`` keeps
    the failure loud.
    """
    tail = "  Write-Output 'DSYNC_OK'\n} catch {\n  [Console]::Error.WriteLine($_.Exception.Message)\n  exit 1\n}\n"
    return _PS_PREAMBLE + "try {\n" + _register_body(has_password) + tail


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


def _build_env(
    *,
    task_name: str,
    user: str,
    run_time: str,
    exe_path: Path,
    arguments: str,
    working_dir: Path,
    run_as_password: str | None,
    run_highest: bool,
) -> dict[str, str]:
    """Build the child process environment for the PowerShell registration.

    Returns a **fresh copy** of ``os.environ`` with the ``DSYNC_*`` keys added —
    ``os.environ`` itself is never mutated, so the password never enters the
    parent process environment. ``DSYNC_RUNTIME`` carries the **raw** ``"HH:mm"``
    string (``validate_run_time`` returns a ``(hour, minute)`` tuple — the
    string is what the PowerShell ``ParseExact`` expects, not the tuple).
    ``DSYNC_TASK_PW`` is present only on the password path; ``DSYNC_RUNLEVEL``
    is only meaningful there (the no-password script hardcodes ``Limited``).
    """
    env: dict[str, str] = {
        **os.environ,
        "DSYNC_TASKNAME": task_name,
        "DSYNC_USER": user,
        "DSYNC_RUNTIME": run_time,
        "DSYNC_EXE": str(exe_path),
        "DSYNC_ARGS": arguments,
        "DSYNC_WORKDIR": str(working_dir),
    }
    if run_as_password:
        env["DSYNC_TASK_PW"] = run_as_password
        env["DSYNC_RUNLEVEL"] = "Highest" if run_highest else "Limited"
    return env


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
    """Create or replace a Windows scheduled task via PowerShell.

    A fixed PowerShell script (the ``ScheduledTasks`` module) is passed to
    ``powershell.exe -EncodedCommand`` (UTF-16LE-base64); all dynamic values —
    including the password — are passed via the **child process environment**,
    never on the command line and never interpolated into the script text. No
    script file touches disk. ``-EncodedCommand`` (not ``-Command -``/stdin) is
    used because, observed live on this dev box (Win11, PS 5.1), a multi-line
    ``try/catch`` read line-by-line from stdin silently no-ops (exit 0, registers
    nothing); ``-EncodedCommand`` parses the script as one unit so the fail-loud
    ``try/catch`` works.

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
                   provided, the task is registered to run **whether the user
                   is logged on or not** (explicit ``-LogonType Password``
                   principal + ``Register-ScheduledTask -User -Password``).
                   When omitted, the task runs only while the user is logged on
                   (``-LogonType Interactive``) and no credential is stored —
                   preserving backward compatibility for existing callers and
                   dev-mode scheduling. The password is passed only via the
                   child env var ``DSYNC_TASK_PW``; it is never logged, never on
                   the command line, and never echoed by the script.
        run_highest: When True and a password is supplied, run with highest
                   privileges (``-RunLevel Highest``). Ignored without a
                   password (the logged-on-only path is always ``Limited``).

    Returns:
        (success, message). On failure the *message* contains
        ``"PowerShell not found"`` (no ``powershell.exe``),
        ``"ScheduledTasks module not available"`` (no ScheduledTasks module),
        or the raw PowerShell exception text passed through verbatim (so the
        wizard classifier can match Windows' own "Access is denied" / "The user
        name or password is incorrect" / logon-right wording). The password
        value and the ``DSYNC_TASK_PW`` literal never appear in the message.
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
    # registration behind ONE normal UAC prompt via the elevation IPC primitive —
    # replacing the old "quit and re-open as administrator" dead-end — while the app
    # itself stays non-admin. Already-elevated callers, the no-password logged-on-only
    # path, and non-Windows hosts keep the direct subprocess path below unchanged.
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

    script = _build_register_script(has_password=has_password)
    # PowerShell -EncodedCommand expects UTF-16LE-base64. The script is a fixed
    # string referencing only $env:DSYNC_* — no dynamic value is ever encoded
    # into it (those flow via the child env), so the encoded blob carries no
    # secret and no untrusted input.
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    child_env = _build_env(
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

    try:
        # Inputs validated above; the encoded script is a fixed string referencing
        # only $env:DSYNC_* (no untrusted-value interpolation); passed as a list
        # with shell=False so nothing is re-parsed; the password reaches the child
        # only via env, never argv. powershell.exe is resolved to its ABSOLUTE
        # System32 path (system_binary) — never a bare name, whose CreateProcess
        # search order probes the calling exe's dir + the CWD before System32 and
        # would hand DSYNC_TASK_PW to a planted binary. Safe by construction.
        result = subprocess.run(  # nosec B603
            [
                system_binary("powershell.exe"),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            env=child_env,
            capture_output=True,
            text=True,
            creationflags=subprocess_no_window_flags(),  # no console flash in the windowed exe
        )
    except FileNotFoundError:
        # powershell.exe absent from PATH — fail loud, never crash the caller.
        logger.error(f"Failed to register task '{task_name}': {_MSG_NO_POWERSHELL}")
        return False, _MSG_NO_POWERSHELL

    stdout = (result.stdout or "").strip()
    # _clean_ps_stderr strips any PowerShell CLIXML serialization down to the
    # human error line BEFORE the marker match below, so canonical markers still
    # match and the echoed script body (which contains the literal
    # $env:DSYNC_TASK_PW reference) never reaches the caller. The script's catch
    # already emits plain text via [Console]::Error.WriteLine; this is the
    # defensive fallback for any CLIXML arriving from another cmdlet/host.
    stderr = _clean_ps_stderr(result.stderr or "")
    success = result.returncode == 0 and "DSYNC_OK" in stdout

    if success:
        logger.info(f"Task '{task_name}' registered successfully")
        return True, stdout

    # Map a missing ScheduledTasks module (pre-Win8) to a distinct, actionable
    # message; otherwise surface the cleaned PowerShell exception text so the
    # wizard classifier can match Windows' own credential/logon wording.
    if any(marker in stderr for marker in _CMDLET_MISSING_MARKERS):
        message = _MSG_NO_MODULE
    else:
        message = stderr or stdout or "PowerShell registration failed with no output."

    # stderr is cleaned (de-CLIXML'd) but otherwise surfaced as-is. register_task
    # never adds the password to the message or logs — the script never echoes
    # $env:DSYNC_TASK_PW, the value is not in argv, and _clean_ps_stderr drops the
    # echoed script body; tested on the failure path. (We do not re-scrub
    # PowerShell's own exception text; the cmdlets are not known to echo the
    # supplied -Password/-User, but that is not asserted as a guarantee.)
    logger.error(f"Failed to register task '{task_name}': {message}")
    return False, message


def delete_task(task_name: str) -> tuple[bool, str]:
    """Remove a scheduled task by name.

    Returns:
        (success, message)
    """
    task_name = validate_task_name(task_name)
    # task_name is validated; schtasks.exe is resolved to its ABSOLUTE System32 path
    # (system_binary) so CreateProcess cannot substitute a binary planted in the calling
    # exe's directory or the current directory — both probed before System32.
    result = subprocess.run(  # nosec B603
        [system_binary("schtasks.exe"), "/Delete", "/F", "/TN", task_name],
        capture_output=True,
        text=True,
        creationflags=subprocess_no_window_flags(),  # no console flash in the windowed exe
    )
    success = result.returncode == 0
    message = (result.stdout + result.stderr).strip()
    return success, message


# --- Per-operation elevation (D5) --------------------------------------------
# When the app is NOT already elevated, an unattended register (stored-password /
# RunLevel Highest) — and deleting an elevated-registered task — self-elevate behind
# ONE UAC prompt via src/scheduler/elevation.py. The register path carries the Windows
# password ONLY inside a DPAPI-CurrentUser-sealed request file (never argv / the parent
# env / logs); the elevated child FAILS CLOSED on a cross-SID unprotect. Success is
# CONFIRMED by reading the real task back (read_schedule) — never assumed from an exit code.

# The FIXED atomic-write helper the elevated child uses to publish its plaintext result
# ({ok, message}) — temp file + Move-Item so the parent never observes a partial write.
_PS_WRITE_RESULT_FN = (
    "function Write-DsyncResult($okv, $msgv) {\n"
    "  $o = [PSCustomObject]@{ ok = $okv; message = $msgv }\n"
    "  $enc = New-Object System.Text.UTF8Encoding($false)\n"
    "  $tmp = $resPath + '.tmp'\n"
    "  [System.IO.File]::WriteAllText($tmp, ($o | ConvertTo-Json -Compress), $enc)\n"
    "  Move-Item -LiteralPath $tmp -Destination $resPath -Force\n"
    "}\n"
)


def _b64(value: str) -> str:
    """UTF-8-base64 a string for injection-proof embedding in a FIXED PowerShell script.

    Base64 output is ``[A-Za-z0-9+/=]`` only, so a value that could otherwise break the
    surrounding single-quoted PowerShell literal (a user-profile path with an apostrophe
    or space, embedded because ShellExecuteEx cannot pass env to the elevated child)
    can never inject. The handshake PATHS are ours (random names under user_data_dir,
    not user-controlled); the task name is ``validate_task_name``-checked first — base64
    is belt-and-suspenders on top of both.
    """
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _build_elevated_register_script(*, has_password: bool, req_path: Path, res_path: Path) -> str:
    """FIXED child bootstrap: DPAPI-unprotect the request → set env → run the SAME register body.

    The request/result paths AND the single-sourced ``elevation.DPAPI_ENTROPY_UTF8`` are all
    base64-embedded (ShellExecuteEx can't pass env — base64 is uniformly injection-proof). A DPAPI ``Unprotect``
    failure — the cross-SID / different-admin case — writes the ``DSYNC_DIFFERENT_ACCOUNT``
    sentinel and exits (**fail closed, CurrentUser scope only, never LocalMachine**). On
    success it runs ``_register_body`` (single-sourced with the direct path) and writes a
    plaintext ``{ok, message}`` result — it NEVER writes ``$env:DSYNC_TASK_PW``.
    """
    return (
        _PS_PREAMBLE
        + "Add-Type -AssemblyName System.Security\n"
        + f"$reqPath = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{_b64(str(req_path))}'))\n"
        + f"$resPath = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{_b64(str(res_path))}'))\n"
        + f"$entropy = [System.Convert]::FromBase64String('{_b64(elevation.DPAPI_ENTROPY_UTF8)}')\n"
        + _PS_WRITE_RESULT_FN
        + "try {\n"
        + "  $blob = [System.IO.File]::ReadAllBytes($reqPath)\n"
        + "  $plain = [System.Security.Cryptography.ProtectedData]::Unprotect("
        "$blob, $entropy, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)\n"
        + "  $payload = [System.Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json\n"
        + "} catch {\n"
        + f"  Write-DsyncResult $false '{_DIFFERENT_ACCOUNT_SENTINEL}'\n"
        + "  exit 0\n"
        + "}\n"
        + "$env:DSYNC_TASKNAME = $payload.DSYNC_TASKNAME\n"
        + "$env:DSYNC_USER = $payload.DSYNC_USER\n"
        + "$env:DSYNC_RUNTIME = $payload.DSYNC_RUNTIME\n"
        + "$env:DSYNC_EXE = $payload.DSYNC_EXE\n"
        + "$env:DSYNC_ARGS = $payload.DSYNC_ARGS\n"
        + "$env:DSYNC_WORKDIR = $payload.DSYNC_WORKDIR\n"
        + "$env:DSYNC_TASK_PW = $payload.DSYNC_TASK_PW\n"
        + "$env:DSYNC_RUNLEVEL = $payload.DSYNC_RUNLEVEL\n"
        + "try {\n"
        + _register_body(has_password)
        + "  Write-DsyncResult $true 'Registered.'\n"
        + "} catch {\n"
        + "  Write-DsyncResult $false $_.Exception.Message\n"
        + "}\n"
    )


def _build_elevated_delete_script(*, task_name: str, res_path: Path) -> str:
    """FIXED child bootstrap for an elevated delete — no password / DPAPI (a delete carries no secret).

    The validated task name is base64-embedded (injection-proof) and removed via
    ``Unregister-ScheduledTask``; a plaintext ``{ok, message}`` result is written atomically.
    """
    return (
        _PS_PREAMBLE
        + f"$resPath = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{_b64(str(res_path))}'))\n"
        + f"$taskName = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{_b64(task_name)}'))\n"
        + _PS_WRITE_RESULT_FN
        + "try {\n"
        + "  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false | Out-Null\n"
        + "  Write-DsyncResult $true 'Removed.'\n"
        + "} catch {\n"
        + "  Write-DsyncResult $false $_.Exception.Message\n"
        + "}\n"
    )


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
    """De-CLIXML + ``DSYNC_``-strip a child result message before it can surface.

    Mirrors the register_task stderr discipline: ``_clean_ps_stderr`` strips any CLIXML,
    then any residual ``DSYNC_`` token (which a normal PowerShell exception never carries)
    collapses to a safe generic line rather than risk echoing an env-var name. The
    password VALUE never reaches the result by construction — this is defense-in-depth.
    """
    cleaned = _clean_ps_stderr(message or "")
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
    gets only the encoded bootstrap + base64 paths), never the parent env, never a log.
    Success requires BOTH the child's ``ok`` AND a positive ``read_schedule`` confirmation;
    the handshake files are deleted in ``finally``.
    """
    payload: dict[str, object] = {
        "DSYNC_TASKNAME": task_name,
        "DSYNC_USER": user,
        "DSYNC_RUNTIME": run_time,
        "DSYNC_EXE": str(exe_path),
        "DSYNC_ARGS": arguments,
        "DSYNC_WORKDIR": str(working_dir),
        "DSYNC_TASK_PW": run_as_password,
        "DSYNC_RUNLEVEL": "Highest" if run_highest else "Limited",
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
        script = _build_elevated_register_script(has_password=True, req_path=req_path, res_path=res_path)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        outcome = elevation.run_elevated_powershell(encoded, timeout_s=_ELEV_TIMEOUT_S)

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

    Used when the plain ``schtasks`` :func:`delete_task` fails with access-denied because
    the task was registered with ``RunLevel Highest``. No password / DPAPI is involved (a
    delete carries no secret); the validated task name is base64-embedded (injection-proof)
    in a FIXED bootstrap. Removal is only reported as success when ``read_schedule`` confirms
    the task is gone (``found=False``) — the child's self-reported ``ok`` is never trusted on
    its own (security F4); an unconfirmed removal returns ``_MSG_ELEVATION_REMOVE_UNCONFIRMED``.
    """
    task_name = validate_task_name(task_name)
    res_path: Path | None = None
    try:
        res_path = elevation.new_result_path()
        script = _build_elevated_delete_script(task_name=task_name, res_path=res_path)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        logger.info("Removing scheduled task '%s' via one-time elevation (UAC).", task_name)
        outcome = elevation.run_elevated_powershell(encoded, timeout_s=_ELEV_TIMEOUT_S)

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
        _cleanup_handshake(res_path)


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


def _build_read_script() -> str:
    """Return the FIXED PowerShell read-back script (same hardening as registration).

    A constant string referencing ONLY ``$env:DSYNC_TASKNAME`` — no dynamic value is
    ever interpolated into the text, so there is no PowerShell string-injection surface.
    ``Get-ScheduledTask`` supplies existence + the action's ``Execute`` path;
    ``Get-ScheduledTaskInfo`` supplies ``NextRunTime`` / ``LastRunTime`` /
    ``LastTaskResult``. Datetimes are emitted with ``InvariantCulture`` ISO round-trip
    (``'o'``) so a non-en-US locale can't corrupt the parse. The never-run ``LastRunTime``
    sentinel (year <= 1900) is nulled so it can't masquerade as a real prior run. The
    ``catch`` distinguishes the cmdlet's ObjectNotFound (→ ``DSYNC_ABSENT``, a definitive
    "not scheduled") from any other failure (→ plain-text stderr + ``exit 1`` → the caller
    classifies UNKNOWN). ``$ProgressPreference`` is silenced so it can't pollute stderr.
    """
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        "try {\n"
        "  $task = Get-ScheduledTask -TaskName $env:DSYNC_TASKNAME\n"
        "  $info = Get-ScheduledTaskInfo -TaskName $env:DSYNC_TASKNAME\n"
        "  $inv = [System.Globalization.CultureInfo]::InvariantCulture\n"
        "  $exec = $null\n"
        "  if ($task.Actions -and @($task.Actions).Count -gt 0) { $exec = @($task.Actions)[0].Execute }\n"
        "  $next = $null\n"
        "  if ($info.NextRunTime) { $next = $info.NextRunTime.ToString('o', $inv) }\n"
        "  $last = $null\n"
        "  if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1900) "
        "{ $last = $info.LastRunTime.ToString('o', $inv) }\n"
        "  $obj = [PSCustomObject]@{ found = $true; next_run = $next; last_run = $last; "
        "last_result = $info.LastTaskResult; action_path = $exec }\n"
        "  Write-Output ('DSYNC_FOUND:' + ($obj | ConvertTo-Json -Compress))\n"
        "} catch {\n"
        "  if ($_.CategoryInfo.Category -eq 'ObjectNotFound' -or "
        "$_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') {\n"
        "    Write-Output 'DSYNC_ABSENT'\n"
        "    exit 0\n"
        "  }\n"
        "  [Console]::Error.WriteLine($_.Exception.Message)\n"
        "  exit 1\n"
        "}\n"
    )


def read_schedule(task_name: str) -> ScheduleReadback:
    """Read the real Windows scheduled task, tri-state (D4). Never raises.

    Runs the fixed :func:`_build_read_script` via ``powershell.exe -EncodedCommand``
    (UTF-16LE-base64), passing only the validated task name through the child env
    (``DSYNC_TASKNAME``) — never interpolated into the script, never on argv. The
    subprocess is bounded by :data:`_READ_TIMEOUT_S`.

    Classification:
      - stdout ``DSYNC_ABSENT`` (rc 0) → ``found=False`` (definitively not scheduled).
      - stdout ``DSYNC_FOUND:<json>`` (rc 0) → ``found=True`` + parsed fields.
      - PowerShell missing / timeout / any other non-zero exit (access denied, no
        ScheduledTasks module, unparseable output) → ``found=None`` (UNKNOWN).
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

    script = _build_read_script()
    # Fixed script referencing only $env:DSYNC_TASKNAME — the encoded blob carries no
    # secret and no untrusted interpolation (identical hardening to register_task).
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    child_env = {**os.environ, "DSYNC_TASKNAME": task_name}

    try:
        # Inputs validated; fixed encoded script; list args + shell=False; read-only
        # cmdlets. powershell.exe is resolved to its ABSOLUTE System32 path
        # (system_binary) — this probe fires on every nav click, so a bare name would
        # give a planted binary repeated execution under the interactive user.
        result = subprocess.run(  # nosec B603
            [
                system_binary("powershell.exe"),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=_READ_TIMEOUT_S,
            creationflags=subprocess_no_window_flags(),  # THE nav-click flasher — no console in the windowed exe
        )
    except FileNotFoundError:
        # No powershell.exe on PATH — the query couldn't run → UNKNOWN, never "absent".
        return ScheduleReadback(found=None, error=_MSG_NO_POWERSHELL)
    except subprocess.TimeoutExpired:
        return ScheduleReadback(found=None, error="The schedule query timed out.")

    stdout = (result.stdout or "").strip()
    stderr = _clean_ps_stderr(result.stderr or "")

    if result.returncode == 0 and _READ_ABSENT_MARKER in stdout:
        return ScheduleReadback(found=False)
    if result.returncode == 0 and _READ_FOUND_PREFIX in stdout:
        return _parse_readback(stdout, stderr)

    # Anything else — a non-zero exit (access denied, missing ScheduledTasks module),
    # empty output, or an unexpected shape — is a FAILED query, not an absent task.
    return ScheduleReadback(found=None, error=stderr or "The schedule query failed.")


def _parse_readback(stdout: str, stderr: str) -> ScheduleReadback:
    """Parse the ``DSYNC_FOUND:<json>`` success line into a ``ScheduleReadback``.

    A malformed/non-dict payload degrades to UNKNOWN (``found=None``) rather than
    asserting a shape we couldn't read — total, never raises.
    """
    line = next((ln for ln in stdout.splitlines() if ln.startswith(_READ_FOUND_PREFIX)), "")
    payload = line[len(_READ_FOUND_PREFIX) :]
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return ScheduleReadback(found=None, error="Could not parse the schedule query result.")
    if not isinstance(data, dict):
        return ScheduleReadback(found=None, error="Could not parse the schedule query result.")
    return ScheduleReadback(
        found=True,
        next_run=_opt_str_field(data.get("next_run")),
        last_run=_opt_str_field(data.get("last_run")),
        last_result=_opt_int_field(data.get("last_result")),
        action_path=_opt_str_field(data.get("action_path")),
    )


def _opt_str_field(value: object) -> str | None:
    """A nullable text field from the JSON payload: blank/None → None; else stripped str."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int_field(value: object) -> int | None:
    """A nullable int field (``LastTaskResult``): None / non-numeric → None."""
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
