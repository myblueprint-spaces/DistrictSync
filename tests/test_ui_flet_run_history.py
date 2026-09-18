"""Unit tests for the pure Run-History derivation (IA-6, COUNTED — the trust-relevant core).

Covers:
- the shared ``home_status`` additions IA-6 single-sources through: ``classify_latest_reason``
  (the status→reason precedence, staleness EXCLUDED) + ``verdict_for_reason`` (reason→Verdict,
  total over the enum);
- ``derive_history_banner`` — every degradation + verdict rule (first-match precedence), including
  the ``is_stale`` REUSE (a > ``STALE_AFTER_HOURS``-old clean latest → the stale banner) and the
  ``None``/``[]``/malformed-latest totality;
- ``to_run_row`` — every field, the plain per-run labels, totality across records-missing-every-key
  (parametrized), and the load-bearing PRIVACY assertion (a fake path + a raw ``ANOMALY:`` string
  appear in NO ``RunRow`` field nor banner string, and ``RunRow`` has NO ``error`` attribute);
- banner/row AGREEMENT (the latest row's verdict category never contradicts the banner).

Pure derivation → SYNTHETIC records, no filesystem.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet import home_status as home_status_mod
from src.ui_flet.home_status import (
    STALE_AFTER_HOURS,
    LatestReason,
    classify_latest_reason,
    derive_home_status,
    verdict_for_reason,
)
from src.ui_flet.run_history import (
    HistoryBanner,
    RunRow,
    SftpDelivery,
    derive_history_banner,
    to_run_row,
    to_run_rows,
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
        "duration_s": 3.2,
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


def _banner(record: dict) -> HistoryBanner:
    return derive_history_banner([record], _CONFIGURED, now=_NOW)


# --------------------------------------------------------------------------- #
# #1 — the shared home_status additions IA-6 single-sources through            #
# --------------------------------------------------------------------------- #
class TestClassifyLatestReason:
    def test_failed_etl(self) -> None:
        assert classify_latest_reason(_record(status="failed")) is LatestReason.FAILED_ETL

    def test_missing_status_is_failed_etl(self) -> None:
        # No status → non-success → FAILED_ETL (the honest fail-safe default).
        assert classify_latest_reason({}) is LatestReason.FAILED_ETL

    def test_failed_delivery(self) -> None:
        assert classify_latest_reason(_record(sftp_attempted=True, sftp_ok=False)) is LatestReason.FAILED_DELIVERY

    def test_anomaly(self) -> None:
        assert classify_latest_reason(_record(anomalies=["ANOMALY: x"])) is LatestReason.ANOMALY

    def test_data_warnings(self) -> None:
        assert classify_latest_reason(_record(data_errors={"total": 3})) is LatestReason.DATA_WARNINGS

    def test_clean(self) -> None:
        assert classify_latest_reason(_record()) is LatestReason.CLEAN

    def test_failed_etl_precedes_all(self) -> None:
        # A failed ETL dominates even with SFTP failure + anomalies + data errors all set.
        rec = _record(
            status="failed", sftp_attempted=True, sftp_ok=False, anomalies=["ANOMALY: y"], data_errors={"total": 9}
        )
        assert classify_latest_reason(rec) is LatestReason.FAILED_ETL

    def test_delivery_precedes_anomaly_and_data_errors(self) -> None:
        rec = _record(sftp_attempted=True, sftp_ok=False, anomalies=["ANOMALY: y"], data_errors={"total": 4})
        assert classify_latest_reason(rec) is LatestReason.FAILED_DELIVERY

    def test_anomaly_precedes_data_errors(self) -> None:
        assert (
            classify_latest_reason(_record(anomalies=["ANOMALY: y"], data_errors={"total": 4})) is LatestReason.ANOMALY
        )

    def test_non_list_anomalies_tolerated(self) -> None:
        # A garbage anomalies value must not be treated as an anomaly nor crash.
        assert classify_latest_reason(_record(anomalies="not-a-list")) is LatestReason.CLEAN

    def test_staleness_is_not_a_reason(self) -> None:
        # A stale-but-clean record is still CLEAN — staleness is layered on top, not a reason.
        assert classify_latest_reason(_record(timestamp=_OLD)) is LatestReason.CLEAN


class TestVerdictForReason:
    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            (LatestReason.FAILED_ETL, Verdict.FAILED),
            (LatestReason.FAILED_DELIVERY, Verdict.FAILED),
            (LatestReason.ANOMALY, Verdict.WARNING),
            (LatestReason.DATA_WARNINGS, Verdict.WARNING),
            (LatestReason.CLEAN, Verdict.HEALTHY),
        ],
    )
    def test_reason_maps_to_verdict(self, reason: LatestReason, expected: Verdict) -> None:
        assert verdict_for_reason(reason) is expected

    def test_total_over_every_reason(self) -> None:
        # A new reason without a verdict would KeyError here — the totality guard.
        for reason in LatestReason:
            assert verdict_for_reason(reason) in Verdict


# --------------------------------------------------------------------------- #
# #2 — derive_history_banner: every degradation + verdict rule                 #
# --------------------------------------------------------------------------- #
class TestBannerUnavailable:
    def test_none_is_calm_warning_no_raise(self) -> None:
        banner = derive_history_banner(None, _CONFIGURED, now=_NOW)
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "Run history unavailable"


class TestBannerEmpty:
    """0038 S7 part (i): the fresh-start discriminator moved to ``store_created_at`` ALONE, and
    is now IMPORTED from ``home_status`` rather than duplicated here — so this surface can no
    longer be left behind when Home's rule changes (it was, byte-for-byte, until S7)."""

    _STORE_STAMP = _RECENT

    def test_empty_upgrader_with_live_schedule_shows_plain_time(self) -> None:
        # An UPGRADER (a store already exists) with an empty table post-update → fresh-start
        # copy, NOT the bare "nothing recorded" one; the time derives from the LIVE read-back.
        banner = derive_history_banner(
            [], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP, schedule_status=_live_schedule("3:00 AM")
        )
        assert banner.verdict is Verdict.WARNING  # never red
        assert banner.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert "3:00 AM" in banner.detail

    def test_empty_upgrader_without_schedule_status_omits_time(self) -> None:
        banner = derive_history_banner([], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP)
        assert banner.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert "Scheduled for" not in banner.detail
        # Honesty C: conditioned hidden-history claim, never a flat assertion.
        assert "If you used an earlier version" in banner.detail

    def test_empty_with_NO_store_is_never_told_about_an_earlier_version(self) -> None:
        # The twin: the SAME completed-setup config with no store must not inherit the
        # upgrader's conditional past-version sentence (the defect part (i) removes).
        banner = derive_history_banner([], _CONFIGURED, now=_NOW, store_created_at=None)
        assert banner.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert "earlier version" not in banner.detail

    def test_empty_genuine_first_run_unscheduled_says_no_sync_yet(self) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=None)
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert "scheduled for" not in banner.detail.lower()

    def test_empty_completed_manual_only_install_is_NOT_an_upgrader(self) -> None:
        # Part (i): finishing setup is evidence about the SETTINGS, never about a run.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=None)
        assert banner.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE

    def test_empty_store_created_at_signals_an_upgrade(self) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=self._STORE_STAMP)
        assert banner.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE

    def test_empty_completed_but_confirmed_unscheduled_says_no_auto_sync(self) -> None:
        # #1b: same honest no-auto-sync copy as Home when the read-back CONFIRMS no schedule.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        missing = ScheduleStatus(state=ScheduleState.MISSING, headline="", detail="", attention=False)
        banner = derive_history_banner([], cfg, now=_NOW, schedule_status=missing)
        assert banner.verdict is Verdict.WARNING
        assert "won't sync automatically" in banner.detail
        # The positive twin for this negative lives in ``TestBannerEmptyLeadsArePinned`` below —
        # this row only proves the no-auto-sync branch WINS over the fresh-start one.
        assert home_status_mod._FRESH_START_LEAD not in banner.detail


