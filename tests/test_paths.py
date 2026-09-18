"""Tests for src/utils/paths.py — path resolution helpers + app-data relocation.

test_paths.py is the guard for the REAL path helpers, so every test opts out of
the autouse ``user_data_dir`` isolation (``real_user_data_dir`` marker) and drives
the underlying seams (``_platform_data_dir`` / ``_legacy_data_dir`` / ``Path.home``)
itself — otherwise these tests would assert against the isolation fixture's fake,
and the relocation/migration tests would risk touching the real ~/.districtsync.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from platformdirs.macos import MacOS
from platformdirs.unix import Unix
from platformdirs.windows import get_win_folder_from_env_vars

from src.utils import paths as paths_module

# Opt out of the autouse user_data_dir isolation (test the real seam) — see docstring.
pytestmark = pytest.mark.real_user_data_dir


@pytest.fixture
def data_dirs(tmp_path, monkeypatch):
    """Redirect the platform + legacy data-dir seams into tmp (hermetic, never real).

    Neither dir exists initially, so tests control the exact new-vs-legacy state.
    Returns a namespace exposing ``.new`` (platform dir) and ``.legacy``.

    ``DISTRICTSYNC_DATA_DIR`` is cleared as well (belt-and-braces on top of the
    suite-wide clear in ``conftest``): it is step 0 of the ladder, so a developer
    shell that happens to export it would otherwise short-circuit every
    resolution test below.
    """
    new = tmp_path / "platform" / "DistrictSync"
    legacy = tmp_path / "home" / ".districtsync"
    monkeypatch.delenv(paths_module._DATA_DIR_ENV_VAR, raising=False)
    monkeypatch.setattr(paths_module, "_platform_data_dir", lambda: new)
    monkeypatch.setattr(paths_module, "_legacy_data_dir", lambda: legacy)
    return SimpleNamespace(new=new, legacy=legacy)


# ---------------------------------------------------------------------------
# Per-OS resolution — the real strings platformdirs returns for our chosen args.
# ---------------------------------------------------------------------------


class TestPerOSResolution:
    """`user_data_dir()` must land on the industry-standard dir on every OS.

    The exact call is pinned to ``platformdirs.user_data_dir("DistrictSync",
    appauthor=False, roaming=False)``. platformdirs uses the appname VERBATIM (no
    case-folding), so the leaf is ``DistrictSync`` on all three OSes — a single,
    consistent, professional identity. (The plan's prose said Linux would be
    lowercase ``districtsync``; the pinned call decides, and it is capitalized —
    see the module docstring / DECISIONS for this judgment call.)
    """

    def test_platform_data_dir_calls_platformdirs_with_pinned_args(self, monkeypatch):
        captured: dict[str, object] = {}

        def _spy(appname, **kwargs):
            captured["appname"] = appname
            captured["kwargs"] = kwargs
            return os.path.join(os.sep + "tmp", "DistrictSync")

        monkeypatch.setattr(paths_module.platformdirs, "user_data_dir", _spy)
        result = paths_module._platform_data_dir()

        assert captured["appname"] == "DistrictSync"
        assert captured["kwargs"] == {"appauthor": False, "roaming": False}
        assert isinstance(result, Path)

    def test_windows_uses_localappdata(self, monkeypatch):
        # Windows: %LOCALAPPDATA%\DistrictSync. The ctypes backend only runs on real
        # Windows, so use the env-var resolver (host-independent) to document the base.
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        base = get_win_folder_from_env_vars("CSIDL_LOCAL_APPDATA")
        combined = os.path.join(base, "DistrictSync").replace("\\", "/")
        assert combined.endswith("AppData/Local/DistrictSync")

    def test_macos_uses_application_support(self):
        result = MacOS("DistrictSync", appauthor=False, roaming=False).user_data_dir.replace("\\", "/")
        assert result.endswith("Library/Application Support/DistrictSync")

    def test_linux_uses_xdg_data_default(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        result = Unix("DistrictSync", appauthor=False, roaming=False).user_data_dir.replace("\\", "/")
        assert result.endswith(".local/share/DistrictSync")
        # VERBATIM appname — the dir is NOT lowercased to "districtsync".
        assert not result.endswith("districtsync")

    def test_linux_respects_xdg_data_home(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg/data")
        result = Unix("DistrictSync", appauthor=False, roaming=False).user_data_dir.replace("\\", "/")
        assert result.endswith("/custom/xdg/data/DistrictSync")


# ---------------------------------------------------------------------------
# Deterministic new-vs-legacy resolution rule.
# ---------------------------------------------------------------------------


class TestUserDataDirResolution:
    def test_fresh_install_creates_platform_dir(self, data_dirs):
        d = paths_module.user_data_dir()
        assert d == data_dirs.new
        assert d.exists() and d.is_dir()
        assert not data_dirs.legacy.exists()

    def test_returns_legacy_when_only_legacy_exists(self, data_dirs):
        data_dirs.legacy.mkdir(parents=True)
        d = paths_module.user_data_dir()
        assert d == data_dirs.legacy
        # A read must NOT create the new dir — doing so would strand the legacy data
        # (the new dir would win next time while its contents were never migrated).
        assert not data_dirs.new.exists()

    def test_prefers_platform_dir_when_both_exist(self, data_dirs):
        data_dirs.legacy.mkdir(parents=True)
        data_dirs.new.mkdir(parents=True)
        assert paths_module.user_data_dir() == data_dirs.new

    def test_idempotent(self, data_dirs):
        assert paths_module.user_data_dir() == paths_module.user_data_dir()


class TestDataDirOverride:
    """``DISTRICTSYNC_DATA_DIR`` is step 0 of the ladder and WINS OUTRIGHT (plan 0038, flag 9).

    The seam exists because ``platformdirs`` resolves the Windows location through
    ``SHGetKnownFolderPath`` and ignores ``LOCALAPPDATA`` — a frozen exe cannot be
    pointed at a throwaway profile any other way (the CI exe smokes, the
    non-destructive fresh-profile QA walk, a support repro).
    """

    def test_override_wins_over_both_existing_locations(self, data_dirs, tmp_path, monkeypatch):
        # The strongest form: BOTH ladder locations exist and are populated, and the
        # override still wins — there is no fallback to reason about.
        data_dirs.new.mkdir(parents=True)
        data_dirs.legacy.mkdir(parents=True)
        override = tmp_path / "override-profile"
        override.mkdir()
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))
        assert paths_module.user_data_dir() == override.resolve()

    def test_override_creates_the_dir_when_absent(self, data_dirs, tmp_path, monkeypatch):
        # Same contract as step 3 (a brand-new install): the override names where the
        # profile IS, and the log sink opens a file in it immediately.
        override = tmp_path / "not" / "yet" / "there"
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))
        resolved = paths_module.user_data_dir()
        assert resolved == override.resolve()
        assert resolved.is_dir()

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_value_is_not_in_play(self, data_dirs, monkeypatch, blank):
        # `DISTRICTSYNC_DATA_DIR=` in a shell must not resolve the profile to the
        # process CWD — a blank value means "not set", and the ladder runs as usual.
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, blank)
        assert paths_module._override_data_dir() is None
        assert paths_module.user_data_dir() == data_dirs.new

    @pytest.mark.parametrize("relative", ["relative-profile", "./sub/dir", "sub/dir"])
    def test_relative_value_is_REFUSED(self, data_dirs, monkeypatch, relative):
        # The frozen launcher chdirs into a temp _MEIPASS that is DELETED on exit, and a
        # scheduled task runs with cwd %SystemRoot%\System32 — "relative" therefore means
        # "a directory that is about to vanish, or a system directory, and somewhere else
        # again next run". Silently absolutizing (the old behavior) hid exactly that;
        # refusing turns it into a one-line fix.
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, relative)
        with pytest.raises(ValueError, match="must be an absolute path"):
            paths_module._override_data_dir()
        with pytest.raises(ValueError, match="must be an absolute path"):
            paths_module.user_data_dir()

    def test_unusable_override_fails_loud_instead_of_falling_back(self, data_dirs, tmp_path, monkeypatch):
        # A silent fallback to the platform dir would write the profile somewhere the
        # operator did not ask for and did not know to look — the very confusion the
        # override exists to remove.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("i am a file", encoding="utf-8")
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(blocker / "profile"))
        with pytest.raises(RuntimeError, match="could not be used as the profile directory"):
            paths_module.user_data_dir()
        assert not data_dirs.new.exists()

    def test_tilde_expands(self, data_dirs, monkeypatch):
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, "~/dsync-profile")
        assert paths_module._override_data_dir() == (Path.home() / "dsync-profile").resolve()

    def test_derived_paths_follow_the_override(self, data_dirs, tmp_path, monkeypatch):
        # The whole profile moves as ONE unit — log, run store, and custom mappings
        # all hang off the single seam (a split profile is the failure mode).
        override = tmp_path / "override-profile"
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))
        resolved = override.resolve()
        assert paths_module.user_log_file() == resolved / "etl_tool.log"
        assert paths_module.user_history_db() == resolved / "history.db"
        assert paths_module.user_mappings_dir() == resolved / "mappings"
        assert paths_module.user_known_hosts_file() == resolved / "known_hosts"

    def test_override_suppresses_the_legacy_migration(self, data_dirs, tmp_path, monkeypatch):
        # Without the override this state (legacy present, platform absent) is exactly
        # the one that migrates. The guard keeps the resolver and the migration from
        # disagreeing: migrate() bypasses user_data_dir() and would otherwise populate
        # the PLATFORM dir while the app reads the OVERRIDE — a split-brain profile.
        TestMigrateLegacyDataDir._seed_legacy(data_dirs.legacy)
        override = tmp_path / "override-profile"
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))

        assert paths_module.migrate_legacy_data_dir() is False
        assert not data_dirs.new.exists()
        assert (data_dirs.legacy / "config.json").exists()  # legacy untouched
        assert paths_module.user_data_dir() == override.resolve()

    def test_migration_still_runs_once_the_override_is_removed(self, data_dirs, tmp_path, monkeypatch):
        # The suppression is scoped to the override being in play — not a permanent
        # opt-out baked into the install. SET it, observe the suppression, then UNSET
        # it and observe the migration: a test that only ever ran with the variable
        # absent would pass identically if the guard were permanent.
        TestMigrateLegacyDataDir._seed_legacy(data_dirs.legacy)
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(tmp_path / "override-profile"))
        assert paths_module.migrate_legacy_data_dir() is False
        assert not data_dirs.new.exists()

        monkeypatch.delenv(paths_module._DATA_DIR_ENV_VAR)
        assert paths_module.migrate_legacy_data_dir() is True
        assert (data_dirs.new / "config.json").exists()

    def test_override_pointing_AT_the_platform_dir_does_not_suppress_migration(self, data_dirs, monkeypatch):
        # The guard exists to prevent a SPLIT profile (migrate into the platform dir
        # while reading the override). An override aimed at the platform dir resolves
        # to the very location the migration targets, so there is nothing to split —
        # suppressing there would strand ~/.districtsync forever behind a variable
        # that changed nothing.
        TestMigrateLegacyDataDir._seed_legacy(data_dirs.legacy)
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(data_dirs.new))

        assert paths_module.migrate_legacy_data_dir() is True
        assert (data_dirs.new / "config.json").exists()
        assert paths_module.user_data_dir() == data_dirs.new.resolve()

    def test_migration_never_raises_on_an_unresolvable_override(self, data_dirs, monkeypatch):
        # `migrate_legacy_data_dir` documents a never-raises contract and is called
        # unconditionally at entry, while `_override_data_dir` deliberately fails loud
        # (ValueError on a relative value; Path.expanduser raises RuntimeError for an
        # unknown ~user on POSIX). A bad value is treated as unset HERE and still fails
        # loud at user_data_dir(), which is the boundary that decides where data goes.
        TestMigrateLegacyDataDir._seed_legacy(data_dirs.legacy)

        def _boom():
            raise RuntimeError("Could not determine home directory")

        monkeypatch.setattr(paths_module, "_override_data_dir", _boom)
        assert paths_module.migrate_legacy_data_dir() is True
        assert (data_dirs.new / "config.json").exists()


class TestDerivedUserPaths:
    """The mappings/log/history-db helpers hang off the resolved data dir."""

    def test_mappings_dir_under_resolved_data_dir(self, data_dirs):
        d = paths_module.user_mappings_dir()
        assert d == data_dirs.new / "mappings"
        assert d.exists() and d.is_dir()

    def test_log_file_under_resolved_data_dir(self, data_dirs):
        assert paths_module.user_log_file() == data_dirs.new / "etl_tool.log"

    def test_history_db_under_resolved_data_dir(self, data_dirs):
        assert paths_module.user_history_db() == data_dirs.new / "history.db"

    def test_history_db_resolves_through_seam_at_call_time(self, monkeypatch, tmp_path):
        # The store must resolve its path through the single seam at call time (not a
        # module constant) so the test-isolation fixture redirects it too.
        target = tmp_path / "isolated" / "DistrictSync"
        monkeypatch.setattr(paths_module, "user_data_dir", lambda: target)
        assert paths_module.user_history_db() == target / "history.db"


# ---------------------------------------------------------------------------
# Legacy → platform relocation (failure-safe, idempotent).
# ---------------------------------------------------------------------------


class TestMigrateLegacyDataDir:
    @staticmethod
    def _seed_legacy(legacy: Path) -> None:
        """Populate a legacy dir with the full set of real artifacts."""
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_text('{"sis_type": "sd40myedbc"}', encoding="utf-8")
        (legacy / "etl_tool.log").write_text("live log line\n", encoding="utf-8")
        (legacy / "etl_tool.log.1").write_text("rotated 1\n", encoding="utf-8")
        (legacy / "etl_tool.log.2").write_text("rotated 2\n", encoding="utf-8")
        (legacy / "history.db").write_bytes(b"SQLite format 3\x00")
        (legacy / "history.db-wal").write_bytes(b"wal-data")
        (legacy / "history.db-shm").write_bytes(b"shm-data")
        mappings = legacy / "mappings"
        mappings.mkdir()
        (mappings / "custom.yaml").write_text("custom: true\n", encoding="utf-8")

    def test_fresh_install_is_noop(self, data_dirs):
        assert paths_module.migrate_legacy_data_dir() is False
        assert not data_dirs.new.exists()
        assert not data_dirs.legacy.exists()

    def test_migrates_all_content_and_leaves_breadcrumb(self, data_dirs):
        self._seed_legacy(data_dirs.legacy)

        assert paths_module.migrate_legacy_data_dir() is True

        new = data_dirs.new
        assert (new / "config.json").read_text(encoding="utf-8") == '{"sis_type": "sd40myedbc"}'
        assert (new / "etl_tool.log").exists()
        assert (new / "etl_tool.log.1").exists()
        assert (new / "etl_tool.log.2").exists()
        assert (new / "mappings" / "custom.yaml").read_text(encoding="utf-8") == "custom: true\n"

        # The breadcrumb names the new location so a human can find the data.
        crumb = (data_dirs.legacy / "MOVED.txt").read_text(encoding="utf-8")
        assert str(new) in crumb

        # Copy-not-move: legacy files stay fully intact (nothing is ever stranded).
        assert (data_dirs.legacy / "config.json").exists()
        assert (data_dirs.legacy / "history.db").exists()

        # Subsequent resolution now returns the new location.
        assert paths_module.user_data_dir() == new

    def test_wal_sidecars_move_as_a_unit(self, data_dirs):
        self._seed_legacy(data_dirs.legacy)
        assert paths_module.migrate_legacy_data_dir() is True
        new = data_dirs.new
        assert (new / "history.db").read_bytes() == b"SQLite format 3\x00"
        assert (new / "history.db-wal").read_bytes() == b"wal-data"
        assert (new / "history.db-shm").read_bytes() == b"shm-data"

    def test_idempotent_second_call_is_noop(self, data_dirs):
        self._seed_legacy(data_dirs.legacy)
        assert paths_module.migrate_legacy_data_dir() is True
        # The new dir now exists → a second call short-circuits without re-copying.
        assert paths_module.migrate_legacy_data_dir() is False
        assert (data_dirs.new / "config.json").exists()

    def test_failure_keeps_legacy_live_with_no_data_loss(self, data_dirs, monkeypatch, caplog):
        self._seed_legacy(data_dirs.legacy)
        real_copy2 = shutil.copy2

        def failing_copy2(src, dst, *args, **kwargs):
            # Simulate a locked file partway through the copy. copytree may hand the
            # copy function an ``os.DirEntry`` rather than a str, so normalize via
            # ``os.fspath`` before matching.
            if os.fspath(src).endswith("history.db"):
                raise OSError("simulated locked file")
            return real_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(paths_module.shutil, "copy2", failing_copy2)

        with caplog.at_level(logging.WARNING, logger="src.utils.paths"):
            assert paths_module.migrate_legacy_data_dir() is False

        # The new dir never became live → the partial migration is invisible.
        assert not data_dirs.new.exists()
        assert paths_module.user_data_dir() == data_dirs.legacy

        # Legacy data is fully intact — no data loss.
        assert (data_dirs.legacy / "config.json").exists()
        assert (data_dirs.legacy / "history.db").exists()
        assert (data_dirs.legacy / "mappings" / "custom.yaml").exists()

        # No orphaned staging dir left behind.
        assert list(data_dirs.new.parent.glob("DistrictSync.migrating-*")) == []

        # The failure was logged (never swallowed silently).
        assert any("migration" in r.getMessage().lower() for r in caplog.records)

    def test_promote_failure_cleans_staging_and_keeps_legacy_live(self, data_dirs, monkeypatch, caplog):
        # The os.replace promote itself fails (the realistic concurrent-second-process
        # / TOCTOU case): the fully-copied staging dir must be cleaned up, the new dir
        # must not become live, and legacy stays the resolved location — the branch
        # after the copy succeeds but before the sentinel clears.
        self._seed_legacy(data_dirs.legacy)

        def failing_replace(src, dst, *args, **kwargs):
            raise OSError("simulated promote failure")

        monkeypatch.setattr(paths_module.os, "replace", failing_replace)

        with caplog.at_level(logging.WARNING, logger="src.utils.paths"):
            assert paths_module.migrate_legacy_data_dir() is False

        assert not data_dirs.new.exists()
        assert paths_module.user_data_dir() == data_dirs.legacy
        assert (data_dirs.legacy / "config.json").exists()
        assert (data_dirs.legacy / "history.db").exists()

        # The fully-staged copy is discarded — no orphaned staging dir.
        assert list(data_dirs.new.parent.glob("DistrictSync.migrating-*")) == []

        assert any("migration" in r.getMessage().lower() for r in caplog.records)

    def test_absent_legacy_never_touches_real_home(self, tmp_path, monkeypatch):
        # Belt-and-suspenders: with no legacy dir, migration is a pure no-op even
        # though _platform_data_dir points somewhere writable.
        new = tmp_path / "platform" / "DistrictSync"
        legacy = tmp_path / "does_not_exist" / ".districtsync"
        monkeypatch.setattr(paths_module, "_platform_data_dir", lambda: new)
        monkeypatch.setattr(paths_module, "_legacy_data_dir", lambda: legacy)
        assert paths_module.migrate_legacy_data_dir() is False
        assert not new.exists()


# ---------------------------------------------------------------------------
# Bundle (read-only) path helpers — unchanged behavior.
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_mode(monkeypatch):
    """Ensure sys.frozen / sys._MEIPASS are cleared for dev-mode tests."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


