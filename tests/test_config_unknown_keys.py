"""Config typos are loud and origin-keyed (plan 0053 S12, failure-policy.md P15).

- An unknown ``global_config`` / entity key or ``source_columns`` ROLE: a BUNDLED config
  RAISES at load (``make validate-config`` / CI catch it), a USER-dir config WARNS one line
  per key and loads (a stray key never stops a district's nightly), and authoring
  (``validate_overlay`` / ``write_overlay``) REFUSES it.
- A misspelled key INSIDE a field mapping (``transfrom:``) is refused for EVERY origin
  (every ``ConfiguredField`` forbids extras — D11), naming the nearest known key.
- The root stays ``extra="ignore"`` (forward compatibility).

Every negative has a positive twin: the correctly spelled key loads (and, where it
matters, takes effect), so a refusal can never be green because nothing loads at all.
"""

from __future__ import annotations

import copy
import inspect
import logging
import shutil
from pathlib import Path
from typing import Any

import ci_flet_pack_smoke as smoke  # loaded + registered by tests/conftest.py
import pandas as pd
import pytest
import yaml

from src.config import authoring, loader
from src.config.authoring import OverlaySpec, write_overlay
from src.config.loader import (
    UnknownConfigKeyError,
    _load_yaml,
    _resolve_inheritance,
    available_configs,
    load_config,
    unknown_config_keys,
    validate_overlay,
)
from src.config.models import (
    FIELD_VARIANTS,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldEnrollStatus,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    MappingConfig,
    UnknownKey,
    classify_field,
    ensure_field_mapping,
    field_shape,
    switch_field_shape,
)
from src.etl.outcomes import OutcomeNote
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import session_time_components, session_time_labels, session_time_roles
from src.etl.transformers.classes import ClassTransformer
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.enrollments import EnrollmentTransformer
from src.etl.transformers.registry import TRANSFORMER_REGISTRY, source_column_roles
from src.etl.transformers.staff import StaffTransformer
from src.etl.transformers.student_courses import StudentCoursesTransformer
from src.utils.paths import bundle_mappings_dir, user_mappings_dir
from tests._pins import BUNDLED_CONFIG_COUNT