class TestBannerEmptyLeadsArePinned:
    """Discharge-round BLOCK-3(ii): BOTH empty-state details, asserted POSITIVELY.

    Neither of this banner's two empty leads was referenced anywhere in ``tests/``. The only
    mention was a NEGATIVE assertion on a third branch with no positive twin, so reverting
    either string to an over-claiming one left ~400 targeted tests green — the exact
    "No vacuous greens" / "pin at the SUPPLY" shape this slice raised against Home's schedule
    card. Each row below asserts the EXACT sentence, so a silent rewording is RED.
    """

    _STORE_STAMP = _RECENT

    def test_the_upgrade_arm_names_the_ledger_and_no_nightly(self) -> None:
        """The arm is gated on the store's birth stamp ALONE — nothing about a schedule.

        A manual-only install with a stamped-but-empty store reaches it (the store's
        quarantine-recreate path; QA row 1o stages exactly this by emptying ``history.db``),
        so "New NIGHTLY syncs will appear here" told an admin who skipped the Schedule step
        about automation they declined. Home's twin arm is scrubbed the same way.
        """
        manual_only = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], manual_only, now=_NOW, store_created_at=self._STORE_STAMP)
        assert banner.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert banner.detail == (
            "New runs will appear here from now on. If you used an earlier version, its run history isn't carried over."
        )
        assert "nightly" not in banner.detail.lower()

    def test_the_no_stamp_arm_names_no_nightly_either(self) -> None:
        """The sentence the Stage-7 batch volunteered, now pinned so it cannot silently revert."""
        manual_only = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], manual_only, now=_NOW, store_created_at=None)
        assert banner.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert banner.detail == "Runs will appear here once the first one completes."
        assert "nightly" not in banner.detail.lower()

    def test_a_CONFIRMED_LIVE_read_back_is_what_earns_the_nightly_mention(self) -> None:
        """The positive twin for both rows above — the gate suppresses, it does not delete.

        Without this, scrubbing every mention of a nightly from this surface would pass the
        two rows above while removing a true, useful sentence.
        """
        banner = derive_history_banner(
            [], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP, schedule_status=_live_schedule("3:00 AM")
        )
        assert banner.detail.endswith("Scheduled for 3:00 AM each night.")


class TestBannerLatestRules:
    def test_failed_etl_is_failed(self) -> None:
        banner = _banner(_record(status="failed"))
        assert banner.verdict is Verdict.FAILED
        assert banner.headline == "Your last sync failed"

    def test_failed_delivery_is_failed(self) -> None:
        banner = _banner(_record(sftp_attempted=True, sftp_ok=False))
        assert banner.verdict is Verdict.FAILED
        assert "didn't reach SpacesEDU" in banner.headline

    def test_anomaly_is_warning(self) -> None:
        banner = _banner(_record(anomalies=["ANOMALY: Students dropped from 200 to 100 rows"]))
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "Something looked off recently"

    def test_multiple_anomalies_plural_detail(self) -> None:
        banner = _banner(_record(anomalies=["ANOMALY: a", "ANOMALY: b"]))
        assert "2 roster files" in banner.detail

    def test_data_warnings_is_warning(self) -> None:
        banner = _banner(_record(data_errors={"total": 3}))
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "Recent runs completed with data warnings"
        # The fixture's record IS delivered (sftp_ok=True) — the delivery claim is earned here.
        assert banner.detail == "Some records had field problems and were skipped — the runs still delivered."

    def test_data_warnings_no_sftp_never_claims_delivery(self) -> None:
        # 2026-08-31 live-install mislabel: a run with data warnings and NO SFTP attempt read
        # "the runs still delivered" while nothing was ever uploaded. The detail must follow the
        # record's SFTP axis exactly like the clean branch does.
        banner = _banner(_record(data_errors={"total": 3}, sftp_attempted=False, sftp_ok=False))
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "Recent runs completed with data warnings"
        assert banner.detail == "Some records had field problems and were skipped — the runs still completed."
        assert "delivered" not in banner.detail

    def test_clean_delivered_is_healthy(self) -> None:
        banner = _banner(_record())
        assert banner.verdict is Verdict.HEALTHY
        # 0032 T1 #1c: no schedule read-back → the record-scoped claim, never "running".
        assert banner.headline == "Your last sync worked"
        # 0032 T1 #1a: sftp_ok names the actual destination (mirrors Home's healthy detail).
        assert banner.detail == "Your last sync delivered to SpacesEDU 5 hours ago."
        assert _RECENT not in banner.detail  # plain relative phrase, not the raw ISO

    def test_clean_with_live_readback_asserts_running(self) -> None:
        # "Your sync is running" (ongoing automation) demands a CONFIRMED-LIVE read-back.
        banner = derive_history_banner([_record()], _CONFIGURED, now=_NOW, schedule_status=_live_schedule())
        assert banner.verdict is Verdict.HEALTHY
        assert banner.headline == "Your sync is running"

    def test_clean_no_sftp_says_completed_to_output_folder(self) -> None:
        # A run that never attempted SFTP must NEVER claim a delivery that didn't happen.
        banner = _banner(_record(sftp_attempted=False, sftp_ok=False))
        assert banner.verdict is Verdict.HEALTHY
        assert banner.detail == "Your last sync completed 5 hours ago — files were written to your output folder."
        assert "delivered" not in banner.detail


