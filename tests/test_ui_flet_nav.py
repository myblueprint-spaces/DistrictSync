"""Tests for src/ui_flet/nav.py — the pure, state-aware navigation model.

The rail renders FLAT at PLAT-1, but the prominence model is trust-critical so
it's built + tested now; its render wiring lands at IA-1.
"""

from __future__ import annotations

from src.config.app_config import AppConfig
from src.ui_flet.nav import DESTINATIONS, NavGroup, nav_model

_EXPECTED_IDS = {"home", "convert", "run_history", "setup", "mapping", "help"}


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
