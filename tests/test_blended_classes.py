"""Tests for blended class detection, validation, naming, and grade range."""

import inspect
import logging

import pandas as pd

import src.etl.transformers.blended as blended_module
import src.etl.transformers.grades as grades_module
from src.etl.transformer import DataTransformer
from src.etl.transformers.blended import BlendedClassDetector


class TestValidateBlendedClass:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_valid_blend_two_grades(self):
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        grade_map = {"MT1": "1", "MT2": "2"}
        assert self.transformer._validate_blended_class(group, grade_map) is True

    def test_invalid_single_record(self):
        group = pd.DataFrame({"master timetable id": ["MT1"]})
        grade_map = {"MT1": "1"}
        assert self.transformer._validate_blended_class(group, grade_map) is False

    def test_invalid_same_grade(self):
        """Two records but same grade — not a valid blend."""
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        grade_map = {"MT1": "5", "MT2": "5"}
        assert self.transformer._validate_blended_class(group, grade_map) is False

    def test_valid_three_grades(self):
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2", "MT3"]})
        grade_map = {"MT1": "1", "MT2": "2", "MT3": "3"}
        assert self.transformer._validate_blended_class(group, grade_map) is True

    def test_missing_grade_in_map(self):
        """MT IDs not in grade map should be ignored."""
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        grade_map = {"MT1": "1"}  # MT2 missing
        assert self.transformer._validate_blended_class(group, grade_map) is False


class TestGetBlendedGradeRange:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_sorted_numeric_grades(self):
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2", "MT3"]})
        grade_map = {"MT1": "3", "MT2": "1", "MT3": "2"}
        result = self.transformer._get_blended_grade_range(group, grade_map)
        assert result == "01/02/03"

    def test_non_numeric_grades_sorted_alphabetically(self):
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        grade_map = {"MT1": "K", "MT2": "1"}
        result = self.transformer._get_blended_grade_range(group, grade_map)
        # KG and 01 can't both be int-sorted, falls to string sort
        assert "01" in result
        assert "KG" in result

    def test_empty_when_no_grades(self):
        group = pd.DataFrame({"master timetable id": ["MT1"]})
        grade_map = {}
        result = self.transformer._get_blended_grade_range(group, grade_map)
        assert result == ""


class TestCreateBlendedClassName:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_full_blended_name(self):
        group = pd.DataFrame(
            {
                "teacher name": ["Adams", "Adams"],
                "course code": ["ENG01", "ENG02"],
                "master timetable id": ["MT1", "MT2"],
            }
        )
        field_map = {"Name": {"teacher last name": "Teacher Name"}}
        course_map = {"ENG01": "English 1", "ENG02": "English 2"}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", course_map)
        assert "Adams" in result
        assert "English 1" in result
        assert "English 2" in result
        assert "01/02" in result
        assert "2025" in result

    def test_spaced_key_name_config_drives_teacher_column(self):
        """The SPACED YAML authoring key ("teacher last name") must drive which
        column supplies the teacher part — pinned with a column name that the
        hardcoded default ("teacher name") would never find."""
        group = pd.DataFrame(
            {
                "instructor": ["Nguyen", "Nguyen"],
                "course code": ["ENG01", "ENG02"],
                "master timetable id": ["MT1", "MT2"],
            }
        )
        field_map = {"Name": {"teacher last name": "Instructor"}}
        course_map = {"ENG01": "English 1", "ENG02": "English 2"}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", course_map)
        assert result == "Nguyen English 1 / English 2 (01/02) 2025"

    def test_fallback_when_no_teacher(self):
        group = pd.DataFrame(
            {
                "course code": ["SCI01", "SCI02"],
                "master timetable id": ["MT1", "MT2"],
            }
        )
        field_map = {"Name": {"teacher last name": "teacher name"}}
        course_map = {"SCI01": "Science 1", "SCI02": "Science 2"}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", course_map)
        assert "Science 1" in result
        assert "2025" in result

    def test_no_teacher_AND_no_course_code_still_names_the_class(self):
        """The partner-visible edge of the course-code guard (plan 0043, slice 1).

        With neither column, `name_parts` is ["(01/02)", "2025"] — length 2, so
        the "Blended Class ..." fallback (which needs <= 1 part) does NOT fire
        and the class ships under this exact name. Pinned because it is the one
        shape where the guard's skip-don't-substitute rule is visible to the
        district, and because a future edit to either guard would change it
        silently.
        """
        group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        field_map = {"Name": {"teacher last name": "teacher name"}}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", {})
        assert result == "(01/02) 2025"


