"""Plan 0055 U1a (DSYNC-97): the update mode, its sources, and what each mode lets the check do.

Owner decision O1 (2026-11-23): the default is ``notify`` (the signed manifest only, nothing downloaded); ``off`` makes no
connection at all; district IT can enforce a mode with an HKLM Group Policy key the app cannot override.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.config.app_config import AppConfig
from src.update import check, policy, status
from src.update.feed import FeedTarget
from src.utils.paths import MACHINE_SCOPE_KEY_PATH


def _registry(monkeypatch, values: dict[tuple[str, str], object]):
    def read(key_path: str, name: str):
        value = values.get((key_path, name))
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(policy, "_read_hklm_string", read)


POLICY = (policy.POLICY_KEY_PATH, policy.POLICY_VALUE_NAME)
MACHINE = (MACHINE_SCOPE_KEY_PATH, policy.MACHINE_SETTING_VALUE_NAME)


class TestThePrecedence:
    def test_nothing_set_is_notify_by_default(self, monkeypatch):
        _registry(monkeypatch, {})
        assert policy.effective_update_mode(setting_enabled=True) == policy.EffectiveMode("notify", "default")

    def test_the_machine_setting_is_used_when_there_is_no_policy(self, monkeypatch):
        _registry(monkeypatch, {MACHINE: "on"})
        assert policy.effective_update_mode(setting_enabled=True) == policy.EffectiveMode("on", "machine")

    @pytest.mark.parametrize("machine", ["on", "off", None])
    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_policy_wins_over_everything(self, monkeypatch, machine, enabled):
        _registry(monkeypatch, {POLICY: "off", MACHINE: machine})
        assert policy.effective_update_mode(setting_enabled=enabled) == policy.EffectiveMode("off", "policy")
        _registry(monkeypatch, {POLICY: "on", MACHINE: machine})
        assert policy.effective_update_mode(setting_enabled=enabled) == policy.EffectiveMode("on", "policy")

    def test_the_per_user_switch_turns_a_copy_off(self, monkeypatch):
        _registry(monkeypatch, {})
        assert policy.effective_update_mode(setting_enabled=False) == policy.EffectiveMode("off", "setting")

    @pytest.mark.parametrize("bad", ["auto", "", "1", "on off"])
    def test_a_malformed_value_is_off_never_on(self, monkeypatch, bad):
        _registry(monkeypatch, {POLICY: bad})
        assert policy.effective_update_mode(setting_enabled=True) == policy.EffectiveMode("off", "policy")
        _registry(monkeypatch, {MACHINE: bad})
        assert policy.effective_update_mode(setting_enabled=True) == policy.EffectiveMode("off", "machine")

    def test_an_unreadable_policy_is_off(self, monkeypatch):
        _registry(monkeypatch, {POLICY: OSError("access denied"), MACHINE: "on"})
        assert policy.effective_update_mode(setting_enabled=True) == policy.EffectiveMode("off", "policy")

    @staticmethod
    def _policy_writers(root) -> list[str]:
        """Every file under ``root`` that names the policy key, other than the one module allowed to (the reader)."""
        hits = []
        for source in root.rglob("*.py"):
            if source.name == "policy.py" and source.parent.name == "update":
                continue
            text = source.read_text(encoding="utf8").replace("\\\\", "\\")
            if "Policies\\myBlueprint" in text or "POLICY_KEY_PATH" in text:
                hits.append(source.name)
        return hits

    def test_the_app_never_names_the_policy_key_outside_its_reader(self):
        """Only Group Policy or an administrator writes it: nothing in src/ but the reader may even name it."""
        from pathlib import Path

        assert self._policy_writers(Path(__file__).resolve().parent.parent / "src") == []

    def test_positive_twin_the_scan_finds_a_planted_writer(self, tmp_path):
        (tmp_path / "rogue.py").write_text('KEY = r"SOFTWARE\\Policies\\myBlueprint\\DistrictSync"\n', encoding="utf8")
        assert self._policy_writers(tmp_path) == ["rogue.py"]


@dataclass
class _Manifest:
    releases: tuple


class _FakeModule:
    """The package's public surface the notify path uses, recording what was called."""

    MANIFEST_MAX_BYTES = 1024 * 1024

    class UpdateRefused(Exception):  # noqa: N818 - the package's name
        def __init__(self, reason: str) -> None:
            super().__init__(reason)
            self.reason = reason

    class ManifestRefused:
        def __init__(self, reason: str) -> None:
            self.reason = reason

    def __init__(self, offered: tuple[str, ...] = (), refuse: str | None = None) -> None:
        self.offered, self.refuse = offered, refuse
        self.empty: tuple = ()  # releases listed with no files
        self.calls: list[str] = []

    def open_manifest(self, raw, keys, now, installed, host, last_sequence, identity):
        self.calls.append("open_manifest")
        return self.ManifestRefused(self.refuse) if self.refuse else _Manifest(())

    def list_feed(self, manifest, installed, bad, channel):
        self.calls.append("list_feed")
        return tuple(SimpleNamespace(version=v, files=("pkg",)) for v in self.offered) + self.empty

    def check_for_update(self, *args, **kwargs):
        self.calls.append("check_for_update")  # the DOWNLOAD path: notify must never reach it
        return None


