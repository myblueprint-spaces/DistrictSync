"""Tests for the bounded SFTP delivery retry in src/sftp/uploader.py (no network).

Delivery (``upload_csvs``) retries TRANSIENT failures (OSError / SSHException) on a
caller-chosen ``RetrySchedule``: ``INTERACTIVE_RETRY`` (3 attempts, 2s then 4s) for a person
waiting, ``UNATTENDED_RETRY`` (5 attempts, 30s/2min/5min/10min) for the scheduled nightly
(DSYNC-36). It NEVER
retries authentication failures (hammering a wrong password can lock the delivery
account) or host-key rejects (the MITM case), and exhausted retries re-raise the
ORIGINAL exception so the pipeline's exit-code-3 contract is untouched.
``test_connection`` never retries — a Setup "Test" click must answer fast.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from src.sftp.uploader import (
    INTERACTIVE_RETRY,
    SFTP_ATTEMPT_CONNECT_WORST_SECONDS,
    SFTP_RETRY_BUDGET_SECONDS,
    UNATTENDED_RETRY,
    HostKeyVerificationError,
    SFTPUploader,
    describe_sftp_error,
    retry_schedule_for_source,
)

SFTP_RETRY_ATTEMPTS = INTERACTIVE_RETRY.attempts  # the pre-DSYNC-36 contract: 3 attempts


def _uploader() -> SFTPUploader:
    return SFTPUploader(host="sftp.ca.spacesedu.com", port=22, username="user", remote_path="/upload")


@pytest.fixture
def output_dir(tmp_path):
    (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
    return tmp_path


class TestRetryContract:
    def test_interactive_schedule_is_the_short_one(self):
        """A person waiting gets the original behaviour: 3 attempts, waits 2s then 4s."""
        assert INTERACTIVE_RETRY.attempts == 3
        assert INTERACTIVE_RETRY.waits == (2.0, 4.0)

    def test_unattended_schedule_is_spaced_out(self):
        assert UNATTENDED_RETRY.attempts == 5
        assert UNATTENDED_RETRY.waits == (30.0, 120.0, 300.0, 600.0)
        assert UNATTENDED_RETRY.total_wait_seconds == 1050.0


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
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
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


class TestStalledPut:
    """DSYNC-9: a stalled put (channel read timeout -> socket.timeout) is a transient
    OSError: it retries, then fails as before (re-raised, so exit code 3)."""

    def test_stalled_put_retries_then_succeeds(self, output_dir):
        uploader = _uploader()
        stalled_client, stalled_sftp = MagicMock(), MagicMock()
        stalled_sftp.put.side_effect = TimeoutError("timed out")
        healthy_client, healthy_sftp = MagicMock(), MagicMock()
        with (
            patch.object(
                uploader, "_connect", side_effect=[(stalled_client, stalled_sftp), (healthy_client, healthy_sftp)]
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep"),
        ):
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 2
        assert healthy_sftp.put.call_count == 1
        stalled_client.close.assert_called_once()

    def test_stalled_put_exhausts_retries_and_raises(self, output_dir):
        uploader = _uploader()
        pairs = [(MagicMock(), MagicMock()) for _ in range(SFTP_RETRY_ATTEMPTS)]
        for _client, sftp in pairs:
            sftp.put.side_effect = TimeoutError("timed out")
        with (
            patch.object(uploader, "_connect", side_effect=pairs) as mock_connect,
            patch("src.sftp.uploader.time.sleep"),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert mock_connect.call_count == SFTP_RETRY_ATTEMPTS
        assert all(client.close.call_count == 1 for client, _ in pairs)


class TestEOFErrorDrop:
    """DSYNC-35: a mid-``put`` peer close surfaces as a bare ``EOFError()`` (paramiko's
    ``BaseSFTP._read_all``) -- not an OSError, empty message. It must retry like any drop."""

    def test_eof_error_from_put_retries_then_succeeds(self, output_dir):
        uploader = _uploader()
        dropped_client, dropped_sftp = MagicMock(), MagicMock()
        dropped_sftp.put.side_effect = EOFError()
        healthy_client, healthy_sftp = MagicMock(), MagicMock()
        with (
            patch.object(
                uploader, "_connect", side_effect=[(dropped_client, dropped_sftp), (healthy_client, healthy_sftp)]
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep"),
        ):
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 2
        assert healthy_sftp.put.call_count == 1
        dropped_client.close.assert_called_once()

    def test_retry_log_names_exception_type_when_message_empty(self, output_dir, caplog):
        uploader = _uploader()
        dropped = (MagicMock(), MagicMock())
        dropped[1].put.side_effect = EOFError()
        with (
            patch.object(uploader, "_connect", side_effect=[dropped, (MagicMock(), MagicMock())]),
            patch("src.sftp.uploader.time.sleep"),
            caplog.at_level("WARNING", logger="src.sftp.uploader"),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert "attempt 1/3 (EOFError)" in caplog.text

    def test_exhausted_eof_errors_propagate_and_log_the_type(self, output_dir, caplog):
        """Exhaustion is still a delivery failure (re-raised -> exit code 3) with a non-blank log."""
        uploader = _uploader()
        pairs = [(MagicMock(), MagicMock()) for _ in range(SFTP_RETRY_ATTEMPTS)]
        for _client, sftp in pairs:
            sftp.put.side_effect = EOFError()
        with (
            patch.object(uploader, "_connect", side_effect=pairs) as mock_connect,
            patch("src.sftp.uploader.time.sleep"),
            caplog.at_level("ERROR", logger="src.sftp.uploader"),
            pytest.raises(EOFError),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert mock_connect.call_count == SFTP_RETRY_ATTEMPTS
        assert "Failed to upload to sftp.ca.spacesedu.com: EOFError" in caplog.text

    def test_describe_sftp_error_falls_back_to_type_name(self):
        assert describe_sftp_error(EOFError()) == "EOFError"
        assert describe_sftp_error(OSError("reset")) == "reset"

    def test_auth_failure_is_still_not_retried(self, output_dir):
        """Positive twin: widening the transient set to EOFError must not touch auth."""
        uploader = _uploader()
        with (
            patch.object(
                uploader, "_connect", side_effect=paramiko.AuthenticationException("bad password")
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(paramiko.AuthenticationException),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()


class TestScheduleSelection:
    """The CALLER picks the schedule; scheduled runs get the long one, everything else the short."""

    def test_scheduled_source_is_unattended(self):
        assert retry_schedule_for_source("scheduled") is UNATTENDED_RETRY

    @pytest.mark.parametrize("source", ["manual", "cli", "unknown", None])
    def test_every_other_source_is_interactive(self, source):
        assert retry_schedule_for_source(source) is INTERACTIVE_RETRY

    def test_retry_is_required_keyword_only(self, output_dir):
        with pytest.raises(TypeError, match="retry"):
            _uploader().upload_csvs(output_dir, manifest={"Students.csv"})  # type: ignore[call-arg]


class TestUnattendedSchedule:
    def test_waits_follow_the_long_sequence_then_succeed(self, output_dir, caplog):
        uploader = _uploader()
        client, sftp = MagicMock(), MagicMock()
        failures = [OSError("[WinError 10054] reset")] * 3 + [EOFError()]
        with (
            patch.object(uploader, "_connect", side_effect=[*failures, (client, sftp)]) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            caplog.at_level("WARNING", logger="src.sftp.uploader"),
        ):
            uploaded = uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert uploaded == ["Students.csv"]
        assert mock_connect.call_count == 5
        assert [c.args[0] for c in mock_sleep.call_args_list] == [30.0, 120.0, 300.0, 600.0]
        # Greppable: attempt N/M, the error, and the wait.
        assert "attempt 1/5 ([WinError 10054] reset); retrying in 30s" in caplog.text
        assert "attempt 4/5 (EOFError); retrying in 600s" in caplog.text

    def test_exhaustion_still_raises_the_original_error(self, output_dir):
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", side_effect=OSError("getaddrinfo failed")) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(OSError, match="getaddrinfo failed"),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert mock_connect.call_count == 5
        assert mock_sleep.call_count == 4

    def test_auth_failure_is_not_retried_even_on_the_long_schedule(self, output_dir):
        uploader = _uploader()
        with (
            patch.object(
                uploader, "_connect", side_effect=paramiko.AuthenticationException("Authentication failed.")
            ) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(paramiko.AuthenticationException),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_host_key_reject_is_not_retried_on_the_long_schedule(self, output_dir):
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", side_effect=HostKeyVerificationError("changed")) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(HostKeyVerificationError),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_a_transient_error_is_retried_on_the_long_schedule(self, output_dir):
        """Positive twin of the auth test: same schedule, a network drop does retry."""
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", side_effect=[OSError("reset"), (MagicMock(), MagicMock())]) as mc,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert mc.call_count == 2
        mock_sleep.assert_called_once_with(30.0)

    def test_interactive_schedule_never_waits_long(self, output_dir):
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", side_effect=OSError("reset")),
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            pytest.raises(OSError),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 4.0]


class TestRetryIdempotency:
    """A retry re-puts under the SAME remote names for every file in the delivery envelope."""

    def test_every_attempt_puts_identical_remote_names(self, tmp_path):
        names = ["Students.csv", "Enrollments.csv", "StudentAttendance.csv", "CourseInfo.csv", "StudentCourses.csv"]
        for n in names:
            (tmp_path / n).write_text("a\n1\n", encoding="utf-8")
        uploader = _uploader()
        dropped_client, dropped_sftp = MagicMock(), MagicMock()
        # The drop happens AFTER the zip and the first standalone feed already went up.
        dropped_sftp.put.side_effect = [None, None, EOFError()]
        ok_client, ok_sftp = MagicMock(), MagicMock()
        with (
            patch.object(uploader, "_connect", side_effect=[(dropped_client, dropped_sftp), (ok_client, ok_sftp)]),
            patch("src.sftp.uploader.time.sleep"),
        ):
            uploader.upload_csvs(tmp_path, manifest=set(names), retry=UNATTENDED_RETRY)

        def remotes(sftp):
            return [c.args[1] for c in sftp.put.call_args_list]

        first, second = remotes(dropped_sftp), remotes(ok_sftp)
        assert len(second) == 4  # one zip + three standalone feeds
        # Everything the failed attempt had already put reappears under the identical name...
        assert set(first) <= set(second)
        # ...and a full delivery has no duplicate remote name.
        assert len(set(second)) == len(second)
        assert sum(r.endswith(".zip") for r in second) == 1


class TestRetryBudget:
    def test_no_retry_when_the_wait_would_cross_the_budget(self, output_dir):
        """A slow-failing attempt must not let the waits carry the run past the budget."""
        uploader = _uploader()
        clock = iter([0.0, 3580.0])  # started; elapsed 3580s at the first failure; 3580 + 30 >= 3600
        with (
            patch.object(uploader, "_connect", side_effect=OSError("reset")) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            patch("src.sftp.uploader.time.monotonic", side_effect=lambda: next(clock)),
            pytest.raises(OSError, match="reset"),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        assert mock_connect.call_count == 1
        mock_sleep.assert_not_called()

    def test_budget_only_bites_when_the_wait_crosses_it(self, output_dir):
        uploader = _uploader()
        clock = iter([0.0, 3569.0, 3570.0])
        with (
            patch.object(uploader, "_connect", side_effect=OSError("reset")) as mock_connect,
            patch("src.sftp.uploader.time.sleep") as mock_sleep,
            patch("src.sftp.uploader.time.monotonic", side_effect=lambda: next(clock)),
            pytest.raises(OSError),
        ):
            uploader.upload_csvs(output_dir, manifest={"Students.csv"}, retry=UNATTENDED_RETRY)
        # 3569 + 30 < 3600: one retry allowed; at 3570 + 120 >= 3600 it stops.
        assert mock_connect.call_count == 2
        mock_sleep.assert_called_once_with(30.0)


class TestWorstCaseBound:
    """The documented calculation, pinned: scheduled delivery stays far inside PT2H."""

    EXECUTION_TIME_LIMIT_SECONDS = 2 * 60 * 60

    def test_the_literal_matches_the_scheduled_task_limit(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "scheduler" / "task_com.py").read_text(encoding="utf-8")
        assert 'ExecutionTimeLimit = "PT2H"' in src

    def test_connect_phase_worst_case_is_240s(self):
        # TCP connect 30 + banner 30 + auth 30 + channel open 30 + SFTP version read 120.
        assert SFTP_ATTEMPT_CONNECT_WORST_SECONDS == 240

    def test_full_schedule_without_any_transfer_time(self):
        """5 attempts x 240s connect phase + 1050s of waiting = 2250s (37.5 min)."""
        total = UNATTENDED_RETRY.attempts * SFTP_ATTEMPT_CONNECT_WORST_SECONDS + UNATTENDED_RETRY.total_wait_seconds
        assert total == 2250
        assert total <= self.EXECUTION_TIME_LIMIT_SECONDS / 2

    def test_budget_bounds_a_slow_failing_transfer(self):
        """The last attempt STARTS before the budget; budget + its connect phase leaves
        over 50 minutes for that attempt's transfer plus the ETL that preceded delivery."""
        worst_before_transfer = SFTP_RETRY_BUDGET_SECONDS + SFTP_ATTEMPT_CONNECT_WORST_SECONDS
        assert worst_before_transfer == 3840
        assert self.EXECUTION_TIME_LIMIT_SECONDS - worst_before_transfer >= 50 * 60

    def test_schedule_waits_alone_fit_in_the_budget(self):
        assert UNATTENDED_RETRY.total_wait_seconds < SFTP_RETRY_BUDGET_SECONDS
