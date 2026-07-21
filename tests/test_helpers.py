"""Tests for src/utils/helpers.py — utility functions.

The zip-naming helpers (``district_slug`` / ``build_zip_name``) live with their
SFTP consumer now — see ``tests/test_sftp_uploader.py``.
"""

import re
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.utils import helpers
from src.utils.helpers import (
    describe_exception_for_log,
    describe_value_for_log,
    ensure_directory,
    normalize_columns,
    subprocess_no_window_flags,
)


class TestEnsureDirectory:
    def test_creates_directory(self, tmp_path):
        new_dir = tmp_path / "a" / "b" / "c"
        result = ensure_directory(new_dir)
        assert result == new_dir
        assert new_dir.is_dir()

    def test_existing_directory(self, tmp_path):
        result = ensure_directory(tmp_path)
        assert result == tmp_path


class TestNormalizeColumns:
    def test_strips_and_lowercases(self):
        df = pd.DataFrame(columns=["  Name  ", "AGE", "  School Number  "])
        result = normalize_columns(df)
        assert list(result.columns) == ["name", "age", "school number"]

    def test_does_not_mutate_original(self):
        df = pd.DataFrame(columns=["Name", "Age"])
        normalize_columns(df)
        assert list(df.columns) == ["Name", "Age"]


class TestSubprocessNoWindowFlags:
    """The single-source no-console flag every Windows-facing subprocess.run must pass.

    On Windows the windowed exe would otherwise flash a console for every PowerShell/
    schtasks/icacls child (e.g. the schedule read-back on a nav click); the helper returns
    ``CREATE_NO_WINDOW`` there and a harmless 0 on POSIX (where the flag is a no-op).
    """

    def test_win32_returns_create_no_window(self):
        # Simulate a Windows host: platform win32 + a subprocess module exposing the flag.
        fake_subprocess = types.SimpleNamespace(CREATE_NO_WINDOW=0x08000000)
        with (
            patch.object(helpers.sys, "platform", "win32"),
            patch.object(helpers, "subprocess", fake_subprocess),
        ):
            assert subprocess_no_window_flags() == 0x08000000

    def test_non_windows_returns_zero(self):
        with patch.object(helpers.sys, "platform", "linux"):
            assert subprocess_no_window_flags() == 0


# A realistic student-record cell for every leak assertion below. The support
# log ships to myBlueprint by email (docs/partner/troubleshooting.md), so a
# descriptor must never carry the content of a source cell.
_PII_SAMPLES = (
    "Nguyen-Ferrari",  # legal surname
    "2011-04-23",  # date of birth
    "1234567890",  # student number / PEN
    "aoife.ó'súilleabháin@sd74.bc.ca",  # email (non-ascii + punctuation)
    "  Jean Luc  ",  # padded legal first name
)


