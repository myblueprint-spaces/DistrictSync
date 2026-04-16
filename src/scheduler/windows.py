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

import logging

# subprocess is required to invoke schtasks.exe.
import subprocess  # nosec B404
from pathlib import Path

from src.utils.validators import validate_run_time, validate_sis_type, validate_task_name

logger = logging.getLogger(__name__)


def _build_task_command(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    sftp: bool,
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
    """
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

    Returns:
        (success, message)
    """
    # Validate all user-supplied values before touching the OS
    task_name = validate_task_name(task_name)
    sis_type = validate_sis_type(sis_type)
    validate_run_time(run_time)

    task_run = _build_task_command(exe_path, sis_type, input_dir, output_dir, sftp)

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

    logger.info(f"Registering Windows scheduled task: {task_name} at {run_time}")
    # Inputs validated by src/utils/validators.py before reaching here.
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
        logger.error(f"Failed to register task '{task_name}': {message}")
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
