"""First-run onboarding hero — the SINGLE front door into the Setup wizard (D10 / IA branch (a)).

VIEW glue (coverage-omitted): a calm, branded, verdict-first welcome for the admin
whose deep job is *trust*. It states what DistrictSync does in one plain line, shows
a plain-language "you're not set up yet" verdict (the ``Verdict.WARNING`` attention
tone — not alarm), greets the district by its friendly name when one is chosen (never
a raw ``sd48myedbc``), and offers ONE prominent "Start setup" CTA into the wizard.

**One front door (D10):** while unconfigured there is exactly one entrance to setup —
this hero's CTA (which navigates to the Setup **wizard**). The old three-step "Getting
started" preview was removed: it duplicated the wizard's own guided path (District →
Folders → Delivery → Schedule → finish) and read as a competing set of instructions.
The launch already lands on the wizard while ``not setup_completed`` (Slice 3/5), so the
hero is a friendly re-entry point, not a second, differently-worded set of steps.

A **single calm "what's ahead" line** (the four input steps named at a glance + a
~3-minute estimate) sits in the hero — a resolved user decision (2026-07-14): it orients
the admin before the CTA WITHOUT re-instating the removed multi-step preview (one line,
not a competing walkthrough — the wizard is still the only guided path).

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
                # A single calm "what's ahead" preview (resolved user decision, 2026-07-14): the four
                # input steps named at a glance + a time estimate — subdued/secondary, NOT a heading,
                # and NOT the removed multi-step walkthrough (the wizard is still the only guided path).
                ft.Text(
                    "Four quick steps — your district, your files, how results are delivered, and "
                    "when it runs. About 3 minutes.",
                    size=14,
                    color=ft.Colors.with_opacity(0.72, tokens.color_on_action),
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

    # ONE front door: a single calm line + the "Start setup" CTA into the wizard. The steps are
    # previewed once (in the hero, above) — this line stays a resume reassurance, not a second
    # enumeration, so nothing here competes with the wizard's guided path (D10).
    start_card = components.card(
        content=ft.Column(
            spacing=18,
            controls=[
                ft.Text(
                    "We'll walk you through it, and you can stop and pick up right where you left off.",
                    size=15,
                    color=tokens.color_text,
                ),
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

    return ft.Column(spacing=22, controls=[hero, status, start_card])