LOADER_LOGGER = "src.config.loader"
SIS = "sd99typo"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _write(directory: Path, body: dict[str, Any], sis: str = SIS) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sis}_mapping.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def bundled_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in BUNDLED dir holding the real ``myedbc`` base (the user dir stays the
    test-isolated one conftest provides), so ``load_config`` resolves origin ``bundled``."""
    directory = tmp_path / "bundled"
    directory.mkdir()
    shutil.copy(bundle_mappings_dir() / "myedbc_mapping.yaml", directory / "myedbc_mapping.yaml")
    monkeypatch.setattr(loader, "bundle_mappings_dir", lambda: directory)
    return directory


def _unknown_key_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOADER_LOGGER and record.levelno == logging.WARNING and "unknown key" in record.getMessage()
    ]


def _overlay(**sections: Any) -> dict[str, Any]:
    return {"_base": "myedbc", **sections}


GLOBAL_TYPO = _overlay(global_config={"enabled_entites": ["Students"]})
GLOBAL_OK = _overlay(global_config={"enabled_entities": ["Students"]})
ENTITY_TYPO = _overlay(mappings={"Family": {"row_filter": [{"column": "Parent Auth / Guardian", "include": ["Y"]}]}})
ENTITY_OK = _overlay(mappings={"Family": {"row_filters": [{"column": "Parent Auth / Guardian", "include": ["Y"]}]}})
ROLE_TYPO = _overlay(mappings={"Staff": {"source_columns": {"staff_stauts": "Employment Status"}}})
ROLE_OK = _overlay(mappings={"Staff": {"source_columns": {"staff_status": "Employment Status"}}})
TRANSFROM = _overlay(mappings={"Students": {"field_map": {"Grade": {"column": "Grade", "transfrom": "grade_to_ceds"}}}})
TRANSFORM_OK = _overlay(
    mappings={"Students": {"field_map": {"Grade": {"column": "Grade", "transform": "grade_to_ceds"}}}}
)


#: One minimal raw dict per structured shape (the switch sweep's base x override grid).
SHAPE_EXAMPLES: dict[str, dict[str, Any]] = {
    "transform": {"column": "A", "transform": "grade_to_ceds"},
    "fixed": {"value": "09"},
    "academic": {"use_academic_year": False, "value": "2025-09-01"},
    "append": {"column": "M", "append_year_to_id": True},
    "email": {"format": "{student number}.x", "sanitize": True},
    "name": {"primary teacher flag": "P", "teacher last name": "L", "course title": "C"},
    "id-role": {"student_id_col": "S", "staff_id_col": "T"},
    "enroll": {"status_column": "St", "active_values": ["Active"]},
}

#: One typed field per variant (plus the all-default forms) for the raw round trip.
ROUND_TRIP_FIELDS: list[Any] = [
    FieldTransform(column="A", transform="grade_to_ceds"),
    FieldTransform(column="A"),
    FieldFixedValue(value=""),
    FieldAcademicYear(),
    FieldAcademicYear(use_academic_year=False, value="2025-09-01"),
    FieldAppendYear(column="M"),
    FieldEmailFormat(format="{student number}.x", sanitize=True),
    FieldNameConfig(),
    FieldIdRolePair(student_id_col="S", staff_id_col="T"),
    FieldEnrollStatus(),
    FieldEnrollStatus(active_values=["Active"]),
]


# --------------------------------------------------------------------------- #
# BUNDLED — raises at load                                                     #
# --------------------------------------------------------------------------- #
class TestBundledRaises:
    def test_a_misspelled_enabled_entities_raises_with_the_suggestion(self, bundled_dir: Path):
        _write(bundled_dir, GLOBAL_TYPO)
        with pytest.raises(UnknownConfigKeyError) as caught:
            load_config(SIS)
        assert caught.value.findings == (UnknownKey("global_config", "enabled_entites", "enabled_entities"),)
        message = str(caught.value)
        assert "global_config: unknown key 'enabled_entites' — did you mean 'enabled_entities'?" in message
        # A ValueError, so every existing config-failure path (the pipeline's `config`
        # category, `make validate-config`) handles it unchanged.
        assert isinstance(caught.value, ValueError)

    def test_twin_the_correct_spelling_loads_and_takes_effect(self, bundled_dir: Path):
        _write(bundled_dir, GLOBAL_OK)
        assert load_config(SIS).active_entities() == {"Students"}

    def test_the_single_config_dir_seam_is_bundled_equivalent(self, bundled_dir: Path):
        _write(bundled_dir, GLOBAL_TYPO)
        with pytest.raises(UnknownConfigKeyError):
            load_config(SIS, config_dir=bundled_dir)
        _write(bundled_dir, GLOBAL_OK)
        assert load_config(SIS, config_dir=bundled_dir).active_entities() == {"Students"}

    def test_an_unknown_entity_key_raises(self, bundled_dir: Path):
        _write(bundled_dir, ENTITY_TYPO)
        with pytest.raises(UnknownConfigKeyError) as caught:
            load_config(SIS)
        assert caught.value.findings == (UnknownKey("mappings.Family", "row_filter", "row_filters"),)
        _write(bundled_dir, ENTITY_OK)
        assert len(load_config(SIS).mappings["Family"].row_filters) == 1

    def test_a_misspelled_source_columns_role_raises(self, bundled_dir: Path):
        _write(bundled_dir, ROLE_TYPO)
        with pytest.raises(UnknownConfigKeyError) as caught:
            load_config(SIS)
        assert caught.value.findings == (UnknownKey("mappings.Staff.source_columns", "staff_stauts", "staff_status"),)
        _write(bundled_dir, ROLE_OK)
        assert load_config(SIS).mappings["Staff"].source_columns == {"staff_status": "Employment Status"}

    def test_every_finding_is_reported_not_just_the_first(self, bundled_dir: Path):
        body = _overlay(
            global_config={"enabled_entites": ["Students"], "homeroom_grads": ["KG"]},
            mappings={"Staff": {"source_columns": {"staff_stauts": "x"}}},
        )
        _write(bundled_dir, body)
        with pytest.raises(UnknownConfigKeyError) as caught:
            load_config(SIS)
        assert [(f.location, f.key) for f in caught.value.findings] == [
            ("global_config", "enabled_entites"),
            ("global_config", "homeroom_grads"),
            ("mappings.Staff.source_columns", "staff_stauts"),
        ]


# --------------------------------------------------------------------------- #
# USER-dir — warns one line per key, loads                                     #
# --------------------------------------------------------------------------- #
class TestUserDirWarns:
    def test_a_misspelled_enabled_entities_warns_suggests_and_loads(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), GLOBAL_TYPO)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            config = load_config(SIS)
        warnings = _unknown_key_warnings(caplog)
        assert len(warnings) == 1
        assert "global_config: unknown key 'enabled_entites' — did you mean 'enabled_entities'?" in warnings[0]
        assert "IGNORED" in warnings[0]
        # Honest about what "ignored" means: the typo'd key did NOT take effect — the base's
        # selection stands instead of the intended Students-only one.
        base = load_config("myedbc")
        assert config.global_config.enabled_entities == base.global_config.enabled_entities
        assert config.active_entities() != {"Students"}

    def test_twin_the_correct_spelling_is_silent_and_takes_effect(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), GLOBAL_OK)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            config = load_config(SIS)
        assert _unknown_key_warnings(caplog) == []
        assert config.active_entities() == {"Students"}

    def test_one_warning_line_per_key(self, caplog: pytest.LogCaptureFixture):
        body = _overlay(
            global_config={"enabled_entites": ["Students"]},
            mappings={"Family": {"row_filter": []}, "Staff": {"source_columns": {"staff_stauts": "x"}}},
        )
        _write(user_mappings_dir(), body)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            load_config(SIS)
        warnings = _unknown_key_warnings(caplog)
        assert len(warnings) == 3
        assert any("mappings.Family: unknown key 'row_filter' — did you mean 'row_filters'?" in w for w in warnings)
        assert any("mappings.Staff.source_columns: unknown key 'staff_stauts'" in w for w in warnings)

    def test_a_misspelled_role_warns_and_the_default_is_read(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), ROLE_TYPO)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            config = load_config(SIS)
        warnings = _unknown_key_warnings(caplog)
        assert len(warnings) == 1 and "did you mean 'staff_status'?" in warnings[0]
        mapping = config.to_raw_dict()["mappings"]["Staff"]
        assert StaffTransformer.resolve_status_column(mapping) == "staff status"  # the documented default

    def test_twin_a_correct_role_is_silent_and_read(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), ROLE_OK)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            config = load_config(SIS)
        assert _unknown_key_warnings(caplog) == []
        mapping = config.to_raw_dict()["mappings"]["Staff"]
        assert StaffTransformer.resolve_status_column(mapping) == "employment status"

    def test_a_suggestion_is_omitted_when_nothing_is_close(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), _overlay(global_config={"zzqx": 1}))
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            load_config(SIS)
        (warning,) = _unknown_key_warnings(caplog)
        assert "unknown key 'zzqx' — no known key is close to it" in warning
        assert "did you mean" not in warning


# --------------------------------------------------------------------------- #
# Field-mapping typos — refused for EVERY origin                               #
# --------------------------------------------------------------------------- #
class TestFieldMappingTyposRefusedEverywhere:
    def test_transfrom_is_refused_for_a_bundled_config(self, bundled_dir: Path):
        _write(bundled_dir, TRANSFROM)
        with pytest.raises(
            ValueError, match=r"field_map entry 'Grade': unknown key 'transfrom' \(did you mean 'transform'\?\)"
        ):
            load_config(SIS)

    def test_transfrom_is_refused_for_a_user_dir_config(self):
        _write(user_mappings_dir(), TRANSFROM)
        with pytest.raises(ValueError, match=r"unknown key 'transfrom' \(did you mean 'transform'\?\)"):
            load_config(SIS)

    def test_transfrom_is_refused_at_authoring(self):
        with pytest.raises(ValueError, match=r"unknown key 'transfrom' \(did you mean 'transform'\?\)"):
            validate_overlay(copy.deepcopy(TRANSFROM))

    def test_twin_the_correct_spelling_loads_everywhere(self, bundled_dir: Path):
        _write(bundled_dir, TRANSFORM_OK)
        spec = load_config(SIS).mappings["Students"].field_map["Grade"]
        assert isinstance(spec, FieldTransform) and spec.transform == "grade_to_ceds"
        _write(user_mappings_dir(), TRANSFORM_OK, sis="sd98typo")
        assert load_config("sd98typo").mappings["Students"].field_map["Grade"].transform == "grade_to_ceds"
        assert validate_overlay(copy.deepcopy(TRANSFORM_OK)).mappings["Students"].field_map["Grade"].transform

    def test_transfrom_without_a_column_is_refused_not_passed_through(self):
        """No distinguishing key at all: before S12 this was logged and returned RAW, and the
        engine shipped the field blank with nothing recorded."""
        body = _overlay(mappings={"Students": {"field_map": {"Grade": {"transfrom": "grade_to_ceds"}}}})
        _write(user_mappings_dir(), body)
        with pytest.raises(
            ValueError, match=r"field_map entry 'Grade': unknown key 'transfrom' \(did you mean 'transform'\?\)"
        ):
            load_config(SIS)

    def test_a_class_name_block_typo_is_refused(self):
        """``FieldNameConfig`` forbids too (it was one of the two ``ignore`` holdouts)."""
        body = _overlay(
            mappings={"Classes": {"field_map": {"Class Name": {"teacher last name": "T", "course titel": "C"}}}}
        )
        with pytest.raises(ValueError, match=r"unknown key 'course titel' \(did you mean 'course title'\?\)"):
            validate_overlay(body)

    def test_twin_a_correct_class_name_block_loads(self):
        body = _overlay(
            mappings={"Classes": {"field_map": {"Class Name": {"teacher last name": "T", "course title": "C"}}}}
        )
        assert validate_overlay(body).mappings["Classes"].field_map["Class Name"].course_title == "C"


# --------------------------------------------------------------------------- #
# Authoring — refuses                                                          #
# --------------------------------------------------------------------------- #
class TestAuthoringRefuses:
    @pytest.mark.parametrize("body", [GLOBAL_TYPO, ENTITY_TYPO, ROLE_TYPO], ids=["global", "entity", "role"])
    def test_validate_overlay_refuses_what_a_user_dir_load_would_only_warn_about(self, body):
        with pytest.raises(UnknownConfigKeyError):
            validate_overlay(copy.deepcopy(body))

    @pytest.mark.parametrize("body", [GLOBAL_OK, ENTITY_OK, ROLE_OK], ids=["global", "entity", "role"])
    def test_twin_the_correct_spelling_validates(self, body):
        validate_overlay(copy.deepcopy(body))

    def test_write_overlay_refuses_and_writes_nothing(self, monkeypatch: pytest.MonkeyPatch):
        real_build = authoring.build_overlay

        def _with_a_stray_key(*args: Any, **kwargs: Any) -> dict[str, Any]:
            overlay = real_build(*args, **kwargs)
            overlay["global_config"] = {**overlay.get("global_config", {}), "enabled_entites": ["Students"]}
            return overlay

        spec = OverlaySpec(sd_number=93, district_name="Typo District", district_domains=(), base="myedbc")
        monkeypatch.setattr(authoring, "build_overlay", _with_a_stray_key)
        with pytest.raises(UnknownConfigKeyError):
            write_overlay(spec, overwrite=False)
        assert list(user_mappings_dir().glob("*.yaml")) == []

        # Twin: the same spec without the stray key is written.
        monkeypatch.setattr(authoring, "build_overlay", real_build)
        assert write_overlay(spec, overwrite=False).exists()

    def test_the_pack_smoke_overlay_carries_no_unknown_key(self, caplog: pytest.LogCaptureFixture):
        """The overlay the CI pack smoke plants loads through authoring's refusal AND from the
        user dir with zero unknown-key warnings."""
        planted = yaml.safe_load(smoke._SD93_OVERLAY_YAML)
        validate_overlay(copy.deepcopy(planted), label=smoke._SD93_OVERLAY_SIS)
        (user_mappings_dir() / f"{smoke._SD93_OVERLAY_SIS}_mapping.yaml").write_text(
            smoke._SD93_OVERLAY_YAML, encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            load_config(smoke._SD93_OVERLAY_SIS)
        assert _unknown_key_warnings(caplog) == []


# --------------------------------------------------------------------------- #
# Forward compatibility + purity                                               #
# --------------------------------------------------------------------------- #
class TestRootStaysIgnoredAndInputIsNotMutated:
    def test_a_root_level_unknown_key_is_still_ignored(self, bundled_dir: Path, caplog: pytest.LogCaptureFixture):
        _write(bundled_dir, _overlay(a_key_from_a_newer_build={"anything": 1}))
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            load_config(SIS)
        assert _unknown_key_warnings(caplog) == []
        validate_overlay(_overlay(a_key_from_a_newer_build=1))

    def test_twin_the_same_key_one_level_down_is_caught(self, bundled_dir: Path):
        _write(bundled_dir, _overlay(global_config={"a_key_from_a_newer_build": 1}))
        with pytest.raises(UnknownConfigKeyError):
            load_config(SIS)

    def test_the_walker_does_not_mutate_its_input(self):
        raw = {
            "global_config": {"enabled_entites": ["Students"]},
            "mappings": {"Staff": {"source_columns": {"staff_stauts": "x"}, "field_map": {}}},
        }
        before = copy.deepcopy(raw)
        assert len(unknown_config_keys(raw, MappingConfig)) == 2
        assert raw == before

    def test_validate_overlay_does_not_mutate_its_input_even_when_refusing(self):
        raw = copy.deepcopy(GLOBAL_TYPO)
        with pytest.raises(UnknownConfigKeyError):
            validate_overlay(raw)
        assert raw == GLOBAL_TYPO

    def test_open_dicts_and_forbid_models_are_not_walked(self):
        raw = {
            "global_config": {
                "attendance": {"any_knob": 1},
                "school_year_sources": {"any_role": "f.txt"},
                "cross_enrollment": {"collapse": True},
            },
            "mappings": {
                "Students": {
                    "source_files": {"any_role": "f.txt"},
                    "headers": {"f.txt": ["a"]},
                    "field_map": {"X": {"column": "a"}},
                    "row_filters": [{"column": "a", "include": []}],
                }
            },
        }
        assert unknown_config_keys(raw, MappingConfig) == []
        # Twin: the walker does see those very blocks' parents.
        raw["global_config"]["attendence"] = {}
        raw["mappings"]["Students"]["source_file"] = {}
        assert [(f.location, f.key) for f in unknown_config_keys(raw, MappingConfig)] == [
            ("global_config", "attendence"),
            ("mappings.Students", "source_file"),
        ]

    def test_a_wrongly_shaped_section_is_left_to_the_model(self):
        assert unknown_config_keys({"global_config": None, "mappings": ["Students"]}, MappingConfig) == []
        assert unknown_config_keys({"mappings": {"Staff": None}}, MappingConfig) == []


# --------------------------------------------------------------------------- #
# Every bundled config — zero findings                                         #
# --------------------------------------------------------------------------- #
class TestBundledConfigsAreClean:
    @pytest.mark.parametrize("sis", available_configs(bundle_mappings_dir()))
    def test_every_bundled_config_has_zero_findings_and_loads(self, sis: str):
        raw = _load_yaml(bundle_mappings_dir() / f"{sis}_mapping.yaml")
        resolved = _resolve_inheritance(raw, [bundle_mappings_dir()])
        assert unknown_config_keys(resolved, MappingConfig) == []
        load_config(sis, config_dir=bundle_mappings_dir())

    def test_twin_a_doctored_bundled_config_is_caught(self):
        raw = _load_yaml(bundle_mappings_dir() / "sd40myedbc_mapping.yaml")
        resolved = _resolve_inheritance(raw, [bundle_mappings_dir()])
        resolved["mappings"]["Classes"]["session_component"] = resolved["mappings"]["Classes"].pop("session_components")
        assert unknown_config_keys(resolved, MappingConfig) == [
            UnknownKey("mappings.Classes", "session_component", "session_components")
        ]

    def test_the_parametrisation_is_not_empty(self):
        assert len(available_configs(bundle_mappings_dir())) >= BUNDLED_CONFIG_COUNT


# --------------------------------------------------------------------------- #
# The role vocabulary is the one the transformers read                        #
# --------------------------------------------------------------------------- #
class _RecordingRoles(dict):
    """A ``source_columns`` block that records every role a read site asks for.

    Truthy while empty so ``mapping.get("source_columns") or {}`` keeps it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.asked: set[str] = set()

    def __bool__(self) -> bool:
        return True

    def get(self, key: Any, default: Any = None) -> Any:
        self.asked.add(key)
        return super().get(key, default)


