"""Pre-flight derivation: which source columns a config EXPECTS, and which of
them no input file carried.

**Why this exists at all.** A mapped column that is simply absent from the
frame is a DELIBERATE blank, not an error: ``apply_field_map``'s own docstring
calls it an "intended blank" and does NOT record it to ``context.data_errors``
(``transformers/base.py``). So a district whose export renames one header
converts cleanly, delivers, reports zero data errors — and ships that column
empty in every row. This module is the ONLY signal for that case; it is not the
setup-time twin of the ``data_errors`` axis, which fires only where something
RAISED.

**Two claims, two scopes.** A ``field_map`` entry names a COLUMN, never a
file — ``Classes`` reads five source files and its entries name bare columns.

* :func:`missing_columns` / :func:`preflight_report` (the self-service creator's
  report) make the FILE-AGNOSTIC claim "this column is not present in ANY of the
  files we loaded", grouped across every entity that names it.
* :func:`missing_columns_by_entity` (plan 0053 S6 — the run-time SOURCE
  OBSERVATION, ``failure-policy.md`` §10, P11) makes the OWN-FILE claim "this
  entity's mapped column is not in the file(s) THIS ENTITY reads": a header
  present in another entity's file must not hide a miss in this one's (the
  masking the file-agnostic union has — Family's contacts file lacking
  ``Email Address`` while the staff file carries one). Which frames each entity's
  field_map is actually read from is transformer knowledge, so it is declared
  once, per entity, in :data:`OBSERVATION_SCOPE` (verified transformer by
  transformer, and completeness-pinned against the registry).
* :func:`label_vocabulary_by_entity` (plan 0053 S7, owner decision D4) derives,
  from the SAME expectations and source-file list, each entity's config-declared
  label vocabulary — what ``outcomes.safe_label`` checks a name against before
  the record or the copy may print it.

Nothing here resolves a transformer's own fallback (``columns.resolve_source_column``'s
``default=``): that stays transformer knowledge, and this layer stays config-only
(CLAUDE.md → Configurable Columns).

**Purity.** No I/O, no ``pandas``, no ``pathlib``, no ``flet`` — it is handed an
already-observed ``{filename -> headers}`` mapping (``PipelineResult.input_columns``)
and a validated ``MappingConfig``, and returns dataclasses. Wording lives with
its consumer (``src.ui_flet.config_editor``), so this module can be re-used by a
surface that would word it differently.

**Totality.** Every function here is TOTAL: a field-map shape it cannot read
yields a SHORTER derivation, never an exception in front of an admin. A
mis-read config must not be able to turn a working conversion into an error
message.

**Privacy.** Every string this module handles is a column NAME — config
vocabulary and a GDE header row, never a cell. No student data can reach it,
which is what makes naming the value the right answer here (the rest of the app
describes values rather than quoting them).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from string import Formatter
from types import MappingProxyType
from typing import Any, Final

from src.config.models import (
    ConfiguredField,
    FieldAppendYear,
    FieldEmailFormat,
    FieldEnrollStatus,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    MappingConfig,
    ensure_field_mapping,
)
from src.etl.column_names import normalize_column_name
from src.etl.outcomes import LabelVocabulary

logger = logging.getLogger(__name__)

ROW_FILTER_FIELD = "(row filter)"
"""The ``output_field`` label for a column referenced by ``row_filters`` only.

``row_filters`` gates ROWS before mapping, so the column it reads has no output
field to name. A parenthesised non-identifier keeps it distinguishable from a
real contract field name — and these labels are internal grouping data that no
admin-facing sentence prints (the wording layer reads ``entities`` only).
"""


class ExpectationOrigin(StrEnum):
    """Which part of an entity's config named an expected column.

    The source observation (:func:`missing_columns_by_entity`) needs to tell the
    three apart: a ``field_map`` or ``row_filters`` column is read from the
    entity's OWN files, while a ``source_columns`` value is a documented
    CROSS-FILE auxiliary read and is excluded from the own-file claim. Carried on
    the expectation itself rather than as a parallel list, so the two can never
    disagree.
    """

    FIELD_MAP = "field_map"
    ROW_FILTER = "row_filter"
    SOURCE_COLUMN = "source_column"


class ObservationScope(StrEnum):
    """Which observed headers an entity's mapped columns are compared against."""

    OWN_FILES = "own_files"  # the union of the entity's OWN source files
    ALL_FILES = "all_files"  # every loaded file: its field_map is also read from another entity's file
    NO_CLAIM = "no_claim"  # its field_map values are not source reads at all


