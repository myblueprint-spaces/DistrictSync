"""Tests for configuration validation and loading."""

import pytest
import yaml
from pydantic import ValidationError

from src.config.loader import _deep_merge, load_config
from src.config.models import (
    CrossEnrollmentConfig,
    EmailDerivedDate,
    EntityConfig,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldEnrollStatus,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    GlobalConfig,
    MappingConfig,
    RowFilter,
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

    def test_enroll_status_null_is_sentinel(self):
        """Bare-null EnrollStatus stays the auto-detect sentinel (None)."""
        assert classify_field(None) is None

    def test_enroll_status_dict_validates(self):
        """A dict with any active-detection key classifies as FieldEnrollStatus."""
        result = classify_field(
            {
                "status_column": "Status",
                "withdraw_date_column": "Left On",
                "active_values": ["Active", "PreReg", "Active No Primary"],
            }
        )
        assert isinstance(result, FieldEnrollStatus)
        assert result.status_column == "Status"
        assert result.withdraw_date_column == "Left On"
        assert result.active_values == ["Active", "PreReg", "Active No Primary"]

    def test_enroll_status_partial_dict_validates(self):
        """A partial dict (one key) still classifies; absent keys stay None."""
        result = classify_field({"active_values": ["Active"]})
        assert isinstance(result, FieldEnrollStatus)
        assert result.active_values == ["Active"]
        assert result.status_column is None
        assert result.withdraw_date_column is None

    def test_enroll_status_unknown_key_raises(self):
        """An unknown/typo'd EnrollStatus key fails loudly (extra='forbid').

        Closes the prior bug where a recognizable-but-malformed EnrollStatus
        dict only warned and passed through. A dict routed into the branch by a
        valid key (``active_values``) that ALSO carries an unknown key
        (``withdraw_colum`` typo) must raise.
        """
        with pytest.raises(ValidationError):
            classify_field({"active_values": ["Active"], "withdraw_colum": "Left"})


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

    def test_enroll_status_null_field_map(self):
        """EnrollStatus: null in an entity field_map stays the None sentinel."""
        cfg = EntityConfig(
            source_files={"student_demographic": "Demo.txt"},
            field_map={"User ID": "Student Number", "EnrollStatus": None},
        )
        assert cfg.field_map["EnrollStatus"] is None

    def test_enroll_status_dict_field_map_roundtrips(self):
        """An EnrollStatus override dict survives validation + raw round-trip.

        Built from a raw dict (the real `load_config` path: raw YAML →
        MappingConfig), so the value classifies to FieldEnrollStatus and the
        transformer pipeline receives the raw dict back via get_raw_field_map.
        """
        cfg = MappingConfig(
            **{
                "version": "1.9",
                "sis": "test",
                "mappings": {
                    "Students": {
                        "source_files": {"student_demographic": "Demo.txt"},
                        "field_map": {
                            "User ID": "Student Number",
                            "EnrollStatus": {"status_column": "Status", "active_values": ["Active"]},
                        },
                    },
                },
            }
        )
        assert isinstance(cfg.mappings["Students"].field_map["EnrollStatus"], FieldEnrollStatus)
        raw = cfg.get_raw_field_map("Students")
        assert raw["EnrollStatus"] == {"status_column": "Status", "active_values": ["Active"]}

    def test_enroll_status_malformed_field_map_raises(self):
        """A recognizable EnrollStatus override with a typo'd key fails loudly
        when the entity field_map is validated (extra='forbid')."""
        with pytest.raises(ValidationError):
            EntityConfig(
                source_files={"student_demographic": "Demo.txt"},
                field_map={"EnrollStatus": {"status_column": "Status", "withdraw_colum": "Left"}},
            )


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
        ["myedbc", "sd40myedbc", "sd48myedbc", "sd51myedbc", "sd60myedbc", "sd74myedbc"],
    )
    def test_all_standard_configs_valid(self, sis_type):
        cfg = load_config(sis_type)
        assert cfg.sis == "MyEducationBC"
        assert "Students" in cfg.mappings
        assert len(cfg.global_config.homeroom_grades) > 0

    def test_mbp_all_config(self):
        cfg = load_config("mbp_all")
        assert "Students" in cfg.mappings
        assert "CourseInfo" in cfg.mappings
        assert "StudentCourses" in cfg.mappings

    def test_mbp_core_config(self):
        cfg = load_config("mbp_core")
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

    def test_sd51_inherits_auto_dates(self):
        """SD51 no longer hardcodes Start/End Date — auto-detection from end-year
        convention produces the correct academic period (2025-2026 for "2026"
        school year). The override was a workaround for the now-fixed
        start-year/end-year bug in context.set_school_year.
        """
        cfg = load_config("sd51myedbc")
        classes_fm = cfg.get_raw_field_map("Classes")
        # Inherited from myedbc base, which uses auto-detection.
        assert classes_fm["Start Date"] == {"use_academic_year": True}
        assert classes_fm["End Date"] == {"use_academic_year": True}

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
        for sis in ("sd48myedbc", "sd51myedbc", "sd60myedbc", "sd74myedbc"):
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

    def test_course_start_grade_default(self):
        assert GlobalConfig().course_start_grade == 10

    @pytest.mark.parametrize("grade", [8, 9, 10])
    def test_course_start_grade_accepts_valid(self, grade):
        assert GlobalConfig(course_start_grade=grade).course_start_grade == grade

    @pytest.mark.parametrize("grade", [7, 11, 0, 13])
    def test_course_start_grade_rejects_out_of_range(self, grade):
        with pytest.raises(ValidationError, match="course_start_grade"):
            GlobalConfig(course_start_grade=grade)

    def test_course_start_grade_roundtrip_via_to_raw_dict(self):
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            global_config=GlobalConfig(course_start_grade=8),
            mappings={
                "Students": EntityConfig(
                    source_files={"student_demographic": "Demo.txt"},
                    field_map={"User ID": "Student Number"},
                ),
            },
        )
        assert cfg.to_raw_dict()["global_config"]["course_start_grade"] == 8

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
        # The numeric early-grade pattern is no longer hard-coded — it is derived
        # from course_start_grade at transform time (default grades 10-12).
        assert cfg.global_config.excluded_course_code_patterns == [
            "^.{5}-K",
            "^X",
            "^ATT",
        ]
        assert cfg.global_config.excluded_course_flavors == ["HUB", "HOL", "DL", "---"]
        assert cfg.global_config.course_start_grade == 10

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

    def test_mbp_all_enables_all_seven(self):
        cfg = load_config("mbp_all")
        assert set(cfg.global_config.enabled_entities) == {
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
            "CourseInfo",
            "StudentCourses",
        }

    def test_mbp_core_excludes_rostering(self):
        cfg = load_config("mbp_core")
        assert cfg.global_config.enabled_entities == [
            "Students",
            "CourseInfo",
            "StudentCourses",
        ]

    def test_mbponly_enables_courses_only(self):
        cfg = load_config("mbponly")
        assert cfg.global_config.enabled_entities == [
            "CourseInfo",
            "StudentCourses",
        ]

    def test_district_configs_inherit_rostering_default(self):
        """sd40/48/74 inherit `enabled_entities` from the base — still the 5 rostering entities.

        SD51 is excluded here because it opts into StudentAttendance (its own
        full enabled_entities list, since deep-merge replaces lists) — see
        ``test_sd51_enables_student_attendance``.
        """
        for sis in ("sd40myedbc", "sd48myedbc", "sd60myedbc", "sd74myedbc"):
            cfg = load_config(sis)
            assert cfg.global_config.enabled_entities == [
                "Students",
                "Staff",
                "Family",
                "Classes",
                "Enrollments",
            ], f"{sis} should still produce only the 5 rostering CSVs"

    def test_sd51_enables_student_attendance(self):
        """SD51 lists the full set (base 5 rostering + opt-in StudentAttendance).

        Deep-merge REPLACES lists, so SD51 must restate the rostering entities
        alongside StudentAttendance or they would vanish.
        """
        cfg = load_config("sd51myedbc")
        assert cfg.global_config.enabled_entities == [
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
            "StudentAttendance",
        ]


