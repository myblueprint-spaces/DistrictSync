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
identical in every state — D7); the initial selection is **Home in every state**
(``nav.initial_destination_id``) since 0038 S6 put the setup wizard on Home. The shell
HOLDS the rail handle and ``select_by_id`` syncs its ``selected_index`` on every id-keyed
hop (via ``nav.selected_index_for``) so programmatic navigation — the wizard-finish
re-entry / fix CTAs / error fallback — moves the highlight too, not only user clicks.

Split again at 0038 S4a for the launch gate: everything after the geometry block is
:func:`build_app_body` (module level, not a 110-line closure), and :func:`main` owns the
BOOT ORDER and the one ``root_host`` whose ``content`` swaps between the launch page and
the app body. See :func:`main` for the enumerated order and why each step sits where it
does — the close-handler hoist in particular is a lifecycle guarantee, not tidiness.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.scheduler import get_scheduler
from src.ui_flet import components, geometry, nav, nav_rail, tokens
from src.ui_flet.identity_gate import gate_reason, needs_identity
from src.ui_flet.screens import identity
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

    **No gradient here (0038 S6).** It led with a ``hero_gradient()`` card whose sub-line
    was a TRANSLUCENT white — a composite the AA contrast function cannot evaluate, so an
    ungated painted pair — and, since S4a reallocated the gradient to the launch page, a
    second gradient surface the design system no longer sanctions. It leads with the
    ordinary ``page_header`` now, like every other surface. This function is effectively
    dead (all six destinations replace their placeholder in ``build_app_body`` before the
    rail renders); it is kept because ``build_screens`` guarantees ``render_by_id`` a
    factory for EVERY destination, so a future rail entry can never KeyError its way to a
    blank pane. (Roadmap item discharged; see ``docs/claugentic-ROADMAP.md``.)
    """
    icon_name = getattr(ft.Icons, dest.selected_icon, ft.Icons.WIDGETS_ROUNDED)
    return ft.Column(
        spacing=22,
        controls=[
            components.page_header(dest.label, "Your nightly roster sync — calm, branded, and built to be trusted."),
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
# Window lifecycle — hoisted so it is bound BEFORE anything is rendered        #
# --------------------------------------------------------------------------- #
def bind_window_lifecycle(page: ft.Page) -> None:
    """Bind the two ZERO-ORPHAN close paths (PLAT-0). Module level, called ONCE, EARLY.

    Hoisted above the launch gate at 0038 S4a, and that position is load-bearing rather
    than tidy: the launch page renders no rail and therefore no Exit button, so the
    title-bar close is the ONLY way out of it. Binding these after the gate — their
    pre-S4a position — would leave the OS close unhandled for exactly as long as that page
    is up, which is precisely when it is the admin's only exit.

    Both paths stay byte-identical to the proven ones: ``page.window.on_event`` routes a
    CLOSE event into the single ``_close_window`` (geometry persist → awaited
    ``destroy()`` → ``os._exit`` fallback), and ``page.on_disconnect`` guarantees the
    python host cannot outlive its view.
    """

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


# --------------------------------------------------------------------------- #
# The app body — everything behind the launch gate                             #
# --------------------------------------------------------------------------- #
def build_app_body(
    page: ft.Page,
    app_cfg: AppConfig,  # noqa: ARG001 - the persist-then-enter seam; see the docstring
) -> ft.Control:
    """The rail + content host + screen map: the whole app, minus the window lifecycle.

    Extracted to module level at 0038 S4a (it was a ~110-line closure inside ``main``) so
    the shell's boot order is legible and the launch gate has ONE thing to swap in. It
    builds and returns a control; it never calls ``page.add`` — ``main`` owns the single
    root host.

    ``app_cfg`` is the config the shell ENTERED with, and since 0038 S6 nothing inside
    reads it: the nav model's launch selection was its last consumer, and that selection is
    now Home in every state. It is kept as a parameter because it is the observable half of
    the persist-then-enter contract (S4a) — the launch page persists the answer, then
    ``_enter_app`` re-loads and hands the FRESH instance here, and the boot tests assert on
    exactly that. Every SCREEN below re-reads ``AppConfig`` per mount (D1) regardless, which
    is what actually makes the first paint correctly scoped; dropping the parameter would
    delete the one point where the ORDER is checkable without weakening it in the code.
    """
    model = nav.nav_model()
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
        if get_scheduler().supports_read_schedule:
            with contextlib.suppress(Exception):
                page.run_thread(_refresh_setup_badge)

    screens["setup"] = lambda: build_setup(page, on_schedule_changed=_on_schedule_changed)
    # Swap the `home` placeholder for the three-way Home surface UNCONDITIONALLY —
    # `build_home` owns the branch decision itself (branch (a) HOSTS the setup wizard when
    # `nav.needs_setup(...)`, (b)/(c) render the verdict-first dashboard). The `on_navigate`
    # / `on_refresh` lambdas close over `select_by_id` (defined below) — Python resolves the free
    # name at call-time (navigation), so this late binding is correct and all screen-map mutation
    # stays co-located here. `on_refresh` re-invokes this screen's build in place (fresh read).
    #
    # `on_schedule_changed` is forwarded (0038 S6) because branch (a)'s hosted wizard runs the
    # SAME register flow the Setup rail item does — a nightly task registered from Home must
    # re-probe the rail badge exactly as one registered from Setup. The shell hands the callback
    # to a screen that only sometimes uses it rather than branching on setup state itself: ONE
    # place decides which Home an admin gets, and it is `build_home`.
    screens["home"] = lambda: build_home(
        page,
        app_config=AppConfig.load(),
        on_navigate=lambda dest: select_by_id(dest),
        on_refresh=lambda: select_by_id("home"),
        on_schedule_changed=_on_schedule_changed,
    )
    # Swap the `convert` placeholder for the real manual-convert surface (IA-5a).
    screens["convert"] = functools.partial(build_convert, page, on_navigate=lambda dest: select_by_id(dest))
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
    screens["help"] = lambda: build_help(
        page,
        app_config=AppConfig.load(),
        # 0038 S4a: the read-only "who looks after this sync" echo offers a one-click hop
        # to Settings, where the address is changeable and clearable.
        on_navigate=lambda dest: select_by_id(dest),
    )
    # Dev-only: behind DISTRICTSYNC_UI_DEMO, route the Help slot to the design-system
    # gallery (3 verdict banners + ErrorCard) so the front-loaded spine is visually
    # exercised. NOT a user nav entry — a hidden override on an existing route.
    if os.environ.get("DISTRICTSYNC_UI_DEMO") and "help" in screens:
        screens["help"] = components.build_design_demo

    ordered = nav.ordered_destinations(model)
    initial_id = nav.initial_destination_id(model)

    # The content area sits on the Direction B wash; screens' white cards float on it.
    content_host = ft.Container(expand=True, padding=pad_sym(36, 28), bgcolor=tokens.color_content_wash)

    def render_by_id(dest_id: str) -> None:
        inner = screens[dest_id]()
        # Scrollable content so tall screens never clip; the reading column is capped at ~960px
        # and CENTRED in the content area (owner decision 2026-08-05, superseding 0032 Tier-1
        # #5's left-anchor: on a maximised window the column hugged the rail with a huge dead
        # right margin). Centring comes from a ROW, not the column's `horizontal_alignment` —
        # on Flet 0.85.3 a scrollable Column silently ignores its cross-axis alignment (the
        # `_gate_frame` trap, recorded in docs/FLET_1.0_CONVENTIONS.md). A fixed width still
        # clamps DOWN to the viewport on a narrow window (Flutter enforces the parent
        # constraint), where centring a full-width child is a no-op — so narrow windows are
        # byte-identical to before. A screen that scrolls a wide region horizontally (Run
        # History's table) does so INSIDE this cap.
        content_host.content = ft.Column(
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[ft.Container(content=inner, width=960)],
                )
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def select_by_id(dest_id: str) -> None:
        render_by_id(dest_id)
        # Sync the rail highlight for BOTH user clicks and programmatic hops (the
        # wizard-finish re-entry / fix CTAs / error fallback). The native rail only self-highlights
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

    body = ft.Row(spacing=0, expand=True, controls=[nav_view, content_host])

    # --- Setup "needs attention" badge (D4): probe the REAL schedule OFF the UI thread --- #
    # The rail must never trust the config flag for the badge — it reflects the tri-state
    # read-back (a task the config believes is registered but Windows no longer has, or one
    # that fired without recording a run). Fetched off-thread so a slow/absent PowerShell can't
    # block paint; the pure `needs_setup_badge` decides; only MISSING-while-expected /
    # contradiction badges (never UNKNOWN). Windows-only (schedule read-back is out of scope
    # elsewhere); a probe failure is swallowed (the badge simply stays clear).
    def _refresh_setup_badge() -> None:  # runs OFF the UI thread
        from src.history.store import read_run_records
        from src.ui_flet.home_status import sync_window_paused
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
        # Window-aware badge: during an enabled seasonal pause the fired-but-no-record
        # contradiction is by design (matches Home's calm "Paused" state) — a MISSING task
        # still badges. `sync_window_paused` is the SAME pure fact Home derives (single source).
        paused = sync_window_paused(cfg, now=None)
        # First-run silence (0038 S6): Home HOSTS the wizard while `needs_setup`, so an
        # attention dot on the Setup rail item would flag the work in progress as a fault.
        # Read here, at probe time, from the SAME predicate Home branches on.
        unfinished = nav.needs_setup(cfg)

        async def _apply() -> None:
            idx = nav.selected_index_for("setup", ordered)
            show_badge = needs_setup_badge(status, paused=paused, setup_unfinished=unfinished)
            rail.destinations[idx].badge = nav_rail.attention_badge() if show_badge else None
            page.update()

        page.run_task(_apply)

    render_by_id(initial_id)

    # The probe lives HERE, at the tail of the app body, so it fires only AFTER entry —
    # never while the launch page is up. A schedule badge on a rail the admin cannot see
    # yet would be work done for nobody, and it would read the profile mid-question.
    if get_scheduler().supports_read_schedule:
        # The badge is advisory; a probe/thread failure simply leaves it clear.
        with contextlib.suppress(Exception):
            page.run_thread(_refresh_setup_badge)

    return body


# The launch form's width. Much narrower than a screen's ~960px reading cap: this is one
# short form, and a form measured in feet is harder to read, not more generous. It is also
# what the page's cards STRETCH to (``screens/identity.py``), so both cards match.
GATE_WIDTH = 460


def _gate_frame(view: ft.Control) -> ft.Control:
    """Frame the launch page: inset, narrow, CENTRED in the window, and still scrollable.

    The launch page mounts into the SAME root host as the app body, which carries no
    padding of its own (the app body's ``content_host`` owns that inset).

    **Centred rather than left-anchored** (2026-08-04), which is the one place in the app
    that is right: every surface BEHIND the rail is left-anchored against it, but this page
    has no rail, so left-anchoring it just pinned a 460px form to the corner of a 1200px
    window with nothing beside it. Both axes are set — ``horizontal_alignment`` on the
    column centres the fixed-width child, and ``alignment`` centres the stack vertically
    when it is shorter than the viewport. ``scroll`` stays on, so a tall state (the
    no-match branch with its district-number field and note) still scrolls instead of
    clipping.
    """
    return ft.Container(
        expand=True,
        padding=pad_sym(36, 28),
        bgcolor=tokens.color_content_wash,
        content=ft.Column(
            controls=[
                # The horizontal centring is done by a ROW, not by the column's
                # `horizontal_alignment`: on Flet 0.85.3 a scrollable Column honours
                # `alignment` (main axis) but NOT `horizontal_alignment` (cross axis), so the
                # cross-axis centring has to come from a control whose MAIN axis is horizontal.
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[ft.Container(content=view, width=GATE_WIDTH)],
                )
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
    )


# --------------------------------------------------------------------------- #
# App shell + lifecycle                                                        #
# --------------------------------------------------------------------------- #
def main(page: ft.Page) -> None:
    """Build the DistrictSync shell. Called by ``ft.run`` from ``launcher.py``.

    **BOOT ORDER (enumerated — each step depends on the one before it):**

    1. **chrome** — title, padding, theme. First, so there is no flash of an unstyled
       window whichever surface paints next.
    2. **geometry** — the saved window bounds, restored CLAMPED to the work area, plus the
       brand icon. Needs the startup ``AppConfig``; native-only, so the whole block is
       failure-tolerant.
    3. **close handlers** — :func:`bind_window_lifecycle`. **ABOVE the gate on purpose:**
       the launch page renders no rail and therefore no Exit button, so the title-bar close
       is the only exit from it. Binding these afterwards would leave that close unhandled
       for exactly as long as the page is up. (Closing at the launch page is an ACCEPTED
       exit — it orphans nothing, and nothing is stored.)
    4. **gate-or-body** — ``page.add(root_host)`` happens exactly ONCE here, and
       ``root_host.content`` is swapped between the launch page and the app body (the
       proven ``content_host`` pattern — no new Flet 0.85.3 API). ``needs_identity`` decides
       which; ``_enter_app`` runs at most once and builds the body from a FRESH
       ``AppConfig.load()`` after the gate, so a just-answered identity is in hand from the
       first paint.
    5. **probes** — the off-thread Setup badge, at the tail of :func:`build_app_body`, so it
       never runs while the launch page is up.

    **The identity FLOOR.** Steps 4's gate is wrapped so that ANY failure in the identity
    layer — the predicate, the page build, or (inside the page) resolution — logs and falls
    through to the app body. Identification can never fail closed: the cost of a bug here
    is an unfiltered district list, never a locked-out admin.
    """
    # --- 1. paint themed chrome FIRST (no flash of unstyled window) -------- #
    page.title = "DistrictSync"
    page.padding = 0
    # Direction B (0033 Slice 2): the content area sits on the calm ``color_content_wash``
    # (white cards float on it); the navy rail owns the contrast. Set on the page too so there
    # is no flash of the brand page-tint before the content host paints.
    page.bgcolor = tokens.color_content_wash
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = build_theme()

    # Startup snapshot, consumed by the geometry restore (window bounds) and by the identity
    # layer: `needs_identity` decides whether the launch page shows, and the page itself
    # renders from this instance. It no longer feeds the NAV model — since 0038 S6 the launch
    # selection is Home in every state — and the app body behind the gate reads nothing from
    # it (`_enter_app` re-loads on the persist-then-enter path; see `build_app_body`).
    app_cfg = AppConfig.load()

    # --- 2. window sizing + brand icon (native mode only; harmless in web) -- #
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

    # --- 3. close handlers, BEFORE anything is rendered (see the docstring) - #
    bind_window_lifecycle(page)

    # --- 4. the ONE root host; its content is the gate OR the app body ------ #
    # No padding here: the app body's own `content_host` owns the content inset, and the
    # launch page is framed by `_gate_frame` below. Double padding would inset the rail.
    root_host = ft.Container(expand=True, bgcolor=tokens.color_content_wash)
    page.add(root_host)

    entered = False

    def _enter_app(app_config: AppConfig | None = None) -> None:
        """Swap the app body in. Idempotent — a second call is a no-op, by design.

        The launch page can reach this from several affordances (Get started, the
        correction link, the escape) and its own error floor calls it too; a double entry
        would stack a second rail + screen map on the same host.

        ``app_config=None`` means **read the settings again**: the launch page persists the
        answer and THEN calls this, so the app body's first paint must see what was just
        written (D1's per-mount freshness only helps from the NEXT hop onward). The
        no-gate path passes the startup snapshot explicitly — nothing has changed under it.

        **The latch is set only on SUCCESS**, and that is not a detail. Arming it first
        would mean a TRANSIENT failure (a locked profile, a probe that raised once) left the
        launch page still mounted with the latch already down: every affordance on it — Get
        started, the correction, the escape — would become a silent no-op, forever, with no
        error on screen. The admin would be stuck in front of their own sync by a bug that
        had already passed. A failed entry therefore leaves the door open for the next press
        AND re-raises, so the failure is visible rather than absorbed.
        """
        nonlocal entered
        if entered:
            return
        body = build_app_body(page, AppConfig.load() if app_config is None else app_config)
        entered = True
        root_host.content = body
        page.update()

    try:
        show_gate = needs_identity(app_cfg)
        logger.info("identity gate: shown=%s reason=%s", show_gate, gate_reason(app_cfg))
    except Exception:  # noqa: BLE001 - the floor: a broken predicate must not block the app
        logger.warning("The launch-page check failed; opening DistrictSync as usual.", exc_info=True)
        show_gate = False

    if not show_gate:
        _enter_app(app_cfg)
        return

    try:
        root_host.content = _gate_frame(
            identity.build_identity(
                page,
                app_config=app_cfg,
                on_enter=_enter_app,  # no argument -> a FRESH AppConfig.load() (persist-then-enter)
            )
        )
        page.update()
    except Exception:  # noqa: BLE001 - the floor: a broken launch page opens the app unfiltered
        logger.warning("The launch page could not be built; opening DistrictSync as usual.", exc_info=True)
        _enter_app(app_cfg)
