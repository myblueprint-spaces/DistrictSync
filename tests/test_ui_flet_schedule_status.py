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
    foreign_account: str = "",
) -> ScheduleStatus:
    """Derive a status. ``foreign_account`` defaults to the SAME-ACCOUNT world, so every
    pre-0046-C case in this file keeps asserting today's behaviour unchanged; the plan 0046 C
    cases pass a name explicitly."""
    return derive_schedule_status(
        readback,
        hint_registered=hint_registered,
        latest_record_ts=latest_record_ts,
        foreign_account=foreign_account,
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
            foreign_account="",
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
            foreign_account="",
        )
        assert status.contradiction is False

    def test_nonbenign_last_result_alone_is_not_a_contradiction(self) -> None:
        # No records to compare against → no gap can be established → no contradiction, even
        # with a non-benign last_result (a pre-store run must not false-alarm).
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00", last_result=2147942402),
            latest_record_ts=None,
            foreign_account="",
        )
        assert status.contradiction is False

    def test_last_run_not_newer_than_record_is_no_contradiction(self) -> None:
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-07T03:00:00", last_result=0),
            latest_record_ts="2026-07-07T03:05:00",
            foreign_account="",
        )
        assert status.contradiction is False

    def test_no_records_at_all_is_no_false_alarm(self) -> None:
        status = _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00", last_result=0),
            latest_record_ts=None,
            foreign_account="",
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
            ScheduleReadback(found=False),
            hint_registered=False,
            latest_record_ts=None,
            foreign_account="",
            surface="home",
        )
        setup = derive_schedule_status(
            ScheduleReadback(found=False),
            hint_registered=False,
            latest_record_ts=None,
            foreign_account="",
            surface="setup",
        )
        assert "in Setup" in home.detail
        assert "below" in setup.detail and "in Setup" not in setup.detail

    def test_expected_missing_copy_is_de_circularized_on_setup(self) -> None:
        setup = derive_schedule_status(
            ScheduleReadback(found=False),
            hint_registered=True,
            latest_record_ts=None,
            foreign_account="",
            surface="setup",
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
            foreign_account="",
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
            foreign_account="",
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


class TestBadgeIsSilentDuringFirstRun:
    """0038 S6: an install that has not reached the wizard's finish line never badges Setup.

    Home now HOSTS the wizard, so the rail's Setup item and the surface the admin is
    already looking at are the same task. An "attention" dot on it mid-wizard points at
    the work in progress and reads as a fault — and the one state that could raise it (a
    stale task left by an earlier install, firing with no record) is exactly what the
    Schedule step reconciles against a few keystrokes later.
    """

    def _contradiction(self) -> object:
        return _derive(
            ScheduleReadback(found=True, last_run="2026-07-08T03:00:00"),
            latest_record_ts="2026-07-07T03:00:00",
            foreign_account="",
        )

    def _expected_missing(self) -> object:
        return _derive(ScheduleReadback(found=False), hint_registered=True)

    def test_a_contradiction_is_suppressed_while_setup_is_unfinished(self) -> None:
        assert needs_setup_badge(self._contradiction(), setup_unfinished=True) is False

    def test_an_expected_missing_task_is_suppressed_too(self) -> None:
        """The stronger half: even the Event-141 signal waits until setup is finished —
        "your schedule isn't registered anymore" is not news to someone still registering it."""
        assert needs_setup_badge(self._expected_missing(), setup_unfinished=True) is False

    def test_both_still_badge_once_setup_is_finished(self) -> None:
        """The positive twin — without it, the two absences above are equally satisfied by
        a badge rule that stopped firing altogether."""
        assert needs_setup_badge(self._contradiction(), setup_unfinished=False) is True
        assert needs_setup_badge(self._expected_missing(), setup_unfinished=False) is True

    def test_the_default_preserves_the_pre_S6_behaviour(self) -> None:
        assert needs_setup_badge(self._expected_missing()) is True


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


# --------------------------------------------------------------------------- #
# Plan 0046 C — the foreign principal: A5's suppression + A4's swapped-in signal #
# --------------------------------------------------------------------------- #
_SERVICE = "CONTOSO\\svc_districtsync"

#: A read-back that IS a record-gap contradiction on a same-account install: the task fired at
#: 04:00 and the newest recorded run is from 02:00, so nothing was captured for that firing.
_GAP_READBACK = ScheduleReadback(found=True, next_run="2026-07-10T03:00:00", last_run="2026-07-09T04:00:00")
_GAP_NEWEST_RECORD = "2026-07-09T02:00:00"


class TestForeignPrincipalSuppressesTheRecordGap:
    """A5: a missing run record is the DOCUMENTED consequence of where the record was written.

    ``src/history/store.py`` writes ``history.db`` under ``paths.user_data_dir()`` of the account
    the task RUNS as, so once the principal is a service account the nightly's records are written
    to that profile and never reach this one. Asserting a fault there would walk the admin into
    re-registering a task that is working perfectly — every night, forever.
    """

    def test_a_real_gap_is_suppressed_under_a_foreign_principal(self) -> None:
        status = _derive(_GAP_READBACK, latest_record_ts=_GAP_NEWEST_RECORD, foreign_account=_SERVICE)
        assert status.contradiction is False
        assert status.headline != "Your last scheduled run reported a problem"

    def test_positive_twin_the_same_gap_still_alarms_on_a_same_account_install(self) -> None:
        """THE test that proves the narrowing is narrow — identical inputs, no recorded principal."""
        status = _derive(_GAP_READBACK, latest_record_ts=_GAP_NEWEST_RECORD, foreign_account="")
        assert status.contradiction is True
        assert status.attention is True
        assert status.headline == "Your last scheduled run reported a problem"

    def test_is_contradiction_itself_is_one_directional(self) -> None:
        from src.ui_flet.schedule_status import _is_contradiction

        assert _is_contradiction(_GAP_READBACK, _GAP_NEWEST_RECORD, foreign_account="") is True
        assert _is_contradiction(_GAP_READBACK, _GAP_NEWEST_RECORD, foreign_account=_SERVICE) is False

    def test_foreign_account_is_required_keyword_only_on_is_contradiction(self) -> None:
        """A forgotten argument must be a TypeError, never a silently-defaulted suppression."""
        import pytest

        from src.ui_flet.schedule_status import _is_contradiction

        with pytest.raises(TypeError):
            _is_contradiction(_GAP_READBACK, _GAP_NEWEST_RECORD)  # type: ignore[call-arg]

    def test_foreign_account_is_required_keyword_only_on_derive(self) -> None:
        import pytest

        with pytest.raises(TypeError):
            derive_schedule_status(  # type: ignore[call-arg]
                ScheduleReadback(found=True), hint_registered=True, latest_record_ts=None
            )


class TestForeignAccountRidesEveryState:
    """Home and Run History read the field regardless of state, so all three builders carry it."""

    def test_live_missing_and_unknown_all_carry_the_recorded_account(self) -> None:
        for readback in (
            ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"),
            ScheduleReadback(found=False),
            ScheduleReadback(found=None, error="denied"),
        ):
            assert _derive(readback, foreign_account=_SERVICE).foreign_account == _SERVICE

    def test_the_default_is_the_conservative_do_not_suppress_value(self) -> None:
        assert ScheduleStatus(state=ScheduleState.LIVE, headline="h", detail="d").foreign_account == ""

    def test_missing_and_unknown_copy_is_byte_identical_under_a_foreign_principal(self) -> None:
        """Neither state may ASSERT where a schedule's records go — one is gone, one is unseen."""
        for readback in (ScheduleReadback(found=False), ScheduleReadback(found=None, error="denied")):
            for expected in (True, False):
                plain = _derive(readback, hint_registered=expected, foreign_account="")
                foreign = _derive(readback, hint_registered=expected, foreign_account=_SERVICE)
                assert (foreign.headline, foreign.detail) == (plain.headline, plain.detail)


class TestSameAccountDetailIsByteIdentical:
    """Nothing changes for the 19 of 20 districts that are not on a service account."""

    def test_benign_last_result_appends_nothing_on_any_state(self) -> None:
        for last_result in (0, None):
            for readback in (
                ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=last_result),
                ScheduleReadback(found=True, next_run=None, last_result=last_result),
                ScheduleReadback(found=False, last_result=last_result),
                ScheduleReadback(found=None, error="denied", last_result=last_result),
            ):
                status = _derive(readback, foreign_account="")
                assert "run records are saved under" not in status.detail
                assert not status.detail.endswith(" ")

    def test_clean_live_detail_is_exactly_todays_sentence(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=0))
        assert status.detail == "Your nightly schedule is registered — next run at 3:00 AM."
        assert status.headline == "Nightly sync is scheduled"

    def test_timeless_live_detail_is_exactly_todays_sentence(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run=None, last_result=None))
        assert status.detail == "Your nightly schedule is registered with Windows."

    def test_the_record_gap_copy_is_untouched_by_the_run_result_note(self) -> None:
        """The contradiction branch returns BEFORE the append — its copy is already about the
        run problem, and a second sentence would restate it from weaker evidence."""
        status = _derive(
            ScheduleReadback(found=True, next_run="2026-07-10T03:00:00", last_run="2026-07-09T04:00:00", last_result=1),
            latest_record_ts=_GAP_NEWEST_RECORD,
            foreign_account="",
        )
        assert status.contradiction is True
        assert "Windows recorded" not in status.detail


