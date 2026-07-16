"""The Flet app shell — themed window + navigation assembly + branded placeholders.

VIEW glue (coverage-omitted): the trust-critical logic lives in the pure modules
(``tokens``/``theme``/``nav``); this file wires them into a window. It uses only
API forms PROVEN against the pinned Flet 0.85.3 in the 2026-06-29 bake-off spike
and recorded in ``docs/FLET_1.0_CONVENTIONS.md`` — do NOT regress to remembered
0.2x forms.

Slimmed at IA-1 (plan 0014 F6 split): the rail VIEW moved to ``nav_rail.py`` — the
shell now owns window paint + sizing, the placeholder host, id-keyed selection, and
the close lifecycle, and assembles the rail from ``nav_rail.build_nav``. The rail is
a single flat ``ft.NavigationRail`` in ONE fixed order (``nav.ordered_destinations``,
identical in every state — D7); the initial selection is Setup while the install
``needs_setup``, else Home (``nav.prominent_initial_id``). The shell HOLDS the rail
handle and ``select_by_id`` syncs its ``selected_index`` on every id-keyed hop (via
``nav.selected_index_for``) so programmatic navigation — Home's "Start setup" / fix
CTAs / error fallback — moves the highlight too, not only user clicks.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import sys
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, geometry, nav, nav_rail, tokens
from src.ui_flet.screens.convert import build_convert, is_write_in_flight
from src.ui_flet.screens.help import build_help
from src.ui_flet.screens.home import build_home
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.run_history import build_run_history
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.theme import build_theme
from src.utils import paths

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


def _persist_window_geometry(page: ft.Page) -> None:
    """Best-effort: remember the window bounds for the next launch (0032 T2 #8).

    Reads whatever ``page.window`` currently reports (the Flet client patches window
    properties back to the Python dataclass) through the TOTAL ``geometry.persist_plan``:
    a mock/absent/NaN value keeps the previously-saved one, and a maximized window keeps
    its previous normal-state bounds while recording ``maximized=True``. NEVER raises —
    geometry persistence must never block or break an exit path.
    """
    try:
        cfg = AppConfig.load()
        saved = geometry.persist_plan(
            current_width=getattr(page.window, "width", None),
            current_height=getattr(page.window, "height", None),
            current_left=getattr(page.window, "left", None),
            current_top=getattr(page.window, "top", None),
            current_maximized=getattr(page.window, "maximized", None),
            previous=geometry.SavedGeometry(
                width=cfg.window_width,
                height=cfg.window_height,
                left=cfg.window_left,
                top=cfg.window_top,
                maximized=cfg.window_maximized,
            ),
        )
        cfg.window_width = saved.width
        cfg.window_height = saved.height
        cfg.window_left = saved.left
        cfg.window_top = saved.top
        cfg.window_maximized = saved.maximized
        cfg.save()
    except Exception:  # noqa: BLE001 - advisory persistence; the exit path must stay unblockable
        logger.debug("Window geometry not persisted (best-effort).", exc_info=True)


async def _close_window(page: ft.Page) -> None:
    """The ONE exit path — shared by the Exit button (``do_exit``) and the OS close
    event (``on_window_event``) so the two can never drift.

    Flet 0.85.3 ``Window.destroy()`` is a coroutine (``flet/controls/core/window.py``);
    the previous *synchronous* call was an un-awaited coroutine — a silent no-op, which
    is why the Exit button did nothing (no exception raised, so the ``os._exit`` fallback
    never fired either). ``await`` it here so the window actually tears down (collapsing
    the ``python → python → flet.exe`` tree — zero orphans, PLAT-0). ``os._exit(0)`` stays
    as the last-resort fallback if ``destroy()`` can't complete, so the host process can
    never orphan. The zero-orphan ``page.on_disconnect`` path is untouched.

    Window geometry is persisted HERE, before ``destroy()`` (0032 T2 #8): the in-app Exit
    button always passes through, and the OS title-bar close does too whenever its CLOSE
    event reaches Python before teardown (best-effort by design — persistence is advisory
    and never blocks the proven zero-orphan close).
    """
    _on_leave(page)
    _persist_window_geometry(page)
    try:
        await page.window.destroy()
    except Exception:
        os._exit(0)


# --------------------------------------------------------------------------- #
# App shell + lifecycle                                                        #
# --------------------------------------------------------------------------- #
def main(page: ft.Page) -> None:
    """Build the DistrictSync shell. Called by ``ft.run`` from ``launcher.py``."""
    # --- paint themed chrome FIRST (no flash of unstyled window) ----------- #
    page.title = "DistrictSync"
    page.padding = 0
    # Direction B (0033 Slice 2): the content area sits on the calm ``color_content_wash``
    # (white cards float on it); the navy rail owns the contrast. Set on the page too so there
    # is no flash of the brand page-tint before the content host paints.
    page.bgcolor = tokens.color_content_wash
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = build_theme()

    # Startup-only snapshot: drives the nav MODEL's launch selection at build time (the rail
    # ORDER is fixed and config-independent — D7). Keeping the startup config here is a bounded,
    # known remainder — the launch predicate re-keys from `needs_setup` to `setup_completed` in
    # Slice 5; every SCREEN below already loads AppConfig fresh, so display state is never stale.
    # Loaded BEFORE the sizing block since the geometry restore reads the saved window bounds.
    app_cfg = AppConfig.load()

    # --- window sizing + brand icon (native mode only; harmless in web) ----- #
    try:
        # Geometry restore (0032 T2 #8): the saved bounds via the pure `geometry.restore_plan`
        # — size shrunk to the current work area, position applied only CLAMPED inside it (a
        # window restored onto a since-removed monitor is a support call), first-run height
        # min(860, work-area height). Defaults/minimums are single-sourced in `geometry`.
        plan = geometry.restore_plan(
            geometry.SavedGeometry(
                width=app_cfg.window_width,
                height=app_cfg.window_height,
                left=app_cfg.window_left,
                top=app_cfg.window_top,
                maximized=app_cfg.window_maximized,
            ),
            geometry.probe_work_area(),
        )
        page.window.width = plan.width
        page.window.height = plan.height
        page.window.min_width = geometry.MIN_WIDTH
        page.window.min_height = geometry.MIN_HEIGHT
        if plan.left is not None:
            page.window.left = plan.left
        if plan.top is not None:
            page.window.top = plan.top
        if plan.maximized:
            # Set LAST among the bounds so an unmaximize returns to the restored size.
            page.window.maximized = True
        # Brand the running window/title-bar/taskbar with the myBlueprint mark
        # (owner decision 2026-07-15: myB on the bar up top; the EXE file keeps the
        # DistrictSync sync mark via flet-pack --icon). Resolved via the pure
        # `paths.window_icon_path()` (dev tree vs frozen `_MEIPASS`); set LAST so a
        # failure here can't skip sizing.
        page.window.icon = str(paths.window_icon_path())
    except Exception:  # nosec B110 — window sizing/icon are native-only; harmless no-op in web mode
        pass
    model = nav.nav_model(app_cfg)
    screens = build_screens(model.destinations)

    # Config-freshness (D1): the screens that render config-derived state bind a fresh
    # `AppConfig.load()` per invocation (the supplier pattern Setup/Convert already use) — NOT the
    # startup instance — so switching district / finishing setup propagates on the next navigation
    # or Refresh, never only after a restart. `build_screens` values stay `Callable[[], ft.Control]`
    # (a plain lambda), so `render_by_id`'s uniform `screens[dest_id]()` call is untouched (RC4).
    #
    # Setup + Convert already load AppConfig fresh internally, so they keep the
    # page-only mount form (Convert stays a `functools.partial`).
    #
    # Setup-badge freshness (0032 T1 #8): the rail's attention badge is probed once at boot,
    # so a register/unregister SUCCESS inside Setup could leave it stale until a restart. The
    # shell (the badge owner) hands Setup a re-probe callback — fired only after a CONFIRMED
    # register/unregister — that re-runs the SAME off-thread probe + rail repaint machinery
    # (`_refresh_setup_badge`, resolved late at call time like `select_by_id`). Advisory:
    # a probe/thread failure simply leaves the badge as-is.
    def _on_schedule_changed() -> None:
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                page.run_thread(_refresh_setup_badge)

    screens["setup"] = lambda: build_setup(page, on_schedule_changed=_on_schedule_changed)
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
    # `on_navigate` (Home's pattern) lets the post-Apply stale-schedule notice route to Settings
    # with rail-follow (0034 Slice 1).
    screens["mapping"] = lambda: build_mapping(
        page,
        app_config=AppConfig.load(),
        on_navigate=lambda dest: select_by_id(dest),
    )
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

    # The content area sits on the Direction B wash; screens' white cards float on it.
    content_host = ft.Container(expand=True, padding=pad_sym(36, 28), bgcolor=tokens.color_content_wash)

    def render_by_id(dest_id: str) -> None:
        inner = screens[dest_id]()
        # Scrollable content so tall screens never clip; the reading column is capped at ~960px
        # and LEFT-anchored (0032 Tier-1 #5) — a fixed width clamps DOWN to the viewport on a
        # narrow window (Flutter enforces the parent constraint), so it never overflows. A screen
        # that scrolls a wide region horizontally (Run History's table) does so INSIDE this cap.
        content_host.content = ft.Column(
            controls=[ft.Container(content=inner, width=960)],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def select_by_id(dest_id: str) -> None:
        render_by_id(dest_id)
        # Sync the rail highlight for BOTH user clicks and programmatic hops (Home's
        # "Start setup" / fix CTAs / error fallback). The native rail only self-highlights
        # on click, so code-driven navigation must set the index here — single-sourced
        # through `nav.selected_index_for` so a click and a code hop can never diverge.
        # (`rail` is bound below before any navigation fires; resolved late at call time.)
        rail.selected_index = nav.selected_index_for(dest_id, ordered)
        page.update()

    # --- exit affordance (lifecycle owner stays in the shell) -------------- #
    # Async handler: Flet 0.85.3 supports coroutine event handlers, and
    # `page.window.destroy()` MUST be awaited (see `_close_window`).
    async def do_exit(_e: ft.ControlEvent | None = None) -> None:
        await _close_window(page)

    # --- left navigation rail (fixed order; view lives in nav_rail) -------- #
    # Hold the rail handle so `select_by_id` can sync `selected_index` on programmatic nav.
    nav_view, rail = nav_rail.build_nav(
        ordered=ordered,
        selected_id=initial_id,
        on_select=select_by_id,
        on_exit=do_exit,
    )

    page.add(ft.Row(spacing=0, expand=True, controls=[nav_view, content_host]))

    # --- Setup "needs attention" badge (D4): probe the REAL schedule OFF the UI thread --- #
    # The rail must never trust the config flag for the badge — it reflects the tri-state
    # read-back (a task the config believes is registered but Windows no longer has, or one
    # that fired without recording a run). Fetched off-thread so a slow/absent PowerShell can't
    # block paint; the pure `needs_setup_badge` decides; only MISSING-while-expected /
    # contradiction badges (never UNKNOWN). Windows-only (schedule read-back is out of scope
    # elsewhere); a probe failure is swallowed (the badge simply stays clear).
    def _refresh_setup_badge() -> None:  # runs OFF the UI thread
        from src.history.store import read_run_records
        from src.ui_flet.schedule_probe import probe_schedule
        from src.ui_flet.schedule_status import needs_setup_badge

        cfg = AppConfig.load()
        records = read_run_records()
        latest_ts = records[0].get("timestamp") if records else None
        status = probe_schedule(
            cfg.schedule_task_name,
            hint_registered=cfg.schedule_registered,
            latest_record_ts=latest_ts,
        )

        async def _apply() -> None:
            idx = nav.selected_index_for("setup", ordered)
            rail.destinations[idx].badge = nav_rail.attention_badge() if needs_setup_badge(status) else None
            page.update()

        page.run_task(_apply)

    if sys.platform == "win32":
        # The badge is advisory; a probe/thread failure simply leaves it clear.
        with contextlib.suppress(Exception):
            page.run_thread(_refresh_setup_badge)

    # --- graceful window-close handling (native): ZERO orphans ------------- #
    async def on_window_event(e: ft.WindowEvent) -> None:
        etype = getattr(e, "type", None)
        if etype == ft.WindowEventType.CLOSE or getattr(e, "data", None) == "close":
            await _close_window(page)

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
