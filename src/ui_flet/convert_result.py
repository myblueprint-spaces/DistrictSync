"""Pure Convert-result model + verdict mapping — the trust core of the Convert surface.

NO ``flet`` import. A ``convert_job`` run (see ``screens/convert.py``) produces a
``ConvertResult`` — a **PII-free** structured summary of what happened (status +
entity counts + data-error/anomaly counts + SFTP booleans + the quality text) —
and ``summarize`` maps it, TOTAL, to the DS-1 verdict vocabulary (a ``Verdict`` +
a plain-language headline + supporting detail). ``screens/convert.py`` renders
that already-tested output verdict-first.

**No DataFrames (privacy — LIVE/top):** ``ConvertResult`` holds only counts and
plain strings — never a transformed frame — so a PII-bearing roster row can never
leak into a summary object or a headline. The view keeps any transient frames it
needs OUTSIDE this pure summary.

**Privacy in ``summarize`` (mirrors ``home_status``):** the raw ``anomalies``
strings can carry an entity name (``"Students dropped from …"``) and the raw
input path / ``sis_type`` / column names live only in the log — NONE of them is
interpolated into the admin-facing ``headline``/``detail``. Faults are named by
CATEGORY only; counts (entity/warning/anomaly totals) are safe scalars, never
identifiers. The one exception is ``failure_copy``'s: a left-out entity's config-declared
file and column labels (plan 0053 S7, D4 — validated by ``outcomes.safe_label`` when the run
recorded them), never an observed header.

Reuses IA-3's verdict spine (``Verdict`` + ``home_status``'s voice, esp. the
exit-3 "built but didn't reach SpacesEDU" headline) so setup / health / convert
feedback read consistently. The one Convert-specific addition is the transient
``NEEDS_ANOMALY_ACK`` state (Home never needs an acknowledgment).

**One copy source for failures (plan 0053 S3).** Every FAILED status that is a run-failure
CATEGORY (no input, no output, an empty student list, an unusable output folder) reads its
words from ``failure_copy.FAILED_CATEGORY_COPY`` — the table Home and Run History read — and a
raised failure's ``on_error`` card is ``failure_copy.error_card_copy(exc)``, which replaced the
retired ``convert_error_copy`` (it sent the admin to the input folder whatever went wrong). A
success-shaped result whose outcomes show an entity that warns (``failure_copy.warning_outcomes``:
FAILED, or since plan 0053 S8 an EMPTY outcome that warns) is the PARTIAL WARNING, worded by
``failure_copy.partial_copy`` exactly as Home words it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.etl.errors import RunErrorCategory
from src.etl.outcomes import EntityOutcome
from src.ui_flet.failure_copy import data_warnings_clause, failed_copy, partial_copy, warning_outcomes
from src.ui_flet.humanize import AnomalyVariant, friendly_anomaly_detail, pluralize
from src.ui_flet.verdict import Verdict


class ConvertStatus(str, Enum):
    """The distinct outcomes of a Convert run (the axis ``summarize`` maps)."""

    DELIVERED = "delivered"  # ETL ok + (SFTP ok OR not requested)
    DELIVERED_WITH_DATA_ERRORS = "delivered_with_data_errors"  # ETL ok + delivered, but per-row errors present
    DELIVERED_FROM_DISK = "delivered_from_disk"  # deliver-from-disk succeeded (no build this action)
    BUILT_NOT_DELIVERED = "built_not_delivered"  # ETL ok, SFTP attempted + failed (exit-3 shape)
    BUILT_WITH_DATA_ERRORS = "built_with_data_errors"  # ETL ok, per-row transform errors present
    NEEDS_ANOMALY_ACK = "needs_anomaly_ack"  # >20% drop — write withheld pending acknowledgment
    NO_INPUT = "no_input"  # nothing could be read from the picked folder
    NO_OUTPUT = "no_output"  # transform produced no entities
    INCOMPLETE_ROSTER = "incomplete_roster"  # other entities built, the roster anchor did not — refused
    OUTPUT_FOLDER_UNUSABLE = "output_folder_unusable"  # output folder unreachable/unwritable (0050)


@dataclass(frozen=True)
class ConvertResult:
    """A PII-free structured summary of a Convert run.

    Holds only counts + plain strings + booleans — **never a DataFrame** — so a
    roster row can never leak into a summary. ``anomalies`` carries the plain
    ``compute_anomalies`` strings (an entity name at most, never PII); ``summarize``
    never surfaces them verbatim.

    Attributes:
        status: the ``ConvertStatus`` this run resolved to.
        entity_counts: per-entity output row counts (safe scalars).
        data_errors_total: total non-fatal per-row transform errors recorded.
        anomalies: the plain per-entity >20%-drop warning strings (log/detail only).
        sftp_attempted: whether an SFTP delivery was attempted this run.
        sftp_ok: whether that delivery succeeded (only meaningful if attempted).
        quality_text: the ``DataQualityReport`` text for a collapsible (may be "").
        entity_outcomes: the run's per-entity outcomes (plan 0053 S2) — one
            ``EntityOutcome`` per configured entity, in configured order — or ``None``,
            which says explicitly that NO outcome ledger existed for this result: the
            output-folder pre-flight refused before one was built, or the result is a
            delivery from disk (a delivery is not a build). REQUIRED keyword-only with
            no default, so no construction site can omit it by accident. Read by
            ``summarize`` since plan 0053 S3: an outcome that warns (a FAILED entity, or since
            S8 an EMPTY one that warns) on a success-shaped status is the PARTIAL WARNING.
        delivery_requested: whether the admin asked for this run to be delivered to
            SpacesEDU (convert_job's ``sftp_requested``; always ``True`` for a delivery from
            disk). It picks a FAILED status's closing line (plan 0053 S3): every FAILED
            status stops BEFORE the upload, so "requested" means "nothing was sent" — the
            same tail the ``on_error`` card gives a raised failure of the same run.
            REQUIRED keyword-only with no default: the tail is a claim about what did NOT
            happen. ``sftp_attempted`` without it is refused — an attempt nobody asked for
            is a state no path produces.
    """

    status: ConvertStatus
    entity_counts: dict[str, int] = field(default_factory=dict)
    data_errors_total: int = 0
    anomalies: tuple[str, ...] = ()
    sftp_attempted: bool = False
    sftp_ok: bool = False
    quality_text: str = ""
    # `kw_only`: every field above has a default, and a required positional field after
    # defaulted ones is a `TypeError` at class definition.
    entity_outcomes: tuple[EntityOutcome, ...] | None = field(kw_only=True)
    delivery_requested: bool = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.sftp_attempted and not self.delivery_requested:
            raise ValueError("a delivery was attempted that nobody requested")


def summarize(result: ConvertResult) -> tuple[Verdict, str, str]:
    """Map a ``ConvertResult`` to ``(Verdict, headline, detail)`` — pure, TOTAL, PII-safe.

    Every ``ConvertStatus`` has an explicit branch; the trailing ``raise`` is a
    programming-error guard surfaced loudly by the totality test, never reached at
    runtime. NEVER interpolates a raw path / ``sis_type`` / column name / raw
    anomaly string into the copy — faults are named by CATEGORY; only safe count
    scalars (and ``failure_copy``'s authored entity phrases and validated, config-declared
    outcome labels — plan 0053 S7) appear.

    **PARTIAL (plan 0053 S3).** A success-shaped status (``DELIVERED``,
    ``DELIVERED_WITH_DATA_ERRORS``, ``BUILT_WITH_DATA_ERRORS``) whose ``entity_outcomes``
    show an outcome that warns (``failure_copy.warning_outcomes`` — a FAILED entity, or since
    S8 an EMPTY one whose tier is WARNING, D5) is a WARNING worded by
    ``failure_copy.partial_copy`` — the same
    headline and detail Home shows for that run — with the data-warning count as a second
    sentence. Every FAILED status, ``BUILT_NOT_DELIVERED`` and the anomaly gate keep their
    precedence: a partial build that also failed to upload is still a delivery failure. The
    anomaly gate's prompt carries the same not-built sentence after its own detail (plan
    0053 S4), because a left-out file is usually the file that vanished.
    """
    status = result.status

    left_out = warning_outcomes(result.entity_outcomes or ())
    if left_out and status in _SUCCESS_SHAPED:
        headline, detail = partial_copy(left_out, delivered=result.sftp_attempted and result.sftp_ok)
        clause = data_warnings_clause(result.data_errors_total)
        return Verdict.WARNING, headline, f"{detail} {clause}" if clause else detail

    if status is ConvertStatus.DELIVERED:
        if result.sftp_attempted:
            return (
                Verdict.HEALTHY,
                "Roster converted and delivered to SpacesEDU",
                "Your roster was built and delivered successfully.",
            )
        return (
            Verdict.HEALTHY,
            "Roster converted",
            "Your roster was built successfully and written to the output folder.",
        )

    if status is ConvertStatus.DELIVERED_WITH_DATA_ERRORS:
        # Delivered, but data errors are a SEPARATE axis that must stay visible even on a
        # successful delivery (fail-loud; mirrors home_status's delivered-with-warnings verdict).
        total = result.data_errors_total
        warning_word = pluralize("warning", total)
        return (
            Verdict.WARNING,
            f"Delivered to SpacesEDU with {total} data {warning_word}",
            "A few records had field problems and were left blank. The rest of the roster was built, saved, and delivered.",
        )

    if status is ConvertStatus.DELIVERED_FROM_DISK:
        # Deliver-from-disk (0034 Slice 2): nothing was rebuilt — the copy must not claim
        # a conversion happened, only that the already-saved files reached SpacesEDU.
        return (
            Verdict.HEALTHY,
            "Files delivered to SpacesEDU",
            "The files in your output folder were sent to SpacesEDU successfully.",
        )

    if status is ConvertStatus.BUILT_NOT_DELIVERED:
        return (
            Verdict.FAILED,
            "Your roster was built but didn't reach SpacesEDU",
            "The data was built and saved, but the upload failed. Your files are safe — you can try delivering again.",
        )

    if status is ConvertStatus.BUILT_WITH_DATA_ERRORS:
        total = result.data_errors_total
        warning_word = pluralize("warning", total)
        return (
            Verdict.WARNING,
            f"Converted with {total} data {warning_word}",
            "A few records had field problems and were left blank — the rest of the roster was built and saved.",
        )

    if status is ConvertStatus.NEEDS_ANOMALY_ACK:
        count = len(result.anomalies)
        detail = friendly_anomaly_detail(count, variant=AnomalyVariant.CONVERT)
        if left_out:
            # Plan 0053 S4: a file the bulkhead left out VANISHES from the output, so it is often
            # the very anomaly this prompt asks about. Name WHY it is missing — the same
            # sentence the PARTIAL verdict gives (nothing is written or sent yet, hence
            # ``delivered=False``) — so "convert anyway" is consent to a known cause.
            detail = f"{detail} {partial_copy(left_out, delivered=False)[1]}"
        return (
            Verdict.WARNING,
            "Some files look much smaller than usual",
            detail,
        )

    if status in _FAILED_STATUS_CATEGORIES:
        # A run-failure CATEGORY: the words are ``failure_copy``'s (plan 0053 S3), so this card and
        # Home / Run History say the same thing about the same fault. INCOMPLETE_ROSTER is the
        # way-OUT delivery gate's refusal (``etl.pipeline.check_delivery_integrity``);
        # OUTPUT_FOLDER_UNUSABLE is plan 0050's (its reasoning sits beside the OUTPUT copy). Every
        # branch names WHAT was wrong — never a path, a column, a district id or a student value.
        headline, detail = failed_copy(_FAILED_STATUS_CATEGORIES[status], delivery_requested=result.delivery_requested)
        return Verdict.FAILED, headline, detail

    raise ValueError(f"Unmapped ConvertStatus: {status!r}")  # pragma: no cover - totality guard


# The success-shaped statuses an outcome that warns turns into the PARTIAL warning (plan 0053 S3/S8).
# DELIVERED_FROM_DISK is not one: a delivery is not a build and carries no outcomes.
_SUCCESS_SHAPED: frozenset[ConvertStatus] = frozenset(
    {
        ConvertStatus.DELIVERED,
        ConvertStatus.DELIVERED_WITH_DATA_ERRORS,
        ConvertStatus.BUILT_WITH_DATA_ERRORS,
    }
)

# The FAILED statuses that ARE a run-failure category → that category, whose copy
# (``failure_copy.FAILED_CATEGORY_COPY``) is the ONE wording on every surface.
_FAILED_STATUS_CATEGORIES: dict[ConvertStatus, RunErrorCategory] = {
    ConvertStatus.NO_INPUT: RunErrorCategory.NO_INPUT,
    ConvertStatus.NO_OUTPUT: RunErrorCategory.NO_OUTPUT,
    ConvertStatus.INCOMPLETE_ROSTER: RunErrorCategory.INCOMPLETE_ROSTER,
    ConvertStatus.OUTPUT_FOLDER_UNUSABLE: RunErrorCategory.OUTPUT,
}


# The way-OUT delivery gate's bounded faults → the Convert status that words each one.
# The GATE itself is single-sourced in ``etl.pipeline.check_delivery_integrity`` (the CLI and
# the desktop path both call it); this table is only the UI's presentation of its verdict, so
# the categories are imported from the pipeline's taxonomy rather than respelled here.
_INTEGRITY_FAULT_STATUSES: dict[str, ConvertStatus] = {
    RunErrorCategory.NO_OUTPUT.value: ConvertStatus.NO_OUTPUT,
    RunErrorCategory.INCOMPLETE_ROSTER.value: ConvertStatus.INCOMPLETE_ROSTER,
}


def status_for_integrity_fault(category: str) -> ConvertStatus:
    """Map a :class:`~src.etl.pipeline.DeliveryIntegrityError`'s bounded category to a status.

    FAILS LOUD on an unmapped category rather than defaulting: a new gate fault silently
    presented as "no output was produced" would be a lie about why delivery was refused, and
    the raise lands on the Convert screen's calm ``on_error`` card with nothing written
    either way. The category is a closed-set enum value — never free text, never PII — so it
    is safe in the exception message the diagnostic log receives.
    """
    try:
        return _INTEGRITY_FAULT_STATUSES[category]
    except KeyError:
        raise ValueError(f"No Convert status is mapped for delivery-integrity category {category!r}") from None


def deliver_error_copy() -> tuple[str, str]:
    """The (headline, detail) for the deliver pre-flight ``on_error`` card — fixed, no dead end.

    Reached only when ``deliver_job`` fails BEFORE the upload begins (an unset output
    folder — a gate/programming error surfaced loudly). Bounded fixed copy, zero-arg (nothing
    to leak), and a concrete next step (the output folder lives in Settings; Help carries the
    support path). The BUILD path's card is ``failure_copy.error_card_copy`` (plan 0053 S3).
    """
    return (
        "The delivery couldn't start",
        "Something went wrong before the upload began. Your files were not changed. "
        "Check your output folder in Settings, then try again — if it keeps failing, "
        "the Help page has our support contact.",
    )
