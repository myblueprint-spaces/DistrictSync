"""Pure predicate + validator tests for the seasonal sync window.

``in_sync_window`` / ``next_resume_date`` are pure and ``today``-injected (no
``date.today()`` inside them), mirroring the school-year date-helper seam in
``dates.py``. Covers wrap-around, non-wrapping, one-day (start == end),
boundary-inclusive, and leap-day (``02-29``) handling; plus ``validate_month_day``
accept/reject.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.etl.sync_window import in_sync_window, next_resume_date
from src.utils.validators import validate_month_day


class TestInSyncWindowWrapAround:
    """Aug 11 -> Jul 6: the real school-year window, spanning the New Year."""

    START, END = "08-11", "07-06"

    @pytest.mark.parametrize(
        "today",
        [
            date(2026, 8, 11),  # start boundary — inclusive
            date(2027, 7, 6),  # end boundary — inclusive
            date(2026, 9, 15),  # autumn term
            date(2026, 12, 31),  # right up to New Year
            date(2027, 1, 1),  # spans into the new calendar year
            date(2027, 6, 30),  # late spring
        ],
    )
    def test_inside(self, today: date) -> None:
        assert in_sync_window(today, self.START, self.END) is True

    @pytest.mark.parametrize(
        "today",
        [
            date(2026, 7, 7),  # one day past the end boundary
            date(2026, 8, 10),  # one day before the start boundary
            date(2026, 7, 15),  # deep summer gap
        ],
    )
    def test_outside(self, today: date) -> None:
        assert in_sync_window(today, self.START, self.END) is False


class TestInSyncWindowNonWrapping:
    """Feb 01 -> Jun 30: a within-one-year window (start < end)."""

    START, END = "02-01", "06-30"

    @pytest.mark.parametrize("today", [date(2026, 2, 1), date(2026, 6, 30), date(2026, 4, 15)])
    def test_inside(self, today: date) -> None:
        assert in_sync_window(today, self.START, self.END) is True

    @pytest.mark.parametrize("today", [date(2026, 1, 31), date(2026, 7, 1), date(2026, 12, 25)])
    def test_outside(self, today: date) -> None:
        assert in_sync_window(today, self.START, self.END) is False


class TestInSyncWindowOneDay:
    """start == end: a single-day window, in-window only that day."""

    def test_only_that_day_is_inside(self) -> None:
        assert in_sync_window(date(2026, 3, 15), "03-15", "03-15") is True

    @pytest.mark.parametrize("today", [date(2026, 3, 14), date(2026, 3, 16)])
    def test_neighbouring_days_are_outside(self, today: date) -> None:
        assert in_sync_window(today, "03-15", "03-15") is False


class TestInSyncWindowLeapDay:
    """``02-29`` never crashes; it clamps toward the window interior in non-leap years."""

    def test_end_boundary_feb29_in_leap_year_includes_feb29(self) -> None:
        assert in_sync_window(date(2028, 2, 29), "02-01", "02-29") is True

    def test_end_boundary_feb29_in_non_leap_year_ends_feb28(self) -> None:
        # Non-leap 2026: Feb 28 is inside, Mar 1 falls outside (effective end = Feb 28).
        assert in_sync_window(date(2026, 2, 28), "02-01", "02-29") is True
        assert in_sync_window(date(2026, 3, 1), "02-01", "02-29") is False

    def test_start_boundary_feb29_in_non_leap_year_opens_mar1(self) -> None:
        # Non-leap 2026, non-wrapping window 02-29 -> 10-01: Feb 28 is before the
        # start, Mar 1 opens it (effective start = Mar 1).
        assert in_sync_window(date(2026, 2, 28), "02-29", "10-01") is False
        assert in_sync_window(date(2026, 3, 1), "02-29", "10-01") is True

    def test_start_boundary_feb29_in_leap_year_opens_feb29(self) -> None:
        assert in_sync_window(date(2028, 2, 29), "02-29", "10-01") is True

    def test_feb29_boundary_never_raises_on_a_non_leap_today(self) -> None:
        # Both boundaries 02-29 in a non-leap year — must not raise.
        assert in_sync_window(date(2026, 2, 28), "02-29", "02-29") is False


class TestNextResumeDate:
    def test_before_this_years_start_returns_this_year(self) -> None:
        assert next_resume_date(date(2026, 7, 15), "08-11") == date(2026, 8, 11)

    def test_on_or_after_this_years_start_returns_next_year(self) -> None:
        assert next_resume_date(date(2026, 9, 15), "08-11") == date(2027, 8, 11)

    def test_exactly_on_start_returns_next_year(self) -> None:
        # Spec rule: today >= start -> next year (the "resumes" copy is only shown
        # when paused, i.e. outside the window, so start day never displays it).
        assert next_resume_date(date(2026, 8, 11), "08-11") == date(2027, 8, 11)

    def test_feb29_start_clamps_to_mar1_in_a_non_leap_resume_year(self) -> None:
        # today before start, resume year 2026 (non-leap) -> Feb 29 clamps to Mar 1.
        assert next_resume_date(date(2026, 1, 15), "02-29") == date(2026, 3, 1)

    def test_feb29_start_stays_feb29_in_a_leap_resume_year(self) -> None:
        # today after start, resume year 2028 (leap) -> real Feb 29.
        assert next_resume_date(date(2027, 6, 1), "02-29") == date(2028, 2, 29)


class TestValidateMonthDay:
    @pytest.mark.parametrize("md", ["08-11", "07-06", "01-01", "12-31", "02-29"])
    def test_accepts_real_month_days_including_leap(self, md: str) -> None:
        assert validate_month_day(md) == md

    def test_strips_surrounding_whitespace(self) -> None:
        assert validate_month_day("  08-11 ") == "08-11"

    @pytest.mark.parametrize(
        "md",
        [
            "13-01",  # month out of range
            "00-05",  # month zero
            "02-30",  # Feb never has 30
            "04-31",  # April has 30 days
            "08-32",  # day out of range
            "08-00",  # day zero
            "8-1",  # not zero-padded MM-DD
            "0811",  # no separator
            "08-11-2026",  # a full date, wrong shape
            "aa-bb",  # non-numeric
            "",  # empty
        ],
    )
    def test_rejects_bad_shapes_and_nonexistent_days(self, md: str) -> None:
        with pytest.raises(ValueError):
            validate_month_day(md)
