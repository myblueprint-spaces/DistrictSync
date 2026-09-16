"""`run_result_verdict` — Windows' own LastTaskResult, classified (plan 0046 C / A4).

The field `ScheduleReadback.last_result` has been read since inception and consumed by NOTHING
in `src/`. This slice makes it the primary "did the nightly actually run?" signal for the one
population that has no other: a district whose task runs as a SERVICE ACCOUNT, whose run records
are written to that account's profile and never reach this one.

Two things this file exists to hold still:

* **Totality.** The function is total over ``int | None`` with no raise path — a code we cannot
  name still produces a TRUE statement (non-zero means the last run did not report success, by
  definition of ``LastTaskResult``) without a guessed cause.
* **Channel separation.** ``RESULT_BATCH_LOGON_PROBLEM`` is a run-time ``LastTaskResult``, NOT a
  COM-exception scode. It must never leak into the registration/removal classifier's tables,
  which classify what registration RAISED. The guard here is structural, not a comment.
"""

from __future__ import annotations

import inspect
import re

import pytest

from src.scheduler.task_com import RESULT_BATCH_LOGON_PROBLEM, RESULT_HAS_NOT_RUN
from src.ui_flet.schedule_status import RunResult, RunResultVerdict, run_result_verdict


class TestRunResultVerdictTable:
    """One row per D4 contract line — the function's full, total mapping."""

    def test_none_is_unreadable_with_no_note(self) -> None:
        """No value read → UNREADABLE. Distinct from "a value we cannot name" (UNRECOGNISED)."""
        verdict = run_result_verdict(None)
        assert verdict.result is RunResult.UNREADABLE
        assert verdict.note is None

    def test_zero_is_ok_with_no_note(self) -> None:
        """Success adds no sentence — today's clean copy must stay byte-identical."""
        verdict = run_result_verdict(0)
        assert verdict.result is RunResult.OK
        assert verdict.note is None

    def test_never_run_sentinel_is_its_own_member(self) -> None:
        """SCHED_S_TASK_HAS_NOT_RUN is not a problem — a fresh task has simply not fired."""
        verdict = run_result_verdict(RESULT_HAS_NOT_RUN)
        assert verdict.result is RunResult.NEVER_RUN
        assert verdict.note
        assert "hasn't run yet" in verdict.note

    def test_one_is_failed(self) -> None:
        """Hedged to be true under BOTH readings — our exit 1, or a bare Win32 code."""
        verdict = run_result_verdict(1)
        assert verdict.result is RunResult.FAILED
        assert verdict.note
        assert "error" in verdict.note

    def test_three_is_delivery_failed_and_never_claims_files_were_built(self) -> None:
        """Exit 3 is OUR delivery-failure code; the copy names it as ours and claims nothing more."""
        verdict = run_result_verdict(3)
        assert verdict.result is RunResult.DELIVERY_FAILED
        assert verdict.note
        assert "delivery failure" in verdict.note
        # It must NOT assert the roster was built — under the bare-Win32 reading nothing was.
        assert "were built" not in verdict.note
        assert "was built" not in verdict.note

    def test_batch_logon_code_is_hedged_and_routes_to_it(self) -> None:
        """Community-sourced: state what Windows reported, offer no in-app remedy, point at IT."""
        verdict = run_result_verdict(RESULT_BATCH_LOGON_PROBLEM)
        assert verdict.result is RunResult.BATCH_LOGON_SUSPECTED
        assert verdict.note
        assert "batch job" in verdict.note
        assert "IT team" in verdict.note

    def test_two_is_unrecognised_not_a_guessed_bad_arguments(self) -> None:
        """Deliberately UNMAPPED: our exit 2 is unreachable for task-baked args, and
        ERROR_FILE_NOT_FOUND is also 2 — naming it would be more likely wrong than right."""
        verdict = run_result_verdict(2)
        assert verdict.result is RunResult.UNRECOGNISED

    @pytest.mark.parametrize("code", [2, 5, 267009, 0x80070005, -1, 4294967295])
    def test_arbitrary_non_zero_is_unrecognised_with_a_cause_free_note(self, code: int) -> None:
        """A code we will not guess at still yields a true statement, with no cause attached."""
        verdict = run_result_verdict(code)
        assert verdict.result is RunResult.UNRECOGNISED
        assert verdict.note
        assert "a problem" in verdict.note

    def test_is_total_over_every_probed_code_without_raising(self) -> None:
        """No raise path: a wide sweep of plausible LastTaskResult values all classify."""
        for code in [None, 0, 1, 2, 3, RESULT_HAS_NOT_RUN, RESULT_BATCH_LOGON_PROBLEM, *range(-5, 64)]:
            verdict = run_result_verdict(code)
            assert isinstance(verdict, RunResultVerdict)
            assert isinstance(verdict.result, RunResult)


