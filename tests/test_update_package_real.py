"""DSYNC-62 (plan 0054 S3): the REAL private shared package, as DistrictSync wires it.

The whole module skips when the package is not installed (a fork or Dependabot pull request never receives the read
key). ``tests/test_update_package_wiring.py::test_the_package_is_present_when_it_is_required`` is the positive twin:
on a push to main it fails if the package is absent, so this module's skip can never pass for a proof.
"""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime, timedelta

import pytest

from src.update import package
from tests.test_updater_package import up

mdu = pytest.importorskip("myb_desktop_updater")

_NOW = datetime(2026, 11, 17, tzinfo=UTC)
_PRIVATE = bytes.fromhex("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721")  # RFC 6979 test key


def _manifest(app: str) -> bytes:
    document = {
        "schema": 1,
        "app": app,
        "channel": "stable",
        "sequence": 1,
        "expires": (_NOW + timedelta(days=90)).isoformat(),
        "feed_host": "feed.example.invalid",
        "releases": [],
    }
    return mdu.seal_document(document, _PRIVATE, "k1")


def _public() -> bytes:
    from myb_desktop_updater import p256

    return p256.public_key(_PRIVATE)


class TestRealPackage:
    def test_the_installed_package_is_the_pinned_version(self):
        assert importlib.metadata.version("myb-desktop-updater") == up.PINNED_VERSION

    def test_identity_is_valid_for_the_package(self):
        identity = package.app_identity(mdu)
        assert identity.name == "DistrictSync"
        assert identity.state_env == "DISTRICTSYNC_STATE_DIR"
        assert identity.updates_env == "DISTRICTSYNC_UPDATES"

    def test_a_manifest_signed_for_districtsync_is_accepted(self):
        identity = package.app_identity(mdu)
        opened = mdu.open_manifest(
            _manifest("DistrictSync"), {"k1": _public()}, _NOW, "0.0.1", "feed.example.invalid", 0, identity
        )
        assert isinstance(opened, mdu.Manifest)

    def test_a_manifest_signed_for_another_app_is_refused(self):
        identity = package.app_identity(mdu)
        opened = mdu.open_manifest(
            _manifest("RosterGate"), {"k1": _public()}, _NOW, "0.0.1", "feed.example.invalid", 0, identity
        )
        assert isinstance(opened, mdu.ManifestRefused) and opened.reason == "wrong_app"

    def test_an_unknown_key_is_refused(self):
        identity = package.app_identity(mdu)
        opened = mdu.open_manifest(
            _manifest("DistrictSync"), {"other": _public()}, _NOW, "0.0.1", "feed.example.invalid", 0, identity
        )
        assert isinstance(opened, mdu.ManifestRefused) and opened.reason == "unknown_key"

    def test_the_off_switch_follows_the_environment_variable(self, monkeypatch, tmp_path):
        identity = package.app_identity(mdu)
        monkeypatch.delenv("DISTRICTSYNC_UPDATES", raising=False)
        assert mdu.updates_enabled(identity, tmp_path) is True
        monkeypatch.setenv("DISTRICTSYNC_UPDATES", "off")
        assert mdu.updates_enabled(identity, tmp_path) is False
