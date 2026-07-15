"""Pure Home status-derivation — the trust core of the sync-health cockpit.

NO ``flet`` import. Given the run records (newest-first, from
``history.store.read_run_records``) + the ``AppConfig`` state, derive a single
``HomeStatus`` — a ``Verdict`` (HEALTHY / WARNING / FAILED) + a plain-language
headline + supporting detail + an optional fix path + optional metric tiles.

**Graceful degradation is a first-class OUTPUT, not an exception path** — an
unreadable log (``records is None``) becomes a calm "status unavailable" WARNING,
never a raise. **The derivation is TOTAL:** every field is read via ``.get`` with a
safe default, so a partial/old record never ``KeyError``s; an unparseable timestamp
skips the staleness rule rather than crashing; every path returns a valid
``HomeStatus``.

**Privacy (LIVE/top):** the record's free-text ``error`` (``str(e)`` in the emitter,
which can carry a filesystem path / ``sis_type`` / column name) is **NEVER interpolated
into the admin-facing ``headline``/``detail``** — faults are named by CATEGORY from the
record's structured fields only (status / sftp / anomalies / data_errors). The raw
``error`` belongs solely to IA-6's raw-log expander.

Rule order (first-match-wins; failures above warnings above healthy — a failed sync is
never masked by a later "healthy") mirrors ``03_Run_History._status_cell``'s proven
precedence (status → sftp → data_errors), extended with anomaly + staleness + empty, and
tied to the CLI exit-code contract (1 = ETL fail, 3 = SFTP fail with output present).

The pipeline emits entity counts as **FLAT top-level keys** on the record
(``record["Students"]``, ``record["Staff"]``, …) — verified against
``pipeline._emit_run_log`` — NOT nested under an ``entity_counts`` key. ``HomeMetrics``
re-buckets those flat keys into its own ``entity_counts`` dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.config.app_config import AppConfig
from src.ui_flet.humanize import AnomalyVariant, friendly_anomaly_detail, friendly_timestamp, pluralize
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.verdict import Verdict

# The 5 SpacesEDU rostering entities always shown, then the 2 myBlueprint+ entities
# shown ONLY when non-zero (a SpacesEDU district run shows 5 tiles, not 7-with-two-zeros).
# ``StudentAttendance`` is deliberately omitted, mirroring ``03_Run_History``'s columns.
_ROSTERING_ENTITIES: tuple[str, ...] = ("Students", "Staff", "Family", "Classes", "Enrollments")
_MYBLUEPRINT_ENTITIES: tuple[str, ...] = ("CourseInfo", "StudentCourses")

# The SINGLE source of the entity-key → plain-language output-CSV label. The 5 rostering
# entities label to themselves; the myBlueprint+ / attendance keys map to their friendly CSV
# names (``CourseInfo`` → "Courses", ``StudentCourses`` → "Student courses",
# ``StudentAttendance`` → "Attendance"). This is a pure presentation fact (no flet), so both
# the pure ``mapping_catalog`` and the flet views (``components.run_table``, Home, Convert)
# consume ONE definition — a rename here changes every surface at once (DRY). An unknown key
# has no entry; callers fall back to the raw key (``ENTITY_LABELS.get(name, name)``).
ENTITY_LABELS: dict[str, str] = {
    "Students": "Students",
    "Staff": "Staff",
    "Family": "Family",
    "Classes": "Classes",
    "Enrollments": "Enrollments",
    "CourseInfo": "Courses",
    "StudentCourses": "Student courses",
    "StudentAttendance": "Attendance",
}

STALE_AFTER_HOURS = 36
"""A nightly job → a successful run older than ~1.5 nightly cycles is "no recent sync".
One generous constant absorbs timezone/clock skew (KISS — no per-tz math for a tool an
admin opens 2-3x/yr); staleness is only ever a WARNING, never a FAILED."""

_RUN_HISTORY_FIX = "run_history"

_CHECK_RUN_HISTORY_LABEL = "Check Run History"

# The FIRST fix target that isn't Run History (D4): a broken/missing schedule routes to
# Setup's schedule section, not the read-only run ledger. Slice 3's rail-follow already
# syncs the highlight on this programmatic hop.
_SETUP_FIX = "setup"

# The MISSING fix CTA names the ACTION, not the destination (finding #2b) — the Firefighter reads
# "fix the schedule", not "open a screen"; still routes to Setup (dest_id stays `_SETUP_FIX`).
_OPEN_SETUP_LABEL = "Fix the nightly schedule"


@dataclass(frozen=True)
class FixAction:
    """A plain-language CTA: the button ``label`` + the ``dest_id`` it navigates to."""

    label: str
    dest_id: str


@dataclass(frozen=True)
class HomeMetrics:
    """Light metric tiles for a delivered run: entity counts + plain last-run time + SFTP flag.

    ``entity_counts`` is re-bucketed from the record's flat top-level count keys — the 5
    rostering entities always, the 2 myBlueprint+ entities only when non-zero.
    """

    entity_counts: dict[str, int]
    last_run_display: str
    sftp_delivered: bool


@dataclass(frozen=True)
class HomeStatus:
    """The derived sync-health verdict the Home view renders (verdict-first)."""

    verdict: Verdict
    headline: str
    detail: str
    fix: FixAction | None
    metrics: HomeMetrics | None


def is_stale(
    last_ts: str,
    now: datetime | None = None,
    *,
    stale_after_hours: int = STALE_AFTER_HOURS,
) -> bool:
    """Whether the last successful run's timestamp is older than the staleness window.

    Pure + total. An unparseable ``last_ts`` → ``False`` (can't determine → don't cry
    wolf). Reused by IA-6.
    """
    text = (last_ts or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return False

    reference = now if now is not None else datetime.now(tz=parsed.tzinfo)
    try:
        elapsed_hours = (reference - parsed).total_seconds() / 3600
    except TypeError:
        return False  # naive/aware mismatch — total, treat as "can't determine"
    return elapsed_hours > stale_after_hours


class LatestReason(Enum):
    """The single-source classification of a latest run's fault axis (staleness EXCLUDED).

    The status→reason precedence a *record* carries, independent of when it ran — the one
    place the ``status → sftp → anomalies → data_errors`` order is decided. ``derive_home_status``
    (Home) and ``run_history.derive_history_banner`` + ``run_history.to_run_row`` (IA-6, the 2nd
    consumer) both classify through this, so a Home verdict and a Run-History row/banner can never
    drift. Staleness is a SEPARATE, time-relative axis the caller layers on top of ``CLEAN`` — it
    is deliberately NOT a reason here (a stale run is a clean run that's merely old).
    """

    FAILED_ETL = "failed_etl"  # status != "success" — the dominant fault
    FAILED_DELIVERY = "failed_delivery"  # ETL ok, SFTP attempted + failed (exit-3 shape)
    ANOMALY = "anomaly"  # delivered but a >20% drop looked off
    DATA_WARNINGS = "data_warnings"  # delivered, some rows had field problems + were skipped
    CLEAN = "clean"  # delivered cleanly (a stale run is still CLEAN — staleness is layered on top)


def classify_latest_reason(record: dict) -> LatestReason:
    """Classify a run record's fault axis (first-match precedence, staleness EXCLUDED) — pure + TOTAL.

    The SINGLE source of the ``status → sftp → anomalies → data_errors`` precedence (mirrors
    ``03_Run_History._status_cell``). Every field is read via ``.get`` so a partial/old record never
    ``KeyError``s (a missing ``status`` → non-``success`` → ``FAILED_ETL``, the honest fail-safe
    default). NEVER inspects/returns the free-text ``error`` (privacy) — category only.
    """
    if record.get("status") != "success":
        return LatestReason.FAILED_ETL
    if bool(record.get("sftp_attempted")) and not bool(record.get("sftp_ok")):
        return LatestReason.FAILED_DELIVERY
    anomalies = record.get("anomalies") or []
    if isinstance(anomalies, list) and anomalies:
        return LatestReason.ANOMALY
    if _data_errors_total(record) > 0:
        return LatestReason.DATA_WARNINGS
    return LatestReason.CLEAN


def _data_errors_total(record: dict) -> int:
    """The ``data_errors.total`` count — total: a missing/non-dict ``data_errors`` → ``0``."""
    data_errors = record.get("data_errors")
    if not isinstance(data_errors, dict):
        return 0
    return _as_int(data_errors.get("total", 0))


def is_delivery_only(record: dict) -> bool:
    """Whether this record is a deliver-from-disk attempt (0034 Slice 2) — pure + TOTAL.

    A delivery ships an EARLIER build's committed CSVs, so its record deliberately carries
    no build entity counts (the flat count keys are zeros by shape) — the ``delivery_only``
    rider lets Home / Run History render it as a delivery, never as a 0-row build. Read via
    ``.get`` so every pre-existing record (no rider) classifies as a build, unchanged.
    """
    return bool(record.get("delivery_only"))


def verdict_for_reason(reason: LatestReason) -> Verdict:
    """Map a ``LatestReason`` to its ``Verdict`` — total over the enum.

    The single source of "which reason is red vs amber vs green": the two failures are FAILED,
    anomaly/data-warnings are WARNING, CLEAN is HEALTHY. A ``KeyError`` here is a programming error
    (a new reason without a verdict) — surfaced loudly by the totality test, never swallowed.
    """
    return _REASON_VERDICTS[reason]


_REASON_VERDICTS: dict[LatestReason, Verdict] = {
    LatestReason.FAILED_ETL: Verdict.FAILED,
    LatestReason.FAILED_DELIVERY: Verdict.FAILED,
    LatestReason.ANOMALY: Verdict.WARNING,
    LatestReason.DATA_WARNINGS: Verdict.WARNING,
    LatestReason.CLEAN: Verdict.HEALTHY,
}


def _entity_counts(record: dict) -> dict[str, int]:
    """Re-bucket the record's FLAT top-level count keys into a metrics dict.

    Rostering entities always present; myBlueprint+ entities only when non-zero (defensive
    ``int`` coercion so a malformed count never crashes the metrics build)."""
    counts: dict[str, int] = {}
    for name in _ROSTERING_ENTITIES:
        counts[name] = _as_int(record.get(name))
    for name in _MYBLUEPRINT_ENTITIES:
        value = _as_int(record.get(name))
        if value > 0:
            counts[name] = value
    return counts


def _as_int(value: object) -> int:
    """Coerce a record count to ``int``; total — a missing/garbage value → ``0``."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _build_metrics(record: dict, *, now: datetime | None, counts_record: dict | None = None) -> HomeMetrics:
    """Populate the metric tiles from a delivered-success record.

    ``counts_record`` (default: the record itself) supplies the entity counts — a
    delivery-only latest carries no build counts of its own, so the caller passes the
    newest BUILD record instead (the roster the delivery actually shipped).
    """
    return HomeMetrics(
        entity_counts=_entity_counts(counts_record if counts_record is not None else record),
        last_run_display=friendly_timestamp(str(record.get("timestamp", "")), now=now),
        sftp_delivered=bool(record.get("sftp_ok")),
    )


def _counts_source(records: list[dict], latest: dict) -> dict | None:
    """The record whose entity counts describe what the latest run/delivery shipped.

    A build record IS its own counts source. A delivery-only latest shipped the newest
    BUILD's committed CSVs, so its tiles fall back to that record's counts; with no build
    on record there is no honest count → ``None`` (no tiles — never a "0 Students" lie).
    """
    if not is_delivery_only(latest):
        return latest
    for record in records:
        if not is_delivery_only(record):
            return record
    return None


def derive_home_status(
    records: list[dict] | None,
    app_config: AppConfig,
    *,
    now: datetime | None = None,
    store_created_at: str | None = None,
    schedule_status: ScheduleStatus | None = None,
) -> HomeStatus:
    """Derive the Home sync-health verdict from the run records + config (pure, TOTAL).

    Assumes a configured install — the dispatcher (IA-3b) gates un-onboarded installs to
    onboarding via ``nav.needs_setup``, so these rules only run for ``not needs_setup``.
    Evaluated top-down, first-match-wins.

    ``store_created_at`` (the run store's ``meta.created_at``, ``None`` when the store was
    never created) is the established-install signal for the fresh-start empty state — the
    view injects it from ``store.store_meta()`` so this stays pure/I-O-free.

    ``schedule_status`` (D4) is the injected tri-state schedule read-back (the view fetches it
    off-thread). When it reports ``attention`` (a schedule the config expected but the OS no
    longer has, or one that fired without completing) it becomes the DOMINANT trust fault,
    routed to Setup — never back into onboarding. A ``None`` (not yet probed / non-applicable)
    or UNKNOWN schedule is silently ignored — Home NEVER asserts an unconfirmed schedule.
    """
    # Rule: status unavailable (the never-crash floor) — the reader couldn't read the store.
    if records is None:
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="Sync status unavailable",
            detail="We couldn't read the run history right now — your nightly sync may still be running normally.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: schedule needs attention (D4) — the read-back contradicts the config (task gone
    # while expected, or fired-but-no-record). The dominant trust fault: even a clean last run
    # can't reassure if the nightly won't run again. Routed to Setup, NEVER to onboarding.
    schedule_attention = _schedule_attention(schedule_status)
    if schedule_attention is not None:
        return schedule_attention

    # Rule: no runs yet (empty but readable, configured). Two honest sub-states — the
    # run store is fresh for EVERY install after this update (no backfill from the polluted
    # log), so an established install must NOT be told "No sync has run yet":
    #   * established (finished setup once, or the store already exists) → "history starts
    #     fresh" — earlier runs live only in the old diagnostic log and aren't shown here;
    #   * otherwise → the calm "waiting for the first sync".
    # Slice 5 (D4a) re-based the discriminator on the durable ``has_completed_setup()`` fact so a
    # completed manual-only upgrader gets the honest fresh-start copy; newcomer-vs-upgrader remain
    # indistinguishable, so fresh-start is the chosen default (not a verified fact) — the copy is
    # therefore conditioned ("If you used an earlier version…"), never a flat claim of hidden
    # history. The next-run reassurance derives from the LIVE read-back, never the raw config flag.
    if not records:
        if app_config.has_completed_setup() or store_created_at:
            fresh = (
                "New syncs will appear here from now on. "
                "If you used an earlier version, its run history isn't carried over."
            )
            if _schedule_is_live(schedule_status):
                detail = fresh + f" Your next nightly sync is scheduled for {schedule_status.next_run_display}."  # type: ignore[union-attr]
            elif app_config.has_completed_setup() and _schedule_confirmed_missing(schedule_status):
                # Honest (finding #1b): a completed install with NO nightly schedule does NOT sync on
                # its own — say so plainly instead of "new syncs will appear" (which implies automation
                # that isn't set up). Calm WARNING, NO fix CTA/badge — a manual-only district must not
                # be nagged. Only fires on a CONFIRMED-absent read-back (MISSING), never on an
                # unconfirmed None/UNKNOWN (which would falsely deny a schedule we simply can't see).
                detail = (
                    "Your roster won't sync automatically until you add a nightly schedule — set one up "
                    "in Settings whenever you're ready. Manual conversions from the Convert tab appear here too."
                )
            else:
                detail = fresh
            return HomeStatus(
                verdict=Verdict.WARNING,
                headline="Run history starts fresh here",
                detail=detail,
                fix=None,
                metrics=None,
            )
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="No sync has run yet",
            detail="Your first nightly sync will appear here.",
            fix=None,  # nothing to fix — just wait for the first run
            metrics=None,
        )

    latest = records[0]

    # Classify the latest record's fault axis via the SINGLE-SOURCE precedence (shared with IA-6's
    # Run History so a Home verdict + a Run-History row/banner can never drift). Staleness is a
    # separate time-relative axis layered on top of the CLEAN reason below. Each branch keeps its
    # OWN Home copy ("your roster"/"last night's sync") + fix/metrics — the reason drives ONLY the
    # verdict selection, never the wording. NEVER interpolate the record's free-text `error`
    # (privacy) — every headline/detail is a FIXED category sentence.
    reason = classify_latest_reason(latest)

    # Rule: last run failed — the dominant fault (precedence over SFTP/anomaly/data-errors).
    if reason is LatestReason.FAILED_ETL:
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Last sync failed",
            detail="Last night's sync hit a problem and didn't finish.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: SFTP delivery failed (ETL succeeded but the roster didn't reach SpacesEDU).
    if reason is LatestReason.FAILED_DELIVERY:
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Your roster didn't reach SpacesEDU",
            detail="The data was built but the upload failed.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: anomaly / >20% drop — delivered but suspicious → attention, not failure.
    if reason is LatestReason.ANOMALY:
        anomalies = latest.get("anomalies") or []
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Something looked off in the last sync",
            detail=friendly_anomaly_detail(len(anomalies), variant=AnomalyVariant.HOME),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: data errors present — delivered, no anomaly, but some records were skipped.
    if reason is LatestReason.DATA_WARNINGS:
        total_data_errors = _data_errors_total(latest)
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline=f"Completed with {total_data_errors} data {pluralize('warning', total_data_errors)}",
            detail="A few records had field problems and were skipped — the sync still delivered.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: stale — a clean delivered success, but too old (a nightly run may have been missed).
    timestamp = str(latest.get("timestamp", ""))
    if is_stale(timestamp, now):
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="No recent sync",
            detail=(
                f"The last successful sync was {friendly_timestamp(timestamp, now=now)} — "
                "a nightly run may have been missed."
            ),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: healthy — a recent, clean, delivered success. The reassurance the surface exists to give.
    # A clean delivery-only latest counts as a fresh sync (the roster genuinely reached SpacesEDU),
    # but its tiles come from the newest BUILD record — or no tiles at all, never zeros.
    counts_record = _counts_source(records, latest)
    return HomeStatus(
        verdict=Verdict.HEALTHY,
        headline="Your roster is syncing",
        detail=f"Last sync delivered cleanly {friendly_timestamp(timestamp, now=now)}.",
        fix=None,
        metrics=_build_metrics(latest, now=now, counts_record=counts_record) if counts_record is not None else None,
    )


