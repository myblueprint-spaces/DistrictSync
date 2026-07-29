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

# Documented SUPPORT / TEST override for the whole user-data profile — see
# ``_override_data_dir`` and ``user_data_dir`` for the contract and the why.
_DATA_DIR_ENV_VAR = "DISTRICTSYNC_DATA_DIR"

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


def _override_data_dir() -> Path | None:
    """The ``DISTRICTSYNC_DATA_DIR`` override, or ``None`` when it is not in play.

    A support/test seam, NOT a user setting — the app never writes this variable.
    It exists because a FROZEN exe cannot otherwise be pointed at a throwaway
    profile: ``platformdirs`` resolves the Windows location through
    ``SHGetKnownFolderPath`` and **ignores a ``LOCALAPPDATA`` env var** (verified),
    so redirecting a packed ``DistrictSync.exe`` — for the CI exe smokes, for a
    non-destructive fresh-profile QA walk, or for a support repro on a district
    machine — is impossible without an explicit seam.

    Boundary validation (the value is untrusted operator input): an unset, empty,
    or whitespace-only value means "not in play" (``FOO=`` in a shell must not
    resolve the profile to the process CWD). ``~`` expands (an expanded ``~`` is
    already absolute).

    A RELATIVE value is REFUSED with :class:`ValueError` rather than silently
    absolutized against the CWD. The frozen launcher chdirs into a temp
    ``sys._MEIPASS`` that is deleted on exit, and a scheduled task runs with cwd
    ``%SystemRoot%\\System32`` — so "relative" means the profile lands in a
    directory that is about to vanish, or in a system directory, and the NEXT run
    resolves somewhere else again. Silently absolutizing hides that; refusing makes
    it a one-line fix. Always pass an absolute path.

    Raises:
        ValueError: the value is set but not absolute.
    """
    raw = os.environ.get(_DATA_DIR_ENV_VAR, "").strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"{_DATA_DIR_ENV_VAR} must be an absolute path (got {raw!r})")
    return expanded.resolve()


def user_data_dir() -> Path:
    """Persistent per-user data directory (logs, custom mappings, app config, run store).

    Resolution is deterministic and never strands a user between two locations:
      0. ``DISTRICTSYNC_DATA_DIR`` (see :func:`_override_data_dir`) — when set it
         **wins outright**: the entire profile lives there, with NO legacy fallback
         and NO migration (``migrate_legacy_data_dir`` is a no-op while it is set,
         so the resolver and the migration can never disagree about the location),
         else
      1. the platform-standard dir if it already exists (fresh install here, or a
         completed migration), else
      2. the legacy ``~/.districtsync`` dir if it exists (pre-migration, or a
         migration that safely fell back), else
      3. create + return the platform-standard dir (a brand-new install).

    Step 0 creates the directory for the same reason step 3 does: the override
    names where the profile *is*, and the log sink opens a file in it immediately.
    The startup banner (``utils/version.startup_banner``) logs the RESOLVED dir on
    every entry, so which step won is always diagnosable from the log.

    The move from (2) to (1) is an explicit, failure-safe entry-point step
    (``migrate_legacy_data_dir``) — NOT a side effect of this resolver — so a read
    can never half-move data.

    Raises:
        ValueError: the override is set but not absolute (see :func:`_override_data_dir`).
        RuntimeError: the override is set but unusable as a directory. Fail LOUD rather
            than fall through to the platform dir — a silent fallback would write the
            profile somewhere the operator did not ask for and did not know to look,
            which is precisely the confusion the override exists to remove.
    """
    override = _override_data_dir()
    if override is not None:
        try:
            override.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"{_DATA_DIR_ENV_VAR}={override} could not be used as the profile directory "
                f"({exc}). Unset it or point it at a writable absolute path."
            ) from exc
        return override
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


def _override_suppresses_migration() -> bool:
    """Whether ``DISTRICTSYNC_DATA_DIR`` should suppress the legacy migration.

    NARROW by design: only an override pointing SOMEWHERE ELSE suppresses. An override
    aimed AT the canonical platform dir resolves to the exact location the migration
    targets, so there is no split-brain to prevent — suppressing there would strand
    ``~/.districtsync`` forever behind a variable that changed nothing.

    Never raises. :func:`migrate_legacy_data_dir` documents a never-raises contract and
    is called unconditionally at entry, while :func:`_override_data_dir` deliberately
    fails LOUD (``ValueError`` on a relative value; ``Path.expanduser`` raises
    ``RuntimeError`` for an unknown ``~user`` on POSIX). An unresolvable value is
    treated as unset HERE and still fails loud at :func:`user_data_dir` — the boundary
    that actually decides where data goes.
    """
    try:
        override = _override_data_dir()
        if override is None:
            return False
        canonical = _platform_data_dir().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(
            "%s could not be resolved (%s) — treating it as unset for the legacy migration",
            _DATA_DIR_ENV_VAR,
            exc,
        )
        return False
    if override == canonical:
        return False
    logger.debug("%s is set — skipping the legacy app-data migration", _DATA_DIR_ENV_VAR)
    return True


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

    A ``DISTRICTSYNC_DATA_DIR`` pointing ELSEWHERE makes this a **no-op**: this function
    resolves ``_platform_data_dir()``/``_legacy_data_dir()`` directly (it deliberately
    does not go through :func:`user_data_dir`), so without the guard an overridden run
    would migrate the legacy tree into the *platform* dir while reading its profile from
    the *override* — a split-brain the override exists to prevent. An override pointing
    AT the platform dir is NOT suppressed (see :func:`_override_suppresses_migration`).
    """
    if _override_suppresses_migration():
        return False

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
