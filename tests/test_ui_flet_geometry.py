"""Tests for src/ui_flet/geometry.py — window geometry restore/persist decisions (0032 T2 #8).

The pure functions are the trust surface: a saved position must NEVER strand the window
off-screen (restore clamps into the current work area, degrades to size-only when the work
area is unknown), the first-run height fits a small screen, and persistence is TOTAL over
garbage (mock attributes from a stub page, NaN, bools, absurd values). ``probe_work_area``
is the one effectful helper — asserted best-effort per platform.
"""

from __future__ import annotations

import sys

from src.ui_flet.geometry import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MIN_HEIGHT,
    MIN_WIDTH,
    Rect,
    SavedGeometry,
    WindowPlan,
    persist_plan,
    probe_work_area,
    restore_plan,
)

# A typical single-monitor work area (primary at origin).
_WORK = Rect(left=0.0, top=0.0, width=1920.0, height=1080.0)


class TestRestoreDefaults:
    def test_fresh_install_no_work_area_uses_launch_defaults(self):
        plan = restore_plan(SavedGeometry(), None)
        assert plan == WindowPlan(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, left=None, top=None, maximized=False)

    def test_fresh_install_large_screen_keeps_defaults_and_os_placement(self):
        plan = restore_plan(SavedGeometry(), _WORK)
        assert (plan.width, plan.height) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        assert plan.left is None and plan.top is None  # nothing saved → OS placement

    def test_first_run_height_is_min_of_default_and_work_area(self):
        # The T2 #8 small-laptop rule: a 1600x700 work area caps the default 860 height.
        plan = restore_plan(SavedGeometry(), Rect(left=0, top=0, width=1600.0, height=700.0))
        assert plan.height == 700.0
        assert plan.width == DEFAULT_WIDTH  # 1180 fits 1600 — untouched

    def test_tiny_work_area_shrinks_both_dimensions(self):
        plan = restore_plan(SavedGeometry(), Rect(left=0, top=0, width=800.0, height=600.0))
        assert (plan.width, plan.height) == (800.0, 600.0)


class TestRestoreSavedGeometry:
    def test_saved_bounds_inside_the_work_area_restore_verbatim(self):
        saved = SavedGeometry(width=1000.0, height=700.0, left=200.0, top=100.0)
        plan = restore_plan(saved, _WORK)
        assert plan == WindowPlan(width=1000.0, height=700.0, left=200.0, top=100.0, maximized=False)

    def test_oversized_saved_window_shrinks_to_the_work_area(self):
        saved = SavedGeometry(width=3000.0, height=2000.0, left=0.0, top=0.0)
        plan = restore_plan(saved, _WORK)
        assert (plan.width, plan.height) == (_WORK.width, _WORK.height)

    def test_position_beyond_the_right_edge_clamps_back_on_screen(self):
        # The support-call case: saved on a monitor that no longer exists to the right.
        saved = SavedGeometry(width=1000.0, height=700.0, left=5000.0, top=100.0)
        plan = restore_plan(saved, _WORK)
        assert plan.left == _WORK.width - 1000.0  # fully inside → title bar reachable
        assert plan.top == 100.0

    def test_negative_position_clamps_to_the_work_area_origin(self):
        saved = SavedGeometry(width=1000.0, height=700.0, left=-4000.0, top=-2000.0)
        plan = restore_plan(saved, _WORK)
        assert (plan.left, plan.top) == (_WORK.left, _WORK.top)

    def test_second_monitor_position_survives_inside_a_virtual_rect(self):
        # Virtual screen spanning a monitor LEFT of primary: negative coordinates are legal.
        virtual = Rect(left=-1920.0, top=0.0, width=3840.0, height=1080.0)
        saved = SavedGeometry(width=1000.0, height=700.0, left=-1500.0, top=50.0)
        plan = restore_plan(saved, virtual)
        assert (plan.left, plan.top) == (-1500.0, 50.0)  # legitimate position preserved

    def test_unknown_work_area_restores_size_only(self):
        # No clamp rect → an unclamped position could be off-screen; drop it, keep size.
        saved = SavedGeometry(width=1000.0, height=700.0, left=5000.0, top=5000.0)
        plan = restore_plan(saved, None)
        assert (plan.width, plan.height) == (1000.0, 700.0)
        assert plan.left is None and plan.top is None

    def test_partial_position_is_treated_as_no_position(self):
        saved = SavedGeometry(width=1000.0, height=700.0, left=200.0, top=None)
        plan = restore_plan(saved, _WORK)
        assert plan.left is None and plan.top is None

    def test_work_area_smaller_than_the_window_still_yields_a_reachable_origin(self):
        # Degenerate clamp range (window wider than work) collapses to the work origin.
        saved = SavedGeometry(width=1000.0, height=700.0, left=500.0, top=400.0)
        plan = restore_plan(saved, Rect(left=0, top=0, width=800.0, height=600.0))
        assert (plan.width, plan.height) == (800.0, 600.0)
        assert (plan.left, plan.top) == (0.0, 0.0)


