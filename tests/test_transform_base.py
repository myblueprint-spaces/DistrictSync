"""Tests for BaseTransformer shared utilities.

Focused on the helpers used by the CourseInfo + StudentCourses entities
(`filter_excluded_course_code_patterns`, `clean_course_code_flavor`).
Existing helpers like `filter_excluded_course_codes` are exercised
indirectly through the entity transformer tests.
"""

import numpy as np
import pandas as pd
import pytest

from src.etl.transformers.base import BaseTransformer

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