# -----------------------------------------------------------------------
# RowFilter (config-driven row inclusion) + CrossEnrollmentConfig
# -----------------------------------------------------------------------
class TestRowFilter:
    def test_basic(self):
        rf = RowFilter(column="Parent Auth / Guardian", include=["Y"])
        assert rf.column == "Parent Auth / Guardian"
        assert rf.include == ["Y"]

    def test_default_include_empty(self):
        assert RowFilter(column="Guardian").include == []

    def test_unknown_key_rejected(self):
        """A typo'd key fails loudly (extra='forbid')."""
        with pytest.raises(ValidationError):
            RowFilter(column="Guardian", includ=["Y"])

    def test_entity_row_filters_roundtrip_via_to_raw_dict(self):
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            mappings={
                "Family": EntityConfig(
                    source_files={"emergency_contacts": "E.txt"},
                    field_map={"First Name": "First Name"},
                    row_filters=[RowFilter(column="Parent Auth / Guardian", include=["Y"])],
                ),
            },
        )
        raw = cfg.to_raw_dict()
        assert raw["mappings"]["Family"]["row_filters"] == [{"column": "Parent Auth / Guardian", "include": ["Y"]}]

    def test_entity_row_filters_absent_key_when_unset(self):
        """No row_filters → the key is omitted (back-compatible raw dict)."""
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            mappings={
                "Family": EntityConfig(
                    source_files={"emergency_contacts": "E.txt"},
                    field_map={"First Name": "First Name"},
                ),
            },
        )
        assert "row_filters" not in cfg.to_raw_dict()["mappings"]["Family"]

    def test_yaml_row_filter_parses_into_typed_model(self, tmp_path):
        yaml_text = """
version: "1.9"
sis: test
mappings:
  Family:
    source_files:
      emergency_contacts: E.txt
    field_map:
      "First Name": "First Name"
    row_filters:
      - column: "Parent Auth / Guardian"
        include: ["Y"]
"""
        (tmp_path / "test_mapping.yaml").write_text(yaml_text)
        cfg = load_config("test", config_dir=tmp_path)
        rf = cfg.mappings["Family"].row_filters
        assert len(rf) == 1
        assert isinstance(rf[0], RowFilter)
        assert rf[0].column == "Parent Auth / Guardian"


