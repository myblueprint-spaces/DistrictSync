"""Windows Task Scheduler integration via ``schtasks.exe``.

Creates a daily scheduled task that runs the DistrictSync CLI at a
specified time.  No third-party dependencies — uses subprocess only.

Registration uses Task Scheduler **XML** (``schtasks /Create /XML``) rather
than an inline ``/TR`` command. ``/TR`` is capped at 261 characters by
``schtasks``; the source-mode command (which must ``cd`` into the project
root and invoke ``python -m src.main`` with quoted input/output paths)
routinely exceeds that limit. XML has no length cap, sets the working
directory natively via ``<WorkingDirectory>`` (no brittle
``cmd /c "cd /d ..."`` wrapper), and carries the schedule, principal, and
run level. The run-as password is never written into the XML — it is passed
to ``schtasks`` via ``/RP`` on the command line and redacted from all logs.

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

import getpass
import logging
import os

# subprocess is required to invoke schtasks.exe.
import subprocess  # nosec B404
import tempfile
from pathlib import Path

# Used only to ESCAPE values we write into XML (output side); no XML is parsed here.
from xml.sax.saxutils import escape as _xml_escape  # nosec B406

from src.utils.validators import (
    validate_run_as_user,
    validate_run_time,
    validate_sis_type,
    validate_task_name,
)

logger = logging.getLogger(__name__)

# Task Scheduler XML namespace + schema version (Task Scheduler 1.2).
_TASK_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_TASK_XML_VERSION = "1.2"
# Fixed past date for the daily trigger's StartBoundary. A daily trigger with
# a past start boundary fires every day at the configured time; the fixed date
# keeps the emitted XML deterministic (testable) and avoids any catch-up run.
_START_DATE = "2024-01-01"


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


def _redact_cmd(cmd: list[str]) -> str:
    """Join *cmd* for display, masking the password that follows ``/RP``.

    The schtasks ``/RP <password>`` value is replaced with ``***`` so the
    run-as password never reaches a log or an error message. The raw list is
    never logged directly — every echo of the command goes through here.
    """
    redacted: list[str] = []
    mask_next = False
    for arg in cmd:
        if mask_next:
            redacted.append("***")
            mask_next = False
            continue
        redacted.append(arg)
        if arg == "/RP":
            mask_next = True
    return " ".join(redacted)


def _build_task_xml(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    sftp: bool,
    run_time: str,
    *,
    run_as_user: str | None = None,
    run_as_password: str | None = None,
    run_highest: bool = True,
) -> str:
    """Build a Task Scheduler 1.2 XML document for the daily DistrictSync run.

    Two execution modes (detected by the exe name, matching the historical
    behaviour):

      - Python interpreter (dev / source install): ``<Command>`` is the
        ``python.exe`` path, ``<Arguments>`` is ``-m src.main --sis X
        --input "Y" --output "Z" [--sftp]``, and ``<WorkingDirectory>`` is the
        project root — so Python finds the ``src`` package without a
        ``cmd /c "cd /d ..."`` wrapper. Without ``-m`` Python would treat
        ``--sis`` as a script path and fail with 0x80070002.
      - Frozen PyInstaller binary (e.g. DistrictSync.exe): ``<Command>`` is the
        exe, ``<Arguments>`` omits ``-m src.main``, and ``<WorkingDirectory>``
        is the exe's parent directory.

    Principal:
      - With a ``run_as_password``: ``<UserId>`` is the resolved run-as user,
        ``<LogonType>Password</LogonType>`` (runs whether or not the user is
        logged on), ``<RunLevel>`` is ``HighestAvailable`` when *run_highest*
        else ``LeastPrivilege``.
      - Without a password: ``<UserId>`` is the current interactive user,
        ``<LogonType>InteractiveToken</LogonType>``, ``<RunLevel>`` is
        ``LeastPrivilege`` (logged-on-only default; *run_highest* is ignored
        without a password, matching the prior ``/TR`` behaviour).

    Every interpolated value (paths, user id, arguments) is XML-escaped at this
    boundary to prevent XML injection and to correctly carry ``&``/``<``/``>``
    in paths. The password is never placed in the XML — it is passed to
    ``schtasks`` via ``/RP`` by :func:`register_task`.
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
    arguments = " ".join(arg_parts)

    if run_as_password:
        user_id = validate_run_as_user(run_as_user or current_run_as_user())
        logon_type = "Password"
        run_level = "HighestAvailable" if run_highest else "LeastPrivilege"
    else:
        user_id = current_run_as_user()
        logon_type = "InteractiveToken"
        run_level = "LeastPrivilege"

    # XML-escape every interpolated value at this boundary (validate-at-boundary).
    command_x = _xml_escape(str(exe_path))
    arguments_x = _xml_escape(arguments)
    working_dir_x = _xml_escape(str(working_dir))
    user_id_x = _xml_escape(user_id)
    start_boundary_x = _xml_escape(f"{_START_DATE}T{run_time}:00")

    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="{_TASK_XML_VERSION}" xmlns="{_TASK_XML_NS}">\n'
        "  <RegistrationInfo>\n"
        "    <Description>DistrictSync daily run</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{start_boundary_x}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByDay>\n"
        "        <DaysInterval>1</DaysInterval>\n"
        "      </ScheduleByDay>\n"
        "    </CalendarTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{user_id_x}</UserId>\n"
        f"      <LogonType>{logon_type}</LogonType>\n"
        f"      <RunLevel>{run_level}</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>false</StartWhenAvailable>\n"
        "    <Enabled>true</Enabled>\n"
        "    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{command_x}</Command>\n"
        f"      <Arguments>{arguments_x}</Arguments>\n"
        f"      <WorkingDirectory>{working_dir_x}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


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
    """Create or replace a Windows scheduled task via Task Scheduler XML.

    The schedule, working directory, principal, and run level are carried in a
    Task Scheduler 1.2 XML document registered with ``schtasks /Create /XML``
    (no inline ``/TR`` command, so the 261-character ``/TR`` limit no longer
    applies). The XML is written to a temporary file as UTF-16-with-BOM
    (the encoding ``schtasks`` expects) and deleted after the call. The XML
    contains **no** password; when a password is supplied it is passed via
    ``schtasks /RP`` on the command line and redacted from all logs.

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
                   is logged on or not** (``<LogonType>Password</LogonType>``
                   in the XML + ``schtasks /RU /RP``). When omitted, the task
                   runs only while the user is logged on
                   (``<LogonType>InteractiveToken</LogonType>``) and no
                   ``/RU /RP`` flags are emitted — preserving backward
                   compatibility for existing callers and dev-mode scheduling.
                   The password is never logged or written to the XML.
        run_highest: When True and a password is supplied, run with highest
                   privileges (``<RunLevel>HighestAvailable</RunLevel>``).
                   Ignored without a password.

    Returns:
        (success, message)
    """
    # Validate all user-supplied values before touching the OS
    task_name = validate_task_name(task_name)
    sis_type = validate_sis_type(sis_type)
    validate_run_time(run_time)

    task_xml = _build_task_xml(
        exe_path,
        sis_type,
        input_dir,
        output_dir,
        sftp,
        run_time,
        run_as_user=run_as_user,
        run_as_password=run_as_password,
        run_highest=run_highest,
    )

    # schtasks /Create /XML is encoding-sensitive: the documented export format
    # is UTF-16 with a BOM, and the declaration above states encoding="UTF-16".
    # Write to a temp file (delete=False so it is closed before schtasks reads
    # it on Windows) and always remove it in the finally block.
    xml_fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="districtsync_task_")
    try:
        with os.fdopen(xml_fd, "w", encoding="utf-16") as fh:
            fh.write(task_xml)

        schtasks_args = [
            "schtasks",
            "/Create",
            "/F",
            "/TN",
            task_name,
            "/XML",
            xml_path,
        ]
        # Run-as: only when a password is supplied. /RU + /RP + /XML is a valid,
        # documented combination — /SC, /ST and /RL all come from the XML now.
        # Without a password we leave the command unchanged so existing callers
        # and dev-mode scheduling behave exactly as before (InteractiveToken).
        if run_as_password:
            resolved_user = validate_run_as_user(run_as_user or current_run_as_user())
            schtasks_args += ["/RU", resolved_user, "/RP", run_as_password]

        logger.info(f"Registering Windows scheduled task: {task_name} at {run_time}")
        # Command is logged redacted so the /RP password never reaches the log.
        # The temp-file path in the command is safe to log.
        logger.debug(f"schtasks command: {_redact_cmd(schtasks_args)}")
        # Inputs validated by src/utils/validators.py before reaching here;
        # passed as an argument list (no shell=True) so the password cannot be
        # re-parsed.
        result = subprocess.run(  # nosec B603
            schtasks_args,
            capture_output=True,
            text=True,
        )
    finally:
        # Always remove the temp XML, even if writing or schtasks failed.
        try:
            os.remove(xml_path)
        except OSError:
            logger.warning(f"Could not remove temp task XML: {xml_path}")

    success = result.returncode == 0
    message = (result.stdout + result.stderr).strip()
    if success:
        logger.info(f"Task '{task_name}' registered successfully")
    else:
        # Surface the schtasks stderr (e.g. wrong password) to the caller. The
        # echoed command is redacted; the password is not in it.
        logger.error(f"Failed to register task '{task_name}' (command: {_redact_cmd(schtasks_args)}): {message}")
    return success, message


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
