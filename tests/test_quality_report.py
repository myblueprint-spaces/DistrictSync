"""Tests for the data quality report module."""

import pandas as pd

from src.quality.report import DataQualityReport


class TestDataQualityReport:
    def test_basic_report_structure(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1", "S2"], "Grade": ["01", "02"]}),
        }
        report = DataQualityReport().analyze(outputs)
        assert "Students" in report.entities
        assert report.entities["Students"].row_count == 2

    def test_detects_missing_fields(self):
        outputs = {
            "Students": pd.DataFrame(
                {
                    "User ID": ["S1", "S2", "S3"],
                    "Email": [None, "", "a@b.com"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert "Email" in report.entities["Students"].missing_fields
        assert report.entities["Students"].missing_fields["Email"] == 2

    def test_detects_duplicates(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1", "S1", "S2"]}),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["Students"].duplicate_count == 2

    def test_detects_enrollment_duplicates(self):
        outputs = {
            "Enrollments": pd.DataFrame(
                {
                    "Class ID": ["C1", "C1", "C2"],
                    "User ID": ["S1", "S1", "S2"],
                    "Role": ["student", "student", "student"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["Enrollments"].duplicate_count == 2

    def test_orphaned_class_ids(self):
        outputs = {
            "Classes": pd.DataFrame({"Class ID": ["C1", "C2"]}),
            "Enrollments": pd.DataFrame(
                {
                    "Class ID": ["C1", "C3"],
                    "User ID": ["S1", "S2"],
                    "Role": ["student", "student"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert any("class IDs not found" in w for w in report.cross_entity_warnings)

    def test_orphaned_user_ids(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1"]}),
            "Staff": pd.DataFrame({"User ID": ["T1"]}),
            "Enrollments": pd.DataFrame(
                {
                    "Class ID": ["C1"],
                    "User ID": ["UNKNOWN_USER"],
                    "Role": ["student"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert any("user IDs not found" in w for w in report.cross_entity_warnings)

    def test_no_orphan_warning_when_users_match(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1"]}),
            "Enrollments": pd.DataFrame(
                {
                    "Class ID": ["C1"],
                    "User ID": ["S1"],
                    "Role": ["student"],
                }
            ),
            "Classes": pd.DataFrame({"Class ID": ["C1"]}),
        }
        report = DataQualityReport().analyze(outputs)
        assert len(report.cross_entity_warnings) == 0

    def test_grade_distribution_warning(self):
        outputs = {
            "Students": pd.DataFrame(
                {
                    "User ID": ["S1", "S2", "S3"],
                    "Grade": ["01", "01", "12"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert any("1 student" in w for w in report.cross_entity_warnings)

    def test_to_text_output(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1", "S2"]}),
        }
        report = DataQualityReport().analyze(outputs)
        text = report.to_text()
        assert "DATA QUALITY REPORT" in text
        assert "Students" in text
        assert "Rows: 2" in text

    def test_empty_outputs(self):
        report = DataQualityReport().analyze({})
        assert len(report.entities) == 0
        assert "DATA QUALITY REPORT" in report.to_text()

    def test_unknown_entity_duplicate_detection(self):
        # Use a clearly synthetic entity name — CourseInfo and StudentCourses
        # are now in key_map, so they'd no longer exercise the heuristic.
        outputs = {
            "UnregisteredCatalog": pd.DataFrame(
                {
                    "Course Code": ["MATH10", "ENG11", "MATH10"],
                    "Course Name": ["Math 10", "English 11", "Math 10 Dup"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["UnregisteredCatalog"].duplicate_count == 2

    def test_unknown_entity_id_column_heuristic(self):
        outputs = {
            "UnregisteredEnrollments": pd.DataFrame(
                {
                    "Student ID": ["S1", "S1", "S2"],
                    "Course Code": ["MATH10", "ENG11", "MATH10"],
                    "Grade": ["10", "11", "10"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        # S1/MATH10 and S2/MATH10 are unique combos, no duplicates
        assert report.entities["UnregisteredEnrollments"].duplicate_count == 0

    def test_courseinfo_duplicate_detection_uses_explicit_key_map(self):
        """CourseInfo is in key_map with ['Course Code', 'School ID'] — same code/school is a dup."""
        outputs = {
            "CourseInfo": pd.DataFrame(
                {
                    "Course Code": ["MATH10", "MATH10", "ENG11"],
                    "School ID": ["100", "100", "100"],  # rows 0/1 dup
                    "Course Name": ["Math 10", "Math 10 dup", "English 11"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["CourseInfo"].duplicate_count == 2

    def test_studentcourses_duplicate_detection_uses_explicit_key_map(self):
        """StudentCourses uses ['Student ID', 'Course Code', 'Completion Date'] as key."""
        outputs = {
            "StudentCourses": pd.DataFrame(
                {
                    "Student ID": ["S1", "S1", "S1"],
                    "Course Code": ["MATH10", "MATH10", "ENG11"],
                    "Completion Date": ["30-Jan-2025", "30-Jan-2025", "30-Jan-2025"],  # rows 0/1 dup
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["StudentCourses"].duplicate_count == 2

    def test_orphaned_family_student_ids(self):
        """Family rows referencing students absent from Students warn (count only)."""
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1"]}),
            "Family": pd.DataFrame(
                {
                    "First Name": ["John", "Mary"],
                    "Student User ID": ["S1", "S999"],
                    "Email": ["j@x.com", "m@x.com"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        warnings = [w for w in report.cross_entity_warnings if "Family student IDs" in w]
        assert warnings == ["1 Family student IDs not found in Students output"]
        # PII rule: counts only, never the student id itself.
        assert "S999" not in warnings[0]

    def test_orphaned_studentcourses_student_ids(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1"]}),
            "StudentCourses": pd.DataFrame(
                {
                    "Student ID": ["S1", "S998", "S999"],
                    "Course Code": ["MATH10", "ENG11", "SCI10"],
                    "Completion Date": ["", "", ""],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert "2 StudentCourses student IDs not found in Students output" in report.cross_entity_warnings

    def test_no_student_ref_warning_when_all_rostered(self):
        outputs = {
            "Students": pd.DataFrame({"User ID": ["S1", "S2"]}),
            "Family": pd.DataFrame({"Student User ID": ["S1", "S2"], "Email": ["a@x.com", "b@x.com"]}),
            "StudentCourses": pd.DataFrame({"Student ID": ["S1"], "Course Code": ["MATH10"], "Completion Date": [""]}),
        }
        report = DataQualityReport().analyze(outputs)
        assert not any("student IDs not found" in w for w in report.cross_entity_warnings)

    def test_no_student_ref_warning_without_students_output(self):
        """mbponly-style runs (no Students output) must not warn on StudentCourses."""
        outputs = {
            "StudentCourses": pd.DataFrame({"Student ID": ["S1"], "Course Code": ["MATH10"], "Completion Date": [""]}),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.cross_entity_warnings == []


class TestMissingFieldWarningThreshold:
    """Pin the >50%-missing per-entity warning (report.py `_check_missing_fields`).

    The warning fires only STRICTLY above 50% — exactly 50% still counts in
    ``missing_fields`` but must stay warning-silent (the documented boundary).
    """

    def test_over_50_percent_missing_emits_the_pinned_warning(self):
        # 3 of 4 Email values missing (None + "" + None) = 75% → warn.
        outputs = {
            "Students": pd.DataFrame(
                {
                    "User ID": ["S1", "S2", "S3", "S4"],
                    "Email": [None, "", None, "a@b.com"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["Students"].missing_fields["Email"] == 3
        assert "Email: 75% missing (3/4)" in report.entities["Students"].warnings

    def test_exactly_50_percent_missing_does_not_warn(self):
        # 2 of 4 missing = exactly 50% → counted, but NO warning (strict > boundary).
        outputs = {
            "Students": pd.DataFrame(
                {
                    "User ID": ["S1", "S2", "S3", "S4"],
                    "Email": [None, "", "a@b.com", "b@c.com"],
                }
            ),
        }
        report = DataQualityReport().analyze(outputs)
        assert report.entities["Students"].missing_fields["Email"] == 2
        assert report.entities["Students"].warnings == []
