"""First-run onboarding hero — the UNCONFIGURED Home surface (IA model branch (a)).

VIEW glue (coverage-omitted): a calm, branded, verdict-first welcome for the admin
whose deep job is *trust*. It states what DistrictSync does in one plain line, shows
a plain-language "you're not set up yet" verdict (the ``Verdict.WARNING`` attention
tone — not alarm), greets the district by its friendly name when one is chosen (never
a raw ``sd48myedbc``), and offers a prominent "Start setup" CTA.

Built as a **callback-driven factory** — ``build_onboarding`` owns NO navigation or
lifecycle (``on_start_setup`` is injected by the shell, which calls
``select_by_id("setup")``). That discipline (mirroring ``nav_rail``) is what lets
**IA-3 reuse this verbatim** as Home branch (a) with its own ``on_start_setup``.

Assembled ENTIRELY from ``components.py`` (cards/buttons) + ``verdict``/``tokens``
+ the ``humanize`` helper — never hand-rolled controls (the ``FilledButton(text=)``
trap; see ``docs/FLET_1.0_CONVENTIONS.md``).

**State honesty:** the hero is static — it has NO empty/loading/error state of its
own. Its only failure axis is ``friendly_district_name``, which is TOTAL (falls back
to the raw id, never blank/crash). The "status unavailable" graceful-degradation is
IA-3's (deriving a verdict from live run-history/staleness) — deliberately NOT here.
No speculative loading skeleton on a display-free static surface (YAGNI).
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import components, tokens
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.verdict import Verdict


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _step_row(icon: str, text: str) -> ft.Row:
    """One plain first-run step: a muted brand icon + calm one-line copy."""
    return ft.Row(
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(getattr(ft.Icons, icon, ft.Icons.CIRCLE_OUTLINED), color=tokens.color_action_primary, size=20),
            ft.Text(text, size=14, color=tokens.color_text),
        ],
    )


def build_onboarding(
    page: ft.Page,  # noqa: ARG001 - kept for the shell's uniform partial(build_*, page) form + IA-3 reuse
    *,
    sis_type: str = "",
    on_start_setup: Callable[[], None],
) -> ft.Control:
    """Build the first-run onboarding hero. ``on_start_setup`` is injected by the caller.

    ``page`` is accepted (unused this slice) so the shell mounts it with the same
    ``functools.partial(build_*, page)`` form as the other surfaces and IA-3 can reuse
    the factory unchanged. ``sis_type`` (already-loaded, may be empty) is greeted by its
    friendly district name when set — never a raw id.
    """
    friendly = friendly_district_name(sis_type)
    greeting = f"Welcome, {friendly}" if friendly else "Welcome to DistrictSync"

    hero = components.card(
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text(greeting, size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                ft.Text(
                    "DistrictSync keeps your MyEd BC roster flowing to SpacesEDU — automatically, every night.",
                    size=15,
                    color=ft.Colors.with_opacity(0.9, tokens.color_on_action),
                ),
            ],
        ),
        gradient=components.hero_gradient(),
        padding=_pad_sym(32, 26),
        border_radius=18,
    )

    status = components.HealthVerdictBanner(
        Verdict.WARNING,
        headline="You're not set up yet",
        detail="A few quick steps and your nightly sync is running.",
    )

    steps_card = components.card(
        content=ft.Column(
            spacing=18,
            controls=[
                ft.Text("Getting started", size=18, weight=ft.FontWeight.W_700, color=tokens.color_text),
                _step_row("FOLDER_OPEN_ROUNDED", "Pick your input and output folders, and choose your district."),
                _step_row("SCHEDULE_ROUNDED", "Set the nightly schedule so the sync runs on its own."),
                _step_row("CHECK_CIRCLE_ROUNDED", "That's it — DistrictSync keeps your roster up to date."),
                ft.Container(
                    padding=_pad_sym(0, 6),
                    content=components.primary_button(
                        "Start setup",
                        lambda _e: on_start_setup(),
                        icon=ft.Icons.ROCKET_LAUNCH_ROUNDED,
                    ),
                ),
            ],
        ),
    )

    return ft.Column(spacing=22, controls=[hero, status, steps_card])
