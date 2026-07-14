"""Integration tests for the Enrollments entity transformation.

Tests homeroom enrollments, subject enrollments, blended teacher enrollments,
and deduplication.
"""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestEnrollmentsTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _run_classes_then_enrollments(self, schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data):
        """Classes must be transformed first to populate homeroom_classes_df and blended maps."""
        self.transformer.transform(schedule_df, classes_mapping, "Classes", raw_data, global_config)
        return self.transformer.transform(schedule_df, enrollments_mapping, "Enrollments", raw_data, global_config)

    def test_enrollments_have_required_columns(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            assert "Class ID" in result.columns
            assert "User ID" in result.columns
            assert "Role" in result.columns

    def test_student_enrollments_have_student_role(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            student_rows = result[result["Role"] == "student"]
            assert len(student_rows) > 0

    def test_teacher_enrollments_have_teacher_role(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            teacher_rows = result[result["Role"] == "teacher"]
            assert len(teacher_rows) > 0

    def test_deduplicated_on_class_user_role(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            dupes = result.duplicated(subset=["Class ID", "User ID", "Role"])
            assert dupes.sum() == 0

    def test_no_nan_teacher_ids(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        """Teacher enrollment rows should not have NaN or 'nan' User IDs."""
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            teacher_rows = result[result["Role"] == "teacher"]
            if not teacher_rows.empty:
                ids = teacher_rows["User ID"].astype(str).str.strip().str.lower()
                assert (ids != "nan").all()
                assert (ids != "").all()
                assert teacher_rows["User ID"].notna().all()

    def test_homeroom_student_enrollments(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        """Students in homeroom grades should get homeroom enrollments derived from
        the demographic file. The demographic file's student-ID column comes from
        the Students config ('User ID' → 'Student Number'), not the Enrollments
        config's schedule-targeted 'Student ID'.
        """
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        assert not result.empty
        student_enrollments = result[result["Role"] == "student"]

        # Homeroom Class IDs follow "{school}_{homeroom}_{year}" — distinct from
        # subject Class IDs which use Master Timetable ID. The demographic fixture
        # places S001/S002/S006 in grades K/3/1 at school 100, homeroom A1.
        homeroom_rows = student_enrollments[student_enrollments["Class ID"].astype(str).str.contains("_A1_")]
        assert {"S001", "S002", "S006"}.issubset(set(homeroom_rows["User ID"].astype(str)))

    def test_school_id_column_renamed(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
        """'school number' should be renamed to 'School ID' in output."""
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            assert "School ID" in result.columns
            assert "school number" not in result.columns


class TestEnrollmentsBlended:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_blended_teacher_enrollments(
        self, blended_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data_with_blended
    ):
        """Teachers in blended classes should get enrollment records."""
        global_config_copy = {**global_config}
        global_config_copy["homeroom_grades"] = []

        self.transformer.transform(
            blended_schedule_df, classes_mapping, "Classes", raw_data_with_blended, global_config_copy
        )
        result = self.transformer.transform(
            blended_schedule_df, enrollments_mapping, "Enrollments", raw_data_with_blended, global_config_copy
        )

        if not result.empty and self.transformer.blended_teacher_map:
            teacher_rows = result[result["Role"] == "teacher"]
            blended_teacher_rows = teacher_rows[teacher_rows["Class ID"].str.startswith("BLENDED")]
            assert len(blended_teacher_rows) > 0


class TestClassInfoCoTeacherEnrollments:
    """Tests for ClassInformation Primary=Y co-teacher enrollments.

    Captures modular-program / non-ATT teachers (e.g. Elaine Su in SD40's MADST01
    sections) who are marked as primary teachers in ClassInformation but never
    appear in the student_schedule as the driving teacher for any student.
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_coteacher_attached_via_section_letter(
        self,
        student_schedule_df,
        student_demographic_df,
        staff_info_df,
        course_info_df,
        emergency_contact_df,
        classes_mapping,
        enrollments_mapping,
        global_config,
    ):
        """A Primary=Y row in ClassInformation with Section Letter matching a
        known homeroom should co-enroll the teacher on that homeroom class.
        """
        # student_demographic_df has homeroom "A1" at school 100 (grades K/3/1).
        # T099 is a new teacher not present anywhere else — must be enrolled
        # solely via the ClassInformation path.
        class_info_df = pd.DataFrame(
            {
                "school number": ["100"],
                "course code": ["MADST01"],
                "teacher id": ["T099"],
                "primary teacher": ["Y"],
                "section letter": ["A1"],
                "semester": ["FY"],
                "term": ["1"],
                "day": [""],
                "period": [""],
                "master timetable id": [""],
            }
        )
        raw_data = {
            "StudentDemographicInformation.txt": student_demographic_df,
            "StudentSchedule.txt": student_schedule_df,
            "StaffInformationEnhanced.txt": staff_info_df,
            "CourseInformation.txt": course_info_df,
            "EmergencyContactInformation.txt": emergency_contact_df,
            "ClassInformationEnh.txt": class_info_df,
        }

        self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        result = self.transformer.transform(
            student_schedule_df, enrollments_mapping, "Enrollments", raw_data, global_config
        )

        assert not result.empty
        teacher_rows = result[(result["Role"] == "teacher") & (result["User ID"] == "T099")]
        assert not teacher_rows.empty, (
            "Teacher T099 (Primary=Y in ClassInformation section A1) must be enrolled "
            "as co-teacher on the matching homeroom class"
        )
        # Expect at least one enrollment on homeroom class 100_A1_2025
        matching = teacher_rows[teacher_rows["Class ID"] == "100_A1_2025"]
        assert not matching.empty, f"Expected T099 on 100_A1_2025, got Class IDs: {teacher_rows['Class ID'].tolist()}"

    def test_coteacher_skipped_when_no_section_match(
        self,
        student_schedule_df,
        student_demographic_df,
        staff_info_df,
        course_info_df,
        emergency_contact_df,
        classes_mapping,
        enrollments_mapping,
        global_config,
    ):
        """A Primary=Y row whose Section Letter does not match any homeroom
        should be silently skipped (no orphan enrollment row).
        """
        class_info_df = pd.DataFrame(
            {
                "school number": ["100"],
                "course code": ["MADST01"],
                "teacher id": ["T099"],
                "primary teacher": ["Y"],
                "section letter": ["ZZZ-unmatched"],
                "semester": ["FY"],
                "term": ["1"],
                "day": [""],
                "period": [""],
                "master timetable id": [""],
            }
        )
        raw_data = {
            "StudentDemographicInformation.txt": student_demographic_df,
            "StudentSchedule.txt": student_schedule_df,
            "StaffInformationEnhanced.txt": staff_info_df,
            "CourseInformation.txt": course_info_df,
            "EmergencyContactInformation.txt": emergency_contact_df,
            "ClassInformationEnh.txt": class_info_df,
        }

        self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        result = self.transformer.transform(
            student_schedule_df, enrollments_mapping, "Enrollments", raw_data, global_config
        )

        # T099 should not be enrolled on any class at all
        if not result.empty:
            t099_rows = result[result["User ID"] == "T099"]
            assert t099_rows.empty, f"Unexpected orphan T099 enrollments: {t099_rows.to_dict('records')}"

    def test_coteacher_dedups_against_schedule_path(
        self,
        student_schedule_df,
        student_demographic_df,
        staff_info_df,
        course_info_df,
        emergency_contact_df,
        classes_mapping,
        enrollments_mapping,
        global_config,
    ):
        """If a teacher is already produced by the student_schedule path, the
        ClassInformation path should not create a duplicate row.
        """
        # T001 is the existing homeroom teacher for A1 at school 100 (via demo).
        # Re-registering them as Primary=Y in ClassInformation must not cause
        # a duplicate (Class ID, User ID, Role) row.
        class_info_df = pd.DataFrame(
            {
                "school number": ["100"],
                "course code": ["ATT--AM"],
                "teacher id": ["T001"],
                "primary teacher": ["Y"],
                "section letter": ["A1"],
                "semester": ["FY"],
                "term": ["1"],
                "day": [""],
                "period": [""],
                "master timetable id": [""],
            }
        )
        raw_data = {
            "StudentDemographicInformation.txt": student_demographic_df,
            "StudentSchedule.txt": student_schedule_df,
            "StaffInformationEnhanced.txt": staff_info_df,
            "CourseInformation.txt": course_info_df,
            "EmergencyContactInformation.txt": emergency_contact_df,
            "ClassInformationEnh.txt": class_info_df,
        }

        self.transformer.transform(student_schedule_df, classes_mapping, "Classes", raw_data, global_config)
        result = self.transformer.transform(
            student_schedule_df, enrollments_mapping, "Enrollments", raw_data, global_config
        )

        assert not result.empty
        dupes = result.duplicated(subset=["Class ID", "User ID", "Role"])
        assert dupes.sum() == 0


class TestEnrollmentsExcludedCourseCodes:
    """Enrollments for excluded course codes must be suppressed too, otherwise
    they become orphans (enrollment pointing at a non-existent class).
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_attendance_enrollments_suppressed(
        self, student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
    ):
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

        self.transformer.transform(schedule_with_att, classes_mapping, "Classes", raw_data_with_att, cfg)
        result = self.transformer.transform(
            schedule_with_att, enrollments_mapping, "Enrollments", raw_data_with_att, cfg
        )

        if not result.empty:
            assert "MT_ATT_AM_2025" not in result["Class ID"].values
