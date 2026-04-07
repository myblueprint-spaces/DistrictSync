"""Tests for the DataExtractor — file loading with encoding/delimiter fallback."""

import pytest

from src.etl.extractor import DataExtractor, ExtractionError


class TestDataExtractor:

    def test_load_utf8_comma_csv(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Name,Grade\nAlice,5\nBob,6\n", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["test.txt"])

        assert "test.txt" in result
        assert len(result["test.txt"]) == 2
        assert "name" in result["test.txt"].columns  # Normalized to lowercase

    def test_load_tab_separated(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Name\tGrade\nAlice\t5\nBob\t6\n", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["test.txt"])

        assert len(result["test.txt"]) == 2

    def test_load_latin1_encoding(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes("Name,Grade\nRené,5\nBjörk,6\n".encode("latin1"))

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["test.txt"])

        assert len(result["test.txt"]) == 2

    def test_missing_file_returns_empty(self, tmp_path):
        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["nonexistent.txt"])

        assert "nonexistent.txt" in result
        assert result["nonexistent.txt"].empty

    def test_column_names_normalized(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("  Student Number  , Grade ,School Number\n123,5,100\n", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["test.txt"])

        cols = result["test.txt"].columns.tolist()
        assert "student number" in cols
        assert "grade" in cols
        assert "school number" in cols

    def test_multiple_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("Col1\n1\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("Col2\n2\n", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["a.txt", "b.txt"])

        assert len(result) == 2
        assert len(result["a.txt"]) == 1
        assert len(result["b.txt"]) == 1

    def test_empty_file_raises_extraction_error(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        with pytest.raises(ExtractionError, match="could not be parsed"):
            extractor.load_data(["empty.txt"])

    def test_headers_only_file_returns_empty_dataframe(self, tmp_path):
        f = tmp_path / "headers.txt"
        f.write_text("Name,Grade,School\n", encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["headers.txt"])

        assert "headers.txt" in result
        assert len(result["headers.txt"]) == 0
        assert "name" in result["headers.txt"].columns
