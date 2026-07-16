"""Window geometry persistence — the pure restore/persist decisions (COUNTED).

0032 Tier-2 #8: the shell remembers the window's size/position/maximized state
across launches. The trust-critical DECISIONS live here, pure and unit-tested;
``shell.py`` only applies them:

* :func:`restore_plan` — what to apply to ``page.window`` at boot. Saved geometry
  is NEVER trusted raw: a position is applied only CLAMPED fully inside the
  current work area (a window restored onto a since-removed monitor is a support
  call — the title bar must always be reachable), an oversized saved window
  shrinks to the work area, and a fresh install's height is
  ``min(DEFAULT_HEIGHT, work-area height)`` so the default window fits a small
  laptop screen. When the work area is unknown the plan degrades to SIZE-only
  (never an unclamped position).
* :func:`persist_plan` — what to write back to ``AppConfig`` at exit. TOTAL over
  garbage (a stub page's mock attributes, NaN/inf, zero, strings): an invalid
  current value keeps the previously-saved one, and a MAXIMIZED window keeps the
  previous normal-state bounds (persisting the maximized size would ratchet the
  restored window up forever) while recording ``maximized=True``.
* :func:`probe_work_area` — the ONE effectful helper (the
  ``convert_output.open_folder`` effectful-but-mockable precedent): a best-effort
  Windows desktop-bounds lookup, ``None`` on any other platform or any failure.
  It prefers the VIRTUAL screen (all monitors — a legitimately-on-the-second-
  monitor position survives the clamp) and falls back to the primary work area.

No ``flet`` import — testable without a display. The launch defaults the shell
has always used (1180x860, min 940x680) are single-sourced here so the sizing
numbers can't drift between the restore plan and the window minimums.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

# The shell's launch defaults (previously inline in shell.main — single source now).
DEFAULT_WIDTH = 1180.0
DEFAULT_HEIGHT = 860.0
MIN_WIDTH = 940
MIN_HEIGHT = 680

# Absurdity ceiling for any coordinate/dimension — far beyond any real monitor wall.
# A saved value outside ±this is corrupt data, not a window position.
_MAX_ABS = 100_000.0

# Windows metrics constants (GetSystemMetrics / SystemParametersInfoW).
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SPI_GETWORKAREA = 0x0030


@dataclass(frozen=True)
class Rect:
    """A screen rectangle in the same virtual-pixel space Flet's window reports."""

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class SavedGeometry:
    """The persisted window facts (mirrors the ``AppConfig.window_*`` fields).

    ``None`` = "never saved" (a fresh install, or a value persistence skipped).
    Values are sanitized at USE time (``restore_plan``/``persist_plan`` are total),
    never trusted at construction — a hand-edited ``config.json`` can hold anything.
    """

    width: float | None = None
    height: float | None = None
    left: float | None = None
    top: float | None = None
    maximized: bool = False


@dataclass(frozen=True)
class WindowPlan:
    """What the shell applies to ``page.window`` at boot (already clamped/safe)."""

    width: float
    height: float
    left: float | None  # None → leave placement to the OS default (always reachable)
    top: float | None
    maximized: bool


def _as_number(value: object) -> float | None:
    """A finite, sane coordinate or ``None`` (bool/str/mock/NaN/inf/absurd → ``None``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or abs(number) > _MAX_ABS:
        return None
    return number


def _as_dimension(value: object) -> float | None:
    """A finite, strictly-positive dimension or ``None``."""
    number = _as_number(value)
    if number is None or number <= 0:
        return None
    return number


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` into ``[lo, hi]``; a degenerate range collapses to ``lo``."""
    if hi < lo:
        hi = lo
    return min(max(value, lo), hi)


def restore_plan(saved: SavedGeometry, work: Rect | None) -> WindowPlan:
    """The safe boot geometry: saved-but-sanitized size, position CLAMPED into ``work``.

    Rules (each one a support-call guard):
      * invalid/absent saved dimensions → the launch defaults (1180x860);
      * the window never exceeds the work area (first-run height therefore lands at
        ``min(860, work-area height)`` — the T2 #8 small-screen rule);
      * a position is applied ONLY when both coordinates are sane AND a work area is
        known — then clamped fully inside it, so the title bar is always reachable;
      * ``maximized`` restores only a strict ``True`` (a corrupt value never maximizes).
    """
    width = _as_dimension(saved.width) or DEFAULT_WIDTH
    height = _as_dimension(saved.height) or DEFAULT_HEIGHT
    maximized = saved.maximized is True

    if work is None:
        # Unknown work area: restoring an unclamped position could strand the window
        # off-screen — restore the size only and let the OS place it.
        return WindowPlan(width=width, height=height, left=None, top=None, maximized=maximized)

    width = min(width, work.width)
    height = min(height, work.height)

    left = _as_number(saved.left)
    top = _as_number(saved.top)
    if left is None or top is None:
        plan_left: float | None = None
        plan_top: float | None = None
    else:
        plan_left = _clamp(left, work.left, work.left + work.width - width)
        plan_top = _clamp(top, work.top, work.top + work.height - height)

    return WindowPlan(width=width, height=height, left=plan_left, top=plan_top, maximized=maximized)


def persist_plan(
    *,
    current_width: object,
    current_height: object,
    current_left: object,
    current_top: object,
    current_maximized: object,
    previous: SavedGeometry,
) -> SavedGeometry:
    """What to persist at exit — TOTAL over whatever ``page.window`` reports.

    A maximized window keeps the PREVIOUS normal-state bounds (persisting the
    maximized size would grow the restored window forever) and records the
    maximized fact. Otherwise each valid current value replaces the saved one and
    an invalid value (a mock attribute on a stub page, NaN, zero) keeps the
    previously-saved value — so a broken read can never erase a good record.
    """
    if current_maximized is True:
        return SavedGeometry(
            width=previous.width,
            height=previous.height,
            left=previous.left,
            top=previous.top,
            maximized=True,
        )

    width = _as_dimension(current_width)
    height = _as_dimension(current_height)
    left = _as_number(current_left)
    top = _as_number(current_top)
    return SavedGeometry(
        width=width if width is not None else previous.width,
        height=height if height is not None else previous.height,
        left=left if left is not None else previous.left,
        top=top if top is not None else previous.top,
        maximized=False,
    )


def probe_work_area() -> Rect | None:
    """Best-effort desktop bounds for the clamp — Windows only, ``None`` on any failure.

    Prefers the VIRTUAL screen (spans every attached monitor, so a second-monitor
    position survives while a DISCONNECTED monitor's coordinates fall outside the
    shrunken rect and get pulled back); falls back to the primary work area. Effectful
    but mockable (tests pass an explicit :class:`Rect` to the pure functions).
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return Rect(left=float(x), top=float(y), width=float(w), height=float(h))
        rect = ctypes.wintypes.RECT()
        if user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            width = float(rect.right - rect.left)
            height = float(rect.bottom - rect.top)
            if width > 0 and height > 0:
                return Rect(left=float(rect.left), top=float(rect.top), width=width, height=height)
        return None
    except Exception:  # noqa: BLE001 - a failed probe degrades to size-only restore, never a crash
        return None
