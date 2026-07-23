"""Tests for src/ui_flet/schedule_status.py — the pure tri-state schedule derivation (D4).

The single owner of the LIVE / MISSING / UNKNOWN contract + ALL schedule copy. These
tests are the precedence table the gate's sizing note asks for FIRST: every
``found`` × ``hint`` × contradiction combination, plus the load-bearing honesty
invariant — an UNKNOWN (query failed) NEVER asserts "scheduled"/a next-run time from
the config hint, and a MISSING never inherits a next-run time.
"""

from __future__ import annotations

from src.scheduler.windows import ScheduleReadback
from src.ui_flet.schedule_status import (
    ScheduleState,
    ScheduleStatus,
    derive_schedule_status,
    interpret_unregister,
    is_transient_location,
    needs_setup_badge,
)


def _derive(
    readback: ScheduleReadback,
    *,
    hint_registered: bool = True,
    latest_record_ts: str | None = None,
) -> ScheduleStatus:
    return derive_schedule_status(
        readback,
        hint_registered=hint_registered,
        latest_record_ts=latest_record_ts,
    )


class TestLive:
    def test_found_true_is_live(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T03:00:00.0000000"))
        assert status.state is ScheduleState.LIVE
        assert status.contradiction is False
        assert status.attention is False

    def test_live_next_run_display_comes_from_real_next_run(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T15:30:00.0000000"))
        assert status.next_run_display == "3:30 PM"
        assert "3:30 PM" in status.detail

    def test_live_without_os_next_run_is_timeless_never_the_hint(self) -> None:
        # Honesty B: a found task with no OS-reported NextRunTime reads timeless — the config
        # schedule_time is NEVER presented as a verified next-run (the hint-as-truth pattern).
        status = _derive(ScheduleReadback(found=True, next_run=None))
        assert status.next_run_display is None
        assert "AM" not in status.detail and "PM" not in status.detail
        assert "registered with Windows" in status.detail

    def test_live_unparseable_next_run_is_timeless(self) -> None:
        # An unparseable NextRunTime must NOT invent a bogus clock time — timeless copy.
        status = _derive(ScheduleReadback(found=True, next_run="not-a-date"))
        assert status.state is ScheduleState.LIVE
        assert status.next_run_display is None
        assert "AM" not in status.detail and "PM" not in status.detail

    def test_live_never_run_is_not_a_contradiction(self) -> None:
        # last_run None (the never-run sentinel is nulled in the reader) → no contradiction.
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_run=None))
        assert status.state is ScheduleState.LIVE
        assert status.contradiction is False


class TestContradiction:
    """The SOLE trigger is the record gap (last_run strictly newer than the newest run record).
    A non-benign LastTaskResult ALONE must not fire it (exit-3 writes a record — see below)."""

    def test_fired_no_record_gap_is_a_contradiction(self) -> None:
        # The task fired more recently than the newest recorded run → the store missed it.
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00.0000000", last_result=0),
            latest_record_ts="2026-07-07T03:00:05",
        )
        assert status.state is ScheduleState.LIVE
        assert status.contradiction is True
        assert status.attention is True
        # HEDGED copy — never a flat "didn't complete"/"was moved" assertion.
        lowered = status.detail.lower()
        assert "didn't report success" in lowered
        assert "if districtsync was moved" in lowered
        assert "didn't complete" not in lowered

    def test_exit3_run_with_a_record_is_not_a_contradiction(self) -> None:
        # An SFTP-failed run (exit 3) builds the roster + WRITES a record — Run History shows it
        # as a completed "Built, not delivered" row. A non-benign last_result must NOT flag it
        # here (last_run is NOT newer than the record that same run wrote).
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00", last_result=3),
            latest_record_ts="2026-07-08T03:00:00",
        )
        assert status.contradiction is False

    def test_nonbenign_last_result_alone_is_not_a_contradiction(self) -> None:
        # No records to compare against → no gap can be established → no contradiction, even
        # with a non-benign last_result (a pre-store run must not false-alarm).
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00", last_result=2147942402),
            latest_record_ts=None,
        )
        assert status.contradiction is False

    def test_last_run_not_newer_than_record_is_no_contradiction(self) -> None:
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-07T03:00:00", last_result=0),
            latest_record_ts="2026-07-07T03:05:00",
        )
        assert status.contradiction is False

    def test_no_records_at_all_is_no_false_alarm(self) -> None:
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00", last_result=0),
            latest_record_ts=None,
        )
        assert status.contradiction is False


