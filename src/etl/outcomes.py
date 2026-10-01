"""Per-entity outcomes and declared criticality — the run's answer to "did each entity build?".

Plan 0053 S2 (``docs/developer/failure-policy.md`` §3, §6, §7; policies P3, P7, P8, P12).
Every configured entity gets EXACTLY ONE :class:`EntityOutcome` per run, recorded into an
:class:`OutcomeLedger` by ``pipeline.run_transform`` (BUILT / EMPTY at its existing
branches, FAILED + the rest NOT_RUN on a raise) or, for a raise before the entity loop,
by the entry point's failure sink (:meth:`OutcomeLedger.finalize_aborted`). The outcomes
reach the run record as ONE additive JSON key, :data:`OUTCOMES_RECORD_KEY`, written by
``pipeline.build_run_record`` and read back by the TOTAL :func:`outcomes_from_record`.

**Declared here, enforced in ONE place.** :data:`ENTITY_CRITICALITY` is pinned to the §3
table, and since plan 0053 S4 the entity bulkhead in ``pipeline.run_transform`` — the only
code that branches on it — keeps an ISOLATABLE entity's failure at entity scope (recorded
FAILED, the rest of the run continues) while a CRITICAL one still fails the whole run.

**Stdlib only, deliberately** — like :mod:`src.etl.errors`, which it imports and which
never imports it (``errors ← outcomes ← {transformers, pipeline} ← ui``). That direction
is why :func:`reason_for` lives here rather than beside the taxonomy.

**PII floor.** An outcome carries an entity NAME, two closed-set codes, a row COUNT and —
since plan 0053 S6 — ``missing_mapped``: mapped column names in the CONFIG's spelling, taken
from the resolved config's own ``field_map`` / ``row_filters`` by the source observation
(``preflight.missing_columns_by_entity``), so their membership in the config's vocabulary
holds by construction, and refused unless printable. Since plan 0053 S7 (owner decision D4)
it may also carry ``labels`` / ``file_label`` — the config-DECLARED column and file names the
admin-facing copy is allowed to print. Those pass :func:`safe_label`, the ONE membership +
shape check (a member of the RESOLVED config's own vocabulary, printable, at most
:data:`MAX_LABEL_LENGTH` characters), and are produced in exactly one place,
:func:`apply_labels`. Never an OBSERVED header, a path, a cell value or ``str(exc)`` (§8).

**Notes (plan 0053 S10, owner ruling 2026-09-25 — the carrier S11 extended).** An outcome
may also carry ``notes``: closed :class:`OutcomeNote` codes, each with a COUNT, that a
transformer recorded through ``TransformContext.record_outcome_note`` while it ran — a fact
about a BUILT (or EMPTY) entity that its kind and reason cannot say, such as "built, but the
co-teacher rows were left out". A code and a count only: never a column name, a value or a
path. Whether a note makes the run PARTIAL is decided in ONE place, ``failure_copy.NOTE_TIER``.

**Only Family may be left out (owner, 2026-09-28).** "We don't have optional files; maybe family
info can be optional." :data:`ENTITY_CRITICALITY` keeps exactly one ISOLATABLE entity, and the
SAME table decides two stops beyond a CRITICAL raise:

* the way IN, ``pipeline.check_required_inputs`` (before the transform): a file a CRITICAL
  entity lists must be present, and must have data rows unless that entity is in
  :data:`MAY_BE_EMPTY` — except a listing the ONE optional-input predicate,
  :func:`source_file_may_be_absent`, lets go without (Family's files, and — owner ruling
  2026-09-30 — the Class Information export of a config whose blended detection is off); a
  stopped entity is recorded FAILED /
  :attr:`OutcomeReason.MISSING_SOURCE_FILE` (:meth:`OutcomeLedger.record_missing_files`),
  naming its file when that is unambiguous;
* the way OUT of each entity, ``pipeline.run_transform``: a CRITICAL entity that comes out
  EMPTY stops the night (:func:`stops_when_empty`) unless the EMPTY is a normal night
  (:func:`empty_is_expected` — StudentAttendance with nothing to send). DistrictSync never
  sends a header-only file.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from src.etl.column_names import SCHOOL_NUMBER_LABEL, SCHOOL_YEAR_LABEL
from src.etl.errors import SourceSchemaError


class EntityCriticality(StrEnum):
    """Whether one entity's failure may be contained to that entity (§3, P3)."""

    CRITICAL = "critical"  # its failure fails the whole run
    ISOLATABLE = "isolatable"  # its failure leaves it out of the run; the rest continues (plan 0053 S4)


ROSTER_ANCHOR_ENTITY: Final = "Students"
"""The entity every other delivered file's student references resolve against.

``Students.csv`` is the referential ROOT of a SpacesEDU delivery: the zero-orphan
invariant (CLAUDE.md → Key Data Flow → Enrollments) is defined as "no emitted row
references a ``User ID`` absent from ``Students.csv``", and ``Family`` /
``StudentCourses`` carry the same dependency (see ``quality/report.py``'s orphan
checks). That made it the ONE entity whose absence invalidated the whole payload, which
``pipeline.check_delivery_integrity`` enforces on the way OUT (``incomplete_roster``). Since
2026-09-28 (owner) every CRITICAL entity is held to that bar: a missing or row-less file one
of them lists stops the night before anything is built (``pipeline.check_required_inputs``),
and one that comes out EMPTY stops it in ``pipeline.run_transform`` (:func:`stops_when_empty`)
— so an empty Students stops there first, and the out-gate's roster check stays the floor
beneath it. Only Family may be skipped (:data:`ENTITY_CRITICALITY`) and only
StudentAttendance may arrive with no rows (:data:`MAY_BE_EMPTY`). Named here — beside the
criticality it forces — rather than inlined, so the special case is explicit and
single-sourced; ``pipeline.check_delivery_integrity`` imports it.
"""

# The §3 table, in code. One row per registry entity; `tests/test_failure_policy_parity.py`
# ties it to `docs/developer/failure-policy.md` §3 row for row, and `tests/test_etl_outcomes.py`
# to `TRANSFORMER_REGISTRY` and the run record's flat count keys. D1 (owner, 2026-09-23),
# REVISED 2026-09-28 (owner): "we don't have optional files; maybe family info can be optional …
# supporting optional is adding complexity" — Family is the ONE isolatable entity. The same table
# decides which missing input only leaves its entity out (`pipeline.check_required_inputs`).
ENTITY_CRITICALITY: Final[Mapping[str, EntityCriticality]] = MappingProxyType(
    {
        # The roster anchor; publishes `context.active_student_ids`; a user missing from a delivered
        # Students.csv is marked Inactive (faq "What happens to students or staff no longer in the file?").
        ROSTER_ANCHOR_ENTITY: EntityCriticality.CRITICAL,
        # A set generated without Staff.csv is incomplete and overwrites what SpacesEDU holds, so it
        # is never sent (output-contract Q5a — answered by the owner 2026-09-28). Publishes
        # `context.left_out_staff_ids` (plan 0053 S13d), which Enrollments reads.
        "Staff": EntityCriticality.CRITICAL,
        # The ONE optional feed (owner 2026-09-28): absence already ships (SD51 builds no Family.csv);
        # publishes no context state; nothing reads it (D1). A missing or row-less contacts file
        # leaves Family out (EMPTY, amber) instead of stopping the night.
        "Family": EntityCriticality.ISOLATABLE,
        # Publishes `context.class_artifacts`, which Enrollments requires, and (plan 0053 S13d)
        # `context.left_out_class_ids`, which Enrollments reads.
        "Classes": EntityCriticality.CRITICAL,
        # A missing enrollment may remove a user from the class (faq "What happens to enrollments no longer
        # in the file?"); a set without Enrollments.csv is never sent (Q5a — answered by the owner 2026-09-28).
        "Enrollments": EntityCriticality.CRITICAL,
        # CRITICAL since 2026-09-28 (owner — D1 revised; was ISOLATABLE by D1 (c) 2026-09-23): a
        # failure stops the night and SpacesEDU keeps the last good sync. A standalone feed.
        "CourseInfo": EntityCriticality.CRITICAL,
        # CRITICAL since 2026-09-28 (owner — D1 revised); reads the CourseInformation SOURCE, never CourseInfo.
        "StudentCourses": EntityCriticality.CRITICAL,
        # CRITICAL since 2026-09-28 (owner — D1 revised): an unmapped absence code or a missing
        # authorized column stops the night. A PRESENT absence file with no rows is still a normal
        # night (`MAY_BE_EMPTY`); a MISSING one stops it (`pipeline.check_required_inputs`).
        "StudentAttendance": EntityCriticality.CRITICAL,
    }
)

