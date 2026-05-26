"""Tests for configuration validation and loading."""

import pytest
import yaml
from pydantic import ValidationError

from src.config.loader import _deep_merge, load_config
from src.config.models import (
    EntityConfig,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    GlobalConfig,
    MappingConfig,
    classify_field,
)


# -----------------------------------------------------------------------
# classify_field
# -----------------------------------------------------------------------
class TestClassifyField:
    def test_none(self):
        assert classify_field(None) is None

    def test_string(self):
        assert classify_field("Student Number") == "Student Number"

    def test_fixed_value(self):
        result = classify_field({"value": ""})
        assert isinstance(result, FieldFixedValue)
        assert result.value == ""

    def test_transform(self):
        result = classify_field({"column": "Grade", "transform": "grade_to_ceds"})
        assert isinstance(result, FieldTransform)
        assert result.column == "Grade"
        assert result.transform == "grade_to_ceds"

    def test_column_only(self):
        result = classify_field({"column": "School Number"})
        assert isinstance(result, FieldTransform)
        assert result.transform == ""

    def test_academic_year(self):
        result = classify_field({"use_academic_year": True})
        assert isinstance(result, FieldAcademicYear)

    def test_academic_year_with_override(self):
        result = classify_field({"use_academic_year": False, "value": "2025-08-25"})
        assert isinstance(result, FieldAcademicYear)
        assert result.value == "2025-08-25"

    def test_academic_year_false_without_value_raises(self):
        with pytest.raises(ValueError):
            classify_field({"use_academic_year": False})

    def test_append_year(self):
        result = classify_field({"column": "Master Timetable ID", "append_year_to_id": True})
        assert isinstance(result, FieldAppendYear)
        assert result.column == "Master Timetable ID"

    def test_email_format(self):
        result = classify_field({"format": "{student number}@sd51.bc.ca"})
        assert isinstance(result, FieldEmailFormat)
        assert result.format == "{student number}@sd51.bc.ca"

    def test_name_config(self):
        raw = {
            "primary teacher flag": "Primary Teacher",
            "teacher last name": "Teacher Name",
            "course title": "Course Title",
            "section letter": "Section Letter",
        }
        result = classify_field(raw)
        assert isinstance(result, FieldNameConfig)
        assert result.course_title == "Course Title"

    def test_id_role_pair(self):
        raw = {"student_id_col": "Student ID", "staff_id_col": "Teacher ID"}
        result = classify_field(raw)
        assert isinstance(result, FieldIdRolePair)
        assert result.student_id_col == "Student ID"

    def test_numeric_coerced_to_string(self):
        result = classify_field(42)
        assert result == "42"


# -----------------------------------------------------------------------
# EntityConfig
# -----------------------------------------------------------------------
class TestEntityConfig:
    def test_basic_entity(self):
        cfg = EntityConfig(
            source_files={"student_demographic": "StudentDemo.txt"},
            field_map={"User ID": "Student Number", "Grade": {"column": "Grade", "transform": "grade_to_ceds"}},
        )
        assert cfg.source_files["student_demographic"] == "StudentDemo.txt"
        assert isinstance(cfg.field_map["Grade"], FieldTransform)

    def test_legacy_list_of_strings_coerced(self):
        cfg = EntityConfig(
            source_files=["StudentSchedule.txt", "CourseInfo.txt"],
            field_map={"Class ID": "mt_id"},
        )
        assert cfg.source_files["student_schedule"] == "StudentSchedule.txt"
        assert cfg.source_files["course_info"] == "CourseInfo.txt"

    def test_legacy_list_of_dicts_coerced(self):
        cfg = EntityConfig(
            source_files=[
                {"role": "student_schedule", "file": "Schedule.txt"},
                {"role": "course_info", "file": "Course.txt"},
            ],
            field_map={"Name": "title"},
        )
        assert cfg.source_files["student_schedule"] == "Schedule.txt"


# -----------------------------------------------------------------------
# GlobalConfig
# -----------------------------------------------------------------------
class TestGlobalConfig:
    def test_defaults(self):
        cfg = GlobalConfig()
        assert cfg.school_year_sources == {}
        assert cfg.homeroom_grades == []

    def test_from_none(self):
        cfg = GlobalConfig.model_validate(None)
        assert cfg.homeroom_grades == []

    def test_with_data(self):
        cfg = GlobalConfig(
            school_year_sources={"student_schedule": "Schedule.txt"},
            homeroom_grades=["KG", "01", "02"],
        )
        assert len(cfg.homeroom_grades) == 3


