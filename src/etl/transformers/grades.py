"""Grade vocabulary helpers: CEDS grade mapping + the homeroom/subject split.

MyEd BC grade values ("K", "01", "Kindergarten", ...) are translated to CEDS
codes once, here, and every consumer (Students, Classes, Enrollments, blended
detection) shares the same table. The homeroom/subject split — convert a grade
column to CEDS, then partition rows by ``homeroom_grades`` membership — was
previously duplicated across four call sites in Classes and Enrollments; it is
hoisted into :func:`split_by_homeroom_grades`.

This module also owns the two OPT-IN rostering scopes, because both are
expressed in the CEDS vocabulary above:

- ``global_config.class_rostering_grades`` → :func:`resolve_timetable_scope`,
  the set of CEDS grades that receive SUBJECT (timetable) rostering, applied by
  :func:`split_by_homeroom_grades`;
- ``global_config.student_rostering_grades`` → :func:`resolve_student_scope`,
  the OUTERMOST bound (which grades of students reach the output at all),
  applied by :func:`filter_to_grade_scope` from ``StudentTransformer``. When it
  is set and ``class_rostering_grades`` is ABSENT, the timetable scope INHERITS
  it (``student − homeroom``) rather than staying the unbounded complement — so
  no grade can be class-rostered without its students being sent.

Each scope is applied by the function that performs its OWN grade→CEDS
conversion, and neither ever rewrites a raw grade column that is converted again
downstream, because ``grade_to_ceds`` is NOT idempotent (``KG``, ``IT``, ``PR``,
``PK``, ``PS`` are CEDS values that are not also keys of :data:`CEDS_MAPPING`,
so re-converting them yields ``"UG"``). A filter applied on the wrong side of a
conversion would silently de-roster Kindergarten — or, worse for the Students
path where the field_map converts the same raw column again, SHIP every
Kindergarten student as ``Grade = "UG"``.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Optional

import pandas as pd

# The config layer owns the `class_rostering_grades` vocabulary (it declares the
# key and validates it), exactly as it owns ALLOWED_TRANSFORMS — so the sentinel
# is imported from there rather than respelled. The two modules CO-OWN this
# vocabulary and the pair is a cycle broken by lazy binding: the sentinel is
# bound here at module level, while `models` binds CEDS_GRADE_CODES back from
# this module lazily inside `_ceds_grade_codes()` (a module-level import there
# would run `src.etl.transformers.__init__` → `base.py` → `models`, while
# `models` is still initialising). If a later slice wants layering purity, the
# clean move is a neutral grade-vocabulary module both layers import.
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


def resolve_student_scope(global_config: Mapping[str, Any]) -> Optional[set[str]]:
    """The CEDS grades whose STUDENTS reach the output at all, or None.

    ``None`` means NO scope is in force — today's behaviour exactly: every grade
    is sent, and the active-status filter alone decides inclusion. Any returned
    SET is a positive, bounded scope; the config boundary rejects an empty list
    (``student_rostering_grades: []`` = "send nobody", which could only ever end
    at the delivery floor), so a returned set is never empty.

    THE single reading of the key: :class:`StudentTransformer` consumes it to
    filter the roster, and :func:`resolve_timetable_scope` consumes it for the
    inherited class bound, so the two can never disagree about what was
    configured.

    Fail-loud on a string, for the same reason as the timetable resolver: this
    also serves raw-dict callers, and ``set("09")`` would silently scope to
    ``{"0", "9"}``.
    """
    configured = global_config.get("student_rostering_grades")
    if configured is None:
        return None
    if isinstance(configured, str):
        raise ValueError(
            f"global_config.student_rostering_grades must be a list of CEDS grade codes "
            f"(got the bare string {configured!r}). There is no "
            f"{CLASS_ROSTERING_HOMEROOM_SENTINEL!r} shortcut for students."
        )
    return set(configured)


def resolve_timetable_scope(
    global_config: Mapping[str, Any],
    homeroom_grades: Sequence[str],
) -> Optional[set[str]]:
    """The CEDS grades that receive SUBJECT (timetable) rostering, or None.

    ``None`` means NO scope is in force — today's behaviour exactly: the
    timetable side is the unbounded complement of ``homeroom_grades``. Any
    returned SET is a positive, bounded scope, and an EMPTY set is meaningful
    ("roster no timetable classes at all"), so every consumer must branch on
    ``is None``, **never on truthiness and never on whether a particular config
    key is present** — since slice 1b, TWO keys can produce a positive scope
    (see the inherited bound below), so a presence check on
    ``class_rostering_grades`` is silently dead on the path the bound adds.

    Three configured forms (all validated at the config boundary — see
    ``GlobalConfig.check_rostering_grade_scopes``):

    - the ``"homeroom"`` sentinel ⇒ rostered ≡ ``homeroom_grades`` ⇒ the
      timetable scope is EMPTY;
    - a ``class_rostering_grades`` list of CEDS codes ⇒ the timetable scope is
      that list MINUS ``homeroom_grades`` (the config boundary guarantees
      ``homeroom_grades ⊆ class_rostering_grades``, which is why the homeroom
      path needs no scoping of its own). It WINS over the inherited bound, and
      the chain guarantees it lies inside the student bound;
    - **the INHERITED bound** — ``class_rostering_grades`` absent while
      ``student_rostering_grades`` is set ⇒ the timetable scope is
      ``student_rostering_grades − homeroom_grades``, a positive set where the
      unbounded complement would otherwise be. Without it, subject and blended
      classes would be built for grades whose students are not being sent, i.e.
      a class with a teacher and zero students.

    ``homeroom_grades`` is passed in rather than re-read so the scope is
    always derived from the SAME list the caller partitions on.

    Fail-loud: an unrecognised string value raises. Config-boundary validation
    already rejects it, but this function also serves raw-dict callers (the UI
    adapter, tests), and a mis-spelled sentinel must never be read as a grade
    list — ``set("09")`` would silently scope to ``{"0", "9"}``.
    """
    configured = global_config.get("class_rostering_grades")
    if configured is None:
        students = resolve_student_scope(global_config)
        if students is None:
            return None
        return students - set(homeroom_grades)
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


#: The derived column :func:`filter_to_grade_scope` masks on. Deliberately NOT
#: ``grade_ceds`` — that name is a real, KEPT output of
#: :func:`split_by_homeroom_grades` (see ``keep="subject"``), so reusing it here
#: would mean dropping a caller's genuine column on the way out. A collision is
#: additionally REFUSED rather than tolerated (see the function), because a
#: silent overwrite-then-drop is exactly the kind of data loss this module's
#: non-idempotency rules exist to prevent.
_SCOPE_CEDS_COLUMN = "__districtsync_grade_scope_ceds__"


def filter_to_grade_scope(
    df: pd.DataFrame,
    grade_col: str,
    scope: set[str],
    *,
    caller: str,
) -> pd.DataFrame:
    """Keep the rows whose CEDS grade is IN ``scope`` — an inclusion filter.

    Deliberately NOT a third ``keep=`` flavor of
    :func:`split_by_homeroom_grades`: that function's contract is the
    homeroom/subject PARTITION, and this is an inclusion filter with no
    partition and no ``homeroom_grades`` argument to give meaning to.

    **Derive → mask → drop, and never in place.** The CEDS value is computed
    into a private temporary column (:data:`_SCOPE_CEDS_COLUMN`), used for the
    mask, and dropped again, so the returned frame's column set is IDENTICAL to
    the input's and ``grade_col`` still holds its RAW value. That is
    load-bearing for the Students path: the Students ``field_map`` converts that
    same raw column with ``grade_to_ceds`` to produce the output ``Grade``, and
    ``grade_to_ceds`` is not idempotent — an in-place rewrite here would ship
    every Kindergarten student as ``Grade = "UG"``. The function takes no
    ``keep=`` argument precisely so it CANNOT be asked to rewrite in place.

    Returns a COPY, so the caller owns it and the caller's frame is untouched.

    Fail-loud, both ways (this is an EXCLUSION guarantee — keeping everyone
    because a column moved would deliver students the district is not licensed
    to send):

    - ``grade_col`` absent ⇒ ``KeyError`` naming the column and the available
      ones (the ``apply_row_filters`` message shape), matching
      :func:`split_by_homeroom_grades`'s documented ``KeyError`` contract;
    - the temporary column name already present ⇒ ``ValueError`` rather than a
      silent overwrite-and-drop of a real source column.
    """
    if grade_col not in df.columns:
        raise KeyError(
            f"[{caller}] grade column '{grade_col}' not found in source columns, so the "
            f"student_rostering_grades scope cannot be applied. Available: {sorted(df.columns)}"
        )
    if _SCOPE_CEDS_COLUMN in df.columns:
        raise ValueError(
            f"[{caller}] the source frame already carries the reserved column "
            f"'{_SCOPE_CEDS_COLUMN}' — rename it; the grade-scope filter needs the name for its "
            f"own derived value and would otherwise drop yours."
        )
    working = df.copy()
    working[_SCOPE_CEDS_COLUMN] = working[grade_col].apply(grade_to_ceds)
    kept = working[working[_SCOPE_CEDS_COLUMN].isin(scope)]
    return kept.drop(columns=_SCOPE_CEDS_COLUMN)  # type: ignore[return-value]
