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
from src.etl.transformers.grades import CEDS_GRADE_CODES, grade_to_ceds
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

    @given(grade=st.text())
    @settings(max_examples=500)
    def test_grade_to_ceds_only_ever_returns_a_CEDS_OUTPUT_code(self, grade):
        """RANGE CONTAINMENT: every value `grade_to_ceds` can return is a member
        of `CEDS_GRADE_CODES` (plan 0043, slice 1).

        This was a harmless curiosity while `CEDS_GRADE_CODES` only validated
        config; it is load-bearing now that `timetable_rostered_grades` MASKS on
        it. If the fallback literal "UG" ever stopped being a table value — say
        the "UGRADED"/"UNGRADED"/"UG" rows were tidied away as duplicates — the
        set would lose "UG" while `grade_to_ceds` kept returning it, and every
        unknown-grade row would be silently dropped from subject rostering with
        a green golden. The coupling is invisible from either side, so it is
        pinned here rather than commented.
        """
        assert grade_to_ceds(grade) in CEDS_GRADE_CODES

    def test_grade_to_ceds_range_holds_for_null_like_values_too(self):
        """The null-like inputs the property strategy cannot generate — and the
        realistic ones: a blank cell in a real CSV reads as NaN, and NaN is the
        input that takes the "UG" fallback in production."""
        import pandas as pd

        for val in [None, float("nan"), pd.NA, "", "  ", "definitely not a grade"]:
            assert grade_to_ceds(val) in CEDS_GRADE_CODES, f"out-of-range CEDS code for val={val!r}"

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
        assert len(result) <= max_len, f"truncate_name({name!r}, {max_len}) returned {len(result)}-char string"

    @given(name=st.text(max_size=50))
    def test_short_names_are_never_truncated(self, name):
        """Names <= 100 chars must be returned unchanged."""
        if len(name) <= 100:
            assert BaseTransformer.truncate_name(name) == name


# ---------------------------------------------------------------------------
# apply_field_map totality (plan 0053 S13b, failure-policy.md §2 scope ladder / §5)
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

from src.config.models import (  # noqa: E402
    ALLOWED_TRANSFORMS,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldEnrollStatus,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
)
from src.etl.errors import SourceSchemaError  # noqa: E402
from src.etl.transformers.context import TransformContext  # noqa: E402

#: The normalized (lower-cased) header vocabulary a generated export draws from; a mapping may
#: also name a column the export does not carry (the intended-blank branch).
_HEADER_VOCABULARY = ("student number", "grade", "legal first name", "teacher id", "master timetable id", "email")
_ABSENT = "not in this export"
_OUTPUT_FIELDS = ("User ID", "Grade", "First Name", "Class ID", "Email", "Role", "Start Date", "School ID", "Name")

_cell = st.one_of(
    st.none(),
    st.text(max_size=12),
    st.sampled_from(["", " ", "K", "12", "Y", "N", "nan", "teacher", "administrator", "2025-09-01", "01/02/2024"]),
)
# Any case: every shape lower-cases its column before reading it.
_column = st.sampled_from((*_HEADER_VOCABULARY, _ABSENT)).flatmap(lambda c: st.sampled_from([c, c.upper(), c.title()]))

#: One strategy per supported field-mapping shape (the `models.FieldMapping` union), built through
#: the models themselves so every generated spec is one a validated config could hold.
_field_spec = st.one_of(
    st.none(),
    _column,
    st.builds(FieldTransform, column=_column, transform=st.sampled_from(["", *sorted(ALLOWED_TRANSFORMS)])),
    st.builds(FieldFixedValue, value=st.text(max_size=8)),
    st.just(FieldAcademicYear()),
    st.just(FieldAcademicYear(use_academic_year=False, value="2025-09-01")),
    st.builds(FieldAppendYear, column=_column, append_year_to_id=st.booleans()),
    st.builds(FieldEmailFormat, format=st.just("{student number}@example.test"), sanitize=st.booleans()),
    st.builds(FieldNameConfig, primary_teacher_flag=_column, teacher_last_name=_column),
    st.builds(FieldIdRolePair, student_id_col=_column, staff_id_col=_column),
    st.just(FieldEnrollStatus()),
)


