"""Pure Home status-derivation — the trust core of the sync-health cockpit.

NO ``flet`` import. Given the parsed run-log records (newest-first, from
``run_log.read_run_records``) + the ``AppConfig`` state, derive a single
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

from src.config.app_config import AppConfig
from src.ui_flet.humanize import friendly_timestamp
from src.ui_flet.verdict import Verdict

# The 5 SpacesEDU rostering entities always shown, then the 2 myBlueprint+ entities
# shown ONLY when non-zero (a SpacesEDU district run shows 5 tiles, not 7-with-two-zeros).
# ``StudentAttendance`` is deliberately omitted, mirroring ``03_Run_History``'s columns.
_ROSTERING_ENTITIES: tuple[str, ...] = ("Students", "Staff", "Family", "Classes", "Enrollments")
_MYBLUEPRINT_ENTITIES: tuple[str, ...] = ("CourseInfo", "StudentCourses")

STALE_AFTER_HOURS = 36
"""A nightly job → a successful run older than ~1.5 nightly cycles is "no recent sync".
One generous constant absorbs timezone/clock skew (KISS — no per-tz math for a tool an
admin opens 2-3x/yr); staleness is only ever a WARNING, never a FAILED."""

_RUN_HISTORY_FIX = "run_history"

_CHECK_RUN_HISTORY_LABEL = "Check Run History"


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


def _build_metrics(record: dict, *, now: datetime | None) -> HomeMetrics:
    """Populate the metric tiles from a delivered-success record."""
    return HomeMetrics(
        entity_counts=_entity_counts(record),
        last_run_display=friendly_timestamp(str(record.get("timestamp", "")), now=now),
        sftp_delivered=bool(record.get("sftp_ok")),
    )


def derive_home_status(
    records: list[dict] | None,
    app_config: AppConfig,
    *,
    now: datetime | None = None,
) -> HomeStatus:
    """Derive the Home sync-health verdict from the run records + config (pure, TOTAL).

    Assumes a configured + scheduled install — the dispatcher (IA-3b) gates unconfigured
    installs to onboarding via ``nav.needs_setup``, so these rules only run for
    ``not needs_setup(app_config)``. Evaluated top-down, first-match-wins.
    """
    # Rule: status unavailable (the never-crash floor) — the reader couldn't read the log.
    if records is None:
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="Sync status unavailable",
            detail="We couldn't read the run log right now — your nightly sync may still be running normally.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: no runs yet (empty but readable, configured) — calm, never red.
    if not records:
        detail = "Your first nightly sync will appear here"
        if app_config.schedule_registered:
            detail += f" — scheduled for {_friendly_schedule_time(app_config.schedule_time)} each night."
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="No sync has run yet",
            detail=detail,
            fix=None,  # nothing to fix — just wait for the first run
            metrics=None,
        )

    latest = records[0]

    # Rule: last run failed — the dominant fault (precedence over SFTP/anomaly/data-errors).
    # NEVER interpolate the record's free-text `error` (privacy) — a FIXED category sentence.
    if latest.get("status") != "success":
        return HomeStatus(
            verdict=Verdict.FAILED,
            headline="Last sync failed",
            detail="Last night's sync hit a problem and didn't finish.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: SFTP delivery failed (ETL succeeded but the roster didn't reach SpacesEDU).
    if bool(latest.get("sftp_attempted")) and not bool(latest.get("sftp_ok")):
        return HomeStatus(
            verdict=Verdict.FAILED,
            headline="Your roster didn't reach SpacesEDU",
            detail="The data was built but the upload failed.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: anomaly / >20% drop — delivered but suspicious → attention, not failure.
    anomalies = latest.get("anomalies") or []
    if isinstance(anomalies, list) and anomalies:
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="Something looked off in the last sync",
            detail=_anomaly_detail(len(anomalies)),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: data errors present — delivered, no anomaly, but some records were skipped.
    total_data_errors = _as_int((latest.get("data_errors") or {}).get("total", 0))
    if total_data_errors > 0:
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline=f"Completed with {total_data_errors} data {_pluralize('warning', total_data_errors)}",
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
    return HomeStatus(
        verdict=Verdict.HEALTHY,
        headline="Your roster is syncing",
        detail=f"Last sync delivered cleanly {friendly_timestamp(timestamp, now=now)}.",
        fix=None,
        metrics=_build_metrics(latest, now=now),
    )


def _anomaly_detail(count: int) -> str:
    """Plain-language anomaly summary — NEVER the raw ``ANOMALY:``-prefixed string."""
    if count == 1:
        return "One roster file was smaller than usual."
    return f"{count} roster files were smaller than usual."


def _pluralize(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _friendly_schedule_time(schedule_time: str) -> str:
    """Turn a ``HH:MM`` schedule time into a plain "3:00 AM"; total — bad input passes through."""
    text = (schedule_time or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except (ValueError, TypeError):
        return text or "each night"
    return parsed.strftime("%I:%M %p").lstrip("0")