class TestBundleRoot:
    def test_dev_returns_project_root(self, dev_mode):
        root = paths_module.bundle_root()
        # Project root should contain the config/ dir we ship
        assert (root / "config").is_dir()
        assert (root / "src" / "utils" / "paths.py").is_file()

    def test_frozen_returns_meipass(self, monkeypatch, tmp_path):
        # Simulate PyInstaller-frozen environment. Both attributes are
        # restored by monkeypatch at teardown so other tests don't
        # inherit frozen state.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths_module.bundle_root() == tmp_path


class TestBundleConfigDir:
    def test_points_at_bundled_config(self, dev_mode):
        d = paths_module.bundle_config_dir()
        assert d.name == "config"
        assert (d / "logging.conf").is_file()


class TestBundleMappingsDir:
    def test_contains_builtin_mappings(self, dev_mode):
        d = paths_module.bundle_mappings_dir()
        assert (d / "myedbc_mapping.yaml").is_file()
        assert (d / "sd40myedbc_mapping.yaml").is_file()


class TestAppIconPath:
    """The brand `.ico` resolves against the bundle root (dev tree vs frozen)."""

    def test_dev_points_at_committed_ico(self, dev_mode):
        p = paths_module.app_icon_path()
        # Dev: <project root>/assets/districtsync.ico — and the binary is committed,
        # so the runtime path resolves in a source run too (manual-Verify: dev titlebar).
        assert p == paths_module.bundle_root() / "assets" / "districtsync.ico"
        assert p.name == "districtsync.ico"
        assert p.is_file(), "the committed brand .ico must exist at the resolved dev path"

    def test_frozen_resolves_under_meipass(self, monkeypatch, tmp_path):
        # Frozen: <_MEIPASS>/assets/districtsync.ico — where `flet pack --add-data
        # "assets;assets"` places it, so `page.window.icon` resolves in the exe.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths_module.app_icon_path() == tmp_path / "assets" / "districtsync.ico"


