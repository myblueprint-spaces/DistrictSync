"""Tests for blended class detection, validation, naming, and grade range."""

import pandas as pd

from src.etl.transformer import DataTransformer


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
        field_map = {"Name": {"teacher_last_name": "Teacher Name"}}
        course_map = {"ENG01": "English 1", "ENG02": "English 2"}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", course_map)
        assert "Adams" in result
        assert "English 1" in result
        assert "English 2" in result
        assert "01/02" in result
        assert "2025" in result

    def test_fallback_when_no_teacher(self):
        group = pd.DataFrame(
            {
                "course code": ["SCI01", "SCI02"],
                "master timetable id": ["MT1", "MT2"],
            }
        )
        field_map = {"Name": {"teacher_last_name": "teacher name"}}
        course_map = {"SCI01": "Science 1", "SCI02": "Science 2"}

        result = self.transformer._create_blended_class_name(group, field_map, "01/02", course_map)
        assert "Science 1" in result
        assert "2025" in result


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
                "Name": {"teacher_last_name": "Teacher Name"},
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
