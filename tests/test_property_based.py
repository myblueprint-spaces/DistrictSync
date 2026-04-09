"""Property-based tests using Hypothesis.

These tests generate random inputs to find edge cases that hand-written
tests miss. They verify invariants that should always hold regardless of
the input — functions never crash unexpectedly, results are always in
range, etc.
"""

import pytest

pytest.importorskip("hypothesis", reason="hypothesis not installed — skipping property-based tests")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from src.etl.transformers.base import BaseTransformer
from src.utils.validators import validate_run_time, validate_sis_type, validate_task_name

# ---------------------------------------------------------------------------
# Validator property tests
# ---------------------------------------------------------------------------


@pytest.mark.property
class TestValidatorsPropertyBased:
    @given(value=st.text())
    @settings(max_examples=500)
    def test_validate_sis_type_only_raises_value_error(self, value):
        """validate_sis_type must only raise ValueError (or return) — never crash."""
        import re

        try:
            result = validate_sis_type(value)
            # If it returns, the result must match the alphanumeric/underscore pattern
            assert re.match(r"^[a-zA-Z0-9_]+$", result), f"Returned invalid SIS type: {result!r}"
        except ValueError:
            pass  # Expected for invalid inputs

    @given(value=st.text())
    @settings(max_examples=500)
    def test_validate_task_name_only_raises_value_error(self, value):
        """validate_task_name must only raise ValueError — never crash."""
        try:
            result = validate_task_name(value)
            # Returned value should only contain safe chars
            import re

            assert re.match(r"^[a-zA-Z0-9_ -]+$", result), f"Returned unsafe task name: {result!r}"
        except ValueError:
            pass

    @given(hour=st.integers(0, 23), minute=st.integers(0, 59))
    def test_validate_run_time_accepts_valid_hh_mm(self, hour, minute):
        """Valid HH:MM times should always be accepted without raising."""
        time_str = f"{hour:02d}:{minute:02d}"
        h, m = validate_run_time(time_str)
        assert h == f"{hour:02d}"
        assert m == f"{minute:02d}"

    @given(hour=st.integers(24, 99), minute=st.integers(0, 59))
    def test_validate_run_time_rejects_invalid_hour(self, hour, minute):
        """Hours >= 24 must always raise ValueError."""
        time_str = f"{hour:02d}:{minute:02d}"
        with pytest.raises(ValueError):
            validate_run_time(time_str)

    @given(hour=st.integers(0, 23), minute=st.integers(60, 99))
    def test_validate_run_time_rejects_invalid_minute(self, hour, minute):
        """Minutes >= 60 must always raise ValueError."""
        time_str = f"{hour:02d}:{minute:02d}"
        with pytest.raises(ValueError):
            validate_run_time(time_str)

    @given(value=st.text().filter(lambda s: ":" not in s))
    @settings(max_examples=200)
    def test_validate_run_time_rejects_non_hh_mm_format(self, value):
        """Strings without ':' are never valid run times."""
        with pytest.raises(ValueError):
            validate_run_time(value)


# ---------------------------------------------------------------------------
# Grade mapping property tests
# ---------------------------------------------------------------------------


@pytest.mark.property
class TestGradeMappingPropertyBased:
    @given(grade=st.text())
    @settings(max_examples=500)
    def test_grade_to_ceds_always_returns_string(self, grade):
        """grade_to_ceds must always return a string — never None or crash."""
        result = BaseTransformer.grade_to_ceds(grade)
        assert isinstance(result, str), f"Expected str, got {type(result)} for grade={grade!r}"

    @given(grade=st.text())
    @settings(max_examples=500)
    def test_grade_to_ceds_never_returns_empty_string(self, grade):
        """grade_to_ceds always returns a non-empty string (defaults to 'UG')."""
        result = BaseTransformer.grade_to_ceds(grade)
        assert len(result) > 0, f"Empty string returned for grade={grade!r}"

    @given(grade_int=st.integers(1, 13))
    def test_ceds_integer_grades_are_zero_padded(self, grade_int):
        """Grades 1-13 as strings always produce exactly 2-character CEDS codes."""
        result = BaseTransformer.grade_to_ceds(str(grade_int))
        assert len(result) == 2, f"Expected 2-char CEDS for grade {grade_int}, got {result!r}"
        assert result == f"{grade_int:02d}", f"Expected zero-padded {grade_int:02d}, got {result!r}"

    @given(grade_int=st.integers(1, 9))
    def test_single_digit_grades_produce_zero_padded_result(self, grade_int):
        """Single-digit grades 1-9 must always be zero-padded to 2 chars."""
        result = BaseTransformer.grade_to_ceds(str(grade_int))
        assert result.startswith("0"), f"Grade {grade_int} should zero-pad to '0{grade_int}', got {result!r}"

    def test_grade_to_ceds_with_none_like_values(self):
        """Falsy / None-like values should return 'UG' (ungraded) without crashing."""
        import pandas as pd

        for val in [None, float("nan"), pd.NA, "", "  "]:
            result = BaseTransformer.grade_to_ceds(val)
            assert isinstance(result, str), f"Expected str for val={val!r}"

    def test_grade_to_ceds_case_insensitive(self):
        """Grade lookup is case-insensitive — 'k' and 'K' both map to 'KG'.

        Note: 'KG' is the CEDS output value, not a recognised source grade.
        The mapping converts SOURCE grade names (K, KINDERGARTEN) to CEDS codes.
        """
        assert BaseTransformer.grade_to_ceds("k") == "KG"
        assert BaseTransformer.grade_to_ceds("K") == "KG"
        assert BaseTransformer.grade_to_ceds("kindergarten") == "KG"
        assert BaseTransformer.grade_to_ceds("KINDERGARTEN") == "KG"


# ---------------------------------------------------------------------------
# Truncate name property tests
# ---------------------------------------------------------------------------


@pytest.mark.property
class TestTruncateNamePropertyBased:
    @given(name=st.text(), max_len=st.integers(10, 200))
    @settings(max_examples=300)
    def test_truncate_never_exceeds_max_len(self, name, max_len):
        """Result must never exceed max_len characters."""
        result = BaseTransformer.truncate_name(name, max_len)
        assert len(result) <= max_len, (
            f"truncate_name({name!r}, {max_len}) returned {len(result)}-char string"
        )

    @given(name=st.text(max_size=50))
    def test_short_names_are_never_truncated(self, name):
        """Names <= 100 chars must be returned unchanged."""
        if len(name) <= 100:
            assert BaseTransformer.truncate_name(name) == name