class TestWindowIconPath:
    """The myBlueprint-mark `.ico` (running window/title-bar/taskbar icon) resolves like
    every bundle asset — dev tree vs frozen `_MEIPASS`. Split from the EXE-file icon
    (`app_icon_path`) per the 2026-07-15 owner decision: myB on the title bar, the
    DistrictSync sync mark on the app file itself."""

    def test_dev_points_at_committed_ico(self, dev_mode):
        p = paths_module.window_icon_path()
        assert p == paths_module.bundle_root() / "assets" / "myblueprint.ico"
        assert p.name == "myblueprint.ico"
        assert p.is_file(), "the committed myBlueprint .ico must exist at the resolved dev path"

    def test_frozen_resolves_under_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths_module.window_icon_path() == tmp_path / "assets" / "myblueprint.ico"


# ===========================================================================
#  Machine scope (plan 0049 S-1a-i) — the switch, the trust predicate, the pin
# ===========================================================================
#
#  Governing invariant: with the switch OFF this whole section is inert — every
#  assertion below that describes a machine-scoped install is reached only through
#  monkeypatched seams, and `AC1`'s mechanical twin (no registry WRITE API anywhere
#  under src/) is what makes "nothing in the field can turn it on" a fact.


MACHINE_SCOPE_KEY = paths_module.MACHINE_SCOPE_KEY_PATH
Reason = paths_module.MachineScopeRefusedReason
# Captured at IMPORT, before the autouse isolation fixture forces the switch off — the
# registry-read tests below drive the REAL function through its own monkeypatched seam.
_REAL_MACHINE_SWITCH_ON = paths_module._machine_switch_on
WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="winreg is a Windows-only module")

