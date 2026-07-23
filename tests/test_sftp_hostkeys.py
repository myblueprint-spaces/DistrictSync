"""Tests for SSH host-key pinning in src/sftp/uploader.py (no network).

Covers the FAIL-CLOSED ``PinnedHostKeyPolicy`` decision (match accepts; mismatch,
missing pin, and unpinned-port all refuse), known_hosts file resolution (user app-data
override wins over the bundled ``config/known_hosts``, per-entry merge, corrupt-file
skip), the ``_connect`` wiring (policy installed, NO host-key file loaded into the
client, ``BadHostKeyException`` re-raised as the canonical reject), and the client-leak
fix (a failed connect/open_sftp closes the SSHClient).

W1-A closed two verified bypasses of the v3.7.0 pinning, and the tests that pinned the
old permissive behavior were inverted rather than dropped:
  (a) ``load_system_host_keys()`` ran BEFORE the policy, and paramiko consults those
      keys first — so a user-writable ``~/.ssh/known_hosts`` entry overrode the bundled
      pin. ``TestConnectDoesNotConsultSystemKnownHosts`` locks the fix.
  (b) an unpinned host was accepted with a warning, so a missing ``--add-data config``
      asset or one corrupt byte silently downgraded every connection to
      trust-on-first-use. ``TestFailClosedOnMissingPin`` locks the fix.

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
    _host_key_unpinned_message,
    _is_transient_sftp_error,
    load_pinned_host_keys,
)

HOST = "sftp.ca.spacesedu.com"
OTHER_HOST = "sftp.app.spacesedu.com"
BRACKETED_HOST = f"[{HOST}]:2222"

# The canonical hard-reject copy, pinned byte-for-byte (PII-free: host name only —
# no paths, no credentials, no student data).
EXPECTED_REJECT_MESSAGE = (
    "SFTP host key verification failed for sftp.ca.spacesedu.com: the server's identity "
    "has changed and no longer matches the pinned key in known_hosts. This can indicate "
    "a man-in-the-middle attack, so delivery was aborted. If the server's key was "
    "legitimately rotated, update the known_hosts file (it documents the ssh-keyscan "
    "command) and run again."
)

# The canonical FAIL-CLOSED copy for a host with no usable pin (W1-A). Distinct from the
# mismatch copy above: this is a broken/incomplete install, NOT a changed server identity.
EXPECTED_UNPINNED_MESSAGE = (
    "SFTP host key verification failed for sftp.ca.spacesedu.com: no pinned key is "
    "available for this server, so its identity could not be verified and delivery was "
    "aborted. The pinned known_hosts file is missing or unreadable — reinstall "
    "DistrictSync, or place a known_hosts file pinning this server in the DistrictSync "
    "app-data folder (config/known_hosts documents the ssh-keyscan command)."
)

# The port variant: a plain-hostname pin does NOT cover "[host]:port".
EXPECTED_PORT_UNPINNED_MESSAGE = (
    "SFTP host key verification failed for [sftp.ca.spacesedu.com]:2222: a pinned key "
    "exists for sftp.ca.spacesedu.com, but this connection uses a non-standard port, "
    "which the plain-hostname pin does NOT cover — so the server's identity could not be "
    "verified and delivery was aborted. Add a bracketed '[sftp.ca.spacesedu.com]:2222' "
    "entry to known_hosts (scan with ssh-keyscan -p PORT) to pin it."
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


@pytest.fixture
def hosts_files(tmp_path, monkeypatch):
    """Redirect both known_hosts seams to tmp files (returned as (user, bundled))."""
    user_file = tmp_path / "user_known_hosts"
    bundle_file = tmp_path / "bundle_known_hosts"
    monkeypatch.setattr("src.sftp.uploader.user_known_hosts_file", lambda: user_file)
    monkeypatch.setattr("src.sftp.uploader.bundle_known_hosts_file", lambda: bundle_file)
    return user_file, bundle_file


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

    # The four former warn-and-ACCEPT cases (unknown host / another host's pin / the two
    # non-22-port shapes) moved to TestFailClosedOnMissingPin below, inverted: each now
    # pins a REFUSAL. They were the W1-A trust-on-first-use bypass, so the scenarios are
    # kept 1:1 and only their verdict changed.

    def test_matching_pin_is_the_only_accepting_branch(self, key_a, key_b):
        """The whole policy in one assertion: accept iff the offered key IS a pin."""
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        assert policy.missing_host_key(MagicMock(), HOST, key_a) is None
        for host, key in ((HOST, key_b), (OTHER_HOST, key_a), (f"[{HOST}]:2222", key_a)):
            with pytest.raises(HostKeyVerificationError):
                policy.missing_host_key(MagicMock(), host, key)

    def test_reject_message_never_retried_type(self):
        """The reject is deliberately NOT an SSHException subclass (the retry loop
        treats SSHException as transient — a changed identity must never be retried)."""
        assert not issubclass(HostKeyVerificationError, paramiko.SSHException)
        assert issubclass(HostKeyVerificationError, RuntimeError)


class TestKnownHostsResolution:
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


class TestFailClosedOnMissingPin:
    """W1-A: an allowlisted host with NO usable pin must be REFUSED, never trust-on-first-use.

    The bundled ``config/known_hosts`` pins every host in ``ALLOWED_SFTP_HOSTS``, so the only
    ways to reach the unpinned branch in production are a broken install (the PyInstaller
    ``--add-data config`` asset missing) or an unreadable/corrupt pin file. Both used to
    degrade EVERY connection to warn-and-accept — the whole pin silently off.
    """

    def test_unpinned_host_fails_closed(self, key_a):
        policy = PinnedHostKeyPolicy(paramiko.HostKeys())
        with pytest.raises(HostKeyVerificationError) as excinfo:
            policy.missing_host_key(MagicMock(), HOST, key_a)
        assert str(excinfo.value) == EXPECTED_UNPINNED_MESSAGE

    def test_unpinned_reject_is_logged_as_error(self, key_a, caplog):
        policy = PinnedHostKeyPolicy(paramiko.HostKeys())
        with caplog.at_level(logging.ERROR, logger="src.sftp.uploader"), pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_a)
        assert EXPECTED_UNPINNED_MESSAGE in caplog.text

    def test_a_pin_for_another_host_does_not_open_this_one(self, key_a, key_b):
        """Pins are per-host — and an unpinned host is now REFUSED, not accepted."""
        policy = PinnedHostKeyPolicy(_pinned(OTHER_HOST, key_a))
        with pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_b)

    def test_missing_pin_files_fail_closed(self, hosts_files, key_a):
        """No pin file at all (the missing --add-data asset) → refuse, don't accept."""
        assert len(load_pinned_host_keys()) == 0
        policy = PinnedHostKeyPolicy(load_pinned_host_keys())
        with pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_a)

    def test_corrupt_pin_files_fail_closed(self, hosts_files, key_a, caplog):
        """One corrupt byte in BOTH files → refuse (was: silent trust-on-first-use)."""
        user_file, bundle_file = hosts_files
        corrupt = f"{HOST} ssh-ed25519 %%%not-base64%%%\n"
        user_file.write_text(corrupt, encoding="utf-8")
        bundle_file.write_text(corrupt, encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="src.sftp.uploader"):
            policy = PinnedHostKeyPolicy(load_pinned_host_keys())
        assert "unreadable pinned-host-key file" in caplog.text
        with pytest.raises(HostKeyVerificationError):
            policy.missing_host_key(MagicMock(), HOST, key_a)

    def test_nonstandard_port_fails_closed_with_the_port_specific_message(self, key_a, caplog):
        """A plain-host pin does NOT cover "[host]:port" — refuse, and say exactly why."""
        policy = PinnedHostKeyPolicy(_pinned(HOST, key_a))
        with (
            caplog.at_level(logging.ERROR, logger="src.sftp.uploader"),
            pytest.raises(HostKeyVerificationError) as excinfo,
        ):
            policy.missing_host_key(MagicMock(), BRACKETED_HOST, key_a)
        assert str(excinfo.value) == EXPECTED_PORT_UNPINNED_MESSAGE
        assert "ssh-keyscan -p PORT" in str(excinfo.value)

    def test_nonstandard_port_without_any_pin_fails_closed_generically(self, key_a):
        policy = PinnedHostKeyPolicy(paramiko.HostKeys())
        with pytest.raises(HostKeyVerificationError) as excinfo:
            policy.missing_host_key(MagicMock(), BRACKETED_HOST, key_a)
        assert str(excinfo.value) == _host_key_unpinned_message(BRACKETED_HOST)

    def test_unpinned_rejects_are_never_retried(self):
        """Same never-retry class as the mismatch — re-offering credentials to an
        unverified server is exactly what pinning exists to prevent."""
        assert not _is_transient_sftp_error(HostKeyVerificationError(EXPECTED_UNPINNED_MESSAGE))
        assert not _is_transient_sftp_error(HostKeyVerificationError(EXPECTED_REJECT_MESSAGE))

    @pytest.mark.parametrize("message", [EXPECTED_UNPINNED_MESSAGE, EXPECTED_PORT_UNPINNED_MESSAGE])
    def test_reject_copy_carries_no_secrets_or_user_paths(self, message: str):
        """Host name only — never a credential, an absolute path, or a key blob."""
        assert HOST in message
        for leak in ("password", "AAAAC3Nza", ":\\", "/Users/", "/home/"):
            assert leak not in message


class TestConnectDoesNotConsultSystemKnownHosts:
    """W1-A bypass (a): paramiko consults ``_system_host_keys`` BEFORE the missing-key
    policy, so a user-writable ``~/.ssh/known_hosts`` entry for an allowlisted host used
    to fully bypass the bundled pin. The client must never load that file."""

    def test_connect_never_loads_system_host_keys(self):
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            uploader._connect()
        mock_cls.return_value.load_system_host_keys.assert_not_called()

    def test_connect_loads_no_host_keys_at_all(self):
        """Nor ``load_host_keys`` — the PinnedHostKeyPolicy is the SINGLE decision point."""
        uploader = _uploader()
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch.object(uploader, "_get_password", return_value="pw"),
        ):
            uploader._connect()
        mock_cls.return_value.load_host_keys.assert_not_called()


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
