"""Full transition/resume/skip/gate table for the pure wizard state machine (Slice 8, D8).

``src/ui_flet/setup_flow.py`` is COUNTED (not view glue): resume derivation, per-step
Enter-advance gates, the no-step-flips-``setup_completed`` invariant, the adaptive
finish-copy variants (byte-exact), the ``task_args_changed`` reconcile predicate, and the
district auto-select-iff-exactly-one rule all live here and are pinned below. The view in
``screens/setup.py`` performs the I/O and feeds the injected facts in.
"""

from __future__ import annotations

import pytest

from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.setup_flow import (
    STEP_ORDER,
    TOTAL_STEPS,
    DeliveryFact,
    DowngradeInterrupt,
    FinishSummaryRow,
    FlowInputs,
    ReconcileOutcome,
    RegisteredSchedule,
    RunTimeSaveDecision,
    ScheduleReconcile,
    SetupStep,
    TaskArgs,
    auto_selected_district,
    can_advance,
    default_window_bounds,
    derive_flow,
    downgrade_interrupt,
    finish_copy,
    finish_needs_attention,
    finish_summary_rows,
    folders_save_note,
    is_skippable,
    next_step,
    prev_step,
    registered_schedule,
    run_time_save_decision,
    schedule_delivery_desync,
    schedule_reconcile,
    sftp_reconcile_suffix,
    step_number,
    task_args_changed,
    task_args_from_persisted,
    task_args_to_persisted,
)

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                            #
# --------------------------------------------------------------------------- #
_LIVE = ScheduleStatus(state=ScheduleState.LIVE, headline="", detail="")
_MISSING = ScheduleStatus(state=ScheduleState.MISSING, headline="", detail="")
_UNKNOWN = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")


def _inputs(**over) -> FlowInputs:
    base = {
        "folders_valid": False,
        "district_chosen": False,
        "schedule": None,
        "schedule_skipped": False,
        "delivery": DeliveryFact.NONE,
    }
    base.update(over)
    return FlowInputs(**base)


# --------------------------------------------------------------------------- #
# Step-order scaffolding                                                        #
# --------------------------------------------------------------------------- #
class TestStepScaffolding:
    def test_five_named_steps_in_fixed_order(self):
        # 2026-07-15 reorder: DISTRICT leads ("pick who you are first, then where files live"), then
        # FOLDERS. F1 still holds: DELIVERY precedes SCHEDULE so the sftp flag is committed BEFORE the
        # task is baked.
        assert TOTAL_STEPS == 5
        assert STEP_ORDER == (
            SetupStep.DISTRICT,
            SetupStep.FOLDERS,
            SetupStep.DELIVERY,
            SetupStep.SCHEDULE,
            SetupStep.FINISH,
        )

    @pytest.mark.parametrize(
        ("step", "number"),
        [
            (SetupStep.DISTRICT, 1),
            (SetupStep.FOLDERS, 2),
            (SetupStep.DELIVERY, 3),
            (SetupStep.SCHEDULE, 4),
            (SetupStep.FINISH, 5),
        ],
    )
    def test_step_number_is_one_based(self, step, number):
        assert step_number(step) == number

    def test_next_and_prev_step_walk_the_order(self):
        assert next_step(SetupStep.DISTRICT) is SetupStep.FOLDERS
        assert next_step(SetupStep.FOLDERS) is SetupStep.DELIVERY
        assert next_step(SetupStep.DELIVERY) is SetupStep.SCHEDULE
        assert next_step(SetupStep.SCHEDULE) is SetupStep.FINISH
        assert next_step(SetupStep.FINISH) is None
        assert prev_step(SetupStep.FOLDERS) is SetupStep.DISTRICT
        assert prev_step(SetupStep.SCHEDULE) is SetupStep.DELIVERY
        assert prev_step(SetupStep.DISTRICT) is None

    def test_only_schedule_and_delivery_are_skippable(self):
        assert is_skippable(SetupStep.SCHEDULE) is True
        assert is_skippable(SetupStep.DELIVERY) is True
        assert is_skippable(SetupStep.FOLDERS) is False
        assert is_skippable(SetupStep.DISTRICT) is False
        assert is_skippable(SetupStep.FINISH) is False


# --------------------------------------------------------------------------- #
# Resume derivation — first unsatisfied step from REAL state (no stored cursor) #
# --------------------------------------------------------------------------- #
class TestResumeDerivation:
    def test_fresh_install_resumes_at_district(self):
        # 2026-07-15 reorder: District leads, so a fresh install lands on the District step first.
        assert derive_flow(_inputs()).resume_step is SetupStep.DISTRICT

    def test_district_chosen_only_resumes_at_folders(self):
        state = derive_flow(_inputs(district_chosen=True))
        assert state.resume_step is SetupStep.FOLDERS
        assert SetupStep.DISTRICT in state.satisfied

    def test_district_and_folders_resume_at_delivery(self):
        # F1 reorder: Delivery is the first step after the two identity/location steps (before Schedule).
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True))
        assert state.resume_step is SetupStep.DELIVERY

    def test_delivery_pending_resumes_at_delivery_even_with_live_schedule(self):
        # Delivery precedes Schedule, so an unsatisfied Delivery lands there first.
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, schedule=_LIVE))
        assert state.resume_step is SetupStep.DELIVERY
        assert SetupStep.SCHEDULE in state.satisfied

    def test_delivery_done_but_schedule_pending_resumes_at_schedule(self):
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, delivery=DeliveryFact.SKIPPED))
        assert state.resume_step is SetupStep.SCHEDULE
        assert SetupStep.DELIVERY in state.satisfied

    def test_all_satisfied_resumes_at_finish(self):
        state = derive_flow(
            _inputs(
                folders_valid=True,
                district_chosen=True,
                schedule=_LIVE,
                delivery=DeliveryFact.TESTED_OK,
            )
        )
        assert state.resume_step is SetupStep.FINISH
        assert state.can_finish is True

    def test_both_deferred_reaches_finish(self):
        # The aha moment is not gated on a password + live SFTP credential (D8): skipping
        # both deferrable steps still reaches the finish line.
        state = derive_flow(
            _inputs(
                folders_valid=True,
                district_chosen=True,
                schedule_skipped=True,
                delivery=DeliveryFact.SKIPPED,
            )
        )
        assert state.resume_step is SetupStep.FINISH
        assert state.can_finish is True

    def test_reconcile_stored_credential_satisfies_delivery(self):
        # A prior session left a keyring credential — the Delivery step reconciles to
        # "already stored" instead of forcing a re-test (satisfied for resume).
        state = derive_flow(
            _inputs(
                folders_valid=True,
                district_chosen=True,
                schedule=_LIVE,
                delivery=DeliveryFact.STORED_CRED_PRESENT,
            )
        )
        assert state.resume_step is SetupStep.FINISH
        assert SetupStep.DELIVERY in state.satisfied


