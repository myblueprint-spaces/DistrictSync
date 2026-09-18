"""Tests for src/ui_flet/schedule_probe.py — the read-back → derive → log boundary (D4).

``read_schedule`` (the PowerShell subprocess) is mocked; these assert the boundary maps the
tri-state read-back to the pure derivation AND logs the config-vs-reality contradiction (the
durable Event-141 trace) without leaking PII.
"""

from __future__ import annotations

import logging

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet import schedule_probe
from src.ui_flet.schedule_status import ScheduleState


def _patch_readback(monkeypatch, readback: ScheduleReadback) -> None:
    monkeypatch.setattr(schedule_probe, "read_schedule", lambda _name: readback)


def test_probe_maps_found_true_to_live(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=True, next_run="2026-07-09T03:00:00.0000000"))
    status = schedule_probe.probe_schedule(
        "DistrictSync_Daily", hint_registered=True, foreign_account="", shared_records=False
    )
    assert status.state is ScheduleState.LIVE


def test_probe_maps_found_false_to_missing(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    status = schedule_probe.probe_schedule(
        "DistrictSync_Daily", hint_registered=True, foreign_account="", shared_records=False
    )
    assert status.state is ScheduleState.MISSING


def test_probe_maps_found_none_to_unknown(monkeypatch) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=None, error="denied"))
    status = schedule_probe.probe_schedule(
        "DistrictSync_Daily", hint_registered=True, foreign_account="", shared_records=False
    )
    assert status.state is ScheduleState.UNKNOWN


def test_expected_missing_logs_contradiction_warning(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule(
            "DistrictSync_Daily", hint_registered=True, foreign_account="", shared_records=False
        )
    assert any("NOT found in Windows" in r.message for r in caplog.records)
    # PII-free: only the config-controlled task name appears.
    assert all("password" not in r.getMessage().lower() for r in caplog.records)


def test_unexpected_missing_does_not_warn(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=False))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule(
            "DistrictSync_Daily", hint_registered=False, foreign_account="", shared_records=False
        )
    assert not caplog.records


def test_contradiction_logs_warning(monkeypatch, caplog) -> None:
    # A record-gap contradiction: the task fired more recently than the newest recorded run.
    _patch_readback(monkeypatch, ScheduleReadback(found=True, last_run="2026-07-08T03:00:00"))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        status = schedule_probe.probe_schedule(
            "DistrictSync_Daily",
            hint_registered=True,
            latest_record_ts="2026-07-07T03:00:00",
            foreign_account="",
            shared_records=False,
        )
    assert status.contradiction is True
    assert any("fired but DistrictSync did not record" in r.message for r in caplog.records)


def test_clean_live_does_not_warn(monkeypatch, caplog) -> None:
    _patch_readback(monkeypatch, ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"))
    with caplog.at_level(logging.WARNING, logger="src.ui_flet.schedule_probe"):
        schedule_probe.probe_schedule(
            "DistrictSync_Daily", hint_registered=True, foreign_account="", shared_records=False
        )
    assert not caplog.records


# --------------------------------------------------------------------------- #
# Plan 0046 C — ``foreign_task_account``: the ONE impure resolution of A5's fact #
# --------------------------------------------------------------------------- #
_SERVICE = "CONTOSO\\svc_districtsync"
_SIGNED_IN = "CONTOSO\\admin"
_TASK_ARGS = {
    "input_dir": "C:/in",
    "output_dir": "C:/out",
    "sis_type": "myedbc",
    "run_time": "03:00",
    "sftp_enabled": False,
}


class _FakeScheduler:
    """Stands in for ``get_scheduler()`` — only ``run_as_user`` matters here."""

    def __init__(self, account: str | None = _SIGNED_IN, raises: bool = False) -> None:
        self._account = account
        self._raises = raises

    def run_as_user(self) -> str:
        if self._raises:
            raise OSError("cannot resolve the current account")
        return self._account or ""


def _cfg(**overrides) -> AppConfig:
    cfg = AppConfig()
    cfg.schedule_task_args = dict(_TASK_ARGS)
    cfg.schedule_unattended = True
    cfg.schedule_run_as_user = ""
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _patch_scheduler(monkeypatch, scheduler: _FakeScheduler) -> None:
    monkeypatch.setattr(schedule_probe, "get_scheduler", lambda: scheduler)


class TestForeignTaskAccountFailsToBlank:
    """EVERY unknown resolves to ``""`` — and ``""`` ALARMS.

    The asymmetry is the whole design: going quiet on an unknown record silently disables the
    app's only "did it actually run?" signal for the districts NOT on a service account —
    invisibly and unboundedly. Staying noisy on a torn record costs one visible amber that the
    next registration heals.
    """

    def test_no_task_args_record_means_the_principal_is_unknown(self, monkeypatch) -> None:
        """``RegisteredSchedule`` is ATOMIC — an absent ``args`` makes ``run_as_user`` ``None``."""
        _patch_scheduler(monkeypatch, _FakeScheduler())
        cfg = _cfg(schedule_task_args=None, schedule_run_as_user=_SERVICE)
        assert schedule_probe.foreign_task_account(cfg) == ""

    def test_a_blank_recorded_principal_is_the_signed_in_account(self, monkeypatch) -> None:
        """Today's world, and the EVIDENCED pre-0046 value — no build could register anything else."""
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user="")) == ""

    def test_a_whitespace_only_recorded_principal_is_not_foreign(self, monkeypatch) -> None:
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user="   ")) == ""

    @pytest.mark.parametrize("recorded", [_SIGNED_IN, _SIGNED_IN.upper(), _SIGNED_IN.lower(), f"  {_SIGNED_IN}  "])
    def test_a_case_insensitive_match_is_not_foreign(self, monkeypatch, recorded: str) -> None:
        """Reduced through ``setup_gates.principal_key``, which restates ``register_task``'s
        own equivalence — so the view and the engine cannot disagree about what foreign IS."""
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user=recorded)) == ""

    def test_a_raising_account_resolver_is_not_foreign(self, monkeypatch) -> None:
        """THE named trap, inverted: a failed resolution must never make every account foreign."""
        _patch_scheduler(monkeypatch, _FakeScheduler(raises=True))
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user=_SERVICE)) == ""

    def test_an_unreadable_config_is_not_foreign(self, monkeypatch) -> None:
        class _Exploding:
            @property
            def schedule_task_args(self):  # noqa: ANN201
                raise OSError("settings unreadable")

        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_Exploding()) == ""  # type: ignore[arg-type]

    def test_it_does_not_compare_against_the_setup_screens_this_account_literal(self, monkeypatch) -> None:
        """``screens/setup.py::_keyring_owner_account`` falls back to the literal "this account".

        Reusing it as the comparison's ``current`` side would make EVERY recorded name compare
        foreign and fire the suppression in the UNSAFE direction on any machine where the account
        resolution fails. A raising resolver must yield "" — never a comparison against that string.
        """
        import inspect

        _patch_scheduler(monkeypatch, _FakeScheduler(raises=True))
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user="this account")) == ""
        # The BODY only — the docstring names the trap on purpose, and naming it is the point.
        body = inspect.getsource(schedule_probe.foreign_task_account).split('"""')[-1]
        assert "this account" not in body
        assert "_keyring_owner_account" not in body


