"""Course-code exclusion + cleaning helpers.

Config-driven filtering of schedule / class-info / course rows by course code
(exact codes, regex patterns, and the derived early-grade floor) plus the
legacy "flavor" truncation ported from the SD62 PowerShell scripts. Shared by
Classes, Enrollments, blended detection, CourseInfo, and StudentCourses.
"""

from typing import Any, Optional

import pandas as pd

from src.etl.column_names import COURSE_CODE, DISTRICT_COURSE_CODE
from src.etl.transformers.ids import normalize_id_series


def resolve_course_code_column(df: pd.DataFrame) -> Optional[str]:
    """Which column names the course code in this frame, or None for neither.

    THE single spelling of the alias precedence: ``course code`` first, then
    ``district course code``. ClassInformation uses the former; the
    deduplicated-schedule fallback frame (SD40's real shape — its
    ClassInformation carries no Master Timetable ID) uses the latter. The two
    share a value space, because ``classes.py``'s ``_merge_course_and_staff``
    renames one onto the other before joining the same course-title source —
    which is why a title map keyed by ``course code`` can be looked up with
    either.

    It lives here rather than beside any one consumer because the precedence is
    a property of MyEd BC exports, not of the entity reading them: a district
    exporting a third spelling must be a ONE-file change. Both filters below
    consume it, as does blended detection's name builder.
    """
    for col in (COURSE_CODE, DISTRICT_COURSE_CODE):
        if col in df.columns:
            return col
    return None


def filter_excluded_course_codes(df: pd.DataFrame, excluded_codes: list[str]) -> pd.DataFrame:
    """Drop rows whose course code matches an entry in excluded_codes.

    Resolves the column via :func:`resolve_course_code_column`. Match is
    case-insensitive and whitespace-trimmed. Returns df unchanged when
    excluded_codes is empty or neither column is present.
    """
    if not excluded_codes or df.empty:
        return df
    exclusion_set = {str(c).strip().upper() for c in excluded_codes}
    col = resolve_course_code_column(df)
    if col:
        values = normalize_id_series(df[col]).str.upper()
        return df[~values.isin(exclusion_set)].copy()  # type: ignore[return-value]
    return df


def filter_excluded_course_code_patterns(
    df: pd.DataFrame,
    patterns: list[str],
    column: Optional[str] = None,
) -> pd.DataFrame:
    """Drop rows whose course code matches any regex in `patterns`.

    Patterns are combined into a single case-insensitive alternation
    and applied to the trimmed string value. When `column` is None, the
    alias precedence is resolved by :func:`resolve_course_code_column`;
    an EXPLICIT `column` is used as given and never falls back to the
    alias (an unfound one leaves df unchanged, as before). Patterns are
    expected to be pre-validated at config load time.
    """
    if not patterns or df.empty:
        return df
    combined = "|".join(f"(?:{p})" for p in patterns)
    col = column if column else resolve_course_code_column(df)
    if col and col in df.columns:
        values = normalize_id_series(df[col])
        matches = values.str.contains(combined, regex=True, case=False, na=False)
        return df[~matches].copy()  # type: ignore[return-value]
    return df


def early_grade_exclusion_pattern(start_grade: Any) -> Optional[str]:
    """Regex that drops MyEd BC course codes for grades below `start_grade`.

    MyEd BC encodes the grade in the 6th-7th characters of the course code
    as a two-digit number; single-digit grades 01-09 appear as "0X".
    This builds a pattern matching "0" followed by any digit strictly below
    `start_grade`, so grades >= start_grade (including 10-12, which begin
    with "1") survive. With start_grade=10 the result is equivalent to the
    legacy ``^.{5}0\\d`` pattern (excludes 00-09). Returns None when
    start_grade <= 1 (nothing to exclude).
    """
    try:
        sg = int(start_grade)
    except (TypeError, ValueError):
        sg = 10
    sg = min(sg, 10)
    if sg <= 1:
        return None
    return rf"^.{{5}}0[0-{sg - 1}]"


def effective_course_code_patterns(global_config: dict) -> list[str]:
    """Configured exclusion patterns plus the grade floor derived from
    `course_start_grade` (default 10). Used by the CourseInfo and
    StudentCourses transformers so the minimum grade is a single,
    editable knob rather than a hand-written regex.
    """
    patterns = list(global_config.get("excluded_course_code_patterns", []))
    early = early_grade_exclusion_pattern(global_config.get("course_start_grade", 10))
    if early:
        patterns.append(early)
    return patterns


def clean_course_code_flavor(code: Any, flavors: list[str]) -> str:
    """Truncate course code to first 7 chars if it contains any flavor substring.

    Mirrors the PowerShell Get-CleanedCourseCode helper. Matching is
    case-insensitive substring (e.g., "DL" matches "MATH-DL01" -> "MATH-DL").
    Returns the original code as a string when no flavor matches, or ""
    for NaN/None inputs.
    """
    if code is None or (isinstance(code, float) and pd.isna(code)):
        return ""
    code_str = str(code)
    if not code_str or not flavors:
        return code_str
    upper = code_str.upper()
    for flavor in flavors:
        f = str(flavor).strip().upper()
        if f and f in upper:
            return code_str[:7]
    return code_str
