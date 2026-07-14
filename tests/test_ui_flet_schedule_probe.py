"""Tests for src/ui_flet/schedule_probe.py — the read-back → derive → log boundary (D4).

``read_schedule`` (the PowerShell subprocess) is mocked; these assert the boundary maps the
tri-state read-back to the pure derivation AND logs the config-vs-reality contradiction (the
durable Event-141 trace) without leaking PII.
"""

from __future__ import annotations

import logging

from src.scheduler.windows import ScheduleReadback
from src.ui_flet import schedule_probe
from src.ui_flet.schedule_status import ScheduleState


def _patch_readback(monkeypatch, readback: ScheduleReadback) -> None:
    monkeypatch.setattr(schedule_probe, "read_schedule", lambda _name: readback)


def test_probe_maps_found_true_to_live(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=True, next_run="2026-07-09T03:00:00.0000000"))
    status = schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True)
    assert status.state is ScheduleState.LIVE


def test_probe_maps_found_false_to_missing(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    status = schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True)
    assert status.state is ScheduleState.MISSING


def test_probe_maps_found_none_to_unknown(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=None, error="denied"))
    status = schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True)
    assert status.state is ScheduleState.UNKNOWN


def test_expected_missing_logs_contradiction_warning(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True)
    assert any("NOT found in Windows" in r.message for r in caplog.records)
    # PII-free: only the config-controlled task name appears.
    assert all("password" not in r.getMessage().lower() for r in caplog.records)


def test_unexpected_missing_does_not_warn(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=False)
    assert not caplog.records


def test_contradiction_logs_warning(monkeypatch, caplog) -> None:
    # A record-gap contradiction: the task fired more recently than the newest recorded run.
    _patch_readback(monkeypatch, ScheduleReadback(found=True, last_run="2026-07-08T03:00:00"))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        status = schedule_probe.probe_schedule(
            "DistrictSync_Daily", hint_registered=True, latest_record_ts="2026-07-07T03:00:00"
        )
    assert status.contradiction is True
    assert any("fired but DistrictSync did not record" in r.message for r in caplog.records)


def test_clean_live_does_not_warn(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True)
    assert not caplog.records
