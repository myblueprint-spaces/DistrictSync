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
- **the per-run display rows** — ``to_run_row(record, *, prior_build, now=None, active_sis=None)`` → a total,
  PII-free ``RunRow`` (plain time, a category-only ``status_label`` + its ``Verdict``, entity
  counts, an SFTP enum, a warnings count, a plain duration, a bounded run-origin ``source`` label,
  an optional different-district note) and ``to_run_rows(records)`` over the newest-first list.

**Privacy (LIVE/top):** the record's free-text ``error`` (``str(e)`` in the emitter — path /
``sis_type`` / column risk) and the raw ``ANOMALY:``-prefixed strings are **NEVER** read into a
``RunRow`` field or a banner headline/detail. ``RunRow`` carries **no** ``error`` field at all, so
a future view edit cannot render one; faults are named by CATEGORY, counts are safe scalars (the
PARTIAL banner's ``failure_copy`` sentence may name a left-out entity's config-declared file and
column — validated labels, plan 0053 S7, D4 — never an observed header). This
is the concrete fix for the Streamlit page's raw-``error`` column + log-path caption (dropped).
The ONE deliberate identity fact surfaced (0034 Slice 4) is the DISTRICT: when a record's
``sis_type`` differs from the active district, ``district_note`` carries the friendly district
display (a bounded config id / display name — never a path, never the free-text error).
The record's ``run_as`` (plan 0049) is an OS account name and therefore stays OUT: it is reduced
at the boundary by ``run_as_display`` to a two-member bounded vocabulary, so the row says WHICH OF
TWO accounts ran a night without ever carrying the name of either.

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
from src.etl.outcomes import EntityOutcome
from src.ui_flet.failure_copy import data_warnings_clause, partial_copy, partial_label
from src.ui_flet.home_status import (
    _MYBLUEPRINT_ENTITIES,
    _ROSTERING_ENTITIES,
    EMPTY_NO_AUTO_SYNC_DETAIL,
    LatestReason,
    _as_int,
    _data_errors_total,
    _foreign_records_elsewhere,
    _foreign_records_status,
    _paused_status,
    _schedule_confirmed_live,
    _schedule_confirmed_missing,
    _schedule_is_live,
    build_record_for,
    classify_latest_reason,
    empty_state_headline,
    failed_detail,
    has_earlier_run_history,
    is_delivery_only,
    is_stale,
    left_out_outcomes,
    sftp_delivered,
    sync_window_paused,
    verdict_for_reason,
    verdict_records,
)
from src.ui_flet.humanize import (
    AnomalyVariant,
    friendly_anomaly_detail,
    friendly_district_name,
    friendly_timestamp,
    pluralize,
)
from src.ui_flet.schedule_status import ScheduleStatus
from src.ui_flet.setup_gates import principal_key
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
        run_as: the BOUNDED "which Windows account ran this" display — one of
            :data:`RUN_AS_THIS_ACCOUNT` / :data:`RUN_AS_ANOTHER_ACCOUNT`, or ``""`` when it could
            not be established. Never the raw ``DOMAIN\\user`` off the record: this row is bounded
            to counts, bounded vocabularies and safe strings, and a raw account name repeated on
            every historical row would be the first raw identifying value in it.
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
    run_as: str = ""


# The bounded run-as vocabulary (plan 0049 S-2a.5). Two members plus ``""`` for "not
# established" — the record's raw ``run_as`` is reduced against the account now running through
# ``setup_gates.principal_key``, the ONE reduction every principal comparison in this app goes
# through, so the column and the schedule gates can never disagree about what "another account"
# means. A record written before the key existed (a pre-v3.22 upgrader) reduces to ``""``, which
# is deliberately NOT a distinct value: it must not make the column appear on a per-user install
# whose ledger simply straddles the upgrade.
RUN_AS_THIS_ACCOUNT = "This account"
RUN_AS_ANOTHER_ACCOUNT = "Another account"

# Stated ONCE above the table when every row agrees — the alternative to a column with nothing
# to say (``components.run_table``'s ``show_mbp`` precedent). Machine scope only: on a per-user
# install every visible record was written by the account reading them, so this would answer a
# question none of the 20 districts asked and break S-2a's own byte-identity promise.
RUN_AS_ALL_THIS_ACCOUNT_NOTE = "Every run below ran as the Windows account you're using now."
RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE = "Every run below ran as another Windows account on this computer."


