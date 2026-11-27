"""The one plain-language line Help shows about updates (plan 0054 S4, DSYNC-63).

Pure over ``skip_reason`` and the recorded last check, so it is unit-tested without a window. ``None``
means "show nothing": the portable build has no updater and must not mention one.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.update.check import last_check_summary, skip_reason
from src.utils.build_flavor import is_installed_build
from src.utils.version import app_version

# Skip reasons that describe a standing condition worth telling the admin about. The others (``recent``,
# ``unknown_version``) are transient and fall through to the last recorded check.
_STANDING: dict[str, str] = {
    "not_installed": "This copy was not installed with the DistrictSync installer, so it does not update itself.",
    "machine_scope": "This computer shares one DistrictSync profile, so this copy does not update itself.",
    "machine_wide": "This copy is installed for the whole computer; it does not update itself yet.",
    "data_dir_override": "Updates are off while a test profile is in use.",
    "off_in_settings": "Updates are switched off on this computer.",
    "off_by_policy": "Updates are switched off by your organisation's policy.",
    "off_switch": "Updates are switched off on this computer.",
    "package_missing": "Updates are unavailable in this copy.",
    "not_configured": "Updates are not available yet.",
}

_LAST: dict[tuple[str, str], str] = {
    ("none", "up_to_date"): "You have the latest version.",
    ("staged", "staged"): "A newer version has been downloaded and will be used after it is installed.",
}


def _version_key(text: str) -> tuple[int, int, int] | None:
    """``major.minor.patch`` as numbers, padded to three; a prerelease or build suffix is ignored. ``None`` if unreadable."""
    import re

    match = re.match(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if match is None:
        return None
    major, minor, patch = (int(part) if part else 0 for part in match.groups())
    return (major, minor, patch)


def _newer(candidate: str, running: str) -> bool:
    """Whether ``candidate`` is newer than ``running``. An unreadable running version (a ``dev`` build) cannot be compared,
    so the line is shown; an unreadable candidate never is."""
    a, b = _version_key(candidate), _version_key(running)
    if a is None:
        return False
    return b is None or a > b


def _when(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def newer_version_available(state_dir: Path | None = None) -> str | None:
    """The newer signed version the last notify check found, while it is still newer than this copy; else ``None``.

    Home's line (plan 0055 U1b). Never raises: a quiet line may never take Home down.
    """
    try:
        if not is_installed_build():
            return None
        if skip_reason(state_dir=state_dir, ignore_interval=True) in _STANDING:
            return None  # updates are off or do not run here: Help says so, Home stays quiet
        last = last_check_summary(state_dir)
        if not last or last.get("status") != "available" or not last.get("version"):
            return None
        version = str(last.get("version"))
        return version if _newer(version, app_version()) else None
    except Exception:  # noqa: BLE001 - advisory copy on Home
        return None


def update_status_line(state_dir: Path | None = None) -> str | None:
    """What Help should say about updates, or ``None`` to say nothing (portable build)."""
    if not is_installed_build():
        return None
    reason = skip_reason(state_dir=state_dir, ignore_interval=True)
    if reason in _STANDING:
        return _STANDING[reason]
    last = last_check_summary(state_dir)
    if not last:
        return "Updates: not checked yet."
    day = _when(last.get("at"))
    suffix = f" Last checked {day}." if day else ""
    if last.get("status") == "available" and last.get("version"):
        # Notify mode (plan 0055 U1): a newer signed version exists and nothing was downloaded. Said only while it is still
        # newer than this copy: after an upgrade the record is stale until the next check.
        if _newer(str(last.get("version")), app_version()):
            return f"Version {last.get('version')} is available; this copy does not update itself." + suffix
        return "Updates: not checked since this version was installed."
    known = _LAST.get((str(last.get("status")), str(last.get("reason"))))
    if known:
        return known + suffix
    return "The last update check could not finish." + suffix
