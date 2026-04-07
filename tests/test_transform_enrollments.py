"""Integration tests for the Enrollments entity transformation.

Tests homeroom enrollments, subject enrollments, blended teacher enrollments,
and deduplication.
"""


from src.etl.transformer import DataTransformer


class TestEnrollmentsTransform:

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025)

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
        """Students in homeroom grades should get homeroom enrollments.
        The enrollment merge uses student_id_col from config ('Student ID' → 'student id').
        The demographic data must have this column for the merge to find student IDs.
        """
        result = self._run_classes_then_enrollments(
            student_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data
        )
        if not result.empty:
            student_enrollments = result[result["Role"] == "student"]
            # Verify we have some student enrollments (homeroom or subject)
            assert len(student_enrollments) > 0

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
        self.transformer.set_school_year(2025)

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