_TRUSTED_SECURITY = ("S-1-5-32-544", paths_module._SE_DACL_PROTECTED)

# The DACL a provisioned profile actually gets (SYSTEM · Administrators · setup user ·
# principal), as ``(ace type, SID)`` pairs — the shape ``_read_dacl_aces`` returns. Used as
# the ``security`` fixture's default so every pre-S-1b row keeps asserting exactly what it
# asserted before the trust predicate grew its open-group walk.
_TRUSTED_ACES = (
    (0, "S-1-5-18"),
    (0, "S-1-5-32-544"),
    (0, "S-1-5-21-1-2-3-1001"),
    (0, "S-1-5-21-1-2-3-1002"),
)


@pytest.fixture
def machine_dir(tmp_path, monkeypatch):
    """Redirect ``machine_data_dir()`` into tmp and return the (not yet created) path.

    Never the real ``C:\\ProgramData\\DistrictSync``: these tests must not read, create
    or trust anything outside tmp.
    """
    target = tmp_path / "ProgramData" / "DistrictSync"
    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: target)
    return target


@pytest.fixture
def switch(monkeypatch):
    """Drive the HKLM switch directly (the registry read has its own tests below)."""

    def _set(on: bool) -> None:
        monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: on)

    return _set


@pytest.fixture
def security(monkeypatch):
    """Drive the owner-SID / DACL-control read AND the DACL ace walk (both are seams).

    ``aces`` defaults to a correctly-provisioned DACL so every call site written before
    S-1b — all of which pass two positional arguments — keeps testing exactly what it
    tested then. The third seam is here rather than in its own fixture because it belongs
    to the same predicate: ``_assert_machine_dir_trusted`` now makes three reads, and a
    driver that drove only two would leave the third hitting the real Win32 API (an
    ``OSError`` → ``INACCESSIBLE`` on CI's Linux leg, a real ``%TEMP%`` DACL on Windows).
    """

    def _set(owner_sid: str, control: int, aces: tuple[tuple[int, str], ...] = _TRUSTED_ACES) -> None:
        monkeypatch.setattr(paths_module, "_read_dir_security", lambda path: (owner_sid, control))
        monkeypatch.setattr(paths_module, "_read_dacl_aces", lambda path: aces)

    return _set


@pytest.fixture
def user_scope_spy(monkeypatch, data_dirs):
    """Record every per-user resolution so a silent FALL-THROUGH is visible."""
    calls: list[bool] = []

    def _resolve(*, create: bool) -> Path:
        calls.append(create)
        if create:
            data_dirs.new.mkdir(parents=True, exist_ok=True)
        return data_dirs.new

    monkeypatch.setattr(paths_module, "_user_scope_data_dir", _resolve)
    return calls


class TestMachineDataDir:
    def test_calls_platformdirs_site_dir_with_pinned_args(self, monkeypatch):
        captured: dict[str, object] = {}

        def _spy(appname, **kwargs):
            captured["appname"] = appname
            captured["kwargs"] = kwargs
            return os.path.join(os.sep + "tmp", "DistrictSync")

        monkeypatch.setattr(paths_module.platformdirs, "site_data_dir", _spy)
        result = paths_module.machine_data_dir()

        assert captured["appname"] == "DistrictSync"
        assert captured["kwargs"] == {"appauthor": False}
        assert isinstance(result, Path)

    def test_creates_nothing(self, tmp_path, monkeypatch):
        target = tmp_path / "ProgramData" / "DistrictSync"
        monkeypatch.setattr(paths_module.platformdirs, "site_data_dir", lambda *a, **k: str(target))
        assert paths_module.machine_data_dir() == target
        assert not target.exists()