# --------------------------------------------------------------------------- #
# Schedule satisfaction honesty — UNKNOWN/MISSING/None never satisfy           #
# --------------------------------------------------------------------------- #
class TestScheduleSatisfactionHonesty:
    @pytest.mark.parametrize("status", [None, _UNKNOWN, _MISSING])
    def test_non_live_schedule_lands_on_schedule_step(self, status):
        # Delivery satisfied (skipped) so resume reaches the Schedule step (which precedes it now).
        state = derive_flow(
            _inputs(
                folders_valid=True,
                district_chosen=True,
                schedule=status,
                delivery=DeliveryFact.SKIPPED,
            )
        )
        assert state.resume_step is SetupStep.SCHEDULE
        assert SetupStep.SCHEDULE not in state.satisfied
        assert state.can_finish is False

    def test_unknown_never_treated_as_scheduled(self):
        # UNKNOWN ("couldn't confirm") must not silently count as done — the honesty invariant.
        assert derive_flow(_inputs(schedule=_UNKNOWN)).can_finish is False


# --------------------------------------------------------------------------- #
# Delivery satisfaction — tested_failed / none are NOT satisfied               #
# --------------------------------------------------------------------------- #
class TestDeliverySatisfaction:
    @pytest.mark.parametrize(
        ("fact", "satisfied"),
        [
            (DeliveryFact.TESTED_OK, True),
            (DeliveryFact.STORED_CRED_PRESENT, True),
            (DeliveryFact.SKIPPED, True),
            (DeliveryFact.TESTED_FAILED, False),
            (DeliveryFact.NONE, False),
        ],
    )
    def test_delivery_satisfaction_table(self, fact, satisfied):
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, schedule=_LIVE, delivery=fact))
        assert (SetupStep.DELIVERY in state.satisfied) is satisfied
        # A failed test lands the user back on Delivery (unfinished business), never Finish.
        assert (state.resume_step is SetupStep.FINISH) is satisfied


# --------------------------------------------------------------------------- #
# Per-step Enter-advance gate                                                   #
# --------------------------------------------------------------------------- #
class TestAdvanceGate:
    def test_folders_advance_requires_valid_folders(self):
        assert can_advance(SetupStep.FOLDERS, _inputs(folders_valid=False)) is False
        assert can_advance(SetupStep.FOLDERS, _inputs(folders_valid=True)) is True

    def test_district_advance_requires_chosen_district(self):
        assert can_advance(SetupStep.DISTRICT, _inputs(district_chosen=False)) is False
        assert can_advance(SetupStep.DISTRICT, _inputs(district_chosen=True)) is True

    def test_skippable_steps_always_advance(self):
        # Schedule + Delivery advance freely (skip = "set up later"), regardless of state.
        assert can_advance(SetupStep.SCHEDULE, _inputs()) is True
        assert can_advance(SetupStep.DELIVERY, _inputs()) is True

    def test_finish_advance_requires_all_prior_satisfied(self):
        not_ready = _inputs(folders_valid=True, district_chosen=True)  # schedule/delivery pending
        assert can_advance(SetupStep.FINISH, not_ready) is False
        ready = _inputs(
            folders_valid=True,
            district_chosen=True,
            schedule_skipped=True,
            delivery=DeliveryFact.SKIPPED,
        )
        assert can_advance(SetupStep.FINISH, ready) is True


class TestWindowAdvanceGate:
    """B: the Schedule step is skippable BUT window-gated — an enabled+invalid seasonal window
    closes Continue (the "Enter can't bypass a disabled button" guarantee, extended to the
    window). Delivery stays unconditionally advanceable; the window never affects it."""

    def test_schedule_blocks_when_window_invalid(self):
        assert can_advance(SetupStep.SCHEDULE, _inputs(window_valid=False)) is False

    def test_schedule_advances_when_window_valid(self):
        assert can_advance(SetupStep.SCHEDULE, _inputs(window_valid=True)) is True

    def test_delivery_is_never_affected_by_the_window(self):
        assert can_advance(SetupStep.DELIVERY, _inputs(window_valid=False)) is True

    def test_window_valid_defaults_true(self):
        # Opt-in: with no window touched the gate is open (the default FlowInputs value).
        assert _inputs().window_valid is True
        assert can_advance(SetupStep.SCHEDULE, _inputs()) is True

    def test_window_does_not_affect_resume_or_finish(self):
        # An invalid window is transient (never persisted) — it must not change resume/satisfaction.
        ready = _inputs(folders_valid=True, district_chosen=True, schedule_skipped=True, delivery=DeliveryFact.SKIPPED)
        assert derive_flow(ready).can_finish is True
        assert derive_flow(_inputs(window_valid=False)).resume_step is derive_flow(_inputs()).resume_step


