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
# Windows run-as account: DOMAIN\user or bare user. Letters, digits, dot,
# underscore, hyphen, and at most ONE backslash domain separator. No
# whitespace or special characters — this value is interpolated into a
# PowerShell ``-User`` / principal ``-UserId`` parameter (passed to
# ``Register-ScheduledTask`` via the child env), so it must stay a clean
# account identifier with no PowerShell-meaningful characters.
_RUN_AS_USER_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._-]+)?$")

# Maximum length for a run-as account string (DOMAIN\user).
_RUN_AS_USER_MAX_LEN = 256


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


def validate_run_as_user(user: str) -> str:
    """Validate a Windows run-as account for a PowerShell scheduled-task principal.

    The value flows to ``Register-ScheduledTask``'s ``-User`` and the principal's
    ``-UserId`` (via the spawned PowerShell process's environment, not a shell
    argument list). Accepts a bare username (``jane``) or a ``DOMAIN\\user`` pair
    (``CORP\\jane``). Permits letters, digits, ``.``, ``_``, ``-`` and at most
    one backslash as the domain separator. Rejects empty values, internal
    whitespace, and any special character so the value stays a clean account
    identifier with no PowerShell-meaningful characters.

    Returns the stripped value on success; raises ``ValueError`` otherwise.
    """
    user = user.strip()
    if not user:
        raise ValueError("Run-as user must not be empty.")
    if len(user) > _RUN_AS_USER_MAX_LEN:
        raise ValueError(f"Run-as user is too long (max {_RUN_AS_USER_MAX_LEN} characters).")
    if not _RUN_AS_USER_RE.match(user):
        raise ValueError(
            f"Invalid run-as user '{user}'. Use 'DOMAIN\\user' or 'user' with only "
            "letters, digits, dots, underscores, and hyphens (no spaces or special characters)."
        )
    return user


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