# CODE dependencies only — an entity that READS another entity's published `TransformContext`
# state (§3 `depends_on`). Whether two files must ARRIVE together was a delivery question (Q5d —
# answered by the owner 2026-09-28: it depends on the mapping, and every file a mapping lists is
# required), not a dependency: StudentCourses falls back when CourseInfo's data is absent. Every
# entity named in a value must be CRITICAL (pinned), so a FAILED isolatable entity can never have
# a dependent — the structural fact S4 relies on instead of a withholding branch. The FK chain the
# owner named on 2026-09-28 (Family→Students, Classes→Students, Enrollments→Classes+Students,
# StudentCourses→Students) is these rows, plus Enrollments→Staff since plan 0053 S13d: Enrollments
# leaves out the teacher rows of a staff member Staff left out for a missing required value (the
# no-orphan cascade), so it reads Staff's published `left_out_staff_ids` — and every bundled config
# runs Staff before Enrollments (pinned in `tests/test_required_values.py`).
DEPENDS_ON: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        # class_artifacts + left_out_class_ids · active_student_ids · left_out_staff_ids
        "Enrollments": frozenset({"Classes", ROSTER_ANCHOR_ENTITY, "Staff"}),
        "Classes": frozenset({ROSTER_ANCHOR_ENTITY}),  # homeroom classes → filter_to_active
        "Family": frozenset({ROSTER_ANCHOR_ENTITY}),  # filter_to_active over active_student_ids
        "StudentCourses": frozenset({ROSTER_ANCHOR_ENTITY}),  # filter_to_active over active_student_ids
    }
)


# The entities whose export may legitimately be EMPTY on a given night, so a skip for that reason
# alone ("nothing to send") is not a warning — owner decision D5 (2026-09-24), plan 0053 S8,
# `docs/developer/failure-policy.md` §7 (the `may-be-empty` table, pinned). Read in two places:
#   * by the input gate, `pipeline.check_required_inputs` (owner 2026-09-28): a member's listed
#     file may be PRESENT with no data rows; a MISSING one still stops the night — "no absences
#     today" is a file with no rows, never an absent file;
#   * through `empty_is_expected` — the ONE "normal night" rule — by `failure_copy.OUTCOME_TIER`
#     and by `pipeline.run_transform` (`stops_when_empty`): for a member only `source_files_empty`
#     / `no_source_files_declared` are a normal night; every row filtered out, or a mapped column
#     missing, is not — and for a CRITICAL member that stops the night.
# RESTRICTIVE BY DEFAULT: an entity not listed is never quietly empty (a CRITICAL one stops the
# night, the ISOLATABLE one warns), so a new entity can never go quiet by omission. Adding one is a DECISIONS entry naming why its absence is a normal
# night (the remedy for a district that warns is a config change, never widening this set). Every
# member must be a registry entity (pinned in `tests/test_standing_empty_warning.py::TestMayBeEmpty`).
MAY_BE_EMPTY: Final[frozenset[str]] = frozenset(
    {
        # A night with no absences is a normal night: its absence files are present with no rows
        # (owner 2026-09-28). A MISSING absence file is not "no absences" — it stops the night.
        "StudentAttendance",
    }
)


def criticality_of(entity: str) -> EntityCriticality:
    """The declared criticality of ``entity`` — CRITICAL for anything not in the table.

    Restrictive by default (P3): an entity a hand-dropped YAML invents, or one added to the
    registry without a §3 row, can never be isolated by omission.
    """
    return ENTITY_CRITICALITY.get(entity, EntityCriticality.CRITICAL)


#: The ``source_files`` ROLE of MyEd BC's Class Information (Enhanced) export. Classes' blended-class
#: detection reads it as its working frame, Enrollments' co-teacher rows read it through the class
#: artifacts Classes publishes, and Staff's teacher-of-record rescue reads it as evidence of teaching.
#: Named once, here, because the ONE optional-input predicate below keys on it.
CLASS_INFORMATION_ROLE: Final = "class_info"


def blended_detection_off(global_config: Mapping[str, object]) -> bool:
    """Whether ``global_config`` switches blended-class detection OFF — the ONE reading of
    ``global_config.blended_classes``.

    The resolved bool, never truthiness: a missing key is ON (every district but the opt-outs), and
    only an explicit ``False`` is off, so a ``None`` or a typo'd value can never flip it. Read by
    Classes (which then skips detection) and by :func:`source_file_may_be_absent` (which then lets
    the Class Information export be missing or row-less) — so "detection is off" and "the file is
    optional" can never disagree.
    """
    return global_config.get("blended_classes", True) is False


def source_file_may_be_absent(entity: str, role: str, *, global_config: Mapping[str, object]) -> bool:
    """Whether the file ``entity`` lists under ``role`` may be MISSING from the input folder, or
    PRESENT with no data rows, without stopping the night — the ONE optional-input predicate.

    Every file a CRITICAL entity lists is required (owner 2026-09-28: "we don't have optional
    files"; ``pipeline.check_required_inputs``). The exceptions are DERIVED from the config's own
    declarations, never from a per-district list:

    * every file an ISOLATABLE entity lists — Family's contacts export (owner 2026-09-28): missing
      or empty, Family is only left out, amber;
    * the Class Information export (:data:`CLASS_INFORMATION_ROLE`) of a config whose blended-class
      detection is OFF (:func:`blended_detection_off` — owner ruling 2026-09-30: "Optional if
      blended off: ClassInformation becomes optional for any config with blended detection off").
      Detection then never reads it, so only Enrollments' co-teacher rows depend on it, and they are
      left out with the standing ``coteacher_source_unusable`` warning instead of the night stopping.
      With detection ON the file is the blended working frame and stays required (§5 #39 strict).

    Judged per LISTING (entity, role): a file another listing requires stays required through it — a
    file Family shares with a CRITICAL entity, or a Class Information file some other role names
    too. ``global_config`` is REQUIRED keyword-only: a default would decide the Class Information
    exception for the caller. :data:`MAY_BE_EMPTY` is a different, narrower rule — row-less allowed,
    missing NOT — and is not answered here.
    """
    if criticality_of(entity) is EntityCriticality.ISOLATABLE:
        return True
    return role == CLASS_INFORMATION_ROLE and blended_detection_off(global_config)


class OutcomeKind(StrEnum):
    """What happened to one configured entity on one run (persisted values — never change)."""

    BUILT = "built"  # the transform produced rows; they are in this run's outputs
    EMPTY = "empty"  # the entity was skipped with nothing to build (a reason says why)
    FAILED = "failed"  # the entity's own transform raised
    NOT_RUN = "not_run"  # the run stopped before this entity was attempted


class OutcomeReason(StrEnum):
    """Why an entity has the outcome it has (persisted values — never change; additive only)."""

    NONE = "none"  # BUILT carries no reason
    NO_SOURCE_FILES_DECLARED = "no_source_files_declared"  # the mapping declares no source file for it
    SOURCE_FILES_EMPTY = "source_files_empty"  # every source file it reads was missing or empty
    NO_ROWS_AFTER_TRANSFORM = "no_rows_after_transform"  # it had input, and its transform kept no row
    # EMPTY only (plan 0053 S13d, owner 2026-09-28): it had input, and every row that reached its
    # output was LEFT OUT for a blank value the SpacesEDU Advanced CSV import requires
    # (`required_fields.REQUIRED_OUTPUT_FIELDS`) — "no rows came out" would read as "the export had
    # none". Decided by `pipeline.run_transform` from the entity's own required-value notes
    # (`required_fields.left_out_for_required_values`).
    REQUIRED_VALUES_MISSING = "required_values_missing"
    MISSING_SOURCE_COLUMN = "missing_source_column"  # a SourceSchemaError: a guarding column is absent
    # A file the entity lists was missing from the input folder, or present with no data rows,
    # and the entity may not be left out (owner 2026-09-28; `pipeline.check_required_inputs`,
    # recorded through `OutcomeLedger.record_missing_files` — FAILED only: its transform never ran).
    MISSING_SOURCE_FILE = "missing_source_file"
    TRANSFORM_ERROR = "transform_error"  # any other raise from its transform
    RUN_ABORTED = "run_aborted"  # an earlier failure stopped the run before it


