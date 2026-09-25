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
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

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
checks). That makes it the ONE entity whose absence invalidates the whole payload —
every other entity may legitimately be empty on a given night (per-entity
skip-on-empty). Named here — beside the criticality it forces — rather than inlined, so
the special case is explicit and single-sourced; ``pipeline.check_delivery_integrity``
imports it.
"""

# The §3 table, in code. One row per registry entity; `tests/test_failure_policy_parity.py`
# ties it to `docs/developer/failure-policy.md` §3 row for row, and `tests/test_etl_outcomes.py`
# to `TRANSFORMER_REGISTRY` and the run record's flat count keys. D1 (owner, 2026-09-23).
ENTITY_CRITICALITY: Final[Mapping[str, EntityCriticality]] = MappingProxyType(
    {
        # The roster anchor; publishes `context.active_student_ids`; a user missing from a delivered
        # Students.csv is marked Inactive (faq "What happens to students or staff no longer in the file?").
        ROSTER_ANCHOR_ENTITY: EntityCriticality.CRITICAL,
        # What an ABSENT Staff.csv does is unknown (output-contract Q5a); a missing user may be deactivated.
        "Staff": EntityCriticality.CRITICAL,
        # Absence already ships (SD51 builds no Family.csv); publishes no context state; nothing reads it (D1).
        "Family": EntityCriticality.ISOLATABLE,
        # Publishes `context.class_artifacts`, which Enrollments requires.
        "Classes": EntityCriticality.CRITICAL,
        # A missing enrollment may remove a user from the class (faq "What happens to enrollments no longer
        # in the file?"); what an ABSENT file does is Q5a.
        "Enrollments": EntityCriticality.CRITICAL,
        # Isolatable by owner decision D1, ahead of partner evidence (Q5c/Q5d open); a standalone feed.
        "CourseInfo": EntityCriticality.ISOLATABLE,
        # Isolatable by owner decision D1 (Q5c/Q5d open); reads the CourseInformation SOURCE, never CourseInfo.
        "StudentCourses": EntityCriticality.ISOLATABLE,
        # Absence already ships on nights without absence files; output-contract: a missing attendance
        # drop must never stop rostering (D1; Q5c open).
        "StudentAttendance": EntityCriticality.ISOLATABLE,
    }
)

# CODE dependencies only — an entity that READS another entity's published `TransformContext`
# state (§3 `depends_on`). Whether two files must ARRIVE together is a delivery question (Q5d),
# not a dependency: StudentCourses falls back when CourseInfo's data is absent. Every entity
# named in a value must be CRITICAL (pinned), so a FAILED isolatable entity can never have a
# dependent — the structural fact S4 relies on instead of a withholding branch.
DEPENDS_ON: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "Enrollments": frozenset({"Classes", ROSTER_ANCHOR_ENTITY}),  # class_artifacts + active_student_ids
        "Classes": frozenset({ROSTER_ANCHOR_ENTITY}),  # homeroom classes → filter_to_active
        "Family": frozenset({ROSTER_ANCHOR_ENTITY}),  # filter_to_active over active_student_ids
        "StudentCourses": frozenset({ROSTER_ANCHOR_ENTITY}),  # filter_to_active over active_student_ids
    }
)


def criticality_of(entity: str) -> EntityCriticality:
    """The declared criticality of ``entity`` — CRITICAL for anything not in the table.

    Restrictive by default (P3): an entity a hand-dropped YAML invents, or one added to the
    registry without a §3 row, can never be isolated by omission.
    """
    return ENTITY_CRITICALITY.get(entity, EntityCriticality.CRITICAL)


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
    MISSING_SOURCE_COLUMN = "missing_source_column"  # a SourceSchemaError: a guarding column is absent
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
                # Plan 0053 S6: NO_ROWS_AFTER_TRANSFORM refined by the source observation — the
                # entity kept no row AND a mapped column is absent from the file(s) it reads.
                OutcomeReason.MISSING_SOURCE_COLUMN,
            }
        ),
        OutcomeKind.FAILED: frozenset({OutcomeReason.MISSING_SOURCE_COLUMN, OutcomeReason.TRANSFORM_ERROR}),
        OutcomeKind.NOT_RUN: frozenset({OutcomeReason.RUN_ABORTED}),
    }
)

OUTCOMES_RECORD_KEY: Final = "entity_outcomes"
"""The run-record key carrying the per-entity outcomes (additive JSON beside ``run_as``)."""

MISSING_MAPPED_KEY: Final = "missing_mapped"
"""The per-entity entry key carrying :attr:`EntityOutcome.missing_mapped` (plan 0053 S6, additive)."""

LABELS_KEY: Final = "labels"
"""The per-entity entry key carrying :attr:`EntityOutcome.labels` (plan 0053 S7, additive)."""

FILE_LABEL_KEY: Final = "file_label"
"""The per-entity entry key carrying :attr:`EntityOutcome.file_label` (plan 0053 S7, additive)."""

MAX_LABEL_LENGTH: Final = 120
"""The longest config-declared label a record or a sentence may carry (plan 0053 S7, D4)."""


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
    ``missing_source_column`` outcome. Produced only by :func:`apply_labels`, which passes each
    through :func:`safe_label` against the resolved config's own vocabulary; the constructor
    re-checks their SHAPE (it cannot know the config) and refuses the states that could never
    come from there: labels on any other reason, a file label without a column label.
    """

    entity: str
    kind: OutcomeKind
    reason: OutcomeReason
    rows: int
    missing_mapped: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    file_label: str = ""

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

    @classmethod
    def built(cls, entity: str, rows: int) -> EntityOutcome:
        return cls(entity, OutcomeKind.BUILT, OutcomeReason.NONE, rows)

    @classmethod
    def empty(cls, entity: str, reason: OutcomeReason) -> EntityOutcome:
        return cls(entity, OutcomeKind.EMPTY, reason, 0)

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
      file beside one would point at the wrong export);
    * ``files`` — the entity's configured ``source_files`` names, config spelling;
    * ``reads_own_files`` — whether every mapped column is read from the entity's OWN files
      (``preflight.OBSERVATION_SCOPE`` is ``OWN_FILES``). Only then can a file be named.
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


