"""Tests for SSH host-key pinning in src/sftp/uploader.py (no network).

Covers the three-way ``PinnedHostKeyPolicy`` decision (match / mismatch / unknown),
known_hosts file resolution (user app-data override wins over the bundled
``config/known_hosts``, per-entry merge, corrupt-file skip), the ``_connect`` wiring
(policy installed, ``BadHostKeyException`` re-raised as the canonical reject), and
the client-leak fix (a failed connect/open_sftp closes the SSHClient).

Keys are synthetic paramiko ECDSA keys generated in-process.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from src.sftp.uploader import (
    HostKeyVerificationError,
    PinnedHostKeyPolicy,
    SFTPUploader,
    load_pinned_host_keys,
)

HOST = "sftp.ca.spacesedu.com"
OTHER_HOST = "sftp.app.spacesedu.com"

# The canonical hard-reject copy, pinned byte-for-byte (PII-free: host name only —
# no paths, no credentials, no student data).
EXPECTED_REJECT_MESSAGE = (
    "SFTP host key verification failed for sftp.ca.spacesedu.com: the server's identity "
    "has changed and no longer matches the pinned key in known_hosts. This can indicate "
    "a man-in-the-middle attack, so delivery was aborted. If the server's key was "
    "legitimately rotated, update the known_hosts file (it documents the ssh-keyscan "
    "command) and run again."
)


@pytest.fixture(scope="module")
def key_a() -> paramiko.ECDSAKey:
    return paramiko.ECDSAKey.generate()


@pytest.fixture(scope="module")
def key_b() -> paramiko.ECDSAKey:
    return paramiko.ECDSAKey.generate()


@pytest.fixture(scope="module")
def key_other_type() -> paramiko.ECDSAKey:
    """A key whose type name differs from the nistp256 default (ecdsa-sha2-nistp384)."""
    return paramiko.ECDSAKey.generate(bits=384)


def _pinned(host: str, key: paramiko.PKey) -> paramiko.HostKeys:
    host_keys = paramiko.HostKeys()
    host_keys.add(host, key.get_name(), key)
    return host_keys


def _entry_line(host: str, key: paramiko.PKey) -> str:
    return f"{host} {key.get_name()} {key.get_base64()}\n"


def _uploader() -> SFTPUploader:
    return SFTPUploader(host=HOST, port=22, username="user", remote_path="/upload")


class TestPinnedHostKeyPolicy:
    def test_pinned_and_matching_accepts(self, key_a):
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        # Returning without raising = paramiko proceeds with the connection.
        assert policy.missing_host_key(MagicMock(), HOST, key_a) is None

    def test_pinned_but_mismatching_rejects(self, key_a, key_b):
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        with pytest.raises(HostKeyVerificationError) as excinfo:
            policy.missing_host_key(MagicMock(), HOST, key_b)
        assert str(excinfo.value) == EXPECTED_REJECT_MESSAGE

    def test_pinned_different_keytype_rejects(self, key_a, key_other_type):
        """A host with ANY pinned key must present a matching one — an unpinned
        key TYPE is a mismatch (an impostor could otherwise force an unpinned type)."""
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        with pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_other_type)

    def test_mismatch_is_logged_as_error(self, key_a, key_b, caplog):
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        with caplog.at_level(logging.ERROR, logger="src.sftp.uploader"), pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_b)
        assert EXPECTED_REJECT_MESSAGE in caplog.text

    def test_unknown_host_accepts_with_warning(self, key_a, caplog):
        """No pinned key for the host → pre-pinning behavior (accept) + a WARNING
        naming the host and pointing at config/known_hosts."""
        policy = PinnedHostKeyPolicy(paramiko.HostKeys())
        with caplog.at_level(logging.WARNING, logger="src.sftp.uploader"):
            assert policy.missing_host_key(MagicMock(), HOST, key_a) is None
        assert f"No pinned SSH host key for {HOST}" in caplog.text
        assert "config/known_hosts" in caplog.text

    def test_other_pinned_host_does_not_pin_this_one(self, key_a, key_b):
        """Pins are per-host: pinning OTHER_HOST leaves HOST on the warn+accept path."""
        policy = PinnedHostKeyPolicy(_pinned(OTHER_HOST, key_a))
        assert policy.missing_host_key(MagicMock(), HOST, key_b) is None

    def test_nonstandard_port_with_plain_pin_warns_distinctly(self, key_a, caplog):
        """A non-22 port looks up as "[host]:port" — a plain-host pin does NOT cover it.
        The owner must hear that their pin was bypassed, not the generic unpinned copy."""
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        bracketed = f"[{HOST}]:2222"
        with caplog.at_level(logging.WARNING, logger="src.sftp.uploader"):
            assert policy.missing_host_key(MagicMock(), bracketed, key_a) is None
        assert "non-standard port" in caplog.text
        assert "does NOT cover" in caplog.text
        assert "ssh-keyscan -p PORT" in caplog.text

    def test_nonstandard_port_without_any_pin_keeps_the_generic_warning(self, key_a, caplog):
        policy = PinnedHostKeyPolicy(paramiko.HostKeys())
        with caplog.at_level(logging.WARNING, logger="src.sftp.uploader"):
            assert policy.missing_host_key(MagicMock(), f"[{HOST}]:2222", key_a) is None
        assert "No pinned SSH host key" in caplog.text

    def test_reject_message_never_retried_type(self):
        """The reject is deliberately NOT an SSHException subclass (the retry loop
        treats SSHException as transient — a changed identity must never be retried)."""
        assert not issubclass(HostKeyVerificationError, paramiko.SSHException)
        assert issubclass(HostKeyVerificationError, RuntimeError)


class TestKnownHostsResolution:
    @pytest.fixture
    def hosts_files(self, tmp_path, monkeypatch):
        """Redirect both known_hosts seams to tmp files (returned as (user, bundled))."""
        user_file = tmp_path / "user_known_hosts"
        bundle_file = tmp_path / "bundle_known_hosts"
        monkeypatch.setattr("src.sftp.uploader.user_known_hosts_file", lambda: user_file)
        monkeypatch.setattr("src.sftp.uploader.bundle_known_hosts_file", lambda: bundle_file)
        return user_file, bundle_file

    def test_no_files_yields_empty_pins(self, hosts_files):
        assert len(load_pinned_host_keys()) == 0

    def test_bundled_file_used_when_no_user_override(self, hosts_files, key_a):
        _, bundle_file = hosts_files
        bundle_file.write_text(_entry_line(HOST, key_a), encoding="utf-8")
        pinned = load_pinned_host_keys()
        assert pinned.check(HOST, key_a)

    def test_user_override_wins_over_bundled(self, hosts_files, key_a, key_b):
        """Same host + key type in both files → the user app-data entry decides."""
        user_file, bundle_file = hosts_files
        user_file.write_text(_entry_line(HOST, key_a), encoding="utf-8")
        bundle_file.write_text(_entry_line(HOST, key_b), encoding="utf-8")
        pinned = load_pinned_host_keys()
        assert pinned.check(HOST, key_a)
        assert not pinned.check(HOST, key_b)

    def test_files_merge_per_entry(self, hosts_files, key_a, key_b):
        """A user file pinning ONE host leaves the bundled pins for other hosts in force."""
        user_file, bundle_file = hosts_files
        user_file.write_text(_entry_line(OTHER_HOST, key_b), encoding="utf-8")
        bundle_file.write_text(_entry_line(HOST, key_a), encoding="utf-8")
        pinned = load_pinned_host_keys()
        assert pinned.check(HOST, key_a)
        assert pinned.check(OTHER_HOST, key_b)

    def test_corrupt_user_file_logged_and_skipped(self, hosts_files, key_a, caplog):
        """A corrupt override file must not brick delivery — logged ERROR, bundled
        pins stay in force."""
        user_file, bundle_file = hosts_files
        user_file.write_text(f"{HOST} ssh-ed25519 %%%not-base64%%%\n", encoding="utf-8")
        bundle_file.write_text(_entry_line(HOST, key_a), encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="src.sftp.uploader"):
            pinned = load_pinned_host_keys()
        assert "unreadable pinned-host-key file" in caplog.text
        assert pinned.check(HOST, key_a)

    def test_shipped_file_pins_every_allowed_host(self):
        """The bundled config/known_hosts ships POPULATED (keys scanned 2026-07-20):
        every host in the SFTP allowlist must carry at least one pinned key, so a
        fresh download verifies server identity with zero user action. The rotation
        instructions (ssh-keyscan command) must survive as comments."""
        from src.utils.paths import bundle_known_hosts_file
        from src.utils.validators import ALLOWED_SFTP_HOSTS

        shipped = bundle_known_hosts_file()
        assert shipped.is_file()
        host_keys = paramiko.HostKeys()
        host_keys.load(str(shipped))
        pinned_hosts = set(host_keys.keys())
        for host in ALLOWED_SFTP_HOSTS:
            assert host in pinned_hosts, f"allowlisted host {host} has no pinned key"
        assert "ssh-keyscan -t ed25519,ecdsa,rsa" in shipped.read_text(encoding="utf-8")


class TestConnectHostKeyWiring:
    def test_pinned_policy_installed_not_autoadd(self):
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            uploader._connect()
        mock_client = mock_cls.return_value
        mock_client.set_missing_host_key_policy.assert_called_once()
        (policy,) = mock_client.set_missing_host_key_policy.call_args.args
        assert isinstance(policy, PinnedHostKeyPolicy)

    def test_system_known_hosts_mismatch_wrapped_as_canonical_reject(self, key_a, key_b):
        """paramiko raises BadHostKeyException itself for a system-known_hosts mismatch
        (before the policy runs) — _connect re-raises it as the same canonical reject."""
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            mock_client = mock_cls.return_value
            mock_client.connect.side_effect = paramiko.BadHostKeyException(HOST, key_b, key_a)
            with pytest.raises(HostKeyVerificationError) as excinfo:
                uploader._connect()
        assert str(excinfo.value) == EXPECTED_REJECT_MESSAGE
        mock_client.close.assert_called_once()


class TestConnectClientLeak:
    """A _connect that raises must close the SSHClient it constructed — callers'
    ``finally: client.close()`` can never reach a client they were never handed."""

    def test_connect_failure_closes_client(self):
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            mock_client = mock_cls.return_value
            mock_client.connect.side_effect = OSError("connection refused")
            with pytest.raises(OSError, match="connection refused"):
                uploader._connect()
        mock_client.close.assert_called_once()

    def test_open_sftp_failure_closes_client(self):
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            mock_client = mock_cls.return_value
            mock_client.open_sftp.side_effect = paramiko.SSHException("no session")
            with pytest.raises(paramiko.SSHException, match="no session"):
                uploader._connect()
        mock_client.close.assert_called_once()

    def test_success_leaves_client_open(self):
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            client, sftp = uploader._connect()
        assert client is mock_cls.return_value
        assert sftp is mock_cls.return_value.open_sftp.return_value
        client.close.assert_not_called()