VALID_REASONS: Final[Mapping[OutcomeKind, frozenset[OutcomeReason]]] = MappingProxyType(
    {
        OutcomeKind.BUILT: frozenset({OutcomeReason.NONE}),
        OutcomeKind.EMPTY: frozenset(
            {
                OutcomeReason.NO_SOURCE_FILES_DECLARED,
                OutcomeReason.SOURCE_FILES_EMPTY,
                OutcomeReason.NO_ROWS_AFTER_TRANSFORM,
                # Plan 0053 S13d: every row left out for a blank required value.
                OutcomeReason.REQUIRED_VALUES_MISSING,
                # Plan 0053 S6: NO_ROWS_AFTER_TRANSFORM (or, since S13d, REQUIRED_VALUES_MISSING)
                # refined by the source observation — the entity kept no row AND a mapped column is
                # absent from the file(s) it reads.
                OutcomeReason.MISSING_SOURCE_COLUMN,
            }
        ),
        OutcomeKind.FAILED: frozenset(
            {OutcomeReason.MISSING_SOURCE_COLUMN, OutcomeReason.TRANSFORM_ERROR, OutcomeReason.MISSING_SOURCE_FILE}
        ),
        OutcomeKind.NOT_RUN: frozenset({OutcomeReason.RUN_ABORTED}),
    }
)


#: The EMPTY reasons that mean "there was nothing to send" — no file declared, or every file the
#: entity reads empty — as opposed to "the export had rows and none survived"
#: (``no_rows_after_transform`` / ``required_values_missing`` / ``missing_source_column``). The ONE spelling, read by
#: :func:`empty_is_expected` (and so by ``failure_copy.OUTCOME_TIER`` and ``pipeline.run_transform``).
NOTHING_TO_SEND: Final[frozenset[OutcomeReason]] = frozenset(
    {OutcomeReason.SOURCE_FILES_EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED}
)


def empty_is_expected(entity: object, reason: OutcomeReason) -> bool:
    """Whether an EMPTY outcome of ``entity`` for ``reason`` is a NORMAL night — the ONE rule.

    True only for a :data:`MAY_BE_EMPTY` entity with nothing to send (:data:`NOTHING_TO_SEND`):
    StudentAttendance on a night without absences. Every other EMPTY outcome is not normal —
    for an ISOLATABLE entity it is a standing warning (``failure_copy.OUTCOME_TIER``), for a
    CRITICAL one it stops the night (:func:`stops_when_empty`). TOTAL: an unknown or non-``str``
    entity is simply not a member.
    """
    return reason in NOTHING_TO_SEND and isinstance(entity, str) and entity in MAY_BE_EMPTY


def stops_when_empty(entity: str, reason: OutcomeReason) -> bool:
    """Whether an EMPTY outcome of ``entity`` for ``reason`` STOPS the night (owner 2026-09-28).

    "DistrictSync never sends a header-only file": an entity the night cannot go without
    (:func:`criticality_of` — CRITICAL, which is anything unlisted) that comes out with no rows
    stops the run — unless that EMPTY is a normal night (:func:`empty_is_expected`). Only
    Family, the ONE ISOLATABLE entity, may be EMPTY in a completed run and still ship the rest
    (a standing WARNING). ``pipeline.run_transform`` is the one caller that acts on it.
    """
    return criticality_of(entity) is EntityCriticality.CRITICAL and not empty_is_expected(entity, reason)


def every_entity_may_be_empty(entities: Iterable[str]) -> bool:
    """Whether a run configured for ``entities`` may legitimately arrive with NO data rows at all.

    True only when there is at least one entity and every one is in :data:`MAY_BE_EMPTY` — an
    attendance-only config (``sd51attendance``), whose night without absences is a folder of
    present, row-less absence files (owner ruling 2026-09-30). The input's "nothing usable"
    guard (``pipeline.has_no_usable_input``) reads it, together with "no listed file is
    missing": a MISSING absence file still stops the night, and a config with any other entity
    (a roster) whose files are all row-less still fails ``no_input``.
    """
    names = list(entities)
    return bool(names) and all(name in MAY_BE_EMPTY for name in names)


def nothing_to_send(outcomes: Iterable[EntityOutcome]) -> bool:
    """Whether a completed run's outcomes say the night had NOTHING TO SEND — the ONE rule.

    True only when there is at least one outcome and EVERY one is EMPTY for a reason
    :func:`empty_is_expected` calls a normal night (an attendance-only config on a night without
    absences — owner ruling 2026-09-30). Such a run is a SUCCESS: nothing is written, archived or
    delivered, the last output is left as it was, and Home stays green with a detail saying so.
    Read by ``pipeline.check_delivery_integrity`` (which then does not refuse the empty set as
    ``no_output``), by both entry points (which skip the write) and by the surfaces' copy.
    """
    items = list(outcomes)
    return bool(items) and all(
        outcome.kind is OutcomeKind.EMPTY and empty_is_expected(outcome.entity, outcome.reason) for outcome in items
    )


class OutcomeNote(StrEnum):
    """A closed fact a transformer records about an entity it DID build (persisted values — never
    change; additive only). Plan 0053 S10 introduced the carrier with the one member owner ruling
    2026-09-25 needed; S11 adds the rest of the §5 catalogue: every fail-open (d)/(e) posture — and
    the (b)/(c) sites whose direction stays open — records one of these, never silence.

    Every member carries a COUNT of at least one, and each comment below says what it counts
    (``failure-policy.md`` §6). One note per entity per run: the first count recorded stands
    (``TransformContext.record_outcome_note``). Whether a note makes the run PARTIAL is decided in
    ONE place, ``failure_copy.NOTE_TIER`` — most members below are Run-History detail only
    (HEALTHY); ``ALL_ACTIVE_DEFAULT``, ``COTEACHER_SOURCE_UNUSABLE`` and — since plan 0053 S13d —
    every row left out for a missing required value (the five ``*_EXCLUDED_REQUIRED_VALUE`` members
    and ``CONTACTS_EXCLUDED_NO_EMAIL``) are WARNING.
    """

    # --- Students: which signal decided "active" (§5 #17/#18). At most ONE of the first four per
    # run: ALL_ACTIVE_DEFAULT > CONFIGURED_STATUS_COLUMN_ABSENT > STATUS_COLUMN_ABSENT_DATE_ONLY
    # (`BaseTransformer.decide_enroll_status`). Each counts the demographic rows the step decided.
    # Neither a status nor a withdraw-date column: every student shipped Active (H1).
    ALL_ACTIVE_DEFAULT = "all_active_default"
    # The EnrollStatus config names a status column the export does not carry; the withdraw date
    # decided instead.
    CONFIGURED_STATUS_COLUMN_ABSENT = "configured_status_column_absent"
    # No enrollment-status column (none configured, neither default spelling present); the
    # withdraw date alone decided.
    STATUS_COLUMN_ABSENT_DATE_ONLY = "status_column_absent_date_only"
    # Rows kept Active with NO positive signal: a blank status (or no status column) AND a blank
    # (or absent) withdraw date. Counts those rows. Not recorded beside ALL_ACTIVE_DEFAULT, whose
    # count already covers every row.
    ACTIVE_WITHOUT_POSITIVE_SIGNAL = "active_without_positive_signal"

    # --- Staff: the departed-staff filter could not run (§5 #12/#21) or the roster merge was
    # skipped (§5 #13). Each counts the staff rows shipped as they were.
    STAFF_STATUS_COLUMN_ABSENT = "staff_status_column_absent"
    STAFF_STATUS_VOCABULARY_UNRECOGNISED = "staff_status_vocabulary_unrecognised"
    STAFF_FILTER_WOULD_EMPTY = "staff_filter_would_empty"
    ROSTER_MERGE_SKIPPED = "roster_merge_skipped"

    # --- The zero-orphan roster filter was skipped (§5 #14, #27(i)), on the entity that asked for
    # it. Each counts the rows of the first filter reached, kept unfiltered (on Enrollments, the first
    # of its homeroom / subject filters — the second adds nothing: the first count stands).
    # The source lacks the student column the filter matches on (the roster exists).
    ACTIVE_ROSTER_COLUMN_UNRESOLVABLE = "active_roster_column_unresolvable"
    # No roster was published this run (Students not enabled, not run first, or no `User ID`).
    ACTIVE_ROSTER_UNAVAILABLE = "active_roster_unavailable"

    # Enrollments: the co-teacher rows could not all be linked, so they were left out and the rest
    # was built (§5 #15). Two causes. (i) A present, non-empty ClassInformation lacked a column the
    # co-teacher rows are linked by (primary-teacher flag, its teacher id, school, Path 1's section
    # column, Path 2's Master Timetable ID): the count is the ClassInformation rows AFFECTED — the
    # whole file when an entry column is missing, the primary-teacher rows when a path column is
    # (with one path missing, the other may still have linked some of those same rows, so it is
    # never a count of rows "not used"). (ii) Since plan 0053 S13e (owner ruling 2026-09-30) the
    # ClassInformation file the Classes mapping lists was MISSING or had NO rows — reachable only
    # where `source_file_may_be_absent` lets the night go without it (blended detection off): there
    # is no row to count, so the count is 1, the one file.
    COTEACHER_SOURCE_UNUSABLE = "coteacher_source_unusable"

    # --- Classes: display-name and lookup columns (d). Blended detection read a lookup without a
    # column it needs — no blend could be detected, or blend names lost a segment (§5 #16/#31);
    # counts the ClassInformation rows detection read.
    BLENDED_LOOKUP_COLUMN_ABSENT = "blended_lookup_column_absent"
    # The homeroom teacher-name column is absent (§5 #35); counts the homeroom classes named
    # without it.
    HOMEROOM_TEACHER_NAME_ABSENT = "homeroom_teacher_name_absent"
    # A subject class-name column (course title, teacher name, section) is absent (§5 #37); counts
    # the subject classes named without that segment.
    CLASS_NAME_COLUMN_ABSENT = "class_name_column_absent"

    # Classes / Enrollments / CourseInfo / StudentCourses: exclusions are configured but the source
    # has no course-code column, so they were not applied (§5 #32); counts the rows of the first
    # source found without one.
    COURSE_CODE_EXCLUSIONS_NOT_APPLIED = "course_code_exclusions_not_applied"

    # --- Contract fields (c). Family contacts left out for a blank Email (§5 #20); counts them.
    # WARNING tier since plan 0053 S13d (the owner's rule: left out + counted + amber).
    CONTACTS_EXCLUDED_NO_EMAIL = "contacts_excluded_no_email"
    # Family / Students / Staff: the mapping produces no email output column at all (§5 #20, #40);
    # counts the rows shipped without it.
    EMAIL_OUTPUT_NOT_MAPPED = "email_output_not_mapped"
    # --- Required values (c), plan 0053 S13d (owner 2026-09-28: "leave out + count + amber"). ONE
    # member per rostering output, because the copy is note-keyed and dedupes by note — a shared
    # member would hide WHICH file lost rows. Each counts the rows of that output LEFT OUT for a
    # blank value the SpacesEDU Advanced CSV import requires (`required_fields.REQUIRED_OUTPUT_FIELDS`,
    # §5 #42); WARNING tier. Family's blank Email stays CONTACTS_EXCLUDED_NO_EMAIL (recorded first);
    # its member below counts the contacts left out for its OTHER required values.
    STUDENTS_EXCLUDED_REQUIRED_VALUE = "students_excluded_required_value"
    STAFF_EXCLUDED_REQUIRED_VALUE = "staff_excluded_required_value"
    CONTACTS_EXCLUDED_REQUIRED_VALUE = "contacts_excluded_required_value"
    CLASSES_EXCLUDED_REQUIRED_VALUE = "classes_excluded_required_value"
    ENROLLMENTS_EXCLUDED_REQUIRED_VALUE = "enrollments_excluded_required_value"
    # The mapping produces no output column for a REQUIRED value other than an email (the email is
    # EMAIL_OUTPUT_NOT_MAPPED) — a mapping fault, never a per-row one, so the rows ship without it
    # (§5 #43); counts them. Unreachable on every bundled config (pinned).
    REQUIRED_OUTPUT_NOT_MAPPED = "required_output_not_mapped"

    # --- Identity / key reads whose direction is still open (b)/(c). An identity field
    # (`User ID`, `Student User ID`, `Class ID`, `School ID`) mapped to a source column the
    # frame lacks ships blank (§5 #19); counts the rows. Since plan 0053 S13d never recorded for a
    # field its output REQUIRES (a rostering file's ID): those rows are left out instead (#42).
    IDENTITY_FIELD_BLANKED = "identity_field_blanked"
    # StudentAttendance: a band's configured column is absent (§5 #33); counts that band's rows.
    ATTENDANCE_SOURCE_COLUMN_ABSENT = "attendance_source_column_absent"
    # StudentCourses: a transcript source lacks a column it reads (§5 #34/#34a); counts the rows of
    # the sources concerned.
    TRANSCRIPT_SOURCE_COLUMN_ABSENT = "transcript_source_column_absent"