class TestBannerStalenessReuse:
    def test_stale_clean_latest_is_warning_via_is_stale(self) -> None:
        banner = _banner(_record(timestamp=_OLD))
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "No recent sync"
        assert _OLD not in banner.detail  # relative phrase, not raw ISO

    def test_within_window_clean_latest_is_healthy(self) -> None:
        banner = _banner(_record(timestamp=_RECENT))
        assert banner.verdict is Verdict.HEALTHY

    def test_unparseable_latest_timestamp_does_not_crash(self) -> None:
        # Staleness skipped (is_stale → False); classifies on the other fields → HEALTHY, no crash.
        banner = _banner(_record(timestamp="garbage-timestamp"))
        assert banner.verdict is Verdict.HEALTHY

    def test_failed_precedes_staleness(self) -> None:
        # An OLD failed run is FAILED (fault axis), not the stale WARNING.
        banner = _banner(_record(status="failed", timestamp=_OLD))
        assert banner.verdict is Verdict.FAILED


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


# A "now" comfortably OUTSIDE the Aug 11 -> Jul 6 window (mid-July summer break) -> the season is paused.
_SUMMER = datetime(2026, 7, 20, 8, 0, 0)
_PAUSED_HEADLINE = "Paused for the summer"


class TestSeasonalPauseBanner:
    """FIX 1: during a summer pause the newest store record is the last in-season run (weeks old by
    construction), so the stale rule would false-fire an amber "No recent sync" banner while Home
    shows a calm HEALTHY "Paused for the summer". The banner must consult the window and return the
    SAME paused verdict/headline/detail Home does — two surfaces can never disagree about one state.
    """

    def test_paused_outranks_stale_and_matches_home(self) -> None:
        # An in-season clean record ~40h old (past STALE_AFTER_HOURS=36) + a window-enabled cfg + a
        # now inside the pause. Base (window-unaware) -> WARNING "No recent sync"; the fix -> the
        # HEALTHY paused banner, asserted EQUAL to Home's verdict + headline + detail for the same inputs.
        old = (_SUMMER - timedelta(hours=40)).isoformat(timespec="seconds")
        cfg = _windowed()
        banner = derive_history_banner([_record(timestamp=old)], cfg, now=_SUMMER)
        home = derive_home_status([_record(timestamp=old)], cfg, now=_SUMMER)
        assert banner.verdict is Verdict.HEALTHY
        assert banner.headline == _PAUSED_HEADLINE
        assert "No recent sync" not in banner.headline
        # No drift: identical verdict + headline + detail as Home for the same inputs.
        assert banner.verdict is home.verdict
        assert banner.headline == home.headline
        assert banner.detail == home.detail

    def test_failed_latest_still_surfaces_in_a_pause(self) -> None:
        # A REAL failure is never hidden by summer — FAILED still outranks the pause (mirrors Home).
        banner = derive_history_banner([_record(status="failed")], _windowed(), now=_SUMMER)
        assert banner.verdict is Verdict.FAILED

    def test_empty_store_paused_matches_home(self) -> None:
        # The empty-store paused case must not drift either (Home shows paused; the banner must too).
        cfg = _windowed(setup_completed=True)
        banner = derive_history_banner([], cfg, now=_SUMMER, store_created_at=_RECENT)
        home = derive_home_status([], cfg, now=_SUMMER, store_created_at=_RECENT)
        assert banner.verdict is Verdict.HEALTHY
        assert banner.headline == _PAUSED_HEADLINE
        assert banner.headline == home.headline

    def test_confirmed_missing_schedule_is_not_masked_by_pause(self) -> None:
        # Mirrors Home's FIX 2 gate so the two surfaces stay identical: a confirmed-gone task
        # suppresses the pause (it won't resume in the fall) — the banner must NOT read "Paused".
        missing = derive_schedule_status(
            ScheduleReadback(found=False),
            hint_registered=False,
            latest_record_ts=None,
            foreign_account="",
            shared_records=False,
        )
        old = (_SUMMER - timedelta(hours=40)).isoformat(timespec="seconds")
        cfg = _windowed(setup_completed=True, schedule_registered=False)
        banner = derive_history_banner([_record(timestamp=old)], cfg, now=_SUMMER, schedule_status=missing)
        assert banner.headline != _PAUSED_HEADLINE

    def test_disabled_window_banner_unchanged(self) -> None:
        # Opt-in default: a disabled window -> the stale banner fires exactly as before (byte-identical).
        old = (_SUMMER - timedelta(hours=40)).isoformat(timespec="seconds")
        banner = derive_history_banner([_record(timestamp=old)], _windowed(sync_window_enabled=False), now=_SUMMER)
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "No recent sync"


# --------------------------------------------------------------------------- #
# #3 — to_run_row: every field                                                 #
# --------------------------------------------------------------------------- #
class TestToRunRowFields:
    def test_clean_delivered_row(self) -> None:
        row = to_run_row(_record(), now=_NOW)
        assert _RECENT not in row.when  # a plain phrase, not the raw ISO
        assert row.status_label == "Delivered"
        assert row.status_verdict is Verdict.HEALTHY
        assert set(row.entity_counts) == {"Students", "Staff", "Family", "Classes", "Enrollments"}
        assert "CourseInfo" not in row.entity_counts
        assert "StudentAttendance" not in row.entity_counts
        assert row.entity_total == 100 + 12 + 80 + 40 + 300
        assert row.sftp is SftpDelivery.DELIVERED
        assert row.warnings == 0
        assert row.duration == "3.2s"

    def test_clean_no_sftp_reads_completed(self) -> None:
        row = to_run_row(_record(sftp_attempted=False, sftp_ok=False), now=_NOW)
        assert row.status_label == "Completed"
        assert row.status_verdict is Verdict.HEALTHY
        assert row.sftp is SftpDelivery.NOT_ATTEMPTED

    def test_failed_row(self) -> None:
        row = to_run_row(_record(status="failed"), now=_NOW)
        assert row.status_label == "Failed"
        assert row.status_verdict is Verdict.FAILED

    def test_built_not_delivered_row(self) -> None:
        row = to_run_row(_record(sftp_attempted=True, sftp_ok=False), now=_NOW)
        assert row.status_label == "Built, not delivered"
        assert row.status_verdict is Verdict.FAILED
        assert row.sftp is SftpDelivery.FAILED

    def test_delivered_with_data_warnings_row(self) -> None:
        row = to_run_row(_record(data_errors={"total": 2}), now=_NOW)
        assert row.status_label == "Delivered · 2 data warnings"
        assert row.status_verdict is Verdict.WARNING
        assert row.warnings == 2

    def test_single_data_warning_singular_label(self) -> None:
        row = to_run_row(_record(data_errors={"total": 1}), now=_NOW)
        assert row.status_label == "Delivered · 1 data warning"

    def test_data_warnings_no_sftp_reads_completed(self) -> None:
        # The row-label twin of the banner's no-SFTP honesty rule (2026-08-31 mislabel): a run
        # that never attempted delivery must not open its label with "Delivered".
        row = to_run_row(_record(data_errors={"total": 2}, sftp_attempted=False, sftp_ok=False), now=_NOW)
        assert row.status_label == "Completed · 2 data warnings"
        assert row.status_verdict is Verdict.WARNING
        assert row.sftp is SftpDelivery.NOT_ATTEMPTED

    def test_myblueprint_counts_present_when_nonzero(self) -> None:
        row = to_run_row(_record(CourseInfo=15, StudentCourses=200), now=_NOW)
        assert row.entity_counts["CourseInfo"] == 15
        assert row.entity_counts["StudentCourses"] == 200

    def test_missing_duration_renders_dash(self) -> None:
        rec = _record()
        del rec["duration_s"]
        row = to_run_row(rec, now=_NOW)
        assert row.duration == "—"

    def test_garbage_duration_renders_dash(self) -> None:
        # A non-None, non-float-coercible duration_s → "—" (total; never crashes).
        row = to_run_row(_record(duration_s="not-a-number"), now=_NOW)
        assert row.duration == "—"


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


