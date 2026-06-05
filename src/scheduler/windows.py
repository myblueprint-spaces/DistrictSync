"""Windows Task Scheduler integration via ``schtasks.exe``.

Creates a daily scheduled task that runs the DistrictSync CLI at a
specified time.  No third-party dependencies — uses subprocess only.

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
from pathlib import Path

from src.utils.validators import (
    validate_run_as_user,
    validate_run_time,
    validate_sis_type,
    validate_task_name,
)

logger = logging.getLogger(__name__)


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


def _build_task_command(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    sftp: bool,
    *,
    run_as_user: str | None = None,
    run_as_password: str | None = None,
    run_highest: bool = True,
) -> str:
    """Construct the /TR command that Task Scheduler will execute.

    Two modes:
      - Frozen PyInstaller binary (e.g. DistrictSync.exe): invoke the exe
        directly — ``"<exe>" --sis X --input Y --output Z [--sftp]``.
      - Python interpreter (dev / source install): wrap in cmd.exe so we
        can cd into the project root and invoke ``python -m src.main``.
        Without the chdir, Python can't find the src package; without
        the -m flag, Python treats --sis as a script path and errors
        out with 0x80070002 (ERROR_FILE_NOT_FOUND).

    The ``run_as_user`` / ``run_as_password`` / ``run_highest`` parameters do
    not affect the ``/TR`` command itself — they are consumed by
    :func:`register_task` to build the ``/RU /RP /RL`` flags — but are accepted
    here so the two functions share one call contract.
    """
    del run_as_user, run_as_password, run_highest  # consumed by register_task
    is_python = exe_path.name.lower().startswith("python")

    if is_python:
        # Project root = two levels up from src/scheduler/windows.py
        project_root = Path(__file__).resolve().parents[2]
        inner = (
            f'cd /d "{project_root}" && '
            f'"{exe_path}" -m src.main '
            f"--sis {sis_type} "
            f'--input "{input_dir}" '
            f'--output "{output_dir}"'
        )
        if sftp:
            inner += " --sftp"
        # schtasks /TR needs the entire value as one shell command.
        # Use cmd /c so the && chain runs in a single process.
        return f'cmd /c "{inner}"'

    parts = [
        f'"{exe_path}"',
        f"--sis {sis_type}",
        f'--input "{input_dir}"',
        f'--output "{output_dir}"',
    ]
    if sftp:
        parts.append("--sftp")
    return " ".join(parts)


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
    """Create or replace a Windows scheduled task.

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
                   is logged on or not** (``schtasks /RU /RP``). When omitted,
                   the task is created with default scope (runs only while the
                   user is logged on) and no ``/RU /RP /RL`` flags are emitted —
                   this preserves backward compatibility for existing callers
                   and dev-mode scheduling. The password is never logged.
        run_highest: When True and a password is supplied, run with highest
                   privileges (``/RL HIGHEST``). Ignored without a password.

    Returns:
        (success, message)
    """
    # Validate all user-supplied values before touching the OS
    task_name = validate_task_name(task_name)
    sis_type = validate_sis_type(sis_type)
    validate_run_time(run_time)

    task_run = _build_task_command(
        exe_path,
        sis_type,
        input_dir,
        output_dir,
        sftp,
        run_as_user=run_as_user,
        run_as_password=run_as_password,
        run_highest=run_highest,
    )

    schtasks_args = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        task_name,
        "/TR",
        task_run,
        "/SC",
        "DAILY",
        "/ST",
        run_time,
    ]

    # Run-as: only when a password is supplied. Supplying /RP (and omitting
    # /IT) is what makes Task Scheduler run the task whether or not the user
    # is logged on. Without a password we leave the command unchanged so
    # existing callers and dev-mode scheduling behave exactly as before.
    if run_as_password:
        resolved_user = validate_run_as_user(run_as_user or current_run_as_user())
        schtasks_args += ["/RU", resolved_user, "/RP", run_as_password]
        if run_highest:
            schtasks_args += ["/RL", "HIGHEST"]

    logger.info(f"Registering Windows scheduled task: {task_name} at {run_time}")
    # Command is logged redacted so the /RP password never reaches the log.
    logger.debug(f"schtasks command: {_redact_cmd(schtasks_args)}")
    # Inputs validated by src/utils/validators.py before reaching here; passed
    # as an argument list (no shell=True) so the password cannot be re-parsed.
    result = subprocess.run(  # nosec B603
        schtasks_args,
        capture_output=True,
        text=True,
    )
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
