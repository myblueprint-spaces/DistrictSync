"""Tests for BaseTransformer shared utilities.

Focused on the helpers used by the CourseInfo + StudentCourses entities
(`filter_excluded_course_code_patterns`, `clean_course_code_flavor`) and the
config-driven active-student predicate (`compute_enroll_status`,
`is_active_mask`, `resolve_active_config`). Existing helpers like
`filter_excluded_course_codes` are exercised indirectly through the entity
transformer tests.
"""

import logging
from datetime import date, datetime
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext

SD62_PATTERNS = [r"^.{5}-K", r"^.{5}0\d", r"^X", r"^ATT"]
SD62_FLAVORS = ["HUB", "HOL", "DL", "---"]


class TestFilterExcludedCourseCodePatterns:
    def test_empty_patterns_returns_unchanged(self):
        df = pd.DataFrame({"course code": ["MAT10", "ENG09"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, [])
        pd.testing.assert_frame_equal(out, df)

    def test_empty_df_returns_unchanged(self):
        df = pd.DataFrame({"course code": []})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert out.empty

    def test_missing_column_returns_unchanged(self):
        df = pd.DataFrame({"some other col": ["MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        pd.testing.assert_frame_equal(out, df)

    def test_filters_x_prefix(self):
        df = pd.DataFrame({"course code": ["MAT10", "XGEN12", "ENG09"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["MAT10", "ENG09"]

    def test_filters_att_prefix(self):
        df = pd.DataFrame({"course code": ["MAT10", "ATT--AM", "ATT--PM"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["MAT10"]

    def test_filters_kindergarten_pattern(self):
        # ^.{5}-K: 5 chars then "-K"
        df = pd.DataFrame({"course code": ["MAT10", "AAAAA-KO", "ABCDE-K1"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["MAT10"]

    def test_filters_early_grade_pattern(self):
        # ^.{5}0\d: 5 chars then 0 followed by a digit
        df = pd.DataFrame({"course code": ["MAT1003", "ABCDE07", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["MAT10"]

    def test_case_insensitive(self):
        df = pd.DataFrame({"course code": ["xgen12", "att--am", "mat10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["mat10"]

    def test_trims_whitespace_before_match(self):
        df = pd.DataFrame({"course code": ["  XGEN12  ", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["course code"]) == ["MAT10"]

    def test_explicit_column_overrides_default(self):
        df = pd.DataFrame(
            {
                "course code": ["XGEN12", "MAT10"],  # would normally be filtered
                "full course code": ["MAT10-A", "XGEN12-B"],
            }
        )
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS, column="full course code")
        # Filters on full course code: row 0 (MAT10-A) survives, row 1 (XGEN12-B) drops
        assert list(out["course code"]) == ["XGEN12"]

    def test_explicit_column_missing_returns_unchanged(self):
        df = pd.DataFrame({"course code": ["XGEN12", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS, column="nonexistent")
        pd.testing.assert_frame_equal(out, df)

    def test_falls_back_to_district_course_code(self):
        df = pd.DataFrame({"district course code": ["XGEN12", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        assert list(out["district course code"]) == ["MAT10"]

    def test_course_code_takes_precedence_over_district(self):
        df = pd.DataFrame(
            {
                "course code": ["MAT10", "ENG09"],  # nothing to filter
                "district course code": ["XGEN12", "ATT--AM"],  # would filter both
            }
        )
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        # Only `course code` is checked when present — both rows survive
        assert len(out) == 2

    def test_nan_values_not_matched(self):
        df = pd.DataFrame({"course code": ["MAT10", np.nan, "XGEN12"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        # NaN should survive (na=False); XGEN12 dropped
        assert len(out) == 2
        assert "MAT10" in out["course code"].values

    def test_returns_copy_not_view(self):
        df = pd.DataFrame({"course code": ["XGEN12", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, SD62_PATTERNS)
        # Mutating result must not affect source
        out.loc[out.index[0], "course code"] = "MUTATED"
        assert "MUTATED" not in df["course code"].values

    def test_single_pattern(self):
        df = pd.DataFrame({"course code": ["XGEN12", "MAT10"]})
        out = BaseTransformer.filter_excluded_course_code_patterns(df, [r"^X"])
        assert list(out["course code"]) == ["MAT10"]


class TestNormalizeIsoDate:
    """`normalize_iso_date` — ISO `yyyy-mm-dd` output (the SpacesEDU attendance date shape).

    Accepts dd-MMM-yyyy / already-ISO / m/d/yyyy / d/m/yyyy and normalizes to ISO,
    so input GDE dates in any recognized format land as `yyyy-MM-dd`.
    """

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2024-09-18", "2024-09-18"),  # already ISO
            ("18-Sep-2024", "2024-09-18"),  # dd-MMM-yyyy
            ("09/18/2024", "2024-09-18"),  # m/d/yyyy
            ("18/09/2024", "2024-09-18"),  # d/m/yyyy
        ],
    )
    def test_parses_known_formats(self, value, expected):
        assert BaseTransformer.normalize_iso_date(value) == expected

    @pytest.mark.parametrize("value", ["", None, np.nan, "nan", "NaN", "  "])
    def test_blank_like_returns_empty(self, value):
        assert BaseTransformer.normalize_iso_date(value) == ""

    def test_unparseable_returned_unchanged(self):
        # Fail-visible: a malformed date stays inspectable, not silently blanked.
        assert BaseTransformer.normalize_iso_date("not-a-date") == "not-a-date"

    def test_is_in_allowed_transforms(self):
        assert "normalize_iso_date" in BaseTransformer.ALLOWED_TRANSFORMS


class TestFriendlyDateFormatToStrftime:
    """`friendly_date_format_to_strftime` — friendly tokens -> strftime, fail-loud on typos."""

    @pytest.mark.parametrize(
        "friendly,strftime_fmt",
        [
            ("yyyy-MM-dd", "%Y-%m-%d"),
            ("dd-MMM-yyyy", "%d-%b-%Y"),
            ("MM/dd/yyyy", "%m/%d/%Y"),
            ("dd/MM/yy", "%d/%m/%y"),
            ("dd MMMM yyyy", "%d %B %Y"),
        ],
    )
    def test_translates_supported_tokens(self, friendly, strftime_fmt):
        assert BaseTransformer.friendly_date_format_to_strftime(friendly) == strftime_fmt

    @pytest.mark.parametrize("bad", ["yyyy-mm-dd", "yyyy-MM-DD", "yyyy-Mon-dd", "garbage"])
    def test_unsupported_token_raises(self, bad):
        # A lowercase `mm`/`DD` typo or any unknown token fails loud at the boundary.
        with pytest.raises(ValueError, match="Unsupported date_format"):
            BaseTransformer.friendly_date_format_to_strftime(bad)


class TestFormatDate:
    """`format_date` — flexible input parse -> arbitrary strftime output shape."""

    def test_dd_mmm_yyyy_output(self):
        fmt = BaseTransformer.friendly_date_format_to_strftime("dd-MMM-yyyy")
        assert BaseTransformer.format_date("2024-09-18", fmt) == "18-Sep-2024"

    def test_iso_output(self):
        fmt = BaseTransformer.friendly_date_format_to_strftime("yyyy-MM-dd")
        assert BaseTransformer.format_date("18-Sep-2024", fmt) == "2024-09-18"

    @pytest.mark.parametrize("value", ["", None, np.nan, "nan", "  "])
    def test_blank_like_returns_empty(self, value):
        assert BaseTransformer.format_date(value, "%Y-%m-%d") == ""

    def test_unparseable_returned_unchanged(self):
        assert BaseTransformer.format_date("not-a-date", "%Y-%m-%d") == "not-a-date"


class TestCleanCourseCodeFlavor:
    def test_empty_flavors_returns_code_unchanged(self):
        assert BaseTransformer.clean_course_code_flavor("MATH-DL01", []) == "MATH-DL01"

    def test_no_match_returns_code_unchanged(self):
        assert BaseTransformer.clean_course_code_flavor("MAT10", SD62_FLAVORS) == "MAT10"

    def test_hub_flavor_truncates_to_seven(self):
        # "EN10HUB-W" contains HUB -> truncate to "EN10HUB"
        assert BaseTransformer.clean_course_code_flavor("EN10HUB-W", SD62_FLAVORS) == "EN10HUB"

    def test_dl_flavor_truncates(self):
        assert BaseTransformer.clean_course_code_flavor("MATH-DL01", SD62_FLAVORS) == "MATH-DL"

    def test_hol_flavor_truncates(self):
        assert BaseTransformer.clean_course_code_flavor("SCI09HOL", SD62_FLAVORS) == "SCI09HO"

    def test_dash_dash_dash_flavor_truncates(self):
        assert BaseTransformer.clean_course_code_flavor("MA---ABC", SD62_FLAVORS) == "MA---AB"

    def test_case_insensitive_match(self):
        assert BaseTransformer.clean_course_code_flavor("math-dl01", SD62_FLAVORS) == "math-dl"

    def test_short_code_under_seven_chars(self):
        # "DLX" has DL but only 3 chars — should return "DLX"
        assert BaseTransformer.clean_course_code_flavor("DLX", SD62_FLAVORS) == "DLX"

    def test_exactly_seven_char_code(self):
        assert BaseTransformer.clean_course_code_flavor("EN10HUB", SD62_FLAVORS) == "EN10HUB"

    def test_none_returns_empty_string(self):
        assert BaseTransformer.clean_course_code_flavor(None, SD62_FLAVORS) == ""

    def test_nan_returns_empty_string(self):
        assert BaseTransformer.clean_course_code_flavor(np.nan, SD62_FLAVORS) == ""

    def test_empty_string_returns_empty_string(self):
        assert BaseTransformer.clean_course_code_flavor("", SD62_FLAVORS) == ""

    def test_non_string_coerced(self):
        # PowerShell would never call this with non-string, but pandas might
        # pass an int/float through. Should coerce gracefully.
        assert BaseTransformer.clean_course_code_flavor(42, SD62_FLAVORS) == "42"

    def test_whitespace_flavor_skipped(self):
        # Empty / whitespace-only entries in the flavor list shouldn't match everything
        assert BaseTransformer.clean_course_code_flavor("MAT10", ["", "  "]) == "MAT10"


class TestEarlyGradeExclusionPattern:
    def test_default_grade_10_matches_legacy_pattern(self):
        # Grade 10 floor is equivalent to the legacy "^.{5}0\\d" (excludes 00-09).
        assert BaseTransformer.early_grade_exclusion_pattern(10) == r"^.{5}0[0-9]"

    def test_grade_9(self):
        assert BaseTransformer.early_grade_exclusion_pattern(9) == r"^.{5}0[0-8]"

    def test_grade_8(self):
        assert BaseTransformer.early_grade_exclusion_pattern(8) == r"^.{5}0[0-7]"

    def test_non_numeric_falls_back_to_10(self):
        assert BaseTransformer.early_grade_exclusion_pattern("oops") == r"^.{5}0[0-9]"

    def test_value_above_10_clamped(self):
        assert BaseTransformer.early_grade_exclusion_pattern(12) == r"^.{5}0[0-9]"

    def test_one_or_below_returns_none(self):
        assert BaseTransformer.early_grade_exclusion_pattern(1) is None


class TestEffectiveCourseCodePatterns:
    def test_appends_derived_early_grade_pattern(self):
        gc = {"excluded_course_code_patterns": [r"^X"], "course_start_grade": 8}
        assert BaseTransformer.effective_course_code_patterns(gc) == [r"^X", r"^.{5}0[0-7]"]

    def test_defaults_to_grade_10_when_unset(self):
        gc = {"excluded_course_code_patterns": [r"^X", r"^ATT"]}
        assert BaseTransformer.effective_course_code_patterns(gc) == [r"^X", r"^ATT", r"^.{5}0[0-9]"]

    def test_empty_config(self):
        assert BaseTransformer.effective_course_code_patterns({}) == [r"^.{5}0[0-9]"]

    def test_first_matching_flavor_wins(self):
        # "ENXHUBDL" has both HUB and DL — either way truncates to 7 chars
        assert BaseTransformer.clean_course_code_flavor("ENXHUBDL", SD62_FLAVORS) == "ENXHUBD"

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("MAT10HUB-X", "MAT10HU"),
            ("ENG12DL", "ENG12DL"),  # already 7 chars
            ("SCI09HOLLY", "SCI09HO"),
            ("X---YZ", "X---YZ"),  # 6 chars
            ("ABCDEFG---H", "ABCDEFG"),  # truncates to 7
        ],
    )
    def test_parametrized_cases(self, code, expected):
        assert BaseTransformer.clean_course_code_flavor(code, SD62_FLAVORS) == expected


# ---------------------------------------------------------------------------
# Active-student predicate (compute_enroll_status / is_active_mask /
# resolve_active_config). Single source of truth for "is this student active".
# `datetime.now()` is patched so "today" is fixed; strptime still delegates to
# the real datetime so the 4 withdraw-date formats parse normally.
# ---------------------------------------------------------------------------

FIXED_TODAY = datetime(2025, 6, 1)


class _FixedDateTime(datetime):
    """datetime subclass whose now() is frozen; strptime stays real."""

    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 - signature parity
        return FIXED_TODAY


def _status(df, field_map=None):
    """compute_enroll_status with frozen 'today'."""
    with patch("src.etl.transformers.base.datetime", _FixedDateTime):
        return BaseTransformer.compute_enroll_status(df, field_map or {"EnrollStatus": None})


def _mask(df, field_map=None):
    with patch("src.etl.transformers.base.datetime", _FixedDateTime):
        return BaseTransformer.is_active_mask(df, field_map or {"EnrollStatus": None})


class TestResolveActiveConfig:
    def test_defaults_pick_two_l_alias_when_present(self):
        cols = ["student number", "enrollment status", "withdraw date"]
        status, withdraw, active = BaseTransformer.resolve_active_config({"EnrollStatus": None}, cols)
        assert status == "enrollment status"  # two-L preferred (listed first)
        assert withdraw == "withdraw date"
        assert active == ["Active", "PreReg"]  # Active + PreReg by default (Advanced CSV spec)

    def test_defaults_pick_one_l_alias_when_only_one_present(self):
        cols = ["student number", "enrolment status"]
        status, _, _ = BaseTransformer.resolve_active_config({"EnrollStatus": None}, cols)
        assert status == "enrolment status"  # one-L honored via alias

    def test_no_status_column_resolves_to_none(self):
        cols = ["student number", "withdraw date"]
        status, withdraw, _ = BaseTransformer.resolve_active_config({"EnrollStatus": None}, cols)
        assert status is None
        assert withdraw == "withdraw date"

    def test_configured_status_column_used_verbatim(self):
        cols = ["student number", "status"]
        status, _, _ = BaseTransformer.resolve_active_config({"EnrollStatus": {"status_column": "Status"}}, cols)
        assert status == "status"  # lower-cased to match normalized frame

    def test_configured_status_column_absent_falls_through(self):
        cols = ["student number", "withdraw date"]
        status, _, _ = BaseTransformer.resolve_active_config({"EnrollStatus": {"status_column": "Status"}}, cols)
        assert status is None  # configured but absent → date branch

    def test_configured_active_values_and_withdraw_column(self):
        cols = ["student number", "left on"]
        status, withdraw, active = BaseTransformer.resolve_active_config(
            {"EnrollStatus": {"withdraw_date_column": "Left On", "active_values": ["Active"]}},
            cols,
        )
        assert status is None
        assert withdraw == "left on"
        assert active == ["Active"]


class TestComputeEnrollStatus:
    def test_status_active(self):
        df = pd.DataFrame({"enrolment status": ["Active"], "student number": ["S1"]})
        assert list(_status(df)) == ["Active"]

    def test_status_inactive(self):
        df = pd.DataFrame({"enrolment status": ["Withdrawn"], "student number": ["S1"]})
        assert list(_status(df)) == ["Inactive"]

    def test_prereg_active_by_default(self):
        # PreReg is in DEFAULT_ACTIVE_VALUES → labeled PreReg, kept by the mask (Advanced CSV spec).
        df = pd.DataFrame({"enrolment status": ["PreReg"], "student number": ["S1"]})
        assert list(_status(df)) == ["PreReg"]
        assert list(_mask(df)) == [True]

    def test_prereg_excluded_when_district_opts_out(self):
        # A district can drop PreReg via active_values (e.g. exclude upcoming-year pre-regs).
        df = pd.DataFrame({"enrolment status": ["PreReg"], "student number": ["S1"]})
        fm = {"EnrollStatus": {"active_values": ["Active"]}}
        assert list(_status(df, fm)) == ["Inactive"]
        assert list(_mask(df, fm)) == [False]

    def test_date_only_back_compat(self):
        df = pd.DataFrame({"withdraw date": ["", "15-Jan-2020", "2099-12-31"], "student number": ["A", "B", "C"]})
        assert list(_status(df)) == ["Active", "Inactive", "Active"]

    def test_two_l_real_header_honored(self):
        """The real two-L MyEd export header is detected (the original bug)."""
        df = pd.DataFrame({"enrollment status": ["Withdrawn"], "student number": ["S1"]})
        assert list(_status(df)) == ["Inactive"]

    def test_active_status_wins_over_past_withdraw(self):
        """Status wins: an active status keeps the row even with a past withdraw
        date (e.g. a re-enrolled student whose prior withdraw date lingers). The
        withdraw date is only a fallback for rows with no status value."""
        df = pd.DataFrame(
            {
                "enrolment status": ["Active", "Active"],
                "withdraw date": ["", "15-Jan-2020"],
                "student number": ["A", "B"],
            }
        )
        assert list(_status(df)) == ["Active", "Active"]

    def test_blank_status_falls_back_to_withdraw_date(self):
        """A blank status value falls back to the withdraw-date column, per row."""
        df = pd.DataFrame(
            {
                "enrolment status": ["Active", "", ""],
                "withdraw date": ["", "", "15-Jan-2020"],
                "student number": ["A", "B", "C"],
            }
        )
        # A: active status; B: blank status + no date → Active; C: blank status + past date → Inactive
        assert list(_status(df)) == ["Active", "Active", "Inactive"]

    def test_future_withdraw_date_does_not_override_active(self):
        df = pd.DataFrame(
            {
                "enrolment status": ["Active"],
                "withdraw date": ["2099-12-31"],
                "student number": ["A"],
            }
        )
        assert list(_status(df)) == ["Active"]

    def test_unparseable_withdraw_date_is_inactive(self):
        df = pd.DataFrame({"withdraw date": ["NOT-A-DATE"], "student number": ["S1"]})
        assert list(_status(df)) == ["Inactive"]

    def test_unparseable_withdraw_date_warns(self, caplog):
        df = pd.DataFrame({"withdraw date": ["NOT-A-DATE"], "student number": ["S1"]})
        with caplog.at_level("WARNING"):
            _status(df)
        assert any("Could not parse" in r.message for r in caplog.records)

    def test_neither_column_defaults_active_with_warning(self, caplog):
        df = pd.DataFrame({"student number": ["S1", "S2"]})
        with caplog.at_level("WARNING"):
            labels = _status(df)
        assert list(labels) == ["Active", "Active"]
        assert any("Defaulting all rows to 'Active'" in r.message for r in caplog.records)

    def test_custom_active_values_drops_active_when_excluded(self):
        """A district that drops 'Active' from active_values is honored (no union)."""
        df = pd.DataFrame({"status": ["Active", "Enrolled"], "student number": ["A", "B"]})
        fm = {"EnrollStatus": {"status_column": "Status", "active_values": ["Enrolled"]}}
        assert list(_status(df, fm)) == ["Inactive", "Enrolled"]

    def test_custom_status_and_withdraw_column_names(self):
        df = pd.DataFrame(
            {
                "status": ["Active", ""],
                "left on": ["", "2020-01-01"],
                "student number": ["A", "B"],
            }
        )
        fm = {"EnrollStatus": {"status_column": "Status", "withdraw_date_column": "Left On"}}
        # A: active by the custom status column; B: blank status → custom withdraw-date fallback → Inactive
        assert list(_status(df, fm)) == ["Active", "Inactive"]

    def test_empty_frame_returns_empty_series(self):
        df = pd.DataFrame({"enrolment status": pd.Series([], dtype="object")})
        assert list(_status(df)) == []


class TestFilterToActive:
    """`filter_to_active` — single source of truth for the zero-orphan filter.

    Both the homeroom (demographic) and subject (schedule) student-row
    derivations route through this helper, so no emitted student row references
    a `User ID` absent from Students.csv.
    """

    def _ctx(self, ids):
        ctx = TransformContext()
        ctx.active_student_ids = set(ids)
        return ctx

    def test_keeps_only_active_rows(self):
        df = pd.DataFrame({"student number": ["S001", "S002", "S003"], "x": [1, 2, 3]})
        out = BaseTransformer.filter_to_active(df, "student number", self._ctx({"S001", "S003"}))
        assert list(out["student number"]) == ["S001", "S003"]

    def test_normalizes_whitespace_both_sides(self):
        # Frame values carry incidental whitespace; the roster set is trimmed.
        df = pd.DataFrame({"student number": [" S001 ", "S002"], "x": [1, 2]})
        out = BaseTransformer.filter_to_active(df, "student number", self._ctx({"S001"}))
        assert list(out["x"]) == [1]

    def test_empty_roster_returns_unchanged_and_warns(self, caplog):
        df = pd.DataFrame({"student number": ["S001", "S002"]})
        with caplog.at_level("WARNING"):
            out = BaseTransformer.filter_to_active(df, "student number", self._ctx(set()), caller="Classes")
        # Never filter-to-empty: all rows survive.
        assert len(out) == 2
        assert any("active_student_ids empty" in r.message for r in caplog.records)
        assert any("[Classes]" in r.message for r in caplog.records)

    def test_missing_column_returns_unchanged_and_warns(self, caplog):
        df = pd.DataFrame({"other col": ["S001"]})
        with caplog.at_level("WARNING"):
            out = BaseTransformer.filter_to_active(df, "student number", self._ctx({"S001"}))
        assert len(out) == 1
        assert any("active_student_ids empty" in r.message for r in caplog.records)

    def test_returns_copy_safe_to_mutate(self):
        df = pd.DataFrame({"student number": ["S001", "S002"], "grade": ["K", "1"]})
        out = BaseTransformer.filter_to_active(df, "student number", self._ctx({"S001"}))
        out.loc[out.index[0], "grade"] = "MUTATED"
        # Source frame is untouched (no shared view).
        assert "MUTATED" not in df["grade"].values

    def test_caller_label_in_warning(self, caplog):
        df = pd.DataFrame({"student number": ["S001"]})
        with caplog.at_level("WARNING"):
            BaseTransformer.filter_to_active(df, "student number", self._ctx(set()), caller="Enrollments")
        assert any("[Enrollments]" in r.message for r in caplog.records)


class TestPastWithdrawDate:
    @pytest.mark.parametrize("value", ["", None, np.nan])
    def test_blank_is_not_withdrawn(self, value):
        assert BaseTransformer.past_withdraw_date(value, date(2025, 6, 1)) is False

    @pytest.mark.parametrize(
        "value",
        ["15-Jan-2020", "2020-06-15", "06/15/2020", "15/06/2020"],
    )
    def test_past_dates_all_four_formats(self, value):
        assert BaseTransformer.past_withdraw_date(value, date(2025, 6, 1)) is True

    def test_future_date_not_withdrawn(self):
        assert BaseTransformer.past_withdraw_date("2099-12-31", date(2025, 6, 1)) is False

    def test_unparseable_is_treated_as_withdrawn(self):
        assert BaseTransformer.past_withdraw_date("garbage", date(2025, 6, 1)) is True


class TestActiveStatusPathLogging:
    """Each run logs (INFO) which signal decided 'active' — the status column or
    the withdraw date — so an unattended run self-documents its resolution path
    (the cheap observability that would have made the SD40 May-6 diagnosis instant).
    """

    def test_logs_status_column_path(self, caplog):
        df = pd.DataFrame({"enrollment status": ["Active"], "student number": ["S1"]})
        with caplog.at_level(logging.INFO, logger="src.etl.transformers.base"):
            _status(df)
        assert any("via status column 'enrollment status'" in r.message for r in caplog.records)

    def test_logs_withdraw_date_path(self, caplog):
        df = pd.DataFrame({"withdraw date": ["2020-01-01"], "student number": ["S1"]})
        with caplog.at_level(logging.INFO, logger="src.etl.transformers.base"):
            _status(df)
        assert any("via withdraw-date column 'withdraw date'" in r.message for r in caplog.records)
