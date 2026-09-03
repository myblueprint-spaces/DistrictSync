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
from pathlib import Path

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


def test_support_email_is_the_exact_canonical_contact() -> None:
    """The canonical published support contact, pinned EXACTLY.

    Owner decision 2026-08-13: the product's support contact is the SpacesEDU
    address, not the myBlueprint one it used to carry. Exact ``==`` because this
    string is what an admin clicks in-app — a silent re-point sends them nowhere.

    The address is all-lowercase, so unlike the previous constant there is no
    mixed-case trap to guard here; the exactness guard remains because the same
    address is spelled a SECOND time in ``src/main.py``'s CLI failure hint (which
    cannot import this module without pulling flet into every headless run), and
    the two must not drift.
    """
    assert SUPPORT_EMAIL == "hello@spacesedu.com"


def test_the_cli_failure_hint_names_the_SAME_support_address() -> None:
    """``src/main.py``'s failure hint must not drift from ``SUPPORT_EMAIL``.

    The CLI deliberately spells the address as a literal instead of importing this
    module — importing it would pull the whole Flet UI layer into every headless
    scheduled run. That leaves two spellings of one fact, so this is the parity test
    tying them back together (CLAUDE.md: a literal copied out of its source needs a
    parity test). Without it, re-pointing the support address in the UI would leave
    every CLI crash pointing an admin at the old contact.
    """
    main_source = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8")
    assert SUPPORT_EMAIL in main_source, (
        f"src/main.py's failure hint no longer names {SUPPORT_EMAIL} — the CLI and the Help "
        f"screen would send admins to different addresses."
    )


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


def test_launch_url_is_a_coroutine_so_every_click_routes_through_open_url() -> None:
    """Owner finding (2026-09-03): every Help-page link was a dead click with
    ``RuntimeWarning: coroutine 'Page.launch_url' was never awaited`` in the console; the
    first fix then CRASHED the handler with ``TypeError: handler must be a coroutine
    function``, because ``run_task`` gates on ``inspect.iscoroutinefunction`` and the
    ``@deprecated`` wrapper around ``Page.launch_url`` is a sync function returning the
    coroutine. Three halves: the premise (it IS a coroutine once unwrapped, and is NOT one
    while wrapped — if a future flet changes either, this row says so), the rule (no
    ``page.launch_url(`` call anywhere in ``src/ui_flet`` outside the helper), and the
    positive twin — the scheduled handler, awaited, actually launches the url.

    The fake page re-implements ``run_task``'s real guard on purpose. The permissive fake it
    replaces is exactly what let the crashing form ship green (CANDIDATES: no vacuous
    greens)."""
    import asyncio
    import inspect
    import re

    import flet as ft

    unwrapped = inspect.unwrap(ft.Page.launch_url)
    assert inspect.iscoroutinefunction(unwrapped), "premise: launch_url is a coroutine on this flet"
    assert not inspect.iscoroutinefunction(ft.Page.launch_url), (
        "premise: the @deprecated wrapper hides that from run_task's guard"
    )

    root = Path(__file__).resolve().parents[1] / "src" / "ui_flet"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("``") or "launch_url`" in stripped:
                continue
            if re.search(r"\bpage\.launch_url\(", line) and path.name != "components.py":
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], offenders

    scheduled: list = []
    launched: list[str] = []

    class _Page:
        def run_task(self, fn, *args):
            # flet 0.85.3's own guard, verbatim — a fake without it cannot see the bug.
            if not inspect.iscoroutinefunction(fn):
                raise TypeError("handler must be a coroutine function")
            scheduled.append((fn, args))

        async def launch_url(self, url):
            launched.append(url)

    page = _Page()
    components.open_url(page, "https://example.invalid/x")  # type: ignore[arg-type]
    assert scheduled, "open_url scheduled nothing"
    handler, args = scheduled[0]
    asyncio.run(handler(*args))
    assert launched == ["https://example.invalid/x"]
