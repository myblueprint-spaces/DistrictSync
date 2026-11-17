"""DSYNC-64 (plan 0054 S5): what "healthy" means for DistrictSync, and the call sites (stand-in package).

The crash counting and the rollback are the start guard's (``tests/test_update_entry*.py``).
"""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest

from src import main as main_module
from src.update import health
from tests.test_installed_flavour import _unstamped


class FakeHealthUpdater(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("myb_desktop_updater")
        self.calls: list[tuple] = []
        outer = self

        class HealthStore:
            def __init__(self, path):
                self.path = path

            def mark_healthy(self, version):
                outer.calls.append(("mark_healthy", version))

        self.HealthStore = HealthStore
        self.parse_version = lambda v: (1,)
        self.clean_work_folders = lambda state_dir: outer.calls.append(("clean", str(state_dir)))


class TestMarkHealthy:
    @pytest.fixture
    def fake(self, eligible, monkeypatch):
        fake = FakeHealthUpdater()
        monkeypatch.setattr(health, "load_updater", lambda: fake)
        monkeypatch.setattr(health, "updater_state_dir", lambda: eligible.state)
        return fake

    def test_marking_healthy_records_the_version_and_cleans_the_work_folders(self, eligible, fake):
        assert health.mark_healthy() is True
        assert ("mark_healthy", "3.30.0") in fake.calls and ("clean", str(eligible.state)) in fake.calls

    def test_it_also_clears_the_start_guards_count(self, fake, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(health, "confirm_start", lambda: events.append("confirm") or True)
        health.mark_healthy()
        assert events == ["confirm"]

    def test_portable_build_records_nothing_but_still_clears_the_guard(self, monkeypatch):
        _unstamped(monkeypatch)
        events: list[str] = []
        monkeypatch.setattr(health, "confirm_start", lambda: events.append("confirm") or True)
        assert health.mark_healthy() is False and events == ["confirm"]

    def test_a_store_failure_never_stops_the_start(self, fake, caplog):
        def boom(*_a):
            raise RuntimeError("store exploded")

        fake.HealthStore.mark_healthy = boom
        with caplog.at_level(logging.WARNING, logger=health.logger.name):
            assert health.mark_healthy() is False
        assert any("healthy start" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("guard", ["machine", "override"])
    def test_machine_scope_and_the_data_dir_override_record_nothing(self, eligible, fake, monkeypatch, guard):
        if guard == "machine":
            monkeypatch.setattr(health, "_in_machine_scope", lambda: True)
        else:
            monkeypatch.setattr(health.paths, "data_dir_override_active", lambda: True)
        assert health.mark_healthy() is False and fake.calls == []

    def test_there_is_no_second_crash_counter_in_this_module(self):
        """DSYNC-66: the start guard is the only counter; a begin_start here would double count."""
        assert not hasattr(health, "begin_start") and not hasattr(health, "counts_as_a_start")


class TestCallSites:
    @pytest.fixture
    def cli(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
        events: list[str] = []
        monkeypatch.setattr(health, "mark_healthy", lambda *_a: events.append("mark") or True)

        def pipeline(*_a, **_k):
            events.append("etl")
            return SimpleNamespace(sftp_attempted=False, sftp_ok=True)

        monkeypatch.setattr(main_module, "run_pipeline", pipeline)
        return events, ["--sis", "myedbc", "--input", str(tmp_path), "--output", str(tmp_path)]

    def test_a_conversion_begins_early_and_is_marked_healthy_before_etl(self, cli):
        events, args = cli
        assert main_module.cli(args) == 0
        assert events == ["mark", "etl"]

    def test_a_failed_conversion_is_still_a_healthy_start(self, cli, monkeypatch):
        """Healthy is start-up completing, never the conversion succeeding (a missing GDE file is a data problem)."""
        events, args = cli

        def failing(*_a, **_k):
            events.append("etl")
            raise RuntimeError("a GDE file is missing")

        monkeypatch.setattr(main_module, "run_pipeline", failing)
        assert main_module.cli(args) == 1
        assert events == ["mark", "etl"]

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


def test_sys_is_left_alone():
    """The fixtures patch ``sys.executable``; a leak would break every later subprocess test."""
    assert sys.executable.lower().endswith(("python.exe", "python3", "python", "python3.13", "pythonw.exe"))
