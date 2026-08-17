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


def _straddling_blend_with_an_inactive_senior() -> dict[str, pd.DataFrame]:
    """Teacher T050, one slot, two sections: grade 03 (homeroom) + grade 10.

    The blend STRADDLES, so plan 0043's suppression rule keeps it (a grade-10
    pupil would ordinarily enrol). That pupil is withdrawn, though, so the
    subject path emits nothing for the blend and it ships studentless — the one
    trigger `_emit_missing_blended_classes` still has.
    """
    schedule = pd.DataFrame(
        {
            "student number": ["S_HR03", "S_SENIOR"],
            "student id": ["S_HR03", "S_SENIOR"],
            "school number": ["500", "500"],
            "school year": ["2024/2025", "2024/2025"],
            "grade": ["03", "10"],
            "master timetable id": ["MT600", "MT601"],
            "teacher id": ["T050", "T050"],
            "section letter": ["A", "A"],
            "district course code": ["HR-3", "MAT10"],
            "primary teacher": ["Y", "Y"],
            "teacher name": ["Vance", "Vance"],
            "period": ["1", "1"],
        }
    )
    demographic = pd.DataFrame(
        {
            "student number": ["S_HR03", "S_SENIOR"],
            "legal first name": ["First", "Senior"],
            "legal surname": ["Last", "Last"],
            "date of birth": ["2016-01-15", "2009-01-15"],
            "grade": ["03", "10"],
            "school number": ["500", "500"],
            "homeroom": ["HR03", "HR10"],
            "previous school number": ["", ""],
            "usual first name": ["", ""],
            "usual surname": ["", ""],
            "student email address": ["", ""],
            "enrolment status": ["Active", "Inactive"],
            "teacher name": ["Vance", "Vance"],
            "teacher id": ["T050", "T050"],
        }
    )
    class_info = pd.DataFrame(
        {
            "school number": ["500", "500"],
            "teacher id": ["T050", "T050"],
            "master timetable id": ["MT600", "MT601"],
            "course code": ["HR-3", "MAT10"],
            "term": ["1", "1"],
            "semester": ["1", "1"],
            "day": ["1", "1"],
            "period": ["1", "1"],
        }
    )
    return {
        "StudentDemographicInformation.txt": demographic,
        "StudentSchedule.txt": schedule,
        "StaffInformationEnhanced.txt": pd.DataFrame(
            {
                "teacher id": ["T050"],
                "first name": ["Vera"],
                "last name": ["Vance"],
                "email address": ["vance@school.ca"],
                "teaching staff": ["Y"],
                "school number": ["500"],
            }
        ),
        "CourseInformation.txt": pd.DataFrame(
            {
                "school number": ["500", "500"],
                "course code": ["HR-3", "MAT10"],
                "title": ["Homeroom 3", "Math 10"],
            }
        ),
        "EmergencyContactInformation.txt": pd.DataFrame(),
        "ClassInformationEnh.txt": class_info,
    }


class TestClassesTransformBlended:
    """Tests for blended class integration in class output."""

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_blended_classes_in_output(self, students_mapping, classes_mapping, enrollments_mapping, global_config):
        """Regression guard for `_emit_missing_blended_classes` (commit e187ac8).

        The subject path can legitimately produce NO row for a surviving blend,
        and the blended-teacher path emits its enrollment regardless — so
        without the missing-blended pass that teacher row is an ORPHAN Class ID,
        which is exactly what the partner's pre-upload validator rejected.

        Scenario rewritten for plan 0043. It used to rely on an all-homeroom
        blend, which that plan now SUPPRESSES (rightly — nobody could ever be in
        it), so the guard needed a trigger that still exists. The surviving one
        is residual #1: a STRADDLING blend (grade 03 + grade 10, so the gate
        keeps it) whose only timetable-side pupil is INACTIVE. `filter_to_active`
        removes them from the subject path, the blend ships studentless, and the
        emitter is the only thing standing between it and an orphan.
        """
        raw_data = _straddling_blend_with_an_inactive_senior()
        transformer = DataTransformer()
        transformer.set_school_year(2025, "08-25", "07-25")

        students = transformer.transform(
            raw_data["StudentDemographicInformation.txt"], students_mapping, "Students", raw_data, global_config
        )
        result = transformer.transform(
            raw_data["StudentSchedule.txt"], classes_mapping, "Classes", raw_data, global_config
        )
        enrollments = transformer.transform(
            raw_data["StudentSchedule.txt"], enrollments_mapping, "Enrollments", raw_data, global_config
        )

        # The inactive senior really is off the roster — otherwise the subject
        # path would emit the blended row itself and the emitter would be untested.
        assert set(students["User ID"]) == {"S_HR03"}

        assert not result.empty
        blended_rows = result[result["Class ID"].str.startswith("BLENDED")]
        assert not blended_rows.empty, (
            "A surviving blend whose in-scope students are all inactive must still be written to "
            "Classes.csv — its teacher enrollment references it"
        )
        # Each detected blended must appear exactly once (dedup guarantee)
        assert blended_rows["Class ID"].duplicated().sum() == 0

        blended_id = blended_rows["Class ID"].iloc[0]
        teacher_rows = enrollments[enrollments["Class ID"] == blended_id]
        assert set(teacher_rows["Role"]) == {"teacher"}, "residual #1: the blend ships with no students"
        assert set(enrollments["Class ID"]) <= set(result["Class ID"]), "orphan Class IDs in Enrollments"

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


class TestNameConfigDrivesClassNames:
    """The spaced-key YAML Name config (the authoring format every mapping file
    uses) must actually drive subject-class naming.

    Regression pin for the key mismatch where ``_assign_class_names`` read
    underscore keys ("section_letter", ...) that no config ever produced, so
    the hardcoded defaults always applied: districts with a renamed section
    column (SD60/SD74 use "Section") silently lost the section letter and the
    primary-teacher flag was never consulted.
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_spaced_keys_drive_section_letter_and_primary_teacher_flag(
        self, student_schedule_df, classes_mapping, global_config, raw_data
    ):
        # Rename the schedule's flag + section columns so ONLY the configured
        # Name columns can find them (the old hardcoded defaults would miss).
        schedule = student_schedule_df.rename(
            columns={"section letter": "sec code", "primary teacher": "lead teacher"}
        ).copy()
        schedule.loc[schedule["master timetable id"] == "MT004", "lead teacher"] = "N"
        mapping = {
            **classes_mapping,
            "field_map": {
                **classes_mapping["field_map"],
                "Name": {
                    "primary teacher flag": "Lead Teacher",
                    "teacher last name": "Teacher Name",
                    "course title": "Course Title",
                    "section letter": "Sec Code",
                },
            },
        }
        raw = {**raw_data, "StudentSchedule.txt": schedule}

        result = self.transformer.transform(schedule, mapping, "Classes", raw, global_config)
        names = dict(zip(result["Class ID"], result["Name"]))

        # Flag = Y -> teacher included; configured section column drives "(A)".
        assert names["MT005_2025"] == "Singh English 12 (A) 2025"
        # Flag = N -> the primary-teacher flag is LIVE and drops the teacher.
        assert names["MT004_2025"] == "Math 10 (A) 2025"


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
