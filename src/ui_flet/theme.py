"""Brand tokens -> a Material-3 ``ft.Theme`` (light-only).

Maps the semantic tokens in ``tokens.py`` onto an M3 ``ft.ColorScheme``. M3 roles
are NOT 1:1 with the brand palette (``primary``/``secondary``/``surface`` carry
specific meaning), so this module is the single place that decides the mapping —
``shell.py`` just calls ``build_theme()``.

Light mode only (one ``theme_mode`` line in the shell); dark mode is deferred
(YAGNI — a non-technical admin opens this 2-3x/year). The ~350 lines of
``!important`` CSS in ``src/ui/brand.py`` are deliberately NOT ported: a typed
theme deletes that whole "fight the framework's dark-mode defaults" failure class.
"""

from __future__ import annotations

import flet as ft

from src.ui_flet import tokens


def build_color_scheme() -> ft.ColorScheme:
    """The brand-mapped M3 colour scheme (light)."""
    return ft.ColorScheme(
        primary=tokens.color_action_primary,
        on_primary=tokens.color_on_action,
        secondary=tokens.MB_ACCENT,  # sky accent (decoupled from verdict-only amber warning — DS-1/RC1)
        on_secondary=tokens.color_on_action,
        tertiary=tokens.color_action_primary_strong,  # deep navy
        on_tertiary=tokens.color_on_action,
        error=tokens.color_status_failed,
        on_error=tokens.color_on_action,
        surface=tokens.color_surface,
        on_surface=tokens.color_text,
        on_surface_variant=tokens.color_muted,
        outline=tokens.color_border,
        outline_variant=tokens.color_border,
    )


def build_theme() -> ft.Theme:
    """The full brand ``ft.Theme`` for the Flet shell (Material 3, light)."""
    return ft.Theme(
        color_scheme_seed=tokens.color_action_primary,
        use_material3=True,
        color_scheme=build_color_scheme(),
        font_family="Segoe UI",
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
