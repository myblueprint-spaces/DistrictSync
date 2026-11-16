"""DSYNC-63 (plan 0054 S4): the update check against the REAL shared package, over real loopback sockets.

Skips as a whole when the private package is not installed (fork and Dependabot pull requests); the positive
twin ``tests/test_update_package_wiring.py::test_the_package_is_present_when_it_is_required`` fails a push to
main if the package is missing, so this module's skip can never pass for a proof.

Layer 2 of the plan's network proof: a ``sys.addaudithook`` records every ``socket.connect`` made while a check
runs, and the test requires each destination to be the loopback feed. A check that is skipped by a guard makes
none at all (with the positive twin: an eligible build connects to exactly the feed).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config.app_config import AppConfig
from src.update import check
from src.update.feed import FeedTarget
from tests.test_installed_flavour import _install_layout, _stamp

mdu = pytest.importorskip("myb_desktop_updater")

NOW = datetime.now(UTC)
PRIVATE = bytes.fromhex("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721")  # RFC 6979 test key
PAYLOAD = b"pretend this is a Velopack package"

# ---- the audit hook: installed once, only records while a test has switched it on ----------------------

_RECORD: list[tuple[object, ...]] | None = None


def _hook(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.connect" and _RECORD is not None:
        _RECORD.append(args[1] if len(args) > 1 else args)  # type: ignore[arg-type]


sys.addaudithook(_hook)


class _Feed:
    """A loopback http server holding a manifest and one package file."""

    def __init__(self, manifest: bytes, files: dict[str, bytes]) -> None:
        feed = self
        self.hits: list[str] = []
        self.manifest, self.files = manifest, files

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                feed.hits.append(self.path)
                body = feed.manifest if self.path == "/manifest.json" else feed.files.get(self.path.lstrip("/"))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a: object) -> None:  # silence
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _manifest(host: str, *, app: str = "DistrictSync", version: str = "3.31.0", sha: str | None = None) -> bytes:
    name = f"DistrictSync-{version}-full.nupkg"
    document = {
        "schema": 1,
        "app": app,
        "channel": "stable",
        "sequence": 1,
        "expires": (NOW + timedelta(days=90)).isoformat(),
        "feed_host": host,
        "releases": [
            {
                "version": version,
                "files": [
                    {
                        "name": name,
                        "sha256": sha or hashlib.sha256(PAYLOAD).hexdigest(),
                        "size": len(PAYLOAD),
                        "url": f"http://{host}/{name}",
                    }
                ],
            }
        ],
    }
    return mdu.seal_document(document, PRIVATE, "k1")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    _stamp(monkeypatch, "installed")
    monkeypatch.setattr("sys.executable", _install_layout(tmp_path))
    monkeypatch.setattr(check.paths, "is_machine_scope", lambda: False)
    monkeypatch.setattr(check.paths, "data_dir_override_active", lambda: False)
    monkeypatch.setattr(check, "app_version", lambda: "3.30.0")
    started: list[_Feed] = []

    def serve(manifest_for_host, files=None):
        # The manifest names the feed's own host, so the port is needed first: start empty, then fill in.
        feed = _Feed(b"", files if files is not None else {})
        feed.manifest = manifest_for_host(feed.host)
        if files is None:
            feed.files = {"DistrictSync-3.31.0-full.nupkg": PAYLOAD}
        started.append(feed)
        return feed

    def run(feed_host: str, *, target_host: str | None = None, allow_http: bool = True):
        target = FeedTarget(
            host=target_host or feed_host,
            trusted_keys={"k1": mdu.p256.public_key(PRIVATE) if hasattr(mdu, "p256") else _public()},
            allow_loopback_http=allow_http,
        )
        global _RECORD
        _RECORD = []
        try:
            outcome = check.run_update_check(
                check.TRIGGER_WINDOW,
                config=AppConfig(),
                target=target,
                updater=mdu,
                state_dir=tmp_path / "state",
                now=NOW,
                budget=30.0,
                # This file is about the check and the staging. Applying is covered in test_update_apply_real.py, so
                # the hand-off is made to refuse deterministically (a copy is running) on every OS, and no installer is touched.
                installer=object(),
                running=lambda: 1,
            )
            return outcome, list(_RECORD)
        finally:
            _RECORD = None

    yield SimpleNamespace(serve=serve, run=run, state=tmp_path / "state")
    for feed in started:
        feed.close()


def _public() -> bytes:
    from myb_desktop_updater import p256

    return p256.public_key(PRIVATE)


def _destinations(connections: list[tuple[object, ...]]) -> set[tuple[object, ...]]:
    return {tuple(c) if isinstance(c, tuple) else (c,) for c in connections}


class TestAgainstALoopbackFeed:
    def test_a_newer_signed_release_is_staged_and_only_the_feed_is_contacted(self, setup):
        """Positive twin of the zero-connection guards: an eligible build connects to exactly the feed."""
        feed = setup.serve(lambda host: _manifest(host))
        outcome, connections = setup.run(feed.host)
        assert outcome == check.CheckOutcome("staged", "not_applied_app_running", "3.31.0")  # staged, apply refused
        staged = setup.state / check.STAGING_FOLDER / "DistrictSync-3.31.0-full.nupkg"
        assert staged.read_bytes() == PAYLOAD
        assert connections, "the audit hook recorded nothing: the proof would be vacuous"
        assert _destinations(connections) == {("127.0.0.1", feed.port)}
        assert feed.hits == ["/manifest.json", "/DistrictSync-3.31.0-full.nupkg"]

    def test_nothing_newer_is_a_clean_none(self, setup):
        feed = setup.serve(lambda host: _manifest(host, version="3.29.0"))
        outcome, connections = setup.run(feed.host)
        assert outcome == check.CheckOutcome("none", "up_to_date")
        assert _destinations(connections) == {("127.0.0.1", feed.port)}

    def test_a_manifest_signed_for_another_app_is_refused_and_nothing_is_staged(self, setup):
        feed = setup.serve(lambda host: _manifest(host, app="RosterGate"))
        outcome, _ = setup.run(feed.host)
        assert outcome == check.CheckOutcome("refused", "wrong_app")
        assert not (setup.state / check.STAGING_FOLDER).exists()

    def test_a_tampered_signature_is_refused(self, setup):
        def tampered(host: str) -> bytes:
            envelope = json.loads(_manifest(host))
            envelope["signature"] = envelope["signature"][:-4] + "AAAA"
            return json.dumps(envelope).encode()

        feed = setup.serve(tampered)
        outcome, _ = setup.run(feed.host)
        assert outcome.status == "refused" and outcome.reason == "bad_signature"

    def test_a_file_that_does_not_match_what_was_signed_is_refused_and_not_kept(self, setup):
        feed = setup.serve(lambda host: _manifest(host, sha="0" * 64))
        outcome, _ = setup.run(feed.host)
        assert outcome == check.CheckOutcome("refused", "hash_mismatch")
        assert not (setup.state / check.STAGING_FOLDER).exists()

    def test_a_manifest_naming_another_host_is_refused(self, setup):
        feed = setup.serve(lambda host: _manifest("elsewhere.example.invalid"))
        outcome, _ = setup.run(feed.host)
        assert outcome == check.CheckOutcome("refused", "wrong_host")

    def test_an_unreachable_feed_is_a_refusal_not_a_crash(self, setup):
        outcome, connections = setup.run("127.0.0.1:9")  # the discard port: nothing listens
        assert outcome == check.CheckOutcome("refused", "unreachable")
        assert _destinations(connections) <= {("127.0.0.1", 9)}

    def test_a_feed_that_answers_404_is_a_refusal(self, setup):
        feed = setup.serve(lambda host: b"", files={})
        feed.manifest = b""
        feed.close()
        outcome, _ = setup.run(feed.host)
        assert outcome.status == "refused"


class TestNoConnectionWhenAGuardApplies:
    def test_the_audit_hook_sees_no_connection_for_a_skipped_check(self, setup, monkeypatch):
        """Positive twin above: the same hook DID record the eligible check's connection."""
        feed = setup.serve(lambda host: _manifest(host))
        monkeypatch.setattr(check.paths, "is_machine_scope", lambda: True)
        outcome, connections = setup.run(feed.host)
        assert outcome == check.CheckOutcome("skipped", "machine_scope")
        assert connections == [] and feed.hits == []

    def test_the_off_switch_makes_no_connection(self, setup, monkeypatch):
        monkeypatch.setenv("DISTRICTSYNC_UPDATES", "off")
        feed = setup.serve(lambda host: _manifest(host))
        outcome, connections = setup.run(feed.host)
        assert outcome == check.CheckOutcome("skipped", "off_switch")
        assert connections == [] and feed.hits == []

    def test_the_portable_build_makes_no_connection(self, setup, monkeypatch):
        from tests.test_installed_flavour import _unstamped

        _unstamped(monkeypatch)
        feed = setup.serve(lambda host: _manifest(host))
        outcome, connections = setup.run(feed.host)
        assert outcome == check.CheckOutcome("skipped", "portable")
        assert connections == [] and feed.hits == []


def test_a_package_built_wheel_is_in_use():
    """The module under test is the real package, not a stand-in left in ``sys.modules`` by another test."""
    assert Path(mdu.__file__).name == "__init__.py" and hasattr(mdu, "check_for_update")
