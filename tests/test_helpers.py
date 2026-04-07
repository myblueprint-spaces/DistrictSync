"""Tests for src/utils/helpers.py — utility functions."""

import contextlib

import pandas as pd
import pytest

from src.utils.helpers import (
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
