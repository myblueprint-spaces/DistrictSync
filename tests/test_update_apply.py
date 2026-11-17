"""DSYNC-64 (plan 0054 S5): when a staged update is handed to the installer, and when it is not (stand-in package)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.config.app_config import AppConfig
from src.update import apply, check
from tests.test_update_check import NOW, TARGET, FakeUpdater, _HandoffRefused, _Staged


def _run(eligible, fake, *, trigger=check.TRIGGER_AFTER_RUN, config=None, now=NOW, running=lambda: 0):
    return check.run_update_check(
        trigger,
        config=eligible.config if config is None else config,
        target=TARGET,
        updater=fake,
        state_dir=eligible.state,
        now=now,
        installer=object(),
        running=running,
    )


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 17, hour, minute, tzinfo=UTC)


def _local(hour: int, minute: int) -> datetime:
    """A moment on the local clock: the registered run time is local, so the window rule reads local time."""
    return datetime(2026, 11, 17, hour, minute).astimezone()


class TestMinutesToNextRun:
    def cfg(self, time: str = "03:00", registered: bool = True) -> AppConfig:
        return AppConfig(schedule_registered=registered, schedule_time=time)

    def test_later_today(self):
        assert apply.minutes_to_next_run(self.cfg(), _at(2, 0)) == 60

    def test_already_past_means_tomorrow(self):
        assert apply.minutes_to_next_run(self.cfg(), _at(4, 0)) == 23 * 60

    def test_nothing_registered_is_none(self):
        assert apply.minutes_to_next_run(self.cfg(registered=False), _at(2, 0)) is None

    @pytest.mark.parametrize("bad", ["", "3", "25:00", "ab:cd", "03:75"])
    def test_an_unreadable_time_is_none(self, bad):
        assert apply.minutes_to_next_run(self.cfg(bad), _at(2, 0)) is None


class TestKeepClearOfTheNightly:
    def cfg(self) -> AppConfig:
        return AppConfig(schedule_registered=True, schedule_time="03:00")

    @pytest.mark.parametrize(
        "hour,minute,allowed",
        [(2, 29, True), (2, 30, False), (2, 45, False), (2, 59, False), (3, 0, True), (3, 1, True)],
    )
    def test_the_window_does_not_request_within_30_minutes_before_the_run(self, hour, minute, allowed):
        """Run at 03:00. At 03:00 exactly the run is due now (tomorrow's is 24 h away): the process-count refusal,
        not this rule, covers a nightly that is already running."""
        assert apply.window_may_request_apply(self.cfg(), _at(hour, minute)) is allowed

    def test_far_from_the_run_it_may(self):
        assert apply.window_may_request_apply(self.cfg(), _at(12, 0)) is True

    def test_no_schedule_means_it_may(self):
        assert apply.window_may_request_apply(AppConfig(), _at(2, 59)) is True

    def test_the_window_leaves_the_update_staged_inside_the_keep_clear_period(self, eligible):
        fake = FakeUpdater(result=_Staged("3.31.0"))
        outcome = _run(eligible, fake, trigger=check.TRIGGER_WINDOW, config=self.cfg(), now=_local(2, 45))
        assert outcome == check.CheckOutcome("staged", "not_applied_keep_clear_of_nightly", "3.31.0")
        assert fake.handoffs == []

    def test_the_window_applies_when_clear_of_the_nightly(self, eligible):
        """Positive twin."""
        fake = FakeUpdater(result=_Staged("3.31.0"))
        outcome = _run(eligible, fake, trigger=check.TRIGGER_WINDOW, config=self.cfg(), now=_local(12, 0))
        assert outcome.status == "handed_off" and len(fake.handoffs) == 1

    def test_the_scheduled_trigger_applies_right_after_the_nightly_whatever_the_clock(self, eligible):
        fake = FakeUpdater(result=_Staged("3.31.0"))
        outcome = _run(eligible, fake, trigger=check.TRIGGER_AFTER_RUN, config=self.cfg(), now=_local(2, 45))
        assert outcome.status == "handed_off"


class TestHandOffOutcomes:
    def test_the_package_sees_how_many_other_processes_run(self, eligible):
        fake = FakeUpdater(result=_Staged("3.31.0"))
        _run(eligible, fake, running=lambda: 0)
        assert fake.handoffs == [("3.31.0", 0)]

    def test_a_refusal_leaves_the_update_staged_with_its_reason(self, eligible):
        fake = FakeUpdater(result=_Staged("3.31.0"))
        fake.handoff_result = _HandoffRefused("app_running")
        assert _run(eligible, fake) == check.CheckOutcome("staged", "not_applied_app_running", "3.31.0")

    def test_an_exception_in_the_handoff_leaves_it_staged(self, eligible, caplog):
        fake = FakeUpdater(result=_Staged("3.31.0"))

        def boom(*_a, **_k):
            raise RuntimeError("installer exploded")

        fake.hand_off = boom
        assert _run(eligible, fake) == check.CheckOutcome("staged", "not_applied_error", "3.31.0")

    def test_nothing_is_handed_off_when_there_is_nothing_newer(self, eligible):
        fake = FakeUpdater(result=None)
        assert _run(eligible, fake).status == "none" and fake.handoffs == []


def test_other_instances_counter_passes_this_exe_name_and_pid(monkeypatch):
    seen: list[tuple[list[str], int]] = []
    fake = SimpleNamespace(running_instances=lambda names, pid: seen.append((names, pid)) or 2)
    monkeypatch.setattr("sys.executable", "C:/x/current/DistrictSync.exe")
    import os

    assert apply.other_instances_counter(fake)() == 2
    assert seen == [(["DistrictSync.exe"], os.getpid())]
