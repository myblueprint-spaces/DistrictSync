"""Leave out the rows missing a value SpacesEDU requires — ONE composed rule (plan 0053 S13d).

``docs/developer/failure-policy.md`` §5 (c) ``contract_field``, catalogue #42 (the rule) and
#43 / #40 (a required column the MAPPING does not produce). Owner ruling 2026-09-28: "leave out +
count + amber" — Family's no-email exclusion, generalised to every value the Advanced CSV spec
requires in the five rostering files (:data:`~src.etl.required_fields.REQUIRED_OUTPUT_FIELDS`).

Each rostering transformer calls :func:`leave_out_rows_missing_required_values` ONCE, on its
OUTPUT frame, AFTER every scope filter it applies (active status, grade scope, cross-enrollment
collapse, departed staff, the unroled-staff drop, ``row_filters``, the roster filter, Family's
email exclusion) and after Students' preferred-name fallback — so a scope filter is never counted
as a missing value. What it does:

* **A blank required value** (the ONE blank test, :func:`~src.etl.transformers.ids.is_blank_series`:
  NaN, ``pd.NA``, empty, whitespace, the literal ``nan``) leaves that ROW out. The count of rows
  left out is recorded ONCE as the output's own ``OutcomeNote`` (``required_fields.REQUIRED_VALUE_NOTE``,
  WARNING tier — the run is amber every night it persists), with ONE aggregated WARNING naming
  the contract columns and how many rows each was blank on — counts and OUTPUT column names
  only, never a value, an id or a name (§8).
* **A required column absent from the frame** (the mapping does not produce it at all) is a
  CONFIG fault, never a per-row one: the rows ship untouched — emptying a whole file for a
  mapping gap would stop the night on something the district cannot fix — and it is surfaced
  ONCE: ``EMAIL_OUTPUT_NOT_MAPPED`` for the output's email column (its meaning unchanged since
  S11), ``REQUIRED_OUTPUT_NOT_MAPPED`` for any other. Every bundled config produces every
  required column (pinned), so on a shipped config this branch is reachable only through a
  hand-edited mapping.
* **The left-out ids** of ``id_column`` are returned for the cascade (``failure-policy.md`` §5
  #42): an id is LEFT OUT only when NONE of its rows was kept — a pupil cross-enrolled at two
  schools with one row lacking its school still ships — and a blank id is never one (it names
  nobody another file could reference).

Composed, not a ``BaseTransformer`` member (P10; ``TestRuleG_BaseTransformerGrowsNoMembers``).
"""

from __future__ import annotations

import logging
from functools import reduce
from typing import Final, NamedTuple

import pandas as pd

from src.etl.outcomes import OutcomeNote
from src.etl.required_fields import EMAIL_OUTPUT_FIELDS, REQUIRED_VALUE_NOTE, required_fields
from src.etl.transformers.ids import is_blank_series, normalize_id_series
from src.etl.transformers.notes import NoteCarrier, record_note

logger = logging.getLogger(__name__)

#: The grep anchor of the rule's ONE log line per output — the troubleshooting page tells an admin
#: to search ``etl_tool.log`` for it (pinned there by ``tests/test_partner_doc_schedule_copy_parity.py``).
REQUIRED_VALUES_LOG_ANCHOR: Final = "REQUIRED VALUES MISSING"


class LeftOut(NamedTuple):
    """What :func:`leave_out_rows_missing_required_values` kept, and whom it left out."""

    #: the rows that ship — a NEW frame (a copy) whenever a row was left out, else the input
    kept: pd.DataFrame
    #: the normalised ``id_column`` values none of whose rows was kept (``frozenset()`` when no
    #: ``id_column`` was asked for) — what the output publishes for the no-orphan cascade
    ids: frozenset[str]