#: The outcome kinds that may carry notes: an entity whose transform RAN TO COMPLETION. A FAILED
#: entity's file is left out whole (its reason says why) and a NOT_RUN one never ran, so a note
#: about what a finished transform left out can only describe a BUILT or EMPTY outcome.
NOTE_BEARING_KINDS: Final[frozenset[OutcomeKind]] = frozenset({OutcomeKind.BUILT, OutcomeKind.EMPTY})

OUTCOMES_RECORD_KEY: Final = "entity_outcomes"
"""The run-record key carrying the per-entity outcomes (additive JSON beside ``run_as``)."""

MISSING_MAPPED_KEY: Final = "missing_mapped"
"""The per-entity entry key carrying :attr:`EntityOutcome.missing_mapped` (plan 0053 S6, additive)."""

LABELS_KEY: Final = "labels"
"""The per-entity entry key carrying :attr:`EntityOutcome.labels` (plan 0053 S7, additive)."""

FILE_LABEL_KEY: Final = "file_label"
"""The per-entity entry key carrying :attr:`EntityOutcome.file_label` (plan 0053 S7, additive)."""

NOTES_KEY: Final = "notes"
"""The per-entity entry key carrying :attr:`EntityOutcome.notes` as ``{note: count}`` (plan 0053 S10,
additive)."""

MAX_LABEL_LENGTH: Final = 120
"""The longest config-declared label a record or a sentence may carry (plan 0053 S7, D4)."""

#: Per entity, the column labels one of its fail-closed guards names from CODE — a
#: ``column_names`` constant no mapping key renames — rather than from the config (owner
#: 2026-09-28: a stopped night names "School Year", "a config/structural label, never observed
#: text"). ``preflight.label_vocabulary_by_entity`` adds them to that entity's label vocabulary,
#: so :func:`apply_labels` records them like any declared column, and ``failure_copy.failure_names``
#: names ONLY these when a missing column stops the whole night — every other stop keeps its
#: category copy. Today two guards, both on Classes: §5 #41's school-year source column
#: ("School Year", owner 2026-09-28) and §5 #39's blended-detection school ("School Number",
#: owner ruling 2026-09-30 — a linking column blended matching needs).
STRUCTURAL_LABELS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "Classes": frozenset({SCHOOL_YEAR_LABEL, SCHOOL_NUMBER_LABEL}),
    }
)


def structural_labels(entity: object, labels: Iterable[object]) -> tuple[str, ...]:
    """The members of ``labels`` that are :data:`STRUCTURAL_LABELS` of ``entity``, first-seen order.

    TOTAL: an unknown entity, a non-``str`` or a label that fails the shape check reads as
    nothing. Membership is exact ``str`` equality against the code's own constant.
    """
    allowed = STRUCTURAL_LABELS.get(entity, frozenset()) if isinstance(entity, str) else frozenset()
    kept: list[str] = []
    for label in labels:
        if isinstance(label, str) and label in allowed and _label_shaped(label) and label not in kept:
            kept.append(label)
    return tuple(kept)


#: One note on one outcome: the closed code and how many rows it concerns (at least one).
Note = tuple[OutcomeNote, int]


