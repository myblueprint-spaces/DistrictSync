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
        self.transformer.set_school_year(2025)

    def test_full_blended_name(self):
        group = pd.DataFrame({
            "teacher name": ["Adams", "Adams"],
            "course code": ["ENG01", "ENG02"],
            "master timetable id": ["MT1", "MT2"],
        })
        field_map = {"Name": {"teacher_last_name": "Teacher Name"}}
        course_map = {"ENG01": "English 1", "ENG02": "English 2"}

        result = self.transformer._create_blended_class_name(
            group, field_map, "01/02", course_map
        )
        assert "Adams" in result
        assert "English 1" in result
        assert "English 2" in result
        assert "01/02" in result
        assert "2025" in result

    def test_fallback_when_no_teacher(self):
        group = pd.DataFrame({
            "course code": ["SCI01", "SCI02"],
            "master timetable id": ["MT1", "MT2"],
        })
        field_map = {"Name": {"teacher_last_name": "teacher name"}}
        course_map = {"SCI01": "Science 1", "SCI02": "Science 2"}

        result = self.transformer._create_blended_class_name(
            group, field_map, "01/02", course_map
        )
        assert "Science 1" in result
        assert "2025" in result


class TestDetectBlendedClasses:
    """Integration test for the full blended class detection pipeline."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025)

    def test_detects_blended_from_class_info(
        self, class_info_enh_df, blended_schedule_df, blended_course_info_df
    ):
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
        self.transformer._detect_blended_classes(
            empty_df, {"source_files": {}, "field_map": {}}, {}, {"mappings": {}}
        )
        assert self.transformer.blended_class_map == {}

    def test_no_blending_single_records(self):
        """Each session has only 1 record — no blending possible."""
        df = pd.DataFrame({
            "school number": ["100", "200"],
            "teacher id": ["T001", "T002"],
            "master timetable id": ["MT001", "MT002"],
            "term": ["1", "1"],
            "semester": ["1", "1"],
            "day": ["1", "2"],
            "period": ["1", "1"],
        })
        raw_data = {
            "StudentSchedule.txt": pd.DataFrame({
                "master timetable id": ["MT001", "MT002"],
                "grade": ["5", "6"],
            }),
            "CourseInformation.txt": pd.DataFrame({"course code": [], "title": []}),
        }
        mapping = {
            "source_files": {
                "student_schedule": "StudentSchedule.txt",
                "course_info": "CourseInformation.txt",
            },
            "field_map": {},
        }
        global_config = {
            "mappings": {
                "Enrollments": {"field_map": {"User ID": {"staff_id_col": "Teacher ID"}}}
            }
        }
        self.transformer._detect_blended_classes(df, mapping, raw_data, global_config)
        assert self.transformer.blended_class_map == {}
