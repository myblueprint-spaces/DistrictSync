"""Unit tests for the IA-7 Help surface — constants drift guard + shell override-ordering.

No flet control is instantiated here (that is the anti-pattern the ``FilledButton(text=)``
post-mortem + ``docs/FLET_1.0_CONVENTIONS.md`` warn against — the view is manually smoked).
These tests cover the only genuinely testable surface: the two module constants (an
exact-case drift guard, since the org URL/email are single-sourced here for the Flet layer)
and the load-bearing ``DISTRICTSYNC_UI_DEMO`` override-ordering invariant (the swap must not
break the dev demo route).
"""

from __future__ import annotations

import os
import sys

import pytest

from src.config.app_config import AppConfig
from src.ui_flet import components
from src.ui_flet.screens.help import HELP_CENTRE_URL, SUPPORT_EMAIL, build_help


# --------------------------------------------------------------------------- #
# Constants drift guard — EXACT-case string equality                           #
# --------------------------------------------------------------------------- #
def test_help_centre_url_is_the_exact_canonical_article() -> None:
    """A change to the org KB URL (incl. a silent case/path drift) must fail this test.

    The value is single-sourced here for the Flet layer and grep-consistent with
    release.yml / README.md — exact ``==`` so a re-pointed article that didn't move
    here is caught, not hidden by a fuzzy match.
    """
    assert HELP_CENTRE_URL == "https://help.spacesedu.com/en-ca/article/mx56qo"


def test_support_email_is_the_exact_mixed_case_canonical_contact() -> None:
    """The canonical support contact with its EXACT mixed-case ``myBlueprint`` preserved.

    A lowercase drift (``myblueprint``) would be real drift a case-insensitive check hides —
    the source uses mixed-case, so the constant (and this guard) must too.
    """
    assert SUPPORT_EMAIL == "support@myBlueprint.ca"


# --------------------------------------------------------------------------- #
# DISTRICTSYNC_UI_DEMO override-ordering — the load-bearing wiring invariant    #
# --------------------------------------------------------------------------- #
def _apply_shell_help_swap() -> dict[str, object]:
    """Reproduce the shell's ``help`` swap + the DISTRICTSYNC_UI_DEMO override block.

    The shell's ``main`` swap logic isn't cleanly extractable without a ``page``, so this
    mirrors the CURRENT wiring (``shell.py``, post-Slice-1): the real ``help`` swap binds a
    fresh-load ``lambda: build_help(page, app_config=AppConfig.load())`` FIRST (each mount
    reads config fresh — the D1 supplier pattern; no longer a ``functools.partial`` over a
    frozen instance), then the override block re-assigns ``screens["help"]`` to
    ``components.build_design_demo`` LAST iff the env var is set — so the override wins in dev.
    Uses a sentinel ``page``; ``build_help``/``AppConfig.load`` are resolved at call time, so
    the env-unset test patches them to observe the route without a live render.
    """
    page = object()  # sentinel — the route is invoked only under patched build_help/AppConfig
    screens: dict[str, object] = {"help": lambda: None}  # the placeholder the shell starts with

    # --- real swap (shell.py, BEFORE the override block): fresh-load lambda (D1) --- #
    screens["help"] = lambda: build_help(page, app_config=AppConfig.load())
    # --- DISTRICTSYNC_UI_DEMO override (shell.py, byte-identical condition) --- #
    if os.environ.get("DISTRICTSYNC_UI_DEMO") and "help" in screens:
        screens["help"] = components.build_design_demo
    return screens


def test_demo_override_wins_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DISTRICTSYNC_UI_DEMO set, ``help`` routes to the design-system gallery, not build_help."""
    monkeypatch.setenv("DISTRICTSYNC_UI_DEMO", "1")
    screens = _apply_shell_help_swap()
    assert screens["help"] is components.build_design_demo


def test_real_help_wins_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DISTRICTSYNC_UI_DEMO unset, ``help`` routes to the real ``build_help`` surface.

    Post-Slice-1 the route is an anonymous fresh-load lambda (not a ``functools.partial``),
    so identity can't be asserted directly — instead invoke the route under a patched
    ``build_help`` + ``AppConfig.load`` and confirm it dispatches to ``build_help`` with a
    FRESH ``AppConfig`` (the D1 per-mount load), and is NOT the demo override.
    """
    monkeypatch.delenv("DISTRICTSYNC_UI_DEMO", raising=False)
    seen: list[object] = []
    # The lambda resolves `build_help`/`AppConfig` as this module's globals at call time.
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr(
        sys.modules[__name__],
        "build_help",
        lambda *_a, **kw: seen.append(kw.get("app_config")) or "HELP_SURFACE",
    )

    screens = _apply_shell_help_swap()
    route = screens["help"]
    assert route is not components.build_design_demo  # override did NOT fire
    assert callable(route)
    assert route() == "HELP_SURFACE"  # routes to the real build_help
    assert len(seen) == 1 and isinstance(seen[0], AppConfig)  # a fresh config per mount (D1)
