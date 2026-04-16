"""Tests for src/utils/paths.py — path resolution helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.utils import paths as paths_module


@pytest.fixture
def redirect_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a tmp dir so tests never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestUserDataDir:
    def test_creates_dir_on_access(self, redirect_home):
        d = paths_module.user_data_dir()
        assert d == redirect_home / ".gde2acsv"
        assert d.exists() and d.is_dir()

    def test_idempotent(self, redirect_home):
        d1 = paths_module.user_data_dir()
        d2 = paths_module.user_data_dir()
        assert d1 == d2


class TestUserMappingsDir:
    def test_nested_under_user_data(self, redirect_home):
        d = paths_module.user_mappings_dir()
        assert d == redirect_home / ".gde2acsv" / "mappings"
        assert d.exists() and d.is_dir()


class TestUserLogFile:
    def test_path_under_user_data(self, redirect_home):
        p = paths_module.user_log_file()
        assert p == redirect_home / ".gde2acsv" / "etl_tool.log"


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