class TestRunResultNoteInTheLiveDetail:
    """A4: the OS result is rendered in the readout — one sentence, appended, never escalating
    on a same-account install."""

    def test_one_sentence_is_appended_and_attention_is_unchanged(self) -> None:
        from src.scheduler.task_com import RESULT_BATCH_LOGON_PROBLEM, RESULT_HAS_NOT_RUN
        from src.ui_flet.schedule_status import run_result_verdict

        base = "Your nightly schedule is registered — next run at 3:00 AM."
        for code in (1, 3, 2, RESULT_HAS_NOT_RUN, RESULT_BATCH_LOGON_PROBLEM):
            status = _derive(
                ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=code),
                foreign_account="",
            )
            note = run_result_verdict(code).note
            assert note is not None
            assert status.detail == f"{base} {note}"
            # Today's rule, unchanged: a same-account install never escalates on an OS result —
            # the run store and Run History already own that narrative.
            assert status.attention is False
            assert status.headline == "Nightly sync is scheduled"

    def test_the_foreign_note_precedes_the_run_result_note(self) -> None:
        from src.ui_flet.schedule_status import FOREIGN_RECORDS_NOTE, run_result_verdict

        status = _derive(
            ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=1),
            foreign_account=_SERVICE,
        )
        foreign_note = FOREIGN_RECORDS_NOTE.format(account=_SERVICE)
        run_note = run_result_verdict(1).note or ""
        assert foreign_note in status.detail
        assert run_note in status.detail
        assert status.detail.index(foreign_note) < status.detail.index(run_note)

    def test_the_foreign_note_names_the_recorded_account(self) -> None:
        status = _derive(ScheduleReadback(found=True, next_run="2026-07-09T03:00:00"), foreign_account=_SERVICE)
        assert _SERVICE in status.detail
        assert "don't appear in Run History here" in status.detail