class _Fetcher:
    def __init__(self) -> None:
        self.fetched: list[str] = []
        self.deadline = None

    def fetch(self, location, max_bytes=None):
        self.fetched.append(location)
        return b"{}"


_TARGET = FeedTarget(
    host="feed.example.test",
    manifest_path="/manifest.json",
    trusted_keys={},
    channel="stable",
    allow_loopback_http=False,
)


class TestNotifyDownloadsNothing:
    def _notify(self, module: _FakeModule) -> tuple[check.CheckOutcome, _Fetcher]:
        fetcher = _Fetcher()
        health = SimpleNamespace(last_sequence=lambda: 0, bad_versions=lambda: ())
        outcome = check._notify_only(module, fetcher, _TARGET, object(), health, datetime.now(UTC), 60.0)
        return outcome, fetcher

    def test_a_newer_version_is_reported_and_nothing_is_downloaded(self, monkeypatch):
        monkeypatch.setattr(check, "app_version", lambda: "3.30.0")
        module = _FakeModule(offered=("3.32.0", "3.31.0"))
        outcome, fetcher = self._notify(module)
        assert outcome == check.CheckOutcome("available", "newer_available", "3.32.0")
        assert fetcher.fetched == ["/manifest.json"]  # the manifest, and only the manifest
        assert "check_for_update" not in module.calls
        assert module.calls == ["open_manifest", "list_feed"]

    def test_up_to_date(self, monkeypatch):
        monkeypatch.setattr(check, "app_version", lambda: "3.30.0")
        outcome, _ = self._notify(_FakeModule(offered=()))
        assert outcome == check.CheckOutcome("none", "up_to_date")

    def test_a_refused_manifest_is_a_result(self, monkeypatch):
        monkeypatch.setattr(check, "app_version", lambda: "3.30.0")
        outcome, _ = self._notify(_FakeModule(refuse="expired"))
        assert outcome == check.CheckOutcome("refused", "expired")


class TestTheModeDecidesTheCheck:
    def test_off_by_policy_opens_no_connection(self, eligible, monkeypatch):
        _registry(monkeypatch, {POLICY: "off"})
        assert check.skip_reason(config=eligible.config) == "off_by_policy"

    def test_off_in_the_per_user_switch(self, eligible, monkeypatch):
        _registry(monkeypatch, {})
        assert check.skip_reason(config=AppConfig(update_enabled=False)) == "off_in_settings"

    def test_positive_twin_notify_and_on_are_not_skipped_for_the_mode(self, eligible, monkeypatch):
        for mode in ("notify", "on"):
            _registry(monkeypatch, {POLICY: mode})
            assert check.skip_reason(config=eligible.config) not in ("off_by_policy", "off_in_settings")


