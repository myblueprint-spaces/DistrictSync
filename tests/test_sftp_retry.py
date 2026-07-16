"""Tests for the bounded SFTP delivery retry in src/sftp/uploader.py (no network).

Delivery (``upload_csvs``) retries TRANSIENT failures (OSError / SSHException) up to
``SFTP_RETRY_ATTEMPTS`` total attempts with exponential backoff (2s, 4s). It NEVER
retries authentication failures (hammering a wrong password can lock the delivery
account) or host-key rejects (the MITM case), and exhausted retries re-raise the
ORIGINAL exception so the pipeline's exit-code-3 contract is untouched.
``test_connection`` never retries — a Setup "Test" click must answer fast.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import paramiko
import pytest

from src.sftp.uploader import (
    SFTP_RETRY_ATTEMPTS,
    SFTP_RETRY_BACKOFF_SECONDS,
    HostKeyVerificationError,
    SFTPUploader,
)


def _uploader() -> SFTPUploader:
    return SFTPUploader(host="sftp.ca.spacesedu.com", port=22, username="user", remote_path="/upload")


@pytest.fixture
def output_dir(tmp_path):
    (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
    return tmp_path


class TestRetryContract:
    def test_bounded_constants(self):
        """The retry budget is module-level and bounded: 3 attempts, 2s base backoff."""
        assert SFTP_RETRY_ATTEMPTS == 3
        assert SFTP_RETRY_BACKOFF_SECONDS == 2.0


class TestTransientRetry:
    def test_transient_connect_error_succeeds_on_second_attempt(self, output_dir):
        uploader = _uploader()
        client, sftp = MagicMock(), MagicMock()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=[paramiko.SSHException("connection reset"), (client, sftp)],
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
        ):
            uploaded = uploader.upload_csvs(output_dir)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 2
        mock_sleep.assert_called_once_with(2.0)

    def test_two_transient_failures_back_off_exponentially(self, output_dir, caplog):
        uploader = _uploader()
        client, sftp = MagicMock(), MagicMock()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=[OSError("network unreachable"), paramiko.SSHException("banner"), (client, sftp)],
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            caplog.at_level("WARNING", logger="src.sftp.uploader"),
        ):
            uploaded = uploader.upload_csvs(output_dir)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [2.0, 4.0]
        # Each retry is logged plainly with its attempt number.
        assert "attempt 1/3" in caplog.text
        assert "attempt 2/3" in caplog.text
        # The delivery only happened once — on the attempt that succeeded.
        assert sftp.put.call_count == 1

    def test_transient_put_error_reconnects_fresh(self, output_dir):
        """A dead transport can't be reused: the retry re-runs the WHOLE connect+put
        unit, and every attempt's client is closed."""
        uploader = _uploader()
        client1, sftp1 = MagicMock(), MagicMock()
        sftp1.put.side_effect = OSError("connection reset by peer")
        client2, sftp2 = MagicMock(), MagicMock()
        with (
            patch.object(uploader, "_connect", side_effect=[(client1, sftp1), (client2, sftp2)]) as mock_connect,
            patch("src.sftp.uploader.time.sleep"),
        ):
            uploaded = uploader.upload_csvs(output_dir)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 2
        assert sftp2.put.call_count == 1
        client1.close.assert_called_once()
        client2.close.assert_called_once()

    def test_exhausted_retries_propagate_original_exception(self, output_dir):
        """After the last attempt the ORIGINAL exception raises (pipeline exit 3)."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=paramiko.SSHException("still unreachable"),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(paramiko.SSHException, match="still unreachable"),
        ):
            uploader.upload_csvs(output_dir)
        assert mock_connect.call_count == SFTP_RETRY_ATTEMPTS
        assert mock_sleep.call_count == SFTP_RETRY_ATTEMPTS - 1


class TestNeverRetried:
    def test_auth_error_is_single_attempt(self, output_dir):
        """Hammering a wrong password can lock the delivery account — one attempt only."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=paramiko.AuthenticationException("Authentication failed."),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(paramiko.AuthenticationException),
        ):
            uploader.upload_csvs(output_dir)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_host_key_reject_is_single_attempt(self, output_dir):
        """The MITM reject must never be re-offered credentials."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=HostKeyVerificationError("identity changed"),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(HostKeyVerificationError),
        ):
            uploader.upload_csvs(output_dir)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_bad_host_key_exception_is_single_attempt(self, output_dir):
        """Defensive: BadHostKeyException subclasses SSHException but is still the
        identity-changed case — excluded from the transient set explicitly."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=paramiko.BadHostKeyException("sftp.ca.spacesedu.com", MagicMock(), MagicMock()),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(paramiko.BadHostKeyException),
        ):
            uploader.upload_csvs(output_dir)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_non_transient_error_is_single_attempt(self, output_dir):
        """A non-network failure (e.g. missing credentials) is not worth retrying."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=RuntimeError("No SFTP password found."),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="No SFTP password found"),
        ):
            uploader.upload_csvs(output_dir)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()


class TestNoRetrySurfaces:
    def test_test_connection_does_not_retry(self):
        """A Setup 'Test connection' click must answer fast — single attempt, no sleep."""
        uploader = _uploader()
        with (
            patch.object(
                uploader,
                "_connect",
                side_effect=paramiko.SSHException("network down"),
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
        ):
            ok, msg = uploader.test_connection()
        assert ok is False
        assert "network down" in msg
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()
