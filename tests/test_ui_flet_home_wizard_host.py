"""Home hosts the setup wizard (plan 0038 S6).

Two halves, written in this order on purpose (the charter's recorded approach for a
lifecycle/host refactor + view glue):

1. **Characterisation** (``TestTheFirstRunPromisesThatMustSurvive``) — the properties the
   PRE-S6 app already had, written and observed GREEN against the old two-surface shape
   (an unconfigured launch selecting the Setup rail item, whose wizard graduates to
   Settings in place) BEFORE Home became the host. A refactor whose only evidence is a
   green end state proves nothing about what it preserved.
2. **The host itself** — the branch-(a) swap, the state-aware welcome band, the
   ``on_complete`` save-verify seam, and the promises that are only checkable through the
   assembled surface (the rail item and the Home host run the SAME wizard; a raising
   finish-save keeps the summary on screen instead of bouncing to step 1).

``screens/home.py`` and ``screens/setup.py`` are coverage-omitted view glue, so these
assert through the real control tree. ``probe_schedule`` is stubbed on every build (the
real probe spawns PowerShell) and ``components.ErrorCard`` is spied as a MODULE attribute
— the contract every render smoke in this repo relies on.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet import components
from src.ui_flet import home_status as home_status_mod
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.screens import home as home_screen
from src.ui_flet.screens import setup as setup_screen
from src.ui_flet.screens.home import build_home
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.setup_flow import TRANSITION_CUE

DISTRICT_STEP_TITLE = "Choose your district"
FINISH_BUTTON = "Finish setup"


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No PowerShell probe, no run store — a deterministic first-run surface."""
    benign = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
    monkeypatch.setattr("src.ui_flet.schedule_probe.probe_schedule", lambda *a, **k: benign)
    monkeypatch.setattr(home_screen, "read_run_records", lambda: [])
    monkeypatch.setattr(home_screen, "store_meta", lambda: None)


def _iter_controls(control):  # noqa: ANN001, ANN202 - untyped Flet tree
    yield control
    for attr in ("controls", "content", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, ft.Control):
                yield from _iter_controls(item)


def _texts(tree) -> list[str]:  # noqa: ANN001 - untyped Flet tree
    return [c.value for c in _iter_controls(tree) if isinstance(getattr(c, "value", None), str)]


def _labels(tree) -> list[str]:  # noqa: ANN001 - untyped Flet tree
    return [
        c.content
        for c in _iter_controls(tree)
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and isinstance(c.content, str)
    ]


def _button(tree, label: str):  # noqa: ANN001, ANN202 - untyped Flet tree
    for candidate in _iter_controls(tree):
        if isinstance(candidate, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and candidate.content == label:
            return candidate
    raise AssertionError(f"no button labelled {label!r}; found: {_labels(tree)}")


def _unfinished(**over: object) -> AppConfig:
    """An install that has NOT reached the wizard's finish line — Home's branch (a)."""
    values: dict[str, object] = {"load_state": ConfigLoadState.LOADED}
    values.update(over)
    return AppConfig(**values)  # type: ignore[arg-type]


def _ready_to_finish(tmp_path: Path, **over: object) -> AppConfig:
    """Folders + district satisfied, so the wizard resumes at the Delivery step."""
    in_dir = tmp_path / "in"
    in_dir.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "input_dir": str(in_dir),
        "output_dir": str(tmp_path / "out"),
        "sis_type": "myedbc",
    }
    values.update(over)
    return _unfinished(**values)


def _drive_to_finish(tree) -> None:  # noqa: ANN001 - untyped Flet tree
    """Defer Delivery, then Schedule — the two skippable steps — landing on Finish.

    The wizard mutates its root Column in place, so the same ``tree`` is re-read after
    every press (the established idiom in ``TestWizardStepsRender``).
    """
    _button(tree, "Set up later").on_click(None)  # Delivery deferred → Schedule
    _button(tree, "Set up later").on_click(None)  # Schedule deferred → Finish
    assert "Step 5 of 5" in _texts(tree), "the wizard did not reach the finish step"


