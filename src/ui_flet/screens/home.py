"""The three-way Home health dashboard — the flagship trust surface (IA model IA-3).

VIEW glue (coverage-omitted): the trust-critical *decision* lives COUNTED in the pure
modules (``run_log.read_run_records`` parses the log; ``home_status.derive_home_status``
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

**Sync read on mount** (no loading state): the run log is a small local text file parsed to
a ``list[dict]`` (microseconds), so it is read inline in the factory — the worker-thread
convention is scoped to ``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``), and an
async path here would add the doc's #1 concurrency trap for no user-perceptible gain
(YAGNI). The empty state IS a real reachable state and is rendered (via the pure derivation's
"no runs yet" branch); a loading skeleton is deliberately NOT built (nothing async to load).
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, nav, tokens
from src.ui_flet.home_status import ENTITY_LABELS, FixAction, HomeMetrics, derive_home_status
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.run_log import read_run_records
from src.ui_flet.screens.onboarding import build_onboarding


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _greeting_header(app_config: AppConfig) -> ft.Control:
    """A branded hero greeting the district by its friendly name (never a raw id)."""
    friendly = friendly_district_name(app_config.sis_type)
    greeting = f"Welcome back, {friendly}" if friendly else "Welcome back"
    return components.card(
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text(greeting, size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                ft.Text(
                    "Here's how your nightly roster sync to SpacesEDU is doing.",
                    size=15,
                    color=ft.Colors.with_opacity(0.9, tokens.color_on_action),
                ),
            ],
        ),
        gradient=components.hero_gradient(),
        padding=_pad_sym(32, 26),
        border_radius=18,
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


def _refresh_button(on_refresh: Callable[[], None]) -> ft.Control:
    """A small secondary "Refresh" affordance — re-reads run state + config in place.

    Covers the Watcher who leaves the app open overnight: Home reads on mount only (a sync read,
    no polling), so a manual re-check re-invokes this screen's build via the shell
    (``select_by_id("home")``) without navigating away. A Row keeps it compact (intrinsic width).

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


def _dashboard(
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
    on_refresh: Callable[[], None] | None,
) -> ft.Control:
    """Branches (b)/(c): read the log, derive the verdict, render verdict-first."""
    records = read_run_records()
    status = derive_home_status(records, app_config)

    controls: list[ft.Control] = [
        _greeting_header(app_config),
        components.HealthVerdictBanner(
            status.verdict,
            headline=status.headline,
            detail=status.detail,
        ),
    ]
    if status.fix is not None:
        controls.append(_fix_button(status.fix, on_navigate))
    if status.metrics is not None:
        controls.append(_metric_tiles_row(status.metrics))
    if on_refresh is not None:
        controls.append(_refresh_button(on_refresh))

    return ft.Column(spacing=22, controls=controls)


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
    hero verbatim; branches (b)/(c) render the health dashboard from the pure trust core,
    wrapped in a never-crash ``ErrorCard`` fallback. ``on_refresh`` (injected by the shell)
    adds a Refresh affordance on the dashboard branches for the leaves-it-open Watcher; the
    onboarding branch (a) is unaffected (freshness there arrives on next navigation).
    """
    if nav.needs_setup(app_config):
        return build_onboarding(
            page,
            sis_type=app_config.sis_type,
            on_start_setup=lambda: on_navigate("setup"),
        )

    try:
        return _dashboard(app_config, on_navigate, on_refresh)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't show your sync status",
            "Your nightly sync keeps running in the background.",
            action=components.primary_button(
                "Check Run History",
                lambda _e: on_navigate("run_history"),
            ),
        )