def run_as_display(value: object, *, current_account: str) -> str:
    """Reduce a record's ``run_as`` to the bounded display vocabulary (pure, TOTAL).

    ``""`` means NOT ESTABLISHED — an absent/blank value (a record written before the key
    existed), a non-string, or an unresolvable current account. Never a guess and never the raw
    name: "not established" and "another account" are different facts, and collapsing them would
    print a foreign-account claim over a record that carries no account at all.
    """
    recorded = value.strip() if isinstance(value, str) else ""
    current = (current_account or "").strip()
    if not recorded or not current:
        return ""
    return RUN_AS_ANOTHER_ACCOUNT if principal_key(recorded, current) else RUN_AS_THIS_ACCOUNT


def run_as_summary_line(rows: list[RunRow], *, machine_scope: bool) -> str | None:
    """The "state it once" line for a run-as column that would not vary (pure, TOTAL).

    ``None`` unless this install reads the SHARED profile AND every row that established an
    account agrees on one. On a per-user install it is always ``None`` — see
    :data:`RUN_AS_ALL_THIS_ACCOUNT_NOTE`. With a mix, ``components.run_table`` renders the column
    instead and this line would be redundant; with nothing established there is nothing to say.
    """
    if not machine_scope:
        return None
    distinct = {row.run_as for row in rows if row.run_as}
    if len(distinct) != 1:
        return None
    only = next(iter(distinct))
    return RUN_AS_ALL_THIS_ACCOUNT_NOTE if only == RUN_AS_THIS_ACCOUNT else RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE


# Plain per-run status labels keyed by the shared ``LatestReason`` (single-sourced precedence).
# ``CLEAN`` and ``DATA_WARNINGS`` are resolved at row-build time to "Delivered" vs "Completed" by
# whether the run's SFTP axis says it shipped (a run with no SFTP attempt reads "Completed" —
# with or without data warnings; claiming delivery for it was the 2026-08-31 mislabel).
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

# Run History's OWN upgrader lead — deliberately not shared with Home's. The two surfaces
# single-source the RULE and the headlines (``home_status``); the lead sentences stay
# per-surface because this one speaks about the LEDGER printed directly beneath it ("runs"),
# where Home speaks about the sync.
#
# It says "New runs", not "New NIGHTLY syncs". This arm is gated on the store's birth stamp
# ALONE — nothing about a schedule — so a manual-only install with a stamped-but-empty store
# (reachable through the store's quarantine-recreate path, and staged verbatim by QA row 1o)
# reads it. Naming a nightly there is the same over-claim ``home_status._expects_a_nightly``
# gates on the Home side; the nightly is named ONLY by the append below, which requires a
# CONFIRMED-LIVE read-back.
_FRESH_START_LEAD = (
    "New runs will appear here from now on. If you used an earlier version, its run history isn't carried over."
)

