"""Tests for src/ui_flet/nav.py — the pure, state-aware navigation model.

The prominence model is trust-critical, so it's built + tested here; IA-1 renders
it as a reordered flat rail via ``ordered_destinations`` + ``prominent_initial_id``
(the pure render-ordering helpers, consumed by ``nav_rail``).
"""

from __future__ import annotations

from src.config.app_config import AppConfig
from src.ui_flet.nav import (
    DESTINATIONS,
    Destination,
    NavGroup,
    NavModel,
    nav_model,
    ordered_destinations,
    prominent_initial_id,
)

_EXPECTED_IDS = {"home", "convert", "run_history", "setup", "mapping", "help"}

_CONFIGURED_SCHEDULED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)


class TestDestinationSet:
    def test_destination_set_is_complete(self):
        assert {d.id for d in DESTINATIONS} == _EXPECTED_IDS

    def test_destination_ids_are_unique(self):
        ids = [d.id for d in DESTINATIONS]
        assert len(ids) == len(set(ids))

    def test_every_destination_has_a_known_group(self):
        for dest in DESTINATIONS:
            assert dest.group in NavGroup

    def test_labels_are_plain_language_not_raw_ids(self):
        for dest in DESTINATIONS:
            assert dest.label
            assert dest.label != dest.id  # never surface a raw id to the user


class TestProminence:
    def test_unconfigured_leads_with_get_started(self):
        model = nav_model(AppConfig())
        assert model.prominent_group is NavGroup.GET_STARTED

    def test_configured_but_unscheduled_still_get_started(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        assert cfg.is_complete()  # paths/SIS present...
        assert nav_model(cfg).prominent_group is NavGroup.GET_STARTED  # ...but no schedule yet

    def test_configured_and_scheduled_leads_with_everyday(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)
        assert nav_model(cfg).prominent_group is NavGroup.EVERYDAY


class TestGrouping:
    def test_groups_partition_all_destinations(self):
        model = nav_model(AppConfig())
        flattened = [d for group in NavGroup for d in model.groups[group]]
        assert {d.id for d in flattened} == _EXPECTED_IDS

    def test_groups_cover_every_navgroup_key(self):
        model = nav_model(AppConfig())
        assert set(model.groups.keys()) == set(NavGroup)

    def test_model_destinations_match_module_constant(self):
        assert nav_model(AppConfig()).destinations == DESTINATIONS


class TestOrderedDestinations:
    def test_unconfigured_leads_with_get_started_setup(self):
        ordered = ordered_destinations(nav_model(AppConfig()))
        assert ordered[0].group is NavGroup.GET_STARTED
        assert ordered[0].id == "setup"

    def test_configured_and_scheduled_leads_with_everyday_home(self):
        ordered = ordered_destinations(nav_model(_CONFIGURED_SCHEDULED))
        assert ordered[0].group is NavGroup.EVERYDAY
        assert ordered[0].id == "home"

    def test_ordering_is_a_permutation_none_dropped(self):
        # No destination is lost by reordering — every live state is a permutation.
        assert {d.id for d in ordered_destinations(nav_model(AppConfig()))} == _EXPECTED_IDS
        assert {d.id for d in ordered_destinations(nav_model(_CONFIGURED_SCHEDULED))} == _EXPECTED_IDS

    def test_within_group_order_preserved(self):
        # Everyday's declared order (home, convert, run_history) survives the reorder.
        ordered = ordered_destinations(nav_model(_CONFIGURED_SCHEDULED))
        everyday = [d.id for d in ordered if d.group is NavGroup.EVERYDAY]
        assert everyday == ["home", "convert", "run_history"]

    def test_empty_groups_are_dropped(self):
        # A hand-built model with two empty groups collapses to just the non-empty one.
        home = Destination("home", "Home", "HOME_OUTLINED", "HOME_ROUNDED", NavGroup.EVERYDAY)
        model = NavModel(
            destinations=(home,),
            groups={NavGroup.GET_STARTED: (), NavGroup.EVERYDAY: (home,), NavGroup.ADVANCED: ()},
            prominent_group=NavGroup.GET_STARTED,  # prominent group is empty
        )
        assert ordered_destinations(model) == (home,)


class TestProminentInitialId:
    def test_unconfigured_initial_is_setup(self):
        assert prominent_initial_id(nav_model(AppConfig())) == "setup"

    def test_configured_and_scheduled_initial_is_home(self):
        assert prominent_initial_id(nav_model(_CONFIGURED_SCHEDULED)) == "home"

    def test_empty_prominent_group_falls_back_to_first_ordered(self):
        # Total: an empty prominent group falls back to the first ordered destination.
        home = Destination("home", "Home", "HOME_OUTLINED", "HOME_ROUNDED", NavGroup.EVERYDAY)
        model = NavModel(
            destinations=(home,),
            groups={NavGroup.GET_STARTED: (), NavGroup.EVERYDAY: (home,), NavGroup.ADVANCED: ()},
            prominent_group=NavGroup.GET_STARTED,  # empty → fall back
        )
        assert prominent_initial_id(model) == "home"

    def test_no_destinations_returns_empty_string(self):
        # Total: no destinations at all → "" (never raises).
        model = NavModel(
            destinations=(),
            groups={NavGroup.GET_STARTED: (), NavGroup.EVERYDAY: (), NavGroup.ADVANCED: ()},
            prominent_group=NavGroup.GET_STARTED,
        )
        assert prominent_initial_id(model) == ""