class TestForeignTaskAccountPositiveTwin:
    """The one path that DOES suppress — a genuinely foreign recorded principal."""

    def test_a_foreign_recorded_principal_is_returned_verbatim(self, monkeypatch) -> None:
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user=_SERVICE)) == _SERVICE

    def test_the_name_is_not_case_folded_for_display(self, monkeypatch) -> None:
        """``principal_key`` case-folds only to COMPARE; the copy must name the account as recorded."""
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user="CONTOSO\\SvcDS")) == "CONTOSO\\SvcDS"

    def test_surrounding_whitespace_is_stripped(self, monkeypatch) -> None:
        _patch_scheduler(monkeypatch, _FakeScheduler())
        assert schedule_probe.foreign_task_account(_cfg(schedule_run_as_user=f" {_SERVICE} ")) == _SERVICE


class TestProbePassesTheAccountThrough:
    def test_foreign_account_reaches_the_derived_status(self, monkeypatch) -> None:
        _patch_readback(monkeypatch, ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"))
        status = schedule_probe.probe_schedule(
            "DistrictSync_Daily", hint_registered=True, foreign_account=_SERVICE, shared_records=False
        )
        assert status.foreign_account == _SERVICE
        assert _SERVICE in status.detail

    def test_it_is_required_keyword_only(self, monkeypatch) -> None:
        """A forgotten kwarg must raise — the screens swallow worker-thread exceptions, and mypy
        excludes ``src/ui_flet``, so nothing else would catch it."""
        _patch_readback(monkeypatch, ScheduleReadback(found=True))
        with pytest.raises(TypeError):
            schedule_probe.probe_schedule("DistrictSync_Daily", hint_registered=True, shared_records=False)  # type: ignore[call-arg]


class TestNoAccountNameEverReachesTheLog:
    """Privacy posture UNCHANGED: the account name is rendered on screen only."""

    def test_the_contradiction_warning_payload_is_byte_identical(self, monkeypatch, caplog) -> None:
        _patch_readback(monkeypatch, ScheduleReadback(found=False))
        with caplog.at_level(logging.WARNING):
            schedule_probe.probe_schedule(
                "DistrictSync_Daily", hint_registered=True, foreign_account=_SERVICE, shared_records=False
            )
        assert caplog.records, "the expected-missing divergence must still WARN"
        for record in caplog.records:
            assert _SERVICE not in record.getMessage()
            assert "svc_districtsync" not in record.getMessage()

    def test_no_divergence_path_names_the_account(self, monkeypatch, caplog) -> None:
        gap = ScheduleReadback(found=True, next_run="2026-07-10T03:00:00", last_run="2026-07-09T04:00:00")
        for account in ("", _SERVICE):
            caplog.clear()
            _patch_readback(monkeypatch, gap)
            with caplog.at_level(logging.WARNING):
                schedule_probe.probe_schedule(
                    "DistrictSync_Daily",
                    hint_registered=True,
                    latest_record_ts="2026-07-09T02:00:00",
                    foreign_account=account,
                    shared_records=False,
                )
            for record in caplog.records:
                assert _SERVICE not in record.getMessage()

    def test_log_divergence_source_holds_no_account_reference(self) -> None:
        import inspect

        source = inspect.getsource(schedule_probe._log_divergence)
        assert "foreign_account" not in source
        assert "run_as_user" not in source
