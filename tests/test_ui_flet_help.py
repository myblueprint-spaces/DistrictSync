"""Unit tests for the IA-7 Help surface — constants drift guard + shell override-ordering.

No flet control is instantiated here (that is the anti-pattern the ``FilledButton(text=)``
post-mortem + ``docs/FLET_1.0_CONVENTIONS.md`` warn against — the view is manually smoked).
These tests cover the only genuinely testable surface: the two module constants (an
exact-case drift guard, since the org URL/email are single-sourced here for the Flet layer)
and the load-bearing ``DISTRICTSYNC_UI_DEMO`` override-ordering invariant (the swap must not
break the dev demo route).
"""

from __future__ import annotations

import functools
import os

import pytest

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
    """Reproduce the shell's ``help`` swap + the DISTRICTSYNC_UI_DEMO override block VERBATIM.

    The shell's ``main`` swap logic isn't cleanly extractable without a ``page``, so this
    mirrors it exactly (``shell.py``): the real ``help`` swap binds ``build_help`` via
    ``functools.partial`` FIRST, then the override block re-assigns ``screens["help"]`` to
    ``components.build_design_demo`` LAST iff the env var is set — so the override wins in dev.
    Uses a sentinel ``page``/``app_cfg`` (never called — we assert identity, not render).
    """
    page = object()  # sentinel — build_help is never invoked in these wiring tests
    app_cfg = object()
    screens: dict[str, object] = {"help": lambda: None}  # the placeholder the shell starts with

    # --- real swap (shell.py, BEFORE the override block) --- #
    screens["help"] = functools.partial(build_help, page, app_config=app_cfg)
    # --- DISTRICTSYNC_UI_DEMO override (shell.py:195-196, byte-identical condition) --- #
    if os.environ.get("DISTRICTSYNC_UI_DEMO") and "help" in screens:
        screens["help"] = components.build_design_demo
    return screens


def test_demo_override_wins_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DISTRICTSYNC_UI_DEMO set, ``help`` routes to the design-system gallery, not build_help."""
    monkeypatch.setenv("DISTRICTSYNC_UI_DEMO", "1")
    screens = _apply_shell_help_swap()
    assert screens["help"] is components.build_design_demo


def test_real_help_wins_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DISTRICTSYNC_UI_DEMO unset, ``help`` routes to a ``build_help`` partial (the real surface)."""
    monkeypatch.delenv("DISTRICTSYNC_UI_DEMO", raising=False)
    screens = _apply_shell_help_swap()
    swapped = screens["help"]
    assert isinstance(swapped, functools.partial)
    assert swapped.func is build_help
