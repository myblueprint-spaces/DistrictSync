"""DSYNC-66 (plan 0054 S5 follow-up): the REAL start guard and recovery, driven through DistrictSync's wiring.

A crash in the guarded app ends the process with ``os._exit``, which cannot run inside pytest, so a build that fails at import
is simulated by a ``main`` that returns without confirming (the guard counts it the same way); the real crash exit and the real
restore are proven by ``scripts/updater_e2e.py``. Skips as a whole when the private package is absent; the positive twin in
``tests/test_update_package_wiring.py`` fails a push to main if it is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.update import entry

guard = pytest.importorskip("myb_start_guard")
mdu = pytest.importorskip("myb_desktop_updater")
recovery = pytest.importorskip("myb_desktop_updater.recovery")

from tests.test_update_apply_real import APP_ID, FakeInstaller  # noqa: E402


@pytest.fixture
def world(eligible, monkeypatch, tmp_path):
    state = eligible.state
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.utils.version.app_version", lambda: "3.31.0")
    # The running (new, broken) build is 3.31.0; the kept copy of the last good one is 3.30.0.
    installer = FakeInstaller(tmp_path, "3.31.0")
    good = tmp_path / f"{APP_ID}-3.30.0-full.nupkg"
    good.write_bytes(b"the last good package")
    mdu.LastGoodStore(state).keep("3.30.0", (good,))
    monkeypatch.setattr("myb_desktop_updater.velopack_installer.VelopackInstaller", lambda *a, **k: installer)
    monkeypatch.setattr(recovery, "running_instances", lambda names, pid: 0)
    return type("World", (), {"state": state, "installer": installer})


def _start(world, *, confirm: bool = False, counted: bool = True):
    """One start of the broken build: counted by the real guard, recovered by DistrictSync's real recovery step."""

    def main() -> int:
        if confirm:
            guard.confirm_start(world.state)
        return 0

    return guard.run_guarded(
        main,
        state_dir=world.state,
        build="broken-build",
        recover=lambda: entry._restore_kept_copy(world.state),
        counted=counted,
    )


class TestARealBrokenStart:
    def test_a_build_that_never_confirms_is_recovered_on_the_fourth_start_before_it_runs_again(self, world):
        for _ in range(3):  # three starts that never reach a first screen: tolerated
            assert _start(world) == 0
        assert world.installer.applied == []
        assert _start(world) == 1  # the fourth stops BEFORE main, with the restore handed to the installer
        assert world.installer.applied == ["3.30.0"]
        assert world.installer.prepared == [("restore", [f"{APP_ID}-3.30.0-full.nupkg"])]

    def test_the_broken_version_is_recorded_bad_so_the_feed_never_offers_it_again(self, world):
        for _ in range(4):
            _start(world)
        store = mdu.HealthStore(world.state / "health.json")
        assert "3.31.0" in store.bad_versions()

    def test_a_confirmed_start_clears_the_count_so_failing_nightlies_never_roll_back(self, world):
        """A nightly that fails its conversion still confirmed its start (start-up completed): never a crash."""
        for _ in range(8):
            assert _start(world, confirm=True) == 0
        assert world.installer.applied == []
        assert not (world.state / guard.COUNT_FILE).exists()

    def test_uncounted_commands_never_trigger_recovery(self, world):
        for _ in range(8):
            assert _start(world, counted=False) == 0
        assert world.installer.applied == [] and not (world.state / guard.COUNT_FILE).exists()

    def test_with_no_kept_copy_the_app_just_tries_again(self, world):
        mdu.LastGoodStore(world.state).get()
        for item in (world.state / "last-good").glob("*"):
            if item.is_file():
                item.unlink()
        results = [_start(world) for _ in range(6)]
        assert results == [0] * 6 and world.installer.applied == []

    def test_a_new_build_starts_a_new_count(self, world):
        for _ in range(3):
            _start(world)
        data = json.loads((world.state / guard.COUNT_FILE).read_text(encoding="utf8"))
        assert data["starts"] == 3
        assert guard.begin_start(world.state, "another-build") == 1


def test_the_start_guard_module_is_the_packages_and_stdlib_only():
    assert Path(guard.__file__).name == "__init__.py"
