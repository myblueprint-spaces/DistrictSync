"""Grade vocabulary helpers: CEDS grade mapping + the homeroom/subject split.

MyEd BC grade values ("K", "01", "Kindergarten", ...) are translated to CEDS
codes once, here, and every consumer (Students, Classes, Enrollments, blended
detection) shares the same table. The homeroom/subject split — convert a grade
column to CEDS, then partition rows by ``homeroom_grades`` membership — was
previously duplicated across four call sites in Classes and Enrollments; it is
hoisted into :func:`split_by_homeroom_grades`.

This module also owns the OPT-IN class-rostering scope
(``global_config.class_rostering_grades``): :func:`resolve_timetable_scope`
turns the config value plus ``homeroom_grades`` into the set of CEDS grades
that receive SUBJECT (timetable) rostering, and :func:`split_by_homeroom_grades`
applies it. The scope lives HERE, inside the one function that performs the
grade→CEDS conversion, because ``grade_to_ceds`` is NOT idempotent (``KG``,
``IT``, ``PR``, ``PK``, ``PS`` are CEDS values that are not also keys of
:data:`CEDS_MAPPING`, so re-converting them yields ``"UG"``) — an external
filter applied on the wrong side of a conversion would silently de-roster
Kindergarten.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Optional

import pandas as pd

# The config layer owns the `class_rostering_grades` vocabulary (it declares the
# key and validates it), exactly as it owns ALLOWED_TRANSFORMS — so the sentinel
# is imported from there rather than respelled. Direction stays etl -> config.
from src.config.models import CLASS_ROSTERING_HOMEROOM_SENTINEL

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


# The CEDS OUTPUT vocabulary — every code a grade column can be converted TO.
# Derived from the table above so the two can never drift; it is the valid set
# for `homeroom_grades` / `class_rostering_grades` at the config boundary (see
# `src/config/models.py`). NOTE "Other" is the one mixed-case member, so a
# consumer must compare EXACTLY, never case-normalise.
CEDS_GRADE_CODES: frozenset[str] = frozenset(CEDS_MAPPING.values())


def grade_to_ceds(grade_value: Any) -> str:
    """Map a raw source grade value to its CEDS code ('UG' when unknown)."""
    original = str(grade_value).strip().upper() if pd.notna(grade_value) else ""
    return CEDS_MAPPING.get(original, "UG")


def resolve_timetable_scope(
    global_config: Mapping[str, Any],
    homeroom_grades: Sequence[str],
) -> Optional[set[str]]:
    """The CEDS grades that receive SUBJECT (timetable) rostering, or None.

    ``None`` means NO scope is in force — today's behaviour exactly: the
    timetable side is the unbounded complement of ``homeroom_grades``. Any
    returned SET is a positive, bounded scope, and an EMPTY set is meaningful
    ("roster no timetable classes at all"), so every consumer must branch on
    ``is None``, never on truthiness and never on which config key produced it.

    Two configured forms (validated at the config boundary — see
    ``GlobalConfig.check_rostering_grade_scopes``):

    - the ``"homeroom"`` sentinel ⇒ rostered ≡ ``homeroom_grades`` ⇒ the
      timetable scope is EMPTY;
    - a list of CEDS codes ⇒ the timetable scope is that list MINUS
      ``homeroom_grades`` (the config boundary guarantees
      ``homeroom_grades ⊆ class_rostering_grades``, which is why the homeroom
      path needs no scoping of its own).

    ``homeroom_grades`` is passed in rather than re-read so the scope is
    always derived from the SAME list the caller partitions on.

    Fail-loud: an unrecognised string value raises. Config-boundary validation
    already rejects it, but this function also serves raw-dict callers (the UI
    adapter, tests), and a mis-spelled sentinel must never be read as a grade
    list — ``set("09")`` would silently scope to ``{"0", "9"}``.
    """
    configured = global_config.get("class_rostering_grades")
    if configured is None:
        return None
    if isinstance(configured, str):
        if configured != CLASS_ROSTERING_HOMEROOM_SENTINEL:
            raise ValueError(
                f"global_config.class_rostering_grades must be a list of CEDS grade codes or the "
                f"string {CLASS_ROSTERING_HOMEROOM_SENTINEL!r} (got {configured!r})."
            )
        return set()
    return set(configured) - set(homeroom_grades)


def split_by_homeroom_grades(
    df: pd.DataFrame,
    grade_col: str,
    homeroom_grades: list,
    *,
    keep: Literal["homeroom", "subject"],
    timetable_scope: Optional[set[str]] = None,
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
      ``homeroom_grades`` — or, when ``timetable_scope`` is given, the rows
      whose CEDS grade IS in that scope (a positive bound; see
      :func:`resolve_timetable_scope`). An EMPTY scope keeps nothing, which is
      the ``"homeroom"`` sentinel's whole point.

    ``timetable_scope`` is only meaningful for ``keep="subject"``: the
    ``homeroom_grades ⊆ class_rostering_grades`` subset rule makes the homeroom
    side unaffected by the scope. Passing one with ``keep="homeroom"`` therefore
    RAISES rather than being ignored — a filter argument silently dropped is
    exactly the permissive-default the engineering rules ban.

    Fail-loud: a missing ``grade_col`` raises ``KeyError`` (a renamed source
    column must never silently keep or drop everyone).
    """
    if keep == "homeroom":
        if timetable_scope is not None:
            raise ValueError(
                "split_by_homeroom_grades(keep='homeroom') does not accept a timetable_scope: "
                "the homeroom side is scoped by homeroom_grades alone (homeroom_grades is a "
                "validated SUBSET of class_rostering_grades). Passing one here would be silently "
                "ignored — drop the argument, or use keep='subject'."
            )
        df[grade_col] = df[grade_col].apply(grade_to_ceds)
        return df[df[grade_col].isin(homeroom_grades)]  # type: ignore[return-value]
    df["grade_ceds"] = df[grade_col].apply(grade_to_ceds)
    if timetable_scope is None:
        return df[~df["grade_ceds"].isin(homeroom_grades)].copy()  # type: ignore[return-value]
    return df[df["grade_ceds"].isin(timetable_scope)].copy()  # type: ignore[return-value]
