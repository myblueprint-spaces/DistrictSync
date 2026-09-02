"""Tests for the loader's ORIGIN seam, the user-dir domains floor, and overlay validation.

Plan 0044 slice 1 (the loader half). Three concerns, all in
``src/config/loader.py``:

- :func:`resolve_config_path` — the public ``(path, origin)`` seam. Origin is
  SAFETY-RELEVANT (it decides whether the user-dir ``district_domains`` floor
  applies), so every case here is proved through a TWO-dir seam whose first
  element is the user tier; a one-dir seam is refused precisely because it would
  make these assertions vacuous (plan review #9).
- the user-dir ``district_domains`` floor — WARN-and-drop for a hand-editable
  user file (counts only, never the value), while a bundled config keeps the
  model validator's loud raise.
- :func:`validate_overlay` — the authoring layer's load-back check, which must
  validate a dict that has NO file yet and create nothing on disk.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
import yaml

from src.config.loader import (
    ResolvedConfigPath,
    load_config,
    resolve_config_path,
    validate_overlay,
)
from src.utils.paths import bundle_mappings_dir, user_mappings_dir

LOADER_LOGGER = "src.config.loader"

#: A synthetic bad row. NOT an email address on purpose in the shared helper — the
#: pasted-address case gets its own no-echo test below.
BAD_DOMAIN = "SD93.BC.CA"
GOOD_DOMAIN = "sd93.bc.ca"


@pytest.fixture
def user_mappings(monkeypatch) -> Path:
    """Isolated user mappings dir (via the autouse user_data_dir redirect).

    Also defensively clears any leaked ``sys.frozen`` / ``sys._MEIPASS`` from prior
    tests so ``bundle_root()`` resolves to the project root in dev.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return user_mappings_dir()


def _write_config(directory: Path, sis_type: str, **extra) -> Path:
    """Write a minimal ``_base: myedbc`` config, plus any extra root keys."""
    data: dict = {
        "_base": "myedbc",
        "sis": "MyEducationBC",
        "district_name": f"Origin test ({sis_type})",
        **extra,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sis_type}_mapping.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _loader_records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOADER_LOGGER and r.levelno >= level]


# --------------------------------------------------------------------------- #
# resolve_config_path — origin, through the two-dir seam                      #
# --------------------------------------------------------------------------- #
class TestResolveConfigPathOrigin:
    @pytest.fixture
    def pair(self, tmp_path) -> list[Path]:
        """A two-dir seam: index 0 = user tier, index 1 = bundled tier."""
        user = tmp_path / "userdir"
        bundled = tmp_path / "bundledir"
        user.mkdir()
        bundled.mkdir()
        return [user, bundled]

    def test_user_dir_only_reports_user(self, pair):
        path = _write_config(pair[0], "origin_useronly")
        resolved = resolve_config_path("origin_useronly", search_dirs=pair)
        assert resolved == ResolvedConfigPath(path, "user")

    def test_bundled_dir_only_reports_bundled(self, pair):
        path = _write_config(pair[1], "origin_bundledonly")
        resolved = resolve_config_path("origin_bundledonly", search_dirs=pair)
        assert resolved == ResolvedConfigPath(path, "bundled")

    def test_present_in_both_reports_user_and_still_logs_the_shadow_line(self, pair, caplog):
        user_path = _write_config(pair[0], "origin_both")
        bundled_path = _write_config(pair[1], "origin_both")
        with caplog.at_level(logging.INFO, logger=LOADER_LOGGER):
            resolved = resolve_config_path("origin_both", search_dirs=pair)
        assert resolved == ResolvedConfigPath(user_path, "user")
        shadow_lines = [r.getMessage() for r in _loader_records(caplog, logging.INFO) if "shadows" in r.getMessage()]
        assert len(shadow_lines) == 1
        assert str(user_path) in shadow_lines[0]
        assert str(bundled_path) in shadow_lines[0]

    def test_absent_returns_none_and_does_not_raise(self, pair):
        assert resolve_config_path("origin_nowhere", search_dirs=pair) is None

    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_a_search_dirs_sequence_that_is_not_a_pair_is_refused(self, tmp_path, count):
        """A one-dir seam cannot EXPRESS an origin — defaulting it would be the vacuous green."""
        dirs = [tmp_path / f"d{i}" for i in range(count)]
        for d in dirs:
            d.mkdir()
        with pytest.raises(ValueError, match="exactly 2 directories"):
            resolve_config_path("origin_useronly", search_dirs=dirs)

    def test_the_pair_contract_is_what_makes_these_assertions_non_vacuous(self, pair):
        """Falsification twin: the SAME id resolves to a DIFFERENT origin per tier."""
        _write_config(pair[0], "origin_tierA")
        _write_config(pair[1], "origin_tierB")
        assert resolve_config_path("origin_tierA", search_dirs=pair).origin == "user"
        assert resolve_config_path("origin_tierB", search_dirs=pair).origin == "bundled"