class TestTheStatusLine:
    def test_notify_says_which_version_is_available(self, monkeypatch, tmp_path):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "skip_reason", lambda **kw: None)
        monkeypatch.setattr(
            status,
            "last_check_summary",
            lambda state_dir=None: {"status": "available", "version": "3.32.0", "at": "2026-11-24T03:00:00+00:00"},
        )
        monkeypatch.setattr(status, "app_version", lambda: "3.30.0")
        line = status.update_status_line(tmp_path)
        assert line is not None and line.startswith("Version 3.32.0 is available")

    def test_after_an_upgrade_the_stale_record_is_not_announced(self, monkeypatch, tmp_path):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "skip_reason", lambda **kw: None)
        monkeypatch.setattr(
            status, "last_check_summary", lambda state_dir=None: {"status": "available", "version": "3.32.0"}
        )
        monkeypatch.setattr(status, "app_version", lambda: "3.32.0")
        line = status.update_status_line(tmp_path)
        assert line is not None and "is available" not in line

    def test_off_by_policy_has_its_own_sentence(self):
        assert "policy" in status._STANDING["off_by_policy"]


class TestTheWholeCheckFollowsTheMode:
    def test_notify_takes_the_manifest_only_path_and_never_the_download(self, eligible, monkeypatch):
        from tests.test_update_check import FakeUpdater, _run

        _registry(monkeypatch, {POLICY: "notify"})
        routed: list[str] = []
        monkeypatch.setattr(
            check, "_notify_only", lambda *a, **k: routed.append("notify") or check.CheckOutcome("none", "up_to_date")
        )
        fake = FakeUpdater()
        assert _run(eligible, fake) == check.CheckOutcome("none", "up_to_date")
        assert routed == ["notify"]
        assert fake.checks == []  # the package's download-and-stage check was never called

    def test_positive_twin_on_takes_the_full_check(self, eligible, monkeypatch):
        from tests.test_update_check import FakeUpdater, _run

        _registry(monkeypatch, {POLICY: "on"})
        monkeypatch.setattr(check, "_notify_only", lambda *a, **k: pytest.fail("on must not take the notify path"))
        fake = FakeUpdater()
        _run(eligible, fake)
        assert len(fake.checks) == 1


class TestNotifyOffersOnlyWhatOnWouldInstall:
    def test_a_release_with_no_files_is_not_announced(self, monkeypatch):
        monkeypatch.setattr(check, "app_version", lambda: "3.30.0")
        module = _FakeModule(offered=())
        module.empty = (SimpleNamespace(version="3.40.0", files=()),)
        fetcher = _Fetcher()
        health = SimpleNamespace(last_sequence=lambda: 0, bad_versions=lambda: ())
        outcome = check._notify_only(module, fetcher, _TARGET, object(), health, datetime.now(UTC), 60.0)
        assert outcome == check.CheckOutcome("none", "up_to_date")


class TestTheDiagnoseLine:
    def test_it_names_the_mode_its_source_and_why_no_check_runs(self, monkeypatch):
        from src.utils import diagnostics

        _registry(monkeypatch, {MACHINE: "on"})
        monkeypatch.setattr(check, "skip_reason", lambda **kw: "machine_wide")
        assert diagnostics._update_mode() == "on (machine); no update check runs here (machine_wide)"

    def test_positive_twin_no_suffix_when_a_check_runs(self, monkeypatch):
        from src.utils import diagnostics

        _registry(monkeypatch, {})
        monkeypatch.setattr(check, "skip_reason", lambda **kw: None)
        assert diagnostics._update_mode() == "notify (default)"


class TestTheVersionCompare:
    @pytest.mark.parametrize(
        ("candidate", "running", "newer"),
        [
            ("3.32.0", "3.31.9", True),
            ("3.32", "3.32.0", False),  # padded: equal, not newer
            ("3.32.0", "3.32", False),
            ("3.32.0", "3.32.0-rc1", False),  # the suffix is ignored: same release
            ("3.32.1", "3.32.0rc1", True),
            ("3.32.0", "dev", True),  # an unreadable running version cannot be compared: say it
            ("garbage", "3.30.0", False),  # an unreadable candidate is never announced
        ],
    )
    def test_it(self, candidate, running, newer):
        assert status._newer(candidate, running) is newer

    def test_an_off_line_carries_no_second_reason(self, monkeypatch):
        from src.utils import diagnostics

        _registry(monkeypatch, {POLICY: "off"})
        monkeypatch.setattr(check, "skip_reason", lambda **kw: "machine_wide")
        assert diagnostics._update_mode() == "off (policy)"