class TestDefaultWindowBounds:
    """B pre-fill: (start, end) derived from the district academic calendar — pure + TOTAL."""

    def test_canonical_base_calendar_reproduces_the_owner_example(self):
        # The base myedbc academic start is 08-25 → start 08-11 (−14d), end 07-06 (start −36d):
        # EXACTLY the owner's canonical Aug 11 → Jul 6 window.
        assert default_window_bounds("08-25", "07-25") == ("08-11", "07-06")

    def test_start_is_academic_start_minus_two_weeks(self):
        assert default_window_bounds("09-08")[0] == "08-25"  # Sep 8 − 14d = Aug 25

    def test_end_ignores_academic_end_data_boundary(self):
        # academic_end ("07-25") is the DATA-year boundary, NOT school-end — it must NOT be the
        # season end (that would overlap the start). The end derives from the start instead.
        _, end = default_window_bounds("08-25", "07-25")
        assert end != "07-25"
        assert end == "07-06"

    def test_end_gives_a_summer_gap_before_the_start(self):
        start, end = default_window_bounds("08-25")
        # end is ~5 weeks before start (the summer pause) — a wrap-around window, start > end.
        assert start > end

    @pytest.mark.parametrize("bad", [None, "", "   ", "13-40", "not-a-date", "0825", "02-29"])
    def test_unusable_start_falls_back_to_plain_defaults(self, bad):
        # None/blank/malformed/leap-day (a school year never starts Feb 29) → the plain fallback.
        assert default_window_bounds(bad) == ("08-11", "07-06")

    def test_academic_end_is_optional(self):
        assert default_window_bounds("08-25") == ("08-11", "07-06")


class TestWindowIsNotATaskArg:
    """B: the seasonal window is NOT baked into the scheduled task — changing it must NEVER
    re-register. ``TaskArgs`` therefore has no window field, so a window-only change is invisible
    to ``task_args_changed`` (the reconcile predicate the Settings Save drives)."""

    def test_task_args_has_no_window_field(self):
        from dataclasses import fields

        names = {f.name for f in fields(TaskArgs)}
        assert not any(n.startswith("sync_window") or n.startswith("window") for n in names)

    def test_window_only_change_is_not_a_task_arg_change(self):
        # Two AppConfigs differing ONLY in the seasonal window produce IDENTICAL TaskArgs, so the
        # reconcile sees "unchanged" → no re-register (the non-negotiable: window is not a task arg).
        common = dict(input_dir="/in", output_dir="/out", sis_type="myedbc", sftp_enabled=False, run_time="03:00")
        before = TaskArgs.of(**common)
        after = TaskArgs.of(**common)  # window differs in the config, but never reaches TaskArgs
        assert task_args_changed(before, after) is False


# --------------------------------------------------------------------------- #
# No step flips setup_completed — the finish line is the only completion signal #
# --------------------------------------------------------------------------- #
class TestNoStepFlipsCompleted:
    def test_module_has_no_setup_completed_concept(self):
        # The pure flow machine must never carry or set a "completed" flag — completion is the
        # view's explicit finish confirmation alone. Guard structurally so a future edit that
        # sneaks a setup_completed field into the flow state fails loudly.
        import src.ui_flet.setup_flow as flow

        assert not hasattr(flow, "setup_completed")
        state = derive_flow(
            _inputs(
                folders_valid=True,
                district_chosen=True,
                schedule=_LIVE,
                delivery=DeliveryFact.TESTED_OK,
            )
        )
        assert not hasattr(state, "setup_completed")
        # can_finish is REACHABILITY, not "the install is set up".
        assert set(vars(state)) == {"resume_step", "satisfied", "can_finish"}


