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
  * **Scales** — the layout ramps (``space_*`` / ``radius_*`` / ``type_*``) the
    component factories size against, so a sizing tweak is one edit, not a sweep.
  * **Direction B roles** — the "Branded Professional" surface roles (navy rail,
    content wash, toned status tints + their deep on-tint text) the design system
    (``docs/DESIGN_SYSTEM.md``) paints. Every painted fg/bg pair is AA-gated below.

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
# The filled primary's hover/focus shade (Direction B ``--blue-strong``): one step
# MB_DARK-ward of the brand blue, so the ONE filled action darkens on interaction
# without jumping the whole way to the rail navy. Pressed uses ``MB_DARK``.
color_action_primary_hover = "#174A96"  # blue-800 — filled-primary hover/focus
# The OUTLINED secondary button's 1px border (Direction B ``--field``/``--A9C3E8``):
# a soft blue hairline on white. Decorative UI-boundary line (not text), so it is
# NOT an ``UI_CONTRAST_PAIRS`` entry — the secondary's *text* (MB_PRIMARY on white)
# is what must clear AA, and does (6.53:1).
color_action_outline = "#A9C3E8"  # secondary (outlined) button border

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
# Direction B content wash: the calm off-white the content area sits on (white cards
# float on it). Distinct from ``page_bg`` (the brand-tinted ``MB_LIGHT_BG`` used for
# chips / heading rows) — the wash is quieter so the navy rail + white cards own the
# contrast. ``color_text``/``color_muted`` are AA-gated on it below.
color_content_wash = "#F7F9FC"  # content-area wash (Direction B ``--content-bg``)
color_chip_bg = MB_LIGHT_BG  # chip / pill fill (alias of the brand light tint)

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
# Scales — the layout ramps the component factories size against (Direction B)  #
# Ints in px. ONE source, so a spacing/radius/type tweak is a single edit — the  #
# design system forbids inline sizes in screens (see docs/DESIGN_SYSTEM.md).     #
# --------------------------------------------------------------------------- #
# Spacing scale (gaps / paddings) — 4-based.
space_xs = 4
space_sm = 8
space_md = 12
space_lg = 16
space_xl = 24
space_2xl = 32

# Corner-radius scale.
radius_sm = 8
radius_md = 10
radius_lg = 12

# Type ramp (font sizes) — caption → metric. Tuned to the approved Direction B
# mockup (navy 26px numerals on tiles, ~16px section heads, 20px page titles).
type_caption = 12  # caps micro-labels, hints
type_body = 13  # body / detail copy
type_emphasis = 14  # field labels, emphasised body
type_section = 16  # verdict headline, section heads
type_title = 20  # page-header title
type_metric = 26  # metric-tile value (navy tabular numeral)


# --------------------------------------------------------------------------- #
# Direction B roles — "Branded Professional" surfaces (navy rail + toned status) #
# The design system paints these; every painted fg/bg pair is AA-gated below.    #
# --------------------------------------------------------------------------- #
# Navy nav rail — the rail owns the navy; its labels/icons sit at rest at a
# white-ish tint (flattened from the mockup's rgba(255,255,255,.78) on navy) and
# go full white when active, with the sky accent as the active marker bar.
color_rail_bg = MB_DARK
color_rail_text = "#BFCBE4"  # AA-safe rail label at rest (8.01:1 on the navy rail)
color_rail_text_active = WHITE  # active rail label (13.06:1 on the navy rail)
color_rail_active_accent = MB_ACCENT  # 3px sky accent bar on the active item

# Toned status surfaces — each verdict is a calm TINT + a 1px LINE border + a DEEP
# on-tint text (never a saturated fill behind body text). The saturated verdict
# colours (``color_status_*``) stay the solid ICON-DISC fill; these tints are the
# band/pill background. The (on-tint, tint) pair is AA-gated below.
color_status_healthy_tint = "#E8F3EC"  # green band/pill fill
color_status_healthy_line = "#BFDCCB"  # green band/pill 1px border
color_on_healthy_tint = "#14532D"  # green-900 deep text on the healthy tint (8.01:1)

color_status_warning_tint = "#F9F0E2"  # amber band/pill fill
color_status_warning_line = "#E7CDA8"  # amber band/pill 1px border
color_on_warning_tint = "#7A3E07"  # amber-900 deep text on the warning tint (7.38:1)

color_status_failed_tint = "#FDECEC"  # red band/pill fill
color_status_failed_line = "#F3C1C1"  # red band/pill 1px border
color_on_failed_tint = "#7F1D1D"  # red-900 deep text on the failed tint (8.77:1)


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
    # --- Direction B additions (toned status bands, navy rail, content wash) --- #
    (color_on_healthy_tint, color_status_healthy_tint),  # deep green text on the healthy tint (8.01)
    (color_on_warning_tint, color_status_warning_tint),  # deep amber text on the warning tint (7.38)
    (color_on_failed_tint, color_status_failed_tint),  # deep red text on the failed tint (8.77)
    (color_rail_text, color_rail_bg),  # rest rail label on the navy rail (8.01)
    (color_rail_text_active, color_rail_bg),  # active rail label on the navy rail (13.06)
    (MB_DARK, color_surface),  # navy metric numerals on a white tile (13.06)
    (color_muted, color_content_wash),  # muted captions on the content wash (7.18)
    (color_text, color_content_wash),  # body text on the content wash (16.93)
)
