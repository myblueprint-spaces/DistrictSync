"""DSYNC-64 (plan 0054 S5): hand-off, last-good copy, health and rollback through the REAL shared package.

Only the Velopack installer is replaced (by ``FakeInstaller``, an implementation of the package's own ``Installer``
protocol), so the package's real refusals, real ``HealthStore`` and real ``LastGoodStore`` run. The Velopack side
itself is proven by running, on a real build and a loopback feed (``scripts/updater_e2e.py``), not here.

Skips as a whole when the private package is absent (fork and Dependabot pull requests); the positive twin in
``tests/test_update_package_wiring.py`` fails a push to main if it is.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.update import check, health
from src.update.package import app_identity

mdu = pytest.importorskip("myb_desktop_updater")

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
APP_ID = "myBlueprint.DistrictSync"


class FakeInstaller:
    """Implements ``myb_desktop_updater.Installer`` and records what it was asked to do."""

    def __init__(self, root: Path, version: str = "3.30.0") -> None:
        self.version = version
        self.package = root / f"{APP_ID}-{version}-full.nupkg"
        self.package.write_bytes(b"the running package")
        self.prepared: list[tuple[str, list[str]]] = []  # (target version, file names in the feed folder)
        self.applied: list[str] = []

    def installed_package(self):
        return mdu.InstalledPackage(self.version, (self.package,))

    def prepare(self, feed: Path):
        names = sorted(p.name for p in feed.iterdir())
        self.prepared.append(("update", names))
        return mdu.PendingUpdate("3.31.0", ("update", feed))

    def prepare_restore(self, good, work: Path):
        self.prepared.append(("restore", [p.name for p in good.files]))
        return mdu.PendingUpdate(good.version, ("restore", work))

    def apply_after_exit(self, pending) -> None:
        self.applied.append(pending.version)


@pytest.fixture
def world(eligible, tmp_path):
    state = eligible.state
    state.mkdir(parents=True, exist_ok=True)
    identity = app_identity(mdu)
    installer = FakeInstaller(tmp_path)
    data = b"the new package"
    staged_dir = state / "staging"
    staged_dir.mkdir()
    staged_file = staged_dir / f"{APP_ID}-3.31.0-full.nupkg"
    staged_file.write_bytes(data)
    staged = mdu.StagedUpdate("3.31.0", 1, staged_dir, (staged_file,), (hashlib.sha256(data).hexdigest(),))
    health_store = mdu.HealthStore(state / check.HEALTH_FILE)
    health_store.mark_healthy("3.30.0")  # the running version has started cleanly

    def hand_off(*, running=lambda: 0, trigger=check.TRIGGER_AFTER_RUN, config=None):
        return check._hand_off(
            mdu, staged, trigger, config or eligible.config, state, identity, health_store, NOW, installer, running
        )

    return type(
        "World",
        (),
        {
            "state": state,
            "identity": identity,
            "installer": installer,
            "staged": staged,
            "staged_file": staged_file,
            "health": health_store,
            "hand_off": staticmethod(hand_off),
        },
    )


class TestHandOff:
    def test_a_verified_update_is_kept_safe_then_handed_over_to_apply_after_exit(self, world):
        outcome = world.hand_off()
        assert outcome == check.CheckOutcome("handed_off", "handed_off", "3.31.0")
        assert world.installer.applied == ["3.31.0"]
        # The installer read the private copy of exactly the verified file, nothing else.
        assert world.installer.prepared == [("update", [world.staged_file.name])]
        # The running version's package is kept BEFORE the update is applied: there is a way back.
        good = mdu.LastGoodStore(world.state).get()
        assert good is not None and good.version == "3.30.0"

    def test_another_running_copy_leaves_it_staged_and_applies_nothing(self, world):
        outcome = world.hand_off(running=lambda: 1)
        assert outcome == check.CheckOutcome("staged", "not_applied_app_running", "3.31.0")
        assert world.installer.applied == [] and mdu.LastGoodStore(world.state).get() is None
        assert world.staged_file.exists()  # still staged for the next opportunity

    def test_an_uncountable_process_list_is_treated_as_running(self, world):
        outcome = world.hand_off(running=lambda: None)
        assert outcome.reason == "not_applied_app_count_unknown" and world.installer.applied == []

    def test_a_running_version_that_never_started_cleanly_is_not_updated_from(self, world):
        mdu.HealthStore(world.state / check.HEALTH_FILE).mark_healthy("3.29.0")  # healthy is now another version
        outcome = world.hand_off()
        assert outcome.reason == "not_applied_running_not_healthy" and world.installer.applied == []

    def test_a_staged_file_changed_after_it_was_verified_is_refused(self, world):
        world.staged_file.write_bytes(b"tampered after the check")
        outcome = world.hand_off()
        assert outcome.reason == "not_applied_staged_changed" and world.installer.applied == []

    def test_the_off_switch_applies_nothing(self, world, monkeypatch):
        monkeypatch.setenv("DISTRICTSYNC_UPDATES", "off")
        assert world.hand_off().reason == "not_applied_updates_off"
        assert world.installer.applied == []

    def test_a_copy_not_installed_by_the_installer_cannot_be_updated(self, world):
        world.installer.installed_package = lambda: None  # type: ignore[method-assign]
        assert world.hand_off().reason == "not_applied_not_installed"


class TestCrashLoopRollback:
    """The package's own policy (more than two failed starts), driven through DistrictSync's wiring."""

    @pytest.fixture
    def after_an_update(self, world, monkeypatch):
        world.hand_off()  # keeps 3.30.0 as the last good copy
        # The app now runs the NEW version (3.31.0), whose installer package is what ``installed_package`` reports.
        world.installer.version = "3.31.0"
        monkeypatch.setattr(health, "app_version", lambda: "3.31.0")
        health.forget_current()
        world.installer.applied.clear()
        world.installer.prepared.clear()
        return world

    def _begin(self, w):
        return health.begin_start(updater=mdu, state_dir=w.state, installer=w.installer, running=lambda: 0)

    def test_a_build_that_dies_at_start_is_rolled_back_and_never_offered_again(self, after_an_update):
        w = after_an_update
        for _ in range(3):  # three starts, none ever marked healthy: the build exits in its own start-up
            self._begin(w)
        assert w.installer.applied == []  # up to two failed starts are tolerated
        self._begin(w)  # the fourth start sees more than two failures before it
        assert w.installer.applied == ["3.30.0"]  # the kept copy is restored, applied after exit
        assert w.installer.prepared == [("restore", [f"{APP_ID}-3.30.0-full.nupkg"])]
        store = mdu.HealthStore(w.state / check.HEALTH_FILE)
        assert "3.31.0" in store.bad_versions()  # recorded bad: the feed will not offer it again

    def test_a_start_that_reaches_the_healthy_mark_is_never_rolled_back_even_if_every_run_fails(self, after_an_update):
        """Healthy is start-up completing, never the conversion succeeding: nightly runs that exit 1 are not crashes."""
        w = after_an_update
        for _ in range(6):
            self._begin(w)
            assert health.mark_healthy() is True  # reached before ETL; the conversion itself may then fail
        assert w.installer.applied == []
        assert mdu.HealthStore(w.state / check.HEALTH_FILE).bad_versions() == ()

    def test_with_no_kept_copy_there_is_nothing_to_roll_back_to(self, world, monkeypatch):
        monkeypatch.setattr(health, "app_version", lambda: "3.30.0")
        health.forget_current()
        for _ in range(6):
            health.begin_start(updater=mdu, state_dir=world.state, installer=world.installer, running=lambda: 0)
        assert world.installer.applied == []

    def test_rollback_works_while_updates_are_switched_off(self, after_an_update, monkeypatch):
        """A safety step, not an update check."""
        monkeypatch.setenv("DISTRICTSYNC_UPDATES", "off")
        w = after_an_update
        for _ in range(4):
            self._begin(w)
        assert w.installer.applied == ["3.30.0"]