def _schedule_attention(schedule_status: ScheduleStatus | None) -> HomeStatus | None:
    """The schedule-attention verdict when the read-back needs a Setup fix, else ``None`` (D4).

    Renders ``schedule_status``'s single-source copy (category-only, PII-free) as a WARNING
    routed to Setup. Only fires on the ``attention`` signal (expected-MISSING or a fired-but-
    no-record contradiction); a clean LIVE, an unexpected MISSING, and every UNKNOWN return
    ``None`` — Home never nags and never asserts an unconfirmed schedule.
    """
    if schedule_status is None or not schedule_status.attention:
        return None
    return HomeStatus(
        verdict=Verdict.WARNING,
        headline=schedule_status.headline,
        detail=schedule_status.detail,
        fix=FixAction(_OPEN_SETUP_LABEL, _SETUP_FIX),
        metrics=None,
    )


def _schedule_is_live(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the injected read-back confirms a LIVE schedule with a known next-run time."""
    return (
        schedule_status is not None
        and schedule_status.state is ScheduleState.LIVE
        and bool(schedule_status.next_run_display)
    )


def _schedule_confirmed_missing(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the read-back DEFINITIVELY confirms no schedule (MISSING) — the honest-nudge signal.

    Only ``MISSING`` (the cmdlet queried the task and it's absent) may drive the "won't sync
    automatically" empty-state copy; ``None``/``UNKNOWN`` (not probed / couldn't confirm) never do
    (they'd falsely deny a schedule we can't see — the D4 honesty invariant, inverted).
    """
    return schedule_status is not None and schedule_status.state is ScheduleState.MISSING
