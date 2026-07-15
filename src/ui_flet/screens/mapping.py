"""The Mapping surface — the review-and-switch home for the district roster config (IA model IA-8a).

VIEW glue (coverage-omitted): the trust-critical *derivation* lives COUNTED in the pure
``mapping_catalog`` (``summarize_config`` / ``list_configs`` — the empty-``enabled_entities``
-means-all output-CSV resolution + the total-over-a-failing-config degradation — and
``post_apply_presentation``, the post-Apply schedule-staleness honesty). This file only
RENDERS that already-tested output: which config is active + what it produces, and a calm switch.

**Post-Apply schedule honesty (plan 0034 Slice 1).** A registered nightly task bakes
``--sis <district>`` into its action args, so switching the district here leaves a LIVE task
converting the OLD district until Settings re-registers it. Apply therefore never claims the
schedule is fine: the immediate banner is record-based honest (hint-hedged, per the pure
``post_apply_presentation``), then the real schedule read-back — the same off-thread,
win32-gated ``probe_schedule`` pattern Home uses — refines it in place. A LIVE (or
unconfirmed-but-expected) schedule paints a WARNING notice naming the old district, with an
"Open Settings" route (``on_navigate("setup")``) to the ONE re-register flow Settings owns —
Mapping never re-registers and never collects credentials (owner decision 2026-07-15).

**The select-a-pre-built-config sliver, NOT the full editor.** Per the 0013 scope-lock, the full
column-mapping editor (YAML editing / column wizard / config creation) is DEFERRED to ROADMAP
(IA-8b). This surface only: reviews the ACTIVE mapping, lets an admin pick a DIFFERENT pre-built
one (seeing its output-CSV summary FIRST), and applies the switch — writing the UI-owned
``AppConfig.sis_type`` (the same field Setup writes; folders / schedule / SFTP untouched).

**Reconciled with Setup, not duplicated.** Setup is first-run onboarding (folders + district +
schedule + SFTP on one scroll); Mapping is the ongoing settings home for the district-config
concern (Advanced group), earning its place via the output-CSV summary Setup's bare dropdown
never shows (picking ``mbp_core`` vs a SpacesEDU district DROPS the 5 rostering CSVs — Mapping
makes that consequence visible before applying). The selection logic is REUSED (``available_configs``
/ ``friendly_district_name`` via ``mapping_catalog``), never copied.

**Structural Apply-gate (security + reliability).** The switch options ARE ``available_configs()``
(a structural allowlist — no free-text ``sis_type``, mirroring Setup's SFTP-host pattern). Apply
is disabled until the pending config is BOTH ``loaded_ok=True`` AND different from the current one
(you can never apply a broken config — the next run would fail — nor a no-op); a re-check inside
the handler guards ``cfg.save()`` even if the gate were bypassed.

**Sync read on mount** (the same justification as Home / Run History): ``list_configs`` reads a
handful of small local YAMLs in microseconds — the worker-thread convention is scoped to
``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``); async here would add the doc's #1
concurrency trap for no gain. The ONE off-thread hop is the post-Apply schedule probe (a
bounded PowerShell subprocess, ``page.run_thread`` → ``page.run_task``, fire-and-forget with a
generation guard — Home's exact marshalling).

Assembled ENTIRELY from ``components.py`` (card / hero / ``primary_button`` /
``HealthVerdictBanner`` / ``ErrorCard``) + ``tokens`` + the pure ``mapping_catalog`` — never
hand-rolled controls (the ``FilledButton(text=)`` trap; see ``docs/FLET_1.0_CONVENTIONS.md``).
Owns no lifecycle. **Never-crash floor:** the whole body is wrapped in ``try/except`` →
``components.ErrorCard`` so even a view-layer bug shows a calm surface, never a stack trace.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, tokens
from src.ui_flet.mapping_catalog import (
    ConfigSummary,
    can_apply,
    list_configs,
    post_apply_presentation,
    summarize_config,
)
from src.ui_flet.schedule_status import ScheduleState
from src.ui_flet.verdict import Verdict


def _greeting_header(app_config: AppConfig) -> ft.Control:  # noqa: ARG001 - uniform header form (config-voiceless title)
    """The Direction B page header titling the surface "Mapping" (never a raw config id).

    The gradient hero demotes to a slim ``page_header`` (0033 Slice 2).
    """
    return components.page_header(
        "Mapping",
        "Review the roster mapping DistrictSync uses, or switch to a different one.",
    )


def _summary_lines(summary: ConfigSummary) -> list[ft.Control]:
    """The plain-language body of a config summary: what it produces + how many files it reads.

    A degraded (``loaded_ok=False``) config gets a calm "couldn't read this configuration" note
    instead of a fabricated summary (never a raw error).
    """
    if not summary.loaded_ok:
        return [
            ft.Text(
                "We couldn't read this configuration — it may need attention.",
                size=14,
                color=tokens.color_status_warning,
                weight=ft.FontWeight.W_600,
            ),
        ]
    produces = ", ".join(summary.output_labels) if summary.output_labels else "nothing yet"
    files_word = "file" if summary.source_file_count == 1 else "files"
    return [
        ft.Text(f"Produces: {produces}", size=14, color=tokens.color_text),
        ft.Text(
            f"Reads {summary.source_file_count} extract {files_word}",
            size=13,
            color=tokens.color_muted,
        ),
    ]


def _summary_card(title: str, summary: ConfigSummary) -> ft.Control:
    """A titled card for one config's summary: friendly name (primary) + what it produces + the raw id hint."""
    return components.card(
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Text(title, size=14, weight=ft.FontWeight.W_700, color=tokens.color_muted),
                ft.Text(summary.district_name, size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
                *_summary_lines(summary),
                # The raw sis_type — a small secondary technical hint only (support recoverability),
                # never the primary label.
                ft.Text(summary.sis_type, size=12, color=tokens.color_muted, selectable=True),
            ],
        ),
    )


