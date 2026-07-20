"""Path resolution helpers — single source of truth.

Separates read-only bundle paths (built-in mappings, logging config,
shipped docs) from user-writable data paths (logs, custom mappings,
runtime config). Works identically when running from source or from
a PyInstaller one-file bundle.

Why this exists: relative paths like ``Path("config/mappings")`` break
in the frozen exe because the launcher chdirs to ``sys._MEIPASS`` (a
temp directory that's deleted on exit) and the scheduled-task runtime
has cwd set to ``%SystemRoot%\\System32``. Both scenarios need
absolute paths resolved against the right anchor.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import platformdirs

logger = logging.getLogger(__name__)

# The industry-standard per-OS user-data directory is keyed off this app name on
# EVERY OS. platformdirs uses the name verbatim (it does NOT case-fold), so all
# three platforms share the same ``DistrictSync`` leaf — a single, professional,
# consistent identity:
#   Windows  %LOCALAPPDATA%\DistrictSync
#   macOS    ~/Library/Application Support/DistrictSync
#   Linux    $XDG_DATA_HOME/DistrictSync  (default ~/.local/share/DistrictSync)
_APP_NAME = "DistrictSync"

# The pre-relocation location every existing install used. Kept as BOTH the
# migration source and the deterministic fallback, so a user is never stranded
# between two locations.
_LEGACY_DIR_NAME = ".districtsync"

# Breadcrumb dropped in the legacy dir after a successful migration.
_MOVED_BREADCRUMB = "MOVED.txt"


def bundle_root() -> Path:
    """Return the root of the PyInstaller bundle (or the project root in dev)."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file builds extract to sys._MEIPASS.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev layout: src/utils/paths.py -> ../../.. = project root.
    return Path(__file__).resolve().parent.parent.parent


def bundle_config_dir() -> Path:
    """Directory containing bundled read-only config (logging.conf, base mappings)."""
    return bundle_root() / "config"


def bundle_mappings_dir() -> Path:
    """Directory containing built-in mapping YAMLs shipped with the binary."""
    return bundle_config_dir() / "mappings"


def bundle_known_hosts_file() -> Path:
    """Bundled SSH ``known_hosts`` file pinning the SpacesEDU SFTP host keys.

    Read-only bundle asset (shipped via ``--add-data "config;config"``, so it
    rides along with the mappings). The user-writable override lives at
    :func:`user_known_hosts_file` and takes precedence.
    """
    return bundle_config_dir() / "known_hosts"


def user_known_hosts_file() -> Path:
    """Per-user ``known_hosts`` override for pinned SFTP host keys.

    Mirrors the mappings hotfix path: a file dropped here wins over the bundled
    :func:`bundle_known_hosts_file`, so host keys can be added or rotated on a
    district server without shipping a new release.
    """
    return user_data_dir() / "known_hosts"


def app_icon_path() -> Path:
    """Path to the DistrictSync sync-mark ``.ico`` (the EXE/file icon).

    A read-only *bundle* asset (not user-writable), so it resolves against
    ``bundle_root()`` exactly like the config dir: in dev this is
    ``<project root>/assets/districtsync.ico``; in a frozen PyInstaller build it is
    ``<_MEIPASS>/assets/districtsync.ico`` (the file is shipped there via the
    ``flet pack`` ``--add-data "assets;assets"`` arg). The EXE file icon is baked
    from this same asset by ``flet pack --icon`` at build time. Pure — resolves a
    path only. The running WINDOW's icon is :func:`window_icon_path` (the
    myBlueprint mark) — owner decision 2026-07-15: myB on the title bar, the sync
    mark for the app file itself.
    """
    return bundle_root() / "assets" / "districtsync.ico"


def window_icon_path() -> Path:
    """Path to the myBlueprint-mark ``.ico`` (the running window/title-bar/taskbar icon).

    Same bundle-asset resolution as :func:`app_icon_path` (``--add-data "assets;assets"``
    ships it into ``<_MEIPASS>/assets`` in the frozen exe). Sourced from the official
    myB favicon (transparent 16/32/48 layers — native title-bar sizes, no upscaling).
    Pure — resolves a path only; ``shell`` decides whether to set ``page.window.icon``.
    """
    return bundle_root() / "assets" / "myblueprint.ico"


def _platform_data_dir() -> Path:
    """The industry-standard per-OS user-data directory (NO side effects).

    Non-roaming on Windows — correct for the WAL SQLite run store, which must not
    be synced across machines mid-write. Resolves the location ONLY; it never
    creates the directory, so ``migrate_legacy_data_dir()`` can run before anything
    materializes the new location.
    """
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=False))


def _legacy_data_dir() -> Path:
    """The pre-relocation location (``~/.districtsync``) — migration source + fallback.

    ``Path.home()`` lives ONLY here (single source of truth for the legacy anchor).
    """
    return Path.home() / _LEGACY_DIR_NAME