# -----------------------------------------------------------------------
# MappingConfig
# -----------------------------------------------------------------------
class TestMappingConfig:
    def _minimal_config(self, **overrides):
        base = {
            "version": "1.9",
            "sis": "test",
            "mappings": {
                "Students": {
                    "source_files": {"student_demographic": "Demo.txt"},
                    "field_map": {"User ID": "Student Number"},
                },
            },
        }
        base.update(overrides)
        return base

    def test_minimal_valid(self):
        cfg = MappingConfig(**self._minimal_config())
        assert cfg.sis == "test"
        assert "Students" in cfg.mappings

    def test_missing_version_raises(self):
        data = self._minimal_config()
        del data["version"]
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_missing_sis_raises(self):
        data = self._minimal_config()
        del data["sis"]
        with pytest.raises(ValidationError):
            MappingConfig(**data)

    def test_missing_mappings_raises(self):
        with pytest.raises(ValidationError):
            MappingConfig(version="1.0", sis="test")

    def test_get_entity(self):
        cfg = MappingConfig(**self._minimal_config())
        assert cfg.get_entity("Students") is not None
        assert cfg.get_entity("Nonexistent") is None

    def test_get_raw_field_map_roundtrip(self):
        cfg = MappingConfig(**self._minimal_config())
        raw = cfg.get_raw_field_map("Students")
        assert raw["User ID"] == "Student Number"

    def test_numeric_version(self):
        cfg = MappingConfig(**self._minimal_config(version=1.9))
        assert cfg.version == 1.9


# -----------------------------------------------------------------------
# Deep merge
# -----------------------------------------------------------------------
class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_new_key_added(self):
        base = {"a": 1}
        override = {"b": 2}
        assert _deep_merge(base, override) == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"x": {"a": 1}}
        _deep_merge(base, {"x": {"b": 2}})
        assert base == {"x": {"a": 1}}


# -----------------------------------------------------------------------
# load_config against real YAML files
# -----------------------------------------------------------------------
class TestLoadConfig:
    @pytest.mark.parametrize(
        "sis_type",
        ["myedbc", "sd40myedbc", "sd48myedbc", "sd51myedbc", "sd74myedbc"],
    )
    def test_all_standard_configs_valid(self, sis_type):
        cfg = load_config(sis_type)
        assert cfg.sis == "MyEducationBC"
        assert "Students" in cfg.mappings
        assert len(cfg.global_config.homeroom_grades) > 0

    def test_myblueprint_plus_config(self):
        cfg = load_config("myBlueprint+")
        assert "Students" in cfg.mappings
        assert "CourseInfo" in cfg.mappings
        assert "StudentCourses" in cfg.mappings

    def test_myblueprint_plus_minimal_config(self):
        cfg = load_config("myBlueprint+_minimal")
        assert "Students" in cfg.mappings
        assert "CourseInfo" in cfg.mappings
        assert "StudentCourses" in cfg.mappings

    def test_nonexistent_config_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("nonexistent_sis")


# -----------------------------------------------------------------------
# Config inheritance
# -----------------------------------------------------------------------
class TestConfigInheritance:
    def test_inheritance_merges_base(self, tmp_path):
        # Write a base config
        base = {
            "version": "1.0",
            "sis": "base",
            "global_config": {
                "homeroom_grades": ["KG", "01"],
            },
            "mappings": {
                "Students": {
                    "source_files": {"student_demographic": "Demo.txt"},
                    "field_map": {"User ID": "Student Number", "Grade": "Grade"},
                },
            },
        }
        (tmp_path / "base_mapping.yaml").write_text(yaml.dump(base))

        # Write a child config that inherits and overrides
        child = {
            "_base": "base",
            "sis": "district42",
            "mappings": {
                "Students": {
                    "source_files": {"student_demographic": "CustomDemo.txt"},
                    "field_map": {"User ID": "Student Number", "Grade": "Grade"},
                },
            },
        }
        (tmp_path / "district42_mapping.yaml").write_text(yaml.dump(child))

        cfg = load_config("district42", config_dir=tmp_path)
        assert cfg.sis == "district42"
        # Source file overridden
        assert cfg.mappings["Students"].source_files["student_demographic"] == "CustomDemo.txt"
        # Global config inherited from base
        assert cfg.global_config.homeroom_grades == ["KG", "01"]

    def test_missing_base_raises(self, tmp_path):
        child = {
            "_base": "nonexistent",
            "version": "1.0",
            "sis": "bad",
            "mappings": {},
        }
        (tmp_path / "bad_mapping.yaml").write_text(yaml.dump(child))

        with pytest.raises(FileNotFoundError, match="nonexistent"):
            load_config("bad", config_dir=tmp_path)


