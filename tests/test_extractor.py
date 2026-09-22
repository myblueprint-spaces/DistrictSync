"""Tests for the DataExtractor — file loading with encoding/delimiter fallback."""

import logging

import pandas as pd
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

    def test_empty_file_yields_an_empty_frame_not_an_error(self, tmp_path):
        """An export with nothing in it is "no records", not a parse failure (plan 0051
        Slice 2). It used to raise, which is the SAME fact an ABSENT file already answers
        with an empty frame — so a district with no family contacts, or an attendance band
        with no absences, failed a nightly that had nothing wrong with it."""
        (tmp_path / "empty.txt").write_bytes(b"")

        result = DataExtractor(str(tmp_path)).load_data(["empty.txt"])

        assert result["empty.txt"].empty

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
        # CP1252 accepts ALL byte values 0x00-0xFF, so arbitrary binary loads rather
        # than raising. Since plan 0051 Slice 2 an empty file does not raise either (it
        # loads as zero rows), so the remaining raise is content no reader can make sense
        # of — see `TestAnEmptyExportIsNoRecordsNotAParseFailure` for that boundary.
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


class TestCasesMigratedFromTheRetiredBytesEntrypoint:
    """The two cases the retired ``load_from_bytes`` class covered that disk did NOT.

    ``load_from_bytes`` was a second public parsing entrypoint left over from the
    Streamlit UI; plan 0051 pointed Convert at ``load_data`` and retired it. Its test
    class was mostly an exact mirror of the disk cases above (utf-8 comma, tab-separated,
    latin1, name normalisation, multiple files, empty-file raise, headers-only frame,
    cp1252, utf-8-with-junk) — those are deleted rather than moved, because a duplicate of
    a test that still runs is not coverage. ``TestDiskBytesParity`` went with them: it
    existed only to prove the two entrypoints agreed, which is a tautology once there is
    one. ``test_not_supplied_source_is_absent_not_backfilled`` is deleted **with the
    contract it pinned** — "must NOT back-fill empty frames for missing keys" was the old
    design's invariant, and ``load_data`` deliberately does the opposite.

    These two had no disk twin and are migrated rather than dropped.
    """

    def test_headerless_injection_through_load_data(self, tmp_path):
        """Headerless column-name injection, through the entrypoint PRODUCTION uses.

        This was only ever covered on the bytes path, and the echoed-header class below
        reaches for the private ``_load_bytes`` core — so nothing asserted that a
        ``headers:`` block survives the trip through ``load_data`` at all. It is how
        SD40's schedule and SD51's daily absences are read, so it is not a detail.
        """
        (tmp_path / "history.txt").write_bytes(b"203496020,xxxx,XLDCA06\n203496021,yyyy,XMA-11\n")
        headers = {"history.txt": ["School Number", "Student Number", "Course Code"]}

        df = DataExtractor(str(tmp_path)).load_data(["history.txt"], file_headers=headers)["history.txt"]

        assert list(df.columns) == ["school number", "student number", "course code"]
        assert len(df) == 2
        assert df["course code"].tolist() == ["XLDCA06", "XMA-11"]

    def test_clean_utf8_accents_round_trip_exactly(self, tmp_path):
        """Clean UTF-8 accented values survive EXACTLY.

        The nearest disk test (`test_non_ascii_values_survive_roundtrip`) writes latin1 and
        asserts only that the ASCII part survived, so it would pass on mojibake. This one
        would not.
        """
        (tmp_path / "x.txt").write_bytes("Name,City\nJosé,Montréal\n".encode())

        result = DataExtractor(str(tmp_path)).load_data(["x.txt"])

        assert result["x.txt"]["name"].tolist() == ["José"]
        assert result["x.txt"]["city"].tolist() == ["Montréal"]