def user_data_dir() -> Path:
    """Persistent per-user data directory (logs, custom mappings, app config, run store).

    Resolution is deterministic and never strands a user between two locations:
      1. the platform-standard dir if it already exists (fresh install here, or a
         completed migration), else
      2. the legacy ``~/.districtsync`` dir if it exists (pre-migration, or a
         migration that safely fell back), else
      3. create + return the platform-standard dir (a brand-new install).

    The move from (2) to (1) is an explicit, failure-safe entry-point step
    (``migrate_legacy_data_dir``) — NOT a side effect of this resolver — so a read
    can never half-move data.
    """
    new = _platform_data_dir()
    if new.exists():
        return new
    legacy = _legacy_data_dir()
    if legacy.exists():
        return legacy
    new.mkdir(parents=True, exist_ok=True)
    return new


def _write_moved_breadcrumb(legacy: Path, new: Path) -> None:
    """Drop a ``MOVED.txt`` breadcrumb in the legacy dir (best-effort; never raises).

    Written only AFTER a successful promote, so a breadcrumb failure cannot affect
    the migration outcome — the new location is already live and complete.
    """
    try:
        (legacy / _MOVED_BREADCRUMB).write_text(
            "DistrictSync moved its data on "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} to:\n"
            f"{new}\n\n"
            f"This folder ({legacy}) is no longer used by DistrictSync and is safe "
            "to delete. Your configuration, logs, and run history now live in the "
            "location above.\n",
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - cosmetic; migration already succeeded
        logger.warning("Could not write migration breadcrumb in %s (%s)", legacy, exc)


def migrate_legacy_data_dir() -> bool:
    """Relocate ``~/.districtsync`` to the platform data dir once, failure-safely.

    Mechanism — **stage-then-atomic-promote**, chosen precisely so a mid-migration
    failure can neither strand nor lose data:

      1. Run only when the legacy dir exists AND the new dir does not. This makes
         the call idempotent — a no-op on a fresh install or an already-migrated
         machine (the common case at every startup: one cheap ``exists()`` check).
      2. COPY the entire legacy tree — ``config.json``, ``etl_tool.log`` + its
         rotations, the ``mappings/`` dir, and ``history.db`` together with its
         ``-wal``/``-shm`` sidecars, as one unit — into a fresh staging dir under
         the NEW dir's *parent*. Same filesystem as the final location (so the
         promote is atomic), while the copy itself tolerates a cross-device
         home→appdata layout.
      3. Promote the fully-staged copy with a single ``os.replace``: the new dir
         becomes "live" only once EVERY file has copied. If any copy fails first,
         the new dir is never created, the staging copy is discarded, and the legacy
         dir stays fully intact and live — ``user_data_dir()`` keeps returning it,
         so a partial migration is invisible and no data is lost.
      4. Leave a ``MOVED.txt`` breadcrumb in the legacy dir. Legacy files are
         deliberately left in place (this is a copy, never a move/delete), so there
         is no window in which the only copy of the data is in flight.

    Returns ``True`` iff data was migrated in THIS call; ``False`` when there was
    nothing to migrate OR the migration failed and we safely fell back to the legacy
    location (logged WARNING). Never raises — safe to call unconditionally at entry.
    """
    new = _platform_data_dir()
    legacy = _legacy_data_dir()

    # Idempotent, fail-safe guard: only the legacy-exists-and-new-does-not state
    # warrants a migration. Every other state (fresh install, already migrated,
    # a prior safe fallback) is a no-op.
    if new.exists() or not legacy.exists():
        return False

    staging: Path | None = None
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{new.name}.migrating-", dir=new.parent))
        # Copy the whole tree into staging; promote only when it fully succeeds.
        shutil.copytree(legacy, staging, dirs_exist_ok=True, copy_function=shutil.copy2)
        # Windows AV/indexers can briefly hold a freshly-written directory, failing
        # the promote with a transient Access-denied — retry a couple of times
        # before falling back (the fallback itself stays safe either way).
        for attempt in range(3):
            try:
                os.replace(staging, new)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        staging = None  # promoted — must NOT be cleaned up in the except path
    except (OSError, shutil.Error) as exc:
        # `new` can exist here despite the entry guard: a concurrent process may
        # have promoted its own staging first (our os.replace then fails) — in
        # that case this process continues on the winner's complete copy.
        logger.warning(
            "Legacy app-data migration to %s failed (%s); data is intact — continuing to use %s",
            new,
            exc,
            new if new.exists() else legacy,
        )
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        return False

    _write_moved_breadcrumb(legacy, new)
    logger.info("Migrated DistrictSync data from %s to %s", legacy, new)
    return True


def user_mappings_dir() -> Path:
    """Per-user directory for district mapping overrides and custom configs."""
    path = user_data_dir() / "mappings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_log_file() -> Path:
    """Canonical log-file path, shared by CLI, wizard, and scheduled runs."""
    return user_data_dir() / "etl_tool.log"


def user_history_db() -> Path:
    """Canonical run-history SQLite store path (consumed by the run store, Slice 4b).

    Resolves through ``user_data_dir()`` at call time — never a module-level
    constant — so the test-isolation seam redirects it too (a store keyed off an
    import-time path would write the real ``history.db`` from every pipeline test).
    """
    return user_data_dir() / "history.db"
