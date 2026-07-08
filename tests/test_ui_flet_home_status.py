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
    STALE_AFTER_HOURS,
    HomeStatus,
    LatestReason,
    classify_latest_reason,
    derive_home_status,
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


class TestScheduleAttention:
    """D4: a schedule the config expected but the OS no longer has (or one that fired without
    completing) is the dominant fault — WARNING routed to Setup, never onboarding."""

    def test_expected_missing_routes_to_setup(self) -> None:
        sched = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"
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


class TestFailedRules:
    def test_failed_etl_is_failed_verdict(self) -> None:
        status = _derive(_record(status="failed"))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"
        assert status.fix is not None and status.fix.dest_id == "run_history"

    def test_failed_etl_precedes_sftp_and_data_errors(self) -> None:
        # A failed ETL is the dominant fault even with an SFTP failure + data errors also set.
        status = _derive(_record(status="failed", sftp_attempted=True, sftp_ok=False, data_errors={"total": 9}))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_sftp_delivery_failed_is_failed_verdict(self) -> None:
        status = _derive(_record(sftp_attempted=True, sftp_ok=False))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"
        assert status.fix is not None and status.fix.dest_id == "run_history"

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
        assert status.headline == "Your roster is syncing"
        assert status.fix is None
        assert status.metrics is not None
        assert status.metrics.sftp_delivered is True
        # Raw ISO never leaks into the healthy detail.
        assert _RECENT not in status.detail

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
        assert status.detail == "Last night's sync hit a problem and didn't finish."

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
]


@pytest.mark.parametrize("records", _SWEEP_INPUTS)
def test_derivation_is_total_over_all_inputs(records: list[dict] | None) -> None:
    status = derive_home_status(records, _CONFIGURED, now=_NOW)
    assert isinstance(status, HomeStatus)
    assert status.verdict in Verdict
    assert status.headline and status.detail
