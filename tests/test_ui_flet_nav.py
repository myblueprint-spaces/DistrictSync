"""Tests for src/ui_flet/nav.py — the pure navigation model.

D7 (Slice 3): the rail order is **FIXED** — Home, Convert, Run History, Setup,
Mapping, Help — in every state (spatial memory is a trust property; no
state-dependent reordering). Since 0038 S6 the launch selection is **Home in every
state** too: Home HOSTS the setup wizard, so selecting Setup would land the same
wizard one rail item away from where the admin already is. The shell renders this via
``ordered_destinations`` + ``initial_destination_id`` + ``selected_index_for``
(consumed by ``nav_rail`` / ``shell`` — the last is the single-source rail-index
mapping used for both the initial highlight and programmatic-navigation sync).

The launch-selection tests below are the pre-S6 ones INVERTED, not replaced: each
state that used to assert "Setup" now asserts "Home", so the change is visible as a
flipped expectation rather than as a deleted guarantee.
"""

from __future__ import annotations

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.nav import (
    DESTINATIONS,
    NavModel,
    initial_destination_id,
    nav_model,
    needs_setup,
    ordered_destinations,
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
    """THE single-sourced "hasn't finished setup" predicate.

    Three consumers since 0038 S6 — Home's branch (a) wizard host, ``build_setup``'s
    wizard-vs-Settings choice, and the Setup rail badge's first-run suppression. (The
    launch selection stopped being one: it is Home in every state now.)

    Re-keyed in Slice 5 (D4a) to ``AppConfig.has_completed_setup()`` — the durable finish-line
    fact — so a Firefighter whose task later breaks is never dropped back into the wizard.
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

    def test_order_is_the_fixed_rail_order(self):
        assert [d.id for d in ordered_destinations(nav_model())] == _FIXED_ORDER

    def test_the_model_factory_accepts_no_state_at_all(self):
        """The load-bearing D7 guarantee, now STRUCTURAL — and the only falsifiable form of it.

        "Needs-setup and fully-configured render the same rail" stopped being a coincidence
        two call sites share when ``nav_model`` lost its config parameter: there is nothing
        left for a state to change. Re-admitting one (even unused and defaulted) re-opens the
        door 0038 S6 closed, and every behavioural assertion in this class stays green while
        it stands open — so the ABSENCE of the parameter is what gets asserted.

        The pin is the signature deliberately. An earlier ``nav_model() == nav_model()``
        stood here and could not fail for any deterministic factory (vacuous green, per
        CLAUDE.md's testing conventions); its second line duplicated
        ``test_model_destinations_match_module_constant`` verbatim, since
        ``ordered_destinations`` returns ``model.destinations`` unchanged.
        """
        import inspect

        assert list(inspect.signature(nav_model).parameters) == []

    def test_model_destinations_match_module_constant(self):
        assert nav_model().destinations == DESTINATIONS

    def test_no_destination_is_dropped(self):
        assert {d.id for d in ordered_destinations(nav_model())} == _EXPECTED_IDS


class TestInitialDestinationId:
    """Launch selection: **Home, in every state** (0038 S6 — Home hosts the wizard).

    These four are the pre-S6 launch-selection tests INVERTED. Two of them asserted
    ``"setup"`` for an unfinished install; both now assert ``"home"``, because selecting
    Setup would put the admin one rail item away from the very wizard Home is showing them
    — and would leave the rail's "you are here" pointing at a surface they are not on.
    """

    def test_unconfigured_initial_is_home(self):
        # WAS "setup" — the newcomer no longer starts on another rail item.
        assert needs_setup(_UNCONFIGURED) is True
        assert initial_destination_id(nav_model()) == "home"

    def test_configured_but_unscheduled_initial_is_home(self):
        # WAS "setup" — upgrade shape 2 (complete, never scheduled) lands on Home too.
        assert needs_setup(_CONFIGURED_UNSCHEDULED) is True
        assert initial_destination_id(nav_model()) == "home"

    def test_configured_and_scheduled_initial_is_home(self):
        assert needs_setup(_CONFIGURED_SCHEDULED) is False
        assert initial_destination_id(nav_model()) == "home"

    def test_an_unreadable_profile_also_lands_on_home(self):
        # G2's rail half: the state where ``needs_setup`` is False WITHOUT the finish line
        # having been reached kept a separate path pre-S6; now every state shares one.
        assert initial_destination_id(nav_model()) == "home"
        assert needs_setup(AppConfig(load_state=ConfigLoadState.UNREADABLE)) is False

    def test_initial_is_always_a_real_destination(self):
        assert initial_destination_id(nav_model()) in {d.id for d in ordered_destinations(nav_model())}

    def test_empty_destination_model_returns_empty_string(self):
        # Total: a hand-built model with no destinations degrades to "" (never raises).
        assert initial_destination_id(NavModel(destinations=())) == ""


class TestSelectedIndexFor:
    """The single-source rail-index mapping — used for the rail's INITIAL highlight AND
    the shell's programmatic-navigation highlight sync, so the two can never drift."""

    def test_each_id_maps_to_its_fixed_order_index(self):
        ordered = ordered_destinations(nav_model())
        for expected_index, dest in enumerate(ordered):
            assert selected_index_for(dest.id, ordered) == expected_index

    def test_setup_index_matches_fixed_order(self):
        ordered = ordered_destinations(nav_model())
        assert selected_index_for("setup", ordered) == _FIXED_ORDER.index("setup")

    def test_unknown_id_falls_back_to_zero(self):
        ordered = ordered_destinations(nav_model())
        assert selected_index_for("does_not_exist", ordered) == 0

    def test_empty_ordered_falls_back_to_zero(self):
        assert selected_index_for("home", ()) == 0
