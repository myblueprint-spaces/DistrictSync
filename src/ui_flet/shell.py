"""The Flet app shell — themed window + navigation assembly + branded placeholders.

VIEW glue (coverage-omitted): the trust-critical logic lives in the pure modules
(``tokens``/``theme``/``nav``); this file wires them into a window. It follows the
PROVEN API forms from ``docs/reference/flet-prototype-spike/app.py`` verbatim
(Flet 0.85.3) — do NOT regress to remembered 0.2x forms (see
``docs/FLET_1.0_CONVENTIONS.md``).

Slimmed at IA-1 (plan 0014 F6 split): the rail VIEW moved to ``nav_rail.py`` — the
shell now owns window paint + sizing, the placeholder host, id-keyed selection, and
the close lifecycle, and assembles the state-aware rail from ``nav_rail.build_nav``.
The rail is a single flat ``ft.NavigationRail`` reordered so the prominent group
leads (``nav.ordered_destinations``); the initial selection is the prominent group's
first destination (``nav.prominent_initial_id``). Highlight is native — the shell
holds no rail reference and never mutates ``selected_index`` after creation.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, nav, nav_rail, tokens
from src.ui_flet.screens.convert import build_convert, is_write_in_flight
from src.ui_flet.screens.help import build_help
from src.ui_flet.screens.home import build_home
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.run_history import build_run_history
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.theme import build_theme

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Flet 0.85 layout helpers (the old ft.padding.* / ft.border.* funcs are gone) #
# (verbatim from the proven prototype)                                         #
# --------------------------------------------------------------------------- #
def pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


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
            components.card(
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
                gradient=components.hero_gradient(),
                padding=pad_sym(32, 26),
                border_radius=18,
            ),
            components.card(
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
                padding=48,
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
def _on_leave(page: ft.Page) -> None:  # noqa: ARG001  (seam — read-only, never blocks the close)
    """Leave-point seam for window close — reads the Convert write-in-flight flag (IA-5b).

    The decouple-the-sync reassurance is AMBIENT — a persistent line in ``nav_rail``
    above Exit (IA-2), always on-screen regardless of which leave path is taken — so it
    is NOT wired as a close-time interruption here.

    IA-5b wires the write-in-flight guard (C6) at this seam: it reads
    ``convert.is_write_in_flight()`` and, if a Convert atomic write is committing, logs
    a debug note. It is **REASSURANCE-ONLY** — it does NOT block the atomic close. The
    loader's backup-and-restore ``save_all`` atomicity is the real safety net: an
    interrupted commit rolls back, so the output dir is never torn. Blocking the close
    on a pandas write would risk the freeze/zombie the Flet migration deleted; the flag
    makes the invariant explicit + gives a future field-justified confirm a seam. The
    zero-orphan ``page.window.destroy()`` path stays byte-identical.
    """
    if is_write_in_flight():
        logger.debug(
            "Window closing while a Convert write is committing — the atomic save_all "
            "completes or rolls back cleanly; not blocking the close."
        )
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

    # Startup-only snapshot: drives the nav MODEL (order + launch selection) at build time.
    # Nav is reworked in Slices 3/5; keeping the startup config here is a bounded, known
    # remainder — every SCREEN below loads AppConfig fresh, so display state is never stale.
    app_cfg = AppConfig.load()
    model = nav.nav_model(app_cfg)
    screens = build_screens(model.destinations)
    # Config-freshness (D1): the screens that render config-derived state bind a fresh
    # `AppConfig.load()` per invocation (the supplier pattern Setup/Convert already use) — NOT the
    # startup instance — so switching district / finishing setup propagates on the next navigation
    # or Refresh, never only after a restart. `build_screens` values stay `Callable[[], ft.Control]`
    # (a plain lambda), so `render_by_id`'s uniform `screens[dest_id]()` call is untouched (RC4).
    #
    # Setup + Convert already load AppConfig fresh internally (they take only `page`), so they keep
    # the `functools.partial(build_*, page)` mount form.
    screens["setup"] = functools.partial(build_setup, page)
    # Swap the `home` placeholder for the three-way health dashboard UNCONDITIONALLY —
    # `build_home` owns the branch decision itself (branch (a) reuses `build_onboarding`
    # when `nav.needs_setup(...)`, (b)/(c) render the verdict-first dashboard). The `on_navigate`
    # / `on_refresh` lambdas close over `select_by_id` (defined below) — Python resolves the free
    # name at call-time (navigation), so this late binding is correct and all screen-map mutation
    # stays co-located here. `on_refresh` re-invokes this screen's build in place (fresh read).
    screens["home"] = lambda: build_home(
        page,
        app_config=AppConfig.load(),
        on_navigate=lambda dest: select_by_id(dest),
        on_refresh=lambda: select_by_id("home"),
    )
    # Swap the `convert` placeholder for the real manual-convert surface (IA-5a).
    screens["convert"] = functools.partial(build_convert, page)
    # Swap the `run_history` placeholder for the real read-only Run History surface (IA-6).
    screens["run_history"] = lambda: build_run_history(
        page,
        app_config=AppConfig.load(),
        on_refresh=lambda: select_by_id("run_history"),
    )
    # Swap the `mapping` placeholder for the real review-and-switch district-config surface (IA-8a).
    screens["mapping"] = lambda: build_mapping(page, app_config=AppConfig.load())
    # Swap the `help` placeholder for the real link-out Help surface (IA-7). Placed BEFORE the
    # DISTRICTSYNC_UI_DEMO override below so the dev override still wins (it re-assigns last).
    screens["help"] = lambda: build_help(page, app_config=AppConfig.load())
    # Dev-only: behind DISTRICTSYNC_UI_DEMO, route the Help slot to the design-system
    # gallery (3 verdict banners + ErrorCard) so the front-loaded spine is visually
    # exercised. NOT a user nav entry — a hidden override on an existing route.
    if os.environ.get("DISTRICTSYNC_UI_DEMO") and "help" in screens:
        screens["help"] = components.build_design_demo

    ordered = nav.ordered_destinations(model)
    initial_id = nav.prominent_initial_id(model)

    content_host = ft.Container(expand=True, padding=pad_sym(36, 28))

    def render_by_id(dest_id: str) -> None:
        inner = screens[dest_id]()
        # Scrollable content so tall screens never clip.
        content_host.content = ft.Column(controls=[inner], scroll=ft.ScrollMode.AUTO, expand=True)

    def select_by_id(dest_id: str) -> None:
        render_by_id(dest_id)
        page.update()

    # --- exit affordance (lifecycle owner stays in the shell) -------------- #
    def do_exit(_e: ft.ControlEvent | None = None) -> None:
        _on_leave(page)
        try:
            page.window.destroy()
        except Exception:
            os._exit(0)

    # --- left navigation rail (state-aware reorder; view lives in nav_rail) - #
    nav_view = nav_rail.build_nav(
        ordered=ordered,
        selected_id=initial_id,
        on_select=select_by_id,
        on_exit=do_exit,
    )

    page.add(ft.Row(spacing=0, expand=True, controls=[nav_view, content_host]))

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

    render_by_id(initial_id)
    page.update()
