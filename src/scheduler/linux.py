"""Linux/macOS cron integration for GDE2Acsv.

Appends or removes a crontab entry using the system ``crontab`` command.
No third-party dependencies.

The entry is tagged with a sentinel comment so it can be identified and
removed later without affecting the rest of the user's crontab.

Usage::

    from src.scheduler.linux import register_cron, delete_cron

    ok, msg = register_cron(
        exe_path=Path("/opt/gde2acsv/GDE2Acsv"),
        sis_type="myedbc",
        input_dir=Path("/data/gde/input"),
        output_dir=Path("/data/gde/output"),
        run_time="03:00",
        sftp=True,
    )
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CRON_SENTINEL = "# GDE2Acsv managed entry"


def _run(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    result = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def register_cron(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    run_time: str,
    sftp: bool = False,
) -> tuple[bool, str]:
    """Add or replace the GDE2Acsv cron entry.

    Args:
        exe_path:  Absolute path to the GDE2Acsv binary.
        sis_type:  SIS config identifier (e.g. "myedbc").
        input_dir: Source GDE file directory.
        output_dir: CSV output directory.
        run_time:  Daily run time in "HH:MM" 24-hour format.
        sftp:      Append ``--sftp`` flag if True.

    Returns:
        (success, message)
    """
    hour, minute = run_time.split(":")

    cmd_parts = [
        str(exe_path),
        f"--sis {sis_type}",
        f'--input "{input_dir}"',
        f'--output "{output_dir}"',
    ]
    if sftp:
        cmd_parts.append("--sftp")
    cmd = " ".join(cmd_parts)

    cron_line = f"{minute} {hour} * * * {cmd} {CRON_SENTINEL}"

    # Read existing crontab (ignore error if none exists yet)
    _, existing = _run(["crontab", "-l"])
    lines = existing.splitlines() if existing and "no crontab" not in existing.lower() else []

    # Remove any previous GDE2Acsv entry
    lines = [ln for ln in lines if CRON_SENTINEL not in ln]
    lines.append(cron_line)
    new_crontab = "\n".join(lines) + "\n"

    code, msg = _run(["crontab", "-"], stdin=new_crontab)
    success = code == 0
    if success:
        logger.info(f"Cron entry registered: {cron_line}")
    else:
        logger.error(f"Failed to register cron entry: {msg}")
    return success, msg or "Cron entry registered."


def delete_cron() -> tuple[bool, str]:
    """Remove the GDE2Acsv managed cron entry.

    Returns:
        (success, message)
    """
    _, existing = _run(["crontab", "-l"])
    if not existing or "no crontab" in existing.lower():
        return True, "No crontab to remove."

    lines = [ln for ln in existing.splitlines() if CRON_SENTINEL not in ln]
    new_crontab = "\n".join(lines) + "\n"
    code, msg = _run(["crontab", "-"], stdin=new_crontab)
    return code == 0, msg or "Cron entry removed."


def cron_entry_exists() -> bool:
    """Return True if a GDE2Acsv cron entry is present."""
    _, existing = _run(["crontab", "-l"])
    return bool(existing and CRON_SENTINEL in existing)