class TestReportedAProblem:
    """The escalation predicate — only these rows may raise `attention`, and only when foreign."""

    @pytest.mark.parametrize(
        "result",
        [RunResult.FAILED, RunResult.DELIVERY_FAILED, RunResult.BATCH_LOGON_SUSPECTED, RunResult.UNRECOGNISED],
    )
    def test_true_for_every_non_zero_reported_result(self, result: RunResult) -> None:
        assert RunResultVerdict(result=result).reported_a_problem is True

    @pytest.mark.parametrize("result", [RunResult.OK, RunResult.NEVER_RUN, RunResult.UNREADABLE])
    def test_false_for_success_never_run_and_unreadable(self, result: RunResult) -> None:
        """UNREADABLE is a failed PROBE, NEVER_RUN is a task that hasn't fired — neither is a fault."""
        assert RunResultVerdict(result=result).reported_a_problem is False

    def test_every_member_is_covered_by_exactly_one_of_the_two_sets(self) -> None:
        """Totality of the predicate itself — a new member cannot be silently un-classified."""
        problems = {
            RunResult.FAILED,
            RunResult.DELIVERY_FAILED,
            RunResult.BATCH_LOGON_SUSPECTED,
            RunResult.UNRECOGNISED,
        }
        calm = {RunResult.OK, RunResult.NEVER_RUN, RunResult.UNREADABLE}
        assert problems | calm == set(RunResult)
        assert not (problems & calm)


class TestChannelSeparation:
    """`RESULT_BATCH_LOGON_PROBLEM` is a RUN-time result, never a registration scode."""

    def test_absent_from_the_registration_hresult_tables(self) -> None:
        """`_HRESULT_CANONICAL` / `_MESSAGE_TO_HRESULT` classify what register/remove RAISED."""
        from src.scheduler import task_com

        assert RESULT_BATCH_LOGON_PROBLEM not in task_com._HRESULT_CANONICAL
        assert RESULT_BATCH_LOGON_PROBLEM not in task_com._MESSAGE_TO_HRESULT.values()

    def test_not_imported_by_the_registration_error_classifier(self) -> None:
        """`setup_errors` classifies registration exceptions; this code cannot reach it."""
        from src.ui_flet import setup_errors

        source = inspect.getsource(setup_errors)
        assert "RESULT_BATCH_LOGON_PROBLEM" not in source

    def test_its_only_consumer_is_run_result_verdict(self) -> None:
        """Pinned so a future reader cannot quietly wire it into a second channel."""
        from src.ui_flet import schedule_status

        source = inspect.getsource(schedule_status.run_result_verdict)
        assert "RESULT_BATCH_LOGON_PROBLEM" in source


class TestCopyMakesNoCausalClaim:
    """0x80070569 is Community-sourced — the copy may report, never assert causation."""

    def test_batch_logon_note_states_what_windows_reported_and_offers_no_in_app_remedy(self) -> None:
        note = run_result_verdict(RESULT_BATCH_LOGON_PROBLEM).note or ""
        assert note.startswith("Windows reported")
        # No causal assertion about THIS install — the general meaning is stated, the diagnosis is IT's.
        assert not re.search(r"\bbecause\b|\bis caused by\b|\bwas caused by\b", note)
        # Never claims the district can fix a policy-blocked sync inside DistrictSync.
        for forbidden in ("in Setup", "in Settings", "re-register", "try again"):
            assert forbidden not in note

    @pytest.mark.parametrize("code", [1, 3, 2, RESULT_BATCH_LOGON_PROBLEM])
    def test_no_note_asserts_a_cause_the_sources_do_not_support(self, code: int) -> None:
        note = run_result_verdict(code).note or ""
        assert not re.search(r"\bbecause\b|\bis caused by\b|\bwas caused by\b", note)
