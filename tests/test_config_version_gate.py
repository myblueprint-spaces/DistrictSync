"""Tests for the config version gate + shadow logging in src/config/loader.py.

Covers:
- in-range versions (major == SUPPORTED_CONFIG_MAJOR, minor <= supported) load silently
- a different major (older OR newer) raises an actionable ValueError
- same-major newer-minor drift loads but WARNS, naming the path + both versions
- an unreadable version raises an actionable ValueError
- the version gate applies to the RESOLVED config (a _base-inherited version counts)
- user-dir files shadowing bundled configs are named in an INFO log line
- all 12 bundled configs still load clean (no version warnings, no rejects)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
import yaml

from src.config.loader import (
    SUPPORTED_CONFIG_MAJOR,
    SUPPORTED_CONFIG_MINOR,
    _parse_version,
    load_config,
)
from src.utils.paths import user_mappings_dir

#: The shipped configs, read straight off disk (not via ``bundle_mappings_dir()``,
#: whose frozen-exe branch other tests monkeypatch).
BUNDLED_MAPPINGS_DIR = Path(__file__).resolve().parents[1] / "config" / "mappings"

ALL_BUNDLED_CONFIGS = [
    "myedbc",
    "sd40myedbc",
    "sd48myedbc",
    "sd51myedbc",
    "sd54myedbc",
    "sd60myedbc",
    "sd74myedbc",
    "sd83myedbc",
    "mbp_all",
    "mbp_core",
    "mbponly",
    "sd51attendance",
]

LOADER_LOGGER = "src.config.loader"


@pytest.fixture
def user_mappings(monkeypatch) -> Path:
    """Isolated user mappings dir (via the autouse user_data_dir redirect).

    Also defensively clears any leaked sys.frozen / sys._MEIPASS from prior
    tests so bundle_root() correctly resolves to the project root in dev.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return user_mappings_dir()


def _write_user_config(user_dir: Path, sis_type: str, version: object) -> Path:
    """Write a minimal _base:myedbc user config declaring ``version``."""
    data: dict = {
        "_base": "myedbc",
        "sis": "MyEducationBC",
        "district_name": f"Version gate test ({version!r})",
    }
    if version is not None:
        data["version"] = version
    path = user_dir / f"{sis_type}_mapping.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _version_records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOADER_LOGGER and r.levelno >= level]


class TestInRangeSilent:
    @pytest.mark.parametrize("version", ["1.0", f"{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}", "1"])
    def test_in_range_version_loads_silently(self, user_mappings, caplog, version):
        _write_user_config(user_mappings, "vgate_inrange", version)
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            cfg = load_config("vgate_inrange")
        assert cfg.district_name.startswith("Version gate test")
        assert _version_records(caplog, logging.WARNING) == []

    def test_lower_minor_than_supported_is_silent(self, user_mappings, caplog):
        # Bundled configs span 1.0-1.9 — older minors within the major stay clean.
        _write_user_config(user_mappings, "vgate_oldminor", "1.2")
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            load_config("vgate_oldminor")
        assert _version_records(caplog, logging.WARNING) == []


