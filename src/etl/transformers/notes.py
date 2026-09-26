"""Record a fail-open posture: ONE aggregated log line + ONE outcome note (plan 0053 S11).

``failure-policy.md`` §5 (c)/(d)/(e): a site that fails OPEN — ships what it has because a
column it would have used is absent — must say so on the run, not only in a log line nobody
reads. :func:`record_note` is the one way a transformer does both: it records the closed
:class:`~src.etl.outcomes.OutcomeNote` on the entity's outcome through
``TransformContext.record_outcome_note`` and logs ``message`` — but only the FIRST time that
(entity, note) is recorded this run, so a site reached twice (Enrollments' two roster filters,
Classes' three course-code filters) still yields one line and one note. That is the "at most ONE
aggregated line per site per run" rule the blended detector once broke with one warning per blend.

The message and the count are the caller's: counts and CONFIG vocabulary only (a key, a
configured column spelling, a default) — never an observed header, a cell value or a name (§8).
Each call site carries a ``# failure-policy: <class>`` tag and a row in the §5 ``tags`` table
(pinned by ``tests/test_failure_policy_parity.py``).

Composed, not a ``BaseTransformer`` method (P10), and free of pandas: the context is taken as
data, so the blended detector (a plain service class) and the transformers share it. The one
note site that is shared by EVERY transformer — :func:`note_identity_blanks`, run by the
field-map engine — lives here for the same reason, taking a frame's column names and row count
rather than the frame.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from typing import Any, Final, Protocol

from src.config.models import FieldAppendYear, FieldTransform, ensure_field_mapping
from src.etl.outcomes import Note, OutcomeNote

logger = logging.getLogger(__name__)

#: The OUTPUT fields that are an identity or a join key (``failure-policy.md`` §5 #19). One the
#: field-map engine can only blank — its mapped source column is absent — is recorded
#: (``OutcomeNote.IDENTITY_FIELD_BLANKED``) rather than shipped blank in silence; whether it
#: should fail instead is an open direction decision, not this module's.
IDENTITY_OUTPUT_FIELDS: Final[frozenset[str]] = frozenset({"User ID", "Student User ID", "Class ID", "School ID"})


#: What an observed CODE looks like when a message may echo it: empty, or 1-4 UPPER-CASE
#: letters/digits/hyphens (``A``, ``T``, ``L-E``). Upper-case only, deliberately: MyEd BC codes
#: arrive upper-case, while a pupil's or a teacher's name arrives mixed-case — a mixed-case
#: pattern echoed ``Li``/``Kai``/``Wong``. Anything else is described by a count, never shown
#: (§8: no observed value in a log line). Shared by the attendance unmapped-code message and the
#: staff-vocabulary line.
CODE_SHAPE: Final[re.Pattern[str]] = re.compile(r"(?:[A-Z0-9][A-Z0-9-]{0,3})?")


def is_code_shaped(value: str) -> bool:
    """Whether ``value`` has :data:`CODE_SHAPE` and so may be echoed in a log line."""
    return CODE_SHAPE.fullmatch(value) is not None


class NoteCarrier(Protocol):
    """The slice of ``TransformContext`` a note is recorded through."""

    def record_outcome_note(self, entity: str, note: OutcomeNote, count: int) -> None: ...

    def outcome_notes_for(self, entity: str) -> tuple[Note, ...]: ...


def record_note(
    context: NoteCarrier,
    entity: str,
    note: OutcomeNote,
    count: int,
    *,
    log: logging.Logger,
    message: str,
    level: int = logging.WARNING,
) -> bool:
    """Record ``note`` (``count`` ≥ 1) on ``entity`` and log ``message`` — once per run.

    Returns ``True`` when this call recorded the note (and logged), ``False`` when the same
    (entity, note) was already recorded this run (nothing logged; the first count stands). A
    count below one is a caller bug and raises (``TransformContext.record_outcome_note``).
    """
    already = any(code is note for code, _count in context.outcome_notes_for(entity))
    context.record_outcome_note(entity, note, count)
    if already:
        return False
    log.log(level, message)
    return True


def note_identity_blanks(
    columns: Collection[str],
    rows: int,
    field_map: Mapping[str, Any],
    entity: str,
    context: NoteCarrier,
    *,
    prefilled: Collection[str],
) -> None:
    """Record an identity field the field-map engine could only blank (§5 #19, plan 0053 S11).

    ``columns`` / ``rows`` describe the frame the engine read (its normalised column names
    and row count); ``prefilled`` is the set of output fields already in the result BEFORE the
    engine's loop ran — the engine skips those, so this does too.

    An intended blank is not a data error, and an ABSENT mapped column is already observed
    per entity by the source observation (``missing_mapped``, S6). An IDENTITY field is
    different in kind: shipped blank it links nothing — a class-(b) posture whose direction
    (fail or ship) is still open. Until that is decided it is never silent: ONE WARNING per
    entity per run naming the output fields and their configured columns (config vocabulary,
    never an observed header) and ``OutcomeNote.IDENTITY_FIELD_BLANKED`` counting the rows.
    Only an entry that READS a column counts (a bare string, a ``column:`` transform, an
    append-year id with append off — with it on, S10 already fails closed); a fixed
    ``value:``, an email ``format:`` or a field another step filled (``prefilled``) is not a
    blank the engine made.
    """
    if rows < 1:
        return
    present = set(columns)
    blanked: list[str] = []
    for tgt_field, raw_spec in field_map.items():
        if tgt_field not in IDENTITY_OUTPUT_FIELDS or tgt_field in prefilled:
            continue
        spec = ensure_field_mapping(raw_spec)
        if isinstance(spec, str):
            column = spec
        elif isinstance(spec, FieldTransform) or (isinstance(spec, FieldAppendYear) and not spec.append_year_to_id):
            column = spec.column
        else:
            continue
        # Exactly the engine's own spelling of the read (`str(spec).lower()` / `column.lower()`).
        if column.lower() not in present:
            blanked.append(f"{tgt_field} ← '{column.strip()}'")
    if blanked:
        # failure-policy: join_key
        record_note(
            context,
            entity,
            OutcomeNote.IDENTITY_FIELD_BLANKED,
            rows,
            log=logger,
            message=(
                f"[{entity}] IDENTITY FIELD BLANK — the source has no column for {blanked}, so "
                f"{'that field ships' if len(blanked) == 1 else 'those fields ship'} blank on all "
                f"{rows} row(s)."
            ),
        )
