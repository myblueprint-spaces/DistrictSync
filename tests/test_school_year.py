"""Tests for school year determination and academic date generation.

The codebase uses MyEd BC's end-year convention: ``school_year`` is the
calendar year the academic period ENDS in. ``school_year=2026`` therefore
means the 2025-2026 academic year (Aug 2025 - Jul 2026).
"""

from datetime import date
from unittest.mock import patch

import pandas as pd

from src.etl.transformer import DataTransformer
from src.etl.transformers.base import BaseTransformer


class TestSetSchoolYear:
    def test_sets_year_and_dates(self):
        """school_year=2026 → 2025-08-25 / 2026-07-25 (end-year convention)."""
        t = DataTransformer()
        t.set_school_year(2026, "08-25", "07-25")
        assert t.school_year == 2026
        assert t.academic_start == "2025-08-25"
        assert t.academic_end == "2026-07-25"

    def test_different_year(self):
        t = DataTransformer()
        t.set_school_year(2024, "08-25", "07-25")
        assert t.school_year == 2024
        assert t.academic_start == "2023-08-25"
        assert t.academic_end == "2024-07-25"

    def test_custom_month_day_overrides(self):
        t = DataTransformer()
        t.set_school_year(2026, start_month_day="09-01", end_month_day="06-30")
        assert t.academic_start == "2025-09-01"
        assert t.academic_end == "2026-06-30"