class TestDeliveryOnlyRows:
    """Deliver-from-disk rows must read as deliveries of saved files, never 0-row builds."""

    def test_clean_delivery_row_labels_saved_files_with_no_counts(self) -> None:
        row = to_run_row(_delivery_record(), now=_NOW)
        assert row.status_label == "Delivered saved files"
        assert row.status_verdict is Verdict.HEALTHY
        assert row.entity_counts == {}  # the table renders "—" cells, never "0 Students"
        assert row.entity_total == 0
        assert row.sftp is SftpDelivery.DELIVERED

    def test_failed_delivery_row_labels_delivery_failed(self) -> None:
        # NOT "Built, not delivered" — this attempt built nothing; only the upload failed.
        row = to_run_row(_delivery_record(sftp_ok=False), now=_NOW)
        assert row.status_label == "Delivery failed"
        assert row.status_verdict is Verdict.FAILED
        assert row.sftp is SftpDelivery.FAILED
        assert row.entity_counts == {}

    def test_clean_delivery_banner_stays_healthy(self) -> None:
        banner = _banner(_delivery_record())
        assert banner.verdict is Verdict.HEALTHY

    def test_failed_delivery_only_banner_never_claims_a_build(self) -> None:
        # The row label says "Delivery failed"; the banner above it must agree —
        # a delivery-only failure built nothing this run.
        banner = _banner(_delivery_record(sftp_ok=False))
        assert banner.verdict is Verdict.FAILED
        assert banner.detail == "The upload of your saved files failed."

    def test_failed_delivery_after_a_build_keeps_the_build_copy(self) -> None:
        banner = _banner(_record(sftp_attempted=True, sftp_ok=False))
        assert banner.detail == "The most recent run built the data but the upload failed."


# --------------------------------------------------------------------------- #
# #3 — the Source column + the different-district note (0034 Slice 4)           #
# --------------------------------------------------------------------------- #
class TestSourceLabel:
    @pytest.mark.parametrize(
        ("source", "label"),
        [
            ("scheduled", "Nightly"),
            ("manual", "Manual"),
            ("cli", "Command line"),
            ("unknown", "—"),
        ],
    )
    def test_bounded_source_maps_to_friendly_label(self, source: str, label: str) -> None:
        assert to_run_row(_record(source=source), now=_NOW).source == label

    def test_missing_source_renders_dash(self) -> None:
        # A pre-enrichment record has no source key — TOTAL, the neutral fallback.
        assert to_run_row(_record(), now=_NOW).source == "—"

    def test_out_of_set_source_is_never_echoed(self) -> None:
        # Anything outside the bounded vocabulary renders the fallback, never the raw value.
        row = to_run_row(_record(source=r"C:\evil\path"), now=_NOW)
        assert row.source == "—"


class TestDistrictNote:
    def test_no_note_when_district_matches_active(self) -> None:
        row = to_run_row(_record(sis_type="myedbc"), now=_NOW, active_sis="myedbc")
        assert row.district_note is None

    def test_rows_resolve_each_distinct_district_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The display resolution is a config READ — to_run_rows must resolve each distinct
        # differing district once per call, never once per row.
        calls: list[str] = []

        def _counting(sis: str) -> str:
            calls.append(sis)
            return f"District {sis}"

        monkeypatch.setattr("src.ui_flet.run_history.friendly_district_name", _counting)
        records = [_record(sis_type="sd40myedbc") for _ in range(3)]
        rows = to_run_rows(records, now=_NOW, active_sis="myedbc")
        assert [r.district_note for r in rows] == ["Different district: District sd40myedbc"] * 3
        assert calls == ["sd40myedbc"]

    def test_note_when_district_differs(self) -> None:
        row = to_run_row(_record(sis_type="zz_not_a_config"), now=_NOW, active_sis="myedbc")
        # An unknown id falls back to the raw district id (a bounded config id, never a path).
        assert row.district_note == "Different district: zz_not_a_config"

    def test_real_district_resolves_to_friendly_display(self) -> None:
        row = to_run_row(_record(sis_type="sd74myedbc"), now=_NOW, active_sis="myedbc")
        assert row.district_note is not None
        assert row.district_note.startswith("Different district: ")

    def test_no_note_without_active_district(self) -> None:
        # The active district must be KNOWN to establish a difference (never a guess).
        row = to_run_row(_record(sis_type="sd74myedbc"), now=_NOW)
        assert row.district_note is None

    def test_no_note_when_record_lacks_district(self) -> None:
        row = to_run_row(_record(), now=_NOW, active_sis="myedbc")
        assert row.district_note is None


# --------------------------------------------------------------------------- #
# #3 — totality across every degradation axis (parametrized)                   #
# --------------------------------------------------------------------------- #
_PARTIAL_RECORDS = [
    {},
    {"status": "success"},
    {"status": "failed"},
    {"timestamp": "garbage"},
    {"Students": "not-a-number"},
    {"data_errors": "not-a-dict"},
    {"anomalies": "not-a-list"},
    {"sftp_attempted": True},
    _record(),
    _record(status="failed", error=r"boom C:\path\x"),
    _delivery_record(),
    _delivery_record(sftp_ok=False),
]


