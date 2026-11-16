"""When a staged update is handed to the installer, and when it is not (plan 0054 S5, DSYNC-64).

The shared package does the hand-off (``myb_desktop_updater.hand_off``): it refuses unless the staged files still match
what was verified, no other process of the app runs, the running version is known healthy and its package could be kept
for rollback first, and then asks Velopack to apply the update AFTER this process exits (never restarting the app). This
module adds only what is DistrictSync's:

**Apply happens after exit, never during.** ``apply_after_exit`` waits for this process to end; nothing here restarts it.

**The single-writer rule** is the package's refusal while another DistrictSync process runs (counted by image name,
excluding this one) or cannot be counted. If the window is open while the nightly finishes, or the reverse, the update
simply stays staged for the next opportunity. A file lock in the profile was considered and not built: the package's check
plus Velopack's own failure to swap under a running process (S0 finding 8, "fails safe") cover it with less to go wrong.

**Keep clear of the nightly.** The window does not request an apply within ``NIGHTLY_KEEP_CLEAR_MINUTES`` before the
registered run time: a nightly that starts during the folder swap may fail to start (plan 0054 section 4). The scheduled
trigger applies right after the nightly finishes, the moment furthest from the next one, so it has no such window.

Every refusal is a returned ``CheckOutcome`` (the update stays staged, the running version untouched); nothing raises.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from src.config.app_config import AppConfig

logger = logging.getLogger(__name__)

NIGHTLY_KEEP_CLEAR_MINUTES: Final = 30
_INSTALLER_MODULE: Final = (
    "myb_desktop_updater.velopack_installer"  # a variable: the portable build must not collect it
)


def minutes_to_next_run(config: AppConfig, now: datetime) -> float | None:
    """Minutes until the registered nightly run, or ``None`` when no run is registered or the time is unreadable."""
    if not config.schedule_registered:
        return None
    try:
        hour, minute = (int(part) for part in config.schedule_time.split(":"))
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return (due - now).total_seconds() / 60


def window_may_request_apply(config: AppConfig, now: datetime) -> bool:
    """False inside the keep-clear window before the registered run (the window's trigger only)."""
    ahead = minutes_to_next_run(config, now)
    return ahead is None or ahead > NIGHTLY_KEEP_CLEAR_MINUTES


def default_installer(exe_path: Path | None = None) -> Any:
    """The package's Velopack installer for the running exe. Imported by name so the portable build never collects it."""
    module = importlib.import_module(_INSTALLER_MODULE)
    return module.VelopackInstaller(exe_path)


def other_instances_counter(updater: ModuleType) -> Callable[[], int | None]:
    """How many OTHER DistrictSync processes run; ``None`` when that cannot be told (the package then refuses)."""
    names = [Path(sys.executable).name]
    pid = os.getpid()
    return lambda: updater.running_instances(names, pid)  # type: ignore[no-any-return]
