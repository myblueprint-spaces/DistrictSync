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


def _when(iso: Any) -> str:
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


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
        # Notify mode (plan 0055 U1): a newer signed version exists and nothing was downloaded.
        return f"Version {last.get('version')} is available; this copy does not update itself." + suffix
    known = _LAST.get((str(last.get("status")), str(last.get("reason"))))
    if known:
        return known + suffix
    return "The last update check could not finish." + suffix