@pytest.mark.parametrize("record", _PARTIAL_RECORDS)
def test_to_run_row_is_total(record: dict) -> None:
    row = to_run_row(record, now=_NOW)
    assert isinstance(row, RunRow)
    assert row.status_label
    assert row.status_verdict in Verdict
    assert row.when


def test_missing_status_row_is_failed() -> None:
    row = to_run_row({}, now=_NOW)
    assert row.status_label == "Failed"
    assert row.status_verdict is Verdict.FAILED
    assert row.when == "recently"  # missing timestamp → friendly_timestamp("") → "recently"
    assert row.entity_total == 0


def test_to_run_rows_empty_is_empty() -> None:
    assert to_run_rows([]) == []


def test_to_run_rows_mixed_list_one_row_each() -> None:
    rows = to_run_rows([_record(), {}, _record(status="failed")], now=_NOW)
    assert len(rows) == 3
    assert all(isinstance(r, RunRow) for r in rows)


# --------------------------------------------------------------------------- #
# #7 [PRIVACY] — the load-bearing assertion                                    #
# --------------------------------------------------------------------------- #
class TestPrivacyNoLeak:
    _SECRET = r"C:\Users\x\secret"
    _RAW_ANOMALY = "ANOMALY: Students dropped from 200 to 100 rows"

    def test_runrow_has_no_error_attribute(self) -> None:
        # A future view edit must not be able to render a raw error — the field simply isn't there.
        row = to_run_row(_record(), now=_NOW)
        assert not hasattr(row, "error")
        assert not hasattr(row, "log_path")

    def test_fake_path_and_raw_anomaly_never_leak(self) -> None:
        for extra in (
            {"status": "failed"},
            {"sftp_attempted": True, "sftp_ok": False},
            {"anomalies": [self._RAW_ANOMALY]},
            {"data_errors": {"total": 2}},
        ):
            rec = _record(error=f"FileNotFoundError: {self._SECRET}\\input.csv", sis_type="sd48myedbc", **extra)

            row = to_run_row(rec, now=_NOW)
            row_strings = [
                row.when,
                row.status_label,
                row.duration,
                row.source,
                str(row.district_note),
                *[str(v) for v in row.entity_counts],
            ]
            for s in row_strings:
                assert self._SECRET not in s
                assert self._RAW_ANOMALY not in s
                assert "sd48myedbc" not in s
                assert "secret" not in s

            banner = _banner(rec)
            for s in (banner.headline, banner.detail):
                assert self._SECRET not in s
                assert self._RAW_ANOMALY not in s
                assert "sd48myedbc" not in s
                assert "secret" not in s


# --------------------------------------------------------------------------- #
# banner/row AGREEMENT — the latest row never contradicts the banner           #
# --------------------------------------------------------------------------- #
class TestBannerRowAgreement:
    @pytest.mark.parametrize(
        "record",
        [
            _record(status="failed"),
            _record(sftp_attempted=True, sftp_ok=False),
            _record(timestamp=_OLD),
            _record(data_errors={"total": 3}),
            _record(),
        ],
    )
    def test_latest_row_verdict_agrees_with_banner(self, record: dict) -> None:
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW)
        row = to_run_row(record, now=_NOW)
        # The banner classifies the LATEST record; the row classifies the same record. They must
        # never contradict on the fault verdict. (Stale is a WARNING on a CLEAN/HEALTHY row — the
        # banner's time-relative axis — so a HEALTHY row under a WARNING stale banner is allowed.)
        if row.status_verdict is Verdict.HEALTHY:
            assert banner.verdict in (Verdict.HEALTHY, Verdict.WARNING)
        else:
            assert banner.verdict is row.status_verdict


# --------------------------------------------------------------------------- #
# Home ↔ Run History AGREEMENT — the same question over the same record        #
# --------------------------------------------------------------------------- #
class TestHomeHistoryAgreementOnFailures:
    """W3-B: Home and this banner answer the SAME "is my sync OK?" question over the SAME latest
    record, so they must never disagree (``docs/claugentic-PRODUCT.md`` → Run History).

    They used to: Home's schedule-attention rule returned above its two FAILED rules, so a failed
    latest under an expected-MISSING / fired-but-no-record schedule read amber-and-schedule on Home
    while Run History read red-and-failed. Run History has no schedule-attention rule at all, so the
    disagreement was purely Home's precedence — this pins that the two verdicts now match on every
    failure shape × every attention flavor.
    """

    _EXPECTED_MISSING = derive_schedule_status(
        ScheduleReadback(found=False),
        hint_registered=True,
        latest_record_ts=None,
        foreign_account="",
        shared_records=False,
    )
    _CONTRADICTION = derive_schedule_status(
        ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
        hint_registered=True,
        latest_record_ts=_RECENT,
        foreign_account="",
        shared_records=False,
    )

    @pytest.mark.parametrize(
        "record",
        [_record(status="failed"), _record(sftp_attempted=True, sftp_ok=False)],
        ids=["failed-etl", "failed-delivery"],
    )
    @pytest.mark.parametrize(
        "schedule",
        [None, _EXPECTED_MISSING, _CONTRADICTION],
        ids=["unprobed", "expected-missing", "contradiction"],
    )
    def test_failed_latest_reads_failed_on_both_surfaces(self, record: dict, schedule: ScheduleStatus | None) -> None:
        home = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        assert home.verdict is Verdict.FAILED
        assert banner.verdict is home.verdict


# --------------------------------------------------------------------------- #
# Plan 0046 C / A5 — the foreign principal on Run History                        #
# --------------------------------------------------------------------------- #
from src.ui_flet.home_status import (  # noqa: E402
    EMPTY_FRESH_START_HEADLINE,
    EMPTY_NO_RUNS_HEADLINE,
    FOREIGN_PRINCIPAL_HEADLINE,
)

_FOREIGN = "CONTOSO\\svc_districtsync"
_STALE_BANNER_HEADLINE = "No recent sync"
_ESTABLISHED = (_NOW - timedelta(hours=72)).isoformat(timespec="seconds")
_ANCIENT = (_NOW - timedelta(hours=STALE_AFTER_HOURS + 48)).isoformat(timespec="seconds")


def _foreign_schedule(*, attention: bool = False, detail: str = "registered elsewhere") -> ScheduleStatus:
    """A LIVE read-back on a RECORDED foreign principal (see the Home-side twin)."""
    return ScheduleStatus(
        state=ScheduleState.LIVE,
        headline="Nightly sync is scheduled",
        detail=detail,
        next_run_display="3:00 AM",
        attention=attention,
        foreign_account=_FOREIGN,
    )


