"""Brand design tokens for the Flet UI — primitive hex + semantic aliases.

Pure module: NO ``flet`` import (so it stays testable without a display and
reusable by both ``theme.py`` and any future surface). The brand *values* are
ported verbatim from ``src/ui/brand.py`` (``MB_*``) — that file's ~350 lines of
``!important`` CSS exist only to fight Streamlit/BaseWeb defaults and are NOT
ported (a typed Flet theme deletes that failure class).

Tiering (so DS-1 can extend without churn):
  * **Primitives** — the raw brand palette (``MB_*``), the single source of hex.
  * **Semantic aliases** — role-named colours (``color_action_primary``, …) the
    UI references by *intent*, not by raw hue.

Accessibility is a token-level guarantee: ``contrast_ratio`` implements the WCAG
2.x relative-luminance formula, and ``UI_CONTRAST_PAIRS`` enumerates every
foreground/background pairing the shell actually paints — the tokens test
asserts each is >= 4.5:1 (WCAG AA for normal text) at authoring time.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Primitives — ported verbatim from src/ui/brand.py (MB_*); the only hex source #
# --------------------------------------------------------------------------- #
MB_PRIMARY = "#1D5BB5"  # myBlueprint blue
MB_DARK = "#0F2D6B"  # deep navy (headings, sidebar)
MB_ACCENT = "#0EA5E9"  # sky-blue accent
MB_GREEN = "#16A34A"  # success / active
MB_LIGHT_BG = "#F0F6FF"  # page background tint
MB_BORDER = "#DBEAFE"  # card / divider border
MB_TEXT = "#0F172A"  # body text
MB_MUTED = "#64748B"  # captions / muted

# Neutral surface (not part of the MB_* palette — the white card/surface base).
WHITE = "#FFFFFF"

# The eight brand primitives, for the "all present" gate (single source of truth).
BRAND_PRIMITIVES: dict[str, str] = {
    "MB_PRIMARY": MB_PRIMARY,
    "MB_DARK": MB_DARK,
    "MB_ACCENT": MB_ACCENT,
    "MB_GREEN": MB_GREEN,
    "MB_LIGHT_BG": MB_LIGHT_BG,
    "MB_BORDER": MB_BORDER,
    "MB_TEXT": MB_TEXT,
    "MB_MUTED": MB_MUTED,
}

# --------------------------------------------------------------------------- #
# Semantic aliases — role-named; the UI references intent, not raw hue          #
# --------------------------------------------------------------------------- #
# Actions
color_action_primary = MB_PRIMARY
color_action_primary_strong = MB_DARK

# Status / verdict (DS-1 will pair these with non-colour cues; the hues live here).
# NOTE: white-on-fill must clear WCAG AA, so the text-bearing healthy fill is a
# darker green (green-700) than the bright brand accent ``MB_GREEN`` (green-600,
# 3.30:1 white-on — fine for >=24px/bold or icons, but below AA for normal text).
# ``MB_GREEN`` stays available as a primitive for large accents/borders; the
# semantic verdict colour the shell paints text on is the AA-safe shade.
color_status_healthy = "#15803D"  # green-700 — AA-safe for white text/normal labels
color_status_healthy_accent = MB_GREEN  # bright brand green — large icons/borders only
# Warning is AMBER (verdict-only), NOT the sky accent. amber-700 `#B45309` clears
# WCAG AA for white text (5.02:1 white-on-fill); amber-600 `#D97706` FAILS (3.19).
# This is decoupled from the M3 `secondary` role — see theme.py (now MB_ACCENT).
color_status_warning = "#B45309"  # amber-700 — AA-safe verdict warning fill
color_status_failed = "#DC2626"  # red-600 — failure verdict (not a Streamlit primitive)

# Surfaces
color_surface = WHITE
page_bg = MB_LIGHT_BG
color_border = MB_BORDER

# Text. ``color_muted`` is a slightly darker slate (slate-600) than the primitive
# ``MB_MUTED`` (slate-500) so muted captions clear AA on BOTH the white card and
# the tinted page (slate-500 is 4.38:1 on the page tint — just below AA). The
# primitive ``MB_MUTED`` is preserved as the brand value; the semantic alias is
# the legibility-tuned one the shell actually uses.
color_text = MB_TEXT
color_muted = "#475569"  # slate-600 — AA-safe muted text on white AND the page tint
color_on_action = WHITE  # text/icons sitting on a filled action surface
# Text/icons on the amber warning fill — white clears AA (5.02:1 on amber-700).
# A named alias so the verdict banner reads by intent, not by reusing color_on_action.
color_on_status_warning = WHITE


# --------------------------------------------------------------------------- #
# WCAG contrast — accessibility as an authoring-time guarantee                  #
# --------------------------------------------------------------------------- #
def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of an ``#RRGGBB`` colour (0.0 black … 1.0 white)."""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected a #RRGGBB hex colour, got {hex_color!r}")
    channels = []
    for i in (0, 2, 4):
        srgb = int(value[i : i + 2], 16) / 255.0
        # sRGB -> linear (the WCAG transfer function)
        linear = srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4
        channels.append(linear)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colours (1.0 … 21.0).

    Pure: ``(L_lighter + 0.05) / (L_darker + 0.05)``. Order-independent. AA for
    normal text is >= 4.5:1.
    """
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


# Every foreground/background pairing the shell paints — the contrast contract.
# Each (fg, bg) MUST clear WCAG AA (>= 4.5:1); the tokens test enforces it so a
# future palette tweak that breaks legibility fails the build, not a user's eyes.
UI_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    (color_text, color_surface),  # body text on a white card
    (color_text, page_bg),  # body text on the tinted page
    (color_muted, color_surface),  # captions on a white card
    (color_muted, page_bg),  # captions on the tinted page
    (color_on_action, color_action_primary),  # white label on a primary button
    (color_on_action, color_action_primary_strong),  # white label on the navy action
    (color_on_action, color_status_healthy),  # white on the healthy verdict fill
    (color_on_status_warning, color_status_warning),  # white on the amber warning fill (5.02)
    (color_on_action, color_status_failed),  # white on the failed verdict fill
    (color_status_healthy, color_surface),  # healthy text on a white card
    (color_status_failed, color_surface),  # failed text on a white card
)
