"""Tests for src/ui_flet/nav.py — the pure navigation model.

D7 (Slice 3): the rail order is **FIXED** — Home, Convert, Run History, Setup,
Mapping, Help — in every state (spatial memory is a trust property; no
state-dependent reordering). The ONLY state-aware decision is the launch
selection: Setup while ``needs_setup``, else Home. The shell renders this via
``ordered_destinations`` + ``prominent_initial_id`` + ``selected_index_for``
(consumed by ``nav_rail`` / ``shell`` — the last is the single-source rail-index
mapping used for both the initial highlight and programmatic-navigation sync).
"""

from __future__ import annotations

from src.config.app_config import AppConfig
from src.ui_flet.nav import (
    DESTINATIONS,
    NavModel,
    nav_model,
    needs_setup,
    ordered_destinations,
    prominent_initial_id,
    selected_index_for,
)

_EXPECTED_IDS = {"home", "convert", "run_history", "setup", "mapping", "help"}
_FIXED_ORDER = ["home", "convert", "run_history", "setup", "mapping", "help"]

_UNCONFIGURED = AppConfig()
_CONFIGURED_UNSCHEDULED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
_CONFIGURED_SCHEDULED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)


class TestDestinationSet:
    def test_destination_set_is_complete(self):
        assert {d.id for d in DESTINATIONS} == _EXPECTED_IDS

    def test_destination_ids_are_unique(self):
        ids = [d.id for d in DESTINATIONS]
        assert len(ids) == len(set(ids))

    def test_labels_are_plain_language_not_raw_ids(self):
        for dest in DESTINATIONS:
            assert dest.label
            assert dest.label != dest.id  # never surface a raw id to the user

    def test_declared_order_is_the_fixed_rail_order(self):
        assert [d.id for d in DESTINATIONS] == _FIXED_ORDER


class TestNeedsSetup:
    """THE single-sourced onboarding predicate — the Home dispatcher + launch selection call it.

    Re-keyed in Slice 5 (D4a) to ``AppConfig.has_completed_setup()`` — the durable finish-line
    fact — so a Firefighter whose task later breaks is never dropped back into onboarding.
    """

    def test_unconfigured_needs_setup(self):
        assert needs_setup(AppConfig()) is True

    def test_configured_but_unscheduled_needs_setup(self):
        assert _CONFIGURED_UNSCHEDULED.is_complete()  # paths/SIS present...
        assert needs_setup(_CONFIGURED_UNSCHEDULED) is True  # ...but never finished setup → still needs it

    def test_configured_and_scheduled_does_not_need_setup(self):
        # Inferred finish line (complete + scheduled) — no onboarding.
        assert needs_setup(_CONFIGURED_SCHEDULED) is False

    def test_explicit_setup_completed_survives_a_broken_schedule(self):
        # The Event-141 firefighter: completed once, schedule later gone — NOT a newcomer.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        assert needs_setup(cfg) is False


class TestFixedOrder:
    """D7: the rail order is identical in EVERY state — no state-dependent reordering."""

    def test_order_is_fixed_across_all_states(self):
        for cfg in (_UNCONFIGURED, _CONFIGURED_UNSCHEDULED, _CONFIGURED_SCHEDULED):
            assert [d.id for d in ordered_destinations(nav_model(cfg))] == _FIXED_ORDER

    def test_order_is_identical_between_needs_setup_states(self):
        # The load-bearing D7 guarantee: needs-setup and fully-configured render the
        # SAME order (byte-identical tuple), not merely the same membership.
        needs = ordered_destinations(nav_model(_UNCONFIGURED))
        done = ordered_destinations(nav_model(_CONFIGURED_SCHEDULED))
        assert needs == done

    def test_ordered_matches_module_destinations(self):
        assert ordered_destinations(nav_model(_UNCONFIGURED)) == DESTINATIONS

    def test_model_destinations_match_module_constant(self):
        assert nav_model(_UNCONFIGURED).destinations == DESTINATIONS

    def test_no_destination_is_dropped_in_any_state(self):
        for cfg in (_UNCONFIGURED, _CONFIGURED_UNSCHEDULED, _CONFIGURED_SCHEDULED):
            assert {d.id for d in ordered_destinations(nav_model(cfg))} == _EXPECTED_IDS


class TestProminentInitialId:
    """Launch selection: Setup while ``needs_setup``, else the first destination (Home).

    (Slice 5 (D4a) re-keyed ``needs_setup`` → durable ``has_completed_setup()``; the fixed
    order above does not depend on that split.)
    """

    def test_unconfigured_initial_is_setup(self):
        assert prominent_initial_id(nav_model(_UNCONFIGURED)) == "setup"

    def test_configured_but_unscheduled_initial_is_setup(self):
        assert prominent_initial_id(nav_model(_CONFIGURED_UNSCHEDULED)) == "setup"

    def test_configured_and_scheduled_initial_is_home(self):
        assert prominent_initial_id(nav_model(_CONFIGURED_SCHEDULED)) == "home"

    def test_initial_is_always_a_real_destination(self):
        for cfg in (_UNCONFIGURED, _CONFIGURED_UNSCHEDULED, _CONFIGURED_SCHEDULED):
            initial = prominent_initial_id(nav_model(cfg))
            assert initial in {d.id for d in ordered_destinations(nav_model(cfg))}

    def test_empty_destination_model_returns_empty_string(self):
        # Total: a hand-built model with no launch id degrades to "" (never raises).
        model = NavModel(destinations=(), initial_id="")
        assert prominent_initial_id(model) == ""


class TestSelectedIndexFor:
    """The single-source rail-index mapping — used for the rail's INITIAL highlight AND
    the shell's programmatic-navigation highlight sync, so the two can never drift."""

    def test_each_id_maps_to_its_fixed_order_index(self):
        ordered = ordered_destinations(nav_model(_UNCONFIGURED))
        for expected_index, dest in enumerate(ordered):
            assert selected_index_for(dest.id, ordered) == expected_index

    def test_setup_index_matches_fixed_order(self):
        ordered = ordered_destinations(nav_model(_UNCONFIGURED))
        assert selected_index_for("setup", ordered) == _FIXED_ORDER.index("setup")

    def test_unknown_id_falls_back_to_zero(self):
        ordered = ordered_destinations(nav_model(_UNCONFIGURED))
        assert selected_index_for("does_not_exist", ordered) == 0

    def test_empty_ordered_falls_back_to_zero(self):
        assert selected_index_for("home", ()) == 0