def apply_observation(outcome: EntityOutcome, missing_mapped: tuple[str, ...]) -> EntityOutcome:
    """``outcome`` with the source observation attached — the ONE reason refinement (plan 0053 S6).

    * nothing observed missing, or the outcome already carries an observation → ``outcome``
      itself, unchanged;
    * otherwise ``missing_mapped`` is attached, and an EMPTY / NO_ROWS_AFTER_TRANSFORM outcome
      becomes EMPTY / MISSING_SOURCE_COLUMN — "it kept no row" is then explained by "a column it
      maps is not in its file" (SD51's Family: the contacts export carries no email column, so
      every contact is excluded for a blank email).

    Every other kind and reason is kept as it is: a FAILED outcome's reason came from the
    exception's TYPE (:func:`reason_for`) and a BUILT one built — the observation never changes
    what happened, only what the record can say about why.
    """
    if not missing_mapped or outcome.missing_mapped:
        return outcome
    reason = outcome.reason
    if outcome.kind is OutcomeKind.EMPTY and reason is OutcomeReason.NO_ROWS_AFTER_TRANSFORM:
        reason = OutcomeReason.MISSING_SOURCE_COLUMN
    return replace(outcome, reason=reason, missing_mapped=missing_mapped)


def apply_labels(
    outcome: EntityOutcome,
    vocabulary: LabelVocabulary | None,
    *,
    error_columns: Sequence[str],
) -> EntityOutcome:
    """``outcome`` with its config-declared labels attached — the ONE label producer (plan 0053 S7).

    Only a ``missing_source_column`` outcome is labelled, and only from a config-declared source:

    * FAILED → ``error_columns``, the raising :class:`~src.etl.errors.SourceSchemaError`'s own
      ``columns`` (config spelling) — never ``str(exc)``;
    * EMPTY → the outcome's ``missing_mapped`` (the source observation's config-spelling names).

    Each goes through :func:`derive_labels` (``safe_label`` + the single-file rule). No
    vocabulary, an outcome already labelled, any other reason, or nothing surviving → the
    outcome unchanged.
    """
    if vocabulary is None or outcome.labels or outcome.reason is not OutcomeReason.MISSING_SOURCE_COLUMN:
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

    def _record(self, outcome: EntityOutcome, *, error_columns: Sequence[str]) -> None:
        if outcome.entity not in self._configured:
            raise ValueError(f"{outcome.entity!r} is not an entity this run is configured to produce")
        if outcome.entity in self._outcomes:
            raise ValueError(f"{outcome.entity!r} already has an outcome for this run")
        observed = apply_observation(outcome, self._missing_mapped.get(outcome.entity, ()))
        self._outcomes[outcome.entity] = apply_labels(
            observed, self._vocabularies.get(outcome.entity), error_columns=error_columns
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
    when the observation found something, and ``"labels"`` / ``"file_label"`` (plan 0053 S7)
    only when the outcome names something, so every other entry is byte-identical to before.
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
    raw_labels: Any = entry.get(LABELS_KEY)
    raw_file: Any = entry.get(FILE_LABEL_KEY, "")
    if not isinstance(raw_labels, (list, tuple)):
        return (), ""
    labels = tuple(raw_labels)
    file_label = raw_file if _file_label_shaped(raw_file) else ""
    try:
        _check_labels("stored", reason, labels, file_label)
    except (TypeError, ValueError):
        return (), ""
    return labels, file_label


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
    try:
        return EntityOutcome(entity, kind, reason, raw_rows, missing_mapped, labels, file_label)
    except (TypeError, ValueError):
        # Known codes in an impossible combination (or a corrupt row count): not evidence of anything.
        return None


def failed_entities(outcomes: Iterable[EntityOutcome]) -> tuple[EntityOutcome, ...]:
    """The outcomes whose entity FAILED — the ONE predicate behind the PARTIAL verdict (P7).

    Plan 0053 S3's reader asks this of a SUCCESSFUL run's record: a non-empty answer means
    the run completed but left at least one entity out, which Home, Run History and Convert
    show as a WARNING every run it persists. Returned in the given (configured) order, as
    whole outcomes so a caller can word each one by its reason.

    FAILED only, deliberately. ``NOT_RUN`` exists only after a raise that failed the whole
    run (a failed record already outranks PARTIAL), and EMPTY is per-entity skip-on-empty,
    which is not a fault today — whether a persistently-empty entity warns is plan 0053 S8's
    question (``failure_copy.OUTCOME_TIER``), not this predicate's.
    """
    return tuple(outcome for outcome in outcomes if outcome.kind is OutcomeKind.FAILED)


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
    read as FAILED/``transform_error``, names nothing.
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
