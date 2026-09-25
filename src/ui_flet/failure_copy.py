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

**PII floor (§8, P9).** Nothing here interpolates a filename, a column, a path, a cell
value or exception text. The ONLY variable text is an entity's phrase, taken from the
authored ``humanize.SIZE_NOUNS`` vocabulary — an entity key that vocabulary does not know
(a hand-dropped YAML's invention) reads as :data:`UNKNOWN_ENTITY_PHRASE`, never echoed —
and counts. Config-declared file and column labels are plan 0053 S7 (D4), not this module.

**Import direction.** This module imports ``humanize`` (the vocabulary), ``errors`` and
``outcomes``; it never imports ``home_status``, which imports it (the reason the entity
maps moved into ``humanize`` — pinned).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from src.etl.errors import RunErrorCategory, classify_error_category
from src.etl.outcomes import EntityOutcome, OutcomeKind, OutcomeReason
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


def outcome_sentence(outcome: EntityOutcome, *, delivered: bool) -> str:
    """One plain sentence for one entity's outcome — TOTAL over every valid (kind, reason).

    ``delivered`` says whether the rest of the run reached SpacesEDU; it words a FAILED
    outcome's "Everything else was delivered." / "Everything else completed." and is
    ignored by the other kinds. An :class:`EntityOutcome` cannot hold an invalid pair (its
    constructor refuses one), so the lookup cannot miss; the ``KeyError`` guard is the
    totality test's tripwire for a new enum member added without a template.
    """
    template = _OUTCOME_TEMPLATES[(outcome.kind, outcome.reason)]
    return template.format(**_grammar(outcome.entity), everything_else=_everything_else(delivered=delivered))


# How much one outcome, on its own, says about the run it belongs to. S3's rule is the kind
# alone: a FAILED entity in a completed run is the PARTIAL WARNING (it pairs exactly with
# `outcomes.failed_entities` — pinned); EMPTY is per-entity skip-on-empty, not a fault; NOT_RUN
# only exists inside a run that failed. Plan 0053 S8 refines EMPTY by entity and reason (D5).
OUTCOME_TIER: Final[Mapping[OutcomeKind, Verdict]] = MappingProxyType(
    {
        OutcomeKind.BUILT: Verdict.HEALTHY,
        OutcomeKind.EMPTY: Verdict.HEALTHY,
        OutcomeKind.FAILED: Verdict.WARNING,
        OutcomeKind.NOT_RUN: Verdict.FAILED,
    }
)


def _joined(phrases: Sequence[str]) -> str:
    if len(phrases) == 1:
        return phrases[0]
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


def partial_copy(failed: Sequence[EntityOutcome], *, delivered: bool) -> tuple[str, str]:
    """The PARTIAL verdict's ``(headline, detail)``: a completed run left files out.

    ``failed`` is :func:`~src.etl.outcomes.failed_entities` of the run's outcomes and must
    not be empty (a run with nothing left out is not partial — asking is a caller bug, so it
    raises). One entity → its own :func:`outcome_sentence`; several → one sentence naming
    them all (unknown keys counted as "other files" — or, when nothing known precedes the
    count, as "N of your files" — never echoed). Home, Run History and Convert render
    exactly this pair.
    """
    if not failed:
        raise ValueError("partial_copy needs at least one entity that was left out")
    phrase = entity_phrase(failed[0].entity) if len(failed) == 1 else f"{len(failed)} of your files"
    headline = f"Your roster synced without {phrase}" if delivered else f"Your sync completed without {phrase}"
    if len(failed) == 1:
        return headline, outcome_sentence(failed[0], delivered=delivered)

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
    detail = (
        f"{_capitalized(_joined(known))} were left out of this sync. {_everything_else(delivered=delivered)} "
        "Re-export those files and the next sync picks them up automatically — if it keeps happening, "
        "the Help page has our support contact."
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