class TestMachineSwitchRead:
    """The HKLM read: absent is NORMAL, unreadable is a REFUSAL, never a fall-through."""

    def _reader(self, monkeypatch, result=None, exc: BaseException | None = None):
        def _read() -> tuple[object, int]:
            if exc is not None:
                raise exc
            assert result is not None
            return result

        monkeypatch.setattr(paths_module, "_read_machine_switch_value", _read)
        monkeypatch.setattr(paths_module.sys, "platform", "win32")

    def test_dword_one_is_on(self, monkeypatch):
        self._reader(monkeypatch, result=(1, paths_module._REG_DWORD))
        assert _REAL_MACHINE_SWITCH_ON() is True

    def test_absent_key_is_off_and_silent(self, monkeypatch, caplog):
        # The state of every install in the field: a missing key is deterministic
        # FileNotFoundError and must not log a warning every single run.
        self._reader(monkeypatch, exc=FileNotFoundError(2, "not found"))
        with caplog.at_level(logging.DEBUG, logger="src.utils.paths"):
            assert _REAL_MACHINE_SWITCH_ON() is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.parametrize(
        "result",
        [
            ("1", 1),  # REG_SZ "1" — the shape a hand-edit produces
            (0, 4),  # REG_DWORD 0 — explicitly off
            (2, 4),  # REG_DWORD 2 — not the documented value
        ],
    )
    def test_wrong_type_or_value_is_off_with_one_warning_naming_the_key(self, monkeypatch, caplog, result):
        self._reader(monkeypatch, result=result)
        with caplog.at_level(logging.WARNING, logger="src.utils.paths"):
            assert _REAL_MACHINE_SWITCH_ON() is False
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert MACHINE_SCOPE_KEY in warnings[0].getMessage()

    def test_permission_error_refuses_rather_than_falling_back(self, monkeypatch):
        # "Any exception -> off" is the forbidden fall-through moved one step earlier:
        # on a provisioned install it silently selects the principal's blank profile.
        self._reader(monkeypatch, exc=PermissionError(5, "access is denied"))
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            _REAL_MACHINE_SWITCH_ON()
        assert excinfo.value.reason is Reason.SWITCH_UNREADABLE
        assert MACHINE_SCOPE_KEY in str(excinfo.value)

    def test_generic_oserror_refuses_too(self, monkeypatch):
        self._reader(monkeypatch, exc=OSError(1359, "internal error"))
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            _REAL_MACHINE_SWITCH_ON()
        assert excinfo.value.reason is Reason.SWITCH_UNREADABLE

    def test_non_windows_never_reads_the_registry(self, monkeypatch):
        def _boom() -> tuple[object, int]:
            raise AssertionError("the registry must not be consulted off Windows")

        monkeypatch.setattr(paths_module, "_read_machine_switch_value", _boom)
        monkeypatch.setattr(paths_module.sys, "platform", "linux")
        assert _REAL_MACHINE_SWITCH_ON() is False

    @WINDOWS_ONLY
    def test_access_mask_and_value_type_match_winreg(self):
        # The two constants are spelled numerically because ``winreg`` does not import
        # off Windows. A 32-bit-view read would answer about WOW6432Node\DistrictSync —
        # a different key — so this parity test is what keeps the number honest.
        import winreg

        assert paths_module.MACHINE_SCOPE_KEY_ACCESS == winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        assert paths_module._REG_DWORD == winreg.REG_DWORD

    @WINDOWS_ONLY
    def test_the_real_registry_read_is_well_formed(self):
        # A real-syscall smoke (the new windows-latest CI leg is where this first runs):
        # it proves the OpenKey/QueryValueEx call marshals — a wrong argument order or a
        # bad access mask surfaces as TypeError / PermissionError, neither of which is an
        # accepted outcome here. The ANSWER is machine state and is deliberately NOT
        # asserted: no test may depend on how the runner's box is provisioned.
        try:
            value, value_type = paths_module._read_machine_switch_value()
        except FileNotFoundError:
            return  # the normal, un-provisioned state
        assert isinstance(value_type, int)
        assert value is not None


class TestMachineDirTrust:
    """Fails CLOSED: every unreadable or unexpected fact is a typed refusal."""

    def test_trusted_admin_owned_protected_dir_passes(self, machine_dir, security):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)
        paths_module._assert_machine_dir_trusted(machine_dir)  # no raise

    def test_system_owned_dir_passes(self, machine_dir, security):
        machine_dir.mkdir(parents=True)
        security("S-1-5-18", paths_module._SE_DACL_PROTECTED)
        paths_module._assert_machine_dir_trusted(machine_dir)  # no raise

    def test_missing_dir_refuses(self, machine_dir, security):
        security(*_TRUSTED_SECURITY)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.MISSING
        assert str(machine_dir) in str(excinfo.value)

    def test_a_file_refuses(self, machine_dir, security):
        machine_dir.parent.mkdir(parents=True)
        machine_dir.write_text("not a directory", encoding="utf-8")
        security(*_TRUSTED_SECURITY)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.NOT_A_DIRECTORY

    def test_reparse_point_refuses(self, machine_dir, security, monkeypatch):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)
        real_lstat = os.lstat

        def _fake_lstat(path):
            result = real_lstat(path)
            return SimpleNamespace(st_mode=result.st_mode, st_reparse_tag=0xA0000003)

        monkeypatch.setattr(paths_module.os, "lstat", _fake_lstat)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.REPARSE

    def test_foreign_owner_refuses(self, machine_dir, security):
        # A standard user can pre-create and OWN C:\ProgramData\DistrictSync (its default
        # ACL grants Users create-subdirectory + CREATOR OWNER full control), so ownership
        # is the fact that separates a provisioned profile from a planted one.
        machine_dir.mkdir(parents=True)
        security("S-1-5-21-1111111111-2222222222-3333333333-1001", paths_module._SE_DACL_PROTECTED)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.FOREIGN_OWNER

    def test_owner_is_compared_as_a_SID_string_not_a_name(self):
        # BUILTIN\Administrators is LOCALISED; a name comparison would fail on a German
        # or French Windows and pass on a machine that renamed a group to match.
        assert frozenset({"S-1-5-32-544", "S-1-5-18"}) == paths_module._TRUSTED_OWNER_SIDS
        assert all(sid.startswith("S-1-") for sid in paths_module._TRUSTED_OWNER_SIDS)

    def test_inherited_acl_refuses(self, machine_dir, security):
        # Inheritance still on => C:\ProgramData's Users:(OI)(CI)(RX) is live, and the
        # LocalMachine-sealed secret S-1a-ii writes there would be world-readable.
        machine_dir.mkdir(parents=True)
        security("S-1-5-32-544", 0x8004)  # SE_DACL_PRESENT, not SE_DACL_PROTECTED
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.INHERITED_ACL

    def test_unreadable_security_refuses_as_inaccessible(self, machine_dir, monkeypatch):
        machine_dir.mkdir(parents=True)

        def _boom(path):
            raise OSError(5, "access is denied")

        monkeypatch.setattr(paths_module, "_read_dir_security", _boom)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.INACCESSIBLE

    def test_unreadable_stat_refuses_as_inaccessible(self, machine_dir, security, monkeypatch):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)

        def _boom(path):
            raise PermissionError(5, "access is denied")

        monkeypatch.setattr(paths_module.os, "lstat", _boom)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.INACCESSIBLE

    def test_every_reason_is_reachable_and_spelled_once(self):
        # A bounded vocabulary S-1b's auto-grant screen branches on: string-matching
        # str(exc) is the fragility `setup_errors` exists to avoid.
        assert {r.value for r in Reason} == {
            "switch_unreadable",
            "missing",
            "not_a_directory",
            "reparse",
            "foreign_owner",
            "inherited_acl",
            "inaccessible",
            # S-1b-i: the open-group ace walk, and the WIN_PD_OVERRIDE_* redirect.
            "open_ace",
            "redirected",
        }