# --------------------------------------------------------------------------- #
# Adaptive finish copy — three honest variants, byte-exact                      #
# --------------------------------------------------------------------------- #
class TestFinishCopy:
    def test_schedule_skipped_variant(self):
        headline, detail = finish_copy(
            schedule_live=False,
            delivery=DeliveryFact.NONE,
            district="New Westminster",
            schedule_time_display=None,
            host="",
            username="",
        )
        # #1a: the schedule-skipped headline names the one thing still open (no over-signal).
        assert headline == "You're set up — nightly sync not scheduled yet"
        assert detail == (
            "DistrictSync will build New Westminster when you run a conversion. "
            "Run conversions from the Convert tab; add a nightly schedule whenever you're ready."
        )

    def test_delivery_deferred_variant_with_time(self):
        headline, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.SKIPPED,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="",
            username="",
        )
        assert headline == "You're all set"
        assert detail == (
            "Tonight at 3:00 AM DistrictSync will build New Westminster into your "
            "output folder. Set up delivery whenever you're ready."
        )

    def test_delivery_deferred_variant_timeless(self):
        _, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.NONE,
            district="New Westminster",
            schedule_time_display=None,
            host="",
            username="",
        )
        assert detail == (
            "Tonight DistrictSync will build New Westminster into your "
            "output folder. Set up delivery whenever you're ready."
        )

    def test_delivery_persisted_variant_claims_nightly_delivery(self):
        # F1 honesty: PERSISTED delivery (STORED_CRED_PRESENT) — the confident nightly-delivery claim.
        headline, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.STORED_CRED_PRESENT,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
        )
        assert headline == "You're all set"
        assert detail == (
            "Tonight at 3:00 AM DistrictSync will build New Westminster and try to deliver it to "
            "SpacesEDU — your delivery password is saved on this computer."
        )

    def test_delivery_tested_unsaved_does_not_claim_nightly_delivery(self):
        # F1 inversion fix: a TESTED-but-UNSAVED delivery names the working connection + prompts
        # Save, and NEVER claims the nightly will deliver (the nightly reads SAVED config).
        _, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.TESTED_OK,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
        )
        assert detail == (
            "Tonight at 3:00 AM DistrictSync will build New Westminster into your output folder. "
            "Your delivery connection to sftp.ca.spacesedu.com as district_x worked — click Save "
            "on the delivery step to have the nightly sync deliver it too."
        )
        assert "try to deliver" not in detail  # no nightly-delivery promise for an unsaved test

    def test_tested_failed_is_defer_copy(self):
        # A FAILED test is not persisted → the plain defer copy (no delivery claim, no Save prompt).
        _, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.TESTED_FAILED,
            district="New Westminster",
            schedule_time_display=None,
            host="h",
            username="u",
        )
        assert detail == (
            "Tonight DistrictSync will build New Westminster into your "
            "output folder. Set up delivery whenever you're ready."
        )

    def test_schedule_skipped_wins_over_delivery_state(self):
        # When the schedule was skipped, the copy never claims "tonight" even if a credential
        # was persisted — no schedule means no nightly run to promise.
        _, detail = finish_copy(
            schedule_live=False,
            delivery=DeliveryFact.STORED_CRED_PRESENT,
            district="Sea to Sky",
            schedule_time_display="3:00 AM",
            host="h",
            username="u",
        )
        assert detail.startswith("DistrictSync will build Sea to Sky when you run a conversion.")
        assert "Tonight" not in detail

    def test_delivery_desync_downgrades_the_persisted_delivery_claim(self):
        # The backtrack guard: the credential IS saved but the LIVE task was baked without --sftp —
        # the copy must NOT claim tonight delivers; it names the one Save in Settings that fixes it.
        headline, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.STORED_CRED_PRESENT,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
            delivery_desync=True,
        )
        assert headline == "You're set up — delivery needs one more save"
        assert detail == (
            "Tonight at 3:00 AM DistrictSync will build New Westminster into your output folder. "
            "Your delivery password is saved, but the nightly schedule hasn't picked up the delivery "
            "change yet — finish setup, then click Save in Settings to have the nightly sync deliver "
            "it too."
        )
        assert "try to deliver" not in detail  # the nightly-delivery promise is withdrawn

    def test_save_then_test_desync_downgrades_the_tested_ok_claim_too(self):
        # W4a nit: on the Save-then-Test path the post-save Test flips the session's delivery fact
        # from STORED_CRED_PRESENT to TESTED_OK — the desync facts are unchanged (the credential IS
        # saved; the live task still lacks --sftp), so the copy must downgrade identically instead
        # of keeping the confident "You're all set" under an amber band.
        headline, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.TESTED_OK,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
            delivery_desync=True,
        )
        assert headline == "You're set up — delivery needs one more save"
        assert "the nightly schedule hasn't picked up the delivery change yet" in detail
        assert "try to deliver" not in detail
        # The tested-but-unsaved "click Save on the delivery step" prompt would be WRONG advice
        # here (the credential is saved — the fix is the one Save in Settings).
        assert "click Save on the delivery step" not in detail

    def test_delivery_desync_only_affects_the_configured_delivery_branches(self):
        # A deferred delivery claims nothing about delivering, so the desync flag changes nothing.
        base = dict(
            schedule_live=True,
            delivery=DeliveryFact.SKIPPED,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="",
            username="",
        )
        assert finish_copy(**base, delivery_desync=True) == finish_copy(**base, delivery_desync=False)

    def test_desync_default_false_keeps_the_confident_claim_byte_identical(self):
        headline, detail = finish_copy(
            schedule_live=True,
            delivery=DeliveryFact.STORED_CRED_PRESENT,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
        )
        assert headline == "You're all set"
        assert "try to deliver it to SpacesEDU" in detail


# --------------------------------------------------------------------------- #
# finish_needs_attention — the single source of the finish banner's amber tone  #
# --------------------------------------------------------------------------- #
class TestFinishNeedsAttention:
    """W4a nit: tone (verdict) and words (headline) must derive from the SAME predicate."""

    def test_configured_delivery_with_desync_needs_attention(self):
        assert finish_needs_attention(delivery=DeliveryFact.STORED_CRED_PRESENT, delivery_desync=True) is True
        assert finish_needs_attention(delivery=DeliveryFact.TESTED_OK, delivery_desync=True) is True

    def test_unconfigured_delivery_never_needs_attention_even_with_desync(self):
        # The unconfigured branches keep the confident copy, so the tone must stay calm too.
        for fact in (DeliveryFact.SKIPPED, DeliveryFact.NONE, DeliveryFact.TESTED_FAILED):
            assert finish_needs_attention(delivery=fact, delivery_desync=True) is False

    def test_no_desync_never_needs_attention(self):
        for fact in DeliveryFact:
            assert finish_needs_attention(delivery=fact, delivery_desync=False) is False

    def test_tone_always_agrees_with_the_headline(self):
        # The agreement property itself: amber tone ⇔ the desync headline, for EVERY delivery fact
        # (schedule_live=True — a desync is only derivable for a live task by construction).
        for fact in DeliveryFact:
            for desync in (False, True):
                headline, _ = finish_copy(
                    schedule_live=True,
                    delivery=fact,
                    district="New Westminster",
                    schedule_time_display="3:00 AM",
                    host="sftp.ca.spacesedu.com",
                    username="district_x",
                    delivery_desync=desync,
                )
                downgraded = headline == "You're set up — delivery needs one more save"
                assert finish_needs_attention(delivery=fact, delivery_desync=desync) is downgraded


