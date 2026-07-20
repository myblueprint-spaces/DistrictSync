"""Tests for the shared shell exit path — src/ui_flet/shell._close_window.

Regression guard for the Slice 2 exit bug: Flet 0.85.3 `Window.destroy()` is a
coroutine, so the previous *synchronous* call was an un-awaited no-op (the Exit
button did nothing). `_close_window` MUST await `destroy()`; `os._exit(0)` is the
fallback when it can't complete. shell.py is coverage-omitted view glue, but these
assert the load-bearing behaviour (awaited vs merely called) directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.ui_flet import shell


def test_close_window_awaits_destroy() -> None:
    """The coroutine `page.window.destroy()` is actually AWAITED (not just called).

    Against the old synchronous no-op this fails: an un-awaited AsyncMock is `called`
    but not `awaited`, so `assert_awaited_once` pins the fix.
    """
    page = MagicMock()
    page.window.destroy = AsyncMock()

    asyncio.run(shell._close_window(page))

    page.window.destroy.assert_awaited_once()


def test_close_window_falls_back_to_os_exit_when_destroy_raises(monkeypatch) -> None:
    """If `destroy()` can't complete, `os._exit(0)` is the last-resort fallback."""
    page = MagicMock()

    async def _boom() -> None:
        raise RuntimeError("destroy could not complete")

    page.window.destroy = _boom
    exit_spy = MagicMock()
    monkeypatch.setattr(shell.os, "_exit", exit_spy)

    asyncio.run(shell._close_window(page))

    exit_spy.assert_called_once_with(0)


# --------------------------------------------------------------------------- #
# Window geometry persistence at the exit seam (0032 T2 #8)                     #
# --------------------------------------------------------------------------- #
def test_close_window_persists_geometry_before_destroy(monkeypatch) -> None:
    """`_close_window` saves the window bounds BEFORE `destroy()` tears the window down —
    after destroy there is no window (and possibly no process) left to read from."""
    order: list[str] = []
    page = MagicMock()

    async def _destroy() -> None:
        order.append("destroy")

    page.window.destroy = _destroy
    monkeypatch.setattr(shell, "_persist_window_geometry", lambda p: order.append("persist"))

    asyncio.run(shell._close_window(page))

    assert order == ["persist", "destroy"]


def test_persist_window_geometry_writes_the_current_bounds(isolated_user_profile) -> None:
    """Real window numbers round-trip into the (isolated) config.json via geometry.persist_plan."""
    from src.config.app_config import AppConfig

    page = MagicMock()
    page.window.width = 1200.0
    page.window.height = 780.0
    page.window.left = 60.0
    page.window.top = 30.0
    page.window.maximized = False

    shell._persist_window_geometry(page)

    cfg = AppConfig.load()
    assert cfg.window_width == 1200.0
    assert cfg.window_height == 780.0
    assert cfg.window_left == 60.0
    assert cfg.window_top == 30.0
    assert cfg.window_maximized is False


def test_persist_window_geometry_on_a_stub_page_keeps_the_previous_record(isolated_user_profile) -> None:
    """A stub page's mock window attributes are invalid values — the TOTAL persist plan keeps
    whatever was previously saved instead of clobbering it with garbage."""
    from src.config.app_config import AppConfig

    AppConfig(window_width=1000.0, window_height=700.0, window_left=5.0, window_top=6.0).save()

    shell._persist_window_geometry(MagicMock())  # every window attr is a MagicMock → invalid

    cfg = AppConfig.load()
    assert cfg.window_width == 1000.0
    assert cfg.window_height == 700.0
    assert cfg.window_left == 5.0
    assert cfg.window_top == 6.0
    assert cfg.window_maximized is False  # a mock attr is never a strict True


def test_persist_window_geometry_never_raises(monkeypatch) -> None:
    """Geometry persistence is advisory — a broken config load must never block the exit path."""
    from src.config.app_config import AppConfig

    def _boom() -> None:
        raise OSError("profile locked")

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _boom()))

    shell._persist_window_geometry(MagicMock())  # must not raise
