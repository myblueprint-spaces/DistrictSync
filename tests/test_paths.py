"""Tests for src/utils/paths.py — path resolution helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.utils import paths as paths_module

# test_paths.py is the guard for the real path helpers, so opt out of the autouse
# user_data_dir isolation (redirect Path.home instead) — otherwise these tests
# would assert against the isolation fixture's fake, not the real implementation.
pytestmark = pytest.mark.real_user_data_dir


@pytest.fixture
def redirect_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a tmp dir so tests never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestUserDataDir:
    def test_creates_dir_on_access(self, redirect_home):
        d = paths_module.user_data_dir()
        assert d == redirect_home / ".districtsync"
        assert d.exists() and d.is_dir()

    def test_idempotent(self, redirect_home):
        d1 = paths_module.user_data_dir()
        d2 = paths_module.user_data_dir()
        assert d1 == d2


class TestUserMappingsDir:
    def test_nested_under_user_data(self, redirect_home):
        d = paths_module.user_mappings_dir()
        assert d == redirect_home / ".districtsync" / "mappings"
        assert d.exists() and d.is_dir()


class TestUserLogFile:
    def test_path_under_user_data(self, redirect_home):
        p = paths_module.user_log_file()
        assert p == redirect_home / ".districtsync" / "etl_tool.log"


class TestUserHistoryDb:
    def test_path_under_user_data(self, redirect_home):
        p = paths_module.user_history_db()
        assert p == redirect_home / ".districtsync" / "history.db"

    def test_resolves_through_user_data_dir_at_call_time(self, monkeypatch, tmp_path):
        # The store must resolve its path through the single seam at call time (not a
        # module constant) so the test-isolation fixture redirects it too.
        target = tmp_path / "isolated" / ".districtsync"
        monkeypatch.setattr(paths_module, "user_data_dir", lambda: target)
        assert paths_module.user_history_db() == target / "history.db"


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