# --------------------------------------------------------------------------- #
# Finish-line checked summary — configured-vs-deferred rows, honest + ordered   #
# --------------------------------------------------------------------------- #
class TestFinishSummaryRows:
    def _rows_by_label(self, rows: list[FinishSummaryRow]) -> dict[str, FinishSummaryRow]:
        return {row.label: row for row in rows}

    def test_rows_follow_wizard_input_order(self):
        rows = finish_summary_rows(
            schedule_live=True,
            delivery=DeliveryFact.STORED_CRED_PRESENT,
            district="New Westminster",
            schedule_time_display="3:00 AM",
        )
        # District → Folders → Delivery → Schedule (the wizard input order; Finish is not a row).
        assert [row.label for row in rows] == ["District", "Folders", "Delivery", "Schedule"]

    def test_all_configured_every_row_done_with_concrete_values(self):
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=True,
                delivery=DeliveryFact.STORED_CRED_PRESENT,
                district="New Westminster",
                schedule_time_display="3:00 AM",
            )
        )
        assert all(row.done for row in rows.values())
        assert rows["Folders"].detail == "Ready"
        assert rows["District"].detail == "New Westminster"  # the friendly name, never a raw id
        assert rows["Delivery"].detail == "SpacesEDU"
        assert rows["Schedule"].detail == "Nightly at 3:00 AM"  # the OS-reported time, named

    def test_all_skippable_deferred_folders_and_district_still_done(self):
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=False,
                delivery=DeliveryFact.SKIPPED,
                district="Sea to Sky",
                schedule_time_display=None,
            )
        )
        # Required steps are done; both skippable steps read as an honest, deferred "later".
        assert rows["Folders"].done is True
        assert rows["District"].done is True
        assert rows["Delivery"].done is False
        assert rows["Delivery"].detail == "Set up later in Setup"
        assert rows["Schedule"].done is False
        assert rows["Schedule"].detail == "Set up later in Setup"

    def test_mixed_delivery_configured_schedule_deferred(self):
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=False,
                delivery=DeliveryFact.TESTED_OK,
                district="New Westminster",
                schedule_time_display=None,
            )
        )
        # A tested-ok credential is CONFIGURED (done); the un-live schedule is deferred.
        assert rows["Delivery"].done is True
        assert rows["Delivery"].detail == "SpacesEDU"
        assert rows["Schedule"].done is False

    def test_live_schedule_without_reported_time_is_timeless(self):
        # #7 honesty carried into the summary: LIVE but no OS next-run → a timeless label, never a
        # config hint presented as a verified time.
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=True,
                delivery=DeliveryFact.SKIPPED,
                district="New Westminster",
                schedule_time_display=None,
            )
        )
        assert rows["Schedule"].done is True
        assert rows["Schedule"].detail == "Nightly sync scheduled"

    @pytest.mark.parametrize(
        ("fact", "done"),
        [
            (DeliveryFact.TESTED_OK, True),
            (DeliveryFact.STORED_CRED_PRESENT, True),
            (DeliveryFact.SKIPPED, False),
            (DeliveryFact.TESTED_FAILED, False),
            (DeliveryFact.NONE, False),
        ],
    )
    def test_delivery_configured_is_narrower_than_flow_satisfied(self, fact, done):
        # Honesty edge: SKIPPED "satisfies" the FLOW (can finish) but is DEFERRED in the summary —
        # a credential is only "configured" when tested-ok or stored; failed/absent never show done.
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=True,
                delivery=fact,
                district="New Westminster",
                schedule_time_display="3:00 AM",
            )
        )
        assert rows["Delivery"].done is done
        assert (rows["Delivery"].detail == "SpacesEDU") is done
        if not done:
            assert rows["Delivery"].detail == "Set up later in Setup"

    def test_deferred_rows_never_render_as_done(self):
        # The load-bearing honesty invariant: a deferred delivery/schedule is NEVER a fake ✓.
        rows = finish_summary_rows(
            schedule_live=False,
            delivery=DeliveryFact.NONE,
            district="New Westminster",
            schedule_time_display="3:00 AM",  # ignored when the schedule isn't live
        )
        deferred = [row for row in rows if not row.done]
        assert {row.label for row in deferred} == {"Delivery", "Schedule"}
        assert all(row.detail == "Set up later in Setup" for row in deferred)

    def test_delivery_desync_annotates_the_live_schedule_row(self):
        # Consistency with the downgraded finish copy: the LIVE schedule row must not read as if
        # tonight delivers when the task was baked without --sftp.
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=True,
                delivery=DeliveryFact.STORED_CRED_PRESENT,
                district="New Westminster",
                schedule_time_display="3:00 AM",
                delivery_desync=True,
            )
        )
        assert rows["Schedule"].done is True  # the schedule IS live — only the delivery link is stale
        assert rows["Schedule"].detail == "Nightly at 3:00 AM — delivery not included yet"
        assert rows["Delivery"].detail == "SpacesEDU"  # the credential is genuinely configured

    def test_delivery_desync_annotates_the_timeless_live_row_too(self):
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=True,
                delivery=DeliveryFact.STORED_CRED_PRESENT,
                district="New Westminster",
                schedule_time_display=None,
                delivery_desync=True,
            )
        )
        assert rows["Schedule"].detail == "Nightly sync scheduled — delivery not included yet"

    def test_delivery_desync_never_touches_a_deferred_schedule_row(self):
        # Not live → no task to be stale; the deferred detail stays byte-identical.
        rows = self._rows_by_label(
            finish_summary_rows(
                schedule_live=False,
                delivery=DeliveryFact.STORED_CRED_PRESENT,
                district="New Westminster",
                schedule_time_display=None,
                delivery_desync=True,
            )
        )
        assert rows["Schedule"].detail == "Set up later in Setup"


# --------------------------------------------------------------------------- #
# schedule_delivery_desync — the wizard backtrack guard (0029 close-out)        #
# --------------------------------------------------------------------------- #
class TestScheduleDeliveryDesync:
    def _registered(self, *, sftp_enabled: bool) -> TaskArgs:
        return TaskArgs.of(
            input_dir="/in",
            output_dir="/out",
            sis_type="myedbc",
            sftp_enabled=sftp_enabled,
            run_time="03:00",
        )

    def test_live_task_baked_without_sftp_and_delivery_now_enabled_is_desync(self):
        # THE backtrack case: register → Back → save a credential → Finish.
        assert (
            schedule_delivery_desync(
                schedule_live=True,
                registered=self._registered(sftp_enabled=False),
                sftp_enabled=True,
            )
            is True
        )

    def test_task_baked_with_sftp_matching_enabled_delivery_is_not_desync(self):
        assert (
            schedule_delivery_desync(
                schedule_live=True,
                registered=self._registered(sftp_enabled=True),
                sftp_enabled=True,
            )
            is False
        )

    def test_delivery_disabled_never_flags(self):
        # The finish copy claims no delivery when sftp is off — nothing to downgrade (and the
        # reverse direction, a baked --sftp with delivery now off, is unreachable from the wizard).
        for baked in (True, False):
            assert (
                schedule_delivery_desync(
                    schedule_live=True,
                    registered=self._registered(sftp_enabled=baked),
                    sftp_enabled=False,
                )
                is False
            )

    def test_no_live_schedule_never_flags(self):
        assert (
            schedule_delivery_desync(
                schedule_live=False,
                registered=self._registered(sftp_enabled=False),
                sftp_enabled=True,
            )
            is False
        )

    def test_no_usable_record_never_flags(self):
        # Defensive: a pre-record install (or a hand-edited config.json) has no evidence of what
        # the task carries — never assert a desync without it.
        assert schedule_delivery_desync(schedule_live=True, registered=None, sftp_enabled=True) is False


