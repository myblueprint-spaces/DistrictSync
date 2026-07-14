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
