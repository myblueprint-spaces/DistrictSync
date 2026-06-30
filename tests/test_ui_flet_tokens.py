"""Tests for src/ui_flet/tokens.py — brand primitives + WCAG contrast guarantee."""

from __future__ import annotations

import re

import pytest

from src.ui_flet import tokens

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_EXPECTED_PRIMITIVES = {
    "MB_PRIMARY": "#1D5BB5",
    "MB_DARK": "#0F2D6B",
    "MB_ACCENT": "#0EA5E9",
    "MB_GREEN": "#16A34A",
    "MB_LIGHT_BG": "#F0F6FF",
    "MB_BORDER": "#DBEAFE",
    "MB_TEXT": "#0F172A",
    "MB_MUTED": "#64748B",
}


class TestBrandPrimitives:
    def test_all_eight_brand_hex_present_and_correct(self):
        """The eight MB_* primitives are ported verbatim from src/ui/brand.py."""
        for name, expected in _EXPECTED_PRIMITIVES.items():
            assert getattr(tokens, name) == expected, name

    def test_brand_primitives_dict_matches_module_attrs(self):
        assert tokens.BRAND_PRIMITIVES == _EXPECTED_PRIMITIVES

    def test_semantic_aliases_present_and_valid_hex(self):
        for alias in (
            "color_action_primary",
            "color_action_primary_strong",
            "color_status_healthy",
            "color_status_warning",
            "color_status_failed",
            "color_surface",
            "page_bg",
            "color_border",
            "color_text",
            "color_muted",
            "color_on_action",
            "color_on_status_warning",
        ):
            value = getattr(tokens, alias)
            assert _HEX_RE.match(value), f"{alias}={value!r} is not #RRGGBB"


class TestContrastRatio:
    def test_identical_colours_ratio_is_one(self):
        assert tokens.contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)

    def test_black_on_white_is_max(self):
        assert tokens.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.05)

    def test_order_independent(self):
        a = tokens.contrast_ratio("#0F172A", "#FFFFFF")
        b = tokens.contrast_ratio("#FFFFFF", "#0F172A")
        assert a == pytest.approx(b)

    def test_rejects_malformed_hex(self):
        with pytest.raises(ValueError):
            tokens.contrast_ratio("#FFF", "#000000")

    def test_every_ui_pair_clears_wcag_aa(self):
        """Every fg/bg pairing the shell paints must clear WCAG AA (>= 4.5:1)."""
        assert tokens.UI_CONTRAST_PAIRS, "the contrast contract must not be empty"
        for fg, bg in tokens.UI_CONTRAST_PAIRS:
            ratio = tokens.contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"contrast {ratio:.2f}:1 for fg={fg} on bg={bg} is below AA"

    def test_warning_is_amber_700_not_the_sky_accent(self):
        """DS-1/RC1: warning is the verdict-only amber (not MB_ACCENT)."""
        assert tokens.color_status_warning == "#B45309"
        assert tokens.color_status_warning != tokens.MB_ACCENT

    def test_white_on_amber_warning_clears_aa(self):
        """The pair the verdict banner paints (white-on-amber-700) clears AA (~5.02)."""
        ratio = tokens.contrast_ratio(tokens.color_on_status_warning, tokens.color_status_warning)
        assert ratio >= 4.5, f"white-on-amber {ratio:.2f}:1 is below AA"

    def test_white_on_amber_pair_is_in_the_contract(self):
        assert (tokens.color_on_status_warning, tokens.color_status_warning) in tokens.UI_CONTRAST_PAIRS