class TestDescribeValueForLog:
    """The single log-safety seam: a source cell becomes a NON-IDENTIFYING shape.

    Type + length + character-class shape are enough to diagnose a district's
    bad data ("10 chars, digits+slashes" → they export d/m/Y); the content
    itself is student PII and must never reach the log file partners email to
    support (privacy is this project's TOP-PRIORITY live standard).
    """

    @pytest.mark.parametrize("value", _PII_SAMPLES)
    def test_never_reproduces_the_value(self, value):
        desc = describe_value_for_log(value)
        assert value not in desc
        assert value.strip() not in desc
        # Not even a word of it: no alphabetic run from the value survives.
        for word in re.findall(r"[^\W\d_]{3,}", value, flags=re.UNICODE):
            assert word.lower() not in desc.lower()

    def test_describes_type_length_and_shape(self):
        assert describe_value_for_log("15-Sep-2024") == "str(11 chars, letters+digits+dashes)"

    def test_slash_dates_and_iso_dates_are_distinguishable(self):
        # The whole diagnostic point: support can still tell the two apart.
        assert describe_value_for_log("23/04/2011") == "str(10 chars, digits+slashes)"
        assert describe_value_for_log("2011-04-23") == "str(10 chars, digits+dashes)"

    def test_singular_char_count(self):
        assert describe_value_for_log("A") == "str(1 char, letters)"

    def test_empty_string(self):
        assert describe_value_for_log("") == "str(empty)"

    def test_whitespace_only(self):
        assert describe_value_for_log("   ") == "str(3 chars, spaces)"

    def test_none(self):
        assert describe_value_for_log(None) == "None"

    @pytest.mark.parametrize("value", [np.nan, float("nan"), pd.NA, pd.NaT])
    def test_missing_values_are_total_and_content_free(self, value):
        assert "missing" in describe_value_for_log(value)

    def test_bytes_report_length_only(self):
        assert describe_value_for_log(b"Nguyen") == "bytes(6 bytes)"
        assert describe_value_for_log(bytearray(b"Nguyen")) == "bytearray(6 bytes)"

    def test_numbers(self):
        assert describe_value_for_log(1234567890) == "int(10 chars, digits)"
        assert describe_value_for_log(np.int64(42)) == "int64(2 chars, digits)"

    def test_datetime_is_shaped_not_printed(self):
        desc = describe_value_for_log(datetime(2011, 4, 23, 9, 30))
        assert "2011" not in desc
        assert desc.startswith("datetime(")

    def test_non_ascii_is_flagged_without_content(self):
        desc = describe_value_for_log("Zoë")
        assert "non-ascii" in desc
        assert "Zo" not in desc

    def test_huge_string_is_bounded_and_cheap(self):
        desc = describe_value_for_log("x" * 5_000_000)
        assert "5000000 chars" in desc
        assert len(desc) < 120

    def test_arbitrary_object_is_not_stringified(self):
        # A whole row/frame must never be dumped — not even measured via repr.
        row = pd.Series({"legal surname": "Nguyen-Ferrari"})
        desc = describe_value_for_log(row)
        assert "Nguyen" not in desc
        assert "Series" in desc

    def test_total_on_an_exploding_object(self):
        class _Hostile:
            def __str__(self):
                raise RuntimeError("boom")

            def __repr__(self):
                raise RuntimeError("boom")

        assert isinstance(describe_value_for_log(_Hostile()), str)

    def test_total_when_a_measurable_value_explodes_while_being_measured(self):
        # An int subclass passes the measurable-type gate, then blows up in str().
        class _ExplodingInt(int):
            def __str__(self):
                raise RuntimeError("boom")

        assert describe_value_for_log(_ExplodingInt(7)) == "undescribable value"

    def test_total_when_the_null_check_itself_raises(self):
        with patch.object(helpers.pd, "isna", side_effect=TypeError("no isna for you")):
            assert describe_value_for_log("abc") == "str(3 chars, letters)"


class TestDescribeExceptionForLog:
    """Exception MESSAGES are untrusted: stdlib parsers echo the offending input
    verbatim (``time data '2011-31-02' does not match format ...``), so only the
    exception TYPE crosses into a log line built from a source cell."""

    def test_type_name_only(self):
        exc = ValueError("time data 'Nguyen-Ferrari' does not match format '%Y-%m-%d'")
        assert describe_exception_for_log(exc) == "ValueError"

    def test_stdlib_strptime_message_is_not_reproduced(self):
        try:
            datetime.strptime("Nguyen-Ferrari", "%Y-%m-%d")
        except ValueError as exc:
            assert "Nguyen" in str(exc)  # the message really does echo the input
            assert describe_exception_for_log(exc) == "ValueError"
        else:  # pragma: no cover — strptime must raise here
            pytest.fail("expected ValueError")


class TestNoRawValueInDataErrorSamples:
    """Static guard on the seam: no ETL diagnostic sample may be built by
    ``!r``-formatting a source cell again. Pins the class of leak shut, not just
    the three known sites (a future logging site can't re-introduce it silently)."""

    def test_no_repr_formatted_sample_lines_in_etl(self):
        etl_root = Path(__file__).resolve().parents[1] / "src" / "etl"
        offenders = []
        for path in sorted(etl_root.rglob("*.py")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if ("first_sample" in stripped or "sample=" in stripped) and "!r" in stripped:
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
        assert offenders == [], "data-error samples must use describe_value_for_log(), not !r:\n" + "\n".join(offenders)