OBSERVATION_SCOPE: Final[Mapping[str, ObservationScope]] = MappingProxyType(
    {
        # apply_field_map over its demographic frame (+ the email / enroll-status reads on that frame).
        "Students": ObservationScope.OWN_FILES,
        # apply_field_map over staff_info, or the staff_info + roster merge — both its own source_files.
        # Its teacher-of-record rescue reads the timetable files, but never through a field_map value.
        "Staff": ObservationScope.OWN_FILES,
        # apply_field_map over its contacts frame; the student-number filter reads the same frame.
        "Family": ObservationScope.OWN_FILES,
        # Every field_map read is on schedule + course_info + staff_info, the demographic homeroom
        # frame or class_info — all five are its own source_files.
        "Classes": ObservationScope.OWN_FILES,
        # Its `staff_id_col` (the field_map's id-role pair) is ALSO read from ClassInformation — a
        # CLASSES source file — for the co-teacher rows (`_classinfo_coteacher_enrollments`, through
        # the published class artifacts). A column legitimately present only there is not a miss.
        "Enrollments": ObservationScope.ALL_FILES,
        # apply_field_map over its one course_info frame.
        "CourseInfo": ObservationScope.OWN_FILES,
        # Output-keyed overrides resolve through the field_map and are read from its own history /
        # selection / course_info frames; its auxiliary reads are `source_columns` (excluded anyway).
        "StudentCourses": ObservationScope.OWN_FILES,
        # Its field_map values are PLACEHOLDERS (the transformer builds rows directly and never calls
        # apply_field_map); its real reads are `global_config.attendance.*`. Comparing a placeholder
        # would be a false claim.
        "StudentAttendance": ObservationScope.NO_CLAIM,
    }
)
"""Per registry entity, where its field_map is actually READ from (plan 0053 S6).

Verified transformer by transformer against ``src/etl/transformers/`` (DECISIONS
2026-09-24, S6) and pinned complete against ``TRANSFORMER_REGISTRY`` by
``tests/test_etl_preflight.py``. An entity not listed (a hand-dropped YAML's
invention, run by ``DefaultTransformer`` over its PRIMARY frame only) is
:attr:`ObservationScope.OWN_FILES` — the union of its own files is a superset of
what it reads, so the default can only under-report, never invent.
"""


def observation_scope(entity: str) -> ObservationScope:
    """:data:`OBSERVATION_SCOPE` for ``entity``; OWN_FILES for anything unlisted."""
    return OBSERVATION_SCOPE.get(entity, ObservationScope.OWN_FILES)


# ---------------------------------------------------------------------------
# The two records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedColumn:
    """One (entity, output field, source column) expectation read off a config.

    ``source_column`` is the CONFIG's own spelling, whitespace-trimmed — that is
    what a report quotes, because the admin compares it against their header
    row. Every COMPARISON goes through :func:`normalize_column_name`, never the
    stored spelling.

    ``origin`` says which part of the config named it (see
    :class:`ExpectationOrigin`). Its default, ``FIELD_MAP``, is the direction
    that REPORTS: an expectation built without one is observed, never silently
    excluded.
    """

    entity: str
    output_field: str
    source_column: str
    origin: ExpectationOrigin = ExpectationOrigin.FIELD_MAP


@dataclass(frozen=True)
class MissingColumn:
    """One source column no loaded file carried, grouped across its consumers.

    One header often feeds several entities (``Student Number`` feeds four), and
    a line each would read as several separate problems — so the grouping is
    part of the derivation, not of the wording. ``entities`` and
    ``output_fields`` are in first-seen order (config order), deduped.
    """

    source_column: str
    entities: tuple[str, ...]
    output_fields: tuple[str, ...]


