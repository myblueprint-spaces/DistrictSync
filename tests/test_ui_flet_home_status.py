"""Unit tests for the pure ``home_status`` derivation (IA-3a, COUNTED — the trust core).

Every rule + first-match precedence exercised on SYNTHETIC records (no filesystem);
``is_stale`` boundaries; the ``None``/``[]`` degradation sentinels; partial-record
totality (missing keys → no ``KeyError``); and the load-bearing PRIVACY invariant — a
record whose free-text ``error`` carries a filesystem path must never leak that path into
the admin-facing ``headline``/``detail``. A parametrized sweep asserts every fixture
returns a valid ``HomeStatus`` with no exception.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet.home_status import (
    MISSED_RUN_AFTER_HOURS,
    STALE_AFTER_HOURS,
    HomeStatus,
    LatestReason,
    classify_latest_reason,
    derive_home_status,
    is_delivery_only,
    is_stale,
    verdict_for_reason,
)
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus, derive_schedule_status
from src.ui_flet.verdict import Verdict


def _live_schedule(next_run_display: str = "3:00 AM") -> ScheduleStatus:
    """A LIVE ScheduleStatus with a known next-run time (the injected read-back)."""
    return ScheduleStatus(
        state=ScheduleState.LIVE,
        headline="Nightly sync is scheduled",
        detail="registered",
        next_run_display=next_run_display,
    )


# A fixed reference "now" so relative timestamps are deterministic.
_NOW = datetime(2026, 7, 4, 8, 0, 0)
_RECENT = (_NOW - timedelta(hours=5)).isoformat(timespec="seconds")  # within the stale window
_OLD = (_NOW - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")  # past it

_CONFIGURED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)


def _record(**overrides: object) -> dict:
    """A clean, recent, delivered-success record; overrides tweak one axis per test."""
    base: dict = {
        "timestamp": _RECENT,
        "status": "success",
        "duration_s": 3.1,
        "Students": 100,
        "Staff": 12,
        "Family": 80,
        "Classes": 40,
        "Enrollments": 300,
        "CourseInfo": 0,
        "StudentCourses": 0,
        "StudentAttendance": 0,
        "sftp_attempted": True,
        "sftp_ok": True,
        "error": "",
        "anomalies": [],
        "data_errors": {},
    }
    base.update(overrides)
    return base


def _derive(record: dict) -> HomeStatus:
    return derive_home_status([record], _CONFIGURED, now=_NOW)


class TestIsStale:
    def test_within_window_not_stale(self) -> None:
        assert is_stale(_RECENT, _NOW) is False

    def test_past_window_is_stale(self) -> None:
        assert is_stale(_OLD, _NOW) is True

    def test_unparseable_ts_is_not_stale(self) -> None:
        # Can't determine → don't cry wolf.
        assert is_stale("not-a-timestamp", _NOW) is False

    def test_empty_ts_is_not_stale(self) -> None:
        assert is_stale("", _NOW) is False

    def test_boundary_exactly_at_window_is_not_stale(self) -> None:
        exactly = (_NOW - timedelta(hours=STALE_AFTER_HOURS)).isoformat(timespec="seconds")
        assert is_stale(exactly, _NOW) is False  # strictly greater-than is stale

    def test_naive_aware_mismatch_is_not_stale(self) -> None:
        # An aware `now` vs a naive parsed `last_ts` → TypeError on subtraction → total (False).
        aware_now = datetime(2026, 7, 4, 8, 0, 0, tzinfo=timezone.utc)
        assert is_stale(_OLD, aware_now) is False  # _OLD is naive → can't determine

    def test_custom_stale_after_hours(self) -> None:
        two_hours_old = (_NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        assert is_stale(two_hours_old, _NOW, stale_after_hours=1) is True
        assert is_stale(two_hours_old, _NOW, stale_after_hours=3) is False


class TestUnavailableSentinel:
    def test_records_none_is_calm_warning_no_raise(self) -> None:
        status = derive_home_status(None, _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Sync status unavailable"
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.metrics is None


class TestEmptyState:
    def test_empty_established_is_fresh_start_not_no_sync(self) -> None:
        # An established (completed-setup) install with an empty store post-update must NOT be
        # told "No sync has run yet" — the store is fresh for everyone after this update.
        status = derive_home_status([], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.WARNING  # amber-toned, never red
        assert status.headline == "Run history starts fresh here"
        assert status.fix is None  # nothing to fix — just wait
        # Honesty C: the hidden-history claim is CONDITIONED (newcomer-vs-upgrader is unknown),
        # never a flat assertion that earlier runs exist.
        assert "If you used an earlier version" in status.detail

    def test_empty_established_with_live_schedule_shows_next_run(self) -> None:
        # The next-run reassurance derives from the LIVE read-back (D4), not the config flag.
        status = derive_home_status([], _CONFIGURED, now=_NOW, schedule_status=_live_schedule("3:00 AM"))
        assert status.headline == "Run history starts fresh here"
        assert "3:00 AM" in status.detail

    def test_empty_established_without_schedule_status_omits_next_run(self) -> None:
        # No injected read-back → NO schedule assertion (never claim a time we didn't confirm).
        status = derive_home_status([], _CONFIGURED, now=_NOW)
        assert "scheduled for" not in status.detail

    def test_empty_genuine_first_run_unscheduled_says_no_sync_yet(self) -> None:
        # Not established (never completed setup, no store yet) → the calm genuine-first-run copy.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=None)
        assert status.verdict is Verdict.WARNING
        assert status.headline == "No sync has run yet"
        assert "scheduled for" not in status.detail

    def test_empty_completed_manual_only_upgrader_gets_fresh_start(self) -> None:
        # D4a: a completed-setup manual-only install (unscheduled) is established via
        # setup_completed — the honest fresh-start copy, not the false "No sync has run yet".
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=None)
        assert status.headline == "Run history starts fresh here"

    def test_empty_store_created_at_signals_established_even_if_unscheduled(self) -> None:
        # A store that already exists (created_at present) is an established signal on its own.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        status = derive_home_status([], cfg, now=_NOW, store_created_at="2026-07-01T03:00:00")
        assert status.headline == "Run history starts fresh here"

    def test_empty_completed_but_confirmed_unscheduled_says_no_auto_sync(self) -> None:
        # #1b: a completed install whose read-back CONFIRMS no schedule (MISSING) must be told
        # plainly that nothing syncs on its own — NOT the "new syncs will appear" copy that implies
        # automation. Calm WARNING, NO fix CTA/badge (a manual-only district must not be nagged).
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([], cfg, now=_NOW, schedule_status=missing)
        assert status.verdict is Verdict.WARNING
        assert status.fix is None
        assert "won't sync automatically" in status.detail
        assert "New syncs will appear" not in status.detail

    def test_empty_completed_unconfirmed_schedule_keeps_neutral_fresh_start(self) -> None:
        # Honesty inverse: an UNKNOWN/None read-back must NOT assert "won't sync automatically"
        # (we can't see the schedule) — it keeps the neutral fresh-start copy.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        status = derive_home_status([], cfg, now=_NOW, schedule_status=None)
        assert "won't sync automatically" not in status.detail
        assert "New syncs will appear" in status.detail


class TestScheduleAttention:
    """D4: a schedule the config expected but the OS no longer has (or one that fired without
    completing) is the dominant fault — WARNING routed to Setup, never onboarding."""

    def test_expected_missing_routes_to_setup(self) -> None:
        sched = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"
        # #2b: the CTA names the ACTION, not the destination (Firefighter landing precision).
        assert status.fix.label == "Fix the nightly schedule"
        assert status.metrics is None

    def test_contradiction_routes_to_setup(self) -> None:
        # A record-gap contradiction: the task fired more recently than the newest record.
        sched = derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_unknown_schedule_never_overrides_a_healthy_run(self) -> None:
        # A failed query must not manufacture a fault — Home falls through to the record rules.
        sched = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.HEALTHY

    def test_clean_live_schedule_does_not_override_a_healthy_run(self) -> None:
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=_live_schedule())
        assert status.verdict is Verdict.HEALTHY

    def test_unexpected_missing_does_not_warn(self) -> None:
        # A configured manual-only install that never scheduled → not a fault on Home.
        sched = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.HEALTHY


class TestFailureBeatsScheduleAttention:
    """W3-B: **a failed sync is never masked by a schedule warning** (the module's own documented
    precedence — "failures above warnings"). The schedule-attention rule used to return BEFORE the
    two FAILED rules, so a failed run under an expected-MISSING / fired-but-no-record schedule
    rendered as an amber *schedule* warning and the failure went unmentioned — and, because
    ``screens/home.py`` paints the record-derived verdict first and re-derives when the async probe
    lands, the admin watched a red "sync failed" band downgrade itself to amber a second later.

    The restored rule: the two FAILED reasons outrank schedule attention; attention still outranks
    every WARNING-tier reason, the empty state and HEALTHY. Because the failure's fix CTA can only
    point one way, the *confirmed-gone* schedule is surfaced as a bounded secondary CLAUSE on the
    failure's detail (never a second CTA, never a new verdict tier).
    """

    _FAILED_ETL_DETAIL = "The sync that ran 5 hours ago hit a problem and didn't finish."
    _FAILED_DELIVERY_DETAIL = "The data was built but the upload failed."
    _SCHEDULE_GONE_CLAUSE = (
        "Your nightly schedule is also no longer registered with Windows — "
        "re-register it in Settings so the sync can run again."
    )

    @staticmethod
    def _expected_missing() -> ScheduleStatus:
        """The Event-141 shape: the config expected a schedule, the OS definitively has none."""
        return derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)

    @staticmethod
    def _contradiction() -> ScheduleStatus:
        """The LIVE fired-but-no-record shape: the task fired newer than the newest record."""
        return derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )

    @staticmethod
    def _failed_etl() -> dict:
        return _record(status="failed")

    @staticmethod
    def _failed_delivery() -> dict:
        return _record(sftp_attempted=True, sftp_ok=False)

    # -- the masking defect itself ------------------------------------------------------- #

    def test_failed_etl_is_not_masked_by_an_expected_missing_schedule(self) -> None:
        status = derive_home_status(
            [self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"
        # The fix stays on the DOMINANT fault — the failure is investigated in Run History.
        assert status.fix is not None and status.fix.dest_id == "run_history"

    def test_failed_etl_is_not_masked_by_a_fired_but_no_record_contradiction(self) -> None:
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction())
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_is_not_masked_by_an_expected_missing_schedule(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_failed_delivery_is_not_masked_by_a_contradiction(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    # -- the secondary fact: surfaced, but only when it is NOT already the failure's story - #

    def test_confirmed_gone_schedule_is_named_as_a_secondary_clause_on_a_failed_etl(self) -> None:
        # Both faults are real; only one CTA can exist. The failure keeps the band + button, and the
        # schedule fact rides along as a bounded clause so the admin doesn't fix the run and walk
        # away believing tonight's sync will resume (it positively won't — the task is gone).
        status = derive_home_status(
            [self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.detail == f"{self._FAILED_ETL_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    def test_confirmed_gone_schedule_is_named_as_a_secondary_clause_on_a_failed_delivery(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.detail == f"{self._FAILED_DELIVERY_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    def test_a_contradiction_adds_no_clause_because_the_failure_already_tells_that_story(self) -> None:
        # The LIVE contradiction's own copy is "your last scheduled run reported a problem" — the
        # SAME category the FAILED band already names, with less precision, and the schedule itself
        # is still registered. Restating it would duplicate, not inform (category-only faults).
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction())
        assert status.detail == self._FAILED_ETL_DETAIL

    @pytest.mark.parametrize(
        "schedule",
        [
            None,  # not probed yet (the first paint)
            _live_schedule(),  # a clean LIVE schedule
            derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None),
            derive_schedule_status(
                ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
            ),
        ],
        ids=["unprobed", "live", "unexpected-missing", "unknown"],
    )
    def test_no_clause_when_the_schedule_is_not_confirmed_gone(self, schedule: ScheduleStatus | None) -> None:
        # D4 honesty, inverted: an UNKNOWN/unprobed read-back must never be spoken of as a fault,
        # and an unexpected MISSING (a manual-only district) is not a broken promise.
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=schedule)
        assert status.detail == self._FAILED_ETL_DETAIL

    def test_secondary_clause_is_fixed_copy_and_leaks_no_record_free_text(self) -> None:
        # Privacy (LIVE/top): the clause is authored copy, not a field lifted off the record — a
        # poisoned free-text `error` cannot ride into it.
        secret = r"C:\Users\x\secret\roster.csv"
        status = derive_home_status(
            [_record(status="failed", error=f"FileNotFoundError: {secret}")],
            _CONFIGURED,
            now=_NOW,
            schedule_status=self._expected_missing(),
        )
        assert secret not in status.detail
        assert "secret" not in status.detail
        assert status.detail == f"{self._FAILED_ETL_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    # -- attention keeps outranking everything BELOW the failures ------------------------- #

    @pytest.mark.parametrize(
        "override",
        [
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 3}},
            {"timestamp": _OLD},
            {},  # a clean, recent, HEALTHY latest
        ],
        ids=["anomaly", "data-warnings", "stale", "healthy"],
    )
    def test_schedule_attention_still_outranks_every_non_failed_latest(self, override: dict) -> None:
        # The demotion is surgical: attention now loses ONLY to the FAILED tier. A nightly that
        # won't run again still dominates an amber record fault and a green one.
        status = derive_home_status(
            [_record(**override)], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Your schedule isn't registered anymore"
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_schedule_attention_still_wins_over_an_empty_store(self) -> None:
        # No record exists → nothing to mask; the schedule fault remains the whole story.
        status = derive_home_status([], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing())
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Your schedule isn't registered anymore"

    # -- the no-flip pin (the observable symptom in screens/home.py) --------------------- #

    @pytest.mark.parametrize("shape", ["failed_etl", "failed_delivery"])
    @pytest.mark.parametrize("flavor", ["expected_missing", "contradiction"])
    def test_verdict_never_downgrades_when_the_async_schedule_probe_lands(self, shape: str, flavor: str) -> None:
        # ``screens/home.py`` paints ``_render(None)`` from the store, then re-renders in place when
        # the off-thread probe returns. A trust instrument must not downgrade its own alarm, so the
        # two paints must agree on verdict AND headline; only the detail may GROW (the secondary
        # clause). This is the pin for the flip the admin actually watched.
        record = self._failed_etl() if shape == "failed_etl" else self._failed_delivery()
        schedule = self._expected_missing() if flavor == "expected_missing" else self._contradiction()

        first_paint = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=None)
        second_paint = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)

        assert second_paint.verdict is first_paint.verdict
        assert second_paint.headline == first_paint.headline
        assert second_paint.detail.startswith(first_paint.detail)
        assert second_paint.fix == first_paint.fix

    @pytest.mark.parametrize("flavor", ["expected_missing", "contradiction"])
    def test_probe_never_lowers_the_severity_of_any_latest_record(self, flavor: str) -> None:
        # The general invariant behind the pin above: across every fault shape, learning the
        # schedule may ESCALATE the verdict (a clean record under a dead schedule) but must never
        # de-escalate it. ``Verdict`` is declared in escalating-attention order.
        severity = list(Verdict)
        schedule = self._expected_missing() if flavor == "expected_missing" else self._contradiction()
        for override in (
            {"status": "failed"},
            {"sftp_attempted": True, "sftp_ok": False},
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 2}},
            {"timestamp": _OLD},
            {},
        ):
            record = _record(**override)
            first = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=None)
            second = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
            assert severity.index(second.verdict) >= severity.index(first.verdict), override


class TestMissedRun:
    """The owner rule (2026-07-15): a CONFIRMED-LIVE schedule + no run record in the last 26h +
    an established store → the missed-run WARNING. Every guard failing → stay silent (a false
    "missed run" on day one costs more trust than a one-day-late first warning)."""

    _MISSED_HEADLINE = "We expected a nightly sync that didn't arrive"
    # The store's birth stamp, comfortably older than the missed-run window.
    _ESTABLISHED = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
    # A clean record just past the window (but well inside STALE_AFTER_HOURS).
    _PAST_WINDOW = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS + 1)).isoformat(timespec="seconds")

    def test_live_empty_established_store_warns_missed(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == self._MISSED_HEADLINE
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.metrics is None

    def test_live_record_past_window_warns_missed(self) -> None:
        status = derive_home_status(
            [_record(timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == self._MISSED_HEADLINE

    def test_live_recent_record_stays_healthy(self) -> None:
        status = derive_home_status(
            [_record()], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.HEALTHY

    def test_fresh_store_guard_stays_silent(self) -> None:
        # A store younger than the window (day-one install) has not missed anything yet.
        fresh = (_NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=fresh, schedule_status=_live_schedule())
        assert status.headline == "Run history starts fresh here"

    def test_no_store_meta_stays_silent(self) -> None:
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=None, schedule_status=_live_schedule())
        assert status.headline != self._MISSED_HEADLINE

    def test_unparseable_created_at_stays_silent(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at="garbage", schedule_status=_live_schedule()
        )
        assert status.headline == "Run history starts fresh here"

    def test_unconfirmed_schedule_never_fires_missed(self) -> None:
        # D4 honesty: None (not probed) and UNKNOWN (query failed) never assert a miss — the
        # schedule-unaware staleness proxy remains the honest fallback for an old clean record.
        unknown = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        for sched in (None, unknown):
            status = derive_home_status(
                [_record(timestamp=_OLD)],
                _CONFIGURED,
                now=_NOW,
                store_created_at=self._ESTABLISHED,
                schedule_status=sched,
            )
            assert status.headline == "No recent sync"

    def test_confirmed_missing_schedule_does_not_fire_missed(self) -> None:
        # An unexpected MISSING (manual-only install) is not a missed run — nothing was promised.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=missing)
        assert status.headline != self._MISSED_HEADLINE

    def test_failed_latest_is_never_masked_by_missed_run(self) -> None:
        # Failures above warnings: an old FAILED record keeps the red verdict, not this amber.
        status = derive_home_status(
            [_record(status="failed", timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_latest_is_never_masked_by_missed_run(self) -> None:
        status = derive_home_status(
            [_record(sftp_attempted=True, sftp_ok=False, timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    def test_missed_run_precedes_old_anomaly_warning(self) -> None:
        # Among WARNINGs the missed run is the fresher fact — the anomaly copy would describe
        # a run that is over a day old.
        status = derive_home_status(
            [_record(anomalies=["ANOMALY: x"], timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.headline == self._MISSED_HEADLINE

    def test_boundary_exactly_at_window_stays_silent(self) -> None:
        exactly = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS)).isoformat(timespec="seconds")
        status = derive_home_status(
            [_record(timestamp=exactly)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY  # strictly-greater-than, mirrors is_stale

    def test_unparseable_newest_timestamp_stays_silent(self) -> None:
        # Can't establish the gap → don't cry wolf (the record still classifies normally).
        status = derive_home_status(
            [_record(timestamp="garbage")],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.headline != self._MISSED_HEADLINE

    def test_detail_is_plain_language_no_raw_values(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert "26" not in status.detail  # the window is copy-free plain language ("the last day")
        assert self._ESTABLISHED not in status.detail  # never the raw ISO


def _windowed(**over: object) -> AppConfig:
    """A configured install WITH the seasonal window enabled (Aug 11 -> Jul 6, wrap-around)."""
    base: dict = dict(
        input_dir="/in",
        output_dir="/out",
        sis_type="myedbc",
        schedule_registered=True,
        sync_window_enabled=True,
        sync_window_start="08-11",
        sync_window_end="07-06",
    )
    base.update(over)
    return AppConfig(**base)


# A "now" comfortably OUTSIDE the Aug 11 -> Jul 6 window (mid-July summer break) and one INSIDE it.
_SUMMER = datetime(2026, 7, 20, 8, 0, 0)  # 07-20: > Jul 6 and < Aug 11 -> paused
_SUMMER_ESTABLISHED = (_SUMMER - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
_PAUSED_HEADLINE = "Paused for the summer"
_MISSED_HEADLINE = "We expected a nightly sync that didn't arrive"


class TestSeasonalPause:
    """B: while an ENABLED seasonal window is OUTSIDE its active season, no nightly sync arrives by
    design — the missed-run and stale warnings must be SUPPRESSED and a calm HEALTHY-toned
    "Paused for the summer — resumes <date>" state shown instead. A genuinely FAILED latest record
    still surfaces (a real failure isn't hidden by summer); missed-run + stale never fire in a pause.
    """

    def test_reproduce_missed_run_is_suppressed_and_paused_shows(self) -> None:
        # Reproduce-first (RED on the pre-slice base, which ignores the window): a LIVE schedule +
        # an established store + no records is a textbook missed-run — but we are OUTSIDE an enabled
        # window, so the expected nightly is a summer no-op. Home must show the calm paused state.
        status = derive_home_status(
            [],
            _windowed(),
            now=_SUMMER,
            store_created_at=_SUMMER_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY  # an intentional pause is healthy, never amber/red
        assert status.headline == _PAUSED_HEADLINE
        assert _MISSED_HEADLINE not in status.headline
        assert "Aug 11" in status.detail  # friendly resume date, PII-free (no raw ISO)
        assert _SUMMER_ESTABLISHED not in status.detail
        assert status.fix is None

    def test_missed_run_with_a_record_is_also_suppressed_in_a_pause(self) -> None:
        # The record-present missed-run path (newest record past the window) is suppressed too.
        old = (_SUMMER - timedelta(hours=MISSED_RUN_AFTER_HOURS + 1)).isoformat(timespec="seconds")
        status = derive_home_status(
            [_record(timestamp=old)],
            _windowed(),
            now=_SUMMER,
            store_created_at=_SUMMER_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE

    def test_stale_clean_success_is_suppressed_in_a_pause(self) -> None:
        # A clean-but-old success in summer is expected (nothing runs) — no "No recent sync" warning.
        old = (_SUMMER - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")
        status = derive_home_status([_record(timestamp=old)], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE
        assert "No recent sync" not in status.headline

    def test_failed_latest_still_surfaces_in_a_pause(self) -> None:
        # Non-negotiable: a REAL failure is never hidden by summer — FAILED outranks the pause.
        status = derive_home_status([_record(status="failed")], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_latest_still_surfaces_in_a_pause(self) -> None:
        status = derive_home_status([_record(sftp_attempted=True, sftp_ok=False)], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    def test_expected_missing_schedule_still_surfaces_in_a_pause(self) -> None:
        # A genuinely gone task makes "resumes <date>" a lie — the MISSING attention still surfaces
        # (only the by-design LIVE fired-but-no-record contradiction is suppressed during a pause).
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
        status = derive_home_status([_record()], _windowed(), now=_SUMMER, schedule_status=missing)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_live_fired_but_no_record_contradiction_is_suppressed_in_a_pause(self) -> None:
        # The paused nightly fires (LastRunTime advances) but writes NO record by design — the
        # resulting fired-but-no-record contradiction is a false alarm in summer and is suppressed.
        contradiction = derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-19T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )
        status = derive_home_status([_record()], _windowed(), now=_SUMMER, schedule_status=contradiction)
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE

    def test_boundary_last_active_day_is_not_paused(self) -> None:
        # Jul 6 is the INCLUSIVE last active day -> normal behavior (not paused).
        on_boundary = datetime(2026, 7, 6, 8, 0, 0)
        status = derive_home_status(
            [_record(timestamp=(on_boundary - timedelta(hours=5)).isoformat())], _windowed(), now=on_boundary
        )
        assert status.headline != _PAUSED_HEADLINE

    def test_boundary_first_paused_day(self) -> None:
        # Jul 7 is the first day OUTSIDE the window -> paused.
        first_paused = datetime(2026, 7, 7, 8, 0, 0)
        status = derive_home_status([_record(timestamp=_OLD)], _windowed(), now=first_paused)
        assert status.headline == _PAUSED_HEADLINE

    def test_inside_window_missed_run_still_fires(self) -> None:
        # Window ENABLED but today INSIDE it (mid-June) -> normal cadence rules, missed-run fires.
        june = datetime(2026, 6, 15, 8, 0, 0)
        established = (june - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
        status = derive_home_status(
            [], _windowed(), now=june, store_created_at=established, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == _MISSED_HEADLINE

    def test_disabled_window_is_year_round_unchanged(self) -> None:
        # The opt-in default: disabled window -> byte-identical to today (no paused state ever).
        cfg = _windowed(sync_window_enabled=False)
        status = derive_home_status(
            [], cfg, now=_SUMMER, store_created_at=_SUMMER_ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.headline == _MISSED_HEADLINE  # the missed-run warning fires as before

    def test_blank_bounds_never_pause(self) -> None:
        # Enabled but with unset bounds (never configured) -> treated as year-round, never paused.
        cfg = _windowed(sync_window_start="", sync_window_end="")
        status = derive_home_status([_record(timestamp=_OLD)], cfg, now=_SUMMER)
        assert status.headline != _PAUSED_HEADLINE

    def test_malformed_bounds_never_pause(self) -> None:
        # A malformed window (should be gated at save, but be TOTAL) -> year-round, never crash.
        cfg = _windowed(sync_window_start="13-40", sync_window_end="xx-yy")
        status = derive_home_status([_record(timestamp=_OLD)], cfg, now=_SUMMER)
        assert isinstance(status, HomeStatus)
        assert status.headline != _PAUSED_HEADLINE

    def test_paused_detail_is_plain_language_and_pii_free(self) -> None:
        status = derive_home_status([], _windowed(), now=_SUMMER, store_created_at=_SUMMER_ESTABLISHED)
        assert "08-11" not in status.detail  # never the raw MM-DD / ISO
        assert "Nothing is wrong" in status.detail


class TestFailedRules:
    def test_failed_etl_is_failed_verdict(self) -> None:
        status = _derive(_record(status="failed"))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"
        # 0032 T2 #4: an ETL failure is investigated in Run History — the label says where it goes.
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.fix.label == "Check Run History"

    def test_failed_etl_detail_derives_from_the_records_timestamp(self) -> None:
        # 0032 T1 #1b: never the hard-coded "Last night's…" — a failed latest can be any age,
        # so the copy dates the failed run from its own timestamp via friendly_timestamp.
        status = _derive(_record(status="failed"))
        assert status.detail == "The sync that ran 5 hours ago hit a problem and didn't finish."

    def test_failed_etl_detail_missing_timestamp_reads_recently(self) -> None:
        # Totality: no timestamp → friendly_timestamp's safe fallback, never a raw/blank slot.
        rec = _record(status="failed")
        del rec["timestamp"]
        status = _derive(rec)
        assert status.detail == "The sync that ran recently hit a problem and didn't finish."

    def test_failed_etl_precedes_sftp_and_data_errors(self) -> None:
        # A failed ETL is the dominant fault even with an SFTP failure + data errors also set.
        status = _derive(_record(status="failed", sftp_attempted=True, sftp_ok=False, data_errors={"total": 9}))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_sftp_delivery_failed_is_failed_verdict(self) -> None:
        status = _derive(_record(sftp_attempted=True, sftp_ok=False))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"
        # 0032 T2 #4: the delivery fix (host/credentials) lives in Settings' delivery section,
        # not the read-only run ledger — and the label names the destination.
        assert status.fix is not None and status.fix.dest_id == "setup"
        assert status.fix.label == "Open Settings"

    def test_sftp_failed_precedes_data_errors(self) -> None:
        status = _derive(_record(sftp_attempted=True, sftp_ok=False, data_errors={"total": 3}))
        assert status.headline == "Your roster didn't reach SpacesEDU"


class TestWarningRules:
    def test_anomaly_is_warning(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: Students dropped from 200 to 100 rows (50% decrease)"]))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Something looked off in the last sync"
        # Never surface the raw ANOMALY:-prefixed string.
        assert "ANOMALY:" not in status.detail
        assert "One roster file" in status.detail

    def test_multiple_anomalies_plural_detail(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: a", "ANOMALY: b"]))
        assert "2 roster files" in status.detail

    def test_anomaly_precedes_data_errors(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: x"], data_errors={"total": 4}))
        assert status.headline == "Something looked off in the last sync"

    def test_data_errors_is_warning(self) -> None:
        status = _derive(_record(data_errors={"total": 3, "by_field": {"Students.email": 3}}))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Completed with 3 data warnings"
        assert status.fix is not None and status.fix.dest_id == "run_history"

    def test_single_data_error_singular_headline(self) -> None:
        status = _derive(_record(data_errors={"total": 1}))
        assert status.headline == "Completed with 1 data warning"

    def test_stale_clean_success_is_warning(self) -> None:
        status = _derive(_record(timestamp=_OLD))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "No recent sync"
        assert _OLD not in status.detail  # plain relative phrase, not the raw ISO
        assert status.fix is not None and status.fix.dest_id == "run_history"


class TestHealthy:
    def test_recent_clean_delivered_success_is_healthy_with_metrics(self) -> None:
        status = _derive(_record())
        assert status.verdict is Verdict.HEALTHY
        # 0032 T1 #1c: no schedule read-back injected → never assert ongoing automation.
        assert status.headline == "Your roster is up to date"
        assert status.fix is None
        assert status.metrics is not None
        assert status.metrics.sftp_delivered is True
        # Raw ISO never leaks into the healthy detail.
        assert _RECENT not in status.detail

    def test_healthy_headline_asserts_syncing_only_on_live_readback(self) -> None:
        # 0032 T1 #1c: "syncing" claims ongoing automation → demands a CONFIRMED-LIVE read-back.
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=_live_schedule())
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == "Your roster is syncing"

    def test_healthy_headline_stays_neutral_on_unconfirmed_readback(self) -> None:
        # An UNKNOWN read-back (query failed) must not upgrade the claim — honesty inverse of D4.
        unknown = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=unknown)
        assert status.headline == "Your roster is up to date"

    def test_healthy_delivered_detail_names_spacesedu(self) -> None:
        # 0032 T1 #1a: "delivered" claims branch on the record's SFTP axis — sftp_ok names the
        # actual destination, never the old axis-blind "delivered cleanly".
        status = _derive(_record())
        assert status.detail == "Last sync delivered to SpacesEDU 5 hours ago."

    def test_healthy_no_sftp_detail_says_completed_to_output_folder(self) -> None:
        # A run that never attempted SFTP must NEVER claim a delivery that didn't happen.
        status = _derive(_record(sftp_attempted=False, sftp_ok=False))
        assert status.verdict is Verdict.HEALTHY
        assert status.detail == "Last sync completed 5 hours ago — files were written to your output folder."
        assert "delivered" not in status.detail
        assert status.metrics is not None
        assert status.metrics.sftp_delivered is False

    def test_metrics_show_5_rostering_tiles_not_7_with_zeros(self) -> None:
        # A SpacesEDU district run (myBlueprint+ counts 0) shows exactly the 5 rostering tiles.
        status = _derive(_record())
        assert status.metrics is not None
        assert set(status.metrics.entity_counts) == {"Students", "Staff", "Family", "Classes", "Enrollments"}
        assert "CourseInfo" not in status.metrics.entity_counts
        assert "StudentAttendance" not in status.metrics.entity_counts

    def test_metrics_include_myblueprint_tiles_when_non_zero(self) -> None:
        status = _derive(_record(CourseInfo=15, StudentCourses=200))
        assert status.metrics is not None
        assert status.metrics.entity_counts["CourseInfo"] == 15
        assert status.metrics.entity_counts["StudentCourses"] == 200


class TestPrivacyNoErrorLeak:
    _SECRET = r"C:\Users\x\secret"

    def test_failed_record_error_path_never_leaks(self) -> None:
        # The free-text `error` (str(e), can carry a path/sis_type) must NEVER appear
        # in the admin-facing headline/detail. Category-only fault naming.
        status = _derive(_record(status="failed", error=f"FileNotFoundError: {self._SECRET}\\input.csv"))
        assert self._SECRET not in status.detail
        assert self._SECRET not in status.headline
        assert "secret" not in status.detail
        # The FIXED category sentence — only the record's own timestamp is rendered (plain phrase).
        assert status.detail == "The sync that ran 5 hours ago hit a problem and didn't finish."

    def test_secret_never_leaks_across_any_rule(self) -> None:
        # Even on delivered/anomaly/data-error rules where `error` may be populated, it never leaks.
        for extra in (
            {"status": "failed"},
            {"sftp_attempted": True, "sftp_ok": False},
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 2}},
        ):
            status = _derive(_record(error=self._SECRET, **extra))
            assert self._SECRET not in status.detail
            assert self._SECRET not in status.headline


class TestTotalityPartialRecords:
    def test_missing_keys_do_not_raise(self) -> None:
        # A partial/old-schema record (only a status) classifies via .get defaults, no KeyError.
        status = derive_home_status([{"status": "success"}], _CONFIGURED, now=_NOW)
        assert isinstance(status, HomeStatus)
        assert status.verdict in Verdict

    def test_empty_record_dict_does_not_raise(self) -> None:
        status = derive_home_status([{}], _CONFIGURED, now=_NOW)
        assert isinstance(status, HomeStatus)

    def test_unparseable_timestamp_skips_staleness_still_classifies(self) -> None:
        # A clean delivered success with a garbage timestamp → staleness skipped → HEALTHY, no crash.
        status = _derive(_record(timestamp="garbage-timestamp"))
        assert status.verdict is Verdict.HEALTHY

    def test_garbage_count_values_do_not_crash_metrics(self) -> None:
        status = _derive(_record(Students="not-a-number", CourseInfo=None))
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 0


class TestClassifyLatestReason:
    """The shared single-source status→reason precedence IA-6 also consumes (staleness EXCLUDED)."""

    def test_failed_etl(self) -> None:
        assert classify_latest_reason(_record(status="failed")) is LatestReason.FAILED_ETL

    def test_missing_status_is_failed_etl(self) -> None:
        assert classify_latest_reason({}) is LatestReason.FAILED_ETL

    def test_failed_delivery(self) -> None:
        assert classify_latest_reason(_record(sftp_attempted=True, sftp_ok=False)) is LatestReason.FAILED_DELIVERY

    def test_anomaly(self) -> None:
        assert classify_latest_reason(_record(anomalies=["ANOMALY: x"])) is LatestReason.ANOMALY

    def test_data_warnings(self) -> None:
        assert classify_latest_reason(_record(data_errors={"total": 3})) is LatestReason.DATA_WARNINGS

    def test_clean(self) -> None:
        assert classify_latest_reason(_record()) is LatestReason.CLEAN

    def test_precedence_failed_over_all(self) -> None:
        rec = _record(
            status="failed", sftp_attempted=True, sftp_ok=False, anomalies=["ANOMALY: y"], data_errors={"total": 9}
        )
        assert classify_latest_reason(rec) is LatestReason.FAILED_ETL

    def test_staleness_is_not_a_reason(self) -> None:
        # A stale-but-clean record is still CLEAN — staleness is a separate axis, not a reason.
        assert classify_latest_reason(_record(timestamp=_OLD)) is LatestReason.CLEAN


class TestVerdictForReason:
    def test_total_over_every_reason(self) -> None:
        for reason in LatestReason:
            assert verdict_for_reason(reason) in Verdict

    def test_reason_verdict_mapping(self) -> None:
        assert verdict_for_reason(LatestReason.FAILED_ETL) is Verdict.FAILED
        assert verdict_for_reason(LatestReason.FAILED_DELIVERY) is Verdict.FAILED
        assert verdict_for_reason(LatestReason.ANOMALY) is Verdict.WARNING
        assert verdict_for_reason(LatestReason.DATA_WARNINGS) is Verdict.WARNING
        assert verdict_for_reason(LatestReason.CLEAN) is Verdict.HEALTHY


def _delivery_record(**overrides: object) -> dict:
    """A deliver-from-disk record (0034 Slice 2): zero count keys by shape + the rider."""
    base = _record(
        Students=0,
        Staff=0,
        Family=0,
        Classes=0,
        Enrollments=0,
        delivery_only=True,
        source="manual",
    )
    base.update(overrides)
    return base


class TestDeliveryOnlyRecord:
    """A delivery ships an EARLIER build — its record must never read as a 0-row build."""

    def test_rider_discriminates_delivery_from_build(self) -> None:
        assert is_delivery_only(_delivery_record()) is True
        assert is_delivery_only(_record()) is False  # pre-existing records classify as builds

    def test_clean_delivery_refreshes_freshness_with_the_builds_counts(self) -> None:
        # The build is past the stale window, but the delivery re-dates the sync (the roster
        # genuinely reached SpacesEDU) — and the tiles show the BUILD's counts, never zeros.
        build = _record(timestamp=_OLD)
        delivery = _delivery_record(timestamp=_RECENT)
        status = derive_home_status([delivery, build], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100
        assert status.metrics.last_run_display == "5 hours ago"  # the delivery's timestamp
        assert status.metrics.sftp_delivered is True

    def test_delivery_with_no_build_on_record_shows_no_tiles(self) -> None:
        # No build record to source counts from → no tiles at all — never a "0 Students" lie.
        status = derive_home_status([_delivery_record()], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is None

    def test_delivery_over_failed_build_uses_the_successful_builds_counts(self) -> None:
        # A failed build (zero counts — atomic save_all rolled back, nothing committed)
        # sitting between the good build and the delivery must never feed the HEALTHY
        # tiles: the delivery shipped the GOOD build's on-disk CSVs.
        good = _record(timestamp=_OLD)
        failed = _record(status="failed", timestamp=_OLD, Students=0, Staff=0, Family=0, Classes=0, Enrollments=0)
        delivery = _delivery_record(timestamp=_RECENT)
        status = derive_home_status([delivery, failed, good], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100

    def test_delivery_with_only_failed_builds_shows_no_tiles(self) -> None:
        # No SUCCESSFUL build on record → no honest count exists → no tiles, never zeros.
        failed = _record(status="failed", timestamp=_OLD, Students=0, Staff=0, Family=0, Classes=0, Enrollments=0)
        status = derive_home_status([_delivery_record(), failed], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is None

    def test_failed_delivery_only_is_the_failed_delivery_verdict(self) -> None:
        status = derive_home_status([_delivery_record(sftp_ok=False)], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.FAILED
        assert "didn't reach SpacesEDU" in status.headline
        # Delivery-only failure built nothing this run — the copy must not claim a build.
        assert status.detail == "The upload of your saved files failed."

    def test_failed_delivery_after_a_build_keeps_the_build_copy(self) -> None:
        status = derive_home_status([_record(sftp_ok=False)], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.FAILED
        assert status.detail == "The data was built but the upload failed."

    def test_build_latest_keeps_its_own_counts(self) -> None:
        # Regression: a build latest is its own counts source (delivery records behind it).
        status = derive_home_status([_record(), _delivery_record(timestamp=_OLD)], _CONFIGURED, now=_NOW)
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100


# Every representative fixture → a valid HomeStatus, no exception (totality sweep).
_SWEEP_INPUTS = [
    None,
    [],
    [_record()],
    [_record(status="failed", error=r"boom C:\path\x")],
    [_record(sftp_attempted=True, sftp_ok=False)],
    [_record(anomalies=["ANOMALY: a"])],
    [_record(data_errors={"total": 5})],
    [_record(timestamp=_OLD)],
    [_record(timestamp="garbage")],
    [{}],
    [{"status": "success", "sftp_attempted": True}],
    [{"anomalies": "not-a-list"}],  # non-list anomalies must be tolerated
    [_delivery_record()],
    [_delivery_record(sftp_ok=False)],
]


@pytest.mark.parametrize("records", _SWEEP_INPUTS)
def test_derivation_is_total_over_all_inputs(records: list[dict] | None) -> None:
    status = derive_home_status(records, _CONFIGURED, now=_NOW)
    assert isinstance(status, HomeStatus)
    assert status.verdict in Verdict
    assert status.headline and status.detail