class TestOpenGroupAceWalk:
    """S-1b-i.1 — ``SE_DACL_PROTECTED`` proves inheritance was stripped, NOT that the
    resulting DACL is closed. The shared profile holds a LocalMachine-sealed delivery
    password whose only confidentiality boundary is this DACL."""

    @pytest.mark.parametrize("open_sid", sorted(paths_module.OPEN_GROUP_SIDS))
    def test_each_open_group_refuses(self, machine_dir, security, open_sid):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY, (*_TRUSTED_ACES, (0, open_sid)))
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module._assert_machine_dir_trusted(machine_dir)
        assert excinfo.value.reason is Reason.OPEN_ACE

    def test_a_correctly_acled_directory_passes(self, machine_dir, security):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY, _TRUSTED_ACES)
        paths_module._assert_machine_dir_trusted(machine_dir)  # no raise
        paths_module.assert_no_open_aces(machine_dir)  # no raise

    def test_a_DENY_ace_for_an_open_group_is_not_a_refusal(self, machine_dir, security):
        """A deny ace is MORE restrictive — refusing it would invert the check."""
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY, (*_TRUSTED_ACES, (1, "S-1-1-0")))
        paths_module.assert_no_open_aces(machine_dir)  # no raise

    def test_an_unreadable_dacl_fails_closed(self, machine_dir, security, monkeypatch):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)

        def _boom(path):
            raise OSError("the DACL could not be read")

        monkeypatch.setattr(paths_module, "_read_dacl_aces", _boom)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module.assert_no_open_aces(machine_dir)
        assert excinfo.value.reason is Reason.INACCESSIBLE

    def test_the_public_face_routes_through_the_private_seam(self, machine_dir, security, monkeypatch):
        """``assert_machine_dir_trusted`` DELEGATES, so the private name stays the ONE
        monkeypatch seam every other test (and two other test modules) already drive."""
        calls: list[Path] = []
        monkeypatch.setattr(paths_module, "_assert_machine_dir_trusted", lambda path: calls.append(path))
        paths_module.assert_machine_dir_trusted(machine_dir)
        assert calls == [machine_dir]


class TestMachineDirRefusesAPlatformdirsRedirect:
    """S-1b-i.2, MEASURED in the installed package: ``platformdirs`` 4.9.6 consults
    ``WIN_PD_OVERRIDE_*`` BEFORE ``SHGetKnownFolderPath`` (``windows.py:356-361``)."""

    @pytest.mark.parametrize("name", ["WIN_PD_OVERRIDE_COMMON_APPDATA", "WIN_PD_OVERRIDE_LOCAL_APPDATA"])
    def test_a_redirect_refuses(self, monkeypatch, tmp_path, name):
        monkeypatch.setenv(name, str(tmp_path / "planted"))
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module.machine_data_dir()
        assert excinfo.value.reason is Reason.REDIRECTED

    def test_without_one_it_resolves(self, monkeypatch):
        """The positive twin — the guard must not refuse every call."""
        for name in [n for n in os.environ if n.startswith("WIN_PD_OVERRIDE_")]:
            monkeypatch.delenv(name, raising=False)
        assert isinstance(paths_module.machine_data_dir(), Path)

    def test_a_blank_value_is_not_a_redirect(self, monkeypatch):
        """``FOO=`` in a shell is 'unset', exactly as ``_override_data_dir`` reads it."""
        monkeypatch.setenv("WIN_PD_OVERRIDE_COMMON_APPDATA", "   ")
        assert isinstance(paths_module.machine_data_dir(), Path)

    def test_the_real_platformdirs_still_honours_the_variable(self, monkeypatch, tmp_path):
        """Not vacuous: proves the threat is real in the INSTALLED version, so the guard
        above is protecting against something rather than restating a belief."""
        monkeypatch.setenv("WIN_PD_OVERRIDE_COMMON_APPDATA", str(tmp_path / "planted"))
        resolved = paths_module.platformdirs.site_data_dir("DistrictSync", appauthor=False)
        if sys.platform == "win32":
            assert str(tmp_path / "planted") in resolved
        else:
            pytest.skip("WIN_PD_OVERRIDE_* is a Windows-only platformdirs feature")


