"""The three-way Home health dashboard — the flagship trust surface (IA model IA-3).

VIEW glue (coverage-omitted): the trust-critical *decision* lives COUNTED in the pure
modules (``history.store.read_run_records`` reads the run store; ``home_status.derive_home_status``
derives the verdict). This file only RENDERS that already-tested output, verdict-first,
so a non-technical admin's deep question — *"is my sync OK?"* — is answered in one
plain-language banner before any metric.

Three-way dispatch (mirrors the IA model + IA-2's promise that onboarding is branch (a)):
  * **(a) unconfigured** — ``nav.needs_setup(app_config)`` → reuse ``build_onboarding``
    VERBATIM (the same hero the IA-2 shell showed), with an ``on_start_setup`` that
    navigates to Setup. No throwaway.
  * **(b) configured + healthy** — a green ``HealthVerdictBanner`` + light metric tiles
    (entity counts, a PLAIN last-run time, SFTP delivered ✓) + the friendly district
    greeting.
  * **(c) configured + broken / attention / empty / unavailable** — an amber/red banner
    NAMING the fault (from the pure derivation, never a raw ``error``/path) + a concrete
    fix-path CTA (``status.fix``).

Built as a **callback-driven factory** — ``build_home`` owns NO navigation or lifecycle
(``on_navigate(dest_id)`` is injected by the shell = ``select_by_id``), mirroring
``onboarding``/``nav_rail`` discipline.

Assembled ENTIRELY from ``components.py`` (cards/buttons/banner) + ``tokens`` — never
hand-rolled controls (the ``FilledButton(text=)`` trap; see ``docs/FLET_1.0_CONVENTIONS.md``).

**Never-crash floor:** the configured-branch read/derive/render is wrapped in
``try/except`` → ``components.ErrorCard`` on any unexpected error, so even a view-layer bug
shows a calm surface, never a stack trace. Defense-in-depth — the parser + derivation are
already TOTAL (their tests prove it); this is the reliability net DS-1 shipped ``ErrorCard``
for.

**Sync read on mount** (no loading state): the run store is a small local SQLite DB read to
a ``list[dict]`` (microseconds), so it is read inline in the factory — the worker-thread
convention is scoped to ``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``), and an
async path here would add the doc's #1 concurrency trap for no user-perceptible gain
(YAGNI). The empty state IS a real reachable state and is rendered (via the pure derivation's
"no runs yet" branch); a loading skeleton is deliberately NOT built (nothing async to load).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.history.store import read_run_records, store_meta
from src.ui_flet import components, nav, tokens
from src.ui_flet.home_status import ENTITY_LABELS, FixAction, HomeMetrics, derive_home_status
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.screens.onboarding import build_onboarding
from src.ui_flet.verdict import Verdict


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _header(app_config: AppConfig, on_refresh: Callable[[], None] | None) -> ft.Control:
    """The Direction B page header: title + sub, with the district chip + Refresh in the right slot.

    Replaces the gradient greeting hero (0033 Slice 2) — the greeting demotes to the header
    subtitle, the district identity rides as a ``district_chip``, and Refresh becomes the
    text-tier affordance the mockup puts in the header (not a standalone secondary button).
    """
    friendly = friendly_district_name(app_config.sis_type)
    trailing_controls: list[ft.Control] = []
    if friendly:
        trailing_controls.append(components.district_chip(friendly))
    if on_refresh is not None:
        trailing_controls.append(
            components.text_button("Refresh", lambda _e: on_refresh(), icon=ft.Icons.REFRESH_ROUNDED)
        )
    trailing = (
        ft.Row(
            spacing=tokens.space_md,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=trailing_controls,
        )
        if trailing_controls
        else None
    )
    return components.page_header("Home", "Your nightly roster sync to SpacesEDU", trailing=trailing)


def _schedule_card(status: ScheduleStatus, on_navigate: Callable[[str], None]) -> ft.Control:
    """A calm "nightly sync scheduled — Confirmed" row-card (Direction B), LIVE state only.

    Surfaces the already-fetched schedule read-back in the mockup's schedule row idiom: a
    ``color_chip_bg`` icon square, the plain readout line, a ``status_pill`` "Confirmed", and a
    text-tier "Change schedule" that hops to Setup. Rendered ONLY on a clean LIVE schedule — a
    MISSING/contradicted schedule is already the dominant WARNING routed to Setup by the verdict
    band above (never both), so this card never competes with an attention state.
    """
    return components.card(
        content=ft.Row(
            spacing=tokens.space_md + 2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=36,
                    height=36,
                    bgcolor=tokens.color_chip_bg,
                    border_radius=tokens.radius_md,
                    alignment=ft.Alignment(0, 0),
                    content=ft.Icon(ft.Icons.SCHEDULE_ROUNDED, size=19, color=tokens.MB_DARK),
                ),
                ft.Container(
                    expand=True,
                    content=ft.Column(
                        spacing=2,
                        controls=[
                            ft.Text(
                                "Nightly sync scheduled",
                                size=tokens.type_emphasis,
                                weight=ft.FontWeight.W_700,
                                color=tokens.color_text,
                            ),
                            ft.Text(status.detail, size=tokens.type_body, color=tokens.color_muted),
                        ],
                    ),
                ),
                ft.Row(
                    spacing=tokens.space_md + 2,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        components.status_pill("Confirmed", Verdict.HEALTHY),
                        components.text_button("Change schedule", lambda _e: on_navigate("setup")),
                    ],
                ),
            ],
        ),
        padding=_pad_sym(tokens.space_lg + 4, tokens.space_lg),
    )


def _metric_tiles_row(metrics: HomeMetrics) -> ft.Control:
    """The light metric-tiles row: entity counts + plain last-run time + SFTP ✓.

    Renders EXACTLY the entity counts present in ``metrics.entity_counts`` (the pure
    derivation already trimmed it to the 5 rostering tiles + myBlueprint+ only-when-nonzero
    — this view never adds a zero-tile or ``StudentAttendance``).
    """
    tiles: list[ft.Control] = [
        components.metric_tile(ENTITY_LABELS.get(name, name), str(count))
        for name, count in metrics.entity_counts.items()
    ]
    tiles.append(components.metric_tile("Last run", metrics.last_run_display))
    if metrics.sftp_delivered:
        tiles.append(components.metric_tile("Delivery", "Delivered to SpacesEDU ✓"))
    return ft.Row(spacing=16, wrap=True, controls=tiles)


def _fix_button(fix: FixAction, on_navigate: Callable[[str], None]) -> ft.Control:
    """The concrete fix-path CTA under the verdict (only when a ``FixAction`` is present)."""
    return ft.Container(
        padding=_pad_sym(0, 2),
        content=components.primary_button(
            fix.label,
            lambda _e: on_navigate(fix.dest_id),
        ),
    )


def _dashboard(
    page: ft.Page,
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
    on_refresh: Callable[[], None] | None,
) -> ft.Control:
    """Branches (b)/(c): read the store, derive the verdict, render verdict-first.

    The real schedule read-back (D4) is fetched OFF the UI thread and injected into a
    re-derive: the initial paint is record-based (schedule unknown), then — once the bounded
    PowerShell probe returns — the verdict re-derives in place (a MISSING/contradicted schedule
    becomes the dominant WARNING routed to Setup). A store read is microseconds (read inline);
    only the schedule probe is threaded (it may spawn PowerShell).
    """
    records = read_run_records()
    # Only the empty branch needs the store's birth stamp (fresh-start vs first-run copy);
    # fetch it just there so a populated-history mount pays for exactly one store read.
    store_created_at = None
    if records == []:
        meta = store_meta()
        store_created_at = meta.get("created_at") if meta else None
    latest_ts = records[0].get("timestamp") if records else None

    container = ft.Column(spacing=22)

    def _render(schedule_status: ScheduleStatus | None) -> None:
        status = derive_home_status(
            records, app_config, store_created_at=store_created_at, schedule_status=schedule_status
        )
        # Verdict-first (Direction B): a slim page header, then the health band as the FIRST
        # content element, then the detail (fix / metrics / the clean-schedule confirmation card).
        controls: list[ft.Control] = [
            _header(app_config, on_refresh),
            components.HealthVerdictBanner(status.verdict, headline=status.headline, detail=status.detail),
        ]
        if status.fix is not None:
            controls.append(_fix_button(status.fix, on_navigate))
        if status.metrics is not None:
            controls.append(components.section_label("Latest roster"))
            controls.append(_metric_tiles_row(status.metrics))
        # The clean-schedule row-card surfaces the LIVE read-back only — an attention state is
        # already the dominant WARNING band + fix button above, so the two never both show.
        if (
            schedule_status is not None
            and schedule_status.state is ScheduleState.LIVE
            and not schedule_status.attention
        ):
            controls.append(_schedule_card(schedule_status, on_navigate))
        container.controls = controls

    _render(None)  # initial paint from the store alone; the schedule read-back arrives async
    _probe_schedule_async(page, app_config, latest_ts, _render)
    return container


def _probe_schedule_async(
    page: ft.Page,
    app_config: AppConfig,
    latest_ts: str | None,
    on_status: Callable[[ScheduleStatus], None],
) -> None:
    """Fetch the schedule read-back OFF the UI thread and re-render on the loop (Windows only).

    Mirrors the SFTP-test marshalling (``page.run_thread`` → ``page.run_task``): the bounded
    PowerShell probe runs on a worker thread; ``on_status`` + ``page.update()`` fire only inside
    the loop-owned coroutine. A probe/thread failure is swallowed — the record-based paint stays.
    """
    if sys.platform != "win32":
        return

    def _work() -> None:  # runs OFF the UI thread
        from src.ui_flet.schedule_probe import probe_schedule

        status = probe_schedule(
            app_config.schedule_task_name,
            hint_registered=app_config.schedule_registered,
            latest_record_ts=latest_ts,
        )

        async def _apply() -> None:
            on_status(status)
            page.update()

        page.run_task(_apply)

    # The schedule read-back is advisory; a probe/thread failure keeps the record-based paint.
    with contextlib.suppress(Exception):
        page.run_thread(_work)


def build_home(
    page: ft.Page,
    *,
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
    on_refresh: Callable[[], None] | None = None,
) -> ft.Control:
    """Build the three-way Home surface. ``on_navigate(dest_id)`` is injected by the shell.

    ``page`` is threaded to ``build_onboarding`` (branch (a)) for the uniform
    ``functools.partial(build_*, page)`` mount form. Branch (a) reuses the IA-2 onboarding
    hero verbatim; branches (b)/(c) render the health dashboard from the pure trust core (with
    the off-thread schedule read-back injected), wrapped in a never-crash ``ErrorCard`` fallback.
    ``on_refresh`` (injected by the shell) adds a Refresh affordance on the dashboard branches
    for the leaves-it-open Watcher; the onboarding branch (a) is unaffected.
    """
    if nav.needs_setup(app_config):
        return build_onboarding(
            page,
            sis_type=app_config.sis_type,
            on_start_setup=lambda: on_navigate("setup"),
        )

    try:
        return _dashboard(page, app_config, on_navigate, on_refresh)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't show your sync status",
            "Your nightly sync keeps running in the background.",
            action=components.primary_button(
                "Check Run History",
                lambda _e: on_navigate("run_history"),
            ),
        )
