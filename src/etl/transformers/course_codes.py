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


def filter_excluded_course_codes(df: pd.DataFrame, excluded_codes: list[str]) -> pd.DataFrame:
    """Drop rows whose course code matches an entry in excluded_codes.

    Checks `course code` first, then `district course code`. Match is
    case-insensitive and whitespace-trimmed. Returns df unchanged when
    excluded_codes is empty or neither column is present.
    """
    if not excluded_codes or df.empty:
        return df
    exclusion_set = {str(c).strip().upper() for c in excluded_codes}
    for col in (COURSE_CODE, DISTRICT_COURSE_CODE):
        if col in df.columns:
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
    and applied to the trimmed string value. When `column` is None,
    checks `course code` then `district course code` (first found
    wins), matching `filter_excluded_course_codes`. Patterns are
    expected to be pre-validated at config load time.
    """
    if not patterns or df.empty:
        return df
    combined = "|".join(f"(?:{p})" for p in patterns)
    candidate_cols = [column] if column else [COURSE_CODE, DISTRICT_COURSE_CODE]
    for col in candidate_cols:
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