class TestResolutionLadder:
    """Switch on => the machine dir or a REFUSAL. Never the per-user profile."""

    def test_switch_on_and_trusted_returns_the_machine_dir(self, machine_dir, switch, security, user_scope_spy):
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)

        assert paths_module.user_data_dir() == machine_dir
        assert paths_module.is_machine_scope() is True
        assert user_scope_spy == []  # the per-user ladder never ran

    def test_switch_off_returns_todays_answer(self, data_dirs, switch, user_scope_spy):
        switch(False)
        assert paths_module.user_data_dir() == data_dirs.new
        assert paths_module.is_machine_scope() is False
        assert user_scope_spy == [True]

    @pytest.mark.parametrize(
        ("state", "reason"),
        [
            ("missing", Reason.MISSING),
            ("file", Reason.NOT_A_DIRECTORY),
            ("foreign", Reason.FOREIGN_OWNER),
            ("inherited", Reason.INHERITED_ACL),
            ("unreadable", Reason.INACCESSIBLE),
        ],
    )
    def test_every_refusal_reason_stops_the_ladder_dead(
        self, machine_dir, switch, security, user_scope_spy, monkeypatch, data_dirs, state, reason
    ):
        switch(True)
        if state == "missing":
            security(*_TRUSTED_SECURITY)
        elif state == "file":
            machine_dir.parent.mkdir(parents=True)
            machine_dir.write_text("x", encoding="utf-8")
            security(*_TRUSTED_SECURITY)
        elif state == "foreign":
            machine_dir.mkdir(parents=True)
            security("S-1-5-21-1-2-3-1001", paths_module._SE_DACL_PROTECTED)
        elif state == "inherited":
            machine_dir.mkdir(parents=True)
            security("S-1-5-32-544", 0x8004)
        else:
            machine_dir.mkdir(parents=True)
            monkeypatch.setattr(paths_module, "_read_dir_security", lambda p: (_ for _ in ()).throw(OSError("denied")))

        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module.user_data_dir()

        assert excinfo.value.reason is reason
        # No fall-through, proven three ways: the per-user resolver never ran, the
        # per-user dir was not created, and nothing was cached for the next caller.
        assert user_scope_spy == []
        assert not data_dirs.new.exists()
        assert paths_module._PIN is None

    def test_switch_unreadable_stops_the_ladder_dead(self, switch, user_scope_spy, monkeypatch, data_dirs):
        def _refuse() -> bool:
            raise paths_module.MachineScopeRefused(Reason.SWITCH_UNREADABLE, MACHINE_SCOPE_KEY)

        monkeypatch.setattr(paths_module, "_machine_switch_on", _refuse)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module.user_data_dir()
        assert excinfo.value.reason is Reason.SWITCH_UNREADABLE
        assert user_scope_spy == []
        assert not data_dirs.new.exists()

    def test_a_refusal_does_not_poison_the_pin(self, machine_dir, switch, security):
        switch(True)
        security(*_TRUSTED_SECURITY)
        with pytest.raises(paths_module.MachineScopeRefused):
            paths_module.user_data_dir()
        # The positive twin: once the folder is there, the very next call succeeds —
        # so the refusal was a decision, not a latched dead state.
        machine_dir.mkdir(parents=True)
        assert paths_module.user_data_dir() == machine_dir

    def test_override_wins_over_the_switch_and_is_never_machine_scope(
        self, tmp_path, monkeypatch, machine_dir, security, data_dirs
    ):
        # Step 0 stays absolute. The override is a support/test seam pointed at an
        # un-ACL'd throwaway dir, so it must never select the shared profile (and, in
        # S-1a-ii, never the LocalMachine secret store).
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)
        consulted: list[bool] = []

        def _switch() -> bool:
            consulted.append(True)
            return True

        monkeypatch.setattr(paths_module, "_machine_switch_on", _switch)
        override = tmp_path / "override-profile"
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))

        assert paths_module.user_data_dir() == override.resolve()
        assert paths_module.is_machine_scope() is False
        assert consulted == []  # the switch is not even read

        # Positive twin: with the override gone, the same switch IS consulted.
        monkeypatch.delenv(paths_module._DATA_DIR_ENV_VAR)
        paths_module.reset_data_dir_pin()
        assert paths_module.user_data_dir() == machine_dir
        assert consulted == [True]

    def test_non_windows_is_never_machine_scope(self, monkeypatch, data_dirs, machine_dir, security):
        machine_dir.mkdir(parents=True)
        security(*_TRUSTED_SECURITY)
        monkeypatch.setattr(paths_module.sys, "platform", "linux")
        monkeypatch.setattr(paths_module, "_read_machine_switch_value", lambda: (1, paths_module._REG_DWORD))
        assert paths_module.is_machine_scope() is False
        assert paths_module.user_data_dir() == data_dirs.new


class TestThePin:
    def test_resolves_once_per_process(self, data_dirs, monkeypatch):
        reads: list[int] = []

        def _switch() -> bool:
            reads.append(1)
            return False

        monkeypatch.setattr(paths_module, "_machine_switch_on", _switch)
        for _ in range(3):
            paths_module.user_data_dir()
            paths_module.is_machine_scope()
        assert reads == [1]

    def test_reset_re_resolves(self, data_dirs, monkeypatch):
        reads: list[int] = []

        def _switch() -> bool:
            reads.append(1)
            return False

        monkeypatch.setattr(paths_module, "_machine_switch_on", _switch)
        paths_module.user_data_dir()
        paths_module.reset_data_dir_pin()
        paths_module.user_data_dir()
        assert reads == [1, 1]

    def test_path_and_scope_are_decided_together_in_either_call_order(self, machine_dir, switch, security, data_dirs):
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        # Scope asked FIRST — the path must still be the machine dir (one decision).
        assert paths_module.is_machine_scope() is True
        assert paths_module.user_data_dir() == machine_dir

    def test_the_answer_cannot_change_mid_run(self, data_dirs, machine_dir, switch, security):
        switch(False)
        first = paths_module.user_data_dir()
        assert first == data_dirs.new
        # The world changes underneath a running process; the pin does not.
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        assert paths_module.user_data_dir() == first
        assert paths_module.is_machine_scope() is False

    def test_pin_data_dir_logs_the_dir_and_the_scope(self, data_dirs, switch, caplog):
        switch(False)
        with caplog.at_level(logging.INFO, logger="src.utils.paths"):
            resolved = paths_module.pin_data_dir()
        assert resolved == data_dirs.new
        line = " ".join(r.getMessage() for r in caplog.records)
        assert str(data_dirs.new) in line
        assert "machine scope: no" in line

    def test_pin_data_dir_says_yes_on_a_machine_scoped_install(self, machine_dir, switch, security, caplog):
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        with caplog.at_level(logging.INFO, logger="src.utils.paths"):
            assert paths_module.pin_data_dir() == machine_dir
        assert "machine scope: yes" in " ".join(r.getMessage() for r in caplog.records)

    def test_pin_data_dir_propagates_the_typed_refusal(self, machine_dir, switch, security):
        switch(True)
        security(*_TRUSTED_SECURITY)
        with pytest.raises(paths_module.MachineScopeRefused) as excinfo:
            paths_module.pin_data_dir()
        assert excinfo.value.reason is Reason.MISSING