def _home(page: MagicMock, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> ft.Control:
    """Build Home under the ErrorCard spy; fail if it fell to a floor."""
    real_errorcard = components.ErrorCard
    floor: dict[str, object] = {"obj": None}

    def spy(*args: object, **kw: object) -> ft.Control:
        obj = real_errorcard(*args, **kw)  # type: ignore[arg-type]
        floor["obj"] = obj
        return obj

    monkeypatch.setattr(components, "ErrorCard", spy)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    view = build_home(page, app_config=cfg, on_navigate=lambda _d: None, **kwargs)  # type: ignore[arg-type]
    assert view is not floor["obj"], "Home fell to an ErrorCard floor — a masked render bug"
    return view


# --------------------------------------------------------------------------- #
# 1. Characterisation — observed GREEN on the pre-S6 two-surface shape         #
# --------------------------------------------------------------------------- #
class TestTheFirstRunPromisesThatMustSurvive:
    """What an unconfigured admin was already guaranteed, before Home became the host."""

    def test_an_unfinished_install_is_offered_the_wizards_district_step(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first step a newcomer meets is District — however they are routed to it."""
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _unfinished()))

        tree = build_setup(page)

        assert DISTRICT_STEP_TITLE in _texts(tree)
        assert "Step 1 of 5" in _texts(tree)

    def test_the_wizard_finish_line_graduates_in_place_with_the_transition_cue(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pressing Finish marks the install set up and swaps Settings in — no navigation.

        This is the RAIL item's behaviour and S6 must not change it: only a host that
        supplies ``on_complete`` takes the payoff somewhere else.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        saved: list[bool] = []
        monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(self.setup_completed))

        tree = build_setup(page)
        _drive_to_finish(tree)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert saved and saved[-1] is True, "the finish line must record completion"
        assert cfg.setup_completed is True
        assert "Settings" in _texts(tree), "the wizard must graduate to Settings in place"
        assert TRANSITION_CUE in _texts(tree)

    def test_needs_setup_implies_the_finish_line_was_never_reached(self) -> None:
        """The implication that keeps a wizard host from ever mounting a Settings scroll.

        Home's branch (a) keys on ``nav.needs_setup``; ``build_setup`` chooses wizard-vs-
        Settings on ``has_completed_setup()``. If the two could disagree, Home would host a
        Settings page under a "let's get you set up" band.
        """
        from src.ui_flet.nav import needs_setup

        for cfg in (
            AppConfig(),
            AppConfig(input_dir="/in"),
            AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc"),
            AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True),
            AppConfig(setup_completed=True),
            AppConfig(load_state=ConfigLoadState.UNREADABLE),
        ):
            if needs_setup(cfg):
                assert not cfg.has_completed_setup(), f"needs_setup ∧ has_completed_setup for {cfg}"

    def test_a_configured_install_still_gets_the_dashboard(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Branches (b)/(c) are untouched by the host swap — the positive twin of every
        "the wizard is absent" assertion below."""
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)

        tree = _home(page, cfg, monkeypatch)

        assert "Home" in _texts(tree), "the dashboard's page header did not paint"
        assert DISTRICT_STEP_TITLE not in _texts(tree)

    def test_the_first_paint_past_the_launch_gate_offers_the_district_step(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """Through the REAL boot: an unfinished install's first painted surface is the wizard.

        Written against the PRE-S6 routing (the rail's launch selection was Setup) and it
        must stay true when Home becomes the host — the promise is "the newcomer's first
        surface is step 1 of the wizard", not "which rail item is highlighted".
        """
        isolated_user_profile.mkdir(parents=True, exist_ok=True)
        # An answered identity, so the launch gate is already satisfied and the boot lands
        # in the app body — setup itself is still unfinished.
        (isolated_user_profile / "config.json").write_text(
            json.dumps({"identity_email": "admin@sd48.bc.ca"}), encoding="utf-8"
        )
        from src.ui_flet import shell

        shell.main(page)

        root = page.add.call_args[0][0]
        assert any(isinstance(c, ft.NavigationRail) for c in _iter_controls(root)), "the app body never mounted"
        assert DISTRICT_STEP_TITLE in _texts(root)
        assert "Step 1 of 5" in _texts(root)


# --------------------------------------------------------------------------- #
# 2. The host — branch (a) is the wizard now                                   #
# --------------------------------------------------------------------------- #
class TestHomeHostsTheWizard:
    def test_the_district_step_renders_under_the_welcome_band(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tree = _home(page, _unfinished(), monkeypatch)

        texts = _texts(tree)
        assert home_status_mod.WELCOME_FRESH in texts
        assert DISTRICT_STEP_TITLE in texts and "Step 1 of 5" in texts

    def test_there_is_no_second_front_door_pointing_somewhere_else(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The retired hero's whole shape: a "Start setup" CTA routing to another rail item.

        Asserted as an ABSENCE with its positive twin right above it — the wizard's own
        forward button IS present, so this cannot pass by branch (a) rendering nothing.
        """
        hops: list[str] = []
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _unfinished()))
        tree = build_home(page, app_config=_unfinished(), on_navigate=hops.append)

        labels = _labels(tree)
        assert "Continue" in labels, "the hosted wizard's forward button is missing"
        assert "Start setup" not in labels
        assert hops == [], "branch (a) navigated somewhere on mount"

    def test_the_band_says_finish_setting_up_over_a_run_history(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Upgrade shape 2 — complete, never scheduled, a year of manual runs behind it."""
        monkeypatch.setattr(home_screen, "read_run_records", lambda: [{"timestamp": "2026-07-01T03:00:00"}])
        cfg = _unfinished(input_dir="/in", output_dir="/out", sis_type="myedbc")

        texts = _texts(_home(page, cfg, monkeypatch))

        assert home_status_mod.WELCOME_RESUME_WITH_HISTORY in texts
        assert home_status_mod.WELCOME_FRESH not in texts
        assert not any("Welcome" in t for t in texts), "a running install was greeted as a new one"

    def test_the_band_never_claims_a_run_history_that_is_absent(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The half-configured install: choices on disk, nothing ever run."""
        texts = _texts(_home(page, _unfinished(sis_type="myedbc"), monkeypatch))

        assert home_status_mod.WELCOME_RESUME_SETTINGS_ONLY in texts
        assert not any("run history" in t for t in texts)

    def test_a_store_stamp_with_no_rows_still_reads_as_history(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The quarantine-recreated store — and the SUPPLY of ``store_created_at``.

        Added after a probe: replacing the host's ``store_created_at=_store_created_at()``
        with ``None`` left 364 tests green, because every other band row reaches its answer
        through ``records``. The pure layer knowing the rule proves nothing about the view
        passing it. This is the only row where the stamp is the sole evidence.
        """
        monkeypatch.setattr(home_screen, "store_meta", lambda: {"created_at": "2026-01-05T03:00:00"})
        cfg = _unfinished(input_dir="/in", output_dir="/out", sis_type="myedbc")

        texts = _texts(_home(page, cfg, monkeypatch))

        assert home_status_mod.WELCOME_RESUME_WITH_HISTORY in texts
        assert home_status_mod.WELCOME_RESUME_SETTINGS_ONLY not in texts

    def test_an_unreadable_run_store_is_promised_nothing(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A store we could not open is not a store we may call safe."""
        monkeypatch.setattr(home_screen, "read_run_records", lambda: None)
        cfg = _unfinished(input_dir="/in", output_dir="/out", sis_type="myedbc")

        texts = _texts(_home(page, cfg, monkeypatch))

        assert home_status_mod.WELCOME_RESUME_SETTINGS_ONLY in texts
        assert not any("run history" in t for t in texts)
        assert home_status_mod.WELCOME_FRESH not in texts, "an established install was greeted as new"

    def test_an_unreadable_profile_gets_the_dashboard_not_the_wizard(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G2's Home half, restated for the host: we could not read the settings, so we may
        not assert "you are a new user" by putting a first-run wizard in front of them."""
        cfg = AppConfig(load_state=ConfigLoadState.UNREADABLE)

        texts = _texts(_home(page, cfg, monkeypatch))

        assert DISTRICT_STEP_TITLE not in texts
        assert "Home" in texts, "the dashboard did not paint; the absence above is vacuous"

    def test_the_hosted_wizard_and_the_rail_item_are_the_same_wizard(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One wizard, two mounts — the divergence this test exists to catch is the whole
        risk of hosting a screen inside another screen.

        Compared by rendered TEXT and BUTTON LABELS on the wizard's own surface (its step
        header, its instruction copy, its buttons); the band is Home's and is excluded by
        construction, since it is not part of what ``build_setup`` returns. What this does
        NOT claim is that S6 preserved the rail item byte-for-byte: the SUCCESS path is
        unchanged, and the FAILURE path is deliberately new on BOTH mounts.
        """
        cfg = _unfinished()
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))

        hosted = build_home(page, app_config=cfg, on_navigate=lambda _d: None)
        wizard_in_host = next(c for c in hosted.controls if isinstance(c, ft.Column))
        from_rail = build_setup(page)

        assert _texts(wizard_in_host) == _texts(from_rail)
        assert _labels(wizard_in_host) == _labels(from_rail)


class TestTheBranchAFloor:
    """A broken wizard must not take Home down — and must not lie about the sync."""

    def _broken(self, page: MagicMock, monkeypatch: pytest.MonkeyPatch, hops: list[str]) -> ft.Control:
        def _boom(*_a: object, **_kw: object) -> ft.Control:
            raise RuntimeError("the wizard exploded")

        monkeypatch.setattr(setup_screen, "build_setup", _boom)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _unfinished()))
        return build_home(page, app_config=_unfinished(), on_navigate=hops.append)

    def test_a_raise_in_the_wizard_lands_on_the_branch_a_error_card(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tree = self._broken(page, monkeypatch, [])

        texts = _texts(tree)
        assert home_screen.SETUP_UNAVAILABLE_HEADLINE in texts
        assert home_screen.SETUP_UNAVAILABLE_DETAIL in texts

    def test_the_floor_never_reuses_the_dashboards_false_reassurance(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dashboard floor says "your nightly sync keeps running in the background".
        For an install with no schedule and no run that is false in every particular — and
        it is the sentence a copy-paste of the other floor would have produced."""
        texts = _texts(self._broken(page, monkeypatch, []))

        assert not any("keeps running" in t for t in texts)
        assert not any("nightly sync" in t.lower() for t in texts)

    def test_the_floor_offers_a_route_to_a_person(self, page: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        hops: list[str] = []
        tree = self._broken(page, monkeypatch, hops)

        _button(tree, home_screen.SETUP_UNAVAILABLE_HELP_LABEL).on_click(None)

        assert hops == ["help"]

    def test_the_help_route_is_secondary_not_a_filled_primary(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The screen's one filled action belongs to the wizard's forward button; an error
        card's escape hatch may not out-weight the action that fixes anything."""
        tree = self._broken(page, monkeypatch, [])

        assert not [c for c in _iter_controls(tree) if isinstance(c, ft.FilledButton)]

    def test_the_floor_probe_is_not_vacuous(self, page: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """POSITIVE twin: without the injected raise the SAME build shows no error card."""
        texts = _texts(_home(page, _unfinished(), monkeypatch))

        assert home_screen.SETUP_UNAVAILABLE_HEADLINE not in texts
        assert DISTRICT_STEP_TITLE in texts

    def test_a_raise_while_deriving_the_band_still_shows_the_floor_not_a_trace(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The band is INSIDE the floor, and the floor is all-or-nothing on purpose.

        A raise deriving the welcome line takes the wizard down with it — deliberately.
        This branch has exactly one thing to offer, and a wizard sitting under a line we
        failed to derive (or a bare band over nothing) is a worse surface than the honest
        card. Degrading the band while keeping the wizard was considered and rejected.
        """

        def _boom() -> list[dict]:
            raise RuntimeError("the run store exploded")

        monkeypatch.setattr(home_screen, "read_run_records", _boom)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _unfinished()))

        tree = build_home(page, app_config=_unfinished(), on_navigate=lambda _d: None)

        assert home_screen.SETUP_UNAVAILABLE_HEADLINE in _texts(tree)


class TestTheFinishSeam:
    """``on_complete`` fires only after a VERIFIED save — the slice's load-bearing promise."""

    def _hosted_finish(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hops: list[str]
    ) -> ft.Control:
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        tree = build_home(page, app_config=cfg, on_navigate=hops.append)
        _drive_to_finish(tree)
        return tree

    def test_a_verified_save_re_enters_home(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved: list[bool] = []
        monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(self.setup_completed))
        hops: list[str] = []

        tree = self._hosted_finish(page, tmp_path, monkeypatch, hops)
        assert hops == [], "nothing may navigate before the finish press"
        _button(tree, FINISH_BUTTON).on_click(None)

        assert saved and saved[-1] is True
        assert hops == ["home"], "the finish line must hand the payoff back to Home"

    def test_a_double_press_saves_once_and_hops_once(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The button stays on screen until the HOST replaces the surface, so it is
        genuinely double-clickable. Un-latched, the reliability lens reproduced two saves
        and two navigations from one impatient admin."""
        saved: list[bool] = []
        monkeypatch.setattr(AppConfig, "save", lambda self: saved.append(self.setup_completed))
        hops: list[str] = []

        tree = self._hosted_finish(page, tmp_path, monkeypatch, hops)
        button = _button(tree, FINISH_BUTTON)
        button.on_click(None)
        button.on_click(None)

        assert saved == [True]
        assert hops == ["home"]

    def test_the_badge_is_re_probed_when_setup_finishes(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The boot probe was suppressed because the install was unfinished (S6).

        Nothing re-asks it, so an admin who SKIPS the Schedule step on a machine carrying a
        leftover task would keep a silent rail for the rest of the session — the exact
        fault the badge exists to raise. Firing ``on_schedule_changed`` on completion is
        what closes that window. It goes FIRST so the probe still happens if the navigation
        raises — not because the re-render could race it (the shell's refresh does its own
        ``AppConfig.load()`` off-thread, so the order cannot change what it reads).
        """
        monkeypatch.setattr(AppConfig, "save", lambda self: None)
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        order: list[str] = []
        tree = build_home(
            page,
            app_config=cfg,
            on_navigate=lambda dest: order.append(f"navigate:{dest}"),
            on_schedule_changed=lambda: order.append("re-probe"),
        )
        _drive_to_finish(tree)

        _button(tree, FINISH_BUTTON).on_click(None)

        assert order == ["re-probe", "navigate:home"]

    def test_the_RAIL_finish_re_probes_the_badge_too(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The twin of the row above on the OTHER mount — the divergence S6 exists to stop.

        The badge suppression is keyed on ``nav.needs_setup``, not on where the wizard is
        mounted, so an admin who walks it from the Setup RAIL item and skips the Schedule
        step was left with the same silenced rail the hosted path now rescues. One wizard
        means one re-probe rule.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        monkeypatch.setattr(AppConfig, "save", lambda self: None)
        fired: list[str] = []

        tree = build_setup(page, on_schedule_changed=lambda: fired.append("re-probe"))
        _drive_to_finish(tree)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert fired == ["re-probe"], "the rail mount finished without re-probing the badge"
        assert TRANSITION_CUE in _texts(tree), "the graduation itself must still happen"

    def test_a_raising_re_probe_costs_neither_mount_its_payoff(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The badge refresh is ADVISORY on both mounts — the siblings' stated contract.

        Paired with the two rows above, which prove the callback genuinely fires: without
        them "nothing broke" would be equally satisfied by a callback wired to nothing.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        monkeypatch.setattr(AppConfig, "save", lambda self: None)

        def _boom() -> None:
            raise RuntimeError("the probe thread would not start")

        hosted = build_home(page, app_config=cfg, on_navigate=(hops := []).append, on_schedule_changed=_boom)
        _drive_to_finish(hosted)
        _button(hosted, FINISH_BUTTON).on_click(None)
        assert hops == ["home"], "an advisory badge refresh cost the hosted admin their finish"

        cfg.setup_completed = False  # a second, independent walk from the rail item
        from_rail = build_setup(page, on_schedule_changed=_boom)
        _drive_to_finish(from_rail)
        _button(from_rail, FINISH_BUTTON).on_click(None)
        assert TRANSITION_CUE in _texts(from_rail), "the same raise blocked the rail graduation"

    def test_a_raising_hand_off_re_opens_the_finish_latch(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A one-shot latch set BEFORE the risky work turns a transient fault into a dead
        button (the S4a lesson, restated for this latch).

        The save SUCCEEDED here and the hand-off did not, so ``FINISH_SAVE_FAILED_NOTE``
        would be a lie — the failure is re-raised LOUD instead, and the latch re-opens so
        the press actually repeats rather than going quiet.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        monkeypatch.setattr(AppConfig, "save", lambda self: None)
        hops: list[str] = []

        def _hop(dest: str) -> None:
            hops.append(dest)
            if len(hops) == 1:
                raise RuntimeError("the shell blew up on the way to Home")

        tree = build_home(page, app_config=cfg, on_navigate=_hop)
        _drive_to_finish(tree)

        with pytest.raises(RuntimeError):
            _button(tree, FINISH_BUTTON).on_click(None)
        assert hops == ["home"]
        assert setup_screen.FINISH_SAVE_FAILED_NOTE not in _texts(tree), (
            "a hand-off failure was reported as a save failure"
        )

        _button(tree, FINISH_BUTTON).on_click(None)

        assert hops == ["home", "home"], "the latch stayed shut — Finish was dead for the rest of the mount"

    def test_a_host_without_the_callback_still_finishes(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The positive twin of the ordering above: the re-probe is OPTIONAL, and its
        absence must not cost the admin their finish."""
        monkeypatch.setattr(AppConfig, "save", lambda self: None)
        hops: list[str] = []

        tree = self._hosted_finish(page, tmp_path, monkeypatch, hops)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert hops == ["home"]

    def test_the_hosted_finish_does_NOT_mount_the_settings_scroll(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two payoffs for one press would flash a Settings page under a screen that is
        being replaced. ``on_complete`` fires INSTEAD of the graduation, never as well."""
        monkeypatch.setattr(AppConfig, "save", lambda self: None)
        tree = self._hosted_finish(page, tmp_path, monkeypatch, [])

        _button(tree, FINISH_BUTTON).on_click(None)

        assert TRANSITION_CUE not in _texts(tree)

    def test_the_summary_stays_visible_until_the_finish_press(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finish-in-place: the checked summary IS the payoff, and it is on screen for as
        long as the admin wants it — the press is what moves them on."""
        tree = self._hosted_finish(page, tmp_path, monkeypatch, [])

        assert "Here's what you set up" in _texts(tree)
        assert _button(tree, FINISH_BUTTON) is not None

    def test_a_raising_save_keeps_the_summary_and_never_bounces_to_step_1(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE regression this seam exists for.

        Firing ``on_complete`` on an unverified save would re-render Home, Home would
        re-read a config that still says "unfinished", and the admin would land back on
        step 1 having just been told they were done — indistinguishable from "it undid my
        setup". So: the note appears, the summary stays, no hop is recorded, and the
        in-memory flag is rolled back so nothing downstream inherits a false completion.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        hops: list[str] = []
        tree = build_home(page, app_config=cfg, on_navigate=hops.append)
        _drive_to_finish(tree)

        def _boom(self: AppConfig) -> None:
            raise OSError("the settings folder is read-only")

        monkeypatch.setattr(AppConfig, "save", _boom)
        _button(tree, FINISH_BUTTON).on_click(None)

        texts = _texts(tree)
        assert setup_screen.FINISH_SAVE_FAILED_NOTE in texts
        assert "Step 5 of 5" in texts and "Step 1 of 5" not in texts
        assert "Here's what you set up" in texts, "the summary the admin earned was taken away"
        assert hops == [], "on_complete fired on a save that never happened"
        assert cfg.setup_completed is False, "the instance kept a completion the disk does not have"

    def test_the_retry_after_a_failed_save_succeeds(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The note says "try again", so pressing again must be a real retry — the positive
        twin of the failure above (without it, a permanently inert button would pass)."""
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        hops: list[str] = []
        tree = build_home(page, app_config=cfg, on_navigate=hops.append)
        _drive_to_finish(tree)
        attempts: list[int] = []

        def _flaky(self: AppConfig) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("transient")

        monkeypatch.setattr(AppConfig, "save", _flaky)
        _button(tree, FINISH_BUTTON).on_click(None)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert len(attempts) == 2
        assert hops == ["home"]
        assert setup_screen.FINISH_SAVE_FAILED_NOTE not in _texts(tree)

    def test_the_rail_hosted_wizard_still_graduates_in_place(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``on_complete=None`` — today's behaviour, unchanged. Paired with the hosted case
        above so "the seam fires" and "the seam is opt-in" are both proven."""
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        monkeypatch.setattr(AppConfig, "save", lambda self: None)

        tree = build_setup(page)
        _drive_to_finish(tree)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert TRANSITION_CUE in _texts(tree)

    def test_a_raising_save_from_the_RAIL_item_also_keeps_the_summary(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The save-verify is the wizard's, not the host's — both mounts inherit it."""
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        tree = build_setup(page)
        _drive_to_finish(tree)

        def _boom(self: AppConfig) -> None:
            raise OSError("read-only")

        monkeypatch.setattr(AppConfig, "save", _boom)
        _button(tree, FINISH_BUTTON).on_click(None)

        texts = _texts(tree)
        assert setup_screen.FINISH_SAVE_FAILED_NOTE in texts
        assert TRANSITION_CUE not in texts, "the Settings graduation ran on an unsaved finish"
        assert cfg.setup_completed is False

    def test_the_retry_after_a_failed_save_from_the_RAIL_item_also_graduates(
        self, page: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The RAIL item's post-failure SUCCESS path — the half the hosted retry cannot reach.

        Mirrors ``test_the_retry_after_a_failed_save_succeeds``, which only walks the
        ``on_complete`` branch. With ``on_complete=None`` the way out is the in-place
        ``_mount_settings(transition_cue=True)`` graduation, and the stale-note clear +
        ``_render()`` that precede it were unexercised on this mount.

        The note assertion is taken AT THE HANDOFF, not after it: ``_mount_settings``
        replaces the whole surface, so "the note is gone once Settings is up" is satisfied
        just as well by a note that was never cleared. Spying the handoff is what makes the
        clear falsifiable on a path that ends by discarding the control it lives on.
        """
        cfg = _ready_to_finish(tmp_path)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        tree = build_setup(page)
        _drive_to_finish(tree)

        at_handoff: list[str] = []
        real_mount = setup_screen._mount_settings

        def _spy_mount(*args: object, **kwargs: object) -> None:
            at_handoff.extend(_texts(tree))
            real_mount(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(setup_screen, "_mount_settings", _spy_mount)
        attempts: list[int] = []

        def _flaky(self: AppConfig) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("transient")

        monkeypatch.setattr(AppConfig, "save", _flaky)
        _button(tree, FINISH_BUTTON).on_click(None)

        assert setup_screen.FINISH_SAVE_FAILED_NOTE in _texts(tree), "the failure half never fired"
        assert at_handoff == [], "the rail item graduated on a save that raised"

        _button(tree, FINISH_BUTTON).on_click(None)

        assert attempts == [1, 1], "the second press was swallowed — the finish latch never re-opened"
        # The positive twin FIRST, so the absence below cannot pass by never having happened.
        assert at_handoff, "the retry never reached _mount_settings — the absence assertion would be vacuous"
        assert setup_screen.FINISH_SAVE_FAILED_NOTE not in at_handoff, (
            "Settings was handed a surface still saying the save failed"
        )
        assert TRANSITION_CUE in _texts(tree)


class TestTheScheduleBadgeCallbackReachesTheHostedWizard:
    """A nightly task registered from HOME must re-probe the rail badge, as from Setup."""

    def test_the_callback_is_forwarded_into_the_hosted_wizard(
        self, page: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        def _spy(_page: ft.Page, **kwargs: object) -> ft.Control:
            seen.update(kwargs)
            return ft.Text("wizard")

        monkeypatch.setattr(setup_screen, "build_setup", _spy)
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _unfinished()))
        sentinel = lambda: None  # noqa: E731 - identity is the assertion

        build_home(
            page,
            app_config=_unfinished(),
            on_navigate=lambda _d: None,
            on_schedule_changed=sentinel,
        )

        assert seen["on_schedule_changed"] is sentinel
        assert callable(seen["on_complete"])

    def test_a_host_without_the_callback_still_builds(self, page: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """The defensive default: every caller without a badge to refresh is unchanged."""
        tree = _home(page, _unfinished(), monkeypatch)

        assert DISTRICT_STEP_TITLE in _texts(tree)
