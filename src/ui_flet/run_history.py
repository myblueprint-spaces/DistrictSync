"""Pure Run-History derivation — the read-only "has the sync been running, and did each work?" core.

NO ``flet`` import (mirrors ``home_status``/``convert_result``). Given the run records
(newest-first, from ``history.store.read_run_records``) + the ``AppConfig`` state, this module
derives two PII-free things the Run History view renders:

- **the verdict-first banner** — ``derive_history_banner(records, app_config, *, now=None)`` → a
  ``HistoryBanner`` (a ``Verdict`` + a plain-language headline + detail). It answers the same
  "is my sync OK?" question Home does, over the SAME latest record — so it classifies through
  ``home_status.classify_latest_reason`` + ``verdict_for_reason`` (the single-source status→verdict
  precedence) and reuses ``home_status.is_stale`` (the landed staleness), keeping Home and Run
  History from ever drifting. Graceful degradation is a first-class OUTPUT: ``None`` → a calm
  "history unavailable" WARNING (never a raise); ``[]`` → "no runs yet" WARNING (never red).
- **the per-run display rows** — ``to_run_row(record, *, now=None, active_sis=None)`` → a total,
  PII-free ``RunRow`` (plain time, a category-only ``status_label`` + its ``Verdict``, entity
  counts, an SFTP enum, a warnings count, a plain duration, a bounded run-origin ``source`` label,
  an optional different-district note) and ``to_run_rows(records)`` over the newest-first list.

**Privacy (LIVE/top):** the record's free-text ``error`` (``str(e)`` in the emitter — path /
``sis_type`` / column risk) and the raw ``ANOMALY:``-prefixed strings are **NEVER** read into a
``RunRow`` field or a banner headline/detail. ``RunRow`` carries **no** ``error`` field at all, so
a future view edit cannot render one; faults are named by CATEGORY, counts are safe scalars. This
is the concrete fix for the Streamlit page's raw-``error`` column + log-path caption (dropped).
The ONE deliberate identity fact surfaced (0034 Slice 4) is the DISTRICT: when a record's
``sis_type`` differs from the active district, ``district_note`` carries the friendly district
display (a bounded config id / display name — never a path, never the free-text error).

**Totality:** every field is read via ``.get`` + ``_as_int`` (reused from ``home_status``), so a
partial/old record yields a safe ``RunRow`` (missing timestamp → "recently"; missing counts → 0;
missing/absent ``status`` → treated as non-success → "Failed", the honest fail-safe default) —
never a ``KeyError``. ``derive_history_banner`` is total over ``None``/``[]``/malformed-latest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.config.app_config import AppConfig
from src.ui_flet.home_status import (
    _MYBLUEPRINT_ENTITIES,
    _ROSTERING_ENTITIES,
    LatestReason,
    _as_int,
    _data_errors_total,
    _schedule_confirmed_missing,
    _schedule_is_live,
    classify_latest_reason,
    is_stale,
    verdict_for_reason,
)
from src.ui_flet.humanize import (
    AnomalyVariant,
    friendly_anomaly_detail,
    friendly_district_name,
    friendly_timestamp,
    pluralize,
)
from src.ui_flet.schedule_status import ScheduleStatus
from src.ui_flet.verdict import Verdict


class SftpDelivery(Enum):
    """The SFTP-delivery axis of a run — the view maps this to a ✓ / ✗ / — glyph + word.

    A typed enum (not a raw emoji) so the pure model stays presentation-free; mirrors the
    Streamlit ``"✅"/"❌"/"—"`` ternary but keeps the glyph in the view.
    """

    DELIVERED = "delivered"  # sftp_ok
    FAILED = "failed"  # sftp_attempted and not sftp_ok
    NOT_ATTEMPTED = "not_attempted"  # SFTP not requested this run


@dataclass(frozen=True)
class HistoryBanner:
    """The verdict-first Run-History banner state (the answer to "is my sync OK?")."""

    verdict: Verdict
    headline: str
    detail: str


@dataclass(frozen=True)
class RunRow:
    """A PII-free per-run display row.

    Carries only counts + plain strings + typed enums — **never** a raw ``error`` / path /
    ``sis_type`` / column / stack trace (there is deliberately NO ``error`` field). ``entity_counts``
    holds the 5 rostering entities always + the 2 myBlueprint+ entities only when non-zero
    (``StudentAttendance`` omitted), reusing ``home_status``'s entity vocabulary.

    Attributes:
        when: a plain relative phrase (``friendly_timestamp``) — never the raw ISO.
        status_label: a plain per-run category label (from structured fields only).
        status_verdict: the ``Verdict`` the label maps to (an optional row-tint cue).
        entity_counts: per-entity output row counts (safe scalars).
        entity_total: the sum of ``entity_counts`` values.
        sftp: the ``SftpDelivery`` axis (the view maps it to a glyph + word).
        warnings: the ``data_errors.total`` count (a safe scalar).
        duration: a plain duration string ("3.2s" / "—").
        source: a friendly run-origin label from the bounded ``_SOURCE_LABELS`` vocabulary
            ("Nightly" / "Manual" / "Command line"); "—" for unknown/absent (a pre-enrichment
            record) — an unexpected raw value is NEVER echoed.
        district_note: a muted "Different district: <name>" note when the record's district
            differs from the active one (a bounded district id/display — never a path), else
            ``None``.
    """

    when: str
    status_label: str
    status_verdict: Verdict
    entity_counts: dict[str, int] = field(default_factory=dict)
    entity_total: int = 0
    sftp: SftpDelivery = SftpDelivery.NOT_ATTEMPTED
    warnings: int = 0
    duration: str = "—"
    source: str = "—"
    district_note: str | None = None


# Plain per-run status labels keyed by the shared ``LatestReason`` (single-sourced precedence).
# ``CLEAN`` is resolved at row-build time to "Delivered" vs "Completed" by whether SFTP was
# attempted (a delivered clean run reads "Delivered"; a clean run with no SFTP reads "Completed").
_REASON_LABELS: dict[LatestReason, str] = {
    LatestReason.FAILED_ETL: "Failed",
    LatestReason.FAILED_DELIVERY: "Built, not delivered",
}

# The bounded run-source → friendly-label vocabulary (the store's ``VALID_SOURCES`` closed set,
# minus ``unknown`` which shares the fallback). Anything else — including a record written before
# the source enrichment existed — renders the neutral fallback; a raw value is NEVER echoed.
_SOURCE_LABELS: dict[str, str] = {
    "scheduled": "Nightly",
    "manual": "Manual",
    "cli": "Command line",
}

_SOURCE_FALLBACK = "—"


def derive_history_banner(
    records: list[dict] | None,
    app_config: AppConfig,
    *,
    now: datetime | None = None,
    store_created_at: str | None = None,
    schedule_status: ScheduleStatus | None = None,
) -> HistoryBanner:
    """Derive the verdict-first Run-History banner (pure, TOTAL, PII-safe).

    Evaluated top-down, first-match-wins (mirrors ``home_status``'s degradation-first order).
    Classifies the LATEST record through the shared ``classify_latest_reason`` +
    ``verdict_for_reason`` and reuses ``is_stale`` — so the banner never drifts from Home or from
    the per-run rows. Graceful degradation (``None``/``[]``) is a first-class calm WARNING output,
    never a raise. NEVER interpolates the raw ``error`` / ``ANOMALY:`` string.

    ``store_created_at`` (the run store's ``meta.created_at``) is the established-install signal
    for the fresh-start empty state; ``schedule_status`` (D4, injected off-thread) supplies the
    honest LIVE next-run reassurance — Run History is read-only (no fix CTA), so it does not
    surface a schedule-attention verdict (Home owns that), only the derived empty-state copy.
    """
    # Rule: unavailable (the never-crash floor) — the reader couldn't read the store.
    if records is None:
        return HistoryBanner(
            verdict=Verdict.WARNING,
            headline="Run history unavailable",
            detail="We couldn't read the run history right now — your nightly sync may still be running normally.",
        )

    # Rule: no runs yet (empty but readable). The store is fresh for EVERY install after this
    # update (no backfill), so an established install is told the history starts fresh rather
    # than the false "No sync has run yet"; a genuine first run keeps the calm waiting copy.
    # Slice 5 (D4a) re-based the discriminator on the durable ``has_completed_setup()`` fact so a
    # completed manual-only upgrader gets the honest fresh-start copy; newcomer-vs-upgrader remain
    # indistinguishable, so fresh-start is the chosen default (not a verified fact) — the copy is
    # conditioned ("If you used an earlier version…"), never a flat claim of hidden history. The
    # schedule reassurance derives from the LIVE read-back (``schedule_status``), never the flag.
    if not records:
        if app_config.has_completed_setup() or store_created_at:
            fresh = (
                "New nightly syncs will appear here from now on. "
                "If you used an earlier version, its run history isn't carried over."
            )
            if _schedule_is_live(schedule_status):
                detail = fresh + f" Scheduled for {schedule_status.next_run_display} each night."  # type: ignore[union-attr]
            elif app_config.has_completed_setup() and _schedule_confirmed_missing(schedule_status):
                # Honest (finding #1b): a completed install with NO nightly schedule won't sync on its
                # own — mirror Home's plain copy rather than implying automation. Only on a CONFIRMED
                # MISSING read-back (never an unconfirmed None/UNKNOWN).
                detail = (
                    "Your roster won't sync automatically until you add a nightly schedule — set one up "
                    "in Settings whenever you're ready. Manual conversions from the Convert tab appear here too."
                )
            else:
                detail = fresh
            return HistoryBanner(verdict=Verdict.WARNING, headline="Run history starts fresh here", detail=detail)
        return HistoryBanner(
            verdict=Verdict.WARNING,
            headline="No sync has run yet",
            detail="Your nightly runs will appear here once the first one completes.",
        )

    latest = records[0]
    reason = classify_latest_reason(latest)

    if reason is LatestReason.FAILED_ETL:
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Your last sync failed",
            detail="The most recent run hit a problem and didn't finish — see the run below.",
        )

    if reason is LatestReason.FAILED_DELIVERY:
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Your last roster didn't reach SpacesEDU",
            detail="The most recent run built the data but the upload failed.",
        )

    if reason is LatestReason.ANOMALY:
        anomalies = latest.get("anomalies") or []
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Something looked off recently",
            detail=friendly_anomaly_detail(len(anomalies), variant=AnomalyVariant.HISTORY),
        )

    if reason is LatestReason.DATA_WARNINGS:
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Recent runs completed with data warnings",
            detail="Some records had field problems and were skipped — the runs still delivered.",
        )

    # reason is CLEAN — a delivered success; staleness is the one time-relative axis layered on top.
    timestamp = str(latest.get("timestamp", ""))
    if is_stale(timestamp, now):
        return HistoryBanner(
            verdict=Verdict.WARNING,
            headline="No recent sync",
            detail=(
                f"Your last sync was {friendly_timestamp(timestamp, now=now)} — a nightly run may have been missed."
            ),
        )

    return HistoryBanner(
        verdict=verdict_for_reason(reason),
        headline="Your sync is running",
        detail=f"Your last sync delivered cleanly {friendly_timestamp(timestamp, now=now)}.",
    )


def _sftp_delivery(record: dict) -> SftpDelivery:
    """The SFTP axis of a run — delivered / failed / not-attempted (defensive booleans)."""
    if bool(record.get("sftp_ok")):
        return SftpDelivery.DELIVERED
    if bool(record.get("sftp_attempted")):
        return SftpDelivery.FAILED
    return SftpDelivery.NOT_ATTEMPTED


def _row_entity_counts(record: dict) -> dict[str, int]:
    """The per-run entity counts: 5 rostering always + 2 myBlueprint+ when non-zero.

    Reuses ``home_status``'s entity tuples + ``_as_int`` (defensive coercion) so a malformed count
    never crashes the row and the column vocabulary matches Home's tiles exactly.
    """
    counts: dict[str, int] = {}
    for name in _ROSTERING_ENTITIES:
        counts[name] = _as_int(record.get(name))
    for name in _MYBLUEPRINT_ENTITIES:
        value = _as_int(record.get(name))
        if value > 0:
            counts[name] = value
    return counts


def _status_label(reason: LatestReason, record: dict, *, sftp: SftpDelivery) -> str:
    """The plain per-run category label from the shared ``LatestReason`` (no emoji, no raw string).

    ``CLEAN`` resolves to "Delivered · N data warnings" when there were warnings, else "Delivered"
    (SFTP delivered/attempted) or "Completed" (SFTP not attempted). All other reasons map through
    ``_REASON_LABELS``. Single-sourced so a row label + the banner can never contradict.
    """
    if reason in _REASON_LABELS:
        return _REASON_LABELS[reason]
    if reason is LatestReason.DATA_WARNINGS:
        total = _data_errors_total(record)
        return f"Delivered · {total} data {pluralize('warning', total)}"
    # reason is CLEAN — distinguish a delivered run from one that never attempted SFTP.
    return "Delivered" if sftp is SftpDelivery.DELIVERED else "Completed"


def _source_label(record: dict) -> str:
    """The friendly run-origin label from the bounded vocabulary; anything else → "—" (total)."""
    return _SOURCE_LABELS.get(str(record.get("source", "")), _SOURCE_FALLBACK)


def _district_note(record: dict, active_sis: str | None) -> str | None:
    """A "Different district: <name>" note when the record's district differs from the active one.

    Pure + total: BOTH sides must be known non-empty to establish a difference (an absent
    ``sis_type`` / unset active district → ``None``, never a guess). The display resolves via
    ``friendly_district_name`` (a bounded district id/display fact — never a path; the module's
    privacy bar deliberately allows this one identity fact, see the module docstring).
    """
    record_sis = str(record.get("sis_type", "") or "").strip()
    active = (active_sis or "").strip()
    if not record_sis or not active or record_sis == active:
        return None
    return f"Different district: {friendly_district_name(record_sis)}"


def _duration(record: dict) -> str:
    """A plain duration string ("3.2s"); missing/garbage → "—" (uniform-string display cell)."""
    value = record.get("duration_s")
    if value is None:
        return "—"
    try:
        return f"{float(value):g}s"
    except (TypeError, ValueError):
        return "—"


def to_run_row(record: dict, *, now: datetime | None = None, active_sis: str | None = None) -> RunRow:
    """Map one run record → a total, PII-free ``RunRow`` (never raises).

    Every field is read via ``.get`` + ``_as_int``; a missing ``status`` classifies as non-success
    → "Failed" (the honest fail-safe default). The raw ``error``/path is NEVER read. ``active_sis``
    (the active district id, injected by the view) enables the different-district note; ``None``
    (the default) derives no note.
    """
    sftp = _sftp_delivery(record)
    reason = classify_latest_reason(record)
    counts = _row_entity_counts(record)
    return RunRow(
        when=friendly_timestamp(str(record.get("timestamp", "")), now=now),
        status_label=_status_label(reason, record, sftp=sftp),
        status_verdict=verdict_for_reason(reason),
        entity_counts=counts,
        entity_total=sum(counts.values()),
        sftp=sftp,
        warnings=_data_errors_total(record),
        duration=_duration(record),
        source=_source_label(record),
        district_note=_district_note(record, active_sis),
    )


def to_run_rows(records: list[dict], *, now: datetime | None = None, active_sis: str | None = None) -> list[RunRow]:
    """Map a newest-first list of run records → ``RunRow``s (one per record, never raises).

    The view branches on ``None``/``[]`` BEFORE calling this — ``to_run_rows`` is only ever handed
    an actual list (``[]`` → ``[]``; a mixed valid/partial list → one safe ``RunRow`` per record).
    """
    return [to_run_row(record, now=now, active_sis=active_sis) for record in records]
