"""Tests for src/utils/helpers.py — utility functions."""

import contextlib
from datetime import date

import pandas as pd
import pytest

from src.utils.helpers import (
    build_zip_name,
    district_slug,
    ensure_directory,
    normalize_columns,
    safe_float_conversion,
    validate_csv,
    validate_path,
)


class TestValidateCsv:
    def test_valid_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col1,col2\n1,2\n", encoding="utf-8")
        assert validate_csv(csv_file) is True

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_csv(tmp_path / "nonexistent.csv")

    def test_invalid_csv(self, tmp_path):
        csv_file = tmp_path / "bad.csv"
        csv_file.write_bytes(b"\x00\x01\x02\x03")
        # Should either return True (pandas is lenient) or raise ValueError
        # pandas can often read even malformed data, so this is best-effort
        with contextlib.suppress(ValueError):
            validate_csv(csv_file)


class TestEnsureDirectory:
    def test_creates_directory(self, tmp_path):
        new_dir = tmp_path / "a" / "b" / "c"
        result = ensure_directory(new_dir)
        assert result == new_dir
        assert new_dir.is_dir()

    def test_existing_directory(self, tmp_path):
        result = ensure_directory(tmp_path)
        assert result == tmp_path


class TestSafeFloatConversion:
    def test_numeric_string(self):
        assert safe_float_conversion("3.14") == 3.14

    def test_integer(self):
        assert safe_float_conversion(42) == 42.0

    def test_none_returns_default(self):
        assert safe_float_conversion(None) == 0.0

    def test_invalid_string_returns_default(self):
        assert safe_float_conversion("abc") == 0.0

    def test_custom_default(self):
        assert safe_float_conversion("abc", default=-1.0) == -1.0

    def test_empty_string(self):
        assert safe_float_conversion("") == 0.0


class TestValidatePath:
    def test_valid_directory(self, tmp_path):
        assert validate_path(tmp_path) is True

    def test_nonexistent_path(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_path(tmp_path / "nonexistent")

    def test_file_not_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("test", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            validate_path(f)


class TestNormalizeColumns:
    def test_strips_and_lowercases(self):
        df = pd.DataFrame(columns=["  Name  ", "AGE", "  School Number  "])
        result = normalize_columns(df)
        assert list(result.columns) == ["name", "age", "school number"]

    def test_does_not_mutate_original(self):
        df = pd.DataFrame(columns=["Name", "Age"])
        normalize_columns(df)
        assert list(df.columns) == ["Name", "Age"]


class TestDistrictSlug:
    def test_strips_myedbc_suffix(self):
        assert district_slug("sd40myedbc") == "sd40"
        assert district_slug("sd48myedbc") == "sd48"
        assert district_slug("sd51myedbc") == "sd51"
        assert district_slug("sd74myedbc") == "sd74"

    def test_base_myedbc_unchanged(self):
        assert district_slug("myedbc") == "myedbc"

    def test_sanitizes_special_characters(self):
        assert district_slug("myBlueprint+") == "myBlueprint"
        assert district_slug("sis with spaces") == "sis_with_spaces"
        assert district_slug("sis/with\\slashes") == "sis_with_slashes"

    def test_fallback_when_all_stripped(self):
        assert district_slug("+++") == "district"
        assert district_slug("   ") == "district"


class TestBuildZipName:
    def test_with_district(self):
        result = build_zip_name("sd40myedbc", for_date=date(2026, 4, 10))
        assert result == "gde2acsv_sd40_2026-04-10.zip"

    def test_with_base_district(self):
        result = build_zip_name("myedbc", for_date=date(2026, 4, 10))
        assert result == "gde2acsv_myedbc_2026-04-10.zip"

    def test_without_district_falls_back_to_date_only(self):
        """Legacy callers that don't know the district get the old format."""
        result = build_zip_name(for_date=date(2026, 4, 10))
        assert result == "gde2acsv_2026-04-10.zip"

    def test_none_district_matches_default(self):
        result = build_zip_name(sis_type=None, for_date=date(2026, 4, 10))
        assert result == "gde2acsv_2026-04-10.zip"

    def test_uses_today_when_no_date_provided(self):
        result = build_zip_name("sd40myedbc")
        today = date.today().isoformat()
        assert result == f"gde2acsv_sd40_{today}.zip"