class TestRoleVocabularyIsWhatTheReadSitesRead:
    def test_staff(self):
        roles = _RecordingRoles()
        StaffTransformer.resolve_status_column({"source_columns": roles})
        assert roles.asked == StaffTransformer.SOURCE_COLUMN_ROLES == source_column_roles("Staff") != frozenset()

    def test_student_courses(self):
        roles = _RecordingRoles()
        StudentCoursesTransformer._resolve_source_columns({"field_map": {}, "source_columns": roles})
        assert roles.asked == StudentCoursesTransformer.SOURCE_COLUMN_ROLES == source_column_roles("StudentCourses")

    def test_classes(self):
        roles = _RecordingRoles()
        in_force = session_time_roles(None)
        session_time_components(roles, roles=in_force)
        session_time_labels(roles, roles=in_force)
        assert roles.asked == ClassTransformer.SOURCE_COLUMN_ROLES == source_column_roles("Classes")

    def test_enrollments(self):
        roles = _RecordingRoles()
        ctx = TransformContext(school_year=2025)
        ctx.class_artifacts = ClassArtifacts(
            homeroom_classes_df=pd.DataFrame(
                {"school number": ["100"], "homeroom": ["A1"], "Class ID": ["100_A1_2025"], "teacher id": ["T001"]}
            ),
            class_info_df=pd.DataFrame(
                {
                    "school number": ["100"],
                    "teacher id": ["T777"],
                    "master timetable id": ["MT900"],
                    "primary teacher": ["Y"],
                    "section letter": ["A1"],
                }
            ),
            blended_class_map={},
            blended_class_metadata={},
            blended_teacher_map={},
        )
        EnrollmentTransformer()._classinfo_coteacher_enrollments(
            "teacher id", "Teacher ID", roles, ctx.class_artifacts, ctx
        )
        assert roles.asked == EnrollmentTransformer.SOURCE_COLUMN_ROLES == source_column_roles("Enrollments")
        assert OutcomeNote.COTEACHER_SOURCE_UNUSABLE not in dict(ctx.outcome_notes_for("Enrollments"))

    def test_an_entity_that_reads_no_block_knows_no_role(self):
        assert source_column_roles("Students") == frozenset()
        assert source_column_roles("NotARegisteredEntity") == BaseTransformer.SOURCE_COLUMN_ROLES == frozenset()
        # A role valid for one entity is unknown on another: the vocabulary is per entity.
        raw = {"mappings": {"Classes": {"source_columns": {"staff_status": "x"}}}}
        assert unknown_config_keys(raw, MappingConfig) == [
            UnknownKey("mappings.Classes.source_columns", "staff_status", None)
        ]

    def test_every_registered_transformer_declares_a_frozenset(self):
        declared = {name: type(t).SOURCE_COLUMN_ROLES for name, t in TRANSFORMER_REGISTRY.items()}
        assert all(isinstance(roles, frozenset) for roles in declared.values())
        assert sum(bool(roles) for roles in declared.values()) == 4  # Staff, Classes, Enrollments, StudentCourses