class TestDifferentMajorRejected:
    def test_older_major_raises_actionable_valueerror(self, user_mappings):
        path = _write_user_config(user_mappings, "vgate_oldmajor", "0.9")
        with pytest.raises(ValueError, match="major") as excinfo:
            load_config("vgate_oldmajor")
        msg = str(excinfo.value)
        assert str(path) in msg
        assert "0.9" in msg
        assert f"{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}" in msg
        assert "DistrictSync team" in msg  # actionable remedy

    def test_newer_major_raises_actionable_valueerror(self, user_mappings):
        _write_user_config(user_mappings, "vgate_newmajor", "2.0")
        with pytest.raises(ValueError, match="cannot drive a conversion"):
            load_config("vgate_newmajor")

    def test_rejected_config_never_validates(self, user_mappings):
        # The gate fires BEFORE Pydantic — even a schema-broken future config
        # gets the version message, not confusing field errors.
        path = user_mappings / "vgate_broken_mapping.yaml"
        path.write_text(
            yaml.safe_dump({"version": "3.0", "sis": "X", "totally_unknown_shape": True}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="major version"):
            load_config("vgate_broken")


class TestMinorDriftWarns:
    def test_newer_minor_loads_with_warning(self, user_mappings, caplog):
        newer_minor = f"{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR + 33}"
        path = _write_user_config(user_mappings, "vgate_drift", newer_minor)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            cfg = load_config("vgate_drift")
        assert cfg.district_name.startswith("Version gate test")
        warnings = _version_records(caplog, logging.WARNING)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert str(path) in message
        assert newer_minor in message
        assert f"{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}" in message


class TestUnreadableVersionRejected:
    @pytest.mark.parametrize("version", ["abc", "v1.0", "1.x", ""])
    def test_garbage_version_raises(self, user_mappings, version):
        _write_user_config(user_mappings, "vgate_garbage", version)
        with pytest.raises(ValueError, match="unreadable version"):
            load_config("vgate_garbage")


class TestBareScalarVersionRejected:
    """PyYAML collapses a bare-float ``version: 1.10`` to the float 1.1 — minor 10
    silently reads as minor 1 and the newer-minor WARNING never fires. The gate
    therefore requires the STRING form; a bare scalar fails loud at the source."""

    @pytest.mark.parametrize("version", [1.0, 1.9, 1.1, 1])
    def test_bare_scalar_version_rejected_with_quoting_remedy(self, user_mappings, version):
        _write_user_config(user_mappings, "vgate_barefloat", version)
        with pytest.raises(ValueError, match="bare YAML scalar") as excinfo:
            load_config("vgate_barefloat")
        assert "quote it as a string" in str(excinfo.value)

    def test_quoted_minor_ten_parses_as_minor_ten_not_one(self):
        """'1.10' must read as minor TEN — the exact fact the float form swallows.

        Asserted on the parser rather than on the warning, because the warning
        depends on where SUPPORTED_CONFIG_MINOR happens to sit (it reached 10
        with `class_rostering_grades`, so '1.10' is now IN range and correctly
        silent). The trailing-zero fact this class exists for is version-gate
        independent, so it is now pinned that way.
        """
        assert _parse_version("1.10", Path("vgate_minorten_mapping.yaml")) == (1, 10)
        # …and the float form the quoting rule exists to forbid really does lose it.
        assert float("1.10") == 1.1

    def test_quoted_two_digit_minor_above_supported_fires_the_newer_minor_warning(self, user_mappings, caplog):
        """A TWO-DIGIT minor above the supported one still warns (bump-proof).

        Symbolic in SUPPORTED_CONFIG_MINOR so a future bump cannot silently turn
        this row green-by-inclusion, while staying two-digit — the shape whose
        bare-float spelling would collapse.
        """
        newer = f"{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR + 10}"
        _write_user_config(user_mappings, "vgate_minortwodigit", newer)
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            load_config("vgate_minortwodigit")
        warnings = _version_records(caplog, logging.WARNING)
        assert len(warnings) == 1
        assert newer in warnings[0].getMessage()


class TestSD83DeclaresTheClassRosteringMinor:
    """SD83 is the one bundled config that opts into `class_rostering_grades`.

    Its declared version must be the QUOTED string '1.10' — the value the bare
    YAML float would collapse to 1.1 — and it must load with no version warning
    (pinned in aggregate by `test_all_bundled_configs_load_clean` too).
    """

    def test_sd83_version_is_the_quoted_string_one_ten(self):
        raw = yaml.safe_load((BUNDLED_MAPPINGS_DIR / "sd83myedbc_mapping.yaml").read_text(encoding="utf-8"))
        assert isinstance(raw["version"], str), "a bare YAML float would read as 1.1"
        assert raw["version"] == "1.10"
        assert _parse_version(raw["version"], Path("sd83myedbc_mapping.yaml")) == (1, 10)

    def test_sd83_opts_into_class_rostering_grades(self):
        """Positive pin: `GlobalConfig` does not forbid extras, so a typo'd key
        would be silently dropped and SD83 would roster grades 9-12 anyway."""
        assert load_config("sd83myedbc").global_config.class_rostering_grades == "homeroom"


class TestInheritedVersion:
    def test_version_inherited_from_base_is_gated_in_range(self, user_mappings, caplog):
        # No own version → resolves to the bundled myedbc version (in range) → silent.
        _write_user_config(user_mappings, "vgate_inherit", None)
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            cfg = load_config("vgate_inherit")
        assert cfg.district_name.startswith("Version gate test")
        assert _version_records(caplog, logging.WARNING) == []

    def test_override_to_bad_major_rejected_despite_good_base(self, user_mappings):
        _write_user_config(user_mappings, "vgate_badchild", "0.1")
        with pytest.raises(ValueError, match="major"):
            load_config("vgate_badchild")


class TestShadowLogging:
    def test_user_dir_shadowing_bundled_logs_info_naming_both_paths(self, user_mappings, caplog):
        user_path = _write_user_config(user_mappings, "sd40myedbc", "1.0")
        with caplog.at_level(logging.INFO, logger=LOADER_LOGGER):
            load_config("sd40myedbc")
        shadow_lines = [
            r.getMessage() for r in caplog.records if r.name == LOADER_LOGGER and "shadows" in r.getMessage()
        ]
        assert len(shadow_lines) >= 1
        assert str(user_path) in shadow_lines[0]
        assert "sd40myedbc_mapping.yaml" in shadow_lines[0]
        # Both tiers named: the winning user path and the hidden bundled path differ.
        assert shadow_lines[0].count("sd40myedbc_mapping.yaml") >= 2

    def test_user_only_config_logs_no_shadow_line(self, user_mappings, caplog):
        _write_user_config(user_mappings, "vgate_noshadow", "1.0")
        with caplog.at_level(logging.INFO, logger=LOADER_LOGGER):
            load_config("vgate_noshadow")
        assert all("shadows" not in r.getMessage() for r in caplog.records if r.name == LOADER_LOGGER)


class TestBundledConfigsStayClean:
    @pytest.mark.parametrize("sis_type", ALL_BUNDLED_CONFIGS)
    def test_bundled_config_loads_without_version_noise(self, user_mappings, caplog, sis_type):
        with caplog.at_level(logging.INFO, logger=LOADER_LOGGER):
            cfg = load_config(sis_type)
        assert cfg.mappings  # validated + non-empty
        assert _version_records(caplog, logging.WARNING) == []
        assert all("shadows" not in r.getMessage() for r in caplog.records if r.name == LOADER_LOGGER)
