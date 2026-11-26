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

    def test_the_app_never_writes_the_policy_key(self):
        """Only Group Policy or an administrator writes it; provisioning (the only registry writer) never names it."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "src"
        for source in root.rglob("*.py"):
            text = source.read_text(encoding="utf8")
            if "POLICY_KEY_PATH" in text and "SetValueEx" in text:
                raise AssertionError(f"{source} writes the policy key")


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
        self.calls: list[str] = []

    def open_manifest(self, raw, keys, now, installed, host, last_sequence, identity):
        self.calls.append("open_manifest")
        return self.ManifestRefused(self.refuse) if self.refuse else _Manifest(())

    def list_feed(self, manifest, installed, bad, channel):
        self.calls.append("list_feed")
        return tuple(SimpleNamespace(version=v) for v in self.offered)

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
        line = status.update_status_line(tmp_path)
        assert line is not None and line.startswith("Version 3.32.0 is available")

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