# --------------------------------------------------------------------------- #
# The message the developer guide quotes is the one the code produces         #
# --------------------------------------------------------------------------- #
ADDING_DISTRICT = Path(__file__).resolve().parent.parent / "docs" / "developer" / "adding-district.md"


def test_the_base_yaml_comments_name_every_role():
    """``adding-district.md`` points readers at the base YAML's comments for each entity's
    accepted ``source_columns`` roles, so every declared role must appear there."""
    text = (bundle_mappings_dir() / "myedbc_mapping.yaml").read_text(encoding="utf-8")
    declared = {role for entity in TRANSFORMER_REGISTRY for role in source_column_roles(entity)}
    assert declared  # the sweep reads a non-empty vocabulary
    assert sorted(role for role in declared if role not in text) == []
    # Twin: a comment that stops naming a role is caught.
    doctored = text.replace("staff_status", "staff_state")
    assert [role for role in declared if role not in doctored] == ["staff_status"]


def _quoted_example_is_current(doc_text: str) -> bool:
    produced = UnknownKey("global_config", "enabled_entites", "enabled_entities").describe()
    return f"`{produced}`" in doc_text


def test_the_guide_quotes_the_message_the_walker_produces():
    """``adding-district.md`` quotes an unknown-key message; it must be the one the code
    prints (a literal copied out of ``src/`` into a doc needs a parity test)."""
    assert _quoted_example_is_current(ADDING_DISTRICT.read_text(encoding="utf-8"))


