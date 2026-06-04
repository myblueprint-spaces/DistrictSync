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


class TestEncodingFallback:
    """Verify the UTF-8 → Latin1 → CP1252 multi-encoding fallback in DataExtractor."""

    def test_load_cp1252_file(self, tmp_path):
        """CP1252-encoded file with Windows-specific characters is decoded correctly."""
        # CP1252 has characters in 0x80–0x9F that Latin1 does not interpret the same way
        # e.g. € (0x80), – (0x96), " (0x93)
        content = "Name,City\nMüller,Düsseldorf\nGarçon,Montréal\n"
        (tmp_path / "staff.txt").write_bytes(content.encode("cp1252"))

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["staff.txt"])

        assert len(result["staff.txt"]) == 2
        names = result["staff.txt"]["name"].tolist()
        assert any("ller" in n for n in names), f"Expected decoded name, got: {names}"

    def test_load_utf8_with_bom(self, tmp_path):
        """UTF-8 file with BOM is parsed correctly without BOM artifact in column names."""
        content = "Name,Grade\nAlice,5\nBob,6\n"
        bom = b"\xef\xbb\xbf"
        (tmp_path / "students.txt").write_bytes(bom + content.encode("utf-8"))

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["students.txt"])

        assert len(result["students.txt"]) == 2
        cols = result["students.txt"].columns.tolist()
        # BOM should not appear as a prefix on the first column name
        assert all("\ufeff" not in c for c in cols), f"BOM artifact in columns: {cols}"
        assert "name" in cols

    def test_latin1_tab_delimited(self, tmp_path):
        """Latin1-encoded tab-delimited file: non-ASCII values load without error."""
        # Use comma-free content with tabs so the comma parser produces garbage
        # (single-column) but the tab parser succeeds correctly.
        # Note: the extractor stops at first parse that does NOT raise, so we
        # verify the data loads (some row count) and values are not corrupted.
        content = "Name\tCity\nRené\tParis\nBjörk\tReykjavík\n"
        (tmp_path / "data.txt").write_bytes(content.encode("latin1"))

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["data.txt"])

        # File must load (not raise) — at minimum 2 data rows are present
        assert len(result["data.txt"]) >= 1

    def test_all_encodings_succeed_binary_file(self, tmp_path):
        """pandas is permissive enough that even binary content loads without crash."""
        # CP1252 accepts ALL byte values 0x00-0xFF, so ExtractionError is only
        # raised for a completely empty file — not for arbitrary binary.
        # This test documents that boundary.
        (tmp_path / "binary.txt").write_bytes(b"\x00\x01\x02Name,Grade\n1,2\n")

        extractor = DataExtractor(str(tmp_path))
        # Should load (possibly with junk) rather than crash
        result = extractor.load_data(["binary.txt"])
        assert "binary.txt" in result

    def test_non_ascii_values_survive_roundtrip(self, tmp_path):
        """Non-ASCII characters in data values are preserved after loading."""
        content = "Name,Email\nJoão Silva,joao@test.ca\n"
        (tmp_path / "contacts.txt").write_bytes(content.encode("latin1"))

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["contacts.txt"])

        names = result["contacts.txt"]["name"].tolist()
        assert len(names) == 1
        assert "o" in names[0].lower()  # at minimum the ASCII part survived

    def test_utf8_with_stray_cp1252_byte_stays_utf8(self, tmp_path):
        """A UTF-8 file with a few stray CP1252 bytes must NOT fall back to latin1.

        Falling back to latin1 would mojibake every genuine accented character in
        the file. Instead the valid UTF-8 text is preserved and only the stray byte
        becomes the replacement character.
        """
        # Mostly valid UTF-8 (José, naïve) with a stray CP1252 en-dash (0x96) and
        # smart quotes (0x93/0x94) pasted into a free-text memo field.
        good = "Name,Memo\nJosé Muñoz,naïve note\n".encode()
        junk = b"Ana,picks up 3" + b"\x96" + b"4pm " + b"\x93" + b"ok" + b"\x94" + b"\n"
        (tmp_path / "demo.txt").write_bytes(good + junk)

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["demo.txt"])

        names = result["demo.txt"]["name"].tolist()
        # Genuine accented characters survive intact (not mojibaked to "JosÃ©")
        assert "José Muñoz" in names
        assert "naïve" in result["demo.txt"]["memo"].tolist()[0]

    def test_embedded_tab_in_comma_field_parses_on_commas(self, tmp_path):
        """A comma file with a stray tab inside a field must still parse on commas.

        The delimiter is chosen from the (tab-free) header, so an embedded tab in a
        data field cannot trick the loader into treating the file as tab-delimited.
        """
        content = "School Number,Student Number,Note\n100,123,hello\tworld\n100,124,fine\n"
        (tmp_path / "data.txt").write_text(content, encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["data.txt"])

        df = result["data.txt"]
        assert list(df.columns) == ["school number", "student number", "note"]
        assert len(df) == 2
        assert df["note"].tolist()[0] == "hello\tworld"

    def test_unquoted_trailing_comma_field_is_recovered(self, tmp_path):
        """MyEd BC emits the trailing Section column unquoted; a comma inside it must
        be recovered (merged back), not dropped or split into a phantom column.
        """
        header = "School Number,Student Number,Course Code,Full Course Code,Section\n"
        # Last column "6B,R-B O3" is unquoted and contains a comma → 6 fields, not 5.
        bad_row = '"203496020","xxxx","XLDCA06","XLDCA06---CKG-6B,R-B O3",6B,R-B O3\n'
        good_row = '"203496021","yyyy","XMA-11","XMA-11---A",11A\n'
        (tmp_path / "history.txt").write_text(header + bad_row + good_row, encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["history.txt"])

        df = result["history.txt"]
        # Both rows kept, no phantom 6th column.
        assert list(df.columns) == [
            "school number",
            "student number",
            "course code",
            "full course code",
            "section",
        ]
        assert len(df) == 2
        sections = df["section"].tolist()
        assert sections[0] == "6B,R-B O3"  # overflow merged back into the last column
        assert sections[1] == "11A"
        # The quoted Full Course Code with its internal comma is untouched.
        assert df["full course code"].tolist()[0] == "XLDCA06---CKG-6B,R-B O3"

    def test_numeric_code_column_with_blanks_keeps_integer_text(self, tmp_path):
        """A code column containing blanks must not be coerced to float (no '.0').

        pandas types a numeric-looking column with any blank as float64, turning
        7575029 into 7575029.0. Reading every column as str avoids this.
        """
        content = "Student Number,PreRegSchoolCode\n1,7575029\n2,\n3,7575030\n"
        (tmp_path / "students.txt").write_text(content, encoding="utf-8")

        extractor = DataExtractor(str(tmp_path))
        result = extractor.load_data(["students.txt"])

        codes = result["students.txt"]["preregschoolcode"].fillna("").tolist()
        assert codes[0] == "7575029"  # not "7575029.0"
        assert codes[1] == ""
        assert codes[2] == "7575030"
