"""The Flet left navigation rail VIEW — the F6 rail extraction from ``shell.py``.

VIEW glue (coverage-omitted): ``build_nav`` assembles a single flat, state-aware
``ft.NavigationRail`` from a pre-ordered ``Destination`` tuple (option (a) — the
prominent group's destinations lead; no section headers, built-in a11y retained
— see plan 0018 gate). It owns **no** lifecycle and reads **no** config: the
shell passes ``on_select``/``on_exit`` callbacks in and stays the lifecycle owner.
Selection is by ``dest.id``, decoupling render order from the screen map.

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
# The flat, state-aware navigation rail                                         #
# --------------------------------------------------------------------------- #
def build_nav(
    *,
    ordered: tuple[nav.Destination, ...],
    selected_id: str,
    on_select: Callable[[str], None],
    on_exit: Callable[..., None],
) -> ft.Control:
    """Build the flat state-aware rail from ``ordered`` (prominent group first).

    ``selected_id`` sets only the INITIAL highlight (its index in ``ordered``,
    falling back to 0); ``ft.NavigationRail`` manages its own highlight on click
    (native — gate #4). ``on_change`` maps the native index back to a ``dest.id``
    and calls ``on_select``; Exit calls ``on_exit``. No lifecycle lives here.
    """
    try:
        selected_index = [d.id for d in ordered].index(selected_id)
    except ValueError:
        selected_index = 0

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

    return ft.Container(
        content=rail,
        bgcolor=tokens.color_surface,
        border=ft.Border(right=ft.BorderSide(1, tokens.color_border)),
    )