def test_twin_a_doctored_guide_is_caught():
    doctored = ADDING_DISTRICT.read_text(encoding="utf-8").replace("did you mean", "perhaps you meant")
    assert not _quoted_example_is_current(doctored)


# --------------------------------------------------------------------------- #
# The safety-relevant policy parameter has no default (no permissive default)   #
# --------------------------------------------------------------------------- #
class TestThePolicyIsNeverDefaulted:
    @pytest.mark.parametrize(
        ("function", "parameter"),
        [(loader._resolve_gate_and_validate, "unknown_keys"), (loader._apply_unknown_key_policy, "policy")],
        ids=["resolve-gate-and-validate", "apply-unknown-key-policy"],
    )
    def test_required_keyword_only(self, function: Any, parameter: str):
        parameters = inspect.signature(function).parameters
        assert parameter in parameters  # positive twin: the pin reads a parameter that exists
        assert parameters[parameter].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[parameter].default is inspect.Parameter.empty

    def test_the_origin_table_is_the_documented_direction(self):
        """Bundled RAISES, user-dir WARNS — never inverted."""
        assert loader._UNKNOWN_KEY_POLICY_BY_ORIGIN == {"user": "warn", "bundled": "raise"}


# --------------------------------------------------------------------------- #
# An `_base` override that SWITCHES a field's shape is not a typo               #
# --------------------------------------------------------------------------- #
SWITCH_TO_FIXED = _overlay(mappings={"Students": {"field_map": {"Grade": {"value": "09"}}}})
PARTIAL_COLUMN = _overlay(mappings={"Students": {"field_map": {"Grade": {"column": "Gr"}}}})


