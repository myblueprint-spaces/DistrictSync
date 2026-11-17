"""DSYNC-64 (plan 0054 S5): what "healthy" means for DistrictSync, and the call sites (stand-in package).

The real package's crash-loop rollback is in ``tests/test_update_health_real.py``.
"""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest

from src import main as main_module
from src.update import health
from src.update.health import counts_as_a_start
from tests.test_installed_flavour import _unstamped


class _Action:
    CONTINUE = "continue"
    ROLL_BACK = "roll_back"


class FakeHealthUpdater(types.ModuleType):
    HealthAction = _Action

    def __init__(self, *, action: str = "continue", kept_version: str | None = None) -> None:
        super().__init__("myb_desktop_updater")
        self.action = action
        self.calls: list[tuple] = []
        outer = self

        class HealthStore:
            def __init__(self, path):
                self.path = path

            def mark_healthy(self, version):
                outer.calls.append(("mark_healthy", version))

        class LastGoodStore:
            def __init__(self, root):
                pass

            def get(self):
                return SimpleNamespace(version=kept_version) if kept_version else None

        self.HealthStore = HealthStore
        self.LastGoodStore = LastGoodStore
        self.AppIdentity = lambda *a: SimpleNamespace(name=a[0])
        self.parse_version = lambda v: (1,)
        self.HandoffRefused = type("HandoffRefused", (), {})

        def startup_health(store, version, previous, identity):
            outer.calls.append(("startup_health", version, previous))
            return outer.action, f"Version {version} did not start cleanly"

        def restore_last_good(installer, identity, state_dir, running):
            outer.calls.append(("restore", running()))
            return SimpleNamespace(version=kept_version, restore=True)

        self.startup_health = startup_health
        self.restore_last_good = restore_last_good
        self.clean_work_folders = lambda state_dir: outer.calls.append(("clean", str(state_dir)))


@pytest.fixture(autouse=True)
def _fresh():
    health.forget_current()
    yield
    health.forget_current()


class TestWhichStartsCount:
    @pytest.mark.parametrize("args", [[], ["--sis", "myedbc", "--input", "x"]])
    def test_the_window_and_a_conversion_count(self, args):
        assert counts_as_a_start(args) is True

    @pytest.mark.parametrize(
        "args", [["--version"], ["--diagnose"], ["--sftp-test"], ["--sftp-show"], ["--help"], ["--elevated-apply", "x"]]
    )
    def test_everything_else_never_reaches_a_healthy_mark_so_it_must_not_count(self, args):
        assert counts_as_a_start(args) is False


