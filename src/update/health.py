"""What "healthy" means for DistrictSync (plan 0054 S5, DSYNC-64; counting moved to the start guard, DSYNC-66).

The shared package keeps the record (``HealthStore``) and the retained copy of the last good package. COUNTING crashed starts and
rolling back is the start guard's job alone (``src.update.entry``, the package's ``myb_start_guard``): it counts before any heavy
import, which a count made here could not. DistrictSync keeps no second counter.

**What healthy means.** The start-up COMPLETED. For a conversion that is the moment before ETL begins (profile pinned,
logging up, configs loadable); for the window it is the first screen up. It is NEVER "the conversion succeeded": a nightly
that exits 1 because a GDE file is missing is a data problem, not a bad build, and must not roll a good version back.

Marking healthy does two things: it clears the guard's count, and it records the version healthy in the health store (so a version
once recorded bad is cleared by a clean start, and the update check can tell a proven version from a new one).

Everything here is total: nothing raises into the caller, and the portable build, a copy not under a Velopack install,
machine scope and ``DISTRICTSYNC_DATA_DIR`` do nothing at all (the same guards as the check, minus the update switches).
"""

from __future__ import annotations

import logging

from src.update.check import _in_machine_scope, updater_state_dir
from src.update.entry import confirm_start
from src.update.package import HEALTH_FILE, load_updater
from src.update.startup import install_root
from src.utils import paths
from src.utils.build_flavor import is_e2e_build, is_installed_build
from src.utils.install_kind import InstallKind, install_kind
from src.utils.version import app_version

logger = logging.getLogger(__name__)


def health_skip_reason() -> str | None:
    """Why no health is recorded, or ``None``. Opens nothing."""
    if not is_installed_build():
        return "portable"
    if install_root() is None:
        return "not_installed"
    if install_kind() is InstallKind.MACHINE:
        return "machine_wide"  # plan 0055: health on a machine-wide install is the SYSTEM updater's (U3)
    if _in_machine_scope():
        return "machine_scope"
    if paths.data_dir_override_active() and not is_e2e_build():
        return "data_dir_override"
    return None


def mark_healthy() -> bool:
    """Mark the current start healthy (start-up completed). ``True`` if it was recorded. Never raises."""
    confirm_start()  # the start guard's count is cleared whether or not the health store is in use (no-op without it)
    try:
        if health_skip_reason() is not None:
            return False
        updater = load_updater()
        if updater is None:
            return False
        version = app_version()
        if updater.parse_version(version) is None:
            return False
        directory = updater_state_dir()
        updater.HealthStore(directory / HEALTH_FILE).mark_healthy(version)
        updater.clean_work_folders(directory)
    except Exception:  # noqa: BLE001 - recording health must never stop the app
        logger.warning("could not record a healthy start", exc_info=True)
        return False
    return True
