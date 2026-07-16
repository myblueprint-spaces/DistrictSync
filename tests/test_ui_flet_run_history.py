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
from src.ui_flet.home_status import (
    STALE_AFTER_HOURS,
    LatestReason,
    classify_latest_reason,
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
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
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
    def test_empty_established_with_live_schedule_shows_plain_time(self) -> None:
        # An established install with an empty store post-update → fresh-start copy, NOT the
        # false "No sync has run yet"; the next-run time derives from the LIVE read-back (D4).
        banner = derive_history_banner([], _CONFIGURED, now=_NOW, schedule_status=_live_schedule("3:00 AM"))
        assert banner.verdict is Verdict.WARNING  # never red
        assert banner.headline == "Run history starts fresh here"
        assert "3:00 AM" in banner.detail

    def test_empty_established_without_schedule_status_omits_time(self) -> None:
        banner = derive_history_banner([], _CONFIGURED, now=_NOW)
        assert banner.headline == "Run history starts fresh here"
        assert "Scheduled for" not in banner.detail
        # Honesty C: conditioned hidden-history claim, never a flat assertion.
        assert "If you used an earlier version" in banner.detail

    def test_empty_genuine_first_run_unscheduled_says_no_sync_yet(self) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=None)
        assert banner.verdict is Verdict.WARNING
        assert banner.headline == "No sync has run yet"
        assert "scheduled for" not in banner.detail.lower()

    def test_empty_completed_manual_only_upgrader_gets_fresh_start(self) -> None:
        # D4a: a completed-setup manual-only install is established via setup_completed.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=None)
        assert banner.headline == "Run history starts fresh here"

    def test_empty_store_created_at_signals_established(self) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at="2026-07-01T03:00:00")
        assert banner.headline == "Run history starts fresh here"

    def test_empty_completed_but_confirmed_unscheduled_says_no_auto_sync(self) -> None:
        # #1b: same honest no-auto-sync copy as Home when the read-back CONFIRMS no schedule.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        missing = ScheduleStatus(state=ScheduleState.MISSING, headline="", detail="", attention=False)
        banner = derive_history_banner([], cfg, now=_NOW, schedule_status=missing)
        assert banner.verdict is Verdict.WARNING
        assert "won't sync automatically" in banner.detail
        assert "New nightly syncs will appear" not in banner.detail


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

    def test_clean_delivered_is_healthy(self) -> None:
        banner = _banner(_record())
        assert banner.verdict is Verdict.HEALTHY
        assert banner.headline == "Your sync is running"
        assert _RECENT not in banner.detail  # plain relative phrase, not the raw ISO


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