@dataclass(frozen=True)
class EntityOutcome:
    """One configured entity's outcome on one run. Illegal states are refused, never defaulted.

    ``rows`` is the number of rows the entity's TRANSFORM produced — positive for BUILT, zero
    for everything else. It is deliberately a separate fact from the run record's flat
    per-entity count keys (see ``pipeline.build_run_record``).

    ``missing_mapped`` (plan 0053 S6) is what the SOURCE OBSERVATION saw before the transform
    ran: the entity's mapped columns absent from the file(s) it reads, in CONFIG spelling —
    ``()`` when nothing was missing or no sound claim could be made. It is an observation about
    the INPUT, so any kind may carry it (a BUILT entity with a blank column; a FAILED one). It
    changes a reason in exactly one place, :func:`apply_observation`. Each name must be a
    non-blank, trimmed, printable ``str``, listed once.

    ``labels`` / ``file_label`` (plan 0053 S7, D4) are what the admin-facing copy may NAME: the
    config-declared columns (and, only when it is unambiguous, the one export file) behind a
    ``missing_source_column`` outcome — or, since 2026-09-28, the one file behind a
    ``missing_source_file`` outcome (a file label alone, never a column). Produced only by
    :func:`apply_labels`, which passes each through :func:`safe_label` against the resolved
    config's own vocabulary; the constructor re-checks their SHAPE (it cannot know the config)
    and refuses the states that could never come from there: column labels on any reason but
    ``missing_source_column``, a file label without a column label on that reason, and a file
    label on any reason but those two.

    ``notes`` (plan 0053 S10) are ``(OutcomeNote, count)`` pairs the entity's transform recorded
    (``TransformContext.record_outcome_note``), in the order recorded: each note once, each count
    an ``int`` of at least 1, and only on a :data:`NOTE_BEARING_KINDS` outcome (the transform ran
    to completion). Everything else is refused.
    """

    entity: str
    kind: OutcomeKind
    reason: OutcomeReason
    rows: int
    missing_mapped: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    file_label: str = ""
    notes: tuple[Note, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entity, str) or not self.entity.strip():
            raise ValueError("an outcome needs the entity it describes")
        if not isinstance(self.kind, OutcomeKind):
            raise TypeError(f"kind must be an OutcomeKind, not {type(self.kind).__name__}")
        if not isinstance(self.reason, OutcomeReason):
            raise TypeError(f"reason must be an OutcomeReason, not {type(self.reason).__name__}")
        # `bool` is an `int`; a True/False row count is a bug at the call site, not a count.
        if isinstance(self.rows, bool) or not isinstance(self.rows, int):
            raise TypeError(f"rows must be an int, not {type(self.rows).__name__}")
        if self.rows < 0:
            raise ValueError(f"{self.entity}: rows cannot be negative ({self.rows})")
        if self.reason not in VALID_REASONS[self.kind]:
            raise ValueError(f"{self.entity}: reason {self.reason.value!r} is not valid for {self.kind.value!r}")
        if self.kind is OutcomeKind.BUILT and self.rows <= 0:
            raise ValueError(f"{self.entity}: a BUILT outcome has at least one row")
        if self.kind is not OutcomeKind.BUILT and self.rows != 0:
            raise ValueError(f"{self.entity}: a {self.kind.value!r} outcome has no rows ({self.rows})")
        _check_missing_mapped(self.entity, self.missing_mapped)
        _check_labels(self.entity, self.reason, self.labels, self.file_label)
        _check_notes(self.entity, self.kind, self.notes)

    @classmethod
    def built(cls, entity: str, rows: int, *, notes: tuple[Note, ...] = ()) -> EntityOutcome:
        return cls(entity, OutcomeKind.BUILT, OutcomeReason.NONE, rows, notes=notes)

    @classmethod
    def empty(cls, entity: str, reason: OutcomeReason, *, notes: tuple[Note, ...] = ()) -> EntityOutcome:
        return cls(entity, OutcomeKind.EMPTY, reason, 0, notes=notes)

    @classmethod
    def failed(cls, entity: str, reason: OutcomeReason) -> EntityOutcome:
        return cls(entity, OutcomeKind.FAILED, reason, 0)

    @classmethod
    def not_run(cls, entity: str) -> EntityOutcome:
        return cls(entity, OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED, 0)


def _usable_column_name(value: object) -> bool:
    """The ONE shape test for a column/file name an outcome may carry: a non-blank, trimmed,
    printable ``str`` (``isprintable`` refuses a newline, a tab and every other control)."""
    return isinstance(value, str) and bool(value) and value == value.strip() and value.isprintable()


# A drive-letter path (`C:\...`, `C:/...`) or a URL scheme. With `@` (an email address or a
# `user@host`), a backslash, and a leading `/` or `~`, these are the shapes a label may never
# have, whatever the config declares: they would carry a person or a machine's folder layout
# into the record and the copy. A mid-name `/` stays legal ("Parent Auth / Guardian").
_PATH_OR_URL = re.compile(r"^[A-Za-z]:[\\/]|://")


def _email_or_path_shaped(value: str) -> bool:
    return "@" in value or "\\" in value or value.startswith(("/", "~")) or bool(_PATH_OR_URL.search(value))


def _label_shaped(value: object) -> bool:
    """A label's shape — the ONE definition, shared by :func:`safe_label`, the constructor and
    the record reader: :func:`_usable_column_name`, at most :data:`MAX_LABEL_LENGTH` characters,
    and neither email- nor path-shaped."""
    return (
        isinstance(value, str)
        and _usable_column_name(value)
        and len(value) <= MAX_LABEL_LENGTH
        and not _email_or_path_shaped(value)
    )


def _file_label_shaped(value: object) -> bool:
    """A FILE label's shape — :func:`_label_shaped` AND a bare filename: no ``/`` (in a file
    name it is always a directory separator, unlike a column's "Parent Auth / Guardian"), no
    ``:`` (a drive-relative ``C:x.txt`` or an NTFS stream — the rule
    ``authoring._require_bare_filename`` applies to creator-written overlays) and never ``.`` or
    ``..``. The ONE file-label test, shared by :func:`derive_labels`, the constructor and the
    record reader, because ``source_files`` itself is unvalidated and a hand-dropped YAML may
    declare a relative path that would carry a folder layout into the record and the copy."""
    return (
        isinstance(value, str)
        and _label_shaped(value)
        and "/" not in value
        and ":" not in value
        and value not in {".", ".."}
    )


def safe_label(text: object, *, vocabulary: Collection[str]) -> str | None:
    """``text`` when the copy may print it as a config-declared label, else ``None`` (plan 0053 S7).

    D4 (owner, 2026-09-23): a file or column may be NAMED only when it is config-DECLARED —
    a member of ``vocabulary``, the RESOLVED config's own spelling of that entity's source
    files or mapped columns (never lowercased, never an observed header) — AND it is a
    non-blank, trimmed, printable ``str`` of at most :data:`MAX_LABEL_LENGTH` characters (no
    newline) that is neither email- nor path-shaped (no ``@``, no backslash, no leading ``/``
    or ``~``, no drive letter, no URL scheme — even a config-declared one). A FILE label must
    further be a bare filename (:func:`_file_label_shaped`, applied by :func:`derive_labels`).
    Anything else is dropped, never repaired: a label that fails is simply not named.

    Membership is EXACT ``str`` equality. A bare ``str`` vocabulary is refused (``TypeError``) —
    ``in`` would test a SUBSTRING and let a fragment of a declared name through.
    """
    if isinstance(vocabulary, str):
        raise TypeError("vocabulary must be a collection of labels, not a str")
    if not isinstance(text, str) or not _label_shaped(text):
        return None
    return text if text in vocabulary else None


@dataclass(frozen=True)
class LabelVocabulary:
    """One entity's config-declared label vocabulary (plan 0053 S7) — built by
    ``preflight.label_vocabulary_by_entity`` from the RESOLVED config.

    * ``columns`` — the entity's ``field_map`` + ``row_filters`` columns in config spelling
      (never its ``source_columns``: those are cross-file reads, so naming the entity's own
      file beside one would point at the wrong export) — except StudentAttendance, whose
      field_map holds placeholders: its columns are its configured ``global_config.attendance``
      band columns (``preflight._attendance_band_columns``, plan 0053 S13b — owner ruling
      2026-09-26) — plus the entity's :data:`STRUCTURAL_LABELS` (owner 2026-09-28);
    * ``files`` — the entity's configured ``source_files`` names, config spelling;
    * ``reads_own_files`` — whether every mapped column is read from the entity's OWN files
      (``preflight.OBSERVATION_SCOPE`` is ``OWN_FILES``, or the entity is StudentAttendance,
      which reads its band columns from its own files — S13b). Only then can a file be named.
    """

    columns: frozenset[str] = frozenset()
    files: tuple[str, ...] = ()
    reads_own_files: bool = False


def derive_labels(columns: Iterable[object], vocabulary: LabelVocabulary) -> tuple[str, tuple[str, ...]]:
    """``(file_label, column labels)`` the copy may print for ``columns``.

    Each column passes :func:`safe_label` against ``vocabulary.columns`` (duplicates dropped,
    order kept). **The single-file rule:** the file is named ONLY when at least one column
    survived, the entity reads its mapped columns from its own files, it has EXACTLY ONE
    configured source file, and that name passes :func:`safe_label` against the entity's
    files AND is a bare filename (:func:`_file_label_shaped`). So an entity with several
    files (Classes: five) never names one, and a column the vocabulary does not know never
    drags a file name in beside it.
    """
    kept: list[str] = []
    for column in columns:
        label = safe_label(column, vocabulary=vocabulary.columns)
        if label is not None and label not in kept:
            kept.append(label)
    if not kept:
        return "", ()
    file_label = ""
    if vocabulary.reads_own_files and len(vocabulary.files) == 1:
        candidate = safe_label(vocabulary.files[0], vocabulary=vocabulary.files)
        if candidate is not None and _file_label_shaped(candidate):
            file_label = candidate
    return file_label, tuple(kept)


def derive_file_label(files: Iterable[object], vocabulary: LabelVocabulary) -> str:
    """The file a ``missing_source_file`` outcome may name, or ``""`` (owner 2026-09-28).

    Unlike a missing COLUMN — which :func:`derive_labels` can pin on a file only when the
    entity has exactly one — a missing FILE is known exactly. So the rule here is simpler: the
    file is named when EXACTLY ONE distinct problem file was given (an entity with two problem
    files names neither, rather than one of them), it is one of the entity's configured
    ``source_files`` (:func:`safe_label` against ``vocabulary.files``) and it is a bare filename
    (:func:`_file_label_shaped` — ``source_files`` itself is unvalidated).
    """
    distinct = list(dict.fromkeys(name for name in files if isinstance(name, str)))
    if len(distinct) != 1:
        return ""
    candidate = safe_label(distinct[0], vocabulary=vocabulary.files)
    return candidate if candidate is not None and _file_label_shaped(candidate) else ""


