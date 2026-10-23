"""DSYNC-32 — diagnosing a nightly that fires but never records a run (pure half).

The incident: a machine-scoped install, the nightly registered under a gMSA, firing at 3 AM and
leaving no run record. The UI said "the app may have been moved or deleted" — but the
contradiction branch returned BEFORE Windows' own ``LastTaskResult`` was read, so the real
answer (``0x8007052E``, a logon failure) was dropped, and the "moved" sentence was asserted
without ever looking at the registered program.

This file pins the pure ``schedule_status`` side of the fix:

* the contradiction branch reads the verdict first — RUNNING is not a contradiction, a reported
  failure names itself with its code, OK/unreadable keep the hedged copy;
* "moved or deleted … re-register" appears iff ``program_moved`` (positive evidence);
* the SD60 combination end to end;
* the Setup "Windows last ran this task" line and the bounded "Copy details" text.

The impure ``registered_program_moved`` seam and the log line live in
``tests/test_ui_flet_schedule_probe.py``; the buttons' render smoke in
``tests/test_ui_flet_render_smoke.py``.
"""

from __future__ import annotations

import pytest

from src.scheduler.task_com import (
    RESULT_ACCESS_DENIED,
    RESULT_HAS_NOT_RUN,
    RESULT_LOGON_FAILURE,
    RESULT_RUNNING,
)
from src.scheduler.windows import ScheduleReadback
from src.ui_flet.schedule_status import (
    FOREIGN_RECORDS_SHARED_NOTE,
    LOG_POINTER_NOTE,
    PROGRAM_MOVED_NOTE,
    WINDOWS_REPORTED_PROBLEM_HEADLINE,
    RunResult,
    ScheduleState,
    ScheduleStatus,
    derive_schedule_status,
    diagnostic_details,
    last_run_line,
    needs_diagnosis,
    needs_setup_badge,
    run_result_verdict,
)

_GMSA = "SD60\\svc_districtsync$"
_TASK = "DistrictSync_Daily"
_NEWEST_RECORD = "2026-10-20T03:00:40"
_HEDGED_HEADLINE = "Your last scheduled run reported a problem"


def _gap(last_result: int | None) -> ScheduleReadback:
    """A record-gap read-back: the task fired after the newest record, with ``last_result``."""
    return ScheduleReadback(
        found=True,
        next_run="2026-10-24T03:00:00.0000000",
        last_run="2026-10-23T03:00:00.0000000",
        last_result=last_result,
        action_path="C:\\Program Files\\DistrictSync\\DistrictSync.exe",
    )


def _derive(
    readback: ScheduleReadback,
    *,
    foreign_account: str = "",
    shared_records: bool = False,
    program_moved: bool = False,
    latest_record_ts: str | None = _NEWEST_RECORD,
) -> ScheduleStatus:
    return derive_schedule_status(
        readback,
        hint_registered=True,
        latest_record_ts=latest_record_ts,
        foreign_account=foreign_account,
        shared_records=shared_records,
        program_moved=program_moved,
    )


class TestTheSd60Combination:
    """Machine scope + a foreign gMSA principal + shared records + a record gap + 0x8007052E."""

    def _status(self, *, program_moved: bool = False) -> ScheduleStatus:
        return _derive(
            _gap(RESULT_LOGON_FAILURE), foreign_account=_GMSA, shared_records=True, program_moved=program_moved
        )

    def test_it_is_a_contradiction_that_names_windows_failure(self) -> None:
        status = self._status()
        assert status.state is ScheduleState.LIVE
        assert status.contradiction is True
        assert status.attention is True
        assert needs_setup_badge(status) is True
        assert status.headline == WINDOWS_REPORTED_PROBLEM_HEADLINE

    def test_the_detail_carries_the_logon_verdict_its_code_and_the_log_pointer(self) -> None:
        detail = self._status().detail
        assert run_result_verdict(RESULT_LOGON_FAILURE).note in detail
        assert "(Windows result 0x8007052E.)" in detail
        assert LOG_POINTER_NOTE in detail

    def test_there_is_no_moved_sentence_without_evidence(self) -> None:
        detail = self._status().detail
        assert "moved or deleted" not in detail
        assert "re-register" not in detail
        assert PROGRAM_MOVED_NOTE not in detail

    def test_positive_twin_the_moved_sentence_appears_with_evidence(self) -> None:
        detail = self._status(program_moved=True).detail
        assert PROGRAM_MOVED_NOTE in detail
        assert "moved or deleted" in detail

    def test_the_status_carries_the_diagnostic_facts(self) -> None:
        status = self._status()
        assert status.run_result is not None
        assert status.run_result.result is RunResult.LOGON_FAILURE
        assert status.last_run == "2026-10-23T03:00:00.0000000"
        assert status.next_run == "2026-10-24T03:00:00.0000000"

    def test_the_account_is_not_in_the_contradiction_copy(self) -> None:
        """The contradiction branch never carried the foreign note; DSYNC-32 keeps it that way."""
        status = self._status()
        assert _GMSA not in status.detail
        assert FOREIGN_RECORDS_SHARED_NOTE.format(account=_GMSA) not in status.detail


