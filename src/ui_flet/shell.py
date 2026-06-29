"""The Flet app shell — themed window + flat navigation + branded placeholders.

VIEW glue (coverage-omitted): the trust-critical logic lives in the pure modules
(``tokens``/``theme``/``nav``); this file wires them into a window. It follows the
PROVEN API forms from ``docs/reference/flet-prototype-spike/app.py`` verbatim
(Flet 0.85.3) — do NOT regress to remembered 0.2x forms (see
``docs/FLET_1.0_CONVENTIONS.md``).

Concerns (window · rail · placeholder host · close lifecycle) co-live here at
PLAT-1 size; this is the documented **boundary to split before IA surfaces grow
it** (IA-1 is the natural point — see plan 0014 F6). The rail renders FLAT this
slice (the ``nav`` prominence model exists but its render wiring lands at IA-1).
"""

from __future__ import annotations

import os
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import nav, tokens
from src.ui_flet.theme import build_theme


# --------------------------------------------------------------------------- #
# Flet 0.85 layout helpers (the old ft.padding.* / ft.border.* funcs are gone) #
# (verbatim from the proven prototype)                                         #
# --------------------------------------------------------------------------- #
def pad(*, left: float = 0, top: float = 0, right: float = 0, bottom: float = 0) -> ft.Padding:
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


def pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def b_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, bottom=side, left=side, right=side)


def b_only(
    *,
    top: ft.BorderSide | None = None,
    right: ft.BorderSide | None = None,
    bottom: ft.BorderSide | None = None,
    left: ft.BorderSide | None = None,
) -> ft.Border:
    return ft.Border(top=top, right=right, bottom=bottom, left=left)


