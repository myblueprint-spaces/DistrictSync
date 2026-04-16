"""Tests for two-tier mapping discovery in src/config/loader.py.

Covers:
- user-dir override of a built-in (same SIS identifier in user dir wins)
- user-dir config inheriting from a bundled base via _base
- available_configs() deduplicates and lists the union
- missing config raises FileNotFoundError citing all search dirs
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from src.config.loader import available_configs, load_config

MINIMAL_MYEDBC_OVERRIDE = {
    "_base": "myedbc",
    "version": "1.0",
    "sis": "MyEducationBC",
    "district_name": "Test District (user override)",
    "mappings": {
        "Students": {
            "source_files": {"student_demographic": "StudentDemographicInformation.txt"},
            "field_map": {
                "Email Address": {"format": "{student number}@testdistrict.example.ca"},
            },
        },
    },
}

STANDALONE_CUSTOM = {
    "_base": "myedbc",
    "version": "1.0",
    "sis": "MyEducationBC",
    "district_name": "My Custom District",
    "mappings": {
        "Students": {
            "source_files": {"student_demographic": "StudentDemographicInformation.txt"},
            "field_map": {},
        },
    },
}


@pytest.fixture
def redirect_home(tmp_path, monkeypatch):
    """Redirect Path.home() so user_mappings_dir() points at a tmp dir.

    This isolates the test from the developer's real ~/.districtsync/mappings/
    so it doesn't pick up stray files. Also defensively clears any
    leaked sys.frozen / sys._MEIPASS from prior tests so bundle_root()
    correctly resolves to the project root in dev.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return tmp_path / ".districtsync" / "mappings"


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


class TestUserOverridesBuiltIn:
    def test_same_identifier_user_dir_wins(self, redirect_home):
        # Built-in sd40myedbc ships with the bundle; user shadows it.
        user_override = redirect_home / "sd40myedbc_mapping.yaml"
        _write_yaml(user_override, {**MINIMAL_MYEDBC_OVERRIDE, "district_name": "SD40 custom"})

        cfg = load_config("sd40myedbc")
        assert cfg.district_name == "SD40 custom"

    def test_without_override_bundle_wins(self, redirect_home):
        # No user override → bundled config loads as before.
        cfg = load_config("sd40myedbc")
        assert cfg.district_name == "SD40 - New Westminster School District"


class TestUserInheritsFromBundleBase:
    def test_custom_district_inherits_myedbc_base(self, redirect_home):
        custom = redirect_home / "mydistrict_mapping.yaml"
        _write_yaml(custom, {**STANDALONE_CUSTOM, "_base": "myedbc"})

        # _base "myedbc" lives in the bundle, not in the user dir.
        cfg = load_config("mydistrict")
        # Inherited field_map from myedbc still present
        assert "Students" in cfg.mappings
        assert cfg.district_name == "My Custom District"


class TestAvailableConfigs:
    def test_union_of_user_and_bundle(self, redirect_home):
        custom = redirect_home / "mynew_mapping.yaml"
        _write_yaml(custom, STANDALONE_CUSTOM)

        ids = available_configs()
        # Built-ins still there
        assert "myedbc" in ids
        assert "sd40myedbc" in ids
        # User addition surfaced
        assert "mynew" in ids

    def test_no_duplicates_on_override(self, redirect_home):
        shadow = redirect_home / "sd40myedbc_mapping.yaml"
        _write_yaml(shadow, MINIMAL_MYEDBC_OVERRIDE)

        ids = available_configs()
        assert ids.count("sd40myedbc") == 1


class TestMissingConfig:
    def test_raises_with_search_path_hint(self, redirect_home):
        with pytest.raises(FileNotFoundError, match="not found in any of"):
            load_config("nonexistent_sis")
