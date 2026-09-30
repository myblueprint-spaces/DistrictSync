"""The ETL's typed failure taxonomy — one bounded category per fault, decided by TYPE.

Plan 0053 S1 (``docs/developer/failure-policy.md`` §6, policy P6). Every failure that
crosses the transform/pipeline boundary is meant to be an :class:`EtlError` carrying a
closed-set :class:`RunErrorCategory`; :func:`classify_error_category` turns ANY exception
into that category by ``isinstance`` alone and never reads the message. The message is
for the diagnostic log; the run store carries only the category (the privacy split —
see ``pipeline.build_run_record``).

**Stdlib only, deliberately.** No pandas, no pipeline, no transformer import: this module
sits at the bottom of the dependency graph (``errors ← transformers ← pipeline ← ui``) so
a transformer can raise a typed error without importing the orchestrator — which is why
``RunErrorCategory`` moved here out of ``pipeline.py``. Nothing re-exports it from there.

**PII floor (§8).** A :class:`SourceSchemaError` message names the missing columns in the
CONFIG's spelling and the COUNT of columns the file did carry — never the observed header
list. A headerless file read without its ``headers:`` block makes row 1 a pupil, so an
observed header can be a name, a birth date or a student number.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import ClassVar


class RunErrorCategory(StrEnum):
    """The bounded, PII-free failure taxonomy stamped on every run record.

    The store carries ONLY this category — never the free-text ``str(e)`` (which can leak
    a path, a column or a cell); the diagnostic log keeps the rich message for ops.

    A ``StrEnum``, not ``(str, Enum)``: on Python 3.13 ``str()`` of a ``(str, Enum)``
    member is ``"RunErrorCategory.NONE"``, and the store stringifies what it is handed.
    The eight original VALUES are persisted in every ``history.db`` since v3.5.0 and must
    never change; new members are additive (P12).
    """

    NONE = "none"  # a completed run (delivery/anomaly/data-warning axes live in their own fields)
    NO_INPUT = "no_input"  # no usable input (input folder missing, or every required file missing/empty)
    NO_OUTPUT = "no_output"  # input loaded, but every entity was empty/skipped — nothing to write or deliver
    INCOMPLETE_ROSTER = "incomplete_roster"  # the roster anchor produced nothing while dependent entities did
    CONFIG = "config"  # a config/validation problem surfaced as the failure
    DATA = "data"  # a build/write problem not otherwise typed (an untyped ValueError)
    OUTPUT = "output"  # the OUTPUT FOLDER is unreachable / unwritable (before or during the write)
    UNKNOWN = "unknown"  # an unclassified failure
    SOURCE_SCHEMA = "source_schema"  # a source file lacks a column that guards WHO ships or a join (§5 (a)/(b))
    INPUT_UNREADABLE = "input_unreadable"  # a source file exists but could not be read/parsed/chosen
    # A file an enabled entity's mapping lists is missing from the input folder, or present with no
    # data rows (owner 2026-09-28 — "we don't have optional files"; `IncompleteInputError`). NO_INPUT
    # stays the narrower "every required file" answer, checked first.
    INCOMPLETE_INPUT = "incomplete_input"
    # An output the night cannot go without — a CRITICAL entity (outcomes.ENTITY_CRITICALITY) —
    # came out with no rows (owner 2026-09-28: "never send a header-only file";
    # `EmptyRequiredOutputError`). NO_OUTPUT stays "nothing at all was built".
    EMPTY_REQUIRED_OUTPUT = "empty_required_output"


class GuardKind(StrEnum):
    """What a missing source column GUARDS — the §5 matrix classes that fail CLOSED.

    Values match the ``# failure-policy: <class>`` site tags (§5), so one spelling serves
    the tag, the error and the doc row.
    """

    PII_SCOPE = "pii_scope"  # (a) WHO may be delivered: row_filters, the grade scope
    JOIN_KEY = "join_key"  # (b) an identity or join: merge keys, home school, derived-date source


class EtlError(Exception):
    """Base of every typed ETL failure: an exception that KNOWS its bounded category.

    ``default_category`` is the class's answer; an instance may be given a narrower one
    at the raise site (the delivery-integrity gate knows which of its two faults fired).
    A legacy ``str`` category is coerced through ``RunErrorCategory(value)`` so a raise
    site still passing an unknown string fails LOUDLY at construction rather than
    persisting a value no reader knows.
    """

    default_category: ClassVar[RunErrorCategory] = RunErrorCategory.UNKNOWN

    def __init__(self, message: str, *, category: RunErrorCategory | str | None = None) -> None:
        super().__init__(message)
        self.category: RunErrorCategory = (
            type(self).default_category if category is None else RunErrorCategory(category)
        )


class SourceSchemaError(EtlError, ValueError):
    """A source file lacks a column the transform cannot run safely without (§5 (a)/(b)).

    ``entity``, ``columns`` and ``guard`` are REQUIRED keyword-only: the error is the
    record of WHICH entity, WHICH configured columns and WHAT they guard, and a default
    for any of them would let a raise site say less than it knows. ``columns`` hold the
    CONFIG's spelling (what the admin wrote), never an observed header; an empty tuple is
    refused — an error that names no column is not a schema error. Site #4
    (``grades.filter_to_grade_scope``) names the grade column through
    ``columns.source_column_label`` (plan 0053 S9) — the config's spelling, or the
    default ``grade`` when the mapping names none.

    Also a ``ValueError`` so every existing ``except ValueError`` / ``pytest.raises(
    ValueError)`` keeps working; classification never relies on that base (the
    :class:`EtlError` branch of :func:`classify_error_category` answers first).
    """

    default_category = RunErrorCategory.SOURCE_SCHEMA

    def __init__(
        self,
        message: str,
        *,
        entity: str,
        columns: Sequence[str],
        guard: GuardKind,
    ) -> None:
        missing = tuple(str(column) for column in columns)
        if not missing:
            raise ValueError("SourceSchemaError needs at least one missing column to name")
        if not str(entity).strip():
            raise ValueError("SourceSchemaError needs the entity whose source lacks the column")
        super().__init__(message)
        self.entity: str = entity
        self.columns: tuple[str, ...] = missing
        self.guard: GuardKind = GuardKind(guard)


class NoUsableInputError(EtlError, RuntimeError):
    """Every required source file was missing or empty — the run received nothing usable.

    A ``RuntimeError`` for back-compat with every ``except``/``pytest.raises(RuntimeError,
    match="No usable required input")`` written before the type existed; its message is
    unchanged. It is the TYPE, not that text, that now yields ``no_input``.
    """

    default_category = RunErrorCategory.NO_INPUT


class IncompleteInputError(EtlError, RuntimeError):
    """A file an enabled entity's mapping lists is missing, or present with no data rows.

    Owner decision 2026-09-28 (``docs/developer/failure-policy.md`` §2 layer 3a, §7): "we don't
    have optional files". Raised by ``pipeline.check_required_inputs`` — the ONE input gate
    both entry points call after the read and before the transform — so the night stops with
    nothing written or sent and SpacesEDU keeps the last good sync. Its exceptions are declared
    there, not here: an ISOLATABLE entity's own file (Family) only leaves that entity out, and a
    ``MAY_BE_EMPTY`` entity's file (StudentAttendance) may be present with no rows.

    ``missing`` / ``empty`` are the problem files in the CONFIG's spelling (the mapping's
    ``source_files`` names — config vocabulary, never a path); ``named`` is what the admin-facing
    copy may print: the files every stopped entity's outcome named unambiguously, decided by the
    gate from the SAME recorded outcomes a run record carries, so Convert's card and Home name
    the same files (``failure_copy.failure_names``). All three are REQUIRED keyword-only, and an
    error naming no problem file is refused. A ``RuntimeError`` like :class:`NoUsableInputError`,
    its all-files sibling, which the gate is checked after.
    """

    default_category = RunErrorCategory.INCOMPLETE_INPUT

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str],
        empty: Sequence[str],
        named: Sequence[str],
    ) -> None:
        missing_files = tuple(str(name) for name in missing)
        empty_files = tuple(str(name) for name in empty)
        if not missing_files and not empty_files:
            raise ValueError("IncompleteInputError needs at least one missing or empty file to name")
        super().__init__(message)
        self.missing: tuple[str, ...] = missing_files
        self.empty: tuple[str, ...] = empty_files
        self.named: tuple[str, ...] = tuple(str(name) for name in named)


class EmptyRequiredOutputError(EtlError, RuntimeError):
    """An output the night cannot go without came out with no rows — stop rather than send it.

    Owner decision 2026-09-28 (``docs/developer/failure-policy.md`` §2 layer 4, §7): DistrictSync
    never sends a header-only file, and never sends a set with a required file missing from it.
    Raised by ``pipeline.run_transform`` — the ONE entity loop both entry points share — the
    moment an entity that ``outcomes.stops_when_empty`` says may not be empty records an EMPTY
    outcome: every later entity is recorded NOT_RUN and nothing is written or sent, so
    SpacesEDU keeps the last good sync. The exceptions are declared in ``outcomes``, not here:
    an ISOLATABLE entity (Family) is only left out, and a ``MAY_BE_EMPTY`` entity
    (StudentAttendance) with nothing to send is a normal night.

    ``entity`` — the registry key of the entity that came out empty — is REQUIRED keyword-only:
    it is what Convert's card and the record's copy NAME, through the authored entity phrase
    (``failure_copy.entity_phrase``), never a value. A blank one is refused. A
    ``RuntimeError`` like :class:`NoUsableInputError`, its way-in counterpart.

    ``required_values_left_out`` (plan 0053 S13d) — REQUIRED keyword-only, a real ``bool`` — says
    whether the entity's rows were left out because each lacked a value SpacesEDU requires (its
    outcome carries a required-value note: ``required_fields.left_out_for_required_values``). The
    export HAD rows then, so the card must not read "none came out of your export": Convert's card
    words it from this attribute exactly as Home and Run History word the record the same attempt
    writes, from that note (``failure_copy``). A default would let a raise site quietly pick the
    wrong sentence, so there is none.
    """

    default_category = RunErrorCategory.EMPTY_REQUIRED_OUTPUT

    def __init__(self, message: str, *, entity: str, required_values_left_out: bool) -> None:
        if not isinstance(entity, str) or not entity.strip():
            raise ValueError("EmptyRequiredOutputError needs the entity that came out empty")
        if not isinstance(required_values_left_out, bool):
            raise TypeError("EmptyRequiredOutputError's required_values_left_out must be a bool")
        super().__init__(message)
        self.entity: str = entity
        self.required_values_left_out: bool = required_values_left_out


class ConfigLoadError(EtlError, ValueError):
    """A district mapping could not be loaded or validated.

    Raised by the Convert path (plan 0053 S5: ``convert_job`` wraps its config load in it, so
    the screen copy and the record both say ``config`` by type). The pipeline's own config block
    deliberately keeps recording ``config`` through ``_record_early_failure`` and exiting
    1, and the self-service creator keeps receiving the loader's ORIGINAL exception types
    (``humanize_config_error`` reads them).
    """

    default_category = RunErrorCategory.CONFIG


class OutputFolderUnsetError(EtlError, ValueError):
    """A conversion was started with no output folder configured — a gate/programming error.

    Convert's run gate (``can_run_convert``) blocks this path, so it is never a normal flow
    and fails loud (D10). Typed (plan 0053 S3) so Convert's ``on_error`` card words it as the
    ``output`` category — "check the output folder in Settings" — rather than the ``data``
    copy an untyped ``ValueError`` classifies to. A ``ValueError`` for every existing caller,
    and never recorded (plan 0053 S5 lists it among the deliberate non-records).
    """

    default_category = RunErrorCategory.OUTPUT


def available_columns_note(count: int) -> str:
    """The ONE phrasing of "what the file did carry" in a missing-column message.

    A COUNT only — the observed header names never reach a message or a log line (§8):
    for a headerless file read without its ``headers:`` block they are row 1, a pupil.
    """
    noun = "column" if count == 1 else "columns"
    return f"the source has {count} {noun}; observed header names are not logged"


def classify_error_category(exc: BaseException) -> RunErrorCategory:
    """Map a failure to its bounded category — by TYPE only, never by message text.

    An :class:`EtlError` answers for itself. The remaining branches are the untyped
    fallbacks kept from before the taxonomy: ``FileNotFoundError`` is a config problem
    (an absent mapping or ``_base``), any other ``ValueError`` is ``data``, and anything
    else is ``unknown``. Order matters only for the first branch, which must precede the
    ``ValueError`` one because :class:`SourceSchemaError` IS a ``ValueError``.
    """
    if isinstance(exc, EtlError):
        return exc.category
    if isinstance(exc, FileNotFoundError):
        return RunErrorCategory.CONFIG
    if isinstance(exc, ValueError):
        return RunErrorCategory.DATA
    return RunErrorCategory.UNKNOWN