def _surface(page: ft.Page, app_config: AppConfig, on_navigate: Callable[[str], None] | None) -> ft.Control:
    """Render the current-mapping summary + the switch selector + the gated Apply.

    Apply writes through ``AppConfig`` and re-renders THIS surface in place (D1): the
    current-mapping card, the pending summary, and the gate all recompute against the freshly
    PERSISTED current — so a switch shows immediately and can be reverted without a restart (the
    gate compares against ``persisted``, never the captured mount instance, via the pure
    ``mapping_catalog.can_apply``).
    """
    summaries = {s.sis_type: s for s in list_configs()}
    # The persisted current sis_type — mutated on each successful Apply so the gate + the
    # current-mapping card always track what's actually saved (never the frozen mount value).
    persisted = {"sis": app_config.sis_type}
    # Ensure the current config is summarizable even if not in the discovered list (defensive).
    summaries.setdefault(persisted["sis"], summarize_config(persisted["sis"]))

    # Mutable pending selection — starts on the current config (so Apply is a no-op → disabled).
    pending = {"sis": app_config.sis_type}
    # Apply/pick generation — an in-flight post-Apply schedule probe only paints if the banner
    # it refines is still the current one (a fresh pick/Apply invalidates the stale refine).
    apply_seq = {"n": 0}

    apply_btn = components.primary_button(
        "Use this mapping",
        None,  # wired below after the handlers are defined
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
    )
    current_card_slot = ft.Column(spacing=0, controls=[])
    applied_banner_slot = ft.Column(spacing=tokens.space_md, controls=[])
    pending_summary_slot = ft.Column(spacing=0, controls=[])

    def _summary_for(sis: str) -> ConfigSummary:
        return summaries.get(sis) or summarize_config(sis)

    def _refresh() -> None:
        # Re-render the current-mapping card + the pending summary + re-derive the gate, all
        # against the freshly-PERSISTED current — so an Apply is reflected in place and revertible.
        current_card_slot.controls = [_summary_card("Current mapping", _summary_for(persisted["sis"]))]
        pending_summary = _summary_for(pending["sis"])
        pending_summary_slot.controls = [_summary_card("Switch to", pending_summary)]
        apply_btn.disabled = not can_apply(pending_summary, persisted["sis"])
        page.update()

    def _on_pick(e: ft.ControlEvent) -> None:
        pending["sis"] = e.control.value or persisted["sis"]
        apply_seq["n"] += 1  # invalidate any in-flight post-Apply probe (its banner is cleared)
        applied_banner_slot.controls = []  # a fresh pick clears a prior confirmation
        _refresh()

    def _on_apply(_e: ft.ControlEvent) -> None:
        pending_summary = _summary_for(pending["sis"])
        # Re-check the gate so a broken / no-op config can never reach AppConfig.save().
        if not can_apply(pending_summary, persisted["sis"]):
            return
        # Capture the OLD district's display name BEFORE overwriting — a registered nightly
        # task keeps converting the pre-Apply district, so the notice must name that one.
        old_summary = _summary_for(persisted["sis"])
        cfg = AppConfig.load()
        cfg.sis_type = pending_summary.sis_type
        cfg.save()
        persisted["sis"] = pending_summary.sis_type  # the switch is now the persisted current
        apply_seq["n"] += 1
        gen = apply_seq["n"]
        hint_registered = cfg.schedule_registered
        task_name = cfg.schedule_task_name

        def _paint_banner(schedule_state: ScheduleState | None) -> None:
            # The pure decision: healthy detail + (optionally) the stale-schedule notice.
            pres = post_apply_presentation(
                old_summary.district_name,
                schedule_state=schedule_state,
                hint_registered=hint_registered,
            )
            banners: list[ft.Control] = [
                components.HealthVerdictBanner(
                    Verdict.HEALTHY,
                    headline=f"Now using {pending_summary.district_name}",
                    detail=pres.healthy_detail,
                )
            ]
            if pres.notice is not None:
                # The fix routes to the ONE re-register flow Settings owns (never re-register
                # here). Secondary tier — "Use this mapping" is this screen's filled primary.
                trailing = (
                    components.secondary_button("Open Settings", lambda _e: on_navigate("setup"))
                    if on_navigate is not None
                    else None
                )
                banners.append(
                    components.HealthVerdictBanner(
                        Verdict.WARNING,
                        headline=pres.notice.headline,
                        detail=pres.notice.detail,
                        trailing=trailing,
                    )
                )
            applied_banner_slot.controls = banners

        # Paint-then-refine (Home's pattern): the immediate banner is record-based honest
        # (hint-hedged, never asserted), then the real read-back upgrades it in place.
        _paint_banner(None)
        _refresh()  # re-render the current card + re-derive the gate (reverting is now possible)
        _refine_from_probe(gen, task_name, hint_registered, _paint_banner)

    def _refine_from_probe(
        gen: int,
        task_name: str,
        hint_registered: bool,
        paint: Callable[[ScheduleState | None], None],
    ) -> None:
        # Windows-only (like the shell's badge probe): elsewhere the hedged initial paint IS
        # the honest final state — a live schedule is never asserted from the hint alone.
        if sys.platform != "win32":
            return

        def _work() -> None:  # runs OFF the UI thread
            from src.ui_flet.schedule_probe import probe_schedule

            status = probe_schedule(task_name, hint_registered=hint_registered)

            async def _apply() -> None:
                if apply_seq["n"] != gen:
                    return  # a newer pick/Apply owns the banner slot — drop the stale refine
                paint(status.state)
                page.update()

            page.run_task(_apply)

        # The read-back is advisory; a probe/thread failure keeps the hedged initial paint.
        with contextlib.suppress(Exception):
            page.run_thread(_work)

    apply_btn.on_click = _on_apply

    switch_dropdown = ft.Dropdown(
        label="Roster mapping",
        value=app_config.sis_type or None,
        # The options ARE available_configs() — a structural allowlist (no free-text sis_type).
        options=[ft.dropdown.Option(key=s.sis_type, text=s.district_name) for s in list_configs()],
        # ft.Dropdown's value-change event is on_select on flet 0.85.3 (no on_change).
        on_select=_on_pick,
        border_color=tokens.color_border,
    )

    _refresh()  # paint the initial current card + pending summary (= current) + the gate (disabled)

    switch_card = components.card(
        content=ft.Column(
            spacing=18,
            controls=[
                ft.Text("Switch mapping", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
                ft.Text(
                    "Pick a different pre-built configuration. You'll see what it produces before applying.",
                    size=14,
                    color=tokens.color_muted,
                ),
                switch_dropdown,
                pending_summary_slot,
                apply_btn,
                applied_banner_slot,
            ],
        ),
    )

    return ft.Column(
        spacing=22,
        controls=[
            _greeting_header(app_config),
            current_card_slot,
            switch_card,
        ],
    )


def build_mapping(
    page: ft.Page,
    *,
    app_config: AppConfig,
    on_navigate: Callable[[str], None] | None = None,
) -> ft.Control:
    """Build the Mapping surface (review + switch the district config). ``page`` drives updates.

    Sync read on mount, verdict-first apply, wrapped in a never-crash ``ErrorCard`` fallback so
    even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth — the
    catalog derivation is already TOTAL). ``on_navigate`` (Home's exact pattern, injected by
    the shell with rail-follow) routes the post-Apply stale-schedule notice to Settings; when
    absent (``None``, defensive default) the notice renders without the routing button — never
    a crash. The 0034 Slice 1 honesty fix supersedes the earlier no-``on_navigate`` decision.
    """
    try:
        return _surface(page, app_config, on_navigate)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't open Mapping",
            "Your nightly sync keeps running in the background.",
        )
