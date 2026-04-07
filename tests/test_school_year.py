"""Tests for school year determination and academic date generation."""

from datetime import datetime
from unittest.mock import patch

import pandas as pd

from src.etl.transformer import DataTransformer


class TestSetSchoolYear:
    def test_sets_year_and_dates(self):
        t = DataTransformer()
        t.set_school_year(2025)
        assert t.school_year == 2025
        assert t.academic_start == "2025-08-25"
        assert t.academic_end == "2026-07-25"

    def test_different_year(self):
        t = DataTransformer()
        t.set_school_year(2023)
        assert t.school_year == 2023
        assert t.academic_start == "2023-08-25"
        assert t.academic_end == "2024-07-25"


class TestDetermineSchoolYear:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_extracts_year_from_data(self):
        """Should extract year from 'school year' column in the source file."""
        df = pd.DataFrame({"school year": ["2025/2026", "2025/2026"]})
        raw_data = {"StudentSchedule.txt": df}
        source_config = {"student_schedule": "StudentSchedule.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2025

    def test_extracts_year_from_four_digit_string(self):
        df = pd.DataFrame({"school year": ["2024"]})
        raw_data = {"StudentSchedule.txt": df}
        source_config = {"student_schedule": "StudentSchedule.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2024

    @patch("src.etl.transformers.base.datetime")
    def test_fallback_september(self, mock_dt):
        """In September, school year should be current year."""
        mock_dt.now.return_value = datetime(2025, 9, 15)
        raw_data = {"file.txt": pd.DataFrame({"other_col": [1]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2025

    @patch("src.etl.transformers.base.datetime")
    def test_fallback_january(self, mock_dt):
        """In January, school year should be previous year."""
        mock_dt.now.return_value = datetime(2026, 1, 10)
        raw_data = {"file.txt": pd.DataFrame({"other_col": [1]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2025

    @patch("src.etl.transformers.base.datetime")
    def test_fallback_august(self, mock_dt):
        """August is the cutoff — should be current year."""
        mock_dt.now.return_value = datetime(2025, 8, 1)
        raw_data = {"file.txt": pd.DataFrame({"other_col": [1]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2025

    @patch("src.etl.transformers.base.datetime")
    def test_fallback_july(self, mock_dt):
        """July is before cutoff — should be previous year."""
        mock_dt.now.return_value = datetime(2025, 7, 31)
        raw_data = {"file.txt": pd.DataFrame({"other_col": [1]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert year == 2024

    def test_missing_school_year_column_triggers_fallback(self):
        """When no 'school year' column exists, should use date-based fallback."""
        raw_data = {"file.txt": pd.DataFrame({"grade": ["10"]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        # Should return some integer (current or previous year)
        assert isinstance(year, int)

    def test_empty_dataframe_triggers_fallback(self):
        raw_data = {"file.txt": pd.DataFrame()}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config)
        assert isinstance(year, int)

    def test_nan_school_year_skipped(self):
        """NaN values in school year column should be skipped."""
        df = pd.DataFrame({"school year": [None, float("nan")]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        # Should fall through to date-based logic since all values are NaN
        year = self.transformer.determine_school_year(raw_data, source_config)
        assert isinstance(year, int)