class TestAShapeSwitchUnderInheritanceLoads:
    """The base's ``Grade`` is ``{column, transform}``. Merged key by key, ``{value: "09"}``
    became ``{column, transform, value}`` and the fixed-value variant (which forbids extras
    since S12) refused two keys the author never typed."""

    def test_the_base_entry_is_what_this_class_assumes(self):
        base = load_config("myedbc").mappings["Students"].field_map["Grade"]
        assert isinstance(base, FieldTransform) and base.transform

    def test_switching_to_a_fixed_value_loads_from_the_user_dir(self, caplog: pytest.LogCaptureFixture):
        _write(user_mappings_dir(), SWITCH_TO_FIXED)
        with caplog.at_level(logging.WARNING, logger=LOADER_LOGGER):
            field = load_config(SIS).mappings["Students"].field_map["Grade"]
        assert field == FieldFixedValue(value="09")
        assert _unknown_key_warnings(caplog) == []

    def test_switching_loads_for_a_bundled_config_and_at_authoring(self, bundled_dir: Path):
        _write(bundled_dir, SWITCH_TO_FIXED)
        assert load_config(SIS).mappings["Students"].field_map["Grade"] == FieldFixedValue(value="09")
        authored = validate_overlay(copy.deepcopy(SWITCH_TO_FIXED))
        assert authored.mappings["Students"].field_map["Grade"] == FieldFixedValue(value="09")

    def test_twin_a_partial_override_still_inherits_the_transform(self):
        _write(user_mappings_dir(), PARTIAL_COLUMN)
        field = load_config(SIS).mappings["Students"].field_map["Grade"]
        assert isinstance(field, FieldTransform)
        assert (field.column, field.transform) == ("Gr", "grade_to_ceds")

    def test_a_switch_keeps_the_inherited_keys_the_new_shape_accepts(self):
        body = _overlay(mappings={"Students": {"field_map": {"Grade": {"append_year_to_id": True}}}})
        field = validate_overlay(body).mappings["Students"].field_map["Grade"]
        assert isinstance(field, FieldAppendYear)
        assert field.column == "Grade"  # inherited; the base's `transform` was dropped, not refused

    def test_a_real_typo_beside_a_switch_is_still_refused(self):
        body = _overlay(mappings={"Students": {"field_map": {"Grade": {"value": "09", "valeu": "x"}}}})
        with pytest.raises(ValueError, match=r"unknown key 'valeu' \(did you mean 'value'\?\)"):
            validate_overlay(body)

    def test_only_field_map_entries_switch(self):
        """Everywhere else a dict pair still merges key by key."""
        base = {"global_config": {"cross_enrollment": {"collapse": True, "home_school_column": "H"}}}
        override = {"global_config": {"cross_enrollment": {"value": "x"}}}
        merged = loader._deep_merge(base, override)["global_config"]["cross_enrollment"]
        assert merged == {"collapse": True, "home_school_column": "H", "value": "x"}

    @pytest.mark.parametrize("base_raw", list(SHAPE_EXAMPLES.values()), ids=list(SHAPE_EXAMPLES))
    @pytest.mark.parametrize("override_raw", list(SHAPE_EXAMPLES.values()), ids=list(SHAPE_EXAMPLES))
    def test_a_switched_entry_classifies_as_the_override_names(
        self, base_raw: dict[str, Any], override_raw: dict[str, Any]
    ):
        """Sweep: whatever the pair, a switched entry is the override's shape (a kept inherited
        key can never re-route detection) and never carries a key that shape forbids."""
        switched = switch_field_shape(base_raw, override_raw)
        if field_shape(base_raw) is field_shape(override_raw):
            assert switched is None
            return
        assert switched is not None
        assert type(classify_field(switched)) is field_shape(override_raw)

    def test_the_sweep_covers_every_variant(self):
        assert {field_shape(raw) for raw in SHAPE_EXAMPLES.values()} == set(FIELD_VARIANTS)


