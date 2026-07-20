"""Grade vocabulary helpers: CEDS grade mapping + the homeroom/subject split.

MyEd BC grade values ("K", "01", "Kindergarten", ...) are translated to CEDS
codes once, here, and every consumer (Students, Classes, Enrollments, blended
detection) shares the same table. The homeroom/subject split — convert a grade
column to CEDS, then partition rows by ``homeroom_grades`` membership — was
previously duplicated across four call sites in Classes and Enrollments; it is
hoisted into :func:`split_by_homeroom_grades`.
"""

from typing import Any, Literal

import pandas as pd

# CEDS grade-level code table (single source of truth; keys are the upper-cased,
# trimmed source values). Unknown values map to "UG" (ungraded).
CEDS_MAPPING: dict[str, str] = {
    "INFANT/TODDLER": "IT",
    "PRESCHOOL": "PR",
    "PRE-K": "PK",
    "PREKINDERGARTEN": "PK",
    "TK": "TK",
    "TRANSITIONAL KINDERGARTEN": "TK",
    "KINDERGARTEN": "KG",
    "K": "KG",
    "01": "01",
    "1": "01",
    "02": "02",
    "2": "02",
    "03": "03",
    "3": "03",
    "04": "04",
    "4": "04",
    "05": "05",
    "5": "05",
    "06": "06",
    "6": "06",
    "07": "07",
    "7": "07",
    "08": "08",
    "8": "08",
    "09": "09",
    "9": "09",
    "10": "10",
    "11": "11",
    "12": "12",
    "13": "13",
    "POSTSECONDARY": "PS",
    "UGRADED": "UG",
    "UNGRADED": "UG",
    "UG": "UG",
    "OTHER": "Other",
    "EL": "KG",
    "KF": "KG",
}


def grade_to_ceds(grade_value: Any) -> str:
    """Map a raw source grade value to its CEDS code ('UG' when unknown)."""
    original = str(grade_value).strip().upper() if pd.notna(grade_value) else ""
    return CEDS_MAPPING.get(original, "UG")


def split_by_homeroom_grades(
    df: pd.DataFrame,
    grade_col: str,
    homeroom_grades: list,
    *,
    keep: Literal["homeroom", "subject"],
) -> pd.DataFrame:
    """Convert grades to CEDS and keep the homeroom or the subject side.

    The one shared spelling of the grade→CEDS→homeroom split used by Classes
    and Enrollments (previously four duplicated sites). Two flavors, matching
    the two source shapes exactly:

    - ``keep="homeroom"`` (demographic frames): overwrite ``grade_col`` with
      its CEDS value IN PLACE (downstream homeroom output reads the converted
      column) and return the rows whose CEDS grade IS in ``homeroom_grades``
      (a filtered view — callers copy when they need to mutate).
    - ``keep="subject"`` (schedule frames): derive a NEW ``grade_ceds`` column
      (the raw grade column is preserved — the Classes Grade output re-derives
      from it) and return a COPY of the rows whose CEDS grade is NOT in
      ``homeroom_grades``.

    Fail-loud: a missing ``grade_col`` raises ``KeyError`` (a renamed source
    column must never silently keep or drop everyone).
    """
    if keep == "homeroom":
        df[grade_col] = df[grade_col].apply(grade_to_ceds)
        return df[df[grade_col].isin(homeroom_grades)]  # type: ignore[return-value]
    df["grade_ceds"] = df[grade_col].apply(grade_to_ceds)
    return df[~df["grade_ceds"].isin(homeroom_grades)].copy()  # type: ignore[return-value]