@dataclass(frozen=True)
class PreflightReport:
    """The finding plus its DENOMINATORS.

    ``checked_files`` (keys that contributed at least one header) and
    ``checked_columns`` (distinct expected source columns considered) exist so
    that "nothing missing" is a MEASURED green rather than an empty derivation:
    ``checked_files == 0`` means nothing was observed and no claim is being
    made, and ``checked_columns == 0`` means the config named no source column
    at all. Neither is an all-clear.
    """

    missing: tuple[MissingColumn, ...]
    checked_files: int
    checked_columns: int


# ---------------------------------------------------------------------------
# Expected columns — reading a config
# ---------------------------------------------------------------------------


def expected_columns(config: MappingConfig) -> tuple[ExpectedColumn, ...]:
    """Every source column the config's ACTIVE entities name, in config order.

    ACTIVE via ``config.active_entities()`` — never ``mappings.keys()``, which
    under ``_base`` inheritance carries entities this district does not emit
    (``CourseInfo``/``StudentCourses`` on a rostering-only tier), whose columns
    would be reported as missing from files the run never even loads.

    Which shapes name a source column (each read through
    ``models.ensure_field_mapping``, the single idempotent boundary):

    - a bare ``str`` → that column
    - ``FieldTransform`` / ``FieldAppendYear`` → ``column``
    - ``FieldEmailFormat`` → the ``{placeholders}`` of ``format`` (parsed by the
      stdlib ``string.Formatter``, i.e. exactly the reading ``str.format`` does
      in ``emails.generate_student_email``) MINUS the ``derived_dates`` keys
      (pseudo fields the transformer injects, not headers) PLUS every
      ``derived_dates[*].column``
    - ``FieldNameConfig`` → its four columns, blanks skipped
    - ``FieldIdRolePair`` → ``student_id_col`` AND ``staff_id_col``
    - ``FieldEnrollStatus`` → ``status_column`` / ``withdraw_date_column`` when
      set (``active_values`` are VALUES, not columns)
    - ``EntityConfig.row_filters[].column`` and every ``source_columns`` VALUE

    And which name none: ``FieldFixedValue`` / ``FieldAcademicYear`` (a literal
    names no column), the ``None`` auto-detect sentinel (its aliases are a SET —
    naming one would be false), and any other shape. (A dict with no mapping
    shape no longer reaches here as a dict: since plan 0053 S12 ``classify_field``
    refuses it at load, and for a raw caller the ``ValueError`` is caught below.)

    Every derived name then passes ONE shape filter, :func:`_looks_like_header`: a
    VALIDATED config has already turned an unreadable field_map value into a string
    (``classify_field``'s ``str(raw)`` fallback), so ``[1, 2]`` arrives here as the
    plausible-looking column name ``'[1, 2]'`` and must not be reported as one.

    TOTAL: anything unreadable is skipped with a DEBUG line naming the entity
    and output field (both config vocabulary) and never the value.
    """
    try:
        active = config.active_entities()
        declared = list(config.mappings.items())
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: config carries no readable entity mappings ({type(exc).__name__})")
        return ()

    expected: list[ExpectedColumn] = []
    for entity_name, entity_cfg in declared:
        if entity_name not in active:
            continue
        entity = str(entity_name)
        for output_field, raw in _mapping_items(entity, entity_cfg, "field_map"):
            try:
                names = _field_map_columns(raw)
            except Exception as exc:  # noqa: BLE001 — total by contract
                logger.debug(f"Pre-flight: unreadable field_map entry {entity}.{output_field} ({type(exc).__name__})")
                continue
            expected.extend(
                ExpectedColumn(entity=entity, output_field=output_field, source_column=name) for name in names
            )
        expected.extend(_row_filter_columns(entity, entity_cfg))
        for role, raw_value in _mapping_items(entity, entity_cfg, "source_columns"):
            name = _clean(raw_value)
            if name:
                expected.append(
                    ExpectedColumn(
                        entity=entity,
                        output_field=role,
                        source_column=name,
                        origin=ExpectationOrigin.SOURCE_COLUMN,
                    )
                )

    # ONE shape filter over every derived expectation (field_map, row_filters AND
    # source_columns): a name that cannot be a header is a stringified junk value, not
    # a column an admin should be sent looking for. See :func:`_looks_like_header`.
    return tuple(item for item in expected if _looks_like_header(item.source_column))