class TestMissing:
    def test_found_false_is_missing(self) -> None:
        status = _derive(ScheduleReadback(found=False), hint_registered=False)
        assert status.state is ScheduleState.MISSING
        assert status.next_run_display is None

    def test_missing_while_expected_is_attention(self) -> None:
        # The Event-141 case: config believed a schedule existed but the task is gone.
        status = _derive(ScheduleReadback(found=False), hint_registered=True)
        assert status.state is ScheduleState.MISSING
        assert status.expected is True
        assert status.attention is True

    def test_missing_when_not_expected_is_not_attention(self) -> None:
        # A configured manual-only install that never scheduled → not an alarm.
        status = _derive(ScheduleReadback(found=False), hint_registered=False)
        assert status.state is ScheduleState.MISSING
        assert status.attention is False

    def test_missing_never_asserts_a_next_run_even_with_hint(self) -> None:
        status = _derive(ScheduleReadback(found=False), hint_registered=True)
        assert "3:00 AM" not in status.detail
        assert status.next_run_display is None

    def test_missing_copy_is_de_circularized_on_the_setup_surface(self) -> None:
        # Finding #3: rendered ON Setup, "add one in Setup" is circular → "add one below".
        home = derive_schedule_status(
            ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None, surface="home"
        )
        setup = derive_schedule_status(
            ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None, surface="setup"
        )
        assert "in Setup" in home.detail
        assert "below" in setup.detail and "in Setup" not in setup.detail

    def test_expected_missing_copy_is_de_circularized_on_setup(self) -> None:
        setup = derive_schedule_status(
            ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None, surface="setup"
        )
        assert "re-register it below" in setup.detail and "in Setup" not in setup.detail


class TestUnknown:
    def test_found_none_is_unknown(self) -> None:
        status = _derive(ScheduleReadback(found=None, error="access denied"))
        assert status.state is ScheduleState.UNKNOWN
        assert status.attention is False

    def test_unknown_never_asserts_scheduled_from_the_hint(self) -> None:
        # The load-bearing honesty invariant: a failed query with the config saying
        # "registered at 03:00" must NOT claim a schedule or a next-run time.
        status = _derive(
            ScheduleReadback(found=None, error="timeout"),
            hint_registered=True,
        )
        assert status.state is ScheduleState.UNKNOWN
        assert status.next_run_display is None
        assert "3:00 AM" not in status.detail
        assert "03:00" not in status.detail
        assert "next run" not in status.detail.lower()
        # "couldn't"/"can't"/"unable" — an honest can't-confirm, never a positive claim.
        lowered = status.detail.lower()
        assert "couldn't" in lowered or "could not" in lowered or "confirm" in lowered

    def test_non_windows_readback_is_unknown(self) -> None:
        # The reader returns found=None off Windows; the derivation must not claim absent.
        status = _derive(ScheduleReadback(found=None, error="only available on Windows"))
        assert status.state is ScheduleState.UNKNOWN