# Run History's OWN lead for the D14 failed-attempts-only state (plan 0053 S5): the banner sits
# directly above the rows it describes, so it points at them rather than at this screen. The
# headline is Home's (``home_status.empty_state_headline``), single-sourced.
_FAILED_ATTEMPTS_ONLY_LEAD = (
    "The conversions run from the Convert tab so far didn't finish — each attempt is listed below."
)


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

    The banner reads the records the VERDICT reads (``home_status.verdict_records``, owner
    decision D14): a failed MANUAL attempt never sets it, exactly as on Home — while the rows
    beneath it (``to_run_rows``) still list every record, that attempt included.

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

    # D14 (plan 0053 S5): the SAME filter, at the same point, as ``derive_home_status``.
    failed_attempts_only = bool(records) and not verdict_records(records)
    records = verdict_records(records)

    # FIX 1: the seasonal-pause fact, gated to Home's EXACT precedence so the two surfaces can never
    # disagree about one state — an ENABLED window outside its season, UNLESS a confirmed-MISSING
    # read-back outranks it (mirrors ``home_status``: a gone task won't resume, so "resumes <date>"
    # would be a lie). During a summer pause the newest store record is the last in-season run (weeks
    # old by construction), so the ``is_stale`` rule below would false-fire an amber "No recent sync"
    # while Home shows the calm HEALTHY "Paused for the summer". Consulted at the empty-state slot AND
    # after the two FAILED reasons (a real failure still surfaces in summer), ABOVE the anomaly /
    # data-warning / stale rules whose "we expected a sync" copy is moot while the season is paused.
    # 0046 C / A9: the pause is FALSE on a foreign principal — the nightly gate reads the RUNNING
    # account's config, where no window exists. Single-sourced in ``sync_window_paused`` so this
    # banner, Home and the Setup badge can never disagree about one state.
    # 0049 S-2a.1: ``shared_records`` rides the same ``ScheduleStatus`` (ONE carrier), so on a
    # machine-scoped install — where the nightly reads the SAME shared config and the pause IS
    # enforced — this banner and Home agree that the season is paused.
    foreign_account = schedule_status.foreign_account if schedule_status is not None else ""
    shared_records = schedule_status.shared_records if schedule_status is not None else False
    paused = sync_window_paused(
        app_config, now=now, foreign_account=foreign_account, shared_records=shared_records
    ) and not _schedule_confirmed_missing(schedule_status)

    # 0046 C / A5: the nightly's run records are written to ANOTHER account's profile, so this
    # ledger's silence is where the records WENT — not a sync that failed to happen. Consulted at
    # the empty-state slot AND above the stale rule, the two arms that would otherwise each imply a
    # fault ("No recent sync … a nightly run may have been missed") or promise a nightly that will
    # never appear here. The rule, the headline and the note are all imported from Home's module /
    # ``schedule_status`` — NO literal is re-spelled in this file, which is the same single-sourcing
    # the EMPTY_* headlines and ``_paused_banner`` already use.
    foreign_records = _foreign_records_elsewhere(records, now=now, schedule_status=schedule_status)

    # Rule: no runs yet (empty but readable). The store is fresh for EVERY install after this
    # update (no backfill), so an UPGRADER is told the history starts fresh rather than that
    # nothing exists; a genuine first run keeps the calm waiting copy.
    #
    # 0038 S7 part (i): the discriminator and both headlines are now SINGLE-SOURCED in
    # ``home_status`` (``has_earlier_run_history`` / ``EMPTY_*``). They were duplicated here
    # byte-for-byte, which meant Home could be corrected and this banner silently left behind —
    # on exactly the state the two surfaces must agree about. ``has_completed_setup()`` is no
    # longer a disjunct: the wizard flips it on save, so it made every brand-new install an
    # "upgrader". The schedule reassurance still derives from the LIVE/MISSING read-back
    # (``schedule_status``), never the config flag.
    if not records:
        # Rule: seasonal pause (empty store) — outside an enabled window no run is expected, so an
        # empty store is calm, not a missed run. Beats the fresh-start / first-run empty sub-states
        # (mirrors Home's empty-store paused branch — the two surfaces stay identical).
        if paused:
            return _paused_banner(app_config, now=now)
        # Rule: the records are in another account's profile (A5) — an empty ledger here is
        # EXPECTED, so neither empty-state arm may run: both would describe an install waiting for
        # a first sync that already happened somewhere else.
        if foreign_records:
            return _foreign_records_banner(schedule_status)  # type: ignore[arg-type]
        upgrade = has_earlier_run_history(store_created_at=store_created_at)
        if app_config.has_completed_setup() and _schedule_confirmed_missing(schedule_status):
            # Honest (finding #1b): a completed install with NO nightly schedule won't sync on its
            # own — the SAME sentence Home shows, from the same constant, rather than implying
            # automation. Only on a CONFIRMED MISSING read-back (never an unconfirmed None/UNKNOWN).
            detail = EMPTY_NO_AUTO_SYNC_DETAIL
        elif failed_attempts_only:
            detail = _FAILED_ATTEMPTS_ONLY_LEAD
            if _schedule_is_live(schedule_status):
                detail += f" Scheduled for {schedule_status.next_run_display} each night."  # type: ignore[union-attr]
        elif upgrade:
            detail = _FRESH_START_LEAD
            if _schedule_is_live(schedule_status):
                detail += f" Scheduled for {schedule_status.next_run_display} each night."  # type: ignore[union-attr]
        else:
            # NAMES no nightly. This arm covers the install with no stamp, no confirmed
            # schedule and (unlike the branch above) no confirmed ABSENCE either — which
            # includes an admin who skipped the Schedule step. "Your nightly runs will appear
            # here" told them about automation they declined; the same over-claim Home's
            # ``_FIRST_SYNC_LEAD`` gate closes, in the surface one click away.
            detail = "Runs will appear here once the first one completes."
        return HistoryBanner(
            verdict=Verdict.WARNING,
            headline=empty_state_headline(upgrade=upgrade, failed_attempts_only=failed_attempts_only),
            detail=detail,
        )

    latest = records[0]
    prior_build = build_record_for(records, 0)
    reason = classify_latest_reason(latest, prior_build=prior_build)

    # Plan 0053 S3: the cause is the record's bounded ``error_category``, through the SAME
    # ``home_status.failed_detail`` Home uses — so the two surfaces word one failure identically.
    if reason is LatestReason.FAILED_ETL:
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Your last sync failed",
            detail=f"The most recent run didn't finish. {failed_detail(latest)}",
        )

    if reason is LatestReason.FAILED_DELIVERY:
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Your last roster didn't reach SpacesEDU",
            detail=(
                "The upload of your saved files failed."
                if is_delivery_only(latest)
                else "The most recent run built the data but the upload failed."
            ),
        )

    # Rule: seasonal pause — outside an enabled window no nightly sync is expected. Slotted BELOW the
    # two FAILED reasons (a real failure still surfaces in summer) and ABOVE anomaly / data-warnings /
    # stale, whose cadence-based copy is moot while the season is intentionally paused. Delegates to
    # Home's ``_paused_status`` so the banner is byte-identical to Home's for the same inputs.
    if paused:
        return _paused_banner(app_config, now=now)

    # Rule: the records are in another account's profile (A5). Slotted at HOME'S EXACT POSITION —
    # below the two FAILED reasons and the pause, above anomaly / data-warnings / stale — because
    # the two surfaces must not classify one install differently. Every rule below it describes a
    # nightly cadence this ledger cannot see, and the rule only fires when the ledger has nothing
    # current to say anyway (no records, or a newest older than the stale window), so "recently" in
    # the anomaly/data-warning copy would already be false by construction.
    if foreign_records:
        return _foreign_records_banner(schedule_status)  # type: ignore[arg-type]

    # Plan 0053 S3: at HOME'S EXACT POSITION (below the pause and the foreign-principal rule,
    # above the anomaly), with Home's EXACT copy — ``failure_copy.partial_copy`` is the one source.
    if reason is LatestReason.PARTIAL:
        headline, detail = partial_copy(
            left_out_outcomes(latest, prior_build=prior_build), delivered=sftp_delivered(latest)
        )
        clause = data_warnings_clause(_data_errors_total(latest))
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline=headline,
            detail=f"{detail} {clause}" if clause else detail,
        )

    if reason is LatestReason.ANOMALY:
        anomalies = latest.get("anomalies") or []
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Something looked off recently",
            detail=friendly_anomaly_detail(len(anomalies), variant=AnomalyVariant.HISTORY),
        )

    if reason is LatestReason.DATA_WARNINGS:
        # "delivered" is claimed only when the record's SFTP axis says it shipped (the same
        # honesty rule the CLEAN branch below has always applied) — a local-only run with data
        # warnings used to read "the runs still delivered" while nothing was ever uploaded.
        return HistoryBanner(
            verdict=verdict_for_reason(reason),
            headline="Recent runs completed with data warnings",
            detail=(
                "Some records had field problems and were skipped — the runs still delivered."
                if sftp_delivered(latest)
                else "Some records had field problems and were skipped — the runs still completed."
            ),
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

    # 0032 T1 #1 (mirrors Home's healthy branch, same shared predicates): "running" (ongoing
    # automation) only on a CONFIRMED-LIVE schedule read-back, else the record-scoped claim; and
    # "delivered to SpacesEDU" only when the record's SFTP axis says it genuinely shipped.
    when = friendly_timestamp(timestamp, now=now)
    return HistoryBanner(
        verdict=verdict_for_reason(reason),
        headline=("Your sync is running" if _schedule_confirmed_live(schedule_status) else "Your last sync worked"),
        detail=(
            f"Your last sync delivered to SpacesEDU {when}."
            if sftp_delivered(latest)
            else f"Your last sync completed {when} — files were written to your output folder."
        ),
    )


def _paused_banner(app_config: AppConfig, *, now: datetime | None) -> HistoryBanner:
    """Mirror Home's seasonal-pause state as a Run-History banner — identical copy (single source).

    Delegates to ``home_status._paused_status`` so the two surfaces can NEVER drift about a pause
    (FIX 1): same HEALTHY verdict, same "Paused for the summer — resumes <date>" headline + detail.
    Run History is read-only (no fix CTA), and the paused state has none anyway, so only the three
    banner fields are carried over.
    """
    paused = _paused_status(app_config, now=now)
    return HistoryBanner(verdict=paused.verdict, headline=paused.headline, detail=paused.detail)


def _foreign_records_banner(schedule_status: ScheduleStatus) -> HistoryBanner:
    """Mirror Home's foreign-principal state as a Run-History banner — identical copy (A5).

    Delegates to ``home_status._foreign_records_status`` exactly as ``_paused_banner`` delegates to
    ``_paused_status``, so the two surfaces can NEVER drift about where the nightly's records went.
    Run History is read-only, so only the three banner fields are carried over — the fix CTA Home
    attaches on the problem arm is dropped here, not re-spelled.
    """
    foreign = _foreign_records_status(schedule_status)
    return HistoryBanner(verdict=foreign.verdict, headline=foreign.headline, detail=foreign.detail)


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
    never crashes the row and the column vocabulary is single-sourced. **Those tuples exclude
    ``StudentAttendance``**, so an attendance-only run renders five zeros HERE. Its real row count
    reaches exactly ONE surface — Home's healthy ``size_clause`` — and only from the HEALTHY
    branch, so a warning/failed attendance run shows its size nowhere. That is an OPEN defect,
    tracked in ``docs/claugentic-ROADMAP.md`` — not a discharged one. A delivery-only
    record (deliver-from-disk, 0034 Slice 2) built nothing this run — its count keys are zeros by
    shape, so return ``{}`` and the table renders "—" cells, never a "0 Students" lie.
    """
    if is_delivery_only(record):
        return {}
    counts: dict[str, int] = {}
    for name in _ROSTERING_ENTITIES:
        counts[name] = _as_int(record.get(name))
    for name in _MYBLUEPRINT_ENTITIES:
        value = _as_int(record.get(name))
        if value > 0:
            counts[name] = value
    return counts


def _status_label(
    reason: LatestReason, record: dict, *, sftp: SftpDelivery, left_out: tuple[EntityOutcome, ...]
) -> str:
    """The plain per-run category label from the shared ``LatestReason`` (no emoji, no raw string).

    ``PARTIAL`` (plan 0053 S3) reads "<Delivered|Completed> · <suffix>", the suffix worded by
    ``failure_copy.partial_label`` over ``left_out`` (the outcomes that warn; the banner names
    them): "N file(s) skipped" for files the run was set up to build and left out, and since
    plan 0053 S10 a note's own words for a file that BUILT without part of itself
    ("co-teachers left out") — never counted as skipped. A delivery-only record's form is
    "Delivered saved files · <suffix>": the saved files it shipped are the partial build's.

    ``DATA_WARNINGS`` and ``CLEAN`` both open with "Delivered" ONLY when the SFTP axis says the
    run genuinely shipped, else "Completed" (SFTP not attempted — a local-only run must never
    claim delivery; a run whose upload FAILED never reaches either reason, ``classify`` routes it
    to ``FAILED_DELIVERY`` first). All other reasons map through ``_REASON_LABELS``.
    Single-sourced so a row label + the banner can never contradict. A delivery-only record
    (deliver-from-disk, 0034 Slice 2) labels honestly as a delivery of already-saved files —
    "Delivered saved files" / "Delivery failed" — never as a build.
    """
    if reason in _REASON_LABELS:
        if reason is LatestReason.FAILED_DELIVERY and is_delivery_only(record):
            return "Delivery failed"
        return _REASON_LABELS[reason]
    if reason is LatestReason.PARTIAL:
        if is_delivery_only(record):
            word = "Delivered saved files"
        else:
            word = "Delivered" if sftp is SftpDelivery.DELIVERED else "Completed"
        return f"{word} · {partial_label(left_out)}"
    if reason is LatestReason.DATA_WARNINGS:
        total = _data_errors_total(record)
        word = "Delivered" if sftp is SftpDelivery.DELIVERED else "Completed"
        return f"{word} · {total} data {pluralize('warning', total)}"
    # reason is CLEAN — distinguish a delivered run from one that never attempted SFTP,
    # and a deliver-from-disk (shipped an earlier build) from the build-and-deliver run.
    if is_delivery_only(record):
        return "Delivered saved files"
    return "Delivered" if sftp is SftpDelivery.DELIVERED else "Completed"


def _source_label(record: dict) -> str:
    """The friendly run-origin label from the bounded vocabulary; anything else → "—" (total)."""
    return _SOURCE_LABELS.get(str(record.get("source", "")), _SOURCE_FALLBACK)


def _district_note(record: dict, active_sis: str | None, district_displays: dict[str, str] | None = None) -> str | None:
    """A "Different district: <name>" note when the record's district differs from the active one.

    Total: BOTH sides must be known non-empty to establish a difference (an absent
    ``sis_type`` / unset active district → ``None``, never a guess). The display resolves via
    ``friendly_district_name`` — a config READ (not pure), so ``to_run_rows`` pre-resolves each
    distinct district once per render into ``district_displays`` (a bounded district id/display
    fact — never a path; the module's privacy bar deliberately allows this one identity fact,
    see the module docstring).
    """
    record_sis = str(record.get("sis_type", "") or "").strip()
    active = (active_sis or "").strip()
    if not record_sis or not active or record_sis == active:
        return None
    if district_displays is not None and record_sis in district_displays:
        return f"Different district: {district_displays[record_sis]}"
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


def to_run_row(
    record: dict,
    *,
    prior_build: dict | None,
    now: datetime | None = None,
    active_sis: str | None = None,
    district_displays: dict[str, str] | None = None,
    current_account: str = "",
) -> RunRow:
    """Map one run record → a total, PII-free ``RunRow`` (never raises).

    Every field is read via ``.get`` + ``_as_int``; a missing ``status`` classifies as non-success
    → "Failed" (the honest fail-safe default). The raw ``error``/path is NEVER read. ``active_sis``
    (the active district id, injected by the view) enables the different-district note; ``None``
    (the default) derives no note. ``district_displays`` is an optional pre-resolved
    district-display cache (see ``to_run_rows``) so a single record resolves live when absent.

    ``prior_build`` (plan 0053 S3) is REQUIRED keyword-only for the same reason it is on
    ``home_status.classify_latest_reason``, which receives it: a delivery-only row's PARTIAL is
    the build's, and a forgotten walk-back would paint that row green. ``to_run_rows`` passes
    ``home_status.build_record_for`` of each row's position; ``None`` for a build record.

    ``current_account`` (``accounts.process_account()``, injected by the view — this module is
    pure and never reads the environment) enables the bounded ``run_as`` display. It DEFAULTS to
    ``""`` deliberately: a caller that cannot name the account gets "not established" and no
    column, which is display degradation, not a safety-relevant permissive default — nothing
    about a run's verdict, delivery or counts depends on it.
    """
    sftp = _sftp_delivery(record)
    reason = classify_latest_reason(record, prior_build=prior_build)
    counts = _row_entity_counts(record)
    left_out = left_out_outcomes(record, prior_build=prior_build)
    return RunRow(
        when=friendly_timestamp(str(record.get("timestamp", "")), now=now),
        status_label=_status_label(reason, record, sftp=sftp, left_out=left_out),
        status_verdict=verdict_for_reason(reason),
        entity_counts=counts,
        entity_total=sum(counts.values()),
        sftp=sftp,
        warnings=_data_errors_total(record),
        duration=_duration(record),
        source=_source_label(record),
        district_note=_district_note(record, active_sis, district_displays),
        run_as=run_as_display(record.get("run_as"), current_account=current_account),
    )


def to_run_rows(
    records: list[dict],
    *,
    now: datetime | None = None,
    active_sis: str | None = None,
    current_account: str = "",
    limit: int | None = None,
) -> list[RunRow]:
    """Map a newest-first list of run records → ``RunRow``s (one per record, never raises).

    The view branches on ``None``/``[]`` BEFORE calling this — ``to_run_rows`` is only ever handed
    an actual list (``[]`` → ``[]``; a mixed valid/partial list → one safe ``RunRow`` per record).
    Each distinct differing district's display name is resolved ONCE per call (the resolution is
    a config read — never repeated per row for the same district).

    ``limit`` caps the rows to the newest ``limit`` records (``None`` = every record) WITHOUT
    narrowing what a row can see: a delivery-only row's ``prior_build`` walk-back still searches
    the WHOLE list. The screen hands this the full ledger — the one Home reads — so the banner's
    verdict (plan 0053 S5, D14) and the table's walk-backs are never decided by a 50-row window.
    """
    shown = records if limit is None else records[: max(limit, 0)]
    active = (active_sis or "").strip()
    displays: dict[str, str] = {}
    if active:
        for record in shown:
            sis = str(record.get("sis_type", "") or "").strip()
            if sis and sis != active and sis not in displays:
                displays[sis] = friendly_district_name(sis)
    return [
        to_run_row(
            record,
            prior_build=build_record_for(records, index),
            now=now,
            active_sis=active_sis,
            district_displays=displays,
            current_account=current_account,
        )
        for index, record in enumerate(shown)
    ]