# --------------------------------------------------------------------------- #
# validated config -> raw dict -> classify is TOTAL                             #
# --------------------------------------------------------------------------- #
class TestRawRoundTripReclassifies:
    @pytest.mark.parametrize("field", ROUND_TRIP_FIELDS, ids=[repr(f) for f in ROUND_TRIP_FIELDS])
    def test_every_variant_survives_get_raw_field_map(self, field: Any):
        config = load_config("myedbc")
        config.mappings["Students"].field_map = {"Probe": field}
        raw = config.get_raw_field_map("Students")["Probe"]
        rebuilt = ensure_field_mapping(raw)
        assert type(rebuilt) is type(field)
        assert rebuilt == field

    def test_an_all_default_enroll_status_block_names_its_shape(self):
        """The regression: it was written back as ``{}``, which classify_field refuses."""
        config = load_config("myedbc")
        config.mappings["Students"].field_map = {"EnrollStatus": FieldEnrollStatus()}
        assert config.get_raw_field_map("Students")["EnrollStatus"] == {"status_column": None}

    def test_the_round_trip_covers_every_variant(self):
        assert {type(field) for field in ROUND_TRIP_FIELDS} == set(FIELD_VARIANTS)


# --------------------------------------------------------------------------- #
# The no-shape branch of classify_field                                         #
# --------------------------------------------------------------------------- #
class TestADictThatNamesNoShapeIsRefused:
    @pytest.mark.parametrize("raw", [{}, {"sanitize": True}], ids=["empty", "sanitize-only"])
    def test_refused_listing_keys_only(self, raw: dict[str, Any]):
        with pytest.raises(ValueError, match="names no mapping shape") as caught:
            classify_field(raw)
        assert f"the field mapping {sorted(raw)} names no mapping shape" in str(caught.value)
        assert "True" not in str(caught.value)  # the key list, never a value

    def test_twin_the_same_key_beside_a_shape_classifies(self):
        field = classify_field({"format": "{student number}.x", "sanitize": True})
        assert isinstance(field, FieldEmailFormat) and field.sanitize