def _check_labels(entity: str, reason: OutcomeReason, labels: object, file_label: object) -> None:
    """Refuse labels that are not distinct label-shaped names, or sit where no producer puts them."""
    if not isinstance(labels, tuple):
        raise TypeError(f"{entity}: labels must be a tuple, not {type(labels).__name__}")
    if not all(_label_shaped(label) for label in labels):
        raise ValueError(f"{entity}: labels must be non-blank, trimmed, printable and at most {MAX_LABEL_LENGTH} long")
    if len(set(labels)) != len(labels):
        raise ValueError(f"{entity}: labels lists a name more than once")
    if not isinstance(file_label, str):
        raise TypeError(f"{entity}: file_label must be a str, not {type(file_label).__name__}")
    if file_label and not _file_label_shaped(file_label):
        raise ValueError(
            f"{entity}: file_label must be a bare filename — trimmed, printable, at most {MAX_LABEL_LENGTH} long"
        )
    if reason is OutcomeReason.MISSING_SOURCE_FILE:
        # The missing FILE is the whole fact (owner 2026-09-28): a file label alone, never a column.
        if labels:
            raise ValueError(f"{entity}: a missing_source_file outcome names its file, never a column")
        return
    if file_label and not labels:
        raise ValueError(f"{entity}: a file is named only beside the column it is missing")
    if labels and reason is not OutcomeReason.MISSING_SOURCE_COLUMN:
        raise ValueError(f"{entity}: only a missing_source_column outcome names a column")


def _check_vocabulary(entity: str, vocabulary: LabelVocabulary) -> None:
    """Refuse a vocabulary whose FIELDS are not the declared types (plan 0053 S7, S7-BEH-1).

    :class:`LabelVocabulary` does not validate itself, and labelling runs inside
    :meth:`OutcomeLedger.record` — on the delivery path, where a raise would fail the run (or,
    in ``record_failure``, replace the entity's own exception). Checking here, at note time,
    lets ``observe_source_columns``' guard turn a malformed vocabulary into "no labels".
    """
    columns: Any = vocabulary.columns
    files: Any = vocabulary.files
    if not isinstance(columns, frozenset) or not all(isinstance(column, str) for column in columns):
        raise TypeError(f"{entity}: vocabulary columns must be a frozenset of str")
    if not isinstance(files, tuple) or not all(isinstance(name, str) for name in files):
        raise TypeError(f"{entity}: vocabulary files must be a tuple of str")
    if not isinstance(vocabulary.reads_own_files, bool):
        raise TypeError(f"{entity}: vocabulary reads_own_files must be a bool")


def _check_missing_mapped(entity: str, columns: object) -> None:
    """Refuse a ``missing_mapped`` that is not a tuple of distinct, usable column names."""
    if not isinstance(columns, tuple):
        raise TypeError(f"{entity}: missing_mapped must be a tuple, not {type(columns).__name__}")
    if not all(_usable_column_name(column) for column in columns):
        raise ValueError(f"{entity}: missing_mapped names must be non-blank, trimmed and printable")
    if len(set(columns)) != len(columns):
        raise ValueError(f"{entity}: missing_mapped lists a column more than once")


def _note_shaped(note: object) -> bool:
    """One ``(OutcomeNote, count)`` pair: a member of the enum and an ``int`` count of at least 1
    (``bool`` is an ``int``, and a True/False count is a caller bug, not a count)."""
    if not isinstance(note, tuple) or len(note) != 2:
        return False
    code, count = note
    return isinstance(code, OutcomeNote) and isinstance(count, int) and not isinstance(count, bool) and count >= 1


def _check_notes(entity: str, kind: OutcomeKind, notes: object) -> None:
    """Refuse ``notes`` that are not distinct ``(OutcomeNote, count ≥ 1)`` pairs on a note-bearing kind."""
    if not isinstance(notes, tuple):
        raise TypeError(f"{entity}: notes must be a tuple, not {type(notes).__name__}")
    if not all(_note_shaped(note) for note in notes):
        raise ValueError(f"{entity}: each note must be an (OutcomeNote, count >= 1) pair")
    codes = [code for code, _count in notes]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{entity}: notes lists a note more than once")
    if notes and kind not in NOTE_BEARING_KINDS:
        raise ValueError(f"{entity}: a {kind.value!r} outcome carries no notes — its transform did not complete")


#: The EMPTY reasons "the export had rows and none survived" that the source observation may
#: explain with a missing mapped column (:func:`apply_observation`).
_REFINED_BY_OBSERVATION: Final[frozenset[OutcomeReason]] = frozenset(
    {OutcomeReason.NO_ROWS_AFTER_TRANSFORM, OutcomeReason.REQUIRED_VALUES_MISSING}
)


def apply_observation(outcome: EntityOutcome, missing_mapped: tuple[str, ...]) -> EntityOutcome:
    """``outcome`` with the source observation attached — the ONE reason refinement (plan 0053 S6).

    * nothing observed missing, or the outcome already carries an observation → ``outcome``
      itself, unchanged;
    * otherwise ``missing_mapped`` is attached, and an EMPTY / NO_ROWS_AFTER_TRANSFORM outcome
      becomes EMPTY / MISSING_SOURCE_COLUMN — "it kept no row" is then explained by "a column it
      maps is not in its file" (SD51's Family: the contacts export carries no email column, so
      every contact is excluded for a blank email). Since plan 0053 S13d an EMPTY /
      REQUIRED_VALUES_MISSING outcome is refined the same way: a required value blank on EVERY
      row is, in practice, a column the export lost, and the column is what the admin can
      re-export (its name reaches the copy through :func:`apply_labels`); the entity's
      required-value note, which rides on the outcome, still says the rows were left out.

    Every other kind and reason is kept as it is: a FAILED outcome's reason came from the
    exception's TYPE (:func:`reason_for`) and a BUILT one built — the observation never changes
    what happened, only what the record can say about why.
    """
    if not missing_mapped or outcome.missing_mapped:
        return outcome
    reason = outcome.reason
    if outcome.kind is OutcomeKind.EMPTY and reason in _REFINED_BY_OBSERVATION:
        reason = OutcomeReason.MISSING_SOURCE_COLUMN
    return replace(outcome, reason=reason, missing_mapped=missing_mapped)


def apply_labels(
    outcome: EntityOutcome,
    vocabulary: LabelVocabulary | None,
    *,
    error_columns: Sequence[str],
    error_files: Sequence[str] = (),
) -> EntityOutcome:
    """``outcome`` with its config-declared labels attached — the ONE label producer (plan 0053 S7).

    Only a ``missing_source_column`` or ``missing_source_file`` outcome is labelled, and only
    from a config-declared source:

    * FAILED / ``missing_source_column`` → ``error_columns``, the raising
      :class:`~src.etl.errors.SourceSchemaError`'s own ``columns`` (config spelling, or a
      :data:`STRUCTURAL_LABELS` constant) — never ``str(exc)``;
    * EMPTY / ``missing_source_column`` → the outcome's ``missing_mapped`` (the source
      observation's config-spelling names);
    * FAILED / ``missing_source_file`` (owner 2026-09-28) → ``error_files``, the input gate's
      problem files for this entity, through :func:`derive_file_label` (a file label alone).
      ``()`` — the default, for every other caller — names nothing.

    Columns go through :func:`derive_labels` (``safe_label`` + the single-file rule). No
    vocabulary, an outcome already labelled, any other reason, or nothing surviving → the
    outcome unchanged.
    """
    if vocabulary is None or outcome.labels or outcome.file_label:
        return outcome
    if outcome.reason is OutcomeReason.MISSING_SOURCE_FILE:
        named_file = derive_file_label(error_files, vocabulary)
        return replace(outcome, file_label=named_file) if named_file else outcome
    if outcome.reason is not OutcomeReason.MISSING_SOURCE_COLUMN:
        return outcome
    source = error_columns if outcome.kind is OutcomeKind.FAILED else outcome.missing_mapped
    file_label, labels = derive_labels(source, vocabulary)
    if not labels:
        return outcome
    return replace(outcome, labels=labels, file_label=file_label)


def reason_for(exc: BaseException) -> OutcomeReason:
    """The FAILED reason for an entity whose transform raised ``exc`` — by TYPE only.

    A :class:`~src.etl.errors.SourceSchemaError` is a missing guarding column; anything else
    is a transform error. Never reads the message (P6).
    """
    if isinstance(exc, SourceSchemaError):
        return OutcomeReason.MISSING_SOURCE_COLUMN
    return OutcomeReason.TRANSFORM_ERROR


