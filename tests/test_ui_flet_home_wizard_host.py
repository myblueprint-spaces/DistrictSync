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
