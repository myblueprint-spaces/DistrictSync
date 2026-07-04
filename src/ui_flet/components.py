"""Design-system component primitives for the Flet UI — the reusable view layer.

VIEW glue (coverage-omitted in ``pyproject.toml``): the trust-critical *intent*
lives COUNTED in the pure modules (``tokens`` values, ``verdict`` mapping); this
file only assembles ``ft.Control``s from them. Every later surface (IA-1+) drops
these in instead of hand-rolling buttons/cards, so the inline shapes in
``shell.py`` / ``picker_field.py`` / ``screens/setup.py`` are factored here ONCE.

Each factory reproduces an existing call-site's exact form via parameters so the
adoption refactor is byte-equivalent (same tokens, radii, padding, text styles):
  * ``primary_button`` — the ``FilledButton`` shape (``picker_field``'s Browse,
    ``setup``'s Save). ``disabled_bgcolor`` carries the **security Save-gate**
    disabled fill (``setup.py``), so its disabled styling survives the port.
  * ``secondary_button`` — same shape on the deep-navy strong action (for IA-1+).
  * ``text_button`` — the muted ``TextButton`` (the shell's Exit affordance).
  * ``card`` — the bordered ``Container`` (flat default) with an optional
    ``gradient`` for the hero headers (the shell placeholder, Setup header).
  * ``ErrorCard`` — the reusable never-crash error surface scaffold.
  * ``HealthVerdictBanner`` — the verdict-first spine: paints a verdict's colour
    + its non-colour ICON cue + headline (resolved from ``verdict_visuals``).

Follows the PROVEN Flet 0.85.3 forms from ``shell.py`` verbatim
(``ft.Padding``/``ft.Border``/``ft.ButtonStyle``/``ft.ControlState`` — NOT the
gone 0.2x helpers). Light-only.
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import tokens
from src.ui_flet.verdict import Verdict, verdict_visuals


# --------------------------------------------------------------------------- #
# Layout helpers — verbatim from shell.py (the 0.2x ft.padding.*/ft.border.* gone) #
# --------------------------------------------------------------------------- #
def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _b_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, bottom=side, left=side, right=side)


# --------------------------------------------------------------------------- #
# Buttons                                                                       #
# --------------------------------------------------------------------------- #
def _filled_button(
    text: str,
    on_click: Callable[..., None] | None,
    *,
    bgcolor: str,
    disabled: bool,
    disabled_bgcolor: str | None,
    icon: str | None,
    radius: float,
    text_size: float,
    text_weight: ft.FontWeight,
) -> ft.FilledButton:
    """Shared ``FilledButton`` shape; ``disabled_bgcolor`` adds the DISABLED state map."""
    bgcolor_map: dict[ft.ControlState, str] = {ft.ControlState.DEFAULT: bgcolor}
    if disabled_bgcolor is not None:
        bgcolor_map[ft.ControlState.DISABLED] = disabled_bgcolor
    # Flet 0.85.3: the button label is `content` (the `text=` kwarg the prior inline
    # forms used does not exist on FilledButton — see FLET_1.0_CONVENTIONS.md).
    return ft.FilledButton(
        content=text,
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor=bgcolor_map,
            color=tokens.color_on_action,
            shape=ft.RoundedRectangleBorder(radius=radius),
            text_style=ft.TextStyle(size=text_size, weight=text_weight),
        ),
    )


def primary_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    disabled: bool = False,
    disabled_bgcolor: str | None = None,
    icon: str | None = None,
    radius: float = 12,
    text_size: float = 14,
    text_weight: ft.FontWeight = ft.FontWeight.W_700,
) -> ft.FilledButton:
    """A primary (brand-blue) filled action button.

    ``disabled_bgcolor`` is the disabled-state fill — pass it to reproduce a gated
    button's disabled appearance (the security Save-gate in ``setup.py`` passes
    ``tokens.color_border``). Omitting it leaves the button with no DISABLED entry,
    matching call-sites that never disable (``picker_field``'s Browse).
    """
    return _filled_button(
        text,
        on_click,
        bgcolor=tokens.color_action_primary,
        disabled=disabled,
        disabled_bgcolor=disabled_bgcolor,
        icon=icon,
        radius=radius,
        text_size=text_size,
        text_weight=text_weight,
    )


def secondary_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    disabled: bool = False,
    disabled_bgcolor: str | None = None,
    icon: str | None = None,
    radius: float = 12,
    text_size: float = 14,
    text_weight: ft.FontWeight = ft.FontWeight.W_700,
) -> ft.FilledButton:
    """A secondary (deep-navy strong) filled action button — same shape as primary."""
    return _filled_button(
        text,
        on_click,
        bgcolor=tokens.color_action_primary_strong,
        disabled=disabled,
        disabled_bgcolor=disabled_bgcolor,
        icon=icon,
        radius=radius,
        text_size=text_size,
        text_weight=text_weight,
    )


def text_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    icon: str | None = None,
    color: str = tokens.color_muted,
    text_size: float = 12,
    text_weight: ft.FontWeight = ft.FontWeight.W_600,
) -> ft.TextButton:
    """A muted text button (the shell's Exit affordance)."""
    return ft.TextButton(
        text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            color=color,
            text_style=ft.TextStyle(size=text_size, weight=text_weight),
        ),
    )


# --------------------------------------------------------------------------- #
# Cards                                                                          #
# --------------------------------------------------------------------------- #
def card(
    content: ft.Control,
    *,
    gradient: ft.Gradient | None = None,
    bgcolor: str = tokens.color_surface,
    padding: ft.Padding | float = 32,
    border_radius: float = 16,
    bordered: bool = True,
) -> ft.Container:
    """A surface container: flat-bordered by default, or a gradient hero header.

    ``gradient`` (when given) paints a hero header — the border is dropped (the
    shell placeholder + Setup header use this). Without it, a white bordered card
    (the shell placeholder body + Setup form card).
    """
    border = _b_all(1, tokens.color_border) if (bordered and gradient is None) else None
    return ft.Container(
        content=content,
        gradient=gradient,
        bgcolor=None if gradient is not None else bgcolor,
        padding=padding,
        border_radius=border_radius,
        border=border,
    )


def hero_gradient() -> ft.LinearGradient:
    """The brand diagonal navy→blue hero gradient (shell placeholder, Setup header)."""
    return ft.LinearGradient(
        begin=ft.Alignment(-1, -1),
        end=ft.Alignment(1, 1),
        colors=[tokens.color_action_primary_strong, tokens.color_action_primary],
    )


# --------------------------------------------------------------------------- #
# Metric tile — one big value over a muted caption (shared by Home + Convert)   #
# --------------------------------------------------------------------------- #
def metric_tile(label: str, value: str) -> ft.Container:
    """One light metric tile: a big value over a muted caption (a bordered card).

    The single source of the entity-count / status tile shape — Home's dashboard
    and Convert's result both render a row of these (DRY; extracted from
    ``home.py``'s former private ``_metric_tile``).
    """
    return card(
        content=ft.Column(
            spacing=2,
            controls=[
                ft.Text(value, size=22, weight=ft.FontWeight.W_800, color=tokens.color_text),
                ft.Text(label, size=13, color=tokens.color_muted),
            ],
        ),
        padding=_pad_sym(20, 16),
    )


# --------------------------------------------------------------------------- #
# FileChip — a compact "icon + filename" chip (first consumer: Convert)         #
# --------------------------------------------------------------------------- #
def FileChip(filename: str, *, present: bool = True) -> ft.Container:  # noqa: N802 - a view-factory named like a component
    """A compact chip naming one GDE file: a file icon + the filename, DS-1 styled.

    ``present=False`` renders the "expected but missing" variant (a muted, dashed
    amber cue) so the missing-file warning reads at a glance. The filename is a
    config-derived source name (never PII), shown verbatim.
    """
    if present:
        icon = ft.Icons.DESCRIPTION_ROUNDED
        icon_color = tokens.color_action_primary
        text_color = tokens.color_text
        bgcolor = tokens.page_bg
        border = _b_all(1, tokens.color_border)
    else:
        icon = ft.Icons.HELP_OUTLINE_ROUNDED
        icon_color = tokens.color_status_warning
        text_color = tokens.color_status_warning
        bgcolor = tokens.color_surface
        border = _b_all(1, tokens.color_status_warning)

    return ft.Container(
        bgcolor=bgcolor,
        border=border,
        border_radius=10,
        padding=_pad_sym(12, 8),
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=18, color=icon_color),
                ft.Text(filename, size=13, weight=ft.FontWeight.W_600, color=text_color),
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# Verdict-first spine                                                            #
# --------------------------------------------------------------------------- #
def HealthVerdictBanner(  # noqa: N802 - a view-factory named like a component
    verdict: Verdict,
    *,
    headline: str | None = None,
    detail: str | None = None,
) -> ft.Container:
    """The verdict banner: a verdict colour fill + its non-colour ICON cue + headline.

    Paints ``verdict_visuals(verdict)`` — colour, the resolved icon (the structural
    non-colour cue), and the plain-language headline (overridable via ``headline``).
    The icon name is resolved like ``nav.py``/``shell.py``: ``getattr(ft.Icons,
    name, fallback)``. White-on-fill is AA-gated for all three verdict colours
    (``UI_CONTRAST_PAIRS``).
    """
    visual = verdict_visuals(verdict)
    icon = getattr(ft.Icons, visual.icon, ft.Icons.INFO_ROUNDED)
    head = headline if headline is not None else visual.headline

    lines: list[ft.Control] = [
        ft.Text(head, size=18, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
    ]
    if detail:
        lines.append(
            ft.Text(detail, size=13, color=ft.Colors.with_opacity(0.9, tokens.color_on_action)),
        )

    return ft.Container(
        bgcolor=visual.color,
        padding=_pad_sym(24, 20),
        border_radius=16,
        content=ft.Row(
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, color=tokens.color_on_action, size=30),
                ft.Column(spacing=2, controls=lines),
            ],
        ),
    )