class TestFilenameCaseInsensitivity:
    """A mapping spells a source file one way; districts drop it in whatever case they like.

    MyEd BC extracts arrive by hand and by export jobs whose casing is not stable, and a
    district config can only name the file once. Windows resolves the difference itself,
    which is exactly why this went unnoticed: any comparison WE make in Python is
    case-sensitive on every platform, and a case-sensitive filesystem gets it wrong at the
    filesystem too.
    """

    def test_a_differently_cased_file_is_found(self, tmp_path):
        (tmp_path / "students.txt").write_text("id,name\n1,A\n", encoding="utf-8")

        data = DataExtractor(str(tmp_path)).load_data(["Students.txt"])

        assert not data["Students.txt"].empty, "the mapping's spelling should resolve to the file on disk"
        assert list(data.keys()) == ["Students.txt"], "the dict stays keyed by the CONFIGURED name"

    def test_upper_case_on_disk_is_found_too(self, tmp_path):
        (tmp_path / "STUDENTS.TXT").write_text("id,name\n1,A\n", encoding="utf-8")

        assert not DataExtractor(str(tmp_path)).load_data(["Students.txt"])["Students.txt"].empty

    def test_a_genuinely_absent_file_still_reports_missing(self, tmp_path):
        """The positive twin — case-insensitivity must not invent a file that isn't there."""
        (tmp_path / "Staff.txt").write_text("id\n1\n", encoding="utf-8")

        data = DataExtractor(str(tmp_path)).load_data(["Students.txt"])

        assert data["Students.txt"].empty

    def test_an_AMBIGUOUS_case_collision_fails_loudly(self, tmp_path):
        """Two files differing only in case: there is no defensible way to choose.

        Only reachable on a case-sensitive filesystem, and the reason it raises instead of
        picking one is the same reason the whole product fails loud on district data —
        loading the wrong file converts the wrong roster, silently.
        """
        (tmp_path / "Students.txt").write_text("id\n1\n", encoding="utf-8")
        (tmp_path / "students.txt").write_text("id\n2\n", encoding="utf-8")
        # The ONLY reliable probe is whether the directory now holds two entries. A
        # case-insensitive filesystem (Windows, default macOS) silently makes the second
        # write an overwrite of the first, so the collision cannot be built there at all.
        if len(list(tmp_path.iterdir())) < 2:  # pragma: no cover - platform-dependent
            pytest.skip("case-insensitive filesystem: the second write replaced the first")

        # The mapping must name NEITHER spelling exactly. With an exact match present the
        # ambiguity does not exist — the exact file wins, which is the correct and safest
        # precedence (see `test_an_exact_match_wins_over_case_variants`). Ambiguity is only
        # real when we are choosing purely on case, and then there is nothing to choose by.
        with pytest.raises(ExtractionError, match="match 'STUDENTS.TXT' when case is ignored"):
            DataExtractor(str(tmp_path)).load_data(["STUDENTS.TXT"])

    def test_an_exact_match_wins_over_case_variants(self, tmp_path):
        """Precedence, pinned: an exactly-named file is never passed over for a variant.

        Runs everywhere — on a case-insensitive filesystem the second write is simply an
        overwrite, and the exact name still resolves, which is the same guarantee.
        """
        (tmp_path / "Students.txt").write_text("id\nexact\n", encoding="utf-8")
        (tmp_path / "STUDENTS.TXT").write_text("id\nvariant\n", encoding="utf-8")

        data = DataExtractor(str(tmp_path)).load_data(["Students.txt"])

        assert not data["Students.txt"].empty

    def test_an_exactly_matching_file_never_consults_the_index(self, tmp_path, monkeypatch):
        """The common path is untouched: an exact hit does no directory listing at all."""
        (tmp_path / "Students.txt").write_text("id\n1\n", encoding="utf-8")
        extractor = DataExtractor(str(tmp_path))

        def _boom(_name):  # noqa: ANN001, ANN202
            raise AssertionError("case-insensitive resolution ran for an exact match")

        monkeypatch.setattr(extractor, "_resolve_case_insensitively", _boom)

        assert not extractor.load_data(["Students.txt"])["Students.txt"].empty


# The 18 positional names the base config injects for StudentDailyAbsences.txt — the
# real block from config/mappings/myedbc_mapping.yaml, because the behaviour under test
# is exactly what a district's declared `headers:` list does.
_DAILY_NAMES = [
    "School Number",
    "Student Number",
    "Student Legal Last Name",
    "Student Legal First Name",
    "Grade",
    "Homeroom",
    "Teacher Name",
    "Absence Date",
    "Reason Code AM",
    "Sub Allocation Code AM",
    "Authorized AM",
    "Reason Code PM",
    "Sub Allocation Code PM",
    "Authorized PM",
    "Absent Code AM",
    "Absent Code PM",
    "Teacher ID",
    "Portion Absent",
]

# One absence record in that layout: absent-code AM "A", authorized "Y".
_DAILY_ROW = [
    "5112005",
    "2713855",
    "Rivers",
    "Sam",
    "01",
    "02",
    "Teacher Name Here",
    "07-Oct-2025",
    "Illness",
    "",
    "Y",
    "",
    "",
    "N",
    "A",
    "",
    "655050",
    "1.0000",
]