def leave_out_rows_missing_required_values(
    frame: pd.DataFrame,
    entity: str,
    context: NoteCarrier,
    *,
    id_column: str | None,
) -> LeftOut:
    """Leave out ``entity``'s rows that lack a required value; record and log it once (§5 #42).

    ``frame`` is the entity's OUTPUT frame (contract column names). ``id_column`` is the column
    whose left-out values the caller publishes for the cascade (``User ID`` for Staff, ``Class
    ID`` for Classes) or ``None`` when nothing downstream references the output's rows —
    REQUIRED keyword-only, because forgetting it would silently skip the cascade. An empty frame
    is returned as it is, recording nothing (a note counts at least one row).
    """
    required = required_fields(entity)
    if frame.empty:
        return LeftOut(frame, frozenset())
    absent = [column for column in required if column not in frame.columns]
    present = [column for column in required if column in frame.columns]
    if not present:
        _note_unmapped(entity, absent, len(frame), context)
        return LeftOut(frame, frozenset())
    blank_by_column = {column: is_blank_series(frame[column]) for column in present}
    missing = reduce(lambda acc, mask: acc | mask, blank_by_column.values())
    left = int(missing.sum())
    # The unmapped notes count the rows that SHIP without the column — after the blank rows go.
    _note_unmapped(entity, absent, len(frame) - left, context)
    if not left:
        return LeftOut(frame, frozenset())
    per_column = {column: int(mask.sum()) for column, mask in blank_by_column.items() if bool(mask.any())}
    # failure-policy: contract_field
    record_note(
        context,
        entity,
        REQUIRED_VALUE_NOTE[entity],
        left,
        log=logger,
        message=(
            f"[{entity}] {REQUIRED_VALUES_LOG_ANCHOR} — left out {left} of {len(frame)} row(s) missing a value "
            f"SpacesEDU's import requires (rows blank per column: {per_column}). A row is never sent with "
            f"a required value blank; fill the values in at the source and the next sync includes those rows."
        ),
    )
    kept: pd.DataFrame = frame[~missing].copy()  # type: ignore[assignment]
    if id_column is None:
        return LeftOut(kept, frozenset())
    return LeftOut(kept, _left_out_ids(frame[missing], kept, id_column))  # type: ignore[arg-type]


def _left_out_ids(dropped: pd.DataFrame, kept: pd.DataFrame, id_column: str) -> frozenset[str]:
    """The normalised ``id_column`` values of ``dropped`` that no kept row carries — never a blank."""
    if id_column not in dropped.columns:
        return frozenset()
    candidates = normalize_id_series(dropped[id_column])[~is_blank_series(dropped[id_column])]
    still_shipped = set(normalize_id_series(kept[id_column])) if id_column in kept.columns else set()
    return frozenset(value for value in candidates if value not in still_shipped)


def _note_unmapped(entity: str, absent: list[str], rows: int, context: NoteCarrier) -> None:
    """A required column the mapping does not produce: the rows ship, surfaced ONCE (§5 #40/#43).

    ``rows`` is how many rows SHIP without it — the frame less any row the rule left out for a
    blank value in a column that IS produced. Nothing is recorded when ``absent`` is empty; when
    every row was left out there is no shipped row to count (a note counts at least one), so the
    gap is logged once instead — the empty output then stops the night on its own.
    """
    if not absent:
        return
    if not rows:
        logger.warning(
            "[%s] No output column for %s, which SpacesEDU's import requires — a mapping gap; check the config field_map.",
            entity,
            absent,
        )
        return
    email = EMAIL_OUTPUT_FIELDS.get(entity)
    if email in absent:
        # failure-policy: contract_field
        record_note(
            context,
            entity,
            OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED,
            rows,
            log=logger,
            message=(
                f"[{entity}] No '{email}' output column — rows without an email cannot be left out. The "
                f"Advanced CSV contract requires it for {entity}.csv; check the config field_map."
            ),
        )
    others = [column for column in absent if column != email]
    if others:
        # failure-policy: contract_field
        record_note(
            context,
            entity,
            OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED,
            rows,
            log=logger,
            message=(
                f"[{entity}] No output column for {others}, which SpacesEDU's import requires — "
                f"{rows} row(s) ship without {'it' if len(others) == 1 else 'them'}. This is a mapping "
                f"gap, not a data one; check the config field_map."
            ),
        )