class TestContradictionBranchReadsTheVerdictFirst:
    def test_running_is_not_a_contradiction(self) -> None:
        """LastRunTime moves at a run's START; the record lands at its END."""
        status = _derive(_gap(RESULT_RUNNING))
        assert status.contradiction is False
        assert status.attention is False
        assert "running right now" in status.detail
        assert status.headline == "Nightly sync is scheduled"

    def test_running_is_not_a_contradiction_on_a_shared_foreign_install_either(self) -> None:
        status = _derive(_gap(RESULT_RUNNING), foreign_account=_GMSA, shared_records=True)
        assert status.contradiction is False
        assert status.attention is False

    def test_positive_twin_the_same_gap_with_a_failure_is_a_contradiction(self) -> None:
        assert _derive(_gap(RESULT_ACCESS_DENIED)).contradiction is True

    @pytest.mark.parametrize("last_result", [0, None])
    def test_ok_or_unreadable_keeps_the_hedged_copy(self, last_result: int | None) -> None:
        status = _derive(_gap(last_result))
        assert status.contradiction is True
        assert status.headline == _HEDGED_HEADLINE
        assert status.detail == "The last nightly run didn't report success — open Run History for details."

    @pytest.mark.parametrize("last_result", [0, None])
    def test_ok_or_unreadable_with_evidence_appends_the_moved_sentence(self, last_result: int | None) -> None:
        status = _derive(_gap(last_result), program_moved=True)
        assert status.detail.endswith(PROGRAM_MOVED_NOTE)

    @pytest.mark.parametrize("last_result", [1, 3, 2, RESULT_ACCESS_DENIED, 0x80070569, 0x8007FFFF])
    def test_every_failure_verdict_names_windows_and_its_code(self, last_result: int) -> None:
        status = _derive(_gap(last_result))
        verdict = run_result_verdict(last_result)
        assert status.headline == WINDOWS_REPORTED_PROBLEM_HEADLINE
        assert verdict.code_hex is not None and verdict.code_hex in status.detail
        assert status.detail.startswith(verdict.coded_note or "")
        assert "moved or deleted" not in status.detail

    def test_a_suppressed_gap_stays_suppressed_whatever_the_result(self) -> None:
        """Plan 0046 A5 is UNCHANGED: foreign + per-user records suppresses the record gap. The
        failure is still reported — through the foreign-problem arm, with its code."""
        status = _derive(_gap(RESULT_LOGON_FAILURE), foreign_account=_GMSA, shared_records=False)
        assert status.contradiction is False
        assert status.attention is True
        assert status.headline == "Your last nightly run reported a problem"
        assert "(Windows result 0x8007052E.)" in status.detail


class TestMovedSentenceOutsideTheContradiction:
    def test_appended_beside_a_reported_problem_when_moved(self) -> None:
        readback = ScheduleReadback(found=True, next_run="2026-10-24T03:00:00", last_result=0x80070002)
        status = _derive(readback, program_moved=True, latest_record_ts=None)
        assert status.contradiction is False
        assert status.detail.endswith(PROGRAM_MOVED_NOTE)

    def test_absent_without_evidence(self) -> None:
        readback = ScheduleReadback(found=True, next_run="2026-10-24T03:00:00", last_result=0x80070002)
        assert PROGRAM_MOVED_NOTE not in _derive(readback, latest_record_ts=None).detail

    def test_absent_beside_a_clean_run_even_with_evidence(self) -> None:
        """A clean run from a different copy is a working schedule — no re-register nag."""
        readback = ScheduleReadback(found=True, next_run="2026-10-24T03:00:00", last_result=0)
        status = _derive(readback, program_moved=True, latest_record_ts=None)
        assert status.detail == "Your nightly schedule is registered — next run at 3:00 AM."

    def test_missing_and_unknown_never_carry_it(self) -> None:
        for readback in (ScheduleReadback(found=False), ScheduleReadback(found=None, error="x")):
            status = _derive(readback, program_moved=True)
            assert PROGRAM_MOVED_NOTE not in status.detail
            assert status.program_moved is False


