"""The Flet left navigation rail VIEW — the F6 rail extraction from ``shell.py``.

VIEW glue (coverage-omitted): ``build_nav`` assembles a single flat
``ft.NavigationRail`` from the FIXED-order ``Destination`` tuple (D7 — one order in
every state; no section headers, built-in a11y retained). It owns **no** lifecycle
and reads **no** config: the shell passes ``on_select``/``on_exit`` callbacks in and
stays the lifecycle owner. Selection is by ``dest.id``, decoupling render order from
the screen map; the initial highlight index comes from the single-source
``nav.selected_index_for``. It returns ``(view, rail)`` — the rail handle lets the
shell sync ``selected_index`` on programmatic navigation, not just user clicks.

Follows the PROVEN Flet 0.85.3 forms (``ft.Padding``/``ft.Border`` dataclasses —
NOT the gone 0.2x helpers; see ``docs/FLET_1.0_CONVENTIONS.md``). The brand mark
+ Exit affordance are lifted verbatim from ``shell.py``. Buttons via the
``components`` factory (the ``FilledButton(text=)`` trap can't recur).
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import components, nav, tokens


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

    # Persistent decouple-the-sync reassurance (IA-2): a calm, always-on-screen line
    # directly above Exit — the real engine is the invisible scheduled CLI, so a
    # 2-3x/year admin must never fear that closing the cockpit stops the sync. Ambient
    # (not a per-close dialog): present regardless of which leave path is taken (in-app
    # Exit OR the OS title-bar ✕), so it satisfies "every leave point" without friction.
    # Static presentation — build_nav takes no new parameter.
    reassurance = ft.Container(
        padding=_pad(left=10, right=10, bottom=10),
        content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            controls=[
                ft.Icon(ft.Icons.VERIFIED_USER_ROUNDED, size=18, color=tokens.color_muted),
                ft.Container(
                    width=84,
                    content=ft.Text(
                        "Closing this window won't stop your nightly sync.",
                        size=11,
                        color=tokens.color_muted,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ),
            ],
        ),
    )

    exit_btn = ft.Container(
        content=components.text_button(
            "Exit",
            on_exit,
            icon=ft.Icons.LOGOUT_ROUNDED,
        ),
        padding=_pad(bottom=12),
    )

    rail = ft.NavigationRail(
        selected_index=selected_index,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=104,
        min_extended_width=180,
        bgcolor=tokens.color_surface,
        indicator_color=ft.Colors.with_opacity(0.14, tokens.color_action_primary),
        on_change=on_change,
        leading=ft.Container(
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.SYNC_ROUNDED, color=tokens.color_on_action, size=22),
                        width=42,
                        height=42,
                        bgcolor=tokens.color_action_primary,
                        border_radius=12,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Text("District", size=11, weight=ft.FontWeight.W_700, color=tokens.color_action_primary_strong),
                    ft.Text("Sync", size=11, weight=ft.FontWeight.W_700, color=tokens.color_action_primary),
                ],
            ),
            padding=_pad(top=14, bottom=18),
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=getattr(ft.Icons, dest.icon, ft.Icons.CIRCLE_OUTLINED),
                selected_icon=getattr(ft.Icons, dest.selected_icon, ft.Icons.CIRCLE),
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
                controls=[reassurance, exit_btn],
            ),
        ),
    )

    view = ft.Container(
        content=rail,
        bgcolor=tokens.color_surface,
        border=ft.Border(right=ft.BorderSide(1, tokens.color_border)),
    )
    return view, rail