def _mapping_items(entity: str, entity_cfg: Any, attribute: str) -> list[tuple[str, Any]]:
    """``entity_cfg.<attribute>`` as a list of ``(key, value)``; ``[]`` if unreadable."""
    try:
        raw = getattr(entity_cfg, attribute, None) or {}
        return [(str(key), value) for key, value in raw.items()]
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: unreadable {attribute} on {entity} ({type(exc).__name__})")
        return []


def _row_filter_columns(entity: str, entity_cfg: Any) -> list[ExpectedColumn]:
    """The columns ``row_filters`` reads on *entity* (``[]`` if unreadable)."""
    try:
        filters = list(getattr(entity_cfg, "row_filters", None) or [])
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: unreadable row_filters on {entity} ({type(exc).__name__})")
        return []
    found: list[ExpectedColumn] = []
    for row_filter in filters:
        try:
            name = _clean(getattr(row_filter, "column", None))
        except Exception as exc:  # noqa: BLE001 — total by contract
            logger.debug(f"Pre-flight: unreadable row filter on {entity} ({type(exc).__name__})")
            continue
        if name:
            found.append(
                ExpectedColumn(
                    entity=entity,
                    output_field=ROW_FILTER_FIELD,
                    source_column=name,
                    origin=ExpectationOrigin.ROW_FILTER,
                )
            )
    return found


def _field_map_columns(raw: Any) -> list[str]:
    """The source columns ONE field_map value names (see :func:`expected_columns`)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        name = _clean(raw)
        return [name] if name else []
    if not isinstance(raw, (dict, ConfiguredField)):
        # A shape that is neither a column name nor a structured variant (a list,
        # a number) names no column, and is dropped before ``ensure_field_mapping``
        # can stringify it into a plausible-looking column name.
        #
        # **This branch only fires for a RAW / UNVALIDATED caller** (a hand-built
        # ``model_construct`` config, or a future caller reading YAML directly). A
        # validated ``MappingConfig`` has already been through ``classify_field``,
        # whose own non-dict fallback is ``str(raw)`` — so ``{"Weird": [1, 2]}``
        # reaches this module as the bare string ``'[1, 2]'`` and is caught one
        # layer out, by :func:`_looks_like_header`.
        return []

    # A dict / typed variant from here on, so ``ensure_field_mapping`` can only
    # return a structured variant (or raise for a dict with no mapping shape —
    # caught by the caller, total by contract) — the bare-string and ``None``
    # cases were both handled above.
    spec = ensure_field_mapping(raw)
    if isinstance(spec, FieldEmailFormat):
        return _email_format_columns(spec)
    if isinstance(spec, FieldNameConfig):
        return _non_blank(
            spec.primary_teacher_flag,
            spec.teacher_last_name,
            spec.course_title,
            spec.section_letter,
        )
    if isinstance(spec, FieldIdRolePair):
        return _non_blank(spec.student_id_col, spec.staff_id_col)
    if isinstance(spec, FieldEnrollStatus):
        return _non_blank(spec.status_column, spec.withdraw_date_column)
    if isinstance(spec, (FieldTransform, FieldAppendYear)):
        return _non_blank(spec.column)
    # FieldFixedValue / FieldAcademicYear (a literal names no column) — nothing.
    return []


def _email_format_columns(spec: FieldEmailFormat) -> list[str]:
    """``format``'s placeholders (minus pseudo fields) plus the derived-date columns."""
    pseudo = set()
    derived: dict[str, Any] = {}
    try:
        derived = dict(spec.derived_dates or {})
        pseudo = {normalize_column_name(str(key)) for key in derived}
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: unreadable email derived_dates ({type(exc).__name__})")

    names: list[str] = []
    try:
        parsed = list(Formatter().parse(str(spec.format)))
    except Exception as exc:  # noqa: BLE001 — total by contract; a malformed template is the transformer's error to raise
        logger.debug(f"Pre-flight: unparseable email format template ({type(exc).__name__})")
        parsed = []
    for _literal, field_name, _format_spec, _conversion in parsed:
        if not field_name:
            # ``None`` = trailing literal text; ``""`` = a positional ``{}``,
            # which names no column.
            continue
        # ``str.format`` reads ``{a.b}`` / ``{a[0]}`` as attribute/index access on
        # the value of ``a``, so the COLUMN is the root name.
        root = _clean(str(field_name).split(".")[0].split("[")[0])
        if not root or root.isdigit():
            continue
        if normalize_column_name(root) in pseudo:
            continue
        names.append(root)
    for date_spec in derived.values():
        name = _clean(getattr(date_spec, "column", None))
        if name:
            names.append(name)
    return names