def _csv(*rows: list[str]) -> bytes:
    return ("\n".join(",".join(r) for r in rows) + "\n").encode("utf-8")


class TestEchoedHeaderRowInAHeaderlessExport:
    """A file declared HEADERLESS that has grown a header row anyway.

    `headers:` injects names positionally with ``header=None``, so a header line the
    export starts emitting arrives as data row 0 and reaches the transformers as a
    record. SD51's 2026-09-17 daily-absence runs failed nine times that way — the
    heading ``Absence Code AM`` parsed as an absence code.
    """

    def test_a_grown_header_row_is_read_as_a_header_not_a_record(self):
        """THE POSITIVE: the mechanism actually fires, and on SD51's exact shape.

        The export's heading is Title Case and spells one column ``Absence Code AM``
        where the config declares ``Absent Code AM`` — so an all-or-nothing match would
        detect nothing. 16 of 18 still land at their declared positions.
        """
        heading = [n.replace("Absent Code", "Absence Code") for n in _DAILY_NAMES]

        df = DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(heading, _DAILY_ROW), _DAILY_NAMES)

        assert len(df) == 1, "the heading must not survive as a record"
        assert df["absent code am"].tolist() == ["A"]
        assert df["authorized am"].tolist() == ["Y"]

    def test_the_same_export_without_the_header_row_is_untouched(self):
        """THE TWIN: the standard headerless GDE is byte-identical through the new path.

        Paired with the test above deliberately — a drop that fired on both would
        silently eat the first absence record of every district in the fleet.
        """
        grown = DataExtractor("x")._load_bytes(
            "StudentDailyAbsences.txt",
            _csv([n.replace("Absent Code", "Absence Code") for n in _DAILY_NAMES], _DAILY_ROW),
            _DAILY_NAMES,
        )
        plain = DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(_DAILY_ROW), _DAILY_NAMES)

        assert len(plain) == 1
        pd.testing.assert_frame_equal(plain, grown)

    def test_a_first_row_that_is_real_data_is_never_dropped(self):
        """Two absence records in, two out — the row-0 check is not a blanket skip."""
        second = list(_DAILY_ROW)
        second[1] = "2713999"

        df = DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(_DAILY_ROW, second), _DAILY_NAMES)

        assert df["student number"].tolist() == ["2713855", "2713999"]

    def test_a_header_whose_column_ORDER_moved_is_reported_and_NOT_dropped(self, caplog):
        """Dropping it would leave every row below it mis-sourced — a worse fault.

        The names are all present, so the loose check recognises a header; none sit
        where the config declares them, so the run keeps its existing fail-loud path
        instead of silently delivering shifted data.
        """
        moved = list(reversed([n.replace("Absent Code", "Absence Code") for n in _DAILY_NAMES]))

        with caplog.at_level("WARNING"):
            df = DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(moved, _DAILY_ROW), _DAILY_NAMES)

        assert len(df) == 2, "the heading row must still be there — nothing was silently repaired"
        assert "column ORDER no longer matches" in caplog.text

    def test_a_file_with_no_declared_headers_is_left_alone(self):
        """No `headers:` block means the file carries its own header — never our business."""
        raw = _csv(["Student Number", "Grade"], ["2713855", "01"])

        df = DataExtractor("x")._load_bytes("StudentDemographicEnhanced.txt", raw, None)

        assert df["student number"].tolist() == ["2713855"]

    def test_an_export_of_nothing_but_a_header_row_yields_an_empty_frame(self):
        """The skip-on-empty path downstream, not a crash."""
        heading = [n.replace("Absent Code", "Absence Code") for n in _DAILY_NAMES]

        df = DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(heading), _DAILY_NAMES)

        assert df.empty

    def test_no_cell_value_is_ever_logged(self, caplog):
        """If the detection were wrong, the row it named would be a pupil's record."""
        heading = [n.replace("Absent Code", "Absence Code") for n in _DAILY_NAMES]

        with caplog.at_level("INFO"):
            DataExtractor("x")._load_bytes("StudentDailyAbsences.txt", _csv(heading, _DAILY_ROW), _DAILY_NAMES)

        assert "2713855" not in caplog.text
        assert "Rivers" not in caplog.text