class TestAttentionSwapsToTheOsResult:
    """The alarm is SWAPPED, not removed — a service-account district never loses its signal."""

    def test_attention_iff_a_reported_problem_on_a_foreign_principal(self) -> None:
        from src.scheduler.task_com import RESULT_BATCH_LOGON_PROBLEM, RESULT_HAS_NOT_RUN
        from src.ui_flet.schedule_status import run_result_verdict

        for code in (None, 0, 1, 2, 3, RESULT_HAS_NOT_RUN, RESULT_BATCH_LOGON_PROBLEM, 99):
            status = _derive(
                ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=code),
                foreign_account=_SERVICE,
            )
            assert status.attention is run_result_verdict(code).reported_a_problem

    def test_ok_and_never_run_never_escalate_on_any_principal(self) -> None:
        from src.scheduler.task_com import RESULT_HAS_NOT_RUN

        for account in ("", _SERVICE):
            for code in (0, None, RESULT_HAS_NOT_RUN):
                status = _derive(
                    ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=code),
                    foreign_account=account,
                )
                assert status.attention is False

    def test_the_problem_headline_is_distinct_from_the_record_gap_headline(self) -> None:
        """Different evidence classes get different headlines — one is an OS report, one an inference."""
        os_problem = _derive(
            ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=1),
            foreign_account=_SERVICE,
        )
        record_gap = _derive(_GAP_READBACK, latest_record_ts=_GAP_NEWEST_RECORD, foreign_account="")
        assert os_problem.headline == "Your last nightly run reported a problem"
        assert os_problem.headline != record_gap.headline

    def test_a_foreign_problem_badges_setup(self) -> None:
        status = _derive(
            ScheduleReadback(found=True, next_run="2026-07-09T03:00:00", last_result=1),
            foreign_account=_SERVICE,
        )
        assert needs_setup_badge(status) is True


class TestDocstringCorrection:
    """``_is_contradiction`` claimed a ``last_result`` consumer it never had (plan 0046 C)."""

    def test_the_false_supporting_evidence_sentence_is_gone(self) -> None:
        from src.ui_flet.schedule_status import _is_contradiction

        doc = _is_contradiction.__doc__ or ""
        assert "supporting evidence" not in doc

    def test_it_now_states_that_it_does_not_read_last_result(self) -> None:
        import inspect

        from src.ui_flet.schedule_status import _is_contradiction

        doc = _is_contradiction.__doc__ or ""
        assert "does NOT read" in doc
        assert "last_result" in doc
        # And the body genuinely does not — the docstring is now checkable against the code.
        body = inspect.getsource(_is_contradiction).split('"""')[-1]
        assert "last_result" not in body