# --------------------------------------------------------------------------- #
# Branded, in-voice placeholder (NOT "coming soon"/TODO — sets the product tone) #
# --------------------------------------------------------------------------- #
def build_placeholder(dest: nav.Destination) -> ft.Control:
    """A calm, branded frame for a surface that hasn't landed yet.

    Reassuring product voice — never a dev stub. Every real surface (IA-1+) drops
    into this same frame, so the tone here is the tone the whole app inherits.
    """
    icon_name = getattr(ft.Icons, dest.selected_icon, ft.Icons.WIDGETS_ROUNDED)
    return ft.Column(
        spacing=22,
        controls=[
            ft.Container(
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(-1, -1),
                    end=ft.Alignment(1, 1),
                    colors=[tokens.color_action_primary_strong, tokens.color_action_primary],
                ),
                padding=pad_sym(32, 26),
                border_radius=18,
                content=ft.Column(
                    spacing=4,
                    controls=[
                        ft.Text(dest.label, size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                        ft.Text(
                            "Your nightly roster sync — calm, branded, and built to be trusted.",
                            size=14,
                            color=ft.Colors.with_opacity(0.85, tokens.color_on_action),
                        ),
                    ],
                ),
            ),
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=16,
                    controls=[
                        ft.Container(
                            content=ft.Icon(icon_name, size=36, color=tokens.color_action_primary),
                            width=80,
                            height=80,
                            bgcolor=tokens.page_bg,
                            border_radius=40,
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Text(dest.label, size=20, weight=ft.FontWeight.W_700, color=tokens.color_text),
                        ft.Text(
                            "This part of DistrictSync is on its way.",
                            size=15,
                            weight=ft.FontWeight.W_600,
                            color=tokens.color_text,
                        ),
                        ft.Text(
                            "We're polishing it now so it's ready when you need it. "
                            "Everything you rely on today keeps running in the background.",
                            size=14,
                            color=tokens.color_muted,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                ),
                bgcolor=tokens.color_surface,
                padding=48,
                border_radius=16,
                border=b_all(1, tokens.color_border),
            ),
        ],
    )


def build_screens(destinations: tuple[nav.Destination, ...]) -> dict[str, Callable[[], ft.Control]]:
    """Plain ``dict[destination_id -> placeholder factory]``.

    A plain dict, not a registry (YAGNI for one-liner placeholders) — IA-1 swaps a
    factory for a real surface by replacing an entry. Factories are deferred (built
    on selection) so a tall screen is only constructed when navigated to.
    """
    return {dest.id: (lambda d=dest: build_placeholder(d)) for dest in destinations}


# --------------------------------------------------------------------------- #
# Lifecycle leave-point seam (documented hook; NO guard logic this slice)      #
# --------------------------------------------------------------------------- #
def _on_leave(page: ft.Page) -> None:  # noqa: ARG001  (seam — intentionally a no-op)
    """Leave-point seam for window close.

    Intentionally a no-op at PLAT-1. IA-2 attaches the "closing this window does
    not stop the nightly sync" reassurance here; IA-5 attaches the write-in-flight
    guard (the loader's backup-and-restore atomicity remains the real safety net).
    Keeping the hook + its docstring now means those slices wire behaviour into an
    existing seam instead of re-architecting the close path.
    """
    return None


# --------------------------------------------------------------------------- #
# App shell + lifecycle                                                        #
# --------------------------------------------------------------------------- #
def main(page: ft.Page) -> None:
    """Build the DistrictSync shell. Called by ``ft.run`` from ``launcher.py``."""
    # --- paint themed chrome FIRST (no flash of unstyled window) ----------- #
    page.title = "DistrictSync"
    page.padding = 0
    page.bgcolor = tokens.page_bg
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = build_theme()

    # --- window sizing (native mode only; harmless in web) ----------------- #
    try:
        page.window.width = 1180
        page.window.height = 860
        page.window.min_width = 940
        page.window.min_height = 680
    except Exception:  # nosec B110 — window sizing is native-only; harmless no-op in web mode
        pass

    model = nav.nav_model(AppConfig.load())
    screens = build_screens(model.destinations)
    destinations = model.destinations

    content_host = ft.Container(expand=True, padding=pad_sym(36, 28))

    def render(index: int) -> None:
        dest = destinations[index]
        inner = screens[dest.id]()
        # Scrollable content so tall screens never clip.
        content_host.content = ft.Column(controls=[inner], scroll=ft.ScrollMode.AUTO, expand=True)

    def select(index: int) -> None:
        rail.selected_index = index
        render(index)
        page.update()

    def on_nav_change(e: ft.ControlEvent) -> None:
        select(e.control.selected_index)

    # --- exit affordance --------------------------------------------------- #
    def do_exit(_e: ft.ControlEvent | None = None) -> None:
        _on_leave(page)
        try:
            page.window.destroy()
        except Exception:
            os._exit(0)

    exit_btn = ft.Container(
        content=ft.TextButton(
            "Exit",
            icon=ft.Icons.LOGOUT_ROUNDED,
            on_click=do_exit,
            style=ft.ButtonStyle(
                color=tokens.color_muted,
                text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_600),
            ),
        ),
        padding=pad(bottom=12),
    )

    # --- left navigation rail (FLAT — prominence wiring is IA-1) ----------- #
    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=104,
        min_extended_width=180,
        bgcolor=tokens.color_surface,
        indicator_color=ft.Colors.with_opacity(0.14, tokens.color_action_primary),
        on_change=on_nav_change,
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
            padding=pad(top=14, bottom=18),
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=getattr(ft.Icons, dest.icon, ft.Icons.CIRCLE_OUTLINED),
                selected_icon=getattr(ft.Icons, dest.selected_icon, ft.Icons.CIRCLE),
                label=dest.label,
            )
            for dest in destinations
        ],
        trailing=ft.Container(
            expand=True,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.END,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
                controls=[exit_btn],
            ),
        ),
    )

    rail_wrap = ft.Container(
        content=rail,
        bgcolor=tokens.color_surface,
        border=b_only(right=ft.BorderSide(1, tokens.color_border)),
    )

    page.add(ft.Row(spacing=0, expand=True, controls=[rail_wrap, content_host]))

    # --- graceful window-close handling (native): ZERO orphans ------------- #
    def on_window_event(e: ft.WindowEvent) -> None:
        etype = getattr(e, "type", None)
        if etype == ft.WindowEventType.CLOSE or getattr(e, "data", None) == "close":
            _on_leave(page)
            try:
                page.window.destroy()
            except Exception:
                os._exit(0)

    try:
        # prevent_close=False -> the OS close button tears the app down on its own;
        # the handler still binds so any explicit close path destroys cleanly.
        page.window.prevent_close = False
        page.window.on_event = on_window_event
    except Exception:  # nosec B110 — window lifecycle is native-only; harmless no-op in web mode
        pass

    # When the desktop client disconnects, ensure the host process doesn't orphan.
    def on_disconnect(_e: ft.ControlEvent) -> None:
        os._exit(0)

    page.on_disconnect = on_disconnect

    render(0)
    page.update()
