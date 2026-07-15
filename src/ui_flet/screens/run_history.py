"""The Run History surface — the read-only "has the sync been running, and did each work?" view.

VIEW glue (coverage-omitted): the trust-critical *decision* lives COUNTED in the pure modules
(``history.store.read_run_records`` reads the run store; ``run_history.derive_history_banner`` +
``to_run_rows`` derive the banner + the display rows). This file only RENDERS that already-tested
output, verdict-first — a staleness/verdict banner answering "is my sync running?" BEFORE the
plain-language ``ft.DataTable`` of past runs (newest-first, humanized throughout).

**Read-only terminal surface** — no fix-path CTA (unlike Home), so ``build_run_history`` takes NO
``on_navigate`` (KISS; add only at a future consumer's need). It owns no lifecycle.

**Sync read on mount** (the same justification as Home / IA-3): the run store is a small local
SQLite DB read to a ``list[dict]`` in microseconds, so it is read inline in the factory — the
worker-thread convention is scoped to ``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``); async
here would add the doc's #1 concurrency trap for no gain.

**Three read-only states, each calm + distinct** (mirroring ``home_status``'s degradation-first
contract): ``None`` → "history unavailable" WARNING banner, no table; ``[]`` → "no runs yet"
WARNING banner (never red), no table; else the verdict banner + the ``run_table`` (capped at
``LIMIT``) in a horizontally-scrollable region. The whole read/derive/render body is wrapped in a
never-crash ``ErrorCard`` (defense-in-depth on top of the already-total derivation).

Assembled ENTIRELY from ``components.py`` (card/banner/table/ErrorCard) + ``tokens`` — never
hand-rolled controls (the ``FilledButton(text=)`` trap; see ``docs/FLET_1.0_CONVENTIONS.md``).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.history.store import read_run_records, store_meta
from src.ui_flet import components
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.run_history import derive_history_banner, to_run_rows
from src.ui_flet.schedule_status import ScheduleStatus

LIMIT = 50
"""The newest-N runs shown (mirrors the Streamlit page). A 2-3x/yr admin reviews a short list —
pagination is a ROADMAP nice-to-have if the log could ever grow large, not built here (YAGNI)."""


def _greeting_header(app_config: AppConfig) -> ft.Control:
    """The Direction B page header titling the surface "Run History" (never a raw config id).

    The gradient hero demotes to a slim ``page_header`` (0033 Slice 2); the district-voiced
    subtitle is preserved as the header sub.
    """
    friendly = friendly_district_name(app_config.sis_type)
    subtitle = (
        f"Every nightly roster sync for {friendly}, newest first."
        if friendly
        else "Every nightly roster sync, newest first."
    )
    return components.page_header("Run History", subtitle)


def _refresh_button(on_refresh: Callable[[], None]) -> ft.Control:
    """A small secondary "Refresh" affordance — re-reads the run history in place.

    Covers the Watcher who leaves the app open overnight: Run History reads on mount only (a sync
    read, no polling), so a manual re-check re-invokes this screen's build via the shell
    (``select_by_id("run_history")``) without navigating away. A Row keeps it compact.

    Local (not a shared ``components`` factory) — same 2-consumer/local-helper convention as
    ``_greeting_header``; promote only if a 3rd surface needs the identical affordance.
    """
    return ft.Row(
        controls=[
            components.secondary_button(
                "Refresh",
                lambda _e: on_refresh(),
                icon=ft.Icons.REFRESH_ROUNDED,
            ),
        ],
    )


def _scrollable_table(table: ft.Control) -> ft.Control:
    """Wrap the (wide) table in its own horizontally-scrollable region.

    A wide table scrolls INSIDE this region so the page body never scrolls horizontally (the shell
    wraps content in a vertical ``ScrollMode.AUTO`` column — horizontal is the view's job).
    """
    return ft.Row(controls=[table], scroll=ft.ScrollMode.AUTO, expand=True)


def _surface(page: ft.Page, app_config: AppConfig, on_refresh: Callable[[], None] | None) -> ft.Control:
    """Read the store, derive the banner + rows, render verdict-first.

    The banner's empty-state next-run line derives from the LIVE schedule read-back (D4),
    fetched OFF the UI thread (Run History is read-only — no schedule-attention verdict, that
    is Home's job). The table + banner paint immediately from the store; the empty-state copy
    re-renders in place once the probe returns.
    """
    records = read_run_records(limit=LIMIT)
    # Only the empty branch needs the store's birth stamp (fresh-start vs first-run copy).
    store_created_at = None
    if records == []:
        meta = store_meta()
        store_created_at = meta.get("created_at") if meta else None
    latest_ts = records[0].get("timestamp") if records else None

    container = ft.Column(spacing=22)

    def _render(schedule_status: ScheduleStatus | None) -> None:
        banner = derive_history_banner(
            records, app_config, store_created_at=store_created_at, schedule_status=schedule_status
        )
        controls: list[ft.Control] = [
            _greeting_header(app_config),
            components.HealthVerdictBanner(banner.verdict, headline=banner.headline, detail=banner.detail),
        ]
        # None (unavailable) / [] (no runs) → banner alone (nothing to tabulate). Otherwise the table.
        if records:
            rows = to_run_rows(records, active_sis=app_config.sis_type)[:LIMIT]
            controls.append(_scrollable_table(components.run_table(rows)))
        if on_refresh is not None:
            controls.append(_refresh_button(on_refresh))
        container.controls = controls

    _render(None)  # initial paint; the next-run line refines once the read-back arrives
    # Only the empty state uses the schedule read-back — skip the probe when there is a table.
    if not records:
        _probe_schedule_async(page, app_config, latest_ts, _render)
    return container


def _probe_schedule_async(
    page: ft.Page,
    app_config: AppConfig,
    latest_ts: str | None,
    on_status: Callable[[ScheduleStatus], None],
) -> None:
    """Fetch the schedule read-back OFF the UI thread and re-render on the loop (Windows only)."""
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

    # The schedule read-back is advisory; a probe/thread failure keeps the initial paint.
    with contextlib.suppress(Exception):
        page.run_thread(_work)


def build_run_history(
    page: ft.Page,
    *,
    app_config: AppConfig,
    on_refresh: Callable[[], None] | None = None,
) -> ft.Control:
    """Build the Run History surface (read-only). ``page`` threads the off-thread schedule probe.

    Sync read on mount, verdict-first render, wrapped in a never-crash ``ErrorCard`` fallback so
    even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth — the parser
    + derivation are already TOTAL). ``on_refresh`` (injected by the shell) adds a Refresh
    affordance for the leaves-it-open Watcher — re-invoking this screen's build in place.
    """
    try:
        return _surface(page, app_config, on_refresh)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't show your run history",
            "Your nightly sync keeps running in the background.",
        )
