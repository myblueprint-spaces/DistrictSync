"""The Flet left navigation rail VIEW — the F6 rail extraction from ``shell.py``.

VIEW glue (coverage-omitted): ``build_nav`` assembles a single flat
``ft.NavigationRail`` from the FIXED-order ``Destination`` tuple (D7 — one order in
every state; no section headers, built-in a11y retained). It owns **no** lifecycle
and reads **no** config: the shell passes ``on_select``/``on_exit`` callbacks in and
stays the lifecycle owner. Selection is by ``dest.id``, decoupling render order from
the screen map; the initial highlight index comes from the single-source
``nav.selected_index_for``. It returns ``(view, rail)`` — the rail handle lets the
shell sync ``selected_index`` on programmatic navigation, not just user clicks.

**Direction B "Branded Professional" navy rail (0033 Slice 2):** the rail owns the
myBlueprint navy (``tokens.color_rail_bg``) — white-ish labels/icons at rest
(``color_rail_text``), full-white + a 12% white indicator pill when active. The brand
block is the sync glyph + "DistrictSync" / "Roster sync for SpacesEDU"; a foot line
carries the reassurance, Exit, and the app version. The native ``ft.NavigationRail``
is RESTYLED (not replaced) so the shell's ``selected_index`` sync + the D4 badge
mutation on ``rail.destinations[i].badge`` keep working verbatim.

**Flet 0.85.3 note (not the mockup 1:1):** the native rail's active marker is the
``indicator_color`` PILL — the mockup's 3px left accent bar is NOT cleanly expressible
on a native ``NavigationRail`` (it has no per-item left-edge slot), so the active state
is the 12% white pill + full-white label/icon (accepted per the plan). Per-item hover
uses the M3 default overlay (no per-destination hover-colour hook on the native rail).

Follows the PROVEN Flet 0.85.3 forms (``ft.Padding``/``ft.Border`` dataclasses —
NOT the gone 0.2x helpers; see ``docs/FLET_1.0_CONVENTIONS.md``). Buttons via the
``components`` factory (the ``FilledButton(text=)`` trap can't recur).
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import components, nav, tokens
from src.utils.version import app_version


# --------------------------------------------------------------------------- #
# Flet 0.85 layout helpers (the old ft.padding.* / ft.border.* funcs are gone) #
# Local (mirrors components.py) so this view never imports shell (would cycle) #
# --------------------------------------------------------------------------- #
def _pad(*, left: float = 0, top: float = 0, right: float = 0, bottom: float = 0) -> ft.Padding:
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


# --------------------------------------------------------------------------- #
# The flat, fixed-order navigation rail                                         #
# --------------------------------------------------------------------------- #
def attention_badge() -> ft.Badge:
    """A small "needs attention" dot badge (no label) for a nav destination (D4/D7).

    A labelless ``ft.Badge`` with ``small_size`` renders as a Material dot on the
    destination's icon — the Setup badge the shell raises when ``schedule_status`` reports a
    missing/contradicted schedule. Kept here (the rail view) so its exact form is proven by
    the rail render-smoke.
    """
    return ft.Badge(small_size=10, bgcolor=tokens.color_status_failed)


def build_nav(
    *,
    ordered: tuple[nav.Destination, ...],
    selected_id: str,
    on_select: Callable[[str], None],
    on_exit: Callable[..., None],
    attention_ids: frozenset[str] = frozenset(),
) -> tuple[ft.Control, ft.NavigationRail]:
    """Build the flat fixed-order rail from ``ordered``; return ``(view, rail)``.

    ``selected_id`` sets only the INITIAL highlight (``nav.selected_index_for`` — the
    same mapping the shell uses to sync the highlight, falling back to 0);
    ``ft.NavigationRail`` still manages its own highlight on user click. ``on_change``
    maps the native index back to a ``dest.id`` and calls ``on_select``; Exit calls
    ``on_exit``. The ``rail`` handle is returned so the shell can set ``selected_index``
    when navigation is driven programmatically. ``attention_ids`` seeds the "needs attention"
    dot badge on those destinations at build; the shell also mutates a destination's ``badge``
    post-build once the off-thread schedule probe returns (D4). No lifecycle lives here.
    """
    selected_index = nav.selected_index_for(selected_id, ordered)

    def on_change(e: ft.ControlEvent) -> None:
        on_select(ordered[e.control.selected_index].id)

    # Brand block (Direction B): the sync glyph mark (signifies DistrictSync — "roster sync
    # for SpacesEDU") in the brand-blue rounded square, "DistrictSync" in full white, and a
    # small on-navy sub-line. A 10% white hairline divides the brand from the nav items.
    brand = ft.Container(
        padding=_pad(top=14, bottom=12),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            controls=[
                ft.Container(
                    content=ft.Icon(ft.Icons.SYNC_ROUNDED, color=tokens.color_on_action, size=22),
                    width=42,
                    height=42,
                    bgcolor=tokens.color_action_primary,
                    border_radius=tokens.radius_lg,
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Text(
                    "DistrictSync",
                    size=tokens.type_body,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_rail_text_active,
                ),
                ft.Container(
                    width=92,
                    content=ft.Text(
                        "Roster sync for SpacesEDU",
                        size=10,
                        color=tokens.color_rail_text,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ),
                _rail_divider(),
            ],
        ),
    )

    # Persistent decouple-the-sync reassurance (IA-2): a calm, always-on-screen line
    # directly above Exit — the real engine is the invisible scheduled CLI, so a
    # 2-3x/year admin must never fear that closing the cockpit stops the sync. Ambient
    # (not a per-close dialog): present regardless of which leave path is taken (in-app
    # Exit OR the OS title-bar ✕). Restyled for the navy rail (on-navy ``color_rail_text``).
    reassurance = ft.Container(
        padding=_pad(left=10, right=10, bottom=8),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.VERIFIED_USER_ROUNDED, size=18, color=tokens.color_rail_text),
                ft.Container(
                    width=92,
                    content=ft.Text(
                        "Closing this window won't stop your nightly sync.",
                        size=11,
                        color=tokens.color_rail_text,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ),
            ],
        ),
    )

    # Exit (on-navy ``color_rail_text``) + a faint version foot line. The version reuses the
    # AA-safe ``color_rail_text`` (rather than the mockup's sub-AA .38 white) so the contrast
    # gate stays green — a smaller size carries the "tertiary" read instead of a fainter colour.
    exit_btn = components.text_button(
        "Exit",
        on_exit,
        icon=ft.Icons.LOGOUT_ROUNDED,
        color=tokens.color_rail_text,
    )
    version_caption = ft.Container(
        padding=_pad(top=2, bottom=10),
        content=ft.Text(f"v{app_version()}", size=10, color=tokens.color_rail_text),
    )

    rail = ft.NavigationRail(
        selected_index=selected_index,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=104,
        min_extended_width=180,
        bgcolor=tokens.color_rail_bg,
        # Active marker = a 12% white indicator pill (the mockup's active pill); the 3px left
        # accent bar is not expressible on a native NavigationRail — see the module docstring.
        indicator_color=ft.Colors.with_opacity(0.12, tokens.WHITE),
        selected_label_text_style=ft.TextStyle(
            color=tokens.color_rail_text_active, weight=ft.FontWeight.W_600, size=tokens.type_caption
        ),
        unselected_label_text_style=ft.TextStyle(
            color=tokens.color_rail_text, weight=ft.FontWeight.W_600, size=tokens.type_caption
        ),
        on_change=on_change,
        leading=brand,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icon(getattr(ft.Icons, dest.icon, ft.Icons.CIRCLE_OUTLINED), color=tokens.color_rail_text),
                selected_icon=ft.Icon(
                    getattr(ft.Icons, dest.selected_icon, ft.Icons.CIRCLE), color=tokens.color_rail_text_active
                ),
                label=dest.label,
                badge=attention_badge() if dest.id in attention_ids else None,
            )
            for dest in ordered
        ],
        trailing=ft.Container(
            expand=True,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.END,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
                controls=[reassurance, exit_btn, version_caption],
            ),
        ),
    )

    view = ft.Container(content=rail, bgcolor=tokens.color_rail_bg)
    return view, rail


def _rail_divider() -> ft.Control:
    """A 10% white hairline separating the brand block from the nav items (on-navy)."""
    return ft.Container(
        width=84,
        height=1,
        margin=ft.Margin(left=0, top=10, right=0, bottom=2),
        bgcolor=ft.Colors.with_opacity(0.10, tokens.WHITE),
    )