class TestRestoreSanitization:
    def test_garbage_saved_dimensions_fall_back_to_defaults(self):
        for bad in (0.0, -100.0, float("nan"), float("inf"), 999_999.0):
            plan = restore_plan(SavedGeometry(width=bad, height=bad), _WORK)
            assert (plan.width, plan.height) == (DEFAULT_WIDTH, DEFAULT_HEIGHT), bad

    def test_garbage_saved_position_is_dropped(self):
        plan = restore_plan(SavedGeometry(width=1000.0, height=700.0, left=float("nan"), top=999_999.0), _WORK)
        assert plan.left is None and plan.top is None

    def test_maximized_restores_only_a_strict_true(self):
        assert restore_plan(SavedGeometry(maximized=True), _WORK).maximized is True
        # A hand-edited config.json can hold anything — only a real True maximizes.
        assert restore_plan(SavedGeometry(maximized="yes"), _WORK).maximized is False  # type: ignore[arg-type]
        assert restore_plan(SavedGeometry(maximized=1), _WORK).maximized is False  # type: ignore[arg-type]

    def test_non_numeric_saved_values_fall_back(self):
        plan = restore_plan(
            SavedGeometry(width="wide", height=True, left="x", top=[]),
            _WORK,  # type: ignore[arg-type]
        )
        assert (plan.width, plan.height) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        assert plan.left is None and plan.top is None


class TestPersistPlan:
    _PREV = SavedGeometry(width=1000.0, height=700.0, left=50.0, top=40.0, maximized=False)

    def test_normal_window_persists_the_current_bounds(self):
        saved = persist_plan(
            current_width=1200,
            current_height=800.5,
            current_left=-10.0,
            current_top=0.0,
            current_maximized=False,
            previous=self._PREV,
        )
        assert saved == SavedGeometry(width=1200.0, height=800.5, left=-10.0, top=0.0, maximized=False)

    def test_maximized_window_keeps_the_previous_normal_bounds(self):
        # Persisting the maximized size would ratchet the restored window up forever.
        saved = persist_plan(
            current_width=1920.0,
            current_height=1080.0,
            current_left=0.0,
            current_top=0.0,
            current_maximized=True,
            previous=self._PREV,
        )
        assert saved == SavedGeometry(width=1000.0, height=700.0, left=50.0, top=40.0, maximized=True)

    def test_invalid_current_values_keep_the_previous_record(self):
        # The stub-page case: window attributes are mocks/None — a broken read must
        # never erase a good record.
        from unittest.mock import MagicMock

        saved = persist_plan(
            current_width=MagicMock(),
            current_height=None,
            current_left=float("nan"),
            current_top="top",
            current_maximized=MagicMock(),  # not a strict True → not maximized
            previous=self._PREV,
        )
        assert saved == self._PREV

    def test_zero_and_negative_dimensions_are_invalid(self):
        saved = persist_plan(
            current_width=0,
            current_height=-5,
            current_left=1.0,
            current_top=2.0,
            current_maximized=False,
            previous=self._PREV,
        )
        assert (saved.width, saved.height) == (1000.0, 700.0)  # previous kept
        assert (saved.left, saved.top) == (1.0, 2.0)  # valid coords persisted

    def test_bool_dimensions_are_invalid_not_one_pixel(self):
        saved = persist_plan(
            current_width=True,
            current_height=True,
            current_left=True,
            current_top=True,
            current_maximized=False,
            previous=self._PREV,
        )
        assert saved == self._PREV


class TestRoundTrip:
    def test_persisted_bounds_restore_verbatim_on_the_same_screen(self):
        saved = persist_plan(
            current_width=1100.0,
            current_height=750.0,
            current_left=300.0,
            current_top=120.0,
            current_maximized=False,
            previous=SavedGeometry(),
        )
        plan = restore_plan(saved, _WORK)
        assert (plan.width, plan.height, plan.left, plan.top) == (1100.0, 750.0, 300.0, 120.0)

    def test_persisted_bounds_from_a_removed_monitor_come_back_reachable(self):
        # Save on a wide dual-monitor desktop; restore on a single 1920x1080 → clamped.
        saved = persist_plan(
            current_width=1100.0,
            current_height=750.0,
            current_left=2200.0,
            current_top=60.0,
            current_maximized=False,
            previous=SavedGeometry(),
        )
        plan = restore_plan(saved, _WORK)
        assert plan.left is not None and plan.left + 1100.0 <= _WORK.width
        assert plan.top == 60.0


class TestConstants:
    def test_minimums_do_not_exceed_defaults(self):
        assert MIN_WIDTH <= DEFAULT_WIDTH
        assert MIN_HEIGHT <= DEFAULT_HEIGHT


class TestProbeWorkArea:
    def test_non_windows_returns_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert probe_work_area() is None

    def test_windows_probe_returns_a_sane_rect_or_none(self):
        # Effectful best-effort: on a real Windows box this is a positive rect; on any
        # failure the contract is None (never an exception).
        result = probe_work_area()
        if sys.platform == "win32":
            assert result is not None
            assert result.width > 0 and result.height > 0
        else:
            assert result is None
