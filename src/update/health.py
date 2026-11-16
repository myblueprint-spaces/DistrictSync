"""What "healthy" means for DistrictSync, and the rollback it triggers (plan 0054 S5, DSYNC-64).

The shared package keeps the record (``HealthStore``), the retained copy of the last good package (``LastGoodStore``)
and the hand-off to Velopack (``restore_last_good``). DistrictSync decides two things, here:

**When a start counts.** Only a start that is expected to reach a healthy mark: the window (no arguments) and a
conversion run (``--sis``). ``--version``, ``--diagnose``, the SFTP subcommands and the elevated helpers never reach one,
so counting them would make a quiet admin's ``--version`` look like a crash loop.

**What healthy means.** The start-up COMPLETED. For a conversion that is the moment before ETL begins (profile pinned,
logging up, configs loadable); for the window it is the first screen up. It is NEVER "the conversion succeeded": a nightly
that exits 1 because a GDE file is missing is a data problem, not a bad build, and must not roll a good version back.

A start is recorded as soon as it begins, so a build that dies in its own start-up (an import error, a broken
config) leaves a failed start behind; after more than two such starts the package records the version bad, never offers it
again, and this module asks it to restore the kept copy, applied after this process exits. That works while updates are
switched off: it is a safety step, not an update check.

Everything here is total: nothing raises into the caller, and the portable build, a copy not under a Velopack install,
machine scope and ``DISTRICTSYNC_DATA_DIR`` do nothing at all (the same guards as the check, minus the update switches).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from src.update import apply
from src.update.check import _in_machine_scope, updater_state_dir
from src.update.package import app_identity, load_updater
from src.update.startup import install_root
from src.utils import paths
from src.utils.build_flavor import is_installed_build
from src.utils.version import app_version

logger = logging.getLogger(__name__)

HEALTH_FILE = "health.json"


@dataclass(frozen=True)
class StartRecord:
    """One recorded start, kept until it is marked healthy."""

    updater: ModuleType
    store: Any
    version: str
    state_dir: Path


_CURRENT: StartRecord | None = None


def counts_as_a_start(args: list[str]) -> bool:
    """Whether this invocation is expected to reach a healthy mark: the window, or a conversion run."""
    return not args or "--sis" in args


def health_skip_reason() -> str | None:
    """Why no start is recorded, or ``None``. Opens nothing."""
    if not is_installed_build():
        return "portable"
    if install_root() is None:
        return "not_installed"
    if _in_machine_scope():
        return "machine_scope"
    if paths.data_dir_override_active():
        return "data_dir_override"
    return None


def begin_start(
    *,
    updater: ModuleType | None = None,
    state_dir: Path | None = None,
    installer: Any | None = None,
    running: Any | None = None,
) -> StartRecord | None:
    """Record that a start has begun; if the version is bad and a good copy is kept, go back to it after exit.

    Returns the record (also held here for :func:`mark_healthy`) or ``None`` when nothing is tracked. Never raises.
    """
    global _CURRENT
    try:
        if health_skip_reason() is not None:
            return None
        module = load_updater() if updater is None else updater
        if module is None:
            return None
        version = app_version()
        if module.parse_version(version) is None:
            return None
        directory = updater_state_dir() if state_dir is None else state_dir
        identity = app_identity(module)
        store = module.HealthStore(directory / HEALTH_FILE)
        kept = module.LastGoodStore(directory).get()
        previous = kept.version if kept is not None else version
        action, message = module.startup_health(store, version, previous, identity)
        record = StartRecord(module, store, version, directory)
        _CURRENT = record
        if action is module.HealthAction.ROLL_BACK:
            logger.warning("%s", message)
            _restore(record, identity, installer, running)
        return record
    except Exception:  # noqa: BLE001 - tracking health must never stop the app from starting
        logger.warning("start-up health tracking failed; the start goes on", exc_info=True)
        return None


def _restore(record: StartRecord, identity: Any, installer: Any | None, running: Any | None) -> None:
    module = record.updater
    result = module.restore_last_good(
        apply.default_installer() if installer is None else installer,
        identity,
        record.state_dir,
        apply.other_instances_counter(module) if running is None else running,
    )
    if isinstance(result, module.HandoffRefused):
        logger.warning("rolling back to the last good version was not possible: %s", result.reason)
    else:
        logger.warning("the last good version %s will be restored when this process exits", result.version)


def mark_healthy(record: StartRecord | None = None) -> bool:
    """Mark the current start healthy (start-up completed). ``True`` if it was recorded. Never raises."""
    target = _CURRENT if record is None else record
    if target is None:
        return False
    try:
        target.store.mark_healthy(target.version)
        target.updater.clean_work_folders(target.state_dir)
    except Exception:  # noqa: BLE001 - see begin_start
        logger.warning("could not record a healthy start", exc_info=True)
        return False
    return True


def forget_current() -> None:
    """Drop the held record (tests)."""
    global _CURRENT
    _CURRENT = None