class TestHandshakeDir:
    """The elevation handshake stays PER-USER and NON-CREATING, in every scope."""

    def test_never_the_machine_dir(self, machine_dir, switch, security, data_dirs):
        machine_dir.mkdir(parents=True)
        data_dirs.new.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)

        assert paths_module.user_data_dir() == machine_dir  # scope really is machine
        assert paths_module.handshake_dir() == data_dirs.new
        assert paths_module.handshake_dir() != machine_dir

    def test_equals_the_profile_when_the_switch_is_off(self, data_dirs, switch):
        data_dirs.new.mkdir(parents=True)
        switch(False)
        assert paths_module.handshake_dir() == paths_module.user_data_dir()

    def test_follows_the_override(self, tmp_path, monkeypatch, data_dirs):
        override = tmp_path / "override-profile"
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))
        assert paths_module.handshake_dir() == override.resolve()

    def test_creates_nothing(self, data_dirs, switch):
        # sweep_orphans() runs unconditionally at BOTH entry points, so a creating
        # resolver would have every nightly as the service principal materialise an
        # empty second profile — the split-brain D0 exists to forbid.
        switch(False)
        assert not data_dirs.new.exists()
        assert paths_module.handshake_dir() == data_dirs.new
        assert not data_dirs.new.exists()
        # Positive twin: the profile resolver on the same state DOES create it.
        assert paths_module.user_data_dir() == data_dirs.new
        assert data_dirs.new.exists()

    def test_prefers_an_existing_legacy_profile(self, data_dirs, switch):
        switch(False)
        data_dirs.legacy.mkdir(parents=True)
        assert paths_module.handshake_dir() == data_dirs.legacy


class TestScopedFileNames:
    def test_per_user_names_are_unchanged(self, data_dirs, switch):
        switch(False)
        assert paths_module.user_log_file() == data_dirs.new / "etl_tool.log"
        assert paths_module.user_history_db() == data_dirs.new / "history.db"

    def test_machine_scope_splits_the_log_per_writer_under_runs(self, machine_dir, switch, security, monkeypatch):
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        monkeypatch.setattr(paths_module, "process_account", lambda: "CORP\\svc$")

        log = paths_module.user_log_file()
        assert log == machine_dir / "runs" / "etl_tool-corp_svc.log"
        assert paths_module.user_history_db() == machine_dir / "runs" / "history.db"

    def test_machine_scope_log_name_is_filename_safe(self, machine_dir, switch, security, monkeypatch):
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        monkeypatch.setattr(paths_module, "process_account", lambda: "")
        assert paths_module.user_log_file() == machine_dir / "runs" / "etl_tool-unknown.log"

    def test_mappings_and_known_hosts_stay_at_the_root(self, machine_dir, switch, security):
        # A self-service district's overlay (plan 0044) must reach the nightly.
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        assert paths_module.user_known_hosts_file() == machine_dir / "known_hosts"
        assert paths_module.user_mappings_dir() == machine_dir / "mappings"


class TestMigrationIsUntouchedByTheSwitch:
    def test_legacy_still_lands_in_the_per_user_platform_dir(self, data_dirs, machine_dir, switch, security):
        # The positive twin for "migrate_legacy_data_dir() is untouched": with the switch
        # ON, a legacy profile must still migrate to %LOCALAPPDATA%, never into the
        # shared dir (it is a per-user relocation and the machine dir is provisioned).
        machine_dir.mkdir(parents=True)
        switch(True)
        security(*_TRUSTED_SECURITY)
        TestMigrateLegacyDataDir._seed_legacy(data_dirs.legacy)

        assert paths_module.migrate_legacy_data_dir() is True
        assert (data_dirs.new / "config.json").exists()
        assert not (machine_dir / "config.json").exists()


class TestNothingInTheFieldCanTurnTheSwitchOn:
    """AC1's MECHANICAL twin — the claim is a fact, not a promise.

    **S-1b-i MOVED this deliberately.** S-1a could assert that NO registry write API
    existed anywhere under ``src/``; S-1b is the slice that adds the writer, so that
    assertion is now false by design and keeping it would only mean never shipping the
    commit point. The replacement is the narrowest pin that still carries AC1's weight:

    * exactly ONE module under ``src/`` may hold a registry write API, and it is the
      elevated provisioning engine (never a UI module, never the path resolver, never a
      module an unelevated code path imports for something else);
    * that writer opens the key through ``paths.MACHINE_SCOPE_KEY_ACCESS`` rather than
      re-spelling an access mask — a writer in the redirected 32-bit view would commit
      ``WOW6432Node\\DistrictSync``, report success, and leave the app per-user with the
      config and the secret already copied;
    * the scanner still has teeth (the planted-file twin below is unchanged).

    What is no longer mechanically pinned, and is now carried by the ops being unreachable
    instead: that the switch cannot be turned on in the FIELD. Nothing in the app calls the
    three ops until S-2 wires Schedule-time dispatch.
    """

    _WRITE_APIS = ("SetValueEx", "CreateKey", "KEY_WRITE", "KEY_ALL_ACCESS", "DeleteValue")

    # The ONE module allowed to hold them.
    _WRITER = Path("src") / "scheduler" / "provisioning.py"

    @staticmethod
    def _scan(files: list[Path]) -> list[str]:
        hits: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for api in TestNothingInTheFieldCanTurnTheSwitchOn._WRITE_APIS:
                    if api in line:
                        hits.append(f"{path}:{lineno}: {api}")
        return hits

    @staticmethod
    def _src_files() -> list[Path]:
        src_root = Path(__file__).resolve().parents[1] / "src"
        return sorted(p for p in src_root.rglob("*.py") if "__pycache__" not in p.parts)

    def test_the_only_registry_writer_under_src_is_the_elevated_provision_op(self):
        files = self._src_files()
        assert files, "the scan must actually see the source tree"
        repo_root = Path(__file__).resolve().parents[1]
        others = [p for p in files if p.relative_to(repo_root) != self._WRITER]
        assert self._scan(others) == []

    def test_that_writer_really_does_hold_one(self):
        """Not vacuous: an allowance for a module with no writer in it proves nothing."""
        repo_root = Path(__file__).resolve().parents[1]
        assert self._scan([repo_root / self._WRITER])

    def test_the_writer_opens_the_key_through_the_exported_access_mask(self):
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / self._WRITER).read_text(encoding="utf-8")
        assert "paths.MACHINE_SCOPE_KEY_ACCESS | winreg.KEY_WRITE" in source
        assert "winreg.KEY_WOW64_32KEY" not in source
        # The key PATH is the exported constant too — never a second spelling.
        assert "paths.MACHINE_SCOPE_KEY_PATH" in source
        assert r'"SOFTWARE\DistrictSync"' not in source

    def test_the_scanner_has_teeth(self, tmp_path):
        # The positive twin: the same scan FINDS a write, so the green above is a fact.
        planted = tmp_path / "writer.py"
        planted.write_text(
            "import winreg\n"
            "key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\\\\DistrictSync')\n"
            "winreg.SetValueEx(key, 'MachineScope', 0, winreg.REG_DWORD, 1)\n",
            encoding="utf-8",
        )
        hits = self._scan([planted])
        assert any("SetValueEx" in hit for hit in hits)
        assert any("CreateKey" in hit for hit in hits)