def _real_foreign_status(*, last_result: int | None) -> ScheduleStatus:
    """A foreign-principal LIVE status built by the REAL derivation, so headline/detail/attention
    are exactly what ships — never a hand-rolled combination the producer could not emit."""
    return derive_schedule_status(
        ScheduleReadback(found=True, next_run="2026-07-05T03:00:00", last_result=last_result),
        hint_registered=True,
        latest_record_ts=None,
        foreign_account=_FOREIGN,
        shared_records=False,
    )


class TestRunHistoryEmptyStateUnderAForeignPrincipal:
    """An empty ledger here is EXPECTED — saying "no runs yet" would be read as "it never ran"."""

    @pytest.mark.parametrize("store_created_at", [None, _ESTABLISHED])
    def test_neither_empty_arm_renders(self, store_created_at: str | None) -> None:
        banner = derive_history_banner(
            [], _CONFIGURED, now=_NOW, store_created_at=store_created_at, schedule_status=_foreign_schedule()
        )
        assert banner.headline not in (EMPTY_FRESH_START_HEADLINE, EMPTY_NO_RUNS_HEADLINE)
        assert banner.headline == FOREIGN_PRINCIPAL_HEADLINE

    @pytest.mark.parametrize(
        ("store_created_at", "expected"),
        [(None, EMPTY_NO_RUNS_HEADLINE), (_ESTABLISHED, EMPTY_FRESH_START_HEADLINE)],
    )
    def test_positive_twin_both_empty_arms_still_render_on_a_same_account_install(
        self, store_created_at: str | None, expected: str
    ) -> None:
        banner = derive_history_banner(
            [], _CONFIGURED, now=_NOW, store_created_at=store_created_at, schedule_status=_live_schedule()
        )
        assert banner.headline == expected


class TestRunHistoryStaleRuleUnderAForeignPrincipal:
    def test_the_stale_banner_is_never_rendered(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_ANCIENT)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=_ESTABLISHED,
            schedule_status=_foreign_schedule(),
        )
        assert banner.headline != _STALE_BANNER_HEADLINE
        assert banner.headline == FOREIGN_PRINCIPAL_HEADLINE

    def test_positive_twin_the_stale_banner_still_renders(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_ANCIENT)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert banner.headline == _STALE_BANNER_HEADLINE

    def test_a_fresh_local_record_still_speaks_for_itself(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_RECENT)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=_ESTABLISHED,
            schedule_status=_foreign_schedule(),
        )
        assert banner.headline != FOREIGN_PRINCIPAL_HEADLINE

    def test_a_failed_latest_still_owns_the_banner(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_ANCIENT, status="failed", error="boom")],
            _CONFIGURED,
            now=_NOW,
            store_created_at=_ESTABLISHED,
            schedule_status=_foreign_schedule(),
        )
        assert banner.verdict is Verdict.FAILED