# --------------------------------------------------------------------------- #
# task_args_changed — the Settings-mode reconcile predicate                     #
# --------------------------------------------------------------------------- #
class TestTaskArgsChanged:
    def _args(self, **over) -> TaskArgs:
        base = {
            "input_dir": "/in",
            "output_dir": "/out",
            "sis_type": "myedbc",
            "sftp_enabled": False,
            "run_time": "03:00",
        }
        base.update(over)
        return TaskArgs.of(**base)

    def test_identical_args_are_not_changed(self):
        assert task_args_changed(self._args(), self._args()) is False

    @pytest.mark.parametrize(
        "field_over",
        [
            {"input_dir": "/in2"},
            {"output_dir": "/out2"},
            {"sis_type": "sd48myedbc"},
            {"sftp_enabled": True},
            {"run_time": "04:00"},
        ],
    )
    def test_any_task_field_change_is_detected(self, field_over):
        assert task_args_changed(self._args(), self._args(**field_over)) is True

    def test_whitespace_only_difference_is_not_a_change(self):
        # Normalized (stripped) so cosmetic whitespace never triggers a needless re-register.
        assert (
            task_args_changed(
                self._args(output_dir="/out"),
                self._args(output_dir="  /out  "),
            )
            is False
        )

    def test_non_task_field_absent_from_predicate(self):
        # SFTP host/user/remote/port are NOT task-baked — they can't even be expressed in
        # TaskArgs, so a change to them can never force a re-register (they're read at run time).
        fields = set(vars(self._args()))
        assert fields == {"input_dir", "output_dir", "sis_type", "sftp_enabled", "run_time"}


# --------------------------------------------------------------------------- #
# Persisted last-REGISTERED TaskArgs — the durable reconcile baseline (0034 S3-d)
# --------------------------------------------------------------------------- #
class TestTaskArgsPersistedRoundTrip:
    def _args(self, **over) -> TaskArgs:
        base = {
            "input_dir": "/in",
            "output_dir": "/out",
            "sis_type": "myedbc",
            "sftp_enabled": True,
            "run_time": "03:00",
        }
        base.update(over)
        return TaskArgs.of(**base)

    def test_round_trip_is_lossless(self):
        args = self._args()
        assert task_args_from_persisted(task_args_to_persisted(args)) == args

    def test_serialized_form_is_json_shaped(self):
        # The record lives in config.json — plain str/bool values only, exactly the 5 fields.
        raw = task_args_to_persisted(self._args())
        assert set(raw) == {"input_dir", "output_dir", "sis_type", "sftp_enabled", "run_time"}
        assert all(isinstance(v, (str, bool)) for v in raw.values())

    def test_round_trip_survives_a_district_switch_comparison(self):
        # The load-bearing S3-d scenario: the persisted record (old district) differs from a
        # pending snapshot built after a Mapping switch — the reconcile must see a change.
        registered = task_args_from_persisted(task_args_to_persisted(self._args(sis_type="myedbc")))
        pending = self._args(sis_type="sd48myedbc")
        assert registered is not None
        assert task_args_changed(registered, pending) is True

    @pytest.mark.parametrize(
        "raw",
        [
            None,  # no record (pre-field installs)
            "not a dict",
            42,
            {},  # empty
            {"input_dir": "/in"},  # missing fields
            {  # wrong-typed string field
                "input_dir": 5,
                "output_dir": "/out",
                "sis_type": "myedbc",
                "sftp_enabled": True,
                "run_time": "03:00",
            },
            {  # wrong-typed bool field
                "input_dir": "/in",
                "output_dir": "/out",
                "sis_type": "myedbc",
                "sftp_enabled": "yes",
                "run_time": "03:00",
            },
        ],
    )
    def test_unusable_record_returns_none_never_raises(self, raw):
        # Defensive-total by design: a hand-edited/absent record reads as "no record" (None) —
        # the honest UNKNOWN the reconcile acts on (W3-C) — instead of crashing Settings.
        assert task_args_from_persisted(raw) is None

    def test_extra_keys_are_ignored(self):
        raw = task_args_to_persisted(self._args())
        raw["future_field"] = "ignored"
        assert task_args_from_persisted(raw) == self._args()

    def test_values_normalize_like_a_live_snapshot(self):
        # Persisted values run through TaskArgs.of, so cosmetic whitespace never reads as a change.
        raw = task_args_to_persisted(self._args())
        raw["output_dir"] = "  /out  "
        assert task_args_from_persisted(raw) == self._args()


# --------------------------------------------------------------------------- #
# The durable registered-schedule record + the reconcile decision (W3-C).        #
# The record is the ONLY reconcile baseline — an absent one is UNKNOWN, never    #
# silently "up to date" (the mount-snapshot fallback's silent no-op).            #
# --------------------------------------------------------------------------- #
def _task_args(**over) -> TaskArgs:
    base = {
        "input_dir": "/in",
        "output_dir": "/out",
        "sis_type": "myedbc",
        "sftp_enabled": False,
        "run_time": "03:00",
    }
    base.update(over)
    return TaskArgs.of(**base)