class TestBeginAndMark:
    def test_portable_build_tracks_nothing(self, monkeypatch):
        _unstamped(monkeypatch)
        assert health.begin_start(updater=FakeHealthUpdater()) is None

    def test_a_start_is_recorded_with_the_kept_version_as_previous(self, eligible):
        fake = FakeHealthUpdater(kept_version="3.29.0")
        record = health.begin_start(updater=fake, state_dir=eligible.state, installer=object(), running=lambda: 0)
        assert record is not None and record.version == "3.30.0"
        assert ("startup_health", "3.30.0", "3.29.0") in fake.calls

    def test_with_no_kept_copy_the_previous_is_the_running_version_so_nothing_can_roll_back(self, eligible):
        fake = FakeHealthUpdater(kept_version=None)
        health.begin_start(updater=fake, state_dir=eligible.state, installer=object(), running=lambda: 0)
        assert ("startup_health", "3.30.0", "3.30.0") in fake.calls

    def test_marking_healthy_records_the_version_and_cleans_the_work_folders(self, eligible):
        fake = FakeHealthUpdater()
        health.begin_start(updater=fake, state_dir=eligible.state, installer=object(), running=lambda: 0)
        assert health.mark_healthy() is True
        assert ("mark_healthy", "3.30.0") in fake.calls and ("clean", str(eligible.state)) in fake.calls

    def test_marking_with_nothing_begun_is_a_no_op(self):
        assert health.mark_healthy() is False

    def test_a_bad_version_with_a_kept_copy_restores_it_after_exit(self, eligible, caplog):
        fake = FakeHealthUpdater(action="roll_back", kept_version="3.29.0")
        with caplog.at_level(logging.WARNING, logger=health.logger.name):
            health.begin_start(updater=fake, state_dir=eligible.state, installer=object(), running=lambda: 0)
        assert ("restore", 0) in fake.calls
        assert any("3.29.0" in r.getMessage() for r in caplog.records)

    def test_a_continue_does_not_restore(self, eligible):
        fake = FakeHealthUpdater(action="continue", kept_version="3.29.0")
        health.begin_start(updater=fake, state_dir=eligible.state, installer=object(), running=lambda: 0)
        assert not any(c[0] == "restore" for c in fake.calls)

    def test_tracking_failure_never_stops_the_start(self, eligible, caplog):
        fake = FakeHealthUpdater()

        def boom(*_a):
            raise RuntimeError("store exploded")

        fake.startup_health = boom
        with caplog.at_level(logging.WARNING, logger=health.logger.name):
            assert health.begin_start(updater=fake, state_dir=eligible.state) is None
        assert any("start goes on" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("guard", ["machine", "override"])
    def test_machine_scope_and_the_data_dir_override_track_nothing(self, eligible, monkeypatch, guard):
        if guard == "machine":
            monkeypatch.setattr(health, "_in_machine_scope", lambda: True)
        else:
            monkeypatch.setattr(health.paths, "data_dir_override_active", lambda: True)
        assert health.begin_start(updater=FakeHealthUpdater(), state_dir=eligible.state) is None


class TestCallSites:
    @pytest.fixture
    def cli(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
        events: list[str] = []
        monkeypatch.setattr(health, "begin_start", lambda **_k: events.append("begin") or None)
        monkeypatch.setattr(health, "mark_healthy", lambda *_a: events.append("mark") or True)

        def pipeline(*_a, **_k):
            events.append("etl")
            return SimpleNamespace(sftp_attempted=False, sftp_ok=True)

        monkeypatch.setattr(main_module, "run_pipeline", pipeline)
        return events, ["--sis", "myedbc", "--input", str(tmp_path), "--output", str(tmp_path)]

    def test_a_conversion_begins_early_and_is_marked_healthy_before_etl(self, cli):
        events, args = cli
        assert main_module.cli(args) == 0
        assert events == ["begin", "mark", "etl"]

    def test_a_failed_conversion_is_still_a_healthy_start(self, cli, monkeypatch):
        """Healthy is start-up completing, never the conversion succeeding (a missing GDE file is a data problem)."""
        events, args = cli

        def failing(*_a, **_k):
            events.append("etl")
            raise RuntimeError("a GDE file is missing")

        monkeypatch.setattr(main_module, "run_pipeline", failing)
        assert main_module.cli(args) == 1
        assert events == ["begin", "mark", "etl"]

    def test_version_never_counts_as_a_start(self, cli):
        events, _ = cli
        assert main_module.cli(["--version"]) == 0
        assert events == []

    def test_diagnose_never_counts_as_a_start(self, cli):
        events, _ = cli
        assert main_module.cli(["--diagnose"]) == 0
        assert events == []

    def test_the_window_marks_healthy_when_the_first_screen_is_up(self, monkeypatch):
        from src.ui_flet import shell

        events: list[str] = []
        monkeypatch.setattr(health, "mark_healthy", lambda *_a: events.append("mark") or True)
        shell._mark_start_healthy()
        assert events == ["mark"]

    def test_a_marking_failure_is_invisible_to_the_window(self, monkeypatch):
        from src.ui_flet import shell

        def boom(*_a):
            raise RuntimeError("x")

        monkeypatch.setattr(health, "mark_healthy", boom)
        shell._mark_start_healthy()  # must not raise


def test_the_window_path_begins_a_start_before_the_ui_launches(monkeypatch, tmp_path):
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
    events: list[str] = []
    monkeypatch.setattr(health, "begin_start", lambda **_k: events.append("begin") or None)
    monkeypatch.setattr(main_module, "_default_ui_launcher", lambda: lambda: events.append("ui"))
    assert main_module.cli([]) == 0
    assert events == ["begin", "ui"]


def test_sys_is_left_alone():
    """The fixtures patch ``sys.executable``; a leak would break every later subprocess test."""
    assert sys.executable.lower().endswith(("python.exe", "python3", "python", "python3.13", "pythonw.exe"))