class TestRunHistorySeasonalPauseUnderAForeignPrincipal:
    """A9 — the same single-source ``sync_window_paused`` fact Home reads."""

    def test_the_paused_banner_is_never_rendered(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_ANCIENT)],
            _windowed(),
            now=_SUMMER,
            store_created_at=_ESTABLISHED,
            schedule_status=_foreign_schedule(),
        )
        assert banner.headline != _PAUSED_HEADLINE

    def test_positive_twin_the_paused_banner_still_renders(self) -> None:
        banner = derive_history_banner(
            [_record(timestamp=_ANCIENT)],
            _windowed(),
            now=_SUMMER,
            store_created_at=_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert banner.headline == _PAUSED_HEADLINE


class TestForeignPrincipalCopyIsIdenticalAcrossBothSurfaces:
    """No literal is re-spelled in ``run_history.py`` — it delegates, exactly as ``_paused_banner``
    delegates to ``_paused_status``, so the two surfaces can never drift about one state."""

    @pytest.mark.parametrize("records", [[], [_record(timestamp=_ANCIENT)]])
    def test_the_calm_arm_is_identical_on_both_surfaces(self, records: list[dict]) -> None:
        """Windows reports no problem: the foreign-principal branch owns the band on BOTH."""
        schedule = _real_foreign_status(last_result=0)
        home = derive_home_status(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        banner = derive_history_banner(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        assert (banner.headline, banner.detail, banner.verdict) == (home.headline, home.detail, home.verdict)
        assert banner.headline == FOREIGN_PRINCIPAL_HEADLINE

    @pytest.mark.parametrize("records", [[], [_record(timestamp=_ANCIENT)]])
    def test_the_problem_arm_shares_the_detail_and_the_verdict(self, records: list[dict]) -> None:
        """Windows reported a problem. The DETAIL — the single-sourced sentence pair — and the
        amber verdict are identical; only the HEADLINE differs, and that is the module's existing,
        documented division of labour, not drift: Home surfaces the schedule-attention verdict
        (with its Setup CTA) and Run History, being read-only, deliberately does not.
        """
        schedule = _real_foreign_status(last_result=1)
        assert schedule.attention is True
        home = derive_home_status(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        banner = derive_history_banner(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        assert banner.detail == home.detail == schedule.detail
        assert banner.verdict is home.verdict is Verdict.WARNING
        assert home.headline == schedule.headline == "Your last nightly run reported a problem"
        assert banner.headline == FOREIGN_PRINCIPAL_HEADLINE

    @pytest.mark.parametrize(
        "record_kind",
        ["clean", "anomaly", "data-warnings"],
    )
    def test_the_rule_sits_at_the_same_position_on_both_surfaces(self, record_kind: str) -> None:
        """RULE ORDER parity, not just copy parity.

        Caught during review: this banner originally slotted the foreign rule BELOW anomaly /
        data-warnings while Home slots it ABOVE, so one ancient-anomaly install read
        "Something looked off recently" here and "runs under a different Windows account" on Home
        — the exact two-surface drift this file exists to prevent. Every rule below the foreign
        one describes a nightly cadence this ledger cannot see.
        """
        overrides: dict[str, object] = {"timestamp": _ANCIENT}
        if record_kind == "anomaly":
            overrides["anomalies"] = ["ANOMALY: Students dropped 42%"]
        elif record_kind == "data-warnings":
            overrides["data_errors"] = {"total": 3, "by_field": {"Grade": 3}}
        records = [_record(**overrides)]
        schedule = _real_foreign_status(last_result=0)
        home = derive_home_status(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        banner = derive_history_banner(
            records, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        assert (banner.headline, banner.detail, banner.verdict) == (home.headline, home.detail, home.verdict)
        assert banner.headline == FOREIGN_PRINCIPAL_HEADLINE

    def test_positive_twin_those_same_records_still_reach_their_own_rules(self) -> None:
        """Non-vacuity: with no recorded principal the anomaly / data-warning banners still win."""
        anomaly = [_record(timestamp=_ANCIENT, anomalies=["ANOMALY: Students dropped 42%"])]
        warnings = [_record(timestamp=_ANCIENT, data_errors={"total": 3, "by_field": {"Grade": 3}})]
        live = _live_schedule()
        assert (
            derive_history_banner(
                anomaly, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=live
            ).headline
            == "Something looked off recently"
        )
        assert (
            derive_history_banner(
                warnings, _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=live
            ).headline
            == "Recent runs completed with data warnings"
        )

    def test_both_surfaces_name_the_recorded_account_from_one_constant(self) -> None:
        from src.ui_flet.schedule_status import FOREIGN_RECORDS_NOTE

        schedule = _real_foreign_status(last_result=0)
        note = FOREIGN_RECORDS_NOTE.format(account=_FOREIGN)
        home = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule)
        banner = derive_history_banner(
            [], _CONFIGURED, now=_NOW, store_created_at=_ESTABLISHED, schedule_status=schedule
        )
        assert note in home.detail
        assert note in banner.detail

    def test_run_history_re_spells_no_foreign_copy_of_its_own(self) -> None:
        import inspect

        from src.ui_flet import run_history as run_history_mod

        source = inspect.getsource(run_history_mod)
        assert "runs under a different Windows account" not in source
        assert "run records are saved under" not in source


# --------------------------------------------------------------------------- #
# Plan 0049 S-2a.5 — "Ran as": one shared ledger, two writers                    #
# --------------------------------------------------------------------------- #
import flet as ft  # noqa: E402

from src.ui_flet import components  # noqa: E402
from src.ui_flet.run_history import (  # noqa: E402
    RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE,
    RUN_AS_ALL_THIS_ACCOUNT_NOTE,
    RUN_AS_ANOTHER_ACCOUNT,
    RUN_AS_THIS_ACCOUNT,
    run_as_display,
    run_as_summary_line,
)

_ME = "CORP\\jsmith"
_RUN_AS_COLUMN = "Ran as"


def _row(run_as: str) -> RunRow:
    """A minimal row carrying only the axis under test."""
    return RunRow(when="recently", status_label="Delivered", status_verdict=Verdict.HEALTHY, run_as=run_as)


def _headers(rows: list[RunRow]) -> list[str]:
    table = components.run_table(rows)
    assert isinstance(table, ft.DataTable)
    return [column.label.value for column in table.columns]


class TestRunAsDisplayIsBounded:
    """The record carries a raw ``DOMAIN\\user``; the ROW may not.

    ``RunRow``'s docstring bounds it to counts, bounded vocabularies and safe strings, and this
    would otherwise be the first raw identifying value in it — repeated on every historical row of
    a ledger an admin may screenshot into a support ticket. The reduction goes through
    ``setup_gates.principal_key``, the ONE comparison every principal question in the app uses, so
    the column and the schedule gates can never disagree about what "another account" means.
    """

    def test_the_account_now_running_reads_as_this_account(self) -> None:
        assert run_as_display(_ME, current_account=_ME) == RUN_AS_THIS_ACCOUNT

    def test_a_different_account_reads_as_another_account(self) -> None:
        assert run_as_display("CONTOSO\\svc_districtsync", current_account=_ME) == RUN_AS_ANOTHER_ACCOUNT

    def test_the_comparison_is_case_insensitive(self) -> None:
        """Restating ``register_task``'s own equivalence — Windows account names are not
        case-sensitive, and a case difference is not a second account."""
        assert run_as_display("corp\\JSMITH", current_account=_ME) == RUN_AS_THIS_ACCOUNT

    def test_surrounding_whitespace_is_not_a_second_account(self) -> None:
        assert run_as_display("  CORP\\jsmith  ", current_account=_ME) == RUN_AS_THIS_ACCOUNT

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", 7, {"account": _ME}],
        ids=["absent", "empty", "whitespace", "non-string-int", "non-string-dict"],
    )
    def test_an_unusable_record_value_is_not_established(self, value: object) -> None:
        """A record written before the key existed carries nothing. "Not established" and
        "another account" are DIFFERENT facts — collapsing them would print a foreign-account
        claim over a record that names no account at all."""
        assert run_as_display(value, current_account=_ME) == ""

    @pytest.mark.parametrize("current", ["", "   "], ids=["empty", "whitespace"])
    def test_an_unresolvable_current_account_is_not_established(self, current: str) -> None:
        """``accounts.process_account()`` can fail. With nothing to compare against, every row
        would otherwise reduce to "another account" — a claim about the whole ledger built on a
        lookup that failed."""
        assert run_as_display("CONTOSO\\svc_districtsync", current_account=current) == ""

    @pytest.mark.parametrize("recorded", [_ME, "CONTOSO\\svc_districtsync"], ids=["mine", "foreign"])
    def test_it_never_echoes_the_raw_account_name(self, recorded: str) -> None:
        """The privacy pin, stated over the two members and their inputs."""
        rendered = run_as_display(recorded, current_account=_ME)
        assert rendered in (RUN_AS_THIS_ACCOUNT, RUN_AS_ANOTHER_ACCOUNT)
        assert "\\" not in rendered
        assert "jsmith" not in rendered.lower()
        assert "svc_districtsync" not in rendered.lower()

    def test_the_vocabulary_has_exactly_two_members(self) -> None:
        """Positive twin for the "bounded" claim: it is a closed set, not a formatter."""
        produced = {
            run_as_display(value, current_account=_ME)
            for value in (_ME, "corp\\JSMITH", "CONTOSO\\svc", "OTHER\\admin", "", None)
        }
        assert produced == {RUN_AS_THIS_ACCOUNT, RUN_AS_ANOTHER_ACCOUNT, ""}


class TestRunAsReachesTheRowThroughTheView:
    """``to_run_row`` is pure and never reads the environment, so the view injects the account."""

    def test_the_injected_account_decides_the_row(self) -> None:
        mine = to_run_row(_record(run_as=_ME), now=_NOW, current_account=_ME)
        theirs = to_run_row(_record(run_as="CONTOSO\\svc_districtsync"), now=_NOW, current_account=_ME)
        assert mine.run_as == RUN_AS_THIS_ACCOUNT
        assert theirs.run_as == RUN_AS_ANOTHER_ACCOUNT

    def test_a_caller_that_cannot_name_the_account_gets_no_claim(self) -> None:
        """The default is display degradation, not a safety-relevant permissive default: nothing
        about a run's verdict, delivery or counts depends on it."""
        assert to_run_row(_record(run_as=_ME), now=_NOW).run_as == ""

    def test_a_record_without_the_key_is_total(self) -> None:
        """Every record written before v3.22 — the ledger straddles the upgrade."""
        assert to_run_row(_record(), now=_NOW, current_account=_ME).run_as == ""

    def test_to_run_rows_threads_it_to_every_row(self) -> None:
        records = [_record(run_as=_ME), _record(run_as="CONTOSO\\svc_districtsync"), _record()]
        rows = to_run_rows(records, now=_NOW, current_account=_ME)
        assert [row.run_as for row in rows] == [RUN_AS_THIS_ACCOUNT, RUN_AS_ANOTHER_ACCOUNT, ""]

    def test_the_raw_account_never_reaches_any_row_field(self) -> None:
        """The ``TestPrivacyNoLeak`` rule, extended to the new field and its source record."""
        row = to_run_row(_record(run_as="CONTOSO\\svc_districtsync"), now=_NOW, current_account=_ME)
        for value in (row.when, row.status_label, row.duration, row.source, str(row.district_note), row.run_as):
            assert "svc_districtsync" not in value
            assert "CONTOSO" not in value


class TestRunAsSummaryLine:
    """ "State it once" — the alternative to a column with nothing to say.

    Per-user it is ALWAYS ``None``: every visible record was written by the account reading them,
    so a line about accounts would answer a question none of the 20 districts asked and break
    S-2a's byte-identity promise.
    """

    @pytest.mark.parametrize(
        "rows",
        [
            [_row(RUN_AS_THIS_ACCOUNT)],
            [_row(RUN_AS_ANOTHER_ACCOUNT)],
            [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT)],
            [_row("")],
            [],
        ],
        ids=["all-mine", "all-theirs", "mixed", "unestablished", "no-rows"],
    )
    def test_per_user_is_always_silent(self, rows: list[RunRow]) -> None:
        assert run_as_summary_line(rows, machine_scope=False) is None

    def test_it_speaks_when_every_row_is_this_account(self) -> None:
        rows = [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_THIS_ACCOUNT)]
        assert run_as_summary_line(rows, machine_scope=True) == RUN_AS_ALL_THIS_ACCOUNT_NOTE

    def test_it_speaks_when_every_row_is_another_account(self) -> None:
        rows = [_row(RUN_AS_ANOTHER_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT)]
        assert run_as_summary_line(rows, machine_scope=True) == RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE

    def test_a_mix_is_silent_because_the_column_says_it_better(self) -> None:
        """With a mix the table renders the column; a sentence claiming one answer would be
        wrong, and one restating the column would be redundant."""
        rows = [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT)]
        assert run_as_summary_line(rows, machine_scope=True) is None

    @pytest.mark.parametrize("rows", [[], [_row("")], [_row(""), _row("")]], ids=["no-rows", "one", "several"])
    def test_nothing_established_is_silent(self, rows: list[RunRow]) -> None:
        """A pre-upgrade ledger establishes no account, so there is nothing to state."""
        assert run_as_summary_line(rows, machine_scope=True) is None

    def test_unestablished_rows_do_not_break_an_otherwise_unanimous_ledger(self) -> None:
        """The upgrade case, stated positively: old rows are silent, not dissenting."""
        rows = [_row(RUN_AS_ANOTHER_ACCOUNT), _row(""), _row(RUN_AS_ANOTHER_ACCOUNT)]
        assert run_as_summary_line(rows, machine_scope=True) == RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE

    def test_neither_sentence_names_an_account(self) -> None:
        for note in (RUN_AS_ALL_THIS_ACCOUNT_NOTE, RUN_AS_ALL_ANOTHER_ACCOUNT_NOTE):
            assert "\\" not in note
            assert "svc" not in note.lower()


class TestRunAsColumnRendersOnlyWhenItHasSomethingToSay:
    """``run_table``'s table-wide rule, the ``show_mbp`` precedent applied to a 14th column.

    A column reading the same value on every row of every per-user install is exactly the case
    that precedent exists for. The sharp edge is the UPGRADE: a ledger where some rows predate
    the ``run_as`` key must not conjure a column out of the difference between "another account"
    and "we don't know", which is not a disagreement about who ran anything.
    """

    def test_a_uniform_ledger_does_not_render_it(self) -> None:
        assert _RUN_AS_COLUMN not in _headers([_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_THIS_ACCOUNT)])

    def test_a_ledger_with_no_established_account_does_not_render_it(self) -> None:
        """Every per-user install today, and every row written before the key existed."""
        assert _RUN_AS_COLUMN not in _headers([_row(""), _row("")])

    def test_a_ledger_straddling_the_upgrade_does_not_render_it(self) -> None:
        """The case the rule is written for: ONE established value plus silent older rows is
        still one value."""
        rows = [_row(RUN_AS_ANOTHER_ACCOUNT), _row(""), _row(RUN_AS_ANOTHER_ACCOUNT)]
        assert _RUN_AS_COLUMN not in _headers(rows)

    def test_positive_twin_a_genuinely_mixed_ledger_renders_it(self) -> None:
        """The shared-store case the column exists for — the admin and the nightly's service
        account both writing into one ledger."""
        assert _RUN_AS_COLUMN in _headers([_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT)])

    def test_a_mixed_ledger_renders_the_unestablished_rows_as_a_dash(self) -> None:
        """Once the column is on for other reasons, a row that names no account says so the way
        every other absent cell in this table does."""
        rows = [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT), _row("")]
        table = components.run_table(rows)
        index = [column.label.value for column in table.columns].index(_RUN_AS_COLUMN)
        assert [row.cells[index].content.value for row in table.rows] == [
            RUN_AS_THIS_ACCOUNT,
            RUN_AS_ANOTHER_ACCOUNT,
            "—",
        ]

    def test_the_column_count_matches_the_cell_count_on_both_sides_of_the_rule(self) -> None:
        """A table-wide decision applied to the header but not the cells (or the reverse) is a
        render crash on a surface with no unit coverage of its own."""
        for rows in (
            [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_THIS_ACCOUNT)],
            [_row(RUN_AS_THIS_ACCOUNT), _row(RUN_AS_ANOTHER_ACCOUNT)],
        ):
            table = components.run_table(rows)
            for data_row in table.rows:
                assert len(data_row.cells) == len(table.columns)

    def test_the_other_columns_are_unchanged_when_it_is_absent(self) -> None:
        """The per-user byte-identity promise, at the only place this slice touches the table."""
        headers = _headers([_row(""), _row("")])
        assert headers[:3] == ["When", "Status", "Source"]
        assert headers[3] == "Students"
