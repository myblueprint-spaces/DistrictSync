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