class OutcomeLedger:
    """Collects exactly one outcome per configured entity for one run (P7).

    Built by each entry point at the SAME point — once the config's raw dicts exist and the
    output folder has passed its pre-flight, immediately before the input is read — so a
    failure anywhere after that yields a COMPLETE ledger through :meth:`finalize_aborted`,
    never ``{}``. ``configured`` is ``pipeline.configured_entity_order(...)``: exactly the
    list ``run_transform`` iterates.
    """

    def __init__(self, configured: Sequence[str]) -> None:
        names = tuple(configured)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"an entity may be configured once per run; listed more than once: {duplicates}")
        self._configured: tuple[str, ...] = names
        self._outcomes: dict[str, EntityOutcome] = {}
        self._missing_mapped: dict[str, tuple[str, ...]] = {}
        self._vocabularies: dict[str, LabelVocabulary] = {}

    @property
    def configured(self) -> tuple[str, ...]:
        return self._configured

    def note_missing_mapped(self, entity: str, columns: Sequence[str]) -> None:
        """Hold the source observation for ``entity`` until its outcome is recorded (plan 0053 S6).

        Called by ``pipeline.observe_source_columns`` after the input is read and BEFORE the
        transform, so :meth:`record` can attach it through :func:`apply_observation`. Refuses an
        entity not configured, one already recorded (an observation cannot rewrite an outcome
        after the fact), a second observation, and an unusable column list — each a caller bug,
        which that caller's own guard turns into a DEBUG line, never a changed run.
        """
        if entity not in self._configured:
            raise ValueError(f"{entity!r} is not an entity this run is configured to produce")
        if entity in self._outcomes:
            raise ValueError(f"{entity!r} already has an outcome; observe before the transform")
        if entity in self._missing_mapped:
            raise ValueError(f"{entity!r} was already observed for this run")
        if isinstance(columns, str):
            # A bare str is a Sequence[str] to the type checker, so `tuple()` would split it into
            # characters and a name with no blank or repeated letter would pass every check below.
            raise TypeError(f"{entity}: missing_mapped must be a sequence of column names, not a str")
        observed = tuple(columns)
        _check_missing_mapped(entity, observed)
        if observed:
            self._missing_mapped[entity] = observed

    def note_label_vocabulary(self, entity: str, vocabulary: LabelVocabulary) -> None:
        """Hold ``entity``'s config-declared label vocabulary until its outcome is recorded (plan 0053 S7).

        Called by ``pipeline.observe_source_columns`` before the transform, beside the source
        observation. Refuses an entity not configured, one already recorded, a second
        vocabulary and a non-:class:`LabelVocabulary` — each a caller bug, which that caller's
        guard turns into a DEBUG line (no labels), never a changed run. Without a vocabulary an
        outcome simply carries no labels.
        """
        if entity not in self._configured:
            raise ValueError(f"{entity!r} is not an entity this run is configured to produce")
        if entity in self._outcomes:
            raise ValueError(f"{entity!r} already has an outcome; note its vocabulary before the transform")
        if entity in self._vocabularies:
            raise ValueError(f"{entity!r} already has a label vocabulary for this run")
        if not isinstance(vocabulary, LabelVocabulary):
            raise TypeError(f"{entity}: vocabulary must be a LabelVocabulary, not {type(vocabulary).__name__}")
        _check_vocabulary(entity, vocabulary)
        self._vocabularies[entity] = vocabulary

    def record(self, outcome: EntityOutcome) -> None:
        """Record ``outcome``; refuses an entity not configured, or one already recorded.

        A held source observation (:meth:`note_missing_mapped`) is attached on the way in —
        :func:`apply_observation`, the one refinement — and then its config-declared labels
        (:func:`apply_labels`, from ``missing_mapped`` for an EMPTY outcome).
        """
        self._record(outcome, error_columns=())

    def record_failure(self, entity: str, exc: BaseException) -> OutcomeReason:
        """Record ``entity`` FAILED for ``exc`` and return the reason — the bulkhead's one call.

        The reason is :func:`reason_for` (by TYPE). Labels come from the exception's own
        config-spelling ``columns`` — only a :class:`~src.etl.errors.SourceSchemaError`
        attributed to THIS entity carries any — never from its message.
        """
        reason = reason_for(exc)
        error_columns: tuple[str, ...] = ()
        if isinstance(exc, SourceSchemaError) and exc.entity == entity:
            error_columns = exc.columns
        self._record(EntityOutcome.failed(entity, reason), error_columns=error_columns)
        return reason

    def record_missing_files(self, entity: str, files: Sequence[str]) -> None:
        """Record ``entity`` FAILED / ``missing_source_file`` — the input gate's one call.

        Owner decision 2026-09-28: ``pipeline.check_required_inputs`` stops the night when a
        file a CRITICAL entity lists is missing (or has no data rows, unless the entity is in
        :data:`MAY_BE_EMPTY`), and records every entity it stopped this way BEFORE the
        transform runs — so the record says which entities could not be fed, and the failure
        sink marks the rest NOT_RUN. ``files`` are that entity's problem files in config
        spelling (at least one; a bare ``str`` is refused, as :meth:`note_missing_mapped`
        refuses one); the file is NAMED only when there is exactly one
        (:func:`derive_file_label`, through :func:`apply_labels`).
        """
        if isinstance(files, str):
            raise TypeError(f"{entity}: files must be a sequence of filenames, not a str")
        problem = tuple(files)
        if not problem:
            raise ValueError(f"{entity}: a missing_source_file outcome needs the file(s) it could not read")
        self._record(
            EntityOutcome.failed(entity, OutcomeReason.MISSING_SOURCE_FILE), error_columns=(), error_files=problem
        )

    def _record(self, outcome: EntityOutcome, *, error_columns: Sequence[str], error_files: Sequence[str] = ()) -> None:
        if outcome.entity not in self._configured:
            raise ValueError(f"{outcome.entity!r} is not an entity this run is configured to produce")
        if outcome.entity in self._outcomes:
            raise ValueError(f"{outcome.entity!r} already has an outcome for this run")
        observed = apply_observation(outcome, self._missing_mapped.get(outcome.entity, ()))
        self._outcomes[outcome.entity] = apply_labels(
            observed, self._vocabularies.get(outcome.entity), error_columns=error_columns, error_files=error_files
        )

    def mark_not_run(self, entities: Iterable[str]) -> None:
        """Record every one of ``entities`` NOT_RUN/RUN_ABORTED (each must still be unrecorded)."""
        for entity in entities:
            self.record(EntityOutcome.not_run(entity))

    def finalize_aborted(self) -> tuple[EntityOutcome, ...]:
        """Mark every still-unrecorded configured entity NOT_RUN/RUN_ABORTED; return the outcomes.

        Called by every failure sink after the ledger exists. Idempotent: an entity that
        already has an outcome keeps it, so a sink that runs after ``run_transform`` recorded
        FAILED + NOT_RUN changes nothing.
        """
        self.mark_not_run([name for name in self._configured if name not in self._outcomes])
        return self.outcomes

    @property
    def outcomes(self) -> tuple[EntityOutcome, ...]:
        """The recorded outcomes, in configured order (possibly incomplete — see :meth:`complete`)."""
        return tuple(self._outcomes[name] for name in self._configured if name in self._outcomes)

    def complete(self) -> tuple[EntityOutcome, ...]:
        """The outcomes, refusing a ledger in which any configured entity has none."""
        missing = [name for name in self._configured if name not in self._outcomes]
        if missing:
            raise RuntimeError(f"no outcome was recorded for configured entities {missing}")
        return self.outcomes


def outcomes_to_record(outcomes: Iterable[EntityOutcome]) -> dict[str, dict[str, Any]]:
    """The JSON value stored at :data:`OUTCOMES_RECORD_KEY` — keyed by entity, in the given order.

    ``{"Family": {"kind": "failed", "reason": "missing_source_column", "rows": 0}, ...}`` —
    plain ``str``/``int``/``list`` only, so the store's ``json.dumps`` and the log line agree.
    ``"missing_mapped"`` (a list of config-spelling column names, plan 0053 S6) is written only
    when the observation found something, ``"labels"`` / ``"file_label"`` (plan 0053 S7)
    only when the outcome names something, and ``"notes"`` (plan 0053 S10, ``{note: count}``)
    only when the transform recorded one, so every other entry is byte-identical to before.
    """
    record: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        entry: dict[str, Any] = {"kind": outcome.kind.value, "reason": outcome.reason.value, "rows": outcome.rows}
        if outcome.missing_mapped:
            entry[MISSING_MAPPED_KEY] = list(outcome.missing_mapped)
        if outcome.labels:
            entry[LABELS_KEY] = list(outcome.labels)
        if outcome.file_label:
            entry[FILE_LABEL_KEY] = outcome.file_label
        if outcome.notes:
            entry[NOTES_KEY] = {code.value: count for code, count in outcome.notes}
        record[outcome.entity] = entry
    return record


