"""The Run History surface — the read-only "has the sync been running, and did each work?" view.

VIEW glue (coverage-omitted): the trust-critical *decision* lives COUNTED in the pure modules
(``run_log.read_run_records`` parses the log; ``run_history.derive_history_banner`` +
``to_run_rows`` derive the banner + the display rows). This file only RENDERS that already-tested
output, verdict-first — a staleness/verdict banner answering "is my sync running?" BEFORE the
plain-language ``ft.DataTable`` of past runs (newest-first, humanized throughout).

**Read-only terminal surface** — no fix-path CTA (unlike Home), so ``build_run_history`` takes NO
``on_navigate`` (KISS; add only at a future consumer's need). It owns no lifecycle.

**Sync read on mount** (the same justification as Home / IA-3): the run log is a small local text
file parsed to a ``list[dict]`` in microseconds, so it is read inline in the factory — the
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

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, tokens
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.run_history import derive_history_banner, to_run_rows
from src.ui_flet.run_log import read_run_records

LIMIT = 50
"""The newest-N runs shown (mirrors the Streamlit page). A 2-3x/yr admin reviews a short list —
pagination is a ROADMAP nice-to-have if the log could ever grow large, not built here (YAGNI)."""


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _greeting_header(app_config: AppConfig) -> ft.Control:
    """A branded hero titling the surface "Run History" (never a raw config id).

    A Run-History-local hero (not a shared ``components`` extraction): the subtitle differs from
    Home's greeting, so a premature shared extraction of a 5-line hero would be over-DRY — promote
    only if a 3rd consumer needs the identical copy.
    """
    friendly = friendly_district_name(app_config.sis_type)
    subtitle = (
        f"Every nightly roster sync for {friendly}, newest first."
        if friendly
        else "Every nightly roster sync, newest first."
    )
    return components.card(
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text("Run History", size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                ft.Text(
                    subtitle,
                    size=15,
                    color=ft.Colors.with_opacity(0.9, tokens.color_on_action),
                ),
            ],
        ),
        gradient=components.hero_gradient(),
        padding=_pad_sym(32, 26),
        border_radius=18,
    )


def _scrollable_table(table: ft.Control) -> ft.Control:
    """Wrap the (wide) table in its own horizontally-scrollable region.

    A wide table scrolls INSIDE this region so the page body never scrolls horizontally (the shell
    wraps content in a vertical ``ScrollMode.AUTO`` column — horizontal is the view's job).
    """
    return ft.Row(controls=[table], scroll=ft.ScrollMode.AUTO, expand=True)


def _surface(app_config: AppConfig) -> ft.Control:
    """Read the log, derive the banner + rows, render verdict-first."""
    records = read_run_records()
    banner = derive_history_banner(records, app_config)

    controls: list[ft.Control] = [
        _greeting_header(app_config),
        components.HealthVerdictBanner(banner.verdict, headline=banner.headline, detail=banner.detail),
    ]
    # None (unavailable) / [] (no runs) → the banner alone (nothing to tabulate). Otherwise the table.
    if records:
        controls.append(_scrollable_table(components.run_table(to_run_rows(records)[:LIMIT])))

    return ft.Column(spacing=22, controls=controls)


def build_run_history(page: ft.Page, *, app_config: AppConfig) -> ft.Control:  # noqa: ARG001 - uniform mount form
    """Build the Run History surface (read-only). ``page`` is threaded for the uniform mount form.

    Sync read on mount, verdict-first render, wrapped in a never-crash ``ErrorCard`` fallback so
    even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth — the parser
    + derivation are already TOTAL).
    """
    try:
        return _surface(app_config)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't show your run history",
            "Your nightly sync keeps running in the background.",
        )