class TestBadgeModel:
    def test_badge_on_expected_missing(self) -> None:
        status = _derive(ScheduleReadback(found=False), hint_registered=True)
        assert needs_setup_badge(status) is True

    def test_badge_on_contradiction(self) -> None:
        # A record-gap contradiction (fired more recently than the newest record) badges Setup.
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00"),
            latest_record_ts="2026-07-07T03:00:00",
        )
        assert needs_setup_badge(status) is True

    def test_no_badge_on_clean_live(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"))
        assert needs_setup_badge(status) is False

    def test_no_badge_on_unknown(self) -> None:
        status = _derive(ScheduleReadback(found=None, error="denied"), hint_registered=True)
        assert needs_setup_badge(status) is False

    def test_no_badge_on_unexpected_missing(self) -> None:
        status = _derive(ScheduleReadback(found=False), hint_registered=False)
        assert needs_setup_badge(status) is False

    def test_badge_none_status_is_false(self) -> None:
        assert needs_setup_badge(None) is False


class TestBadgeIsWindowAware:
    """During an enabled seasonal pause the fired-but-no-record contradiction is by design and
    must NOT badge (matching Home's calm 'Paused' state) — but a genuinely MISSING task still
    must, because a gone schedule won't resume in the fall. The two attention sources are
    mutually exclusive, so ``paused`` suppresses exactly the contradiction case."""

    def _contradiction(self) -> object:
        # LIVE, fired more recently than the newest recorded run → attention via contradiction.
        return _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00"),
            latest_record_ts="2026-07-07T03:00:00",
        )

    def test_contradiction_still_badges_when_not_paused(self) -> None:
        # Baseline: outside a pause the contradiction badges exactly as before.
        assert needs_setup_badge(self._contradiction(), paused=False) is True

    def test_contradiction_is_suppressed_during_a_pause(self) -> None:
        status = self._contradiction()
        assert status.contradiction is True  # precondition — this IS the summer-spurious source
        assert needs_setup_badge(status, paused=True) is False

    def test_missing_still_badges_during_a_pause(self) -> None:
        # A gone task is a real problem even in summer — the pause must not hide it.
        missing = _derive(ScheduleReadback(found=False), hint_registered=True)
        assert missing.contradiction is False
        assert needs_setup_badge(missing, paused=True) is True

    def test_paused_default_is_false_so_existing_callers_are_unchanged(self) -> None:
        assert needs_setup_badge(self._contradiction()) is True

    def test_none_status_is_false_even_when_paused(self) -> None:
        assert needs_setup_badge(None, paused=True) is False


class TestTransientLocation:
    def test_downloads_is_transient(self) -> None:
        assert is_transient_location(r"C:\Users\jane\Downloads\DistrictSync.exe") is True

    def test_temp_is_transient(self) -> None:
        assert is_transient_location(r"C:\Users\jane\AppData\Local\Temp\DistrictSync.exe") is True

    def test_program_files_is_permanent(self) -> None:
        assert is_transient_location(r"C:\Program Files\DistrictSync\DistrictSync.exe") is False

    def test_forward_slash_path_is_handled(self) -> None:
        assert is_transient_location("/home/jane/Downloads/DistrictSync") is True

    def test_substring_only_folder_is_not_flagged(self) -> None:
        # "temperature" is not a transient dir — component match, not substring.
        assert is_transient_location(r"C:\temperature\DistrictSync.exe") is False

    def test_empty_is_not_transient(self) -> None:
        assert is_transient_location("") is False


class TestUnregisterPresentation:
    def test_successful_delete_is_success_shaped(self) -> None:
        outcome = interpret_unregister(True, "SUCCESS: The scheduled task was successfully deleted.")
        assert outcome.success_shaped is True

    def test_absent_task_is_success_shaped_idempotent(self) -> None:
        outcome = interpret_unregister(False, "ERROR: The system cannot find the file specified.")
        assert outcome.success_shaped is True  # already not scheduled — the desired end state holds

    def test_absent_cron_entry_is_success_shaped_idempotent(self) -> None:
        # crontab's own "the task doesn't exist" wording — a Linux Unregister of a missing
        # entry must classify as the idempotent success shape, same as schtasks' phrasings.
        outcome = interpret_unregister(False, "no crontab for jane")
        assert outcome.success_shaped is True
        assert outcome.headline == "No schedule was registered"

    def test_no_crontab_to_remove_message_is_success_shaped(self) -> None:
        # delete_cron's own benign message carries the same marker — belt and braces.
        outcome = interpret_unregister(False, "No crontab to remove.")
        assert outcome.success_shaped is True

    def test_real_failure_is_not_success_shaped(self) -> None:
        outcome = interpret_unregister(False, "ERROR: Access is denied.")
        assert outcome.success_shaped is False

    def test_failed_crontab_read_is_not_success_shaped(self) -> None:
        # The fail-loud read abort (register/delete refuse to rewrite an unreadable crontab)
        # is a REAL failure — it must never be mistaken for the absent-entry success shape.
        outcome = interpret_unregister(
            False, "Couldn't read the existing crontab (crontab -l exited 1): permission denied"
        )
        assert outcome.success_shaped is False