def _missing_mapped_from(raw: Any) -> tuple[str, ...]:
    """A stored ``missing_mapped`` value → a usable tuple, or ``()`` for anything else (never raises)."""
    if not isinstance(raw, (list, tuple)):
        return ()
    columns = tuple(raw)
    try:
        _check_missing_mapped("stored", columns)
    except (TypeError, ValueError):
        return ()
    return columns


def _labels_from(entry: Mapping[Any, Any], reason: OutcomeReason) -> tuple[tuple[str, ...], str]:
    """Stored ``labels`` / ``file_label`` → a usable pair, or ``((), "")`` (never raises).

    The reader cannot re-check MEMBERSHIP (the config that produced the record may have
    changed since); it re-checks the SHAPE, the reason and the pairing, so a damaged or
    hand-edited record can name less — never something unprintable, over-long or multi-line —
    and never costs the entry its kind and reason. A file label that is unusable on its own
    is dropped and the column labels kept.
    """
    raw_labels: Any = entry.get(LABELS_KEY, ())
    raw_file: Any = entry.get(FILE_LABEL_KEY, "")
    if not isinstance(raw_labels, (list, tuple)):
        # Present but not a list: damaged — name nothing. (An ABSENT key is `()`, which is how a
        # `missing_source_file` entry naming only its file arrives — owner 2026-09-28.)
        return (), ""
    labels = tuple(raw_labels)
    file_label = raw_file if _file_label_shaped(raw_file) else ""
    try:
        _check_labels("stored", reason, labels, file_label)
    except (TypeError, ValueError):
        return (), ""
    return labels, file_label


def _notes_from(raw: Any, kind: OutcomeKind) -> tuple[Note, ...]:
    """A stored ``notes`` value → usable ``(OutcomeNote, count)`` pairs, or ``()`` (never raises).

    Not a mapping, or on a kind that carries no notes → ``()``. Within the mapping each pair is
    kept only when its key is a note this build knows and its count is an ``int`` of at least 1;
    anything else is dropped, never the entry's kind and reason. A note code this build does not
    know — written by a NEWER build — is DROPPED rather than read as a warning: most of the
    catalogue S11 added is Run-History detail only, so erring toward amber would light an older
    build's Home on facts that are not warnings (DECISIONS 2026-09-25).
    """
    if not isinstance(raw, Mapping) or kind not in NOTE_BEARING_KINDS:
        return ()
    kept: list[Note] = []
    for key, count in raw.items():
        try:
            code = OutcomeNote(key)
        except (TypeError, ValueError):
            continue
        note = (code, count)
        if _note_shaped(note):
            kept.append(note)
    return tuple(kept)


def _outcome_from_entry(entity: Any, entry: Any) -> EntityOutcome | None:
    """One stored entry → an outcome, or ``None`` when the entry is unusable (never raises)."""
    if not isinstance(entity, str) or not entity.strip() or not isinstance(entry, Mapping):
        return None
    raw_kind: Any = entry.get("kind")
    raw_reason: Any = entry.get("reason")
    raw_rows: Any = entry.get("rows")
    missing_mapped = _missing_mapped_from(entry.get(MISSING_MAPPED_KEY))
    try:
        kind = OutcomeKind(raw_kind)
        reason = OutcomeReason(raw_reason)
    except (TypeError, ValueError):
        # A kind or reason this build does not know — most likely written by a NEWER build.
        # Read it as a failure: an unknown code must err toward a warning, never toward green.
        return EntityOutcome(entity, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR, 0, missing_mapped)
    labels, file_label = _labels_from(entry, reason)
    notes = _notes_from(entry.get(NOTES_KEY), kind)
    try:
        return EntityOutcome(entity, kind, reason, raw_rows, missing_mapped, labels, file_label, notes)
    except (TypeError, ValueError):
        # Known codes in an impossible combination (or a corrupt row count): not evidence of anything.
        return None


def failed_entities(outcomes: Iterable[EntityOutcome]) -> tuple[EntityOutcome, ...]:
    """The outcomes whose entity FAILED — the ONE predicate behind the PARTIAL verdict (P7).

    Plan 0053 S3's reader asks this of a SUCCESSFUL run's record: a non-empty answer means
    the run completed but left at least one entity out, which Home, Run History and Convert
    show as a WARNING every run it persists. Returned in the given (configured) order, as
    whole outcomes so a caller can word each one by its reason.

    FAILED only, deliberately — this is the "not built because it RAISED" predicate the
    pipeline's own log and ``--dry-run`` lines use. It is no longer the whole PARTIAL rule:
    since plan 0053 S8 (owner decision D5) the reader's predicate is
    ``failure_copy.warning_outcomes``, which also selects an EMPTY outcome whose tier
    (``failure_copy.OUTCOME_TIER``) is WARNING. Every FAILED outcome is one of those (pinned),
    so this set is always a subset of it. ``NOT_RUN`` exists only after a raise that failed
    the whole run (a failed record already outranks PARTIAL).
    """
    return tuple(outcome for outcome in outcomes if outcome.kind is OutcomeKind.FAILED)


def stopped_file_labels(outcomes: Iterable[EntityOutcome]) -> tuple[str, ...]:
    """The files a night stopped by the input gate may NAME — the ONE reduction (owner 2026-09-28).

    Over the FAILED / ``missing_source_file`` outcomes (``pipeline.check_required_inputs``):
    their file labels, distinct, first-seen order — but ONLY when every such outcome names its
    file. One unnamed stop (an entity with two problem files, or no vocabulary) makes the answer
    ``()``: a list that silently omits a file would tell the admin the rest of the folder is
    fine. Also ``()`` when no outcome is a missing-file stop. Read by the gate itself (for the
    raised error, which Convert's card words) and by ``failure_copy`` over a stored record, so
    both surfaces name exactly the same files.
    """
    stops = [o for o in outcomes if o.kind is OutcomeKind.FAILED and o.reason is OutcomeReason.MISSING_SOURCE_FILE]
    if not stops or not all(o.file_label for o in stops):
        return ()
    return tuple(dict.fromkeys(o.file_label for o in stops))


def stopped_empty_entities(outcomes: Iterable[EntityOutcome]) -> tuple[str, ...]:
    """The entities whose EMPTY outcome stopped the night — the ONE reduction (owner 2026-09-28).

    Over the EMPTY outcomes :func:`stops_when_empty` rates as stopping: their entity keys,
    distinct, in the given (configured) order. ``pipeline.run_transform`` stops at the FIRST
    such outcome, so a record it wrote names one. Read by ``failure_copy.failure_names`` for an
    ``empty_required_output`` record, which words each key through the authored entity phrase
    — never the key itself.
    """
    return tuple(
        dict.fromkeys(
            o.entity for o in outcomes if o.kind is OutcomeKind.EMPTY and stops_when_empty(o.entity, o.reason)
        )
    )


def outcomes_from_record(record: Any) -> tuple[EntityOutcome, ...]:
    """Read a run record's outcomes back — TOTAL: never raises, whatever the record holds (P12).

    ``record`` is the whole run record (a store row or a parsed log line). No key, a
    ``None`` value (an attempt that ended before the ledger existed, or a delivery-only
    record), or any non-mapping → ``()``. Within the mapping: a blank or non-``str`` entity
    key is dropped; an unknown kind or reason reads as FAILED/TRANSFORM_ERROR; a known
    kind/reason in an illegal combination, or an unusable row count, is dropped. An absent or
    unusable ``missing_mapped`` (plan 0053 S6 — not a list, a non-``str`` or blank name, a
    duplicate) reads as ``()``; it never costs the entry its kind and reason. Likewise unusable
    ``labels`` / ``file_label`` (plan 0053 S7 — not label-shaped, a duplicate, on a reason that
    names nothing, a file without a column) read as ``()`` / ``""``; an unknown kind or reason,
    read as FAILED/``transform_error``, names nothing. Unusable ``notes`` (plan 0053 S10 — not a
    mapping, an unknown code, a count that is not an ``int`` ≥ 1, on a kind that carries none) are
    dropped pair by pair (:func:`_notes_from`).
    """
    if not isinstance(record, Mapping):
        return ()
    stored = record.get(OUTCOMES_RECORD_KEY)
    if not isinstance(stored, Mapping):
        return ()
    outcomes: list[EntityOutcome] = []
    for entity, entry in stored.items():
        outcome = _outcome_from_entry(entity, entry)
        if outcome is not None:
            outcomes.append(outcome)
    return tuple(outcomes)