def ErrorCard(  # noqa: N802 - a view-factory named like a component
    headline: str,
    detail: str | None = None,
    *,
    action: ft.Control | None = None,
) -> ft.Container:
    """The reusable never-crash error surface: a red-bordered card with a clear cause.

    The shell's reliability net — a surface that fails renders this instead of a
    raw stack trace. Uses the failed-verdict colour for the icon/headline (red text
    on white clears AA — ``UI_CONTRAST_PAIRS``), keeping body text legible.
    """
    body: list[ft.Control] = [
        ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color=tokens.color_status_failed, size=26),
                ft.Text(headline, size=16, weight=ft.FontWeight.W_700, color=tokens.color_status_failed),
            ],
        ),
    ]
    if detail:
        body.append(ft.Text(detail, size=14, color=tokens.color_text))
    if action is not None:
        body.append(action)

    return ft.Container(
        bgcolor=tokens.color_surface,
        padding=24,
        border_radius=16,
        border=_b_all(1, tokens.color_status_failed),
        content=ft.Column(spacing=12, controls=body),
    )


# --------------------------------------------------------------------------- #
# Dev-only render demo (NOT a user nav entry) — exercises the front-loaded spine #
# --------------------------------------------------------------------------- #
def build_design_demo() -> ft.Control:
    """A dev-scoped gallery of the 3 verdict banners + an ErrorCard.

    Reachable only behind the ``DISTRICTSYNC_UI_DEMO`` env flag (wired in
    ``shell.build_screens``) or by a test/manual call — never a user-facing route.
    Exercises the verdict-first spine + error surface this slice so they ship
    visually proven, not as dead library code (their live-data consumer is IA-3).
    """
    return ft.Column(
        spacing=18,
        controls=[
            card(
                content=ft.Text(
                    "Design-system demo (dev only)",
                    size=20,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_on_action,
                ),
                gradient=hero_gradient(),
            ),
            HealthVerdictBanner(Verdict.HEALTHY, detail="Last run completed cleanly."),
            HealthVerdictBanner(Verdict.WARNING, detail="A source file looked smaller than usual."),
            HealthVerdictBanner(Verdict.FAILED, detail="The output was not delivered."),
            ErrorCard(
                "Something went wrong",
                "We hit a problem rendering this screen. Your nightly sync keeps running.",
            ),
        ],
    )