@st.composite
def _export(draw) -> pd.DataFrame:
    header = draw(st.lists(st.sampled_from(_HEADER_VOCABULARY), unique=True, max_size=len(_HEADER_VOCABULARY)))
    rows = draw(st.integers(min_value=0, max_value=6))
    return pd.DataFrame(
        {column: draw(st.lists(_cell, min_size=rows, max_size=rows)) for column in header}, index=range(rows)
    )


class _Host(BaseTransformer):
    """A concrete ``BaseTransformer`` (the ABC requires ``transform``) to host the engine."""

    def transform(self, df, mapping, context):  # pragma: no cover — unused
        return df


def _apply(working: pd.DataFrame, field_map: dict) -> pd.DataFrame:
    context = TransformContext()
    context.set_school_year(2026, "09-01", "06-30")
    return _Host().apply_field_map(working, pd.DataFrame(index=working.index), field_map, "Students", context)


def _must_raise(working: pd.DataFrame, field_map: dict) -> bool:
    """The ONE sanctioned raise: an append-year ID whose column the export lacks (§5 #29)."""
    return any(
        isinstance(spec, FieldAppendYear) and spec.append_year_to_id and spec.column.lower() not in working.columns
        for spec in field_map.values()
    )


@pytest.mark.property
class TestApplyFieldMapTotality:
    """The field-map engine is TOTAL over the supported shape vocabulary (plan 0053 S13b).

    Whatever the export and the mapping, ``apply_field_map`` returns exactly one output row per
    input row with every mapped field present — a bad cell or column blanks and never raises —
    and the only exception that may escape is the typed ``SourceSchemaError`` of an append-year
    identity whose column is absent (§5 #29), which then ALWAYS escapes. (That a blanked cell is
    also RECORDED in ``context.data_errors`` is not asserted here.)
    """

    @given(working=_export(), field_map=st.dictionaries(st.sampled_from(_OUTPUT_FIELDS), _field_spec, max_size=6))
    @settings(max_examples=150, deadline=None)
    def test_never_raises_and_keeps_every_row(self, working: pd.DataFrame, field_map: dict) -> None:
        try:
            out = _apply(working, field_map)
        except SourceSchemaError as exc:
            assert _must_raise(working, field_map), f"unsanctioned raise: {exc.columns} / {exc.guard}"
            return
        assert not _must_raise(working, field_map), "an absent append-year ID column must fail closed"
        assert len(out) == len(working)
        assert list(out.index) == list(working.index)
        assert set(field_map) <= set(out.columns)

    # The twins: each branch of the property is reachable, with the inputs it names.
    def test_twin_an_absent_append_year_column_raises_typed(self) -> None:
        working = pd.DataFrame({"grade": ["1"]})
        with pytest.raises(SourceSchemaError):
            _apply(working, {"Class ID": FieldAppendYear(column="Master Timetable ID")})

    def test_twin_the_same_map_over_an_export_carrying_the_column_keeps_every_row(self) -> None:
        working = pd.DataFrame({"master timetable id": ["MT1", "MT2"], "grade": ["1", None]})
        out = _apply(
            working,
            {
                "Class ID": FieldAppendYear(column="Master Timetable ID"),
                "Grade": FieldTransform(column="Grade", transform="grade_to_ceds"),
                "Role": FieldTransform(column="Grade", transform="normalize_staff_role"),  # raises per row → blank
                "Email": _ABSENT,  # an intended blank
            },
        )
        assert len(out) == 2 and {"Class ID", "Grade", "Role", "Email"} <= set(out.columns)
        assert out["Role"].isna().all()
