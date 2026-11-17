"""The start guard at DistrictSync's entry (plan 0054 S5 follow-up, DSYNC-66).

``src/main.py`` calls :func:`run_entry` as its FIRST statement, before it imports anything heavy (pandas, the ETL, Flet).
That is the whole point of the shared package's ``myb_start_guard``: a start is counted before the app's own imports run,
so an update that breaks those imports is still counted, and after too many such starts the kept copy of the last good
version is restored before the broken code is imported again.

What this module decides (the package decides nothing about DistrictSync):

**Which starts count.** Only a start that will reach a confirmed start: the window (no arguments) and a conversion run
(``--sis``). ``--version``, ``--diagnose``, ``--schedule-check``, ``--elevated-apply``, the SFTP subcommands and Velopack's own
``--veloapp-*`` hooks are ``counted=False``: they open no window and never confirm, so counting them would make a quiet admin's
``--version`` (or an installer hook) look like a crash loop. They still run inside the guard, so a crash in them also logs
and exits at once instead of waiting on an error box.

**What a confirmed start is.** "Start-up completed", never "the conversion succeeded": for a conversion, the moment before
ETL begins (profile pinned, logging up, configs loadable); for the window, the first screen. A nightly that exits 1 over a
missing GDE file is a data problem, not a bad build, and its start is confirmed.

**Where the guard applies.** Only the installed flavour running from a Velopack install, per-user profile, no
``DISTRICTSYNC_DATA_DIR`` (except the e2e test build). Everywhere else this returns ``None`` and the app starts exactly as before.
Failing to load the guard or the profile also returns ``None``: the guard must never be the reason an app does not start.

**Recovery** restores the kept copy through the package (applied after this process exits). Before it does, the broken version is
recorded BAD in the health store, because recovery happens before the app's own health check could: without that record the
feed would offer the same broken update again at the next check. A restore that is refused (no kept copy, another copy running)
lets the app try again.

**The child job.** For the window only, ``start_child_job()`` runs first and ``adopt_children()`` is called when the Flet
client process is up and again at the first screen. The job holds only the window's process, never this process and never the
installer's apply helper, which must outlive the app.

Imports here are the standard library plus two stdlib-light DistrictSync modules; the shared packages are imported by NAME so
the portable build never collects them.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from src.update.package import HEALTH_FILE, UPDATER_STATE_FOLDER, app_identity
from src.update.startup import install_root
from src.utils.build_flavor import is_e2e_build, is_installed_build

_GUARD_MODULE: Final = "myb_start_guard"
_RECOVERY_MODULE: Final = "myb_desktop_updater.recovery"
_UPDATER_MODULE: Final = "myb_desktop_updater"

#: Argument that begins a Velopack-driven invocation (install, update, obsolete hooks).
_VELOPACK_PREFIX: Final = "--veloapp-"

logger = logging.getLogger(__name__)

_GUARD: ModuleType | None = None
_STATE_DIR: Path | None = None


def start_counts(args: list[str]) -> bool:
    """Whether this invocation will reach a confirmed start: the window, or a conversion run (never a Velopack hook)."""
    if any(a.startswith(_VELOPACK_PREFIX) for a in args):
        return False
    return not args or "--sis" in args


def is_window_start(args: list[str]) -> bool:
    return not args


def _applicable() -> bool:
    """Installed flavour, under a Velopack install, per-user profile, no data-dir override (except the e2e build)."""
    if not is_installed_build() or install_root() is None:
        return False
    from src.utils import paths  # light (platformdirs), but not needed anywhere else

    try:
        if paths.is_machine_scope():
            return False
    except paths.MachineScopeRefused:
        return False
    return not (paths.data_dir_override_active() and not is_e2e_build())


def guard_state_dir() -> Path:
    """Where the guard's count and crash log live: the updater's own state folder inside the profile."""
    from src.utils import paths

    return paths.user_data_dir() / UPDATER_STATE_FOLDER


def _record_bad_then_restore(state_dir: Path) -> int | None:
    """Recovery step for the guard: record this build bad, then restore the kept copy. ``1`` to stop, ``None`` to go on."""
    updater = importlib.import_module(_UPDATER_MODULE)
    recovery = importlib.import_module(_RECOVERY_MODULE)
    from src.utils.version import app_version

    identity = app_identity(updater)
    version = app_version()
    kept = updater.LastGoodStore(state_dir).get()
    if kept is not None and kept.version != version and updater.parse_version(version) is not None:
        store = updater.HealthStore(state_dir / HEALTH_FILE)
        # The package records a version bad only through its start-up check, which counts failed starts. Recovery runs
        # before the app's own check could, so run that check to its verdict here: more than two failed starts.
        for _ in range(store.max_failed_starts + 3):
            action = store.begin_start(version, kept.version)
            if action is updater.HealthAction.ROLL_BACK:
                break
    result = recovery.recover_broken_start(identity, state_dir, [Path(sys.executable).name])
    if isinstance(result, updater.HandoffRefused):
        logger.warning("recovery of a broken start was refused: %s", result.reason)
        return None
    logger.warning("this build could not start; the last good version %s will be restored", result.version)
    return 1


def run_entry(args: list[str] | None = None) -> int | None:
    """Run the app under the start guard, or return ``None`` when the guard does not apply (the app then starts as before).

    Returns the exit code of the guarded run. The guarded body imports ``src.main`` (and so every heavy import) INSIDE the
    guard and runs ``cli``; a crash ends the process with exit code 1 through the package, never an error dialog.
    """
    global _GUARD, _STATE_DIR
    argv = sys.argv[1:] if args is None else args
    try:
        if not _applicable():
            return None
        guard = importlib.import_module(_GUARD_MODULE)
        state_dir = guard_state_dir()
    except Exception:  # noqa: BLE001 - the guard must never be the reason the app does not start
        return None
    _GUARD, _STATE_DIR = guard, state_dir
    if is_window_start(argv):
        guard.start_child_job()  # before anything that starts a process: only the window's helper must be adopted

    def app() -> int:
        from src.main import cli  # the heavy imports happen here, inside the guard

        return int(cli(argv))

    return int(
        guard.run_guarded(
            app,
            state_dir=state_dir,
            build=guard.build_fingerprint(),
            recover=lambda: _record_bad_then_restore(state_dir),
            counted=start_counts(argv),
        )
    )


def confirm_start() -> bool:
    """The start is confirmed (start-up completed): clear the guard's count. ``False`` where the guard is not in use."""
    if _GUARD is None or _STATE_DIR is None:
        return False
    try:
        _GUARD.confirm_start(_STATE_DIR)
    except Exception:  # noqa: BLE001 - advisory
        logger.debug("could not confirm the start", exc_info=True)
        return False
    return True


def adopt_window_children() -> int:
    """Put the Flet window process (this process's children so far) into the kill-on-close job. Never raises."""
    if _GUARD is None:
        return 0
    try:
        return int(_GUARD.adopt_children())
    except Exception:  # noqa: BLE001 - advisory
        logger.debug("could not adopt the window process", exc_info=True)
        return 0


def forget_guard() -> None:
    """Drop the held guard (tests)."""
    global _GUARD, _STATE_DIR
    _GUARD, _STATE_DIR = None, None


def guard_module() -> Any:
    return _GUARD
