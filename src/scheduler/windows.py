"""Windows Task Scheduler integration via ``schtasks.exe``.

Creates a daily scheduled task that runs the GDE2Acsv CLI at a
specified time.  No third-party dependencies — uses subprocess only.

Usage::

    from src.scheduler.windows import register_task, query_task, delete_task

    ok, msg = register_task(
        task_name="GDE2Acsv_Daily",
        exe_path=Path("C:/GDE2Acsv/GDE2Acsv.exe"),
        sis_type="myedbc",
        input_dir=Path("C:/GDE2Data/input"),
        output_dir=Path("C:/GDE2Data/output"),
        run_time="03:00",
        sftp=True,
    )
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.utils.validators import validate_run_time, validate_sis_type, validate_task_name

logger = logging.getLogger(__name__)


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
        task_name: Name displayed in Task Scheduler (e.g. "GDE2Acsv_Daily").
        exe_path:  Absolute path to GDE2Acsv.exe.
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

    cmd_parts = [
        f'"{exe_path}"',
        f"--sis {sis_type}",
        f'--input "{input_dir}"',
        f'--output "{output_dir}"',
    ]
    if sftp:
        cmd_parts.append("--sftp")
    task_run = " ".join(cmd_parts)

    schtasks_args = [
        "schtasks", "/Create", "/F",
        "/TN", task_name,
        "/TR", task_run,
        "/SC", "DAILY",
        "/ST", run_time,
    ]

    logger.info(f"Registering Windows scheduled task: {task_name} at {run_time}")
    result = subprocess.run(
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
    result = subprocess.run(
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
    result = subprocess.run(
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
