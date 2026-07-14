"""Tests for class ID generation, class name generation, and name truncation."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestTruncateName:
    def test_short_name_unchanged(self):
        assert DataTransformer._truncate_name("Short Name") == "Short Name"

    def test_exact_limit_unchanged(self):
        name = "x" * 100
        assert DataTransformer._truncate_name(name) == name

    def test_truncates_at_word_boundary(self):
        name = "A " + "word " * 25  # Well over 100 chars
        result = DataTransformer._truncate_name(name)
        assert len(result) <= 100
        assert result.endswith("...")
        # Should not cut in the middle of "word"
        assert not result.endswith("wor...")

    def test_hard_truncate_single_long_word(self):
        name = "x" * 150
        result = DataTransformer._truncate_name(name)
        assert len(result) == 100
        assert result.endswith("...")

    def test_custom_max_len(self):
        name = "This is a test string that is longer than fifty characters easily"
        result = DataTransformer._truncate_name(name, max_len=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_empty_string(self):
        assert DataTransformer._truncate_name("") == ""


class TestGenerateClassId:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_without_year(self):
        row = pd.Series({"master timetable id": "MT001"})
        result = self.transformer.generate_class_id(row, "master timetable id", append_year=False)
        assert result == "MT001"

    def test_with_year(self):
        row = pd.Series({"master timetable id": "MT001"})
        result = self.transformer.generate_class_id(row, "master timetable id", append_year=True)
        assert result == "MT001_2025"

    def test_missing_column(self):
        row = pd.Series({"other_col": "value"})
        result = self.transformer.generate_class_id(row, "master timetable id", append_year=True)
        # get() returns "" for missing key, empty string is falsy so no year appended
        assert result == ""

    def test_empty_mt_id_no_year_appended(self):
        row = pd.Series({"master timetable id": ""})
        result = self.transformer.generate_class_id(row, "master timetable id", append_year=True)
        assert result == ""


class TestGenerateClassName:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_full_name_with_teacher_and_section(self):
        row = pd.Series(
            {
                "primary teacher": "Y",
                "last name": "Harper",
                "title": "Science 7",
                "section letter": "A",
            }
        )
        result = self.transformer.generate_class_name(row, "primary teacher", "last name", "title", "section letter")
        assert result == "Harper Science 7 (A) 2025"

    def test_no_teacher_flag_column(self):
        """When teacher flag column doesn't exist, should still use teacher name."""
        row = pd.Series(
            {
                "last name": "Reed",
                "title": "English 7",
                "section letter": "B",
            }
        )
        result = self.transformer.generate_class_name(row, "", "last name", "title", "section letter")
        assert result == "Reed English 7 (B) 2025"

    def test_teacher_flag_not_primary(self):
        """When teacher flag is 'N', teacher name should be excluded."""
        row = pd.Series(
            {
                "primary teacher": "N",
                "last name": "Harper",
                "title": "Science 7",
                "section letter": "A",
            }
        )
        result = self.transformer.generate_class_name(row, "primary teacher", "last name", "title", "section letter")
        assert "Harper" not in result
        assert "Science 7 (A) 2025" in result

    def test_nan_teacher_name(self):
        row = pd.Series(
            {
                "primary teacher": "Y",
                "last name": float("nan"),
                "title": "Math 10",
                "section letter": "A",
            }
        )
        result = self.transformer.generate_class_name(row, "primary teacher", "last name", "title", "section letter")
        assert "nan" not in result.lower()
        assert "Math 10 (A) 2025" in result

    def test_missing_title_uses_unknown_course(self):
        row = pd.Series(
            {
                "primary teacher": "Y",
                "last name": "Smith",
                "section letter": "A",
            }
        )
        result = self.transformer.generate_class_name(row, "primary teacher", "last name", "", "section letter")
        assert "Unknown Course" in result

    def test_no_section(self):
        row = pd.Series(
            {
                "primary teacher": "Y",
                "last name": "Harper",
                "title": "Science 7",
                "section letter": "",
            }
        )
        result = self.transformer.generate_class_name(row, "primary teacher", "last name", "title", "section letter")
        assert result == "Harper Science 7 2025"

    def test_long_name_truncated(self):
        row = pd.Series(
            {
                "last name": "Verylonglastnameington",
                "title": "Advanced Placement International Baccalaureate Science and Technology Course Extended",
                "section letter": "ABCDE",
            }
        )
        result = self.transformer.generate_class_name(row, "", "last name", "title", "section letter")
        assert len(result) <= 100
