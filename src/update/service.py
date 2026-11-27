"""``--update-service``: the SYSTEM updater's entry (plan 0055 U2; this is U2a, DSYNC-98).

Dispatched in ``main``'s module-top block, before the start guard and the heavy imports. It never calls
``AppConfig.load()``, ``user_data_dir()``, ``pin_data_dir()`` or the keyring, and opens no file under
``C:\\ProgramData\\DistrictSync`` (the setup user and the principal can write there): every input it reads is writable by
Administrators/SYSTEM only (the SYSTEM trust rule, plan section 3).

**U2a builds the refusals and the trusted state folder only.** The download, the Authenticode check and the hand-off to
Velopack (plan steps 3-6) and the supervised health probe wait on the update feed (plan 0054 S6b/S8) and on the shared
package's machine-wide support (U3, a change in ``myb-desktop-shared``). Until then every run ends in a refusal code,
written as one line to ``updater.log``: the task can run, and it can never install anything.

Exit code 0 always: a refusal is a normal night for this task, and Task Scheduler must not report it as a failure.
"""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

#: The folder name under ``%ProgramData%``: outside the install root (an MSI upgrade or uninstall deletes that) and
#: outside ``C:\\ProgramData\\DistrictSync`` (writable by non-admins).
STATE_FOLDER_NAME: Final = "DistrictSync-Updater"
LOG_FILE: Final = "updater.log"

# Refusal codes, in the order they are decided. A code, never free text, never a path or an account.
NOT_WINDOWS: Final = "not_windows"
NOT_MACHINE_WIDE: Final = "not_machine_wide"
MODE_NOT_ON: Final = "mode_not_on"
NOT_CONFIGURED: Final = "not_configured"
STATE_UNTRUSTED: Final = "state_untrusted"
APPLY_NOT_BUILT: Final = "apply_not_built"  # U2's apply waits on the feed and on U3
REFUSALS: Final = (NOT_WINDOWS, NOT_MACHINE_WIDE, MODE_NOT_ON, NOT_CONFIGURED, STATE_UNTRUSTED, APPLY_NOT_BUILT)


def state_dir() -> Path:
    """``%ProgramData%\\DistrictSync-Updater``. Resolves only; the provisioning op creates it (never-adopt)."""
    from src.utils import paths

    return paths.machine_data_dir().parent / STATE_FOLDER_NAME


def state_dir_trusted(path: Path) -> bool:
    """Whether the state folder is one only SYSTEM/Administrators can change: present, not a reparse point, owner
    trusted, and no write-class ace for anyone else. Anything unreadable is untrusted."""
    from src.utils.install_kind import writable_by_non_admin

    try:
        if not path.is_dir() or path.is_symlink() or _is_reparse_point(path):
            return False
        # Inherit-only aces count too: they decide who can change the files the updater creates here later.
        return not writable_by_non_admin(path, count_inherit_only=True)
    except OSError:
        return False


def _file_trusted(path: Path) -> bool:
    """A file in the trusted folder may be appended to only if it is not a link and only admins can change it."""
    from src.utils.install_kind import writable_by_non_admin

    try:
        if not path.exists():
            return True  # created by this (SYSTEM) process, inheriting the folder's protected descriptor
        if path.is_symlink() or _is_reparse_point(path) or not path.is_file():
            return False
        return not writable_by_non_admin(path)
    except OSError:
        return False


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def refusal() -> str:
    """The first reason this run installs nothing. Reads only admin-writable inputs; never raises."""
    try:
        if sys.platform != "win32":
            return NOT_WINDOWS
        from src.utils.install_kind import InstallKind, install_kind

        if install_kind() is not InstallKind.MACHINE:
            return NOT_MACHINE_WIDE
        from src.update import policy

        # HKLM only: ``setting_enabled=True`` means ``config.json`` (writable by the setup user) is never an input.
        if policy.effective_update_mode(setting_enabled=True).mode != policy.ON:
            return MODE_NOT_ON
        from src.update.feed import production_target

        if production_target() is None:
            return NOT_CONFIGURED
        if not state_dir_trusted(state_dir()):
            return STATE_UNTRUSTED
        return APPLY_NOT_BUILT
    except Exception:  # noqa: BLE001 - the SYSTEM task never crashes; doubt installs nothing
        return STATE_UNTRUSTED


def _log(code: str) -> None:
    """One plain line per run, only into a TRUSTED state folder: time, version, result code. No account, no path."""
    try:
        folder = state_dir()
        if not state_dir_trusted(folder):
            return
        from src.utils.version import app_version

        line = (
            f"{datetime.now(UTC).isoformat(timespec='seconds')} version={app_version()} result=refused reason={code}\n"
        )
        target = folder / LOG_FILE
        if not _file_trusted(target):
            return
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:  # noqa: BLE001 - the SYSTEM task never crashes on its log
        return


def run_update_service() -> int:
    """The task's whole run today: decide the refusal, log it, exit 0 (always: a refusal is a normal night)."""
    with contextlib.suppress(Exception):  # Task Scheduler must never see this task fail
        _log(refusal())
    return 0


#: How long the SYSTEM updater waits for a new version's probe (plan 0055 U3).
PROBE_TIMEOUT_SECONDS: Final = 120.0
HEALTH_FILE: Final = "health.json"
PROBE_FLAG: Final = "--update-health-probe"


def supervised_check(version: str, previous: str, exe: Path, *, module: object | None = None) -> str:
    """Decide a newly applied version's health as SYSTEM (plan 0055 U3): probe ``exe --update-health-probe`` under
    supervision; ``healthy`` / ``retry`` / ``recover`` / ``nothing`` (the package's ``SupervisedAction``). The state is
    ``health.json`` in the TRUSTED updater folder only; an untrusted folder answers ``state_untrusted`` and nothing is
    recorded. Called by U2's apply flow (blocked on the feed); the restore on ``recover`` is the package's
    ``recover_broken_start``. Never raises."""
    try:
        folder = state_dir()
        if not state_dir_trusted(folder):
            return STATE_UNTRUSTED
        package: Any = module
        if package is None:
            import myb_desktop_updater as package
        store = package.HealthStore(folder / HEALTH_FILE)
        action = package.supervised_health(
            store,
            version,
            previous,
            lambda: package.run_health_probe([str(exe), PROBE_FLAG], timeout_seconds=PROBE_TIMEOUT_SECONDS),
        )
        return str(action.value)
    except Exception:  # noqa: BLE001 - the SYSTEM task never crashes; doubt changes nothing
        return "error"
