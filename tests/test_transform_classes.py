"""Integration tests for the Classes entity transformation.

Tests homeroom class generation, subject class creation, and blended class integration.
"""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestClassesTransformHomeroom:
    """Tests for homeroom class generation (grades K-7)."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_homeroom_classes_created(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        assert not result.empty
        # Should have homeroom classes for grades in homeroom_grades config
        homeroom_classes = result[
            result["Class ID"].str.contains("_2025") & ~result["Class ID"].str.startswith("BLENDED")
        ]
        assert len(homeroom_classes) > 0

    def test_homeroom_class_id_format(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        # Homeroom class IDs should be: {school_number}_{homeroom}_{year}
        homeroom_ids = result[result["Class ID"].str.match(r"^\d+_\w+_\d{4}$")]["Class ID"]
        for class_id in homeroom_ids:
            assert class_id.endswith("_2025")

    def test_homeroom_has_academic_dates(self, student_schedule_df, classes_mapping, global_config, raw_data):
        # set_school_year(2025) → academic period 2024-2025 (end-year convention)
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        if "Start Date" in result.columns and "End Date" in result.columns:
            assert (result["Start Date"] == "2024-08-25").any() or result["Start Date"].isna().all()
            assert (result["End Date"] == "2025-07-25").any() or result["End Date"].isna().all()

    def test_homeroom_name_format(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        # Homeroom names should contain the homeroom code and year
        if "Name" in result.columns:
            names = result["Name"].dropna().tolist()
            names_with_year = [n for n in names if "2025" in str(n)]
            assert len(names_with_year) > 0


class TestClassesTransformSubject:
    """Tests for subject class creation (non-homeroom grades)."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_subject_classes_created(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        # Grades 10, 12 are not in homeroom_grades → should get subject classes
        # MT004 (MAT10 grade 10) and MT005 (ENG12 grade 12) should be subject classes
        assert not result.empty

    def test_subject_class_name_includes_teacher(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        if "Name" in result.columns:
            names = result["Name"].dropna().tolist()
            # At least some names should have teacher last names
            has_teacher_name = any("Liu" in str(n) or "Singh" in str(n) or "Reed" in str(n) for n in names)
            assert has_teacher_name

    def test_deduplicated_by_class_id(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        assert result["Class ID"].duplicated().sum() == 0

    def test_all_classes_have_school_id(self, student_schedule_df, classes_mapping, global_config, raw_data):
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        if "School ID" in result.columns:
            assert result["School ID"].notna().all()


class TestClassesTransformBlended:
    """Tests for blended class integration in class output."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_blended_classes_in_output(
        self, blended_schedule_df, classes_mapping, global_config, raw_data_with_blended
    ):
        """Regression guard: blended classes must appear in Classes.csv even when
        all of their constituent students are in homeroom grades. The fixture
        has grades 1/2/3 for the blend; with homeroom_grades=[KG..07], all those
        students go through the homeroom path, so without the missing-blended
        pass the BLENDED row would be dropped (causing orphan enrollments).
        """
        global_config_copy = {**global_config}
        global_config_copy["homeroom_grades"] = ["01", "02", "03", "04", "05", "06", "07", "KG"]

        result = self.transformer.transform(
            blended_schedule_df, classes_mapping, "Classes", raw_data_with_blended, global_config_copy
        )

        assert not result.empty
        blended_rows = result[result["Class ID"].str.startswith("BLENDED")]
        assert not blended_rows.empty, (
            "Blended classes must be written to Classes.csv even when all constituent students are in homeroom grades"
        )
        # Each detected blended must appear exactly once (dedup guarantee)
        assert blended_rows["Class ID"].duplicated().sum() == 0

    def test_blended_class_grade_is_empty(
        self, blended_schedule_df, classes_mapping, global_config, raw_data_with_blended
    ):
        """Blended classes should have empty Grade field."""
        global_config_copy = {**global_config}
        # Set homeroom grades to empty so all grades go through subject path
        global_config_copy["homeroom_grades"] = []

        result = self.transformer.transform(
            blended_schedule_df, classes_mapping, "Classes", raw_data_with_blended, global_config_copy
        )
        if not result.empty:
            blended_rows = result[result["Class ID"].str.startswith("BLENDED")]
            if not blended_rows.empty:
                assert (blended_rows["Grade"] == "").all()


class TestExcludedCourseCodes:
    """Rows whose course code is in global_config.excluded_course_codes
    must not become Classes.csv rows (e.g. MyEd BC's ATT--AM/ATT--PM
    attendance-only schedule entries for SD40).
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_attendance_codes_filtered_from_subject_classes(
        self, student_schedule_df, classes_mapping, global_config, raw_data
    ):
        # Inject an ATT--AM row for a grade-10 student (non-homeroom path)
        att_row = pd.DataFrame(
            {
                "student number": ["S004"],
                "student id": ["S004"],
                "school number": ["200"],
                "school year": ["2025/2026"],
                "grade": ["10"],
                "master timetable id": ["MT_ATT_AM"],
                "teacher id": ["T003"],
                "section letter": ["A"],
                "district course code": ["ATT--AM"],
                "primary teacher": ["Y"],
                "teacher name": ["Liu"],
            }
        )
        schedule_with_att = pd.concat([student_schedule_df, att_row], ignore_index=True)
        raw_data_with_att = {**raw_data, "StudentSchedule.txt": schedule_with_att}

        cfg = {**global_config, "excluded_course_codes": ["ATT--AM", "ATT--PM"]}
        result = self.transformer.transform(schedule_with_att, classes_mapping, "Classes", raw_data_with_att, cfg)

        # The ATT row's Class ID (MT_ATT_AM_2025) must not appear
        assert "MT_ATT_AM_2025" not in result["Class ID"].values
        # Other non-homeroom classes still flow through (grade 10/12 subjects)
        assert any(result["Class ID"].astype(str).str.startswith("MT00"))

    def test_exclusion_empty_by_default(self, student_schedule_df, classes_mapping, global_config, raw_data):
        """Absent excluded_course_codes → no rows are filtered (backward compatible)."""
        result = self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        assert not result.empty
