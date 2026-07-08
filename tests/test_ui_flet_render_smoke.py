"""Render-smoke: every Flet screen's control tree must CONSTRUCT on the pinned flet
(0.85.3) without raising and without falling to its never-crash ErrorCard floor.

Why this exists: the screen views are coverage-omitted (Flet view glue) and the
manual GUI check was historically deferred to a human, so flet-0.85.3 API-drift
bugs shipped latent and undetected:
  - ``ft.Dropdown(on_change=...)``   -> TypeError (Dropdown's event is ``on_select``)
  - ``ft.TextField(helper_text=...)`` -> TypeError (the field is ``helper``)
Either one crashes the FIRST screen an admin opens (Setup). Unit tests never
instantiated the views, so the gates were green. This test instantiates each
screen against a stub Page so that entire class of render crash fails CI.

A stub Page (plain MagicMock) is enough: build_* functions construct controls and
close over ``page`` for event handlers — they don't need a live session at build
time. ``page.add``/``update``/``window.*`` are no-ops on the mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.ui_flet import components
from src.ui_flet.screens.convert import build_convert
from src.ui_flet.screens.help import build_help
from src.ui_flet.screens.home import build_home
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.onboarding import build_onboarding
from src.ui_flet.screens.run_history import build_run_history
from src.ui_flet.screens.setup import build_setup


@pytest.fixture
def stub_page() -> MagicMock:
    """A permissive stub Page — any attr/method access returns a child mock no-op."""
    return MagicMock()


@pytest.fixture
def app_cfg() -> AppConfig:
    """Default (unconfigured) config — hermetic, independent of ~/.districtsync."""
    return AppConfig()


@pytest.fixture(autouse=True)
def _hermetic_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_setup/build_convert load AppConfig internally — pin it to defaults so
    the smoke never reads (or depends on) the developer's real ~/.districtsync."""
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cls()))


def _assert_renders(build_callable, monkeypatch: pytest.MonkeyPatch) -> ft.Control:
    """Build the screen; fail if it raises OR returns its ErrorCard floor.

    Each screen's floor does ``return components.ErrorCard(...)``. If the build's
    return value IS that ErrorCard, the screen swallowed a real render bug — a false
    pass by return-type alone. Spy on ErrorCard to catch that precisely.
    """
    real_errorcard = components.ErrorCard
    floor: dict[str, object] = {"obj": None}

    def spy(*args, **kwargs):
        obj = real_errorcard(*args, **kwargs)
        floor["obj"] = obj
        return obj

    monkeypatch.setattr(components, "ErrorCard", spy)
    out = build_callable()
    assert isinstance(out, ft.Control), f"build returned {type(out).__name__}, not a Control"
    assert out is not floor["obj"], "screen fell to its ErrorCard floor — a masked render bug"
    return out


