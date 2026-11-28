"""The facts ``--diagnose`` prints about how DistrictSync is installed (plan 0055 D2, DSYNC-102).

Read-only and total: every reader returns a value or ``None`` and never raises, because a support report must never
stop on a line. Nothing here writes, elevates or connects.

* the install folder (the Velopack root) and the running version;
* the MSI product's own version. It stays at the version the MSI installed while Velopack moves the files forward, so
  the two differ after an update (plan section 3); "the newest MSI only" is the rule an administrator needs this for;
* a copy installed for one Windows account (``%LOCALAPPDATA%\\myBlueprint.DistrictSync``) beside a machine-wide one;
* the SYSTEM updater's last line (``updater.log``) and the nightly's last notice.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

#: The names the Velopack MSI may register under ``Uninstall``: the ``--packTitle``, or ``MSI:<pack id>`` as the spike's
#: entry read (S0b #2). Which one the release MSI uses is confirmed by the lab run (L1, review B2).
PRODUCT_NAME: Final = "DistrictSync"
MSI_ENTRY_NAMES: Final = (PRODUCT_NAME, "MSI:myBlueprint.DistrictSync")
_UNINSTALL_KEY: Final = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_MAX_LOG_TAIL_BYTES: Final = 4096


def _uninstall_entries() -> list[dict[str, object]]:  # pragma: no cover - Windows-only registry read
    """``DisplayName``/``DisplayVersion``/``WindowsInstaller`` for every HKLM uninstall entry, both registry views."""
    if sys.platform != "win32":
        return []
    import winreg

    entries: list[dict[str, object]] = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEY, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, name) as key:
                        entry: dict[str, object] = {}
                        for value in ("DisplayName", "DisplayVersion", "WindowsInstaller"):
                            try:
                                entry[value] = winreg.QueryValueEx(key, value)[0]
                            except OSError:
                                continue
                        entries.append(entry)
                except OSError:
                    continue
    return entries


def msi_product_version() -> str | None:
    """The version of the installed DistrictSync MSI product, or ``None`` (no MSI, or unreadable)."""
    try:
        for entry in _uninstall_entries():
            if entry.get("DisplayName") in MSI_ENTRY_NAMES and entry.get("WindowsInstaller") == 1:
                version = entry.get("DisplayVersion")
                return str(version) if version else None
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return None
    return None


def per_user_update_exe() -> Path | None:
    """The ``Update.exe`` of a copy installed for THIS Windows account (the per-user ``Setup.exe`` install), or ``None``.

    Its own uninstaller (``Update.exe uninstall --silent``) is what ``--migrate --apply`` runs, and only as this account
    (DSYNC-116). Never raises.
    """
    try:
        from src.update.package import PACK_ID

        local = os.environ.get("LOCALAPPDATA", "").strip()
        update_exe = Path(local) / PACK_ID / "Update.exe"
        return update_exe if local and update_exe.is_file() else None
    except Exception:  # noqa: BLE001 - a report line, never a stop
        return None


def per_user_copy_present() -> bool:
    """Whether a copy installed for this Windows account (the per-user ``Setup.exe`` install) is also here."""
    return per_user_update_exe() is not None


def updater_last_line(log_file: Path) -> str | None:
    """The last line the SYSTEM updater wrote, or ``None``. Reads at most the file's last 4 KB."""
    try:
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _MAX_LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    return lines[-1] if lines else None
