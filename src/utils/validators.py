"""Centralized input validation for security-sensitive operations.

All user-supplied values that flow into subprocess calls, crontab entries,
SFTP connections, or config file paths must be validated here before use.
"""

from __future__ import annotations

import re
import shlex

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

ALLOWED_SFTP_HOSTS: frozenset[str] = frozenset(
    {
        "sftp.ca.spacesedu.com",
        "sftp.app.spacesedu.com",
        "sftp.myblueprint.ca",
    }
)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_SIS_TYPE_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_TASK_NAME_RE = re.compile(r"^[a-zA-Z0-9_ -]+$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# Validators — each returns the sanitised value or raises ValueError
# ---------------------------------------------------------------------------


def validate_sis_type(value: str) -> str:
    """Ensure *value* is alphanumeric/underscore only (e.g. ``myedbc``)."""
    value = value.strip()
    if not _SIS_TYPE_RE.match(value):
        raise ValueError(f"Invalid SIS type '{value}'. Must contain only letters, digits, and underscores.")
    return value


def validate_task_name(value: str) -> str:
    """Ensure *value* is safe for use as a Windows Task Scheduler name."""
    value = value.strip()
    if not _TASK_NAME_RE.match(value):
        raise ValueError(
            f"Invalid task name '{value}'. Must contain only letters, digits, spaces, underscores, and hyphens."
        )
    return value


def validate_run_time(value: str) -> tuple[str, str]:
    """Validate ``HH:MM`` format and return ``(hour, minute)`` strings.

    Raises ValueError for malformed or out-of-range values.
    """
    value = value.strip()
    if not _TIME_RE.match(value):
        raise ValueError(f"Invalid run time '{value}'. Expected HH:MM (24-hour) format.")
    hour, minute = value.split(":")
    if not (0 <= int(hour) <= 23):
        raise ValueError(f"Hour must be 00–23, got '{hour}'.")
    if not (0 <= int(minute) <= 59):
        raise ValueError(f"Minute must be 00–59, got '{minute}'.")
    return hour, minute


def validate_sftp_host(host: str) -> str:
    """Ensure *host* is in the SpacesEDU SFTP allowlist.

    Raises ValueError if the host is not one of the known SpacesEDU servers.
    """
    host = host.strip().lower()
    if host not in ALLOWED_SFTP_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_SFTP_HOSTS))
        raise ValueError(f"SFTP host '{host}' is not allowed. Permitted hosts: {allowed}")
    return host


def quote_for_shell(value: str) -> str:
    """Shell-quote a value for safe inclusion in crontab or similar."""
    return shlex.quote(str(value))