class TestRegisteredSchedule:
    def test_a_usable_record_carries_both_facts(self):
        record = registered_schedule(
            raw_task_args=task_args_to_persisted(_task_args()),
            unattended_flag=True,
            supports_unattended=True,
        )
        assert record == RegisteredSchedule(args=_task_args(), unattended=True)

    @pytest.mark.parametrize("raw", [None, "not a dict", {}, {"input_dir": "/in"}])
    def test_an_absent_or_garbled_record_is_unknown_on_BOTH_facts(self, raw):
        # The record is ATOMIC (both facets are written by the same confirmed register and
        # cleared by the same unregister): no args record ⇒ the unattended flag is equally
        # un-evidenced, so it must read unknown rather than its False default.
        record = registered_schedule(raw_task_args=raw, unattended_flag=False, supports_unattended=True)
        assert record.args is None
        assert record.unattended is None

    def test_an_unknown_record_is_not_unattended_where_the_platform_has_no_logon_type(self):
        # cron has no logon type — there is nothing an unproven re-register could downgrade, so
        # "unknown" would only produce a nonsense Windows-password prompt.
        record = registered_schedule(raw_task_args=None, unattended_flag=False, supports_unattended=False)
        assert record.unattended is False

    def test_a_RECORDED_unattended_fact_is_honored_regardless_of_platform_capability(self):
        # A durable recorded fact is evidence — it is never overridden by the running platform's
        # capability (a config can travel; only the INFERENCE is capability-gated).
        record = registered_schedule(
            raw_task_args=task_args_to_persisted(_task_args()),
            unattended_flag=True,
            supports_unattended=False,
        )
        assert record.unattended is True


class TestScheduleReconcile:
    def test_no_registered_task_needs_no_reconcile(self):
        record = registered_schedule(raw_task_args=task_args_to_persisted(_task_args()), unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=False, record=record, pending=_task_args(sis_type="sd48myedbc"))
            is ScheduleReconcile.NO_TASK
        )

    def test_a_matching_record_is_up_to_date(self):
        record = registered_schedule(raw_task_args=task_args_to_persisted(_task_args()), unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=True, record=record, pending=_task_args())
            is ScheduleReconcile.UP_TO_DATE
        )

    def test_a_cosmetic_whitespace_difference_is_still_up_to_date(self):
        record = registered_schedule(raw_task_args=task_args_to_persisted(_task_args()), unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=True, record=record, pending=_task_args(output_dir="  /out  "))
            is ScheduleReconcile.UP_TO_DATE
        )

    def test_a_changed_record_reregisters(self):
        record = registered_schedule(raw_task_args=task_args_to_persisted(_task_args()), unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=True, record=record, pending=_task_args(sis_type="sd48myedbc"))
            is ScheduleReconcile.REREGISTER
        )

    def test_an_ABSENT_record_reregisters_even_when_pending_matches_the_current_config(self):
        # THE W3-C regression: after a Mapping district switch the config on disk ALREADY carries
        # the new district, so any baseline derived from the current config equals `pending` and
        # the reconcile silently does nothing — while the live task still bakes the OLD district.
        # With no durable record the app cannot know what the task carries, so it must act.
        record = registered_schedule(raw_task_args=None, unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=True, record=record, pending=_task_args(sis_type="sd48myedbc"))
            is ScheduleReconcile.REREGISTER
        )

    def test_an_absent_record_with_no_task_registered_is_still_NO_TASK(self):
        record = registered_schedule(raw_task_args=None, unattended_flag=False)
        assert (
            schedule_reconcile(schedule_registered=False, record=record, pending=_task_args())
            is ScheduleReconcile.NO_TASK
        )


class TestDowngradeInterruptOnAnUnknownRecord:
    def test_an_unknown_logon_type_without_a_password_interrupts(self):
        # Never silently replace a possibly-unattended task with a logged-on-only one: on a
        # district server nobody is signed in, so that would stop the nightly sync entirely.
        assert downgrade_interrupt(registered_unattended=None, password_supplied=False) is not None

    def test_an_unknown_logon_type_with_a_password_supplied_proceeds(self):
        # A supplied password keeps the task unattended either way — nothing can be downgraded.
        assert downgrade_interrupt(registered_unattended=None, password_supplied=True) is None

    def test_the_unknown_copy_never_asserts_a_state_it_did_not_check(self):
        interrupt = downgrade_interrupt(registered_unattended=None, password_supplied=False)
        known = DowngradeInterrupt()
        assert interrupt is not None
        assert interrupt.headline != known.headline
        assert interrupt.detail != known.detail
        # The KNOWN-unattended copy asserts the current state; the unknown variant must not.
        assert "currently runs" in known.detail
        assert "currently runs" not in interrupt.detail
        assert "can't tell" in interrupt.detail

    def test_the_unknown_variant_offers_the_same_three_choices(self):
        # The view renders the labels straight off the interrupt — the choices must not diverge.
        interrupt = downgrade_interrupt(registered_unattended=None, password_supplied=False)
        known = DowngradeInterrupt()
        assert interrupt is not None
        assert interrupt.keep_unattended_label == known.keep_unattended_label
        assert interrupt.signed_in_only_label == known.signed_in_only_label
        assert interrupt.cancel_label == known.cancel_label


# --------------------------------------------------------------------------- #
# Settings Save run-time persistence — config, not a register side-effect (0034 S3-b)
# --------------------------------------------------------------------------- #
class TestRunTimeSaveDecision:
    def test_unchanged_value_persists_nothing(self):
        assert run_time_save_decision(saved_run_time="03:00", field_run_time="03:00") == RunTimeSaveDecision(
            persist=None, invalid=False
        )

    def test_whitespace_only_difference_is_unchanged(self):
        assert run_time_save_decision(saved_run_time="03:00", field_run_time="  03:00  ") == RunTimeSaveDecision(
            persist=None, invalid=False
        )

    def test_valid_edit_persists_the_normalized_value(self):
        assert run_time_save_decision(saved_run_time="03:00", field_run_time=" 04:30 ") == RunTimeSaveDecision(
            persist="04:30", invalid=False
        )

    @pytest.mark.parametrize("bad", ["25:00", "03:60", "3 am", "0300", ""])
    def test_invalid_edit_flags_invalid_and_persists_nothing(self, bad):
        decision = run_time_save_decision(saved_run_time="03:00", field_run_time=bad)
        assert decision == RunTimeSaveDecision(persist=None, invalid=True)