class TestDetectBlendedClasses:
    """Integration test for the full blended class detection pipeline."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_detects_blended_from_class_info(self, class_info_enh_df, blended_schedule_df, blended_course_info_df):
        """Teacher T010 teaches MT100/MT101/MT102 at same time with grades 1,2,3 → blended."""
        raw_data = {
            "StudentSchedule.txt": blended_schedule_df,
            "CourseInformation.txt": blended_course_info_df,
            "ClassInformationEnh.txt": class_info_enh_df,
        }
        mapping = {
            "source_files": {
                "student_schedule": "StudentSchedule.txt",
                "course_info": "CourseInformation.txt",
                "class_info": "ClassInformationEnh.txt",
            },
            "field_map": {
                "Name": {"teacher last name": "Teacher Name"},
            },
        }
        global_config = {
            "mappings": {
                "Enrollments": {
                    "field_map": {
                        "User ID": {"staff_id_col": "Teacher ID"},
                    }
                }
            }
        }

        self.transformer._detect_blended_classes(class_info_enh_df, mapping, raw_data, global_config)

        # MT100, MT101, MT102 should all map to the same blended class
        assert "MT100" in self.transformer.blended_class_map
        assert "MT101" in self.transformer.blended_class_map
        assert "MT102" in self.transformer.blended_class_map
        blended_id = self.transformer.blended_class_map["MT100"]
        assert self.transformer.blended_class_map["MT101"] == blended_id
        assert self.transformer.blended_class_map["MT102"] == blended_id

        # MT103 (different teacher, different day) should NOT be blended
        assert "MT103" not in self.transformer.blended_class_map

        # Metadata should exist
        assert blended_id in self.transformer.blended_class_metadata
        meta = self.transformer.blended_class_metadata[blended_id]
        assert meta["Name"]  # Should have a name
        assert meta["School ID"] == "300"

        # Teacher map should include T010
        assert blended_id in self.transformer.blended_teacher_map
        assert "T010" in self.transformer.blended_teacher_map[blended_id]

    def test_no_blending_when_empty_class_info(self):
        empty_df = pd.DataFrame()
        self.transformer._detect_blended_classes(empty_df, {"source_files": {}, "field_map": {}}, {}, {"mappings": {}})
        assert self.transformer.blended_class_map == {}

    def test_no_blending_single_records(self):
        """Each session has only 1 record — no blending possible."""
        df = pd.DataFrame(
            {
                "school number": ["100", "200"],
                "teacher id": ["T001", "T002"],
                "master timetable id": ["MT001", "MT002"],
                "term": ["1", "1"],
                "semester": ["1", "1"],
                "day": ["1", "2"],
                "period": ["1", "1"],
            }
        )
        raw_data = {
            "StudentSchedule.txt": pd.DataFrame(
                {
                    "master timetable id": ["MT001", "MT002"],
                    "grade": ["5", "6"],
                }
            ),
            "CourseInformation.txt": pd.DataFrame({"course code": [], "title": []}),
        }
        mapping = {
            "source_files": {
                "student_schedule": "StudentSchedule.txt",
                "course_info": "CourseInformation.txt",
            },
            "field_map": {},
        }
        global_config = {"mappings": {"Enrollments": {"field_map": {"User ID": {"staff_id_col": "Teacher ID"}}}}}
        self.transformer._detect_blended_classes(df, mapping, raw_data, global_config)
        assert self.transformer.blended_class_map == {}

    def test_no_blending_for_blank_teacher_rows(self):
        """Sections with no primary teacher must NOT be grouped as blended.

        Regression: SD40 FY2026 had 500+ student-schedule rows per school
        with blank Teacher ID spanning 2-3 grades. Before the fix, these all
        collapsed into a single fake blend with session_key
        '<school>_<blank>_<blank>_<blank>_<blank>_<blank>', producing
        BLENDED class IDs like 'BLENDED_4040016__FY___2026' with empty
        userId enrollment rows that the partner's pre-upload validator
        rejected. A blended class requires a shared TEACHER by definition;
        teacherless sections must be skipped entirely.
        """
        # Two MT IDs at same school, same (empty) time slot, two grades,
        # but BOTH have blank teacher id — must NOT blend.
        df = pd.DataFrame(
            {
                "school number": ["500", "500"],
                "teacher id": ["", ""],
                "master timetable id": ["MT500", "MT501"],
                "term": ["", ""],
                "semester": ["FY", "FY"],
                "day": ["", ""],
                "period": ["", ""],
            }
        )
        raw_data = {
            "StudentSchedule.txt": pd.DataFrame(
                {
                    "master timetable id": ["MT500", "MT501"],
                    "grade": ["6", "7"],
                }
            ),
            "CourseInformation.txt": pd.DataFrame({"course code": [], "title": []}),
        }
        mapping = {
            "source_files": {
                "student_schedule": "StudentSchedule.txt",
                "course_info": "CourseInformation.txt",
            },
            "field_map": {},
        }
        global_config = {"mappings": {"Enrollments": {"field_map": {"User ID": {"staff_id_col": "Teacher ID"}}}}}
        self.transformer._detect_blended_classes(df, mapping, raw_data, global_config)
        assert self.transformer.blended_class_map == {}
        assert self.transformer.blended_class_metadata == {}
        assert self.transformer.blended_teacher_map == {}

    def test_blank_teacher_rows_excluded_from_mixed_batch(self):
        """When some rows have teachers and others don't, blank ones must be
        dropped from session grouping but valid blends must still be detected.
        """
        df = pd.DataFrame(
            {
                "school number": ["500"] * 4,
                "teacher id": ["T001", "T001", "", ""],
                "master timetable id": ["MT500", "MT501", "MT502", "MT503"],
                "course code": ["ENG06", "ENG07", "MAT06", "MAT07"],
                "term": ["1"] * 4,
                "semester": ["FY"] * 4,
                "day": ["1"] * 4,
                "period": ["1"] * 4,
            }
        )
        raw_data = {
            "StudentSchedule.txt": pd.DataFrame(
                {
                    "master timetable id": ["MT500", "MT501", "MT502", "MT503"],
                    "grade": ["6", "7", "6", "7"],
                }
            ),
            "CourseInformation.txt": pd.DataFrame(
                {
                    "course code": ["ENG06", "ENG07", "MAT06", "MAT07"],
                    "title": ["English 6", "English 7", "Math 6", "Math 7"],
                }
            ),
        }
        mapping = {
            "source_files": {
                "student_schedule": "StudentSchedule.txt",
                "course_info": "CourseInformation.txt",
            },
            "field_map": {},
        }
        global_config = {"mappings": {"Enrollments": {"field_map": {"User ID": {"staff_id_col": "Teacher ID"}}}}}
        self.transformer._detect_blended_classes(df, mapping, raw_data, global_config)
        # T001's valid blend of MT500/MT501 should be detected
        assert "MT500" in self.transformer.blended_class_map
        assert "MT501" in self.transformer.blended_class_map
        # MT502/MT503 (teacherless) must NOT be blended
        assert "MT502" not in self.transformer.blended_class_map
        assert "MT503" not in self.transformer.blended_class_map
        # No empty teacher id should end up in blended_teacher_map
        for teachers in self.transformer.blended_teacher_map.values():
            assert "" not in teachers
            assert "nan" not in [str(t).lower() for t in teachers]


# ---------------------------------------------------------------------------
# class_rostering_grades — blend suppression (plan 0042, slice 1a)
# ---------------------------------------------------------------------------
_MAPPING = {
    "source_files": {
        "student_schedule": "StudentSchedule.txt",
        "course_info": "CourseInformation.txt",
        "class_info": "ClassInformationEnh.txt",
    },
    "field_map": {"Name": {"teacher last name": "Teacher Name"}},
}
_ENROLLMENTS_FIELD_MAP = {"mappings": {"Enrollments": {"field_map": {"User ID": {"staff_id_col": "Teacher ID"}}}}}
#: Base MyEd BC homeroom grades — the blend fixture's 01/02/03 sit entirely inside it.
_HOMEROOM_KG_TO_07 = ["IT", "PR", "PK", "TK", "KG", "01", "02", "03", "04", "05", "06", "07"]


class TestBlendSuppressionByRosteringScope:
    """A blend none of whose grades is on the timetable side is not registered.

    Unconditional since plan 0043: the rule keys on the RESOLVED rostered set
    (`grades.timetable_rostered_grades`), which for a district that configured
    no scope is the CEDS complement of `homeroom_grades` — so an all-homeroom
    blend is suppressed for every district, not only for the scoped ones.

    Each case asserts across ALL THREE maps together: `_register_blends` writes
    `class_map` and `teacher_map` BEFORE the grade range is known, so a
    suppression placed after them would leave two maps populated while
    `metadata` was skipped — orphan Class IDs in Enrollments.csv, the exact
    partner-ingest rejection commit e187ac8 fixed.
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _detect(self, class_info_enh_df, blended_schedule_df, blended_course_info_df, **global_overrides):
        raw_data = {
            "StudentSchedule.txt": blended_schedule_df,
            "CourseInformation.txt": blended_course_info_df,
            "ClassInformationEnh.txt": class_info_enh_df,
        }
        self.transformer._detect_blended_classes(
            class_info_enh_df, _MAPPING, raw_data, {**_ENROLLMENTS_FIELD_MAP, **global_overrides}
        )
        return (
            self.transformer.blended_class_map,
            self.transformer.blended_class_metadata,
            self.transformer.blended_teacher_map,
        )

    def test_an_all_homeroom_blend_is_suppressed_with_NO_scope_configured(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """THE default-path pin (plan 0043). The fixture's 01/02/03 blend is
        wholly inside the base homeroom grades, so every one of its pupils is
        rostered through their homeroom and the subject path emits nothing for
        them — the class could only ever ship with a teacher and zero students.

        Before 0043 this blend WAS produced (the rule only fired for a district
        that had configured a scope), which is what put
        `BLENDED_7474018_T0001003_1_1_2_2_2026` in the SD74 golden. This unit
        row and that golden deletion are now the same statement.
        """
        class_map, metadata, teacher_map = self._detect(
            class_info_enh_df, blended_schedule_df, blended_course_info_df, homeroom_grades=_HOMEROOM_KG_TO_07
        )
        assert (class_map, metadata, teacher_map) == ({}, {}, {})

    def test_the_SAME_blend_survives_when_ONE_of_its_grades_leaves_homeroom(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """The positive twin for the row above, differing ONLY in whether "03"
        is a homeroom grade. Without it, a detector that had simply stopped
        detecting would pass the suppression assertion.
        """
        homeroom_without_03 = [g for g in _HOMEROOM_KG_TO_07 if g != "03"]
        class_map, metadata, teacher_map = self._detect(
            class_info_enh_df, blended_schedule_df, blended_course_info_df, homeroom_grades=homeroom_without_03
        )
        assert {"MT100", "MT101", "MT102"} <= set(class_map)
        blended_id = class_map["MT100"]
        assert blended_id in metadata
        assert blended_id in teacher_map

    def test_the_unconfigured_suppression_log_does_NOT_name_a_scope(
        self, caplog, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """`resolve_timetable_scope`'s `None` is KEPT (rather than collapsed into
        the derived complement) for exactly one reason: so this message can be
        honest. An unscoped district has no configured scope to print, and
        printing the derived one would read "…not inside the timetable rostering
        scope ['01', …, 'UG']" — 24 codes, and untrue in spirit.

        Pinned because this log is now the ONLY consumer of that `None`; without
        the pin the branch is deletable on a green suite.
        """
        with caplog.at_level(logging.INFO, logger=blended_module.logger.name):
            self._detect(
                class_info_enh_df, blended_schedule_df, blended_course_info_df, homeroom_grades=_HOMEROOM_KG_TO_07
            )
        messages = [r.getMessage() for r in caplog.records if "Suppressed blend" in r.getMessage()]
        assert len(messages) == 1, messages
        assert "every grade in this blend ['01', '02', '03'] is a homeroom grade" in messages[0]
        assert "scope" not in messages[0]

    def test_the_configured_suppression_log_names_the_configured_scope(
        self, caplog, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """The other branch: a district that DID configure a scope is told which
        one excluded the blend."""
        with caplog.at_level(logging.INFO, logger=blended_module.logger.name):
            self._detect(
                class_info_enh_df,
                blended_schedule_df,
                blended_course_info_df,
                homeroom_grades=["01"],
                class_rostering_grades=["01", "10", "11", "12"],
            )
        messages = [r.getMessage() for r in caplog.records if "Suppressed blend" in r.getMessage()]
        assert len(messages) == 1, messages
        assert "none of its grades ['01', '02', '03'] is inside the configured timetable rostering scope" in messages[0]
        assert "['10', '11', '12']" in messages[0]

    def test_sentinel_suppresses_the_blend_from_all_three_maps(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        class_map, metadata, teacher_map = self._detect(
            class_info_enh_df,
            blended_schedule_df,
            blended_course_info_df,
            homeroom_grades=_HOMEROOM_KG_TO_07,
            class_rostering_grades="homeroom",
        )
        assert class_map == {}
        assert metadata == {}
        assert teacher_map == {}

    def test_a_list_excluding_every_blend_grade_suppresses_it(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        class_map, metadata, teacher_map = self._detect(
            class_info_enh_df,
            blended_schedule_df,
            blended_course_info_df,
            homeroom_grades=[],
            class_rostering_grades=["10", "11", "12"],
        )
        assert (class_map, metadata, teacher_map) == ({}, {}, {})

    def test_a_blend_with_ONE_in_scope_grade_survives(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """Necessary-not-sufficient, from the other side: partial overlap keeps
        the blend, because those grades DO receive subject enrollments."""
        class_map, metadata, teacher_map = self._detect(
            class_info_enh_df,
            blended_schedule_df,
            blended_course_info_df,
            homeroom_grades=["01"],
            class_rostering_grades=["01", "02", "03"],
        )
        assert {"MT100", "MT101", "MT102"} <= set(class_map)
        blended_id = class_map["MT100"]
        assert blended_id in metadata and blended_id in teacher_map


class TestBlendGradesIsSingleSourced:
    """`_blend_grades` is THE spelling of the MT-ID → CEDS grade-set derivation."""

    def setup_method(self):
        self.detector = BlendedClassDetector()
        self.group = pd.DataFrame({"master timetable id": ["MT1", "MT2"]})
        self.grade_map = {"MT1": "1", "MT2": "2"}

    def test_it_derives_the_ceds_grade_set(self):
        assert self.detector._blend_grades(self.group, self.grade_map) == {"01", "02"}

    def test_validate_consumes_it(self, monkeypatch):
        monkeypatch.setattr(BlendedClassDetector, "_blend_grades", staticmethod(lambda *_: {"01"}))
        assert self.detector.validate(self.group, self.grade_map) is False

    def test_get_grade_range_consumes_it(self, monkeypatch):
        monkeypatch.setattr(BlendedClassDetector, "_blend_grades", staticmethod(lambda *_: {"07", "08"}))
        assert self.detector.get_grade_range(self.group, self.grade_map) == "07/08"

    def test_register_blends_names_the_blend_through_it(
        self, monkeypatch, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """`_register_blends` reaches the helper through `get_grade_range`: force
        it to report 07/08 and the registered blend is NAMED 07/08, whatever its
        real grades.

        This row used to assert the SUPPRESSION check read the helper. Since
        plan 0043 it does not — suppression gates on the PER-ROW enrollable set
        (`_enrollable_grades`), while the mode-based helper keeps qualification
        and naming. Asserting the old statement would now pass for the wrong
        reason (a one-grade set fails `validate` before the gate is reached), so
        it is re-pointed at the consumption that is actually still there.
        """
        transformer = DataTransformer()
        transformer.set_school_year(2025, "08-25", "07-25")
        monkeypatch.setattr(BlendedClassDetector, "_blend_grades", staticmethod(lambda *_: {"07", "08"}))
        transformer._detect_blended_classes(
            class_info_enh_df,
            _MAPPING,
            {
                "StudentSchedule.txt": blended_schedule_df,
                "CourseInformation.txt": blended_course_info_df,
                "ClassInformationEnh.txt": class_info_enh_df,
            },
            {**_ENROLLMENTS_FIELD_MAP, "homeroom_grades": ["01"], "class_rostering_grades": ["01", "02", "03"]},
        )
        assert transformer.blended_class_map, "the blend must survive on its REAL (02/03) enrollable grades"
        blended_id = transformer.blended_class_map["MT100"]
        assert transformer.blended_class_metadata[blended_id]["Grade"] == "07/08"

    def test_no_FOURTH_spelling_of_the_conversion_exists_in_the_module(self):
        """Structural: `grade_to_ceds` is called in exactly one place in this
        module — inside `_blend_grades`. A new call site is a new spelling.

        Plan 0043 added a SECOND grade derivation to this module
        (`_build_enrollable_grade_map`), and this pin is why it is not spelled
        here: it delegates to `grades.ceds_grade_series`, the very function the
        subject split uses, which is what makes the two row-set-identical. The
        count stays 1 because the second derivation genuinely lives in the
        module that owns the vocabulary — NOT because the assertion was relaxed.
        """
        source = inspect.getsource(blended_module)
        assert source.count("grade_to_ceds(") == 1


class TestEnrollableGradeMapIsRowSetIdentical:
    """`_build_enrollable_grade_map` must classify the rows `split_by_homeroom_grades`
    classifies — same rows, same null handling.

    The natural implementation (a sibling of `_build_grade_map`) breaks it: that
    map opens with `.dropna()`, but a blank/NaN grade converts to "UG", "UG" is
    not a homeroom grade, so the row SURVIVES the subject filter and is a real
    student. A dropna-built gate would suppress a blend that has a pupil,
    re-key that pupil to a per-section class and GROW Classes.csv — every
    property the per-row rule exists to guarantee, broken. See
    `TestRowSetIdentityUnderBlankGrades` for the end-to-end half.
    """

    #: MT1 carries two grade-03 rows and one BLANK; MT2 one grade-04 row.
    SCHEDULE = pd.DataFrame(
        {
            "master timetable id": ["MT1", "MT1", "MT1", "MT2"],
            "grade": ["03", "03", None, "04"],
        }
    )

    def test_a_blank_grade_row_contributes_UG(self):
        assert BlendedClassDetector._build_enrollable_grade_map(self.SCHEDULE) == {
            "MT1": {"03", "UG"},
            "MT2": {"04"},
        }

    def test_the_MODE_map_over_the_same_frame_would_have_LOST_it(self):
        """The discriminator, asserted rather than described: the mode map's
        `.dropna()` yields {'03', '04'} where the enrollable map yields
        {'03', '04', 'UG'} — and under the base homeroom grades that difference
        is the whole suppression decision."""
        mode = BlendedClassDetector._build_grade_map(self.SCHEDULE)
        enrollable = BlendedClassDetector._build_enrollable_grade_map(self.SCHEDULE)
        assert set(mode.values()) == {"03", "04"}
        assert set().union(*enrollable.values()) == {"03", "04", "UG"}
        assert not set(mode.values()) - set(_HOMEROOM_KG_TO_07)
        assert set().union(*enrollable.values()) - set(_HOMEROOM_KG_TO_07) == {"UG"}

    def test_the_map_is_derived_through_the_SHARED_ceds_helper(
        self, monkeypatch, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
        """The positive twin for the structural pin below: the gate really does
        consume `grades.ceds_grade_series` (the subject split's own derivation),
        not a parallel expression that happens to agree today. Force it to call
        every row a homeroom grade and the blend that would otherwise survive is
        suppressed.
        """
        transformer = DataTransformer()
        transformer.set_school_year(2025, "08-25", "07-25")
        monkeypatch.setattr(
            blended_module, "ceds_grade_series", lambda series: pd.Series(["01"] * len(series), index=series.index)
        )
        transformer._detect_blended_classes(
            class_info_enh_df,
            _MAPPING,
            {
                "StudentSchedule.txt": blended_schedule_df,
                "CourseInformation.txt": blended_course_info_df,
                "ClassInformationEnh.txt": class_info_enh_df,
            },
            {**_ENROLLMENTS_FIELD_MAP, "homeroom_grades": [g for g in _HOMEROOM_KG_TO_07 if g != "03"]},
        )
        assert transformer.blended_class_map == {}


class TestTheEffectiveRosteredSetIsSingleSourced:
    """The CEDS complement is spelled ONCE — in `grades.timetable_rostered_grades`.

    Same house positive-count form, and the same reason, as the pin above: the
    blend-suppression rule must mask on exactly the set
    `split_by_homeroom_grades` masks on, and a second hand-rolled
    "CEDS_GRADE_CODES minus homeroom_grades" at either call site is how the two
    silently diverge. It lives in the blended suite rather than the grades one
    because this module is where that second spelling would be written — so it
    COUNTS OVER BOTH modules, not over `grades` alone. An earlier version
    inspected `grades` only, which left the very site this sentence names as the
    risk passing green.
    """

    #: Both halves of the pair the invariant binds — the module that owns the
    #: derivation, and the module most likely to re-spell it.
    MODULES = (grades_module, blended_module)

    def test_the_complement_is_spelled_exactly_once_ACROSS_BOTH_MODULES(self):
        spellings = {
            module.__name__: inspect.getsource(module).count("set(CEDS_GRADE_CODES)") for module in self.MODULES
        }
        assert sum(spellings.values()) == 1, (
            "the CEDS complement must be derived in exactly one place "
            "(grades.timetable_rostered_grades) — a second spelling is the DRY breach "
            f"this pin exists to catch, and re-spelling the one site differently defeats it: {spellings}"
        )

    def test_the_subject_split_consumes_the_helper(self, monkeypatch):
        """The positive twin for the count above: the subject mask really is the
        helper's return value, not a parallel expression that agrees today."""
        monkeypatch.setattr(grades_module, "timetable_rostered_grades", lambda *_args, **_kwargs: {"12"})
        out = grades_module.split_by_homeroom_grades(
            pd.DataFrame({"grade": ["1", "12"]}), "grade", ["01"], keep="subject"
        )
        assert list(out["grade_ceds"]) == ["12"]


# ---------------------------------------------------------------------------
# create_name — the course-code column guard (plan 0043, slice 1)
# ---------------------------------------------------------------------------
class TestCourseCodeColumnGuard:
    """`create_name` used to index `session_group["course code"]` unguarded, so a
    frame without that column killed the whole run at the Classes entity.

    The realistic trigger is not a column-less synthetic frame: it is
    ClassInformation with no Master Timetable ID (SD40's real shape), which makes
    detection fall back to the deduplicated schedule — where the column is
    spelled `district course code`. So the guard resolves the alias FIRST and
    only degrades when neither spelling exists.
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _detect(self, class_info_df, schedule_df, course_info_df, **global_overrides):
        self.transformer._detect_blended_classes(
            class_info_df,
            _MAPPING,
            {
                "StudentSchedule.txt": schedule_df,
                "CourseInformation.txt": course_info_df,
                "ClassInformationEnh.txt": class_info_df,
            },
            {**_ENROLLMENTS_FIELD_MAP, **global_overrides},
        )
        return self.transformer.blended_class_metadata

    def test_the_district_course_code_alias_produces_REAL_course_titles(
        self, blended_schedule_df, blended_course_info_df
    ):
        """The positive row. ClassInformation lacking Master Timetable ID falls
        back to the schedule, whose `district course code` values live in the
        same space as the course catalogue's `course code` — so the blend is
        named with genuine titles. Before the guard this exact input raised
        KeyError('course code') and exited the run with code 1.
        """
        class_info_without_mt_id = pd.DataFrame({"school number": ["300"], "teacher id": ["T010"]})

        metadata = self._detect(class_info_without_mt_id, blended_schedule_df, blended_course_info_df)

        names = [meta["Name"] for meta in metadata.values()]
        assert names == ["Adams English 1 / English 2 / Science 3 (01/02/03) 2025"]
        assert "Unknown Course" not in names[0]

    def test_neither_column_skips_the_segment_and_warns_exactly_ONCE(self, caplog):
        """The degradation row. Two blends, one warning: the column is resolved
        once per detection, never inside the per-blend naming call (SD40's
        FY2026 run had 411 blends, i.e. 411 identical WARNINGs).

        And the segment is SKIPPED, not filled with "Unknown Course" — that
        default answers "this code has no title", so printing it for a district
        whose export simply omits the column would put a fabricated course title
        on the partner's class list.
        """
        class_info = pd.DataFrame(
            {
                "school number": ["400"] * 4,
                "teacher id": ["T030", "T030", "T031", "T031"],
                "teacher name": ["Cole", "Cole", "Diaz", "Diaz"],
                "master timetable id": ["MT200", "MT201", "MT202", "MT203"],
                "term": ["1"] * 4,
                "semester": ["1"] * 4,
                "day": ["1"] * 4,
                "period": ["1", "1", "2", "2"],
            }
        )
        schedule = pd.DataFrame(
            {
                "school number": ["400"] * 4,
                "master timetable id": ["MT200", "MT201", "MT202", "MT203"],
                "grade": ["1", "2", "3", "4"],
            }
        )
        course_info = pd.DataFrame({"school number": ["400"], "course code": ["ENG01"], "title": ["English 1"]})

        with caplog.at_level(logging.WARNING, logger=blended_module.logger.name):
            metadata = self._detect(class_info, schedule, course_info)

        assert sorted(meta["Name"] for meta in metadata.values()) == ["Cole (01/02) 2025", "Diaz (03/04) 2025"]
        missing_column_warnings = [r for r in caplog.records if "district course code" in r.getMessage()]
        assert len(missing_column_warnings) == 1, f"expected exactly one warning, got {len(missing_column_warnings)}"

    def test_no_blends_means_NO_warning_about_names_that_were_never_built(self, caplog):
        """The absence row, and its positive twin is the test directly above —
        the SAME missing columns, differing only in whether any blend was named.

        The warning says blended class names omit their course titles. On a
        district with no multi-section same-teacher sessions there are no
        blended class names, so the sentence is not true of anything; it landed
        in the log partners are asked to send to support. Resolved once, warned
        once, and only when there is something to warn about.
        """
        class_info = pd.DataFrame(
            {
                "school number": ["400", "400"],
                "teacher id": ["T030", "T031"],
                "teacher name": ["Cole", "Diaz"],
                # One section each: no teacher runs two sections at one slot,
                # so `validate` qualifies nothing and no name is ever built.
                "master timetable id": ["MT200", "MT202"],
                "term": ["1", "1"],
                "semester": ["1", "1"],
                "day": ["1", "1"],
                "period": ["1", "2"],
            }
        )
        schedule = pd.DataFrame(
            {
                "school number": ["400", "400"],
                "master timetable id": ["MT200", "MT202"],
                "grade": ["1", "3"],
            }
        )
        course_info = pd.DataFrame({"school number": ["400"], "course code": ["ENG01"], "title": ["English 1"]})

        with caplog.at_level(logging.WARNING, logger=blended_module.logger.name):
            metadata = self._detect(class_info, schedule, course_info)

        assert metadata == {}, "precondition: this frame must produce no blends, or the twin proves nothing"
        assert [r for r in caplog.records if "district course code" in r.getMessage()] == []