# -----------------------------------------------------------------------
# District config equivalence — verifies _base inheritance resolves correctly
# -----------------------------------------------------------------------
class TestDistrictConfigEquivalence:
    """Verify each district config resolves to the expected values after inheritance."""

    def test_sd48_source_files(self):
        cfg = load_config("sd48myedbc")
        assert cfg.mappings["Students"].source_files["student_demographic"] == "StudentDemographicEnhanced.txt"
        assert cfg.mappings["Staff"].source_files["staff_info"] == "StaffInformation.txt"
        assert cfg.mappings["Classes"].source_files["staff_info"] == "StaffInformation.txt"
        assert cfg.mappings["Classes"].source_files["student_demographic"] == "StudentDemographicEnhanced.txt"
        assert cfg.mappings["Enrollments"].source_files["student_demographic"] == "StudentDemographicEnhanced.txt"

    def test_sd48_inherits_base_field_maps(self):
        cfg = load_config("sd48myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        # Should inherit myedbc field mappings exactly
        assert students_fm["User ID"] == "Student Number"
        assert students_fm["First Name"] == "Legal First Name"
        assert students_fm["Email Address"] == "Student email address"

    def test_sd51_custom_email(self):
        cfg = load_config("sd51myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        assert students_fm["Email Address"] == {"format": "{student number}@sd51.bc.ca"}

    def test_sd51_fixed_dates(self):
        cfg = load_config("sd51myedbc")
        classes_fm = cfg.get_raw_field_map("Classes")
        assert classes_fm["Start Date"] == {"value": "2025-08-25", "use_academic_year": False}
        assert classes_fm["End Date"] == {"value": "2026-07-25", "use_academic_year": False}

    def test_sd74_different_schedule_file(self):
        cfg = load_config("sd74myedbc")
        assert cfg.global_config.school_year_sources["student_schedule"] == "studentcourseselection.txt"
        assert cfg.mappings["Classes"].source_files["student_schedule"] == "studentcourseselection.txt"
        assert cfg.mappings["Enrollments"].source_files["student_schedule"] == "studentcourseselection.txt"

    def test_sd74_swapped_name_columns(self):
        cfg = load_config("sd74myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        assert students_fm["First Name"] == "Usual first name"
        assert students_fm["Last Name"] == "Usual surname"
        assert students_fm["Preferred First Name"] == "Legal first name"
        assert students_fm["Preferred Last Name"] == "Legal surname"

    def test_sd74_family_source(self):
        cfg = load_config("sd74myedbc")
        assert cfg.mappings["Family"].source_files["emergency_contacts"] == "ParentInformation.txt"
        family_fm = cfg.get_raw_field_map("Family")
        assert family_fm["Last Name"] == "Surname"

    def test_sd74_class_name_config(self):
        cfg = load_config("sd74myedbc")
        classes_fm = cfg.get_raw_field_map("Classes")
        name_cfg = classes_fm["Name"]
        assert name_cfg["primary teacher flag"] == ""
        assert name_cfg["course title"] == "Title"
        assert name_cfg["section letter"] == "Section"

    def test_all_districts_have_five_entities(self):
        for sis in ("sd48myedbc", "sd51myedbc", "sd74myedbc"):
            cfg = load_config(sis)
            for entity in ("Students", "Staff", "Family", "Classes", "Enrollments"):
                assert entity in cfg.mappings, f"{sis} missing {entity}"


# -----------------------------------------------------------------------
# CourseInfo / StudentCourses global_config fields + enabled_entities
# -----------------------------------------------------------------------
class TestMyBlueprintPlusGlobalConfig:
    """Verify global_config fields supporting the CourseInfo / StudentCourses entities."""

    def test_defaults_empty(self):
        cfg = GlobalConfig()
        assert cfg.excluded_course_code_patterns == []
        assert cfg.excluded_course_flavors == []
        assert cfg.enabled_entities == []

    def test_accepts_values(self):
        cfg = GlobalConfig(
            excluded_course_code_patterns=["^.{5}-K", r"^.{5}0\d", "^X", "^ATT"],
            excluded_course_flavors=["HUB", "HOL", "DL", "---"],
            enabled_entities=["Students", "CourseInfo", "StudentCourses"],
        )
        assert cfg.excluded_course_code_patterns == ["^.{5}-K", r"^.{5}0\d", "^X", "^ATT"]
        assert cfg.excluded_course_flavors == ["HUB", "HOL", "DL", "---"]
        assert cfg.enabled_entities == ["Students", "CourseInfo", "StudentCourses"]

    def test_invalid_regex_rejected_at_load(self):
        with pytest.raises(ValidationError, match="Invalid regex"):
            GlobalConfig(excluded_course_code_patterns=["^[unterminated"])

    def test_roundtrip_via_to_raw_dict(self):
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            global_config=GlobalConfig(
                excluded_course_code_patterns=["^X", "^ATT"],
                excluded_course_flavors=["HUB", "DL"],
                enabled_entities=["Students", "Staff"],
            ),
            mappings={
                "Students": EntityConfig(
                    source_files={"student_demographic": "Demo.txt"},
                    field_map={"User ID": "Student Number"},
                ),
            },
        )
        raw = cfg.to_raw_dict()
        assert raw["global_config"]["excluded_course_code_patterns"] == ["^X", "^ATT"]
        assert raw["global_config"]["excluded_course_flavors"] == ["HUB", "DL"]
        assert raw["global_config"]["enabled_entities"] == ["Students", "Staff"]

    def test_roundtrip_defaults_when_unset(self):
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            mappings={
                "Students": EntityConfig(
                    source_files={"student_demographic": "Demo.txt"},
                    field_map={"User ID": "Student Number"},
                ),
            },
        )
        raw = cfg.to_raw_dict()
        assert raw["global_config"]["excluded_course_code_patterns"] == []
        assert raw["global_config"]["excluded_course_flavors"] == []
        assert raw["global_config"]["enabled_entities"] == []

    def test_base_myedbc_carries_patterns_and_flavors(self):
        """Patterns + flavors are MyEd BC conventions — they live in the base config
        so any inheriting district that enables CourseInfo / StudentCourses gets them
        for free."""
        cfg = load_config("myedbc")
        assert cfg.global_config.excluded_course_code_patterns == [
            "^.{5}-K",
            r"^.{5}0\d",
            "^X",
            "^ATT",
        ]
        assert cfg.global_config.excluded_course_flavors == ["HUB", "HOL", "DL", "---"]

    def test_yaml_load_with_new_fields(self, tmp_path):
        """End-to-end: YAML with the new fields parses and validates."""
        yaml_text = """
version: "1.9"
sis: test
global_config:
  excluded_course_code_patterns:
    - "^.{5}-K"
    - "^.{5}0\\\\d"
    - "^X"
    - "^ATT"
  excluded_course_flavors: ["HUB", "HOL", "DL", "---"]
  enabled_entities: ["Students", "CourseInfo"]
mappings:
  Students:
    source_files:
      student_demographic: "Demo.txt"
    field_map:
      "User ID": "Student Number"
"""
        (tmp_path / "test_mapping.yaml").write_text(yaml_text)
        cfg = load_config("test", config_dir=tmp_path)
        assert cfg.global_config.excluded_course_code_patterns == ["^.{5}-K", r"^.{5}0\d", "^X", "^ATT"]
        assert cfg.global_config.excluded_course_flavors == ["HUB", "HOL", "DL", "---"]
        assert cfg.global_config.enabled_entities == ["Students", "CourseInfo"]


# -----------------------------------------------------------------------
# enabled_entities behavior
# -----------------------------------------------------------------------
class TestEnabledEntities:
    """`enabled_entities` controls which mappings the pipeline actually produces."""

    def test_base_myedbc_enables_only_rostering(self):
        """The base config defines 7 entity templates but enables only the 5 rostering ones."""
        cfg = load_config("myedbc")
        assert set(cfg.mappings.keys()) >= {
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
            "CourseInfo",
            "StudentCourses",
        }
        assert cfg.global_config.enabled_entities == [
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
        ]

    def test_myblueprintplus_enables_all_seven(self):
        cfg = load_config("myBlueprint+")
        assert set(cfg.global_config.enabled_entities) == {
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
            "CourseInfo",
            "StudentCourses",
        }

    def test_myblueprintplus_minimal_excludes_rostering(self):
        cfg = load_config("myBlueprint+_minimal")
        assert cfg.global_config.enabled_entities == [
            "Students",
            "CourseInfo",
            "StudentCourses",
        ]

    def test_district_configs_inherit_rostering_default(self):
        """sd40/48/51/74 inherit `enabled_entities` from the base — still the 5 rostering entities."""
        for sis in ("sd40myedbc", "sd48myedbc", "sd51myedbc", "sd74myedbc"):
            cfg = load_config(sis)
            assert cfg.global_config.enabled_entities == [
                "Students",
                "Staff",
                "Family",
                "Classes",
                "Enrollments",
            ], f"{sis} should still produce only the 5 rostering CSVs"