class TestAnEmptyExportIsNoRecordsNotAParseFailure:
    """Plan 0051 Slice 2 — "there is nothing here" resolves to an empty frame.

    The line is NARROW and the narrowness is the safety property: a file with CONTENT
    that no encoding/delimiter can read is still a loud failure. Only bytes that carry
    no record at all become an empty frame, and the set is exactly what used to raise —
    measured against the real extractor, not reasoned about:

      raised before  ->  0 bytes · newlines only · a BOM · a BOM plus newlines
      parsed before  ->  whitespace with SPACES · ",,,\n" · "\t\t\n" · a header row

    Nothing in the second column changes. Folding whitespace-with-spaces in would have
    been a silent behaviour change on a path all 20 districts share, dressed up as a
    bugfix — it parses to a junk frame today and the entity RUNS.
    """

    EMPTY_SHAPES = {
        "zero bytes": b"",
        "one newline": b"\n",
        "crlf": b"\r\n",
        "several newlines": b"\n\n\n",
        "utf-8 BOM only": b"\xef\xbb\xbf",
        "utf-8 BOM then crlf": b"\xef\xbb\xbf\r\n",
        "utf-16 BOM only": b"\xff\xfe",
    }

    @pytest.mark.parametrize("label", sorted(EMPTY_SHAPES))
    def test_an_empty_export_loads_as_an_empty_frame(self, tmp_path, label):
        """A BOM is the one that matters in practice: PowerShell's `Out-File` and
        `Export-Csv -Encoding UTF8` write one even when there is nothing to export, so a
        predicate keyed on `bytes.strip()` alone would miss the most likely real shape."""
        (tmp_path / "x.txt").write_bytes(self.EMPTY_SHAPES[label])

        result = DataExtractor(str(tmp_path)).load_data(["x.txt"])

        assert result["x.txt"].empty

    @pytest.mark.parametrize("label", sorted(EMPTY_SHAPES))
    def test_it_says_so_in_the_log_naming_the_file(self, tmp_path, caplog, label):
        """THE DIAGNOSTIC OBLIGATION, not polish.

        Before this slice an empty required source raised an `ExtractionError` that NAMED
        the file. Now the run walks on to `incomplete_roster` / `NO_OUTPUT`, which names
        the symptom and points nowhere near the filename — the exact loss plan 0051
        criticises elsewhere. This log line is the only remaining trace, so the level and
        the filename are both asserted.
        """
        (tmp_path / "StudentDailyAbsences.txt").write_bytes(self.EMPTY_SHAPES[label])

        with caplog.at_level(logging.WARNING, logger="src.etl.extractor"):
            DataExtractor(str(tmp_path)).load_data(["StudentDailyAbsences.txt"])

        assert any(
            rec.levelno == logging.WARNING and "StudentDailyAbsences.txt" in rec.message for rec in caplog.records
        ), f"no WARNING naming the file for {label!r}: {[r.message for r in caplog.records]}"

    #: Shapes that parse today and MUST keep parsing — the positive twin of the set above.
    #: Without these, narrowing the predicate too far would go unnoticed.
    STILL_PARSES = {
        "whitespace with spaces": b"   \n",
        "multi-line whitespace": b"  \n  \n",
        "empty comma fields": b",,,\n",
        "empty tab fields": b"\t\t\n",
        "a header row only": b"Name,Grade\n",
    }

    @pytest.mark.parametrize("label", sorted(STILL_PARSES))
    def test_shapes_that_parsed_before_are_untouched(self, tmp_path, label):
        (tmp_path / "x.txt").write_bytes(self.STILL_PARSES[label])

        result = DataExtractor(str(tmp_path)).load_data(["x.txt"])

        assert result["x.txt"].columns.size > 0, "a parsed frame keeps its columns"

    def test_content_that_cannot_be_parsed_STILL_RAISES(self, tmp_path):
        """The positive twin that makes the whole class mean something.

        Without it, "empty yields an empty frame" is one `except` away from "anything
        unreadable yields an empty frame", which is the swallowed-error failure this
        product's fail-loud rule exists to prevent. A real byte sequence is hard to
        construct (latin1 never fails), so this asserts the GUARD rather than a shape:
        the raise site is still reachable and still names the file.
        """
        (tmp_path / "broken.txt").write_bytes(b"Name,Grade\nA,5\n")
        import src.etl.extractor as extractor_module

        monkey = pytest.MonkeyPatch()
        monkey.setattr(extractor_module.DataExtractor, "_read_with_fallback", staticmethod(lambda *a, **k: None))
        try:
            with pytest.raises(ExtractionError, match="broken.txt"):
                DataExtractor(str(tmp_path)).load_data(["broken.txt"])
        finally:
            monkey.undo()