def _looks_like_header(name: str) -> bool:
    """Whether *name* could be a GDE header row entry at all.

    The shape filter that keeps a stringified junk VALUE out of an admin-facing
    report. A validated ``MappingConfig`` has already run ``classify_field``, whose
    non-dict fallback is ``str(raw)`` — so a hand-edited ``field_map: {"Weird": [1, 2]}``
    arrives here as the bare string ``'[1, 2]'``, indistinguishable by TYPE from a real
    column name. Reporting it would tell an admin to go looking for a column named
    ``[1, 2]`` in their export, which is worse than saying nothing: the ETL does not
    treat that entry as a column either — it blanks the field and
    ``apply_field_map``'s own data-error path owns the failure.

    Deliberately a SHAPE test and nothing more (non-empty once normalised, no
    ``[]``/``{}`` brackets, not purely numeric): a GDE header is arbitrary district
    vocabulary, so anything narrower would start suppressing real columns — and
    under-reporting a real missing column is the failure this module exists to avoid.
    """
    cleaned = normalize_column_name(name)
    if not cleaned:
        return False
    if any(bracket in cleaned for bracket in "[]{}"):
        return False
    return not cleaned.isdigit()


def _non_blank(*values: Any) -> list[str]:
    """The trimmed spellings of *values* that are non-blank, in order."""
    return [name for name in (_clean(value) for value in values) if name]


def _clean(value: Any) -> str:
    """*value* as its trimmed config spelling; ``""`` when it names nothing.

    Coerces at THIS boundary — a hand-edited YAML value reaching here may not be
    a ``str``, and this module must not raise on one (``normalize_column_name``
    itself deliberately still does, for a frame label).
    """
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:  # noqa: BLE001 — total by contract
        return ""


# ---------------------------------------------------------------------------
# Missing columns — comparing against what was observed
# ---------------------------------------------------------------------------


def missing_columns(
    expected: Iterable[ExpectedColumn],
    input_columns: Mapping[str, Sequence[str]],
) -> tuple[MissingColumn, ...]:
    """The expected columns present in NONE of the observed files, grouped.

    **The soundness rule:** when NOTHING was observed — an empty mapping, or one
    whose every value is empty — this returns ``()``. The claim's premise is
    "we read your files"; without it, a default-constructed ``PipelineResult``
    would indict every column the config names. Under-reporting is the safe
    direction; a wall of invented findings is not.

    Comparison is on the NORMALISED name (so a padded/mixed-case config entry
    matches the extractor's normalised header); the reported spelling is the
    first one the config used.
    """
    observed = _observed_names(input_columns)
    if not observed:
        return ()

    spelling: dict[str, str] = {}
    entities: dict[str, list[str]] = {}
    fields: dict[str, list[str]] = {}
    order: list[str] = []

    for item in expected:
        key = normalize_column_name(_clean(getattr(item, "source_column", None)))
        if not key or key in observed:
            continue
        if key not in spelling:
            spelling[key] = _clean(item.source_column)
            entities[key] = []
            fields[key] = []
            order.append(key)
        entity = _clean(getattr(item, "entity", None))
        if entity and entity not in entities[key]:
            entities[key].append(entity)
        output_field = _clean(getattr(item, "output_field", None))
        if output_field and output_field not in fields[key]:
            fields[key].append(output_field)

    return tuple(
        MissingColumn(
            source_column=spelling[key],
            entities=tuple(entities[key]),
            output_fields=tuple(fields[key]),
        )
        for key in order
    )


