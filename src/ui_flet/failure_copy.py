"""The ONE copy source for "what went wrong" — failure categories and per-entity outcomes.

Plan 0053 S3 (``docs/developer/failure-policy.md`` §6, §7, §8; policies P6, P7, P9). PURE
and COUNTED: no ``flet``, no I/O. Home (``home_status``), Run History (``run_history``) and
Convert (``convert_result`` + the ``on_error`` card) all word a failure through this module,
so a category reads the same on every surface (pinned by
``tests/test_ui_flet_failure_copy.py``).

Two closed vocabularies are humanised here:

* :data:`FAILED_CATEGORY_COPY` — one ``(headline, detail)`` per
  :class:`~src.etl.errors.RunErrorCategory` member except ``NONE`` (a completed run has no
  failure to word; asking for it raises). Only ``NO_INPUT`` and ``INPUT_UNREADABLE`` send
  the admin to the input folder — before this table existed, every Convert crash did,
  whatever the cause. :func:`failed_copy` appends ONE of two fixed tails chosen by ONE bool
  with ONE meaning on every surface — *this attempt was meant to reach SpacesEDU and did
  not* — which each surface passes from what it can PROVE (see :func:`failed_copy`);
  neither tail asserts anything about what SpacesEDU holds.
* :func:`outcome_sentence` — one sentence per valid ``(OutcomeKind, OutcomeReason)`` pair,
  and :func:`partial_copy`, the PARTIAL verdict's headline + detail for a run that
  completed without one or more of its files.

**Which outcomes warn** is decided here too, once: :func:`OUTCOME_TIER` (entity, kind, reason)
→ :class:`Verdict`, and :func:`warning_outcomes`, the PARTIAL predicate Home, Run History and
Convert all select through (plan 0053 S8, owner decision D5).

**PII floor (§8, P9).** Nothing here interpolates a path, a cell value, exception text or an
OBSERVED header. The variable text is an entity's phrase, taken from the authored
``humanize.SIZE_NOUNS`` vocabulary — an entity key that vocabulary does not know (a
hand-dropped YAML's invention) reads as :data:`UNKNOWN_ENTITY_PHRASE`, never echoed —
counts, and, since plan 0053 S7 (owner decision D4), an outcome's config-DECLARED
``labels`` / ``file_label``: names that passed ``outcomes.safe_label`` (a member of the
resolved config's own vocabulary, printable, at most ``outcomes.MAX_LABEL_LENGTH``
characters) when the run recorded them, and whose shape the total record reader re-checks.
No other value reaches a sentence. An outcome without labels reads exactly as before.

**Import direction.** This module imports ``humanize`` (the vocabulary), ``errors`` and
``outcomes``; it never imports ``home_status``, which imports it (the reason the entity
maps moved into ``humanize`` — pinned).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Final

from src.etl.errors import RunErrorCategory, classify_error_category
from src.etl.outcomes import MAY_BE_EMPTY, VALID_REASONS, EntityOutcome, OutcomeKind, OutcomeReason
from src.ui_flet.humanize import SIZE_NOUNS, pluralize
from src.ui_flet.verdict import Verdict

# --------------------------------------------------------------------------- #
# Entity phrases                                                               #
# --------------------------------------------------------------------------- #

UNKNOWN_ENTITY_PHRASE: Final = "one of your files"
"""The phrase for an entity key the authored vocabulary does not know — never the raw key."""


def entity_phrase(entity: object) -> str:
    """The plain noun phrase a sentence uses for ``entity`` ("family contacts") — TOTAL.

    The plural of ``humanize.SIZE_NOUNS`` — the countable-thing vocabulary — because a
    sentence ABOUT an entity names what it holds ("family contacts were left out"), not
    the CSV heading ``ENTITY_LABELS`` carries ("Family"). Anything the vocabulary does not
    know, including a non-string, is :data:`UNKNOWN_ENTITY_PHRASE`.
    """
    nouns = SIZE_NOUNS.get(entity) if isinstance(entity, str) else None
    return nouns[1] if nouns is not None else UNKNOWN_ENTITY_PHRASE


def _is_known(entity: object) -> bool:
    return isinstance(entity, str) and entity in SIZE_NOUNS


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _grammar(entity: object) -> dict[str, str]:
    """The agreement words for a sentence about ``entity``: every known phrase is plural."""
    if _is_known(entity):
        return {"Subject": _capitalized(entity_phrase(entity)), "were": "were", "their": "their", "them": "them"}
    return {"Subject": _capitalized(UNKNOWN_ENTITY_PHRASE), "were": "was", "their": "its", "them": "it"}


def _everything_else(*, delivered: bool) -> str:
    return "Everything else was delivered." if delivered else "Everything else completed."


# --------------------------------------------------------------------------- #
# Per-entity outcome sentences                                                 #
# --------------------------------------------------------------------------- #

# One template per VALID (kind, reason) pair — `outcomes.VALID_REASONS`, exactly (pinned). The
# placeholders are filled ONLY from `_grammar` + `_everything_else`, i.e. from authored words.
_OUTCOME_TEMPLATES: Final[Mapping[tuple[OutcomeKind, OutcomeReason], str]] = MappingProxyType(
    {
        (OutcomeKind.BUILT, OutcomeReason.NONE): "{Subject} {were} built.",
        (OutcomeKind.EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED): (
            "{Subject} {were} not built: this district's mapping names no export file for {them}."
        ),
        (OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY): (
            "{Subject} {were} not built: {their} export file was missing or empty."
        ),
        (OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM): (
            "{Subject} {were} not built: none of the rows in {their} export file could be used."
        ),
        # Plan 0053 S6: the NO_ROWS case the source observation saw ALONGSIDE a mapped column absent
        # from the export. It states the two facts together, never one as the cause of the other —
        # the observation proves co-occurrence only. The column is deliberately NOT named here
        # (config-declared labels in copy are S7, D4); only the entity phrase varies.
        (OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN): (
            "{Subject} {were} not built: none of {their} rows could be used, and {their} export "
            "file is missing a column this district's mapping reads."
        ),
        (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN): (
            "{Subject} {were} left out of this sync: {their} export file is missing a column this "
            "district's mapping needs — often because a different report was saved under the same "
            "name. {everything_else} Re-export that file and the next sync picks it up automatically."
        ),
        (OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR): (
            "{Subject} {were} left out of this sync: something went wrong while building {them}. "
            "{everything_else} The next sync tries again — if it keeps happening, the Help page has "
            "our support contact."
        ),
        (OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED): (
            "{Subject} {were} not built: the sync stopped before reaching {them}."
        ),
    }
)


# The label-aware variants (plan 0053 S7, D4): one per pair that can carry labels — exactly the
# `missing_source_column` pairs, since `EntityOutcome` refuses labels on any other reason (pinned).
# Each is its unlabelled twin above with the "a column" wording replaced by the NAMED column(s)
# and, when the outcome names one, the export file — the only values interpolated are those
# config-declared labels (see the module docstring's PII floor).
_LABELLED_TEMPLATES: Final[Mapping[tuple[OutcomeKind, OutcomeReason], str]] = MappingProxyType(
    {
        (OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN): (
            "{Subject} {were} not built: none of {their} rows could be used, and {their} export "
            "file{file_clause} is missing {columns_clause} this district's mapping reads."
        ),
        (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN): (
            "{Subject} {were} left out of this sync: {their} export file{file_clause} is missing "
            "{columns_clause} this district's mapping needs — often because a different report was "
            "saved under the same name. {everything_else} Re-export that file and the next sync "
            "picks it up automatically."
        ),
    }
)


def _label_clauses(outcome: EntityOutcome) -> dict[str, str]:
    """The two label slots: ``, <file>,`` (or nothing) and ``the column(s) “…”, which``."""
    quoted = [f"“{label}”" for label in outcome.labels]
    noun = "column" if len(quoted) == 1 else "columns"
    file_clause = f", {outcome.file_label}," if outcome.file_label else ""
    return {"file_clause": file_clause, "columns_clause": f"the {noun} {_joined(quoted)}, which"}


def outcome_sentence(outcome: EntityOutcome, *, delivered: bool) -> str:
    """One plain sentence for one entity's outcome — TOTAL over every valid (kind, reason).

    ``delivered`` says whether the rest of the run reached SpacesEDU; it words a FAILED
    outcome's "Everything else was delivered." / "Everything else completed." and is
    ignored by the other kinds. An :class:`EntityOutcome` cannot hold an invalid pair (its
    constructor refuses one), so the lookup cannot miss; the ``KeyError`` guard is the
    totality test's tripwire for a new enum member added without a template.

    **Label-aware (plan 0053 S7).** An outcome carrying config-declared ``labels`` reads its
    :data:`_LABELLED_TEMPLATES` variant, naming the column(s) — and the export file when the
    outcome names one; an outcome without labels reads its unlabelled template exactly as
    before (pinned byte-for-byte).
    """
    words = {**_grammar(outcome.entity), "everything_else": _everything_else(delivered=delivered)}
    if outcome.labels:
        return _LABELLED_TEMPLATES[(outcome.kind, outcome.reason)].format(**words, **_label_clauses(outcome))
    return _OUTCOME_TEMPLATES[(outcome.kind, outcome.reason)].format(**words)


# --------------------------------------------------------------------------- #
# Which outcomes warn (plan 0053 S8, owner decision D5)                        #
# --------------------------------------------------------------------------- #

# The EMPTY reasons that mean "there was nothing to send" — the only ones a `MAY_BE_EMPTY`
# entity may carry without a warning. The other two EMPTY reasons say the export HAD rows and
# none survived (every row filtered out, or a mapped column missing): a standing WARNING on any
# entity, because that is a district losing a file every night without anything having failed.
_NOTHING_TO_SEND: Final[frozenset[OutcomeReason]] = frozenset(
    {OutcomeReason.SOURCE_FILES_EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED}
)


def OUTCOME_TIER(entity: object, kind: OutcomeKind, reason: OutcomeReason) -> Verdict:
    """How much ONE outcome says about the run it belongs to — TOTAL over valid triples.

    A function of the entity AND the cause, not the kind alone (owner decision D5, plan 0053
    S8; ``docs/developer/failure-policy.md`` §7, the ``outcome-tier`` table, pinned):

    * **BUILT** → HEALTHY. A BUILT entity the source observation found a mapped column
      missing from (``missing_mapped``) still built rows; that is a log + record fact, never
      an amber (SD74 and Unity measured BUILT with a mapped column absent).
    * **FAILED** → WARNING, on any entity (S3 — the bulkhead left it out of a completed run).
    * **EMPTY** → WARNING on any entity for ``no_rows_after_transform`` and
      ``missing_source_column`` (the export had rows; none could be used). For
      ``source_files_empty`` / ``no_source_files_declared`` ("nothing to send") → HEALTHY
      ONLY for an entity in :data:`~src.etl.outcomes.MAY_BE_EMPTY`, WARNING for every other
      — an entity the district enabled whose export never arrives is a standing warning,
      and the remedy is a config change, never a quieter tier.
    * **NOT_RUN** → FAILED. It is only ever recorded inside a run that failed (every entity
      after a raising CRITICAL one, or all of them on a failure before the loop), whose
      FAILED status outranks PARTIAL anyway; FAILED says what such an outcome means.

    ``entity`` is compared by membership only (an unknown key is simply not a member, so it
    warns — never quieter by omission). A reason invalid for its ``kind`` raises
    ``ValueError`` — an :class:`EntityOutcome` can never hold one, so only a caller bug
    reaches it. Named in capitals because it is the plan's Naming-table name, cited by the
    standard and the parity tests; it is a pure function, never a table.
    """
    if reason not in VALID_REASONS.get(kind, frozenset()):
        raise ValueError(f"{reason!r} is not a valid reason for {kind!r}")
    if kind is OutcomeKind.BUILT:
        return Verdict.HEALTHY
    if kind is OutcomeKind.FAILED:
        return Verdict.WARNING
    if kind is OutcomeKind.NOT_RUN:
        return Verdict.FAILED
    # kind is EMPTY
    if reason in _NOTHING_TO_SEND and isinstance(entity, str) and entity in MAY_BE_EMPTY:
        return Verdict.HEALTHY
    return Verdict.WARNING


def warning_outcomes(outcomes: Iterable[EntityOutcome]) -> tuple[EntityOutcome, ...]:
    """The outcomes whose tier is WARNING — the ONE PARTIAL predicate (P7, D5), configured order.

    Home and Run History (``home_status.left_out_outcomes``) and Convert
    (``convert_result.summarize``) all select through this, so a run is PARTIAL on one
    surface exactly when it is on the others. A superset of
    :func:`~src.etl.outcomes.failed_entities` (every FAILED outcome warns — pinned); since S8
    it also holds each EMPTY outcome :func:`OUTCOME_TIER` rates WARNING. Only a
    success-shaped record reaches the question: a failed one is FAILED first.
    """
    return tuple(
        outcome for outcome in outcomes if OUTCOME_TIER(outcome.entity, outcome.kind, outcome.reason) is Verdict.WARNING
    )


# The next step a PARTIAL detail gives when its ONE warning outcome is an EMPTY one (plan 0053
# S8). The EMPTY sentences above state what happened and no next step (they were written while
# EMPTY was never surfaced); a WARNING band ends with one. One per EMPTY reason — TOTAL over
# `VALID_REASONS[EMPTY]` (pinned) — each saying what would change it, since the warning repeats
# every sync until something does. No slot, no path, no folder name.
_EMPTY_NEXT_STEP: Final[Mapping[OutcomeReason, str]] = MappingProxyType(
    {
        OutcomeReason.NO_SOURCE_FILES_DECLARED: (
            "If this file should be part of your sync, the Help page has our support contact."
        ),
        OutcomeReason.SOURCE_FILES_EMPTY: (
            "Re-export that file and the next sync picks it up automatically — if it keeps happening, "
            "the Help page has our support contact."
        ),
        OutcomeReason.NO_ROWS_AFTER_TRANSFORM: (
            "Check that export — if it keeps happening, the Help page has our support contact."
        ),
        OutcomeReason.MISSING_SOURCE_COLUMN: (
            "Re-export that file with the column, or — if your export names it differently — the Help "
            "page has our support contact."
        ),
    }
)


# The EMPTY reasons a re-export can cure. The multi-entity PARTIAL detail says "Re-export
# those files" only when EVERY left-out outcome is FAILED or EMPTY for one of these (plan 0053
# S8): an undeclared source or a file whose rows were all filtered out is not fixed by
# re-exporting, so a mix falls back to the neutral step — the same distinction
# `_EMPTY_NEXT_STEP` draws for one entity.
_REEXPORT_CURES: Final[frozenset[OutcomeReason]] = frozenset(
    {OutcomeReason.SOURCE_FILES_EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN}
)
_MULTI_REEXPORT_STEP: Final = (
    "Re-export those files and the next sync picks them up automatically — if it keeps happening, "
    "the Help page has our support contact."
)
_MULTI_NEUTRAL_STEP: Final = "If these files should be part of your sync, the Help page has our support contact."


def _reexport_cures(outcome: EntityOutcome) -> bool:
    return outcome.kind is OutcomeKind.FAILED or (
        outcome.kind is OutcomeKind.EMPTY and outcome.reason in _REEXPORT_CURES
    )


def _joined(phrases: Sequence[str]) -> str:
    if len(phrases) == 1:
        return phrases[0]
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


def partial_copy(failed: Sequence[EntityOutcome], *, delivered: bool) -> tuple[str, str]:
    """The PARTIAL verdict's ``(headline, detail)``: a completed run left files out.

    ``failed`` is :func:`warning_outcomes` of the run's outcomes (FAILED, and since plan
    0053 S8 an EMPTY outcome that warns) and must not be empty (a run with nothing left out
    is not partial — asking is a caller bug, so it raises). One entity → its own
    :func:`outcome_sentence` — for an EMPTY one followed by the "everything else" sentence
    and that reason's next step, since an EMPTY sentence states no next step of its own;
    several → one sentence naming them all (unknown keys counted as "other files" — or, when
    nothing known precedes the count, as "N of your files" — never echoed) and ending "Re-export
    those files" only when a re-export can cure every one of them, else a neutral step. Home, Run
    History and Convert render exactly this pair.
    """
    if not failed:
        raise ValueError("partial_copy needs at least one entity that was left out")
    phrase = entity_phrase(failed[0].entity) if len(failed) == 1 else f"{len(failed)} of your files"
    headline = f"Your roster synced without {phrase}" if delivered else f"Your sync completed without {phrase}"
    if len(failed) == 1:
        only = failed[0]
        sentence = outcome_sentence(only, delivered=delivered)
        if only.kind is OutcomeKind.EMPTY:
            sentence = f"{sentence} {_everything_else(delivered=delivered)} {_EMPTY_NEXT_STEP[only.reason]}"
        return headline, sentence

    known: list[str] = []
    for outcome in failed:
        if _is_known(outcome.entity) and entity_phrase(outcome.entity) not in known:
            known.append(entity_phrase(outcome.entity))
    unknown = sum(1 for outcome in failed if not _is_known(outcome.entity))
    if unknown and known:
        known.append(f"{unknown} other {pluralize('file', unknown)}")
    elif unknown:
        # Nothing named precedes the count, so "other" would refer to nothing: every key was
        # unknown (a hand-dropped YAML's inventions) and none may be echoed (P9).
        known.append(f"{unknown} of your files")
    step = _MULTI_REEXPORT_STEP if all(_reexport_cures(outcome) for outcome in failed) else _MULTI_NEUTRAL_STEP
    detail = (
        f"{_capitalized(_joined(known))} were left out of this sync. {_everything_else(delivered=delivered)} {step}"
    )
    return headline, detail


def data_warnings_clause(total: int) -> str:
    """The second sentence a PARTIAL detail carries when the run ALSO had data warnings.

    ``""`` when there were none, so a caller appends it unconditionally. The count is the
    record's ``data_errors.total`` — a safe scalar.
    """
    if total <= 0:
        return ""
    verb = "was" if total == 1 else "were"
    return f"There {verb} also {total:,} data {pluralize('warning', total)}: some records had field problems and were left blank."


# --------------------------------------------------------------------------- #
# Failure categories                                                           #
# --------------------------------------------------------------------------- #

# (headline, detail) per category. Surface-neutral ("the sync", "try again") because Home,
# Run History and Convert all render the SAME detail. Every detail names a cause and ends with
# a next step; none carries an interpolation slot (no `{`, pinned). Only NO_INPUT and
# INPUT_UNREADABLE mention the input folder (pinned) — that misdirection is what this replaced.
FAILED_CATEGORY_COPY: Final[Mapping[RunErrorCategory, tuple[str, str]]] = MappingProxyType(
    {
        RunErrorCategory.NO_INPUT: (
            "No files could be read",
            "We couldn't read any MyEd BC extract files from the input folder. Check that the input "
            "folder holds this district's extract files, then try again.",
        ),
        RunErrorCategory.INPUT_UNREADABLE: (
            "An export file couldn't be read",
            "One of the MyEd BC extract files in the input folder couldn't be read — it may be damaged, "
            "incomplete, or share its name with another file. Re-export it into the input folder, then "
            "try again.",
        ),
        RunErrorCategory.SOURCE_SCHEMA: (
            "An export file is missing a column",
            "One of your MyEd BC extract files is missing a column this district's mapping needs — often "
            "because a different report was saved under the same name. Re-export that file, then try again.",
        ),
        RunErrorCategory.CONFIG: (
            "The district mapping couldn't be read",
            "The mapping for this district couldn't be loaded, so no roster could be built. Check that "
            "the right district is chosen — if it keeps failing, the Help page has our support contact.",
        ),
        RunErrorCategory.DATA: (
            "The roster couldn't be built",
            "Something in this district's data stopped the roster from being built. Try again — if it "
            "keeps failing, the Help page has our support contact.",
        ),
        # Plan 0050, moved here with the copy. ONE detail serves TWO paths — the pre-flight
        # refusal (nothing ran) and the write-time ``OSError`` (the conversion ran, only the save
        # failed) — so the tail says "nothing NEW was saved", true on both. It deliberately does
        # NOT promise "your existing files were not changed": the rollback is BEST-EFFORT
        # (``_commit_staged`` logs a failed restore rather than raising, and on a drive that drops
        # mid-commit the restore fails on the same dead path). The network/shared-folder clause
        # is COACHING, not detection — nothing here inspects a path. The folder itself is named by
        # Convert's caption (``convert_output.resolved_output_caption``), never by this copy.
        RunErrorCategory.OUTPUT: (
            "We couldn't save to your output folder",
            "DistrictSync couldn't write to your output folder. Check the output folder in Settings — "
            "if it's on a network drive or a shared folder, make sure you can still open it. Then try "
            "again; if it keeps failing, the Help page has our support contact.",
        ),
        RunErrorCategory.NO_OUTPUT: (
            "No output was produced",
            "The export files were read, but no roster files came out of them. Check that the right "
            "district is selected, then try again.",
        ),
        RunErrorCategory.INCOMPLETE_ROSTER: (
            "Your student list came through empty",
            "The other roster files were built, but with no students they would point at people "
            "SpacesEDU has never seen, so DistrictSync stopped. Check this district's student export, "
            "then try again.",
        ),
        RunErrorCategory.UNKNOWN: (
            "The roster couldn't be built",
            "Something unexpected stopped the roster from being built. Try again — if it keeps failing, "
            "the Help page has our support contact.",
        ),
    }
)

#: The category every unreadable, absent or impossible stored value falls back to.
FALLBACK_CATEGORY: Final = RunErrorCategory.UNKNOWN

# The two tails — ONE is appended, chosen by `failed_copy`'s `delivery_requested` (its docstring
# says what the bool means and what each surface passes). Neither says what SpacesEDU holds. "Nothing NEW was saved" (not "your files were not changed") because
# the write's rollback is best-effort (the ROADMAP "Three sites promise …" item; plan 0050).
NOTHING_SENT_TAIL: Final = "Nothing was sent to SpacesEDU this time."
NOTHING_SAVED_TAIL: Final = "Nothing new was saved to your output folder."


def failed_copy(category: RunErrorCategory, *, delivery_requested: bool) -> tuple[str, str]:
    """``(headline, detail + tail)`` for a failure of ``category`` — the ONE composition.

    ``NONE`` raises: a completed run has no failure copy, and wording one would be a bug.
    ``delivery_requested`` is REQUIRED keyword-only — the tail is a claim about what did
    NOT happen, and a default would let a caller make the wrong one (pinned by signature).

    **One meaning on every surface:** ``True`` says *this attempt was meant to reach
    SpacesEDU and did not* → :data:`NOTHING_SENT_TAIL`; ``False`` → :data:`NOTHING_SAVED_TAIL`.
    Each surface passes what it can PROVE, never more:

    * **Convert** (``convert_result.summarize`` and the ``on_error`` card) passes the admin's
      REQUEST (``ConvertResult.delivery_requested`` / ``sftp_requested``): every path that
      reaches this copy stopped BEFORE the upload (a failed upload is ``BUILT_NOT_DELIVERED``,
      a result of its own), so "requested" implies "not sent".
    * **A run record** (Home, Run History — ``home_status.failed_detail``) does not store the
      request, and ``sftp_attempted`` is set only AFTER the committed write, so it passes
      ``sftp_attempted and not sftp_ok`` — the one shape that proves nothing was sent. A
      nightly with delivery configured that fails in the ETL therefore reads the SAVED tail
      (true), and a record whose upload SUCCEEDED before a later step raised never claims
      "nothing was sent".
    """
    if category is RunErrorCategory.NONE:
        raise ValueError("a completed run (category 'none') has no failure copy")
    headline, detail = FAILED_CATEGORY_COPY[category]
    tail = NOTHING_SENT_TAIL if delivery_requested else NOTHING_SAVED_TAIL
    return headline, f"{detail} {tail}"


def failed_copy_for(value: object, *, delivery_requested: bool) -> tuple[str, str]:
    """:func:`failed_copy` for a STORED category value — TOTAL (a run record is untrusted data).

    Absent, blank, unknown to this build, or ``none`` on a failed record → the
    :data:`FALLBACK_CATEGORY` copy. Never raises, never echoes the value.
    """
    try:
        category = RunErrorCategory(value)
    except (TypeError, ValueError):
        category = FALLBACK_CATEGORY
    if category is RunErrorCategory.NONE:
        category = FALLBACK_CATEGORY
    return failed_copy(category, delivery_requested=delivery_requested)


def error_card_copy(exc: BaseException, *, delivery_requested: bool) -> tuple[str, str]:
    """Convert's ``on_error`` card for a raised failure — classified by TYPE, never message.

    Replaces the retired ``convert_result.convert_error_copy``, which told the admin to
    check the input folder whatever went wrong. ``str(exc)`` is never read: the exception
    goes to :func:`~src.etl.errors.classify_error_category` (``isinstance`` only) and the
    card is the category's copy — so a card and a run record for the same fault agree.
    """
    return failed_copy_for(classify_error_category(exc), delivery_requested=delivery_requested)
