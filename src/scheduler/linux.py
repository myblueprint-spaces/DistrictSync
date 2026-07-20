"""Linux/macOS cron integration for DistrictSync.

Appends or removes a crontab entry using the system ``crontab`` command.
No third-party dependencies.

The entry is tagged with a sentinel comment so it can be identified and
removed later without affecting the rest of the user's crontab.

Usage::

    from src.scheduler.linux import register_cron, delete_cron

    ok, msg = register_cron(
        exe_path=Path("/opt/districtsync/DistrictSync"),
        sis_type="myedbc",
        input_dir=Path("/data/gde/input"),
        output_dir=Path("/data/gde/output"),
        run_time="03:00",
        sftp=True,
    )
"""

from __future__ import annotations

import logging
import os

# subprocess is required to invoke the system `crontab` command.
import subprocess  # nosec B404
from pathlib import Path

from src.utils.validators import quote_for_shell, validate_run_time, validate_sis_type

logger = logging.getLogger(__name__)

CRON_SENTINEL = "# DistrictSync managed entry"


def _run(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    # Inputs are validated by src/utils/validators.py before reaching here.
    # LC_ALL=C pins crontab's messages to English so the "no crontab" empty-vs-error
    # classification in _read_crontab_lines is locale-independent (fresh env copy —
    # os.environ is never mutated).
    env = {**os.environ, "LC_ALL": "C"}
    result = subprocess.run(  # nosec B603
        args,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _read_crontab_lines() -> tuple[list[str] | None, str]:
    """Read the current user's crontab lines — fail LOUD on an unreadable crontab.

    Returns ``(lines, "")`` when the read is trustworthy: exit 0 (the parsed lines) or
    the benign ``no crontab for <user>`` (a genuinely empty crontab → ``[]``). ANY other
    failure (permission denied, a broken crontab wrapper, …) returns ``(None, message)``
    and the caller MUST abort without rewriting: treating an unreadable crontab as empty
    would replace the user's other cron jobs with only the DistrictSync line
    (``crontab -`` installs a WHOLE new crontab, not an append).
    """
    code, output = _run(["crontab", "-l"])
    if code == 0:
        return (output.splitlines() if output else []), ""
    if "no crontab" in output.lower():
        return [], ""
    msg = f"Couldn't read the existing crontab (crontab -l exited {code}): {output or 'unknown error'}"
    logger.error(msg)
    return None, msg


def register_cron(
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    run_time: str,
    sftp: bool = False,
) -> tuple[bool, str]:
    """Add or replace the DistrictSync cron entry.

    Args:
        exe_path:  Absolute path to the DistrictSync binary.
        sis_type:  SIS config identifier (e.g. "myedbc").
        input_dir: Source GDE file directory.
        output_dir: CSV output directory.
        run_time:  Daily run time in "HH:MM" 24-hour format.
        sftp:      Append ``--sftp`` flag if True.

    Returns:
        (success, message)
    """
    # Validate all user-supplied values before touching crontab
    sis_type = validate_sis_type(sis_type)
    hour, minute = validate_run_time(run_time)

    # Detect python-interpreter mode (source install) vs frozen binary.
    # In source mode we must invoke "python -m src.main" from the
    # project root; otherwise Python treats --sis as a script path and
    # errors out.
    is_python = exe_path.name.lower().startswith("python")
    if is_python:
        project_root = Path(__file__).resolve().parents[2]
        cmd_parts = [
            "cd",
            quote_for_shell(str(project_root)),
            "&&",
            quote_for_shell(str(exe_path)),
            "-m",
            "src.main",
        ]
    else:
        cmd_parts = [quote_for_shell(str(exe_path))]

    cmd_parts += [
        "--sis",
        quote_for_shell(sis_type),
        "--input",
        quote_for_shell(str(input_dir)),
        "--output",
        quote_for_shell(str(output_dir)),
    ]
    if sftp:
        cmd_parts.append("--sftp")
    cmd = " ".join(cmd_parts)

    cron_line = f"{minute} {hour} * * * {cmd} {CRON_SENTINEL}"

    # Read the existing crontab first; a failed read (other than the benign "no crontab")
    # aborts loudly — rewriting from a blind read would wipe the user's other cron jobs.
    lines, read_error = _read_crontab_lines()
    if lines is None:
        return False, read_error

    # Remove any previous DistrictSync entry
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
    """Remove the DistrictSync managed cron entry.

    Returns:
        (success, message)
    """
    # Same fail-loud read as register_cron: an unreadable crontab must never be
    # rewritten as if it were empty (that would destroy the user's other entries).
    existing_lines, read_error = _read_crontab_lines()
    if existing_lines is None:
        return False, read_error
    if not existing_lines:
        return True, "No crontab to remove."

    lines = [ln for ln in existing_lines if CRON_SENTINEL not in ln]
    new_crontab = "\n".join(lines) + "\n"
    code, msg = _run(["crontab", "-"], stdin=new_crontab)
    return code == 0, msg or "Cron entry removed."


def cron_entry_exists() -> bool:
    """Return True if a DistrictSync cron entry is present."""
    _, existing = _run(["crontab", "-l"])
    return bool(existing and CRON_SENTINEL in existing)