def _observed_names(input_columns: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """Every observed header, normalised, folded across ALL files into one set."""
    try:
        values = list(input_columns.values())
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: unreadable observed-column mapping ({type(exc).__name__})")
        return frozenset()

    names: set[str] = set()
    for columns in values:
        if isinstance(columns, str) or not isinstance(columns, Iterable):
            continue
        try:
            for column in columns:
                name = normalize_column_name(_clean(column))
                if name:
                    names.add(name)
        except Exception as exc:  # noqa: BLE001 — total by contract
            logger.debug(f"Pre-flight: unreadable observed columns for one file ({type(exc).__name__})")
    return frozenset(names)


def preflight_report(
    config: MappingConfig,
    input_columns: Mapping[str, Sequence[str]],
) -> PreflightReport:
    """The full derivation: the missing columns plus the denominators that measure it.

    ``checked_files`` counts the keys that contributed at least one header (so a
    file absent from disk — carried as an empty tuple — is not counted as read);
    ``checked_columns`` counts the distinct expected source columns considered,
    whether or not anything was observed. See :class:`PreflightReport`.
    """
    expected = expected_columns(config)
    missing = missing_columns(expected, input_columns)

    try:
        checked_files = sum(1 for columns in input_columns.values() if columns)
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: uncountable observed-column mapping ({type(exc).__name__})")
        checked_files = 0
    checked_columns = len({normalize_column_name(_clean(item.source_column)) for item in expected} - {""})

    logger.info(
        f"Pre-flight column check: {len(missing)} expected column(s) not present in any of "
        f"{checked_files} file(s) read ({checked_columns} distinct expected column(s) considered)"
    )
    return PreflightReport(missing=missing, checked_files=checked_files, checked_columns=checked_columns)


# ---------------------------------------------------------------------------
# The run-time source observation — OWN files, per entity (plan 0053 S6)
# ---------------------------------------------------------------------------


def missing_columns_by_entity(
    config: MappingConfig,
    input_columns: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Per ACTIVE entity, its mapped columns absent from the file(s) it reads.

    ``{entity: (source column in CONFIG spelling, ...)}`` in config order, deduped
    on the normalised name (first spelling wins); an entity with nothing missing —
    or about which no claim can be made — is absent. ``input_columns`` is the
    run's raw observation (``pipeline.observed_input_columns``), keyed by the
    config's spelling of each file.

    **What is compared.** The entity's ``field_map`` and ``row_filters`` columns
    (``ExpectationOrigin.FIELD_MAP`` / ``ROW_FILTER``); never its
    ``source_columns``, which are documented cross-file auxiliary reads. They are
    compared with the union of the normalised headers of the entity's OWN
    ``source_files`` — with every loaded file for an
    :attr:`ObservationScope.ALL_FILES` entity, and not at all for a
    :attr:`ObservationScope.NO_CLAIM` one (see :data:`OBSERVATION_SCOPE`). A
    headerless file carries its DECLARED headers here (the extractor injects
    them), so its mapped columns match by construction.

    **The soundness rule, per entity:** no claim unless EVERY one of the entity's
    own source files was observed with a header row. A file absent from disk (an
    empty tuple), an entity declaring no source file, or a header row that could
    not be read in full all mean "we did not see what this entity reads" — and
    under-reporting is the safe direction.

    TOTAL: never raises; anything unreadable yields a SHORTER answer (``{}`` at
    worst), logged at DEBUG without the value.
    """
    try:
        observed = _observed_by_file(input_columns)
        expected = expected_columns(config)
        sources = _entity_source_files(config)
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: no per-entity observation ({type(exc).__name__})")
        return {}

    every_file: frozenset[str] = frozenset().union(*observed.values())
    pools: dict[str, frozenset[str] | None] = {}
    found: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    for item in expected:
        if item.origin is ExpectationOrigin.SOURCE_COLUMN:
            continue
        entity = item.entity
        if entity not in pools:
            pools[entity] = _pool_for(entity, sources.get(entity, ()), observed, every_file)
        pool = pools[entity]
        if pool is None:
            continue
        key = normalize_column_name(_clean(item.source_column))
        already = seen.setdefault(entity, set())
        if not key or key in pool or key in already:
            continue
        already.add(key)
        found.setdefault(entity, []).append(_clean(item.source_column))

    return {entity: tuple(columns) for entity, columns in found.items()}


def label_vocabulary_by_entity(config: MappingConfig) -> dict[str, LabelVocabulary]:
    """Per ACTIVE entity, the config-DECLARED names its outcome may carry as labels (plan 0053 S7).

    Built on the same derivation as :func:`missing_columns_by_entity` — never a second
    reading of the config:

    * ``columns`` — the entity's ``field_map`` + ``row_filters`` expectations
      (:func:`expected_columns`, so already through :func:`_looks_like_header`), in CONFIG
      spelling (trimmed, never lowercased). Never its ``source_columns``: those are cross-file
      auxiliary reads. Empty for an :attr:`ObservationScope.NO_CLAIM` entity, whose field_map
      values are placeholders rather than columns it reads — naming one would be false;
    * ``files`` — its configured ``source_files`` (config spelling, deduped);
    * ``reads_own_files`` — :attr:`ObservationScope.OWN_FILES` only. An ALL_FILES entity
      (Enrollments) may read a mapped column from ANOTHER entity's file, so its own file
      must never be named beside that column.

    Membership itself is decided in ONE place, ``outcomes.safe_label``; this only says what
    the resolved config declares. TOTAL: never raises (``{}`` at worst — no labels).
    """
    try:
        expected = expected_columns(config)
        sources = _entity_source_files(config)
    except Exception as exc:  # noqa: BLE001 — total by contract; no vocabulary names nothing
        logger.debug(f"Pre-flight: no label vocabulary ({type(exc).__name__})")
        return {}

    vocabularies: dict[str, LabelVocabulary] = {}
    for entity, files in sources.items():
        scope = observation_scope(entity)
        columns: frozenset[str] = frozenset()
        if scope is not ObservationScope.NO_CLAIM:
            columns = frozenset(
                _clean(item.source_column)
                for item in expected
                if item.entity == entity and item.origin is not ExpectationOrigin.SOURCE_COLUMN
            ) - {""}
        vocabularies[entity] = LabelVocabulary(
            columns=columns,
            files=files,
            reads_own_files=scope is ObservationScope.OWN_FILES,
        )
    return vocabularies


def _pool_for(
    entity: str,
    files: Sequence[str],
    observed: Mapping[str, frozenset[str]],
    every_file: frozenset[str],
) -> frozenset[str] | None:
    """The headers ``entity``'s columns are compared against — ``None`` means make no claim."""
    scope = observation_scope(entity)
    if scope is ObservationScope.NO_CLAIM or not files:
        return None
    if any(not observed.get(filename) for filename in files):
        return None  # soundness: one of its own files was not seen with a header row
    if scope is ObservationScope.ALL_FILES:
        return every_file
    return frozenset().union(*(observed[filename] for filename in files))


def _entity_source_files(config: MappingConfig) -> dict[str, tuple[str, ...]]:
    """Each ACTIVE entity's declared source filenames (config spelling)."""
    active = config.active_entities()
    files: dict[str, tuple[str, ...]] = {}
    for entity_name, entity_cfg in list(config.mappings.items()):
        if entity_name not in active:
            continue
        entity = str(entity_name)
        names = [_clean(value) for _role, value in _mapping_items(entity, entity_cfg, "source_files")]
        files[entity] = tuple(dict.fromkeys(name for name in names if name))
    return files


def _observed_by_file(input_columns: Mapping[str, Sequence[str]]) -> dict[str, frozenset[str]]:
    """``{filename: its normalised headers}`` for every file whose header row was READ IN FULL.

    A file that is absent, header-less, not a column sequence, or whose column list
    raised part-way is left OUT ("not observed") rather than kept partial: a
    partial header row would make every column it did not yield look missing.
    """
    try:
        items = list(input_columns.items())
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.debug(f"Pre-flight: unreadable observed-column mapping ({type(exc).__name__})")
        return {}

    observed: dict[str, frozenset[str]] = {}
    for filename, columns in items:
        name = _clean(filename)
        if not name or isinstance(columns, str) or not isinstance(columns, Iterable):
            continue
        try:
            headers = frozenset(normalize_column_name(_clean(column)) for column in columns) - {""}
        except Exception as exc:  # noqa: BLE001 — total by contract
            logger.debug(f"Pre-flight: unreadable observed columns for one file ({type(exc).__name__})")
            continue
        if headers:
            observed[name] = headers
    return observed
