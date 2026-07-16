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
identifiers.

Reuses IA-3's verdict spine (``Verdict`` + ``home_status``'s voice, esp. the
exit-3 "built but didn't reach SpacesEDU" headline) so setup / health / convert
feedback read consistently. The one Convert-specific addition is the transient
``NEEDS_ANOMALY_ACK`` state (Home never needs an acknowledgment).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

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
    """

    status: ConvertStatus
    entity_counts: dict[str, int] = field(default_factory=dict)
    data_errors_total: int = 0
    anomalies: tuple[str, ...] = ()
    sftp_attempted: bool = False
    sftp_ok: bool = False
    quality_text: str = ""


def summarize(result: ConvertResult) -> tuple[Verdict, str, str]:
    """Map a ``ConvertResult`` to ``(Verdict, headline, detail)`` — pure, TOTAL, PII-safe.

    Every ``ConvertStatus`` has an explicit branch; the trailing ``raise`` is a
    programming-error guard surfaced loudly by the totality test, never reached at
    runtime. NEVER interpolates a raw path / ``sis_type`` / column name / raw
    anomaly string into the copy — faults are named by CATEGORY; only safe count
    scalars appear.
    """
    status = result.status

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
        return (
            Verdict.WARNING,
            "Some files look much smaller than usual",
            friendly_anomaly_detail(count, variant=AnomalyVariant.CONVERT),
        )

    if status is ConvertStatus.NO_INPUT:
        return (
            Verdict.FAILED,
            "No files could be read",
            "We couldn't read any MyEd BC extract files from the folder you chose. Check the folder and try again.",
        )

    if status is ConvertStatus.NO_OUTPUT:
        return (
            Verdict.FAILED,
            "No output was produced",
            "The conversion ran but produced no roster files. Check that the right district is selected.",
        )

    raise ValueError(f"Unmapped ConvertStatus: {status!r}")  # pragma: no cover - totality guard


def convert_error_copy() -> tuple[str, str]:
    """The (headline, detail) for Convert's generic ``on_error`` card — fixed, no dead end.

    0035 W3b (T1 #2): a mid-build failure routed to ``on_error`` surfaces a BOUNDED
    category message only — the raw exception (which may carry a path / column name)
    stays in the log, NEVER in the banner. Zero-arg by design: nothing can be
    interpolated, so nothing can leak. The copy ends with a concrete next step
    (check the input folder → try again → the Help page's support path) so a failure
    is never a dead end.
    """
    return (
        "The conversion couldn't finish",
        "Something went wrong while building your roster. Your existing files were not changed. "
        "Check that your input folder holds this district's MyEd BC extract files, then try "
        "again — if it keeps failing, the Help page has our support contact.",
    )


def deliver_error_copy() -> tuple[str, str]:
    """The (headline, detail) for the deliver pre-flight ``on_error`` card — fixed, no dead end.

    Reached only when ``deliver_job`` fails BEFORE the upload begins (an unset output
    folder — a gate/programming error surfaced loudly). Same contract as
    :func:`convert_error_copy`: bounded fixed copy, zero-arg (nothing to leak), and a
    concrete next step (the output folder lives in Settings; Help carries the support path).
    """
    return (
        "The delivery couldn't start",
        "Something went wrong before the upload began. Your files were not changed. "
        "Check your output folder in Settings, then try again — if it keeps failing, "
        "the Help page has our support contact.",
    )
