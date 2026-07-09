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
    FlowInputs,
    SetupStep,
    TaskArgs,
    auto_selected_district,
    can_advance,
    derive_flow,
    finish_copy,
    is_skippable,
    next_step,
    prev_step,
    step_number,
    task_args_changed,
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
        assert TOTAL_STEPS == 5
        assert STEP_ORDER == (
            SetupStep.FOLDERS,
            SetupStep.DISTRICT,
            SetupStep.SCHEDULE,
            SetupStep.DELIVERY,
            SetupStep.FINISH,
        )

    @pytest.mark.parametrize(
        ("step", "number"),
        [
            (SetupStep.FOLDERS, 1),
            (SetupStep.DISTRICT, 2),
            (SetupStep.SCHEDULE, 3),
            (SetupStep.DELIVERY, 4),
            (SetupStep.FINISH, 5),
        ],
    )
    def test_step_number_is_one_based(self, step, number):
        assert step_number(step) == number

    def test_next_and_prev_step_walk_the_order(self):
        assert next_step(SetupStep.FOLDERS) is SetupStep.DISTRICT
        assert next_step(SetupStep.DELIVERY) is SetupStep.FINISH
        assert next_step(SetupStep.FINISH) is None
        assert prev_step(SetupStep.DISTRICT) is SetupStep.FOLDERS
        assert prev_step(SetupStep.FOLDERS) is None

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
    def test_fresh_install_resumes_at_folders(self):
        assert derive_flow(_inputs()).resume_step is SetupStep.FOLDERS

    def test_folders_valid_only_resumes_at_district(self):
        state = derive_flow(_inputs(folders_valid=True))
        assert state.resume_step is SetupStep.DISTRICT
        assert SetupStep.FOLDERS in state.satisfied

    def test_folders_and_district_resume_at_schedule(self):
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True))
        assert state.resume_step is SetupStep.SCHEDULE

    def test_schedule_live_but_delivery_pending_resumes_at_delivery(self):
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, schedule=_LIVE))
        assert state.resume_step is SetupStep.DELIVERY
        assert SetupStep.SCHEDULE in state.satisfied

    def test_schedule_skipped_resumes_at_delivery(self):
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, schedule_skipped=True))
        assert state.resume_step is SetupStep.DELIVERY

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
        state = derive_flow(_inputs(folders_valid=True, district_chosen=True, schedule=status))
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
            delivery_tested_ok=False,
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
            delivery_tested_ok=False,
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
            delivery_tested_ok=False,
            district="New Westminster",
            schedule_time_display=None,
            host="",
            username="",
        )
        assert detail == (
            "Tonight DistrictSync will build New Westminster into your "
            "output folder. Set up delivery whenever you're ready."
        )

    def test_sftp_tested_variant_with_time(self):
        headline, detail = finish_copy(
            schedule_live=True,
            delivery_tested_ok=True,
            district="New Westminster",
            schedule_time_display="3:00 AM",
            host="sftp.ca.spacesedu.com",
            username="district_x",
        )
        assert headline == "You're all set"
        assert detail == (
            "Tonight at 3:00 AM DistrictSync will build New Westminster and try to "
            "deliver to SpacesEDU — we tested the connection to sftp.ca.spacesedu.com "
            "as district_x just now and it worked."
        )

    def test_sftp_tested_variant_timeless(self):
        _, detail = finish_copy(
            schedule_live=True,
            delivery_tested_ok=True,
            district="New Westminster",
            schedule_time_display=None,
            host="sftp.ca.spacesedu.com",
            username="district_x",
        )
        assert detail == (
            "Tonight DistrictSync will build New Westminster and try to "
            "deliver to SpacesEDU — we tested the connection to sftp.ca.spacesedu.com "
            "as district_x just now and it worked."
        )

    def test_schedule_skipped_wins_over_delivery_state(self):
        # When the schedule was skipped, the copy never claims "tonight" even if a credential
        # was tested — no schedule means no nightly run to promise.
        _, detail = finish_copy(
            schedule_live=False,
            delivery_tested_ok=True,
            district="Sea to Sky",
            schedule_time_display="3:00 AM",
            host="h",
            username="u",
        )
        assert detail.startswith("DistrictSync will build Sea to Sky when you run a conversion.")
        assert "Tonight" not in detail


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
# District auto-select — iff exactly one config (D9)                            #
# --------------------------------------------------------------------------- #
class TestAutoSelectDistrict:
    def test_single_config_auto_selects(self):
        assert auto_selected_district(["myedbc"]) == "myedbc"

    def test_multiple_configs_no_pre_selection(self):
        assert auto_selected_district(["myedbc", "sd48myedbc", "sd40myedbc"]) == ""

    def test_zero_configs_no_pre_selection(self):
        assert auto_selected_district([]) == ""
