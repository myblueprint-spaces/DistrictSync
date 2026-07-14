"""Tests for src/ui_flet/theme.py — brand tokens -> Material-3 ft.ColorScheme (light)."""

from __future__ import annotations

import flet as ft

from src.ui_flet import tokens
from src.ui_flet.theme import build_color_scheme, build_theme


class TestBuildTheme:
    def test_returns_ft_theme(self):
        assert isinstance(build_theme(), ft.Theme)

    def test_material3_enabled(self):
        assert build_theme().use_material3 is True

    def test_seed_is_brand_primary(self):
        assert build_theme().color_scheme_seed == tokens.color_action_primary

    def test_color_scheme_role_mapping(self):
        """The M3 ColorScheme maps the expected token hex to each role (light)."""
        cs = build_theme().color_scheme
        assert isinstance(cs, ft.ColorScheme)
        assert cs.primary == tokens.color_action_primary
        assert cs.on_primary == tokens.color_on_action
        assert cs.secondary == tokens.MB_ACCENT
        assert cs.tertiary == tokens.color_action_primary_strong
        assert cs.error == tokens.color_status_failed
        assert cs.surface == tokens.color_surface
        assert cs.on_surface == tokens.color_text
        assert cs.on_surface_variant == tokens.color_muted
        assert cs.outline == tokens.color_border

    def test_build_color_scheme_is_an_ft_color_scheme(self):
        assert isinstance(build_color_scheme(), ft.ColorScheme)

    def test_secondary_is_decoupled_from_the_verdict_warning(self):
        """DS-1/RC1: the M3 secondary role stays the sky accent — the amber warning
        is verdict-ONLY and must not recolour secondary app-wide."""
        cs = build_color_scheme()
        assert cs.secondary == tokens.MB_ACCENT
        assert cs.secondary != tokens.color_status_warning
