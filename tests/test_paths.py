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
    """
    new = tmp_path / "platform" / "DistrictSync"
    legacy = tmp_path / "home" / ".districtsync"
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