# --------------------------------------------------------------------------- #
# Reconcile downgrade interrupt — no silent logon-type downgrade (0034 S3-a)     #
# --------------------------------------------------------------------------- #
class TestDowngradeInterrupt:
    @pytest.mark.parametrize(
        ("registered_unattended", "password_supplied", "interrupts"),
        [
            (True, False, True),  # THE case: unattended task + blank password → must ask
            (True, True, False),  # password supplied → stays unattended, no downgrade possible
            (False, False, False),  # was never unattended → nothing to downgrade
            (False, True, False),
        ],
    )
    def test_interrupt_truth_table(self, registered_unattended, password_supplied, interrupts):
        result = downgrade_interrupt(registered_unattended=registered_unattended, password_supplied=password_supplied)
        assert (result is not None) is interrupts

    def test_choice_copy_is_byte_pinned(self):
        # Owner-approved copy (2026-07-15) — the two explicit choices, verbatim; calm framing;
        # no default that downgrades silently; cancel = no change.
        interrupt = downgrade_interrupt(registered_unattended=True, password_supplied=False)
        assert interrupt == DowngradeInterrupt()
        assert interrupt.headline == "Keep the nightly sync running when you're signed out?"
        assert interrupt.detail == (
            "Your nightly schedule currently runs whether or not anyone is signed in. "
            "Updating it without your Windows password would change it to run only while you're signed in."
        )
        assert interrupt.keep_unattended_label == "Keep running when signed out — re-enter the Windows password"
        assert interrupt.signed_in_only_label == "Continue — the sync will only run while signed in"
        assert interrupt.cancel_label == "Cancel"
        assert interrupt.keep_next_headline == "Enter your Windows password to update the schedule"
        assert interrupt.keep_next_detail == (
            "Type your Windows account password below, then choose Schedule nightly sync — your new "
            "settings will apply and the sync will keep running when you're signed out."
        )
        assert interrupt.cancelled_headline == "Schedule not updated"
        assert interrupt.cancelled_detail == (
            "Your settings are saved, but the nightly schedule still runs with your previous settings. "
            "Save again whenever you're ready to update it."
        )

    def test_interrupt_carries_no_password_shaped_field(self):
        # I1/I3 structural guard: the interrupt is COPY only — a password can't even ride it.
        interrupt = DowngradeInterrupt()
        assert all(isinstance(v, str) for v in vars(interrupt).values())
        assert "password" not in {k.lower() for k in vars(interrupt)}


# --------------------------------------------------------------------------- #
# Reconcile-outcome Save note — honest, never "updating…" for an interrupt.     #
# --------------------------------------------------------------------------- #
class TestReconcileSaveNote:
    def test_folders_note_dispatched_claims_updating(self):
        # A genuinely dispatched re-register earns the optimistic "updating the schedule" note.
        assert folders_save_note(ReconcileOutcome.DISPATCHED) == "Saved — updating the nightly schedule to match…"

    def test_folders_note_interrupted_is_honest_and_defers(self):
        # The reconcile only opened the downgrade dialog — nothing is updating yet, so the note must
        # NOT claim the schedule is updating; it points the admin at the open choice.
        note = folders_save_note(ReconcileOutcome.INTERRUPTED)
        assert note == "Saved — confirm the schedule choice above."
        assert "updating" not in note.lower()

    def test_folders_note_none_is_plain_saved(self):
        assert folders_save_note(ReconcileOutcome.NONE) == "Saved."

    def test_folders_note_always_leads_with_saved(self):
        # "Saved" is truthful for every outcome — the config fields DID persist regardless of what
        # the schedule reconcile did (only the schedule clause is conditioned on the outcome).
        for outcome in ReconcileOutcome:
            assert folders_save_note(outcome).startswith("Saved")

    def test_sftp_suffix_dispatched_claims_delivering(self):
        assert sftp_reconcile_suffix(ReconcileOutcome.DISPATCHED) == " Updating the nightly schedule to deliver too…"

    def test_sftp_suffix_interrupted_is_honest_and_defers(self):
        suffix = sftp_reconcile_suffix(ReconcileOutcome.INTERRUPTED)
        assert suffix == " Confirm the schedule choice above to update the nightly sync."
        assert "updating" not in suffix.lower()

    def test_sftp_suffix_none_is_empty(self):
        # No live task to update → the base "Delivery settings saved" note stands alone.
        assert sftp_reconcile_suffix(ReconcileOutcome.NONE) == ""

    def test_folders_note_blocked_is_honest_and_names_the_fix(self):
        # The register flow early-returned (e.g. a malformed run time) — nothing dispatched, so
        # the note must NOT claim "updating…"; it says the schedule wasn't updated + names the fix.
        note = folders_save_note(ReconcileOutcome.BLOCKED)
        assert note == (
            "Saved — the nightly schedule wasn't updated. "
            "Fix the run time in the Daily schedule section, then save again."
        )
        assert "updating" not in note.lower()

    def test_sftp_suffix_blocked_is_honest_and_names_the_fix(self):
        suffix = sftp_reconcile_suffix(ReconcileOutcome.BLOCKED)
        assert suffix == (
            " The nightly schedule wasn't updated — fix the run time in the Daily schedule section, then save again."
        )
        assert "updating" not in suffix.lower()

    def test_reconcile_outcome_members_are_the_four_states(self):
        assert {o.value for o in ReconcileOutcome} == {"dispatched", "interrupted", "blocked", "none"}


# --------------------------------------------------------------------------- #
# District auto-select — iff exactly one config (D9)                            #
# --------------------------------------------------------------------------- #
class TestAutoSelectDistrict:
    def test_single_config_auto_selects(self):
        assert auto_selected_district(["myedbc"]) == "myedbc"

    def test_multiple_configs_no_pre_selection(self):
        assert auto_selected_district(["myedbc", "sd48myedbc", "sd40myedbc"]) == ""

    def test_zero_configs_no_pre_selection(self):
        assert auto_selected_district([]) == ""