class TestDetermineSchoolYear:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_slash_format_returns_end_year(self):
        """'2025/2026' → 2026 (end year)."""
        df = pd.DataFrame({"school year": ["2025/2026", "2025/2026"]})
        raw_data = {"StudentSchedule.txt": df}
        source_config = {"student_schedule": "StudentSchedule.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert year == 2026

    def test_dash_format_returns_end_year(self):
        """'2025-2026' → 2026."""
        df = pd.DataFrame({"school year": ["2025-2026"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert year == 2026

    def test_single_year_returned_as_end(self):
        """'2026' is already the end year per MyEd BC convention."""
        df = pd.DataFrame({"school year": ["2026"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert year == 2026

    def test_fallback_september(self):
        """September is past default rollover (07-25) → next academic year ends next year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 9, 15),
            rollover_month_day="07-25",
        )
        assert year == 2026

    def test_fallback_january(self):
        """January is before rollover → current academic year ends this calendar year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2026, 1, 10),
            rollover_month_day="07-25",
        )
        assert year == 2026

    def test_fallback_august(self):
        """August is past default rollover (07-25) → next academic year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 8, 1),
            rollover_month_day="07-25",
        )
        assert year == 2026

    def test_fallback_just_before_rollover(self):
        """One day before default rollover → still current academic year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 7, 24),
            rollover_month_day="07-25",
        )
        assert year == 2025

    def test_fallback_on_rollover_rolls_forward(self):
        """On the rollover date → next academic year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 7, 25),
            rollover_month_day="07-25",
        )
        assert year == 2026

    def test_fallback_with_early_rollover(self):
        """Custom early rollover (07-01) accommodates districts uploading next year on Jul 5."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 7, 5),
            rollover_month_day="07-01",
        )
        assert year == 2026

    def test_fallback_with_late_rollover(self):
        """Custom late rollover (08-15) keeps Aug 1 in the previous year."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 8, 1),
            rollover_month_day="08-15",
        )
        assert year == 2025

    def test_fallback_invalid_rollover_falls_back_to_aug_1(self):
        """Malformed rollover should not crash — defaults to Aug 1 cutoff."""
        year = self.transformer.determine_school_year(
            {"file.txt": pd.DataFrame({"other": [1]})},
            {"role": "file.txt"},
            today=date(2025, 8, 1),
            rollover_month_day="not-a-date",
        )
        assert year == 2026  # Aug 1 == fallback rollover → rolls forward

    def test_missing_school_year_column_triggers_fallback(self):
        raw_data = {"file.txt": pd.DataFrame({"grade": ["10"]})}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert isinstance(year, int)

    def test_empty_dataframe_triggers_fallback(self):
        raw_data = {"file.txt": pd.DataFrame()}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert isinstance(year, int)

    def test_nan_school_year_skipped(self):
        df = pd.DataFrame({"school year": [None, float("nan")]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        assert isinstance(year, int)

    def test_unparseable_value_falls_through(self):
        """A garbage cell shouldn't terminate the search — fallback applies."""
        df = pd.DataFrame({"school year": ["not-a-year"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(
            raw_data, source_config, today=date(2026, 6, 1), rollover_month_day="07-25"
        )
        assert year == 2026  # June < default rollover → today's calendar year

    def test_uses_real_now_when_today_not_passed(self):
        """When ``today`` is omitted, falls back to datetime.now(). Smoke test."""
        with patch("src.etl.transformers.base.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value = date(2025, 9, 15)
            year = self.transformer.determine_school_year(
                {"file.txt": pd.DataFrame({"other": [1]})},
                {"role": "file.txt"},
                rollover_month_day="07-25",
            )
        assert year == 2026  # Sep past Jul 25 default rollover


class TestParseSchoolYearToEnd:
    """Direct tests of the static parser, independent of the transformer."""

    def test_slash_format(self):
        assert BaseTransformer._parse_school_year_to_end("2025/2026") == 2026

    def test_dash_format(self):
        assert BaseTransformer._parse_school_year_to_end("2025-2026") == 2026

    def test_single_year_end_naming(self):
        """Default naming='end' (MyEd BC): bare 2026 is already the end year."""
        assert BaseTransformer._parse_school_year_to_end("2026") == 2026
        assert BaseTransformer._parse_school_year_to_end("2026", "end") == 2026

    def test_single_year_start_naming(self):
        """naming='start' (Ontario/US): bare 2025 names the start; translate to end."""
        assert BaseTransformer._parse_school_year_to_end("2025", "start") == 2026

    def test_range_ignores_naming(self):
        """YYYY/YYYY is unambiguous — naming setting doesn't apply."""
        assert BaseTransformer._parse_school_year_to_end("2025/2026", "end") == 2026
        assert BaseTransformer._parse_school_year_to_end("2025/2026", "start") == 2026

    def test_whitespace_trimmed(self):
        assert BaseTransformer._parse_school_year_to_end("  2026  ") == 2026

    def test_three_digit_returns_none(self):
        assert BaseTransformer._parse_school_year_to_end("202") is None

    def test_empty_returns_none(self):
        assert BaseTransformer._parse_school_year_to_end("") is None

    def test_garbage_returns_none(self):
        assert BaseTransformer._parse_school_year_to_end("not-a-year") is None

    def test_three_part_slash_returns_none(self):
        """Only two-part splits are valid academic-year ranges."""
        assert BaseTransformer._parse_school_year_to_end("2024/2025/2026") is None


class TestSchoolYearNamingConvention:
    """Integration tests for the school_year_naming knob via determine_school_year."""

    def setup_method(self):
        self.transformer = DataTransformer()

    def test_start_naming_translates_single_year(self):
        """Ontario-style: source has '2025' meaning the 2025-2026 academic year."""
        df = pd.DataFrame({"school year": ["2025"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year = self.transformer.determine_school_year(
            raw_data, source_config, school_year_naming="start", rollover_month_day="07-25"
        )
        assert year == 2026

    def test_end_naming_is_default(self):
        """Without specifying naming, MyEd BC end-year semantics apply."""
        df = pd.DataFrame({"school year": ["2025"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        year_default = self.transformer.determine_school_year(raw_data, source_config, rollover_month_day="07-25")
        year_explicit = self.transformer.determine_school_year(
            raw_data, source_config, school_year_naming="end", rollover_month_day="07-25"
        )
        assert year_default == year_explicit == 2025

    def test_range_unaffected_by_naming(self):
        """A '2025/2026' range always parses to 2026 regardless of naming."""
        df = pd.DataFrame({"school year": ["2025/2026"]})
        raw_data = {"file.txt": df}
        source_config = {"role": "file.txt"}

        for naming in ("end", "start"):
            year = self.transformer.determine_school_year(
                raw_data, source_config, school_year_naming=naming, rollover_month_day="07-25"
            )
            assert year == 2026, f"naming={naming!r} produced {year}"
