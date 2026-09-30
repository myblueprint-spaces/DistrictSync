"""The values SpacesEDU's import REQUIRES in each rostering file — the ONE table (plan 0053 S13d).

Owner ruling 2026-09-28 ("leave out + count + amber"), restated 2026-09-30 as "this generalises
Family's no-email rule": a row missing a value the SpacesEDU *Advanced CSV* spec REQUIRES is left
out of its file, COUNTED, and the run is shown amber — never shipped blank for the importer to
reject, never dropped in silence (``docs/developer/failure-policy.md`` §5 (c) ``contract_field``,
catalogue #42; policy P5 (c)).

**Source.** "SIS/LMS Sync (AdvancedCSV) for SpacesEDU - 1.0" (Google Doc
``1BePvuk5rg-YjUUvdwjb3X3Z0JWEUc5AtVjDfR3nub0U``, the ``published_reference`` of
``docs/developer/output-contract.md``). It covers the five SpacesEDU rostering files ONLY; the
course and attendance feeds (CourseInfo, StudentCourses, StudentAttendance) follow the
myBlueprint+ docs and are deliberately out of this table — a lookup for them raises. Spelled in
DistrictSync's OUTPUT column names, which are uniform across every bundled config (pinned:
every bundled config's active rostering entities produce every column listed here).

**Stdlib only**, like :mod:`src.etl.outcomes` (which it imports, never the reverse), so the
transformers (``transformers.required_values`` — the rule; ``transformers.notes`` — the identity
note's reconciliation), ``pipeline.run_transform`` (the EMPTY reason), the flet-free copy module
(``ui_flet.failure_copy`` — the stopped night's wording) and the doc-parity tests all read the
SAME table. ``output-contract.md`` marks each rostering column Required / Optional from it
(``tests/test_output_contract_doc.py``, both directions).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from src.etl.outcomes import Note, OutcomeNote

#: Per rostering output, the columns whose value SpacesEDU's import requires, in the file's own
#: column order. Every other column of these five files is OPTIONAL (it may ship blank).
REQUIRED_OUTPUT_FIELDS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "Students": (
            "User ID",
            "Student Number",
            "First Name",
            "Last Name",
            "Grade",
            "EnrollStatus",
            "SchoolCode",
            "Email Address",
        ),
        "Staff": ("User ID", "First Name", "Last Name", "Email", "Role", "School ID"),
        "Family": ("First Name", "Last Name", "Email", "Student User ID"),
        "Classes": ("Class ID", "Name", "School ID"),
        "Enrollments": ("Class ID", "User ID", "Role", "School ID"),
    }
)

#: The email column of each output that carries one — the column whose absence from the frame is
#: ``OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED`` rather than the generic
#: ``OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED`` (§5 #20/#40 vs #43).
EMAIL_OUTPUT_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {"Students": "Email Address", "Staff": "Email", "Family": "Email"}
)

#: The note each rostering output records when rows are left out for a blank required value —
#: one member per output, because the copy is note-keyed and dedupes by note (a shared member
#: would hide WHICH file lost rows). Family's blank Email is its own, older note
#: (``CONTACTS_EXCLUDED_NO_EMAIL``, recorded first by ``FamilyTransformer``); Family's member here
#: counts the contacts left out for its OTHER required values.
REQUIRED_VALUE_NOTE: Final[Mapping[str, OutcomeNote]] = MappingProxyType(
    {
        "Students": OutcomeNote.STUDENTS_EXCLUDED_REQUIRED_VALUE,
        "Staff": OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE,
        "Family": OutcomeNote.CONTACTS_EXCLUDED_REQUIRED_VALUE,
        "Classes": OutcomeNote.CLASSES_EXCLUDED_REQUIRED_VALUE,
        "Enrollments": OutcomeNote.ENROLLMENTS_EXCLUDED_REQUIRED_VALUE,
    }
)

#: Every note that says "rows were left out because a required value was blank" — the per-output
#: members above plus Family's email exclusion, which is the same rule's first instance.
REQUIRED_VALUE_NOTES: Final[frozenset[OutcomeNote]] = frozenset(
    {*REQUIRED_VALUE_NOTE.values(), OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL}
)


def required_fields(entity: str) -> tuple[str, ...]:
    """The required output columns of rostering ``entity`` — raises for any other entity.

    A lookup for a course or attendance feed (or an unknown key) is a caller bug, not "nothing
    required": those feeds are outside the spec this table mirrors, and a silent ``()`` would
    make the rule a no-op wherever it was wired by mistake.
    """
    try:
        return REQUIRED_OUTPUT_FIELDS[entity]
    except KeyError:
        raise ValueError(
            f"{entity!r} is not a SpacesEDU rostering output; the required-value rule covers only "
            f"{sorted(REQUIRED_OUTPUT_FIELDS)}"
        ) from None


def is_required_field(entity: object, field: object) -> bool:
    """Whether ``field`` is a REQUIRED output column of ``entity`` — TOTAL (anything else is False)."""
    return isinstance(entity, str) and field in REQUIRED_OUTPUT_FIELDS.get(entity, ())


def left_out_for_required_values(notes: Iterable[Note]) -> bool:
    """Whether ``notes`` (an outcome's ``(note, count)`` pairs) record rows LEFT OUT for a blank
    required value — the ONE predicate behind ``OutcomeReason.REQUIRED_VALUES_MISSING`` (read by
    ``pipeline.run_transform``) and the stopped night's wording (``failure_copy``)."""
    return any(note in REQUIRED_VALUE_NOTES for note, _count in notes)
