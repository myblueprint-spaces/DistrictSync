"""DSYNC-63 (plan 0054 S4): when DistrictSync checks for an update, and when it must not.

Runs everywhere: the shared package is replaced by a stand-in that counts every call, so "zero connections"
is asserted as "the package's check was never called". The real package, over real loopback sockets, is in
``tests/test_update_check_real.py``.
"""

from __future__ import annotations

import json
import logging
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import main as main_module
from src.config.app_config import AppConfig
from src.update import check, feed, status
from src.update.feed import FeedTarget
from tests.test_installed_flavour import _install_layout, _unstamped

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
TARGET = FeedTarget(host="feed.example.invalid", trusted_keys={"k1": b"\x04" + b"\x01" * 64})


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Raises:
    """Marks a result the stand-in RAISES (the real package returns its refusals as values)."""

    def __init__(self, error: Exception) -> None:
        self.error = error


class _HandoffRefused:
    def __init__(self, reason: str) -> None:
        self.reason = reason


class _Staged:
    def __init__(self, version: str) -> None:
        self.version = version


class FakeUpdater(types.ModuleType):
    """Stands in for ``myb_desktop_updater``; records every call that could open a connection."""

    UpdateRefused = _Refused

    def __init__(self, result=None, *, enabled: bool = True, parseable: bool = True) -> None:
        super().__init__("myb_desktop_updater")
        self.result = result
        self.enabled = enabled
        self.parseable = parseable
        self.checks: list[tuple[str, str]] = []  # (host, manifest path)
        outer = self

        class FeedFetcher:
            def __init__(self, host, allow_loopback_http=False, max_bytes=0, timeout=0.0):
                self.host, self.allow_loopback_http, self.timeout = host, allow_loopback_http, timeout

        class HealthStore:
            def __init__(self, path):
                self.path = path

            def last_sequence(self):
                return 0

            def bad_versions(self):
                return ()

            def is_healthy(self, version):
                return True

        def check_for_update(fetcher, manifest_path, *_rest):
            outer.checks.append((fetcher.host, manifest_path))
            if isinstance(outer.result, Raises):
                raise outer.result.error
            if callable(outer.result):
                return outer.result()
            return outer.result

        self.FeedFetcher = FeedFetcher
        self.HealthStore = HealthStore
        self.check_for_update = check_for_update
        self.HandoffRefused = _HandoffRefused
        self.handoffs: list[tuple[str, int | None]] = []  # (staged version, others running as the package saw it)
        self.handoff_result: object | None = None

        def hand_off(staged, installer, identity, state_dir, running_count, healthy):
            outer.handoffs.append((staged.version, running_count()))
            return outer.handoff_result if outer.handoff_result is not None else SimpleNamespace(version=staged.version)

        self.hand_off = hand_off
        self.AppIdentity = lambda *a: SimpleNamespace(name=a[0])
        self.updates_enabled = lambda _identity, _dir: outer.enabled
        self.parse_version = lambda v: (1,) if outer.parseable else None


def _run(eligible, updater, **kw):
    return check.run_update_check(
        check.TRIGGER_WINDOW,
        config=eligible.config,
        target=TARGET,
        updater=updater,
        state_dir=eligible.state,
        now=NOW,
        installer=kw.pop("installer", object()),
        running=kw.pop("running", lambda: 0),
        **kw,
    )


