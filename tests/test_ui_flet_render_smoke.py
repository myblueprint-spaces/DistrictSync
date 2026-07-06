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

    def test_run_history(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_run_history(stub_page, app_config=app_cfg), monkeypatch)

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
