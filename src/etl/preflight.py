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

**Scope of the claim (deliberately file-agnostic).** A ``field_map`` entry
names a COLUMN, never a file — ``Classes`` reads five source files and its
entries name bare columns — so the only sound claim is "this column is not
present in ANY of the files we loaded". Nothing here says which file a column
should have been in, and nothing here resolves a transformer's own fallback
(``base.resolve_column``'s ``default=``): those are transformer knowledge, and
this layer stays config-only (CLAUDE.md → Configurable Columns).

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
from string import Formatter
from typing import Any

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

logger = logging.getLogger(__name__)

ROW_FILTER_FIELD = "(row filter)"
"""The ``output_field`` label for a column referenced by ``row_filters`` only.

``row_filters`` gates ROWS before mapping, so the column it reads has no output
field to name. A parenthesised non-identifier keeps it distinguishable from a
real contract field name — and these labels are internal grouping data that no
admin-facing sentence prints (the wording layer reads ``entities`` only).
"""


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
    """

    entity: str
    output_field: str
    source_column: str


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
    naming one would be false), ``classify_field``'s warn-passthrough dict, and
    any other shape.

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
                expected.append(ExpectedColumn(entity=entity, output_field=role, source_column=name))

    return tuple(expected)


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
            found.append(ExpectedColumn(entity=entity, output_field=ROW_FILTER_FIELD, source_column=name))
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
        # a number) names no column. Deliberately handled BEFORE
        # ``ensure_field_mapping``, whose non-dict fallback would stringify it
        # into a plausible-looking column name and report that junk to an admin.
        return []

    # A dict / typed variant from here on, so ``ensure_field_mapping`` can only
    # return a structured variant or the warn-passthrough dict — the bare-string
    # and ``None`` cases were both handled above.
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
    # FieldFixedValue / FieldAcademicYear (a literal names no column) and
    # classify_field's warn-passthrough dict (no usable column key by
    # definition) — nothing, never a raise.
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
    except Exception as exc:  # noqa: BLE001 — a malformed template is the transformer's error to raise
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