class TestNeedsDiagnosis:
    def test_attention_needs_diagnosis(self) -> None:
        assert needs_diagnosis(_derive(_gap(RESULT_LOGON_FAILURE))) is True

    def test_a_non_escalating_reported_problem_needs_diagnosis(self) -> None:
        readback = ScheduleReadback(found=True, next_run="2026-10-24T03:00:00", last_result=1)
        status = _derive(readback, latest_record_ts=None)
        assert status.attention is False
        assert needs_diagnosis(status) is True

    @pytest.mark.parametrize("last_result", [0, None, RESULT_HAS_NOT_RUN, RESULT_RUNNING])
    def test_a_calm_live_status_does_not(self, last_result: int | None) -> None:
        readback = ScheduleReadback(found=True, next_run="2026-10-24T03:00:00", last_result=last_result)
        assert needs_diagnosis(_derive(readback, latest_record_ts=None)) is False

    def test_none_does_not(self) -> None:
        assert needs_diagnosis(None) is False


class TestLastRunLine:
    def test_ok(self) -> None:
        readback = ScheduleReadback(found=True, last_run="2026-10-23T03:00:00", last_result=0)
        assert (
            last_run_line(_derive(readback, latest_record_ts=None))
            == "Windows last ran this task Oct 23 at 3:00 AM — OK."
        )

    def test_a_failure_shows_the_code_and_the_label(self) -> None:
        line = last_run_line(_derive(_gap(RESULT_LOGON_FAILURE), foreign_account=_GMSA, shared_records=True))
        assert line == "Windows last ran this task Oct 23 at 3:00 AM — result 0x8007052E (account sign-in rejected)."

    def test_never_run(self) -> None:
        readback = ScheduleReadback(found=True, last_run=None, last_result=RESULT_HAS_NOT_RUN)
        assert last_run_line(_derive(readback, latest_record_ts=None)) == "Windows hasn't run this task yet."

    def test_running(self) -> None:
        line = last_run_line(_derive(_gap(RESULT_RUNNING)))
        assert line is not None and line.endswith("— it's running now.")

    def test_an_unreported_time_is_said_not_guessed(self) -> None:
        readback = ScheduleReadback(found=True, last_run=None, last_result=1)
        line = last_run_line(_derive(readback, latest_record_ts=None)) or ""
        assert line.startswith("Windows didn't report when it last ran this task")
        assert "0x00000001" in line

    def test_unreadable_result(self) -> None:
        readback = ScheduleReadback(found=True, last_run="2026-10-23T03:00:00", last_result=None)
        line = last_run_line(_derive(readback, latest_record_ts=None)) or ""
        assert line.endswith("— its result couldn't be read.")

    def test_only_live_has_a_line(self) -> None:
        assert last_run_line(None) is None
        for readback in (ScheduleReadback(found=False), ScheduleReadback(found=None, error="x")):
            assert last_run_line(_derive(readback)) is None


class TestCopyDetails:
    def test_carries_the_four_facts(self) -> None:
        status = _derive(_gap(RESULT_LOGON_FAILURE), foreign_account=_GMSA, shared_records=True)
        text = diagnostic_details(status, task_name=_TASK)
        assert f"Task: {_TASK}" in text
        assert "Windows last run: 2026-10-23 03:00:00" in text
        assert "Windows result: 0x8007052E (LOGON_FAILURE)" in text
        assert "Next run: 2026-10-24 03:00:00" in text
        assert "Run record: none for the last run" in text

    def test_names_no_account_and_no_path(self) -> None:
        """Bounded diagnostic output: the foreign principal and the registered action path both
        ride the status/read-back, and neither may reach the clipboard."""
        for moved in (False, True):
            status = _derive(
                _gap(RESULT_LOGON_FAILURE), foreign_account=_GMSA, shared_records=True, program_moved=moved
            )
            text = diagnostic_details(status, task_name=_TASK)
            assert _GMSA not in text
            assert "svc_districtsync" not in text
            assert "\\" not in text
            assert "Program Files" not in text
            assert "DistrictSync.exe" not in text
            assert "@" not in text

    def test_positive_twin_the_moved_fact_is_a_fixed_phrase(self) -> None:
        status = _derive(_gap(RESULT_LOGON_FAILURE), program_moved=True)
        assert "Registered program: missing or not this copy of DistrictSync" in diagnostic_details(
            status, task_name=_TASK
        )
        assert "Registered program" not in diagnostic_details(_derive(_gap(RESULT_LOGON_FAILURE)), task_name=_TASK)

    def test_is_total_over_every_state(self) -> None:
        for readback in (ScheduleReadback(found=False), ScheduleReadback(found=None, error="x")):
            text = diagnostic_details(_derive(readback), task_name=_TASK)
            assert "Windows result: not reported" in text
            assert "Windows last run: not reported" in text
        assert "Schedule state: not checked" in diagnostic_details(None, task_name="")