class TestCrossEnrollmentConfig:
    def test_defaults(self):
        cc = CrossEnrollmentConfig()
        assert cc.collapse is False
        assert cc.home_school_column == ""

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            CrossEnrollmentConfig(collapse=True, home_col="Home school number")

    def test_global_default_none(self):
        assert GlobalConfig().cross_enrollment is None

    def test_roundtrip_via_to_raw_dict(self):
        cfg = MappingConfig(
            version="1.9",
            sis="test",
            global_config=GlobalConfig(
                cross_enrollment=CrossEnrollmentConfig(collapse=True, home_school_column="Home school number"),
            ),
            mappings={
                "Students": EntityConfig(
                    source_files={"student_demographic": "Demo.txt"},
                    field_map={"User ID": "Student Number"},
                ),
            },
        )
        raw = cfg.to_raw_dict()
        assert raw["global_config"]["cross_enrollment"] == {
            "collapse": True,
            "home_school_column": "Home school number",
        }

    def test_roundtrip_none_when_unset(self):
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
        assert cfg.to_raw_dict()["global_config"]["cross_enrollment"] is None


# -----------------------------------------------------------------------
# SD60 config — guardians-only Family + cross-enrollment collapse
# -----------------------------------------------------------------------
class TestSD60Config:
    def test_valid_and_rostering_entities(self):
        cfg = load_config("sd60myedbc")
        assert cfg.sis == "MyEducationBC"
        for entity in ("Students", "Staff", "Family", "Classes", "Enrollments"):
            assert entity in cfg.mappings
        assert cfg.global_config.enabled_entities == [
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
        ]

    def test_family_carries_guardian_row_filter(self):
        cfg = load_config("sd60myedbc")
        raw = cfg.to_raw_dict()
        assert raw["mappings"]["Family"]["row_filters"] == [{"column": "Parent Auth / Guardian", "include": ["Y"]}]

    def test_cross_enrollment_collapse_enabled(self):
        cfg = load_config("sd60myedbc")
        cc = cfg.global_config.cross_enrollment
        assert cc is not None
        assert cc.collapse is True
        assert cc.home_school_column == "Home school number"

    def test_active_no_primary_dropped(self):
        """SD60 no longer retains "Active No Primary" (plan 0030).

        The EnrollStatus override was removed, so SD60 inherits the base
        ``null`` sentinel → default ``active_values=["Active","PreReg"]``. The
        bare ``null`` round-trips to ``None`` via ``get_raw_field_map`` (no
        ``active_values`` list at all), so ANP is definitively absent.
        """
        cfg = load_config("sd60myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        assert students_fm["EnrollStatus"] is None
        assert "Active No Primary" not in repr(students_fm["EnrollStatus"])

    def test_school_code_maps_to_home_school_number(self):
        """SD60 rosters every student under their home school (plan 0030)."""
        cfg = load_config("sd60myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        assert students_fm["SchoolCode"] == "Home school number"

    def test_email_generation_round_trip(self):
        """SD60 email is generated with sanitize + a derived 2-digit admission year.

        The round-trip must emit PLAIN nested dicts (not model instances) so the
        transformer's dict-style reads work.
        """
        cfg = load_config("sd60myedbc")
        students_fm = cfg.get_raw_field_map("Students")
        email = students_fm["Email Address"]
        assert email["format"] == "{legal first name}{legal surname}{admission yy}@learn60.ca"
        assert email["sanitize"] is True
        assert email["derived_dates"] == {"admission yy": {"column": "Admission date", "date_format": "yy"}}
        # Plain dicts, not model instances (transformer reads dict-style).
        assert isinstance(email["derived_dates"]["admission yy"], dict)


# -----------------------------------------------------------------------
# FieldEmailFormat / EmailDerivedDate — opt-in email extensions (plan 0030)
# -----------------------------------------------------------------------
class TestEmailFormatModels:
    def test_bare_format_defaults_off(self):
        """A bare ``{"format": ...}`` yields sanitize=False and no derived dates."""
        ef = classify_field({"format": "{student number}@sd51.bc.ca"})
        assert isinstance(ef, FieldEmailFormat)
        assert ef.sanitize is False
        assert ef.derived_dates == {}

    def test_sanitize_and_derived_dates_parse(self):
        ef = classify_field(
            {
                "format": "{legal first name}{legal surname}{admission yy}@learn60.ca",
                "sanitize": True,
                "derived_dates": {"admission yy": {"column": "Admission date", "date_format": "yy"}},
            }
        )
        assert isinstance(ef, FieldEmailFormat)
        assert ef.sanitize is True
        assert ef.derived_dates["admission yy"].column == "Admission date"
        assert ef.derived_dates["admission yy"].date_format == "yy"

    def test_field_email_format_forbids_extra_key(self):
        with pytest.raises(ValidationError):
            FieldEmailFormat(format="{x}@y.ca", saintize=True)  # typo'd key

    def test_derived_date_forbids_extra_key(self):
        with pytest.raises(ValidationError):
            EmailDerivedDate(column="Admission date", date_format="yy", colunm="typo")

    def test_derived_date_rejects_empty_column(self):
        with pytest.raises(ValidationError):
            EmailDerivedDate(column="", date_format="yy")

    def test_derived_date_rejects_empty_date_format(self):
        with pytest.raises(ValidationError):
            EmailDerivedDate(column="Admission date", date_format="")