# --------------------------------------------------------------------------- #
# Config VALUES never reach a warning or a refusal (keys + suggestions only)    #
# --------------------------------------------------------------------------- #
SENTINEL = "SENTINELvalue_Jane_Doe_123"
_GLOBAL_SENTINEL = _overlay(global_config={"enabled_entites": SENTINEL})
_FIELD_SENTINEL = _overlay(mappings={"Students": {"field_map": {"Grade": {"column": "Grade", "transfrom": SENTINEL}}}})

VALUE_PRIVACY_CASES = [
    pytest.param("user", _GLOBAL_SENTINEL, "enabled_entites", "enabled_entities", id="user-global"),
    pytest.param(
        "user", _overlay(mappings={"Family": {"row_filtr": SENTINEL}}), "row_filtr", "row_filters", id="user-entity"
    ),
    pytest.param(
        "user",
        _overlay(mappings={"Staff": {"source_columns": {"staff_stauts": SENTINEL}}}),
        "staff_stauts",
        "staff_status",
        id="user-role",
    ),
    pytest.param("bundled", _GLOBAL_SENTINEL, "enabled_entites", "enabled_entities", id="bundled-global"),
    pytest.param("user", _FIELD_SENTINEL, "transfrom", "transform", id="user-field-extra"),
    pytest.param("authoring", _GLOBAL_SENTINEL, "enabled_entites", "enabled_entities", id="authoring-global"),
    pytest.param("authoring", _FIELD_SENTINEL, "transfrom", "transform", id="authoring-field-extra"),
]


class TestValuesNeverReachTheMessage:
    @pytest.mark.parametrize(("where", "body", "key", "suggestion"), VALUE_PRIVACY_CASES)
    def test_the_sentinel_is_absent_and_the_key_is_present(
        self,
        where: str,
        body: dict[str, Any],
        key: str,
        suggestion: str,
        bundled_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        texts: list[str] = []
        with caplog.at_level(logging.DEBUG):
            try:
                if where == "authoring":
                    validate_overlay(copy.deepcopy(body))
                elif where == "bundled":
                    _write(bundled_dir, body)
                    load_config(SIS)
                else:
                    _write(user_mappings_dir(), body)
                    load_config(SIS)
            except ValueError as exc:
                texts.append(str(exc))
        texts.extend(record.getMessage() for record in caplog.records)
        assert all(SENTINEL not in text for text in texts)
        # Positive twin: the message that WAS produced names the key and its suggestion.
        assert any(key in text and suggestion in text for text in texts)

    @pytest.mark.parametrize(
        "raw",
        [{"colum": SENTINEL}, {"column": "Grade", "transfrom": SENTINEL}],
        ids=["no-shape-unknown-key", "variant-extra"],
    )
    def test_classify_field_refusals_carry_no_value(self, raw: dict[str, Any]):
        with pytest.raises(ValueError) as caught:
            classify_field(raw)
        assert SENTINEL not in str(caught.value)
        assert "unknown key" in str(caught.value)