class TestScreensRender:
    def test_setup(self, stub_page, monkeypatch):
        _assert_renders(lambda: build_setup(stub_page), monkeypatch)

    def test_home(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(
            lambda: build_home(stub_page, app_config=app_cfg, on_navigate=lambda _d: None),
            monkeypatch,
        )

    def test_convert(self, stub_page, monkeypatch):
        _assert_renders(lambda: build_convert(stub_page), monkeypatch)

    def test_home_with_refresh(self, stub_page, monkeypatch):
        # The Refresh affordance (D1) must render on the dashboard branch without
        # crashing — a configured+scheduled config is required to reach that branch
        # (the default unconfigured config renders onboarding, which has no Refresh).
        configured = AppConfig(
            input_dir="in",
            output_dir="out",
            sis_type="myedbc",
            schedule_registered=True,
        )
        tree = _assert_renders(
            lambda: build_home(
                stub_page,
                app_config=configured,
                on_navigate=lambda _d: None,
                on_refresh=lambda: None,
            ),
            monkeypatch,
        )
        # The button label is a plain string on FilledButton.content (see components._filled_button).
        assert any(getattr(c, "content", None) == "Refresh" for c in _iter_controls(tree)), (
            "dashboard branch must render the Refresh affordance"
        )

    def test_run_history(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_run_history(stub_page, app_config=app_cfg), monkeypatch)

    def test_run_history_with_refresh(self, stub_page, app_cfg, monkeypatch):
        # The Refresh affordance (D1) must render without crashing.
        _assert_renders(
            lambda: build_run_history(stub_page, app_config=app_cfg, on_refresh=lambda: None),
            monkeypatch,
        )

    def test_mapping(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_mapping(stub_page, app_config=app_cfg), monkeypatch)

    def test_help(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_help(stub_page, app_config=app_cfg), monkeypatch)

    def test_onboarding(self, stub_page, monkeypatch):
        _assert_renders(
            lambda: build_onboarding(stub_page, sis_type="myedbc", on_start_setup=lambda: None),
            monkeypatch,
        )

    def test_design_demo(self, monkeypatch):
        # The DISTRICTSYNC_UI_DEMO override target (Help slot).
        _assert_renders(components.build_design_demo, monkeypatch)


def _iter_controls(control):
    """Depth-first walk of a flet control tree (``.controls`` list + a single ``.content`` child)."""
    yield control
    children: list[object] = []
    ctrls = getattr(control, "controls", None)
    if isinstance(ctrls, list):
        children.extend(ctrls)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        children.append(content)
    for child in children:
        if isinstance(child, ft.Control):
            yield from _iter_controls(child)


def _find(control, cls):
    """All controls of type ``cls`` in the tree rooted at ``control`` (depth-first order)."""
    return [c for c in _iter_controls(control) if isinstance(c, cls)]


def _pick_event(value):
    """A stub Dropdown ``on_select`` event exposing ``e.control.value`` (the handler's read)."""
    evt = MagicMock()
    evt.control.value = value
    return evt


def test_mapping_post_apply_rerenders_and_allows_revert(stub_page, monkeypatch):
    """D1 acceptance: Apply re-renders the surface in place AND a switch can be reverted.

    Drives the real (coverage-omitted) handlers through the built control tree: the Apply gate
    starts disabled (no-op), enables on picking a different config, disables again right after
    Apply (now the persisted current), and — the load-bearing fix — RE-ENABLES when the previous
    mapping is re-selected (reverting was impossible before, since the gate compared against the
    frozen mount instance). ``AppConfig.save`` is stubbed so the interaction never touches the
    real profile (the existing per-file hermetic ``load`` patch covers the reads).
    """
    app_cfg = AppConfig()  # default persisted current == "myedbc"
    original = app_cfg.sis_type
    target = "mbp_core"  # a real bundled config, different from the default
    assert original != target
    # `_on_apply` loads + saves AppConfig — keep the save off the real ~/.districtsync.
    monkeypatch.setattr(AppConfig, "save", lambda self: None)

    surface = _assert_renders(lambda: build_mapping(stub_page, app_config=app_cfg), monkeypatch)

    dropdowns = _find(surface, ft.Dropdown)
    assert dropdowns, "the Mapping surface exposes a district dropdown"
    dropdown = dropdowns[0]
    apply_btn = next(b for b in _find(surface, ft.FilledButton) if b.content == "Use this mapping")

    # Initially pending == persisted == original → Apply disabled (a no-op switch).
    assert apply_btn.disabled is True

    # Pick a different, loadable config → Apply enables.
    dropdown.on_select(_pick_event(target))
    assert apply_btn.disabled is False

    # Apply → the switch becomes the persisted current → Apply disables again (no re-apply).
    apply_btn.on_click(None)
    assert apply_btn.disabled is True

    # THE fix: re-selecting the previous mapping is applyable — a switch can be reverted in place.
    dropdown.on_select(_pick_event(original))
    assert apply_btn.disabled is False


def test_nav_rail_builds_and_exposes_rail_handle():
    """D7 render-smoke: build_nav constructs the fixed-order rail WITHOUT raising and hands
    the shell the rail handle (so it can sync the highlight on programmatic navigation).

    The rail is coverage-omitted view glue; this mount-smoke catches a flet-0.85.3 API
    drift in the rail the same way TestScreensRender guards the screens.
    """
    from src.ui_flet import nav, nav_rail

    ordered = nav.ordered_destinations(nav.nav_model(AppConfig()))
    view, rail = nav_rail.build_nav(
        ordered=ordered,
        selected_id="setup",
        on_select=lambda _id: None,
        on_exit=lambda *_a: None,
    )
    assert isinstance(view, ft.Control)
    assert isinstance(rail, ft.NavigationRail)
    # Initial highlight is the fixed-order index of selected_id (single-sourced mapping).
    assert rail.selected_index == nav.selected_index_for("setup", ordered)
    # One rail destination per ordered entry — fixed order, nothing dropped.
    assert len(rail.destinations) == len(ordered)


def test_no_ft_dropdown_uses_on_change():
    """Guard the specific trap: ft.Dropdown has NO on_change on 0.85.3 (use on_select).

    A static check so a re-introduced ``ft.Dropdown(on_change=...)`` or
    ``some_dropdown.on_change =`` fails even if a new screen isn't added above.
    """
    import ast
    import glob
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders: list[str] = []
    for path in glob.glob(os.path.join(root, "src", "ui_flet", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            # ft.Dropdown(on_change=...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Dropdown"
                and any(kw.arg == "on_change" for kw in node.keywords)
            ):
                offenders.append(f"{os.path.relpath(path, root)}:{node.lineno} ft.Dropdown(on_change=)")
    assert not offenders, "ft.Dropdown has no on_change on flet 0.85.3 — use on_select: " + "; ".join(offenders)