class TestGuards:
    """Each guard: the outcome is a skip with its reason AND the package's check was never called."""

    def test_the_eligible_build_checks_exactly_the_feed_host(self, eligible):
        """Positive twin of every skip below."""
        fake = FakeUpdater(result=None)
        outcome = _run(eligible, fake)
        assert outcome == check.CheckOutcome("none", "up_to_date")
        assert fake.checks == [("feed.example.invalid", "/manifest.json")]

    def test_portable_build(self, eligible, monkeypatch):
        _unstamped(monkeypatch)
        fake = FakeUpdater()
        assert _run(eligible, fake) == check.CheckOutcome("skipped", "portable")
        assert fake.checks == []

    def test_installed_build_not_under_an_install(self, eligible, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.executable", _install_layout(tmp_path / "elsewhere", with_update_exe=False))
        fake = FakeUpdater()
        assert _run(eligible, fake).reason == "not_installed"
        assert fake.checks == []

    def test_machine_scope(self, eligible, monkeypatch):
        monkeypatch.setattr(check.paths, "is_machine_scope", lambda: True)
        fake = FakeUpdater()
        assert _run(eligible, fake).reason == "machine_scope"
        assert fake.checks == []

    def test_a_refused_machine_profile_counts_as_machine_scope(self, eligible, monkeypatch):
        def refuse():
            raise check.paths.MachineScopeRefused(check.paths.MachineScopeRefusedReason.MISSING, Path("x"))

        monkeypatch.setattr(check.paths, "is_machine_scope", refuse)
        fake = FakeUpdater()
        assert _run(eligible, fake).reason == "machine_scope"
        assert fake.checks == []

    def test_data_dir_override(self, eligible, monkeypatch):
        monkeypatch.setattr(check.paths, "data_dir_override_active", lambda: True)
        fake = FakeUpdater()
        assert _run(eligible, fake).reason == "data_dir_override"
        assert fake.checks == []

    def test_off_in_settings(self, eligible):
        eligible.config = AppConfig(update_enabled=False)
        fake = FakeUpdater()
        assert _run(eligible, fake).reason == "off_in_settings"
        assert fake.checks == []

    def test_not_configured_means_no_feed_host_or_key(self, eligible):
        fake = FakeUpdater()
        outcome = check.run_update_check(
            check.TRIGGER_WINDOW, config=eligible.config, target=None, updater=fake, state_dir=eligible.state, now=NOW
        )
        assert outcome.reason == "not_configured"  # production holds neither host nor key yet
        assert fake.checks == []

    def test_package_missing(self, eligible, monkeypatch):
        def missing():
            raise ImportError("no package")

        monkeypatch.setattr(check, "load_updater", missing)
        assert _run(eligible, None).reason == "package_missing"

    def test_the_packages_own_off_switch(self, eligible):
        fake = FakeUpdater(enabled=False)
        assert _run(eligible, fake).reason == "off_switch"
        assert fake.checks == []

    def test_a_development_stamp(self, eligible):
        fake = FakeUpdater(parseable=False)
        assert _run(eligible, fake).reason == "unknown_version"
        assert fake.checks == []

    def test_a_check_just_made_is_not_repeated(self, eligible):
        fake = FakeUpdater(result=None)
        assert _run(eligible, fake).status == "none"
        again = _run(eligible, fake)
        assert again == check.CheckOutcome("skipped", "recent")
        assert len(fake.checks) == 1

    def test_an_old_check_is_repeated(self, eligible):
        fake = FakeUpdater(result=None)
        _run(eligible, fake)
        later = check.run_update_check(
            check.TRIGGER_WINDOW,
            config=eligible.config,
            target=TARGET,
            updater=fake,
            state_dir=eligible.state,
            now=NOW + check.MIN_INTERVAL + timedelta(minutes=1),
        )
        assert later.status == "none" and len(fake.checks) == 2

    def test_production_holds_neither_host_nor_key_today(self):
        """The slice ships inert: filling these two values in is the single act that switches checking on."""
        assert feed.production_target() is None


class TestOutcomes:
    def test_a_staged_update_is_handed_to_the_installer(self, eligible):
        assert _run(eligible, FakeUpdater(result=_Staged("3.31.0"))) == check.CheckOutcome(
            "handed_off", "handed_off", "3.31.0"
        )

    def test_a_refusal_is_a_result_with_its_code(self, eligible):
        assert _run(eligible, FakeUpdater(result=_Refused("unreachable"))) == check.CheckOutcome(
            "refused", "unreachable"
        )

    def test_an_exception_is_a_result_not_an_exception(self, eligible, caplog):
        with caplog.at_level(logging.WARNING, logger=check.logger.name):
            outcome = _run(eligible, FakeUpdater(result=Raises(RuntimeError("boom"))))
        assert outcome == check.CheckOutcome("error", "raised")
        assert any("RuntimeError" in r.getMessage() for r in caplog.records)

    def test_a_slow_check_is_cut_off_by_the_budget(self, eligible):
        outcome = _run(eligible, FakeUpdater(result=lambda: time.sleep(2)), budget=0.05)
        assert outcome == check.CheckOutcome("error", "budget_exceeded")

    def test_nothing_unexpected_escapes(self, eligible, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("guard blew up")

        monkeypatch.setattr(check, "skip_reason", boom)
        assert _run(eligible, FakeUpdater()) == check.CheckOutcome("error", "unexpected")

    def test_attempts_are_recorded_and_skips_are_not(self, eligible):
        _unstamp_state = eligible.state
        skipped = _run(eligible, FakeUpdater(enabled=False))
        assert skipped.status == "skipped" and not (_unstamp_state / check.LAST_CHECK_FILE).exists()
        _run(eligible, FakeUpdater(result=_Staged("3.31.0")))  # positive twin
        record = json.loads((_unstamp_state / check.LAST_CHECK_FILE).read_text(encoding="utf8"))
        assert record["status"] == "handed_off" and record["version"] == "3.31.0" and record["trigger"] == "window"
        assert check.last_check_summary(_unstamp_state)["status"] == "handed_off"

    def test_the_record_carries_no_address_or_path(self, eligible):
        _run(eligible, FakeUpdater(result=_Refused("unreachable")))
        text = (eligible.state / check.LAST_CHECK_FILE).read_text(encoding="utf8")
        assert "feed.example.invalid" not in text and str(eligible.state) not in text


class TestAfterScheduledRun:
    @pytest.fixture
    def cli(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
        calls: list[str] = []
        monkeypatch.setattr(
            main_module, "run_pipeline", lambda *a, **k: SimpleNamespace(sftp_attempted=False, sftp_ok=True)
        )
        monkeypatch.setattr(
            check,
            "run_update_check",
            lambda trigger, **k: calls.append(trigger) or check.CheckOutcome("none", "up_to_date"),
        )
        base = ["--sis", "myedbc", "--input", str(tmp_path), "--output", str(tmp_path)]
        return calls, base

    def test_a_scheduled_run_triggers_one_check(self, cli):
        calls, base = cli
        assert main_module.cli([*base, "--source", "scheduled"]) == 0
        assert calls == ["after_run"]

    @pytest.mark.parametrize("source", ["manual", "cli"])
    def test_other_sources_never_trigger_a_check(self, cli, source):
        calls, base = cli
        assert main_module.cli([*base, "--source", source]) == 0
        assert calls == []

    def test_a_dry_run_never_triggers_a_check(self, cli):
        calls, base = cli
        assert main_module.cli([*base, "--source", "scheduled", "--dry-run"]) == 0
        assert calls == []

    def test_the_exit_code_is_identical_whatever_the_check_does(self, cli, monkeypatch):
        calls, base = cli
        for behaviour in (
            lambda *a, **k: check.CheckOutcome("refused", "unreachable"),
            lambda *a, **k: check.CheckOutcome("error", "budget_exceeded"),
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("raises")),
        ):
            monkeypatch.setattr(check, "run_update_check", behaviour)
            assert main_module.cli([*base, "--source", "scheduled"]) == 0

    def test_a_failed_run_still_checks_so_the_fix_can_arrive(self, cli, monkeypatch):
        """A build whose every nightly fails must still be able to take the release that fixes it."""
        calls, base = cli

        def failing(*_a, **_k):
            raise RuntimeError("a GDE file is missing")

        monkeypatch.setattr(main_module, "run_pipeline", failing)
        assert main_module.cli([*base, "--source", "scheduled"]) == 1
        assert calls == ["after_run"]

    def test_a_run_that_exits_early_still_checks(self, cli, monkeypatch):
        calls, base = cli

        def exits(*_a, **_k):
            raise SystemExit(1)

        monkeypatch.setattr(main_module, "run_pipeline", exits)
        assert main_module.cli([*base, "--source", "scheduled"]) == 1
        assert calls == ["after_run"]

    def test_a_failed_manual_run_does_not_check(self, cli, monkeypatch):
        calls, base = cli
        monkeypatch.setattr(main_module, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        assert main_module.cli([*base, "--source", "manual"]) == 1
        assert calls == []

    def test_a_failed_delivery_still_exits_3_with_the_check_made(self, cli, monkeypatch):
        calls, base = cli
        monkeypatch.setattr(
            main_module, "run_pipeline", lambda *a, **k: SimpleNamespace(sftp_attempted=True, sftp_ok=False)
        )
        assert main_module.cli([*base, "--source", "scheduled", "--sftp"]) == 3
        assert calls == ["after_run"]


class TestStatusLine:
    def test_portable_build_says_nothing(self, monkeypatch):
        _unstamped(monkeypatch)
        assert status.update_status_line() is None

    def test_a_standing_condition_is_stated(self, eligible, monkeypatch):
        monkeypatch.setattr(check.paths, "is_machine_scope", lambda: True)
        assert "shares one DistrictSync profile" in status.update_status_line(eligible.state)

    def test_not_yet_available_while_nothing_is_configured(self, eligible):
        assert status.update_status_line(eligible.state) == "Updates are not available yet."

    def test_after_a_check_it_reports_the_last_one(self, eligible, monkeypatch):
        monkeypatch.setattr(status, "skip_reason", lambda **_k: None)
        assert status.update_status_line(eligible.state) == "Updates: not checked yet."
        _run(eligible, FakeUpdater(result=None))
        line = status.update_status_line(eligible.state)
        assert line.startswith("You have the latest version. Last checked ")

    def test_a_failed_check_is_stated_plainly(self, eligible, monkeypatch):
        monkeypatch.setattr(status, "skip_reason", lambda **_k: None)
        _run(eligible, FakeUpdater(result=_Refused("unreachable")))
        assert status.update_status_line(eligible.state).startswith("The last update check could not finish.")


def test_the_status_copy_never_says_account_or_sign_in():
    """Identification-is-never-authentication vocabulary (0038) also applies here: plain words only."""
    copy = " ".join([*status._STANDING.values(), *status._LAST.values()]).lower()
    for word in ("sign in", "log in", "account", "credential", "authorized", "verify"):
        assert word not in copy


def test_the_data_dir_override_helper(monkeypatch):
    from src.utils import paths

    monkeypatch.delenv("DISTRICTSYNC_DATA_DIR", raising=False)
    assert paths.data_dir_override_active() is False
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", "   ")
    assert paths.data_dir_override_active() is False
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(Path.home()))
    assert paths.data_dir_override_active() is True


def test_update_enabled_is_a_chosen_setting_that_defaults_on():
    assert AppConfig().update_enabled is True
    assert AppConfig(update_enabled=False)._carries_chosen_settings()
