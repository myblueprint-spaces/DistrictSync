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

**Readable errors:** the script's ``catch`` emits the bare exception message
via ``[Console]::Error.WriteLine`` (not ``Write-Error``, which PowerShell
CLIXML-serializes on a redirected stderr into a noisy ``#< CLIXML`` blob that
echoes the whole script). :func:`_clean_ps_stderr` is a defensive Python
fallback that decodes the human message out of any CLIXML that still arrives
(extracting only the ``<S S="Error">`` text, never the echoed script body), so
the caller and the Setup Wizard always get a clean one-liner. :func:`is_elevated`
lets the wizard tell an un-elevated "Access is denied" (run as administrator)
apart from an elevated one (a credential / batch-logon-right problem).

``delete_task`` / ``query_task`` deliberately remain on ``schtasks.exe`` —
they are read-only / name-only, have no command-length or credential surface,
and are fully tested; migrating them would expand blast radius for no benefit
(possible ROADMAP consistency follow-up).

Usage::

    from src.scheduler.windows import register_task, query_task, delete_task

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
import getpass
import logging
import os
import re

# subprocess is required to invoke powershell.exe / schtasks.exe.
import subprocess  # nosec B404
import sys
from pathlib import Path

from src.utils.validators import (
    validate_run_as_user,
    validate_run_time,
    validate_sis_type,
    validate_task_name,
)

logger = logging.getLogger(__name__)

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


def _build_register_script(*, has_password: bool) -> str:
    """Return the FIXED PowerShell registration script for the given path.

    The script is a constant string — it references only ``$env:DSYNC_*``
    variables and **never** interpolates a dynamic value into its own text, so
    there is no PowerShell string-injection surface and the exact text is
    auditable. It never echoes ``$env:DSYNC_TASK_PW``.

    Two variants:

      - *has_password* True → unattended: an **explicit**
        ``New-ScheduledTaskPrincipal -LogonType Password`` forces the
        stored-credential (``TASK_LOGON_PASSWORD``) logon, then
        ``Register-ScheduledTask -InputObject $task -User -Password`` stores it.
        ``-RunLevel`` is chosen by the env var ``$env:DSYNC_RUNLEVEL``
        (``Highest`` / ``Limited``).
      - *has_password* False → logged-on-only: ``-LogonType Interactive
        -RunLevel Limited`` and ``Register-ScheduledTask`` with no
        ``-User/-Password`` (interactive-token task; never ``S4U``).

    The run time is parsed with ``InvariantCulture`` so a non-en-US district
    locale cannot break ``'HH:mm'`` parsing. ``-StartWhenAvailable:$false``
    preserves the no-catch-up-run guarantee; the battery flags / ``PT2H``
    execution time limit / ``IgnoreNew`` multiple-instances policy preserve
    parity with the prior XML registration. ``$ProgressPreference =
    'SilentlyContinue'`` suppresses the cmdlets' progress stream so it cannot
    pollute the captured stderr the caller surfaces.
    """
    common = (
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        "try {\n"
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
    # The catch writes the bare exception message to stderr via
    # [Console]::Error.WriteLine — NOT Write-Error. Write-Error to a redirected
    # stderr is CLIXML-serialized by PowerShell (a ``#< CLIXML … <Objs>…`` blob
    # that buries the human message under the echoed script + XML noise);
    # [Console]::Error.WriteLine emits plain text so the caller surfaces a clean
    # one-liner (e.g. "Access is denied."). ``exit 1`` keeps the failure loud.
    tail = "  Write-Output 'DSYNC_OK'\n} catch {\n  [Console]::Error.WriteLine($_.Exception.Message)\n  exit 1\n}\n"
    return common + principal_and_register + tail


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
        # only via env, never argv. powershell.exe is a trusted Windows binary
        # resolved from PATH (B607). Safe by construction.
        result = subprocess.run(  # nosec B603,B607
            [
                "powershell",
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
    # task_name is validated; schtasks.exe is a trusted Windows binary.
    result = subprocess.run(  # nosec B603,B607
        ["schtasks", "/Delete", "/F", "/TN", task_name],
        capture_output=True,
        text=True,
    )
    success = result.returncode == 0
    message = (result.stdout + result.stderr).strip()
    return success, message


def query_task(task_name: str) -> dict:
    """Return basic status information about the scheduled task.

    Returns a dict with keys: ``exists``, ``status``, ``last_run``,
    ``next_run``, ``last_result``.  All values are strings.
    """
    # Read-only query; schtasks.exe is a trusted Windows binary.
    result = subprocess.run(  # nosec B603,B607
        ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"exists": False, "status": "Not Found"}

    info: dict = {"exists": True}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            info[key] = value.strip()
    return info