class TestResolveConfigPathRealDirs:
    def test_no_search_dirs_uses_the_real_user_dir(self, user_mappings):
        path = _write_config(user_mappings, "origin_realuser")
        resolved = resolve_config_path("origin_realuser")
        assert resolved == ResolvedConfigPath(path, "user")

    def test_no_search_dirs_finds_a_shipped_config_in_the_bundle(self, user_mappings):
        resolved = resolve_config_path("myedbc")
        assert resolved is not None
        assert resolved.origin == "bundled"
        assert resolved.path.parent == bundle_mappings_dir()

    def test_missing_everywhere_is_none(self, user_mappings):
        assert resolve_config_path("origin_no_such_district") is None


# --------------------------------------------------------------------------- #
# load_config acts on the SAME origin the seam reports                        #
# --------------------------------------------------------------------------- #
class TestLoadConfigUsesTheSeam:
    def test_load_config_and_resolve_config_path_agree_on_the_winning_file(self, user_mappings):
        user_path = _write_config(user_mappings, "sd40myedbc")
        resolved = resolve_config_path("sd40myedbc")
        cfg = load_config("sd40myedbc")
        assert resolved == ResolvedConfigPath(user_path, "user")
        assert cfg.district_name == "Origin test (sd40myedbc)"


# --------------------------------------------------------------------------- #
# The user-dir district_domains floor                                         #
# --------------------------------------------------------------------------- #
class TestUserDirDomainsFloor:
    def test_a_bad_row_is_dropped_and_the_good_one_kept(self, user_mappings, caplog):
        _write_config(user_mappings, "floor_mixed", district_domains=[GOOD_DOMAIN, BAD_DOMAIN])
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            cfg = load_config("floor_mixed")
        assert cfg.district_domains == [GOOD_DOMAIN]

        warnings = _loader_records(caplog, logging.WARNING)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "dropped 1 of 2" in message
        assert "floor_mixed" in message
        assert "district_domains" in message

    def test_the_warning_never_echoes_the_offending_value(self, user_mappings, caplog):
        """The likeliest bad row is a pasted PERSONAL address; a log is ops-visible."""
        leaked_local, leaked_domain = "aparticularperson", "somedistrict.bc.ca"
        pasted = f"{leaked_local}@{leaked_domain}"
        _write_config(user_mappings, "floor_noecho", district_domains=[pasted])
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            cfg = load_config("floor_noecho")
        assert cfg.district_domains == []
        for record in caplog.records:
            assert pasted not in record.getMessage()
            assert leaked_local not in record.getMessage()

    def test_the_no_echo_pin_would_catch_a_regression(self):
        """Falsification twin — prove the assertion above is not vacuous."""
        leaky = "dropped 'aparticularperson@somedistrict.bc.ca' from district_domains"
        assert "aparticularperson" in leaky

    def test_the_warning_names_the_consequence_in_plain_language(self, user_mappings, caplog):
        _write_config(user_mappings, "floor_consequence", district_domains=[BAD_DOMAIN])
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            load_config("floor_consequence")
        message = _loader_records(caplog, logging.WARNING)[0].getMessage()
        assert "picker" in message
        assert "not be matched" in message
        assert "conversion itself is unaffected" in message

    def test_a_non_list_value_drops_the_whole_key_with_the_same_warning_shape(self, user_mappings, caplog):
        _write_config(user_mappings, "floor_notalist", district_domains=GOOD_DOMAIN)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            cfg = load_config("floor_notalist")
        assert cfg.district_domains == []
        warnings = _loader_records(caplog, logging.WARNING)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "dropped 1 of 1" in message
        assert "not a list" in message
        assert GOOD_DOMAIN not in message

    def test_all_valid_domains_warn_about_nothing(self, user_mappings, caplog):
        """Positive twin for the no-warning path — the floor must be silent when clean."""
        _write_config(user_mappings, "floor_clean", district_domains=[GOOD_DOMAIN, "sd93-schools.ca"])
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            cfg = load_config("floor_clean")
        assert cfg.district_domains == [GOOD_DOMAIN, "sd93-schools.ca"]
        assert _loader_records(caplog, logging.WARNING) == []

    def test_an_absent_key_is_untouched(self, user_mappings, caplog):
        _write_config(user_mappings, "floor_absent")
        with caplog.at_level(logging.DEBUG, logger=LOADER_LOGGER):
            cfg = load_config("floor_absent")
        assert cfg.district_domains == []
        assert _loader_records(caplog, logging.WARNING) == []

    def test_the_same_bad_row_still_RAISES_outside_the_user_tier(self, tmp_path):
        """The single-dir ``config_dir=`` override is "bundled"-equivalent: no floor.

        Bundled rows are CI's to catch (`make validate-config`), so the model
        validator's loud raise must survive there — the floor exists only for
        hand-editable user files that no gate ever sees.
        """
        (tmp_path / "myedbc_mapping.yaml").write_text(
            (bundle_mappings_dir() / "myedbc_mapping.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        _write_config(tmp_path, "floor_bundled", district_domains=[GOOD_DOMAIN, BAD_DOMAIN])
        with pytest.raises(ValueError, match="district_domains"):
            load_config("floor_bundled", tmp_path)

    def test_the_bundled_raise_pin_is_not_vacuous(self, tmp_path):
        """Positive twin: the same override path loads fine with a VALID row."""
        (tmp_path / "myedbc_mapping.yaml").write_text(
            (bundle_mappings_dir() / "myedbc_mapping.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        _write_config(tmp_path, "floor_bundled_ok", district_domains=[GOOD_DOMAIN])
        assert load_config("floor_bundled_ok", tmp_path).district_domains == [GOOD_DOMAIN]


# --------------------------------------------------------------------------- #
# validate_overlay — the authoring load-back, before any file exists          #
# --------------------------------------------------------------------------- #
def _overlay(**extra) -> dict:
    return {
        "_base": "myedbc",
        "sis": "sd93custom",
        "district_name": "SD93 - Synthetic Test District",
        "district_domains": [GOOD_DOMAIN],
        **extra,
    }


class TestValidateOverlay:
    def test_happy_path_resolves_the_bundled_base(self, user_mappings):
        cfg = validate_overlay(_overlay())
        assert cfg.district_domains == [GOOD_DOMAIN]
        assert cfg.district_name == "SD93 - Synthetic Test District"
        # Inheritance actually resolved: the base's grade policy came through.
        base = load_config("myedbc")
        assert cfg.global_config.homeroom_grades == base.global_config.homeroom_grades
        assert cfg.mappings.keys() == base.mappings.keys()

    def test_the_callers_dict_is_not_mutated(self, user_mappings):
        raw = _overlay()
        validate_overlay(raw)
        assert raw["_base"] == "myedbc"
        assert raw == _overlay()

    def test_no_file_is_created_anywhere_in_the_user_dir(self, user_mappings):
        before = sorted(p.name for p in user_mappings.glob("*"))
        validate_overlay(_overlay())
        assert sorted(p.name for p in user_mappings.glob("*")) == before

    def test_the_nothing_created_pin_is_not_vacuous(self, user_mappings):
        """Positive twin: the SAME dir the previous test watched is live and loadable."""
        validate_overlay(_overlay())
        assert sorted(p.name for p in user_mappings.glob("*")) == []
        _write_config(user_mappings, "sd93custom", district_domains=[GOOD_DOMAIN])
        assert [p.name for p in user_mappings.glob("*")] == ["sd93custom_mapping.yaml"]
        assert load_config("sd93custom").district_domains == [GOOD_DOMAIN]

    def test_unknown_base_raises_filenotfound(self, user_mappings):
        with pytest.raises(FileNotFoundError, match="no_such_base_mapping.yaml"):
            validate_overlay(_overlay(_base="no_such_base"))

    def test_a_bad_major_version_raises_mentioning_major(self, user_mappings):
        with pytest.raises(ValueError, match="major"):
            validate_overlay(_overlay(version="2.0"))

    def test_a_schema_error_is_wrapped_like_load_config(self, user_mappings):
        with pytest.raises(ValueError) as excinfo:
            validate_overlay(_overlay(global_config={"student_rostering_grades": []}))
        assert str(excinfo.value).startswith("Invalid mapping config 'sd93custom'")

    def test_an_overlay_is_user_tier_so_the_domains_floor_applies(self, user_mappings, caplog):
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            cfg = validate_overlay(_overlay(district_domains=[GOOD_DOMAIN, BAD_DOMAIN]))
        assert cfg.district_domains == [GOOD_DOMAIN]
        warnings = _loader_records(caplog, logging.WARNING)
        assert len(warnings) == 1
        assert "dropped 1 of 2" in warnings[0].getMessage()
        assert BAD_DOMAIN not in warnings[0].getMessage()

    def test_the_injected_search_pair_is_honoured(self, tmp_path):
        """A ``_base`` living in the injected USER dir resolves without touching the bundle."""
        user = tmp_path / "userdir"
        bundled = tmp_path / "bundledir"
        bundled.mkdir()
        (user).mkdir()
        (user / "localbase_mapping.yaml").write_text(
            (bundle_mappings_dir() / "myedbc_mapping.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        cfg = validate_overlay(_overlay(_base="localbase"), search_dirs=[user, bundled])
        assert cfg.district_name == "SD93 - Synthetic Test District"

    @pytest.mark.parametrize("count", [1, 3])
    def test_a_search_dirs_sequence_that_is_not_a_pair_is_refused(self, tmp_path, count):
        dirs = [tmp_path / f"d{i}" for i in range(count)]
        for d in dirs:
            d.mkdir()
        with pytest.raises(ValueError, match="exactly 2 directories"):
            validate_overlay(_overlay(), search_dirs=dirs)

    def test_an_overlay_without_a_sis_label_still_reports_a_readable_error(self, user_mappings):
        raw = _overlay(global_config={"student_rostering_grades": []})
        del raw["sis"]
        with pytest.raises(ValueError) as excinfo:
            validate_overlay(raw)
        assert "<unsaved overlay>" in str(excinfo.value)

    def test_an_explicit_label_names_the_config_even_with_no_sis_key(self, user_mappings):
        """`write_overlay` passes `label=sis_id` — since plan 0044 review fix #2 an
        overlay carries no `sis:` key of its own (that key is the inherited SIS
        PRODUCT NAME, not the config id), so the id must come from the caller.
        """
        raw = _overlay(global_config={"student_rostering_grades": []})
        del raw["sis"]
        with pytest.raises(ValueError) as excinfo:
            validate_overlay(raw, label="sd93custom")
        assert str(excinfo.value).startswith("Invalid mapping config 'sd93custom'")
