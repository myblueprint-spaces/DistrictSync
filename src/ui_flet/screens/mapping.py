"""The Mapping surface — the review-switch-and-author home for the district roster config (IA-8a).

VIEW glue (coverage-omitted): the trust-critical *derivation* lives COUNTED in the pure
``mapping_catalog`` (``summarize_config`` / ``list_configs`` — the empty-``enabled_entities``
-means-all output-CSV resolution + the total-over-a-failing-config degradation — and
``post_apply_presentation``, the post-Apply schedule-staleness honesty) and in the pure
``config_editor`` (``activation_allowed``, ``overlay_staleness``, ``files_primary_action``).
This file only RENDERS that already-tested output: which config is active + what it produces,
a calm switch, and — since plan 0044 S6 — the two doors onto the district creator.

**Post-Apply schedule honesty (plan 0034 Slice 1).** A registered nightly task bakes
``--sis <district>`` into its action args, so switching the district here leaves a LIVE task
converting the OLD district until Settings re-registers it. Apply therefore never claims the
schedule is fine: the immediate banner is record-based honest (hint-hedged, per the pure
``post_apply_presentation``), then the real schedule read-back — the same off-thread,
win32-gated ``probe_schedule`` pattern Home uses — refines it in place. A LIVE (or
unconfirmed-but-expected) schedule paints a WARNING notice naming the old district, with an
"Open Settings" route (``on_navigate("setup")``) to the ONE re-register flow Settings owns —
Mapping never re-registers and never collects credentials (owner decision 2026-07-15). That
presentation is ``_after_switch``, shared VERBATIM by Apply and by an activation from the
hosted creator panel: two writers of ``sis_type`` on one screen may not paint two different
stories about the same nightly task.

**Two doors onto the creator (plan 0044 S6).** The creator was reachable only from the
first-run wizard, so nobody past first run could set up a district of their own or fix one.
This screen hosts ``screens/creator.build_creator`` in a ``body_host`` container whose
``content`` swaps between the Mapping view and the creator panel (the ``shell.root_host``
pattern, in-screen — the view's controls leave the tree, so they cannot contribute a second
filled primary):

* :data:`MAPPING_CREATE_LABEL` — text tier at the foot of the Switch card, offered in EVERY
  state (Mapping is on the rail in every state, D7) — opens the four forms with
  ``creator_form_for_new``, exactly the prefill the wizard uses;
* :data:`MAPPING_EDIT_LABEL` — secondary tier on a USER-authored card — re-opens them with
  ``creator_form_from_overlay``. A shipped mapping gets NO change door on either card: those
  are ours;
* :data:`MAPPING_RESUME_LABEL` — text tier — picks up a creation abandoned anywhere, because
  ``creator_pending_sis`` is a fact about the INSTALL, and an overlay only one surface can
  resume is how one becomes invisible litter.

The panel owns no step numbers, no footer Continue and no ``setup_flow`` import; its
``MAPPING_PANEL_BACK_LABEL`` control promotes to a filled :data:`MAPPING_PANEL_DONE_LABEL`
in the one state where the creator surface offers no primary of its own, so exactly ONE
filled primary renders in every Mapping state. That promoted Done REFUSES while a file name
on screen is not in the config on disk (the wizard footer's own rule): the rows re-render
themselves, so it would otherwise carry a pending rename out of the panel and discard the
only record of it — see ``_on_panel_done``.

**The verified-fact check (plan 0044 S6).** "Use this mapping" is one of four writers of
``AppConfig.sis_type``. For a mapping authored on THIS computer it now consults the pure
``config_editor.activation_allowed`` BEFORE any write: refused ⇒ nothing is saved, no
confirmation is painted, and the WARNING band carries :data:`MAPPING_NEEDS_TEST_NOTE` plus
the change door that runs the test. Shipped mappings are untouched by the check (the
``origin`` map is derived from the SAME memoised catalog build the options come from, and an
id missing from it is treated as ``"bundled"`` — the fail-OPEN direction ``_origin_of``
documents for itself: this exists to stop an admin MISTAKE, never to strand one).

**The select-a-pre-built-config sliver, plus authoring — NOT a YAML editor.** The full
column-mapping editor remains DEFERRED to ROADMAP (IA-8b): the panel asks the creator's four
questions and its file names, and nothing here edits YAML text.

**Reconciled with Setup, not duplicated.** Setup is first-run onboarding (folders + district +
schedule + SFTP on one scroll); Mapping is the ongoing settings home for the district-config
concern (Advanced group), earning its place via the output-CSV summary Setup's bare dropdown
never shows (picking ``mbp_core`` vs a SpacesEDU district DROPS the 5 rostering CSVs — Mapping
makes that consequence visible before applying). The selection logic is REUSED (``available_configs``
/ ``friendly_district_name`` via ``mapping_catalog``), never copied.

**Structural Apply-gate (security + reliability).** The switch options come from the enumerated
catalog (a structural allowlist — no free-text ``sis_type``, mirroring Setup's SFTP-host pattern).
Apply is disabled until the pending config is BOTH ``loaded_ok=True`` AND different from the
current one (you can never apply a broken config — the next run would fail — nor a no-op); a
re-check inside the handler guards ``cfg.save()`` even if the gate were bypassed.

**Scoped to the admin's district (0038 S5).** The options come from ONE
``mapping_catalog.filtered_catalog`` build per view build — replacing the pre-S5 double
``list_configs()`` parse — so a matched admin sees their own district's mappings. The SAVED
mapping is present in every rendered list by construction, so the surface that switches a
district can never fail to show the one in use.

**Sync read on mount** (the same justification as Home / Run History): the catalog reads a
handful of small local YAMLs in microseconds — the worker-thread convention is scoped to
``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``); async here would add the doc's #1
concurrency trap for no gain. The ONE off-thread hop is the post-Apply schedule probe (a
bounded read-back, ``page.run_thread`` → ``page.run_task``, fire-and-forget with a
generation guard — Home's exact marshalling); the creator panel's test conversion runs on the
creator surface's own ``JobRunner``.

Assembled ENTIRELY from ``components.py`` (card / ``primary_button`` /
``HealthVerdictBanner`` / ``ErrorCard``) + ``tokens`` + the pure ``mapping_catalog`` — never
hand-rolled controls (the ``FilledButton(text=)`` trap; see ``docs/FLET_1.0_CONVENTIONS.md``).
Owns no lifecycle. **Never-crash floor:** the whole body is wrapped in ``try/except`` →
``components.ErrorCard``, and the panel carries its OWN floor (with the way back) so a bug
while authoring can never leave an admin looking at a stack trace either.

**One-way dependency:** this module imports ``screens/creator.py``; the creator imports NO
host (S6 widens S3's rule from "may not name the wizard" to "may not name a host").
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Sequence

import flet as ft

from src.config.app_config import AppConfig
from src.config.authoring import current_digest, read_authored_with
from src.scheduler import get_scheduler
from src.ui_flet import components, tokens
from src.ui_flet.config_editor import (
    CreatorForm,
    activation_allowed,
    files_primary_action,
    has_unsaved_renames,
    overlay_staleness,
)
from src.ui_flet.filepicker import validate_output_dir
from src.ui_flet.identity_gate import stored_identity_domain
from src.ui_flet.mapping_catalog import (
    CUSTOM_ORIGIN_LABEL,
    ConfigSummary,
    can_apply,
    disambiguated_labels,
    filtered_catalog,
    post_apply_presentation,
    summarize_config,
)
from src.ui_flet.schedule_status import ScheduleState
from src.ui_flet.screens.creator import (
    CREATOR_DISCARDED_NOTE,
    CreatorStage,
    base_digest_for,
    build_creator,
    creator_form_for_new,
    creator_form_from_overlay,
    creator_gate_current,
    pending_creator_sis,
)
from src.ui_flet.verdict import Verdict
from src.utils.version import app_version

logger = logging.getLogger(__name__)


def _greeting_header(app_config: AppConfig) -> ft.Control:  # noqa: ARG001 - uniform header form (config-voiceless title)
    """The Direction B page header titling the surface "Mapping" (never a raw config id).

    The gradient hero demotes to a slim ``page_header`` (0033 Slice 2).
    """
    return components.page_header(
        "Mapping",
        "Review the roster mapping DistrictSync uses, or switch to a different one.",
    )


# Where a mapping added on THIS computer lives, said once — and, since plan 0044 S6, that
# its setup can be changed, because now it can be (the change door is on this very card).
# S2's deliberate silence about editing retires with the capability arriving; what the note
# still promises NOTHING about is a column-level report or a YAML text surface.
CUSTOM_ORIGIN_NOTE = (
    "This mapping lives in this computer's DistrictSync folder — it wasn't shipped with "
    "DistrictSync, so you can change how it's set up here."
)

# ---- the two doors onto the creator (plan 0044 S6 §6.2) ------------------- #
#: CREATE. Names the situation rather than the tool ("add a mapping" means nothing to an
#: admin who has never seen one), and avoids "edit" — this is not a YAML editor.
MAPPING_CREATE_LABEL = "Set up a district that isn't listed"
#: CHANGE. Only ever on a card whose mapping was authored HERE.
MAPPING_EDIT_LABEL = "Change this district's setup"
#: RESUME — an unfinished creation, pickable up from this surface as well as the wizard.
MAPPING_RESUME_LABEL = "Finish setting up the district you started"
#: The panel's way back, at text tier while the creator surface owns the primary...
MAPPING_PANEL_BACK_LABEL = "Back to Mapping"
#: ...promoted to the screen's ONE filled primary in the single state where the creator
#: surface offers none (``files_primary_action`` answering ``"none"``). Mapping owns no step
#: footer, so ``files_continue_lock_reason`` has no consumer here — this promotion is the
#: equivalent, and the panel never renders a disabled control it has nothing to open.
MAPPING_PANEL_DONE_LABEL = "Done"

# ---- the verified-fact refusals (plan 0044 S6 §6.1) ---------------------- #
MAPPING_NEEDS_TEST_HEADLINE = "This district needs a test conversion first"
#: Structural: no district name, no path, no digest. States the OUTCOME first ("nothing was
#: changed"), then the reason, then the one act that fixes it — the label of the button
#: rendered beside this very band.
MAPPING_NEEDS_TEST_NOTE = (
    "Nothing was changed. This district was set up on this computer, and it hasn't passed a "
    "test conversion as it now reads. Choose “Change this district's setup” to run one, then "
    "come back here."
)

# ---- the output-folder precondition on THIS host (§6.3) ------------------ #
MAPPING_PANEL_NEEDS_OUTPUT_HEADLINE = "No output folder is set yet"
#: The HOST owns the ROUTE (routing is host business), so this note carries one — unlike the
#: creator surface's own ``GATE_REFUSED_NO_OUTPUT_NOTE``, which names no step and no screen
#: because it renders on both hosts. Told BEFORE the button is pressed; the surface's own
#: bounded refusal still renders if it is pressed anyway.
MAPPING_PANEL_NEEDS_OUTPUT_NOTE = (
    "A test conversion needs an output folder, even though it writes nothing to it. Set one "
    "under Settings → Folders & district, then come back."
)
OPEN_SETTINGS_LABEL = "Open Settings"

# ---- the provenance notes on a user-authored card (§6.4) ----------------- #
#: A card-specific PAIR, not the gate's copy: ``GATE_STALE_*`` says "please run the test
#: again", which is right beside a test button and wrong on a card that has none. These name
#: the fact and point at the change door.
MAPPING_STALE_VERSION_NOTE = (
    "A different version of DistrictSync set this district up. Change its setup to run a "
    "test conversion against this version."
)
MAPPING_STALE_BASE_NOTE = (
    "The standard mapping this district builds on has changed since it was set up. Change "
    "its setup to run a test conversion against the new one."
)

#: The panel's own never-crash floor (the view has Mapping's). It says what is TRUE of a
#: failed authoring surface — nothing was changed — and offers the way back, because a floor
#: without one is a dead end in front of the screen the admin was on.
MAPPING_PANEL_FLOOR_HEADLINE = "We couldn't open this district's setup"
MAPPING_PANEL_FLOOR_DETAIL = "Nothing was changed. Your nightly sync keeps running in the background."


def _note_line(text: str, *, color: str) -> ft.Control:
    """One advisory line — never colour-alone (a glyph rides beside the words)."""
    return ft.Row(
        spacing=tokens.space_sm,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=18, color=color),
            ft.Text(text, size=tokens.type_body, color=color, expand=True),
        ],
    )


def _summary_lines(summary: ConfigSummary, *, notes: Sequence[str] = ()) -> list[ft.Control]:
    """The plain-language body of a config summary: what it produces + how many files it reads.

    A degraded (``loaded_ok=False``) config gets a calm "couldn't read this configuration" note
    instead of a fabricated summary (never a raw error).

    A config from the per-user ``mappings/`` dir also carries ``CUSTOM_ORIGIN_NOTE`` — on BOTH
    branches, because a mapping that was added here AND cannot be read is exactly the row whose
    provenance the admin most needs (that is the one they can fix or remove).

    ``notes`` (plan 0044 S6 §6.4) are the caller's already-derived provenance lines — "another
    build wrote this" / "the standard mapping it builds on has changed". Passed IN rather than
    read here: they cost a YAML read and a digest, so the caller derives them ONCE per mount
    and only on the ``origin == "user"`` branch (the S4b cost note), which is also why they
    are a plain sequence of sentences this function only paints.
    """
    if not summary.loaded_ok:
        lines: list[ft.Control] = [
            ft.Text(
                "We couldn't read this configuration — it may need attention.",
                size=14,
                color=tokens.color_status_warning,
                weight=ft.FontWeight.W_600,
            ),
        ]
    else:
        produces = ", ".join(summary.output_labels) if summary.output_labels else "nothing yet"
        files_word = "file" if summary.source_file_count == 1 else "files"
        lines = [
            ft.Text(f"Produces: {produces}", size=14, color=tokens.color_text),
            ft.Text(
                f"Reads {summary.source_file_count} extract {files_word}",
                size=13,
                color=tokens.color_muted,
            ),
        ]
    if summary.origin == "user":
        lines.append(ft.Text(CUSTOM_ORIGIN_NOTE, size=tokens.type_body, color=tokens.color_muted))
    lines.extend(_note_line(note, color=tokens.color_status_warning) for note in notes)
    return lines


# The "nothing chosen yet" state of the current-mapping card (0038 S5). Without it, a blank
# `sis_type` fell through `summarize_config("")` into the DEGRADED summary and painted "We
# couldn't read this configuration — it may need attention." over an empty name: a failure
# report about a district that was never chosen. Reachable from Convert's "Change mapping"
# route, which fires precisely when no district is saved.
NO_DISTRICT_TITLE = "No district saved yet"
NO_DISTRICT_DETAIL = "Pick one below to set the mapping DistrictSync uses for your nightly sync."


def _no_district_card() -> ft.Control:
    """The honest empty state — an unanswered question, not a fault."""
    return components.card(
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Text("Current mapping", size=14, weight=ft.FontWeight.W_700, color=tokens.color_muted),
                ft.Text(NO_DISTRICT_TITLE, size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
                ft.Text(NO_DISTRICT_DETAIL, size=14, color=tokens.color_muted),
            ],
        ),
    )


def _name_row(summary: ConfigSummary) -> ft.Control:
    """The card's district name, with the provenance badge beside it for an added mapping.

    A badge rather than more copy at the name's own tier: the pickers already mark the row
    with the same words (``CUSTOM_ORIGIN_LABEL``, single-sourced from ``mapping_catalog``), so
    an admin who chose the row and an admin reading the card see one wording. It rides in a
    wrapping Row so a long district name on a narrow window pushes the badge onto its own
    line rather than clipping it.
    """
    name = ft.Text(summary.district_name, size=tokens.type_title, weight=ft.FontWeight.W_800, color=tokens.color_text)
    if summary.origin != "user":
        return name
    return ft.Row(
        spacing=tokens.space_sm,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[name, components.origin_badge(CUSTOM_ORIGIN_LABEL)],
    )


def _summary_card(
    title: str,
    summary: ConfigSummary,
    *,
    notes: Sequence[str] = (),
    action: ft.Control | None = None,
) -> ft.Control:
    """A titled card for one config's summary: friendly name (primary) + what it produces + the raw id hint.

    ``action`` is the card's own affordance — the change door on a user-authored mapping
    (plan 0044 S6), absent on a shipped one.
    """
    body: list[ft.Control] = [
        ft.Text(title, size=14, weight=ft.FontWeight.W_700, color=tokens.color_muted),
        _name_row(summary),
        *_summary_lines(summary, notes=notes),
        # The raw sis_type — a small secondary technical hint only (support recoverability),
        # never the primary label.
        ft.Text(summary.sis_type, size=12, color=tokens.color_muted, selectable=True),
    ]
    if action is not None:
        body.append(ft.Row(controls=[action]))
    return components.card(content=ft.Column(spacing=10, controls=body))


def _surface(page: ft.Page, app_config: AppConfig, on_navigate: Callable[[str], None] | None) -> ft.Control:
    """Render the Mapping view (summary + switch + gated Apply) or the hosted creator panel.

    Apply writes through ``AppConfig`` and re-renders the view in place (D1): the
    current-mapping card, the pending summary, and the gate all recompute against the freshly
    PERSISTED current — so a switch shows immediately and can be reverted without a restart (the
    gate compares against ``persisted``, never the captured mount instance, via the pure
    ``mapping_catalog.can_apply``).

    The view is REBUILDABLE (plan 0044 S6) rather than built once: an activation or a discard
    from the panel changes which districts EXIST, so returning from the panel re-derives the
    catalog — otherwise the district an admin had just created would be missing from the very
    dropdown that could switch back to it.
    """
    # The persisted current sis_type — mutated on each successful Apply/activation so the gate,
    # the current-mapping card AND the scoped option list always track what's actually saved
    # (never the frozen mount value). Shared across view builds deliberately: it is the one
    # piece of state a rebuild must NOT re-read from the mount instance (`_on_apply` writes
    # through a FRESH `AppConfig.load()`, so the mount instance is stale the moment a switch
    # lands).
    persisted = {"sis": app_config.sis_type}
    # Apply/pick generation — an in-flight post-Apply schedule probe only paints if the banner
    # it refines is still the current one (a fresh pick/Apply/rebuild invalidates the stale refine).
    apply_seq = {"n": 0}
    # The ONE container whose `content` swaps between the view and the creator panel — the
    # `shell.root_host` pattern, in-screen, so the view's controls are OUT of the tree while
    # the panel is open and cannot contribute a second filled primary.
    body_host = ft.Container()
    # Handles into the CURRENT view build, rebound by every `_build_view` call: the banner slot
    # `_after_switch` paints into, that build's `_refresh`, and the district name it is showing
    # (the name the post-switch notice has to call the OLD district — read from the build that
    # was showing it, so there is one spelling of it for both writers).
    live: dict[str, object] = {
        "refresh": lambda: None,
        "banner": ft.Column(spacing=tokens.space_md, controls=[]),
        "name": "",
    }
    # The hosted panel's state — the same three things the wizard holds (`sis`, `form`, the
    # pending rename map that must outlive a re-render: S4 review BLOCKING 2), plus the stage
    # it is on, the note it is showing and the FRESH `AppConfig` taken at panel OPEN (never the
    # mount instance — `_on_apply`'s reason: another surface may have written since).
    panel: dict[str, object] = {}
    # The provenance notes are I/O (a YAML read + a base digest), so they are derived at most
    # ONCE per district per VIEW BUILD and only on the `origin == "user"` branch. Cleared by
    # `_show_view` — see the reason there: a memo that outlived the change door would keep
    # showing a note the door had just fixed.
    stale_memo: dict[str, tuple[str, ...]] = {}

    def _origin_of(origins: dict[str, str], sis: str) -> str:
        """This install's provenance for ``sis`` — ABSENT reads as ``"bundled"``.

        The same fail-OPEN direction ``mapping_catalog._origin_of`` documents for itself, and
        the one consistent with the threat model: the verified-fact check exists to stop an
        admin MISTAKE, and may never strand an admin whose provenance we could not read.
        """
        return origins.get(sis, "bundled")

    def _stale_notes(sis: str, origin: str) -> tuple[str, ...]:
        """The provenance notes for a card (§6.4). TOTAL — no notes rather than a raise."""
        if origin != "user" or not sis.strip():
            return ()
        if sis not in stale_memo:
            try:
                fact = overlay_staleness(
                    read_authored_with(sis),
                    running_version=app_version(),
                    current_base_digest=base_digest_for(sis),
                )
                notes = [
                    note
                    for note, flag in (
                        (MAPPING_STALE_VERSION_NOTE, fact.version_differs),
                        (MAPPING_STALE_BASE_NOTE, fact.base_changed),
                    )
                    if flag
                ]
            except Exception:  # noqa: BLE001 - advisory: a provenance read may not break the card
                logger.warning("Could not read the provenance of the mapping %r.", sis)
                notes = []
            stale_memo[sis] = tuple(notes)
        return stale_memo[sis]

    # ---- the schedule read-back (shared by both writers) ----------------- #
    def _refine_from_probe(
        gen: int,
        task_name: str,
        hint_registered: bool,
        paint: Callable[[ScheduleState | None], None],
    ) -> None:
        # Read-back-capable schedulers only (like the shell's badge probe): elsewhere the hedged
        # initial paint IS the honest final state — a live schedule is never asserted from the
        # hint alone. Gated on the scheduler capability (W4a T2.3), not ``sys.platform``.
        if not get_scheduler().supports_read_schedule:
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

    def _after_switch(*, old_district_name: str, new_district_name: str) -> None:
        """The ONE post-switch presentation, for BOTH writers of ``sis_type`` on this screen.

        Apply and an activation from the panel paint the identical "Now using …" band and the
        identical ``post_apply_presentation`` stale-schedule notice with its "Open Settings"
        route. Mapping still never re-registers the nightly and never collects credentials
        (owner decision 2026-07-15) — which is exactly why an activation may not skip this
        notice: the task it warns about is just as stale either way.
        """
        apply_seq["n"] += 1
        gen = apply_seq["n"]
        # Read the schedule facts from DISK, after the write: both writers have just saved, and
        # a mount-time snapshot would describe the pre-write world.
        cfg = AppConfig.load()
        hint_registered = cfg.schedule_registered
        task_name = cfg.schedule_task_name

        def _paint_banner(schedule_state: ScheduleState | None) -> None:
            # The pure decision: healthy detail + (optionally) the stale-schedule notice.
            pres = post_apply_presentation(
                old_district_name,
                schedule_state=schedule_state,
                hint_registered=hint_registered,
            )
            banners: list[ft.Control] = [
                components.HealthVerdictBanner(
                    Verdict.HEALTHY,
                    headline=f"Now using {new_district_name}",
                    detail=pres.healthy_detail,
                )
            ]
            if pres.notice is not None:
                # The fix routes to the ONE re-register flow Settings owns (never re-register
                # here). Secondary tier — "Use this mapping" is this screen's filled primary.
                trailing = (
                    components.secondary_button(OPEN_SETTINGS_LABEL, lambda _e: on_navigate("setup"))
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
            slot: ft.Column = live["banner"]  # type: ignore[assignment]
            slot.controls = banners

        # Paint-then-refine (Home's pattern): the immediate banner is record-based honest
        # (hint-hedged, never asserted), then the real read-back upgrades it in place.
        _paint_banner(None)
        refresh: Callable[[], None] = live["refresh"]  # type: ignore[assignment]
        refresh()  # re-render the current card + re-derive the gate (reverting is now possible)
        _refine_from_probe(gen, task_name, hint_registered, _paint_banner)

    # ---- the hosted creator panel (§6.2) --------------------------------- #
    def _panel_cfg() -> AppConfig:
        return panel["cfg"]  # type: ignore[return-value]

    def _panel_form() -> CreatorForm:
        return panel["form"]  # type: ignore[return-value]

    def _panel_pending() -> dict[str, str]:
        return panel["pending"]  # type: ignore[return-value]

    def _panel_names_pending() -> bool:
        """Whether the panel's rows express file names the config on disk does not have.

        ONE spelling for the host, read from the pure ``has_unsaved_renames`` — the same
        comparison the creator surface tiers its own Save on, and the same one the wizard's
        footer is closed by. Two spellings of "something is pending" is how a second filled
        primary appears (plan 0044 S4 review, BLOCKING 2).
        """
        return has_unsaved_renames(_panel_pending(), _panel_form().renames)

    def _panel_done_ready() -> bool:
        """Whether the panel's way back is the screen's ONE filled primary.

        Mirrors ``files_primary_action``'s ``"none"`` answer EXACTLY, from the two inputs the
        host holds: the pending rename map it owns (the same ``has_unsaved_renames``
        comparison the creator tiers its own Save on) and ``creator_gate_current`` — which is
        precisely the creator surface's own ``already``, and the same fact the wizard's FILES
        step is satisfied by. Deliberately NOT ``cfg.sis_type == sis`` as well: the creator's
        ``already`` does not include that equality, so requiring it here would leave the
        (reachable) "tested, current, but not the active district" state with NO filled
        primary at all — the invariant this promotion exists to hold.

        Re-derived on EVERY host render, so a rename picked after this control was built
        drops it back to the text-tier Back rather than leaving a second filled primary on
        screen beside the creator's Save.
        """
        sis = str(panel.get("sis") or "").strip()
        if not sis:
            return False
        already = creator_gate_current(_panel_cfg(), sis)
        return files_primary_action(unsaved=_panel_names_pending(), passed=False, already=already) == "none"

    def _on_panel_done(_e: ft.ControlEvent | None = None) -> None:
        """Leave the panel — unless file names on screen are not in the config on disk.

        The load-bearing half of the two-primaries fix on THIS host (plan 0044 S6 review,
        BLOCKING 1), and the same refusal the wizard's footer ``_forward`` makes: the rows
        re-render themselves when a name is picked, but this control was built before the
        pick and is still painting the filled "Done" — so pressing it would leave the panel
        with the pending names discarded, and the write it skipped is the only record of
        them. Re-renders the panel instead of returning silently: one press and the surface
        reads its own truth (this control drops to the text-tier Back, the creator's Save
        takes the primary tier, and ``FILES_UNSAVED_NOTE`` is on screen). The pending map is
        the host's own dict, mutated in place, so the picks survive the re-render.
        """
        if _panel_names_pending():
            _show_panel()
            return
        _show_view()

    def _panel_way_back() -> ft.Control:
        """Back (text tier, while the creator owns the primary) → Done (filled) when it does not."""
        if panel.get("stage") == "files" and _panel_done_ready():
            return ft.Row(
                controls=[
                    components.primary_button(
                        MAPPING_PANEL_DONE_LABEL,
                        _on_panel_done,
                        icon=ft.Icons.CHECK_ROUNDED,
                    )
                ]
            )
        return ft.Row(
            controls=[
                components.text_button(
                    MAPPING_PANEL_BACK_LABEL,
                    lambda _e: _show_view(),
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                )
            ]
        )

    def _panel_controls() -> list[ft.Control]:
        cfg = _panel_cfg()
        controls: list[ft.Control] = [_greeting_header(app_config), _panel_way_back()]
        # The host's precondition notice (§6.3): the admin is told BEFORE pressing, and the
        # creator surface's own bounded refusal still renders if they press anyway.
        if not validate_output_dir(str(cfg.output_dir or "")).ok:
            controls.append(
                components.HealthVerdictBanner(
                    Verdict.WARNING,
                    headline=MAPPING_PANEL_NEEDS_OUTPUT_HEADLINE,
                    detail=MAPPING_PANEL_NEEDS_OUTPUT_NOTE,
                    trailing=(
                        components.text_button(OPEN_SETTINGS_LABEL, lambda _e: on_navigate("setup"))
                        if on_navigate is not None
                        else None
                    ),
                )
            )
        note = str(panel.get("note") or "")
        if note:
            controls.append(_note_line(note, color=tokens.color_muted))
        controls.append(
            build_creator(
                page,
                cfg=cfg,
                sis_id=str(panel.get("sis") or ""),
                form=_panel_form(),
                on_written=_on_creator_written,
                on_files_saved=_on_creator_files_saved,
                on_activated=_on_creator_activated,
                on_discarded=_on_creator_discarded,
                stage=panel["stage"],  # type: ignore[arg-type]
                pending=_panel_pending(),
                # Mapping owns no step footer, so there is no locked Continue to explain.
                continue_lock_note=None,
            )
        )
        return controls

    def _show_panel() -> None:
        try:
            content: ft.Control = ft.Column(spacing=tokens.space_xl, controls=_panel_controls())
        except Exception:  # noqa: BLE001 - the panel's own floor, with the way back on it
            logger.warning("Could not render the district setup panel on Mapping.", exc_info=True)
            content = components.ErrorCard(
                MAPPING_PANEL_FLOOR_HEADLINE,
                MAPPING_PANEL_FLOOR_DETAIL,
                action=components.secondary_button(MAPPING_PANEL_BACK_LABEL, lambda _e: _show_view()),
            )
        body_host.content = content
        page.update()

    def _open_panel(*, sis: str, form: CreatorForm, stage: CreatorStage, note: str = "") -> None:
        """Open the panel on a FRESH ``AppConfig`` — never the mount instance."""
        panel.clear()
        panel.update(
            {
                "sis": sis,
                "form": form,
                "stage": stage,
                "note": note,
                "cfg": AppConfig.load(),
                # Created here and mutated in place by the creator surface for the panel's
                # lifetime, so a re-render cannot forget the file names that were picked.
                "pending": {},
            }
        )
        _show_panel()

    def _on_creator_written(new_sis: str, form: CreatorForm, note: str) -> None:
        """The overlay is on disk — stay on the panel and move to the file names."""
        panel["sis"] = new_sis
        panel["form"] = form
        panel["stage"] = "files"
        panel["note"] = note
        _show_panel()

    def _on_creator_files_saved(form: CreatorForm, note: str) -> None:
        """The file names are on disk — re-render the SAME stage against what it now says."""
        panel["form"] = form
        panel["stage"] = "files"
        panel["note"] = note
        _show_panel()

    def _on_creator_activated() -> None:
        """``sis_type`` is now this district: close the panel, then the shared post-switch paint."""
        old_name = str(live["name"])
        persisted["sis"] = str(_panel_cfg().sis_type)
        _show_view()  # rebuilt against the invalidated catalog, so the new district is offerable
        _after_switch(old_district_name=old_name, new_district_name=str(live["name"]))

    def _on_creator_discarded() -> None:
        """The overlay is gone — back to the view, saying so."""
        _show_view(note=CREATOR_DISCARDED_NOTE)

    # ---- the Mapping view ------------------------------------------------ #
    def _build_view(*, note: str = "") -> ft.Control:  # noqa: C901 - one view build, assembled linearly
        # ONE catalog build per view build (0038 S5): the district rows this admin sees, scoped
        # by the stored identity's domain. Replaces the pre-S5 DOUBLE `list_configs()` parse
        # (the summaries dict and the dropdown options each read the disk independently); the
        # build is session-memoised, so the switch selector no longer costs a second pass.
        # Scoped unconditionally to the stored address since 2026-08-04 (the per-surface
        # show-all row retired) — an admin who needs another district's mapping clears that
        # address in Settings, which is the one input this scoping has.
        catalog = filtered_catalog(
            stored_identity_domain(app_config),
            saved_sis=persisted["sis"],
            # The un-applied selection rides too, so a re-derived list can never drop the row
            # the dropdown is set to.
            picked_sis=persisted["sis"],
        )
        summaries = {s.sis_type: s for s in catalog.summaries}
        # The SAME memoised build answers "where did this mapping come from?" for the
        # verified-fact check — no second parse, and no second source of provenance.
        origins = {s.sis_type: s.origin for s in catalog.summaries}
        # Ensure the current config is summarizable even if not in the discovered list (defensive).
        # `filtered_catalog` already carries the saved district when it EXISTS; this covers the
        # case where it does not exist at all (a hand-edited `config.json`), which the filter
        # deliberately refuses to fabricate. Guarded by an `if` rather than `setdefault`, whose
        # eagerly-evaluated argument re-parsed the current district's YAML on EVERY mount even
        # though the catalog had just summarised it.
        if persisted["sis"].strip() and persisted["sis"] not in summaries:
            summaries[persisted["sis"]] = summarize_config(persisted["sis"])

        # Mutable pending selection — starts on the current config (so Apply is a no-op → disabled).
        pending = {"sis": persisted["sis"]}
        # The row a verified-fact refusal is currently displayed for (``""`` = none). Its ONLY
        # job is to keep the refusal band's change door from rendering TWICE: the band sits
        # directly under the "Switch to" card whose own door would point at the same district,
        # and two identically-labelled buttons for one row is a coin toss, not a choice. The
        # band's door wins because it opens on the TEST, which is what the refusal asks for.
        refused = {"sis": ""}

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

        def _edit_door(sis: str) -> ft.Control | None:
            """The CHANGE door — secondary tier, and ONLY on a mapping authored here."""
            if _origin_of(origins, sis) != "user":
                return None
            return components.secondary_button(
                MAPPING_EDIT_LABEL,
                lambda _e, sis=sis: _open_panel(
                    sis=sis,
                    form=creator_form_from_overlay(sis),
                    stage="forms",
                ),
                icon=ft.Icons.TUNE_ROUNDED,
            )

        def _refresh() -> None:
            # Re-render the current-mapping card + the pending summary + re-derive the gate, all
            # against the freshly-PERSISTED current — so an Apply is reflected in place and revertible.
            # A BLANK current district is a question we have not asked, not a config that failed to
            # load — it gets the honest empty state rather than the degraded failure card.
            current = persisted["sis"]
            if not current.strip():
                current_card_slot.controls = [_no_district_card()]
                live["name"] = ""
            else:
                current_summary = _summary_for(current)
                current_card_slot.controls = [
                    _summary_card(
                        "Current mapping",
                        current_summary,
                        notes=_stale_notes(current, _origin_of(origins, current)),
                        action=_edit_door(current),
                    )
                ]
                live["name"] = current_summary.district_name
            pending_summary = _summary_for(pending["sis"])
            pending_summary_slot.controls = (
                []
                if not pending["sis"].strip()
                else [
                    _summary_card(
                        "Switch to",
                        pending_summary,
                        # The door on the PENDING row, only while it is a different mapping from
                        # the current one (on mount the two cards describe the same config) and
                        # while no refusal band below is already carrying it.
                        action=(
                            _edit_door(pending["sis"])
                            if pending["sis"] != current and pending["sis"] != refused["sis"]
                            else None
                        ),
                    )
                ]
            )
            apply_btn.disabled = not can_apply(pending_summary, current)
            page.update()

        def _on_pick(e: ft.ControlEvent) -> None:
            pending["sis"] = e.control.value or persisted["sis"]
            apply_seq["n"] += 1  # invalidate any in-flight post-Apply probe (its banner is cleared)
            applied_banner_slot.controls = []  # a fresh pick clears a prior confirmation
            refused["sis"] = ""  # ...and the refusal that was in it
            _refresh()

        def _paint_needs_test(sis: str) -> None:
            """The verified-fact REFUSAL: nothing written, and the fix one press away."""
            refused["sis"] = sis
            applied_banner_slot.controls = [
                components.HealthVerdictBanner(
                    Verdict.WARNING,
                    headline=MAPPING_NEEDS_TEST_HEADLINE,
                    detail=MAPPING_NEEDS_TEST_NOTE,
                    trailing=components.secondary_button(
                        MAPPING_EDIT_LABEL,
                        lambda _e: _open_panel(
                            sis=sis,
                            form=creator_form_from_overlay(sis),
                            # Straight onto the file names + the test conversion: that IS the fix.
                            stage="files",
                        ),
                        icon=ft.Icons.TUNE_ROUNDED,
                    ),
                )
            ]
            _refresh()  # the card above hands its door to the band (see ``refused``)

        def _on_apply(_e: ft.ControlEvent) -> None:
            pending_summary = _summary_for(pending["sis"])
            # Re-check the gate so a broken / no-op config can never reach AppConfig.save().
            if not can_apply(pending_summary, persisted["sis"]):
                return
            target = pending_summary.sis_type
            origin = _origin_of(origins, target)
            # A FRESH instance: the digests the check reads live in settings another surface may
            # have written since this mount (the panel records one on every activation).
            cfg = AppConfig.load()
            verdict = activation_allowed(
                cfg,
                sis_id=target,
                origin=origin,
                # `None` on the bundled branch deliberately — the rule never reads it there, so
                # the 20 shipped rows pay no config load.
                current_digest=current_digest(target) if origin == "user" else None,
            )
            if not verdict.allowed:
                # Nothing written, no save, no confirmation: "Now using …" over an untested
                # district is the exact claim this check exists to prevent.
                _paint_needs_test(target)
                return
            # Capture the OLD district's display name BEFORE overwriting — a registered nightly
            # task keeps converting the pre-Apply district, so the notice must name that one.
            old_name = str(live["name"])
            refused["sis"] = ""
            cfg.sis_type = target
            cfg.save()
            persisted["sis"] = target  # the switch is now the persisted current
            _after_switch(old_district_name=old_name, new_district_name=pending_summary.district_name)

        apply_btn.on_click = _on_apply

        def _options(cat) -> list[ft.dropdown.Option]:  # noqa: ANN001 - a FilteredCatalog
            # A structural allowlist (no free-text sis_type), now scoped — and labelled through
            # `disambiguated_labels` so no two rows can read identically (a partner-authored YAML
            # sharing a bundled district_name carries its raw id).
            labels = disambiguated_labels(cat.summaries)
            return [ft.dropdown.Option(key=s.sis_type, text=labels[s.sis_type]) for s in cat.summaries]

        switch_dropdown = ft.Dropdown(
            label="Roster mapping",
            value=persisted["sis"] or None,
            options=_options(catalog),
            # ft.Dropdown's value-change event is on_select on flet 0.85.3 (no on_change).
            on_select=_on_pick,
            border_color=tokens.color_border,
        )

        # The CREATE door is text tier at the FOOT of the switch card, so "Use this mapping"
        # stays that card's one filled primary — and it is offered in every state, because
        # Mapping is on the rail in every state (D7) and a door that came and went would be
        # state-dependent for no safety gain.
        doors: list[ft.Control] = [
            components.text_button(
                MAPPING_CREATE_LABEL,
                lambda _e: _open_panel(sis="", form=creator_form_for_new(app_config), stage="forms"),
                icon=ft.Icons.ADD_ROUNDED,
            )
        ]
        # ...and a creation abandoned ANYWHERE is resumable here: the token is a fact about the
        # install, not about the wizard. `pending_creator_sis` self-heals a token whose overlay
        # is gone, so a stale one renders no door rather than a dead one.
        resumable = pending_creator_sis(app_config)
        if resumable:
            doors.append(
                components.text_button(
                    MAPPING_RESUME_LABEL,
                    lambda _e, sis=resumable: _open_panel(
                        sis=sis,
                        form=creator_form_from_overlay(sis),
                        stage="files",
                    ),
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                )
            )

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
                    ft.Row(wrap=True, spacing=tokens.space_md, controls=doors),
                ],
            ),
        )

        live["refresh"] = _refresh
        live["banner"] = applied_banner_slot
        _refresh()  # paint the initial current card + pending summary (= current) + the gate (disabled)

        controls: list[ft.Control] = [_greeting_header(app_config)]
        if note:
            controls.append(_note_line(note, color=tokens.color_muted))
        controls.extend([current_card_slot, switch_card])
        return ft.Column(spacing=22, controls=controls)

    def _show_view(*, note: str = "") -> None:
        apply_seq["n"] += 1  # a rebuild invalidates any in-flight probe painting into the old slot
        # ...and so does the provenance memo (plan 0044 S6 review, SHOULD 1): the change door
        # is the very fix these notes ask for, and it re-writes ``authored_with`` on its way
        # back here. A memo held across the return would leave "a different version set this
        # district up" standing over a district this version has just re-written — the note
        # surviving its own fix. Cleared rather than invalidated per district because a
        # rebuild re-derives at most one card's worth (the ``origin == "user"`` branch only),
        # and only for the districts the rebuilt view actually shows.
        stale_memo.clear()
        body_host.content = _build_view(note=note)
        page.update()

    _show_view()
    return body_host


def build_mapping(
    page: ft.Page,
    *,
    app_config: AppConfig,
    on_navigate: Callable[[str], None] | None = None,
) -> ft.Control:
    """Build the Mapping surface (review + switch + author the district config). ``page`` drives updates.

    Sync read on mount, verdict-first apply, wrapped in a never-crash ``ErrorCard`` fallback so
    even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth — the
    catalog derivation is already TOTAL). ``on_navigate`` (Home's exact pattern, injected by
    the shell with rail-follow) routes the post-Apply stale-schedule notice and the creator
    panel's output-folder precondition to Settings; when absent (``None``, defensive default)
    both render without the routing button — never a crash, and the rail still carries Settings.
    The 0034 Slice 1 honesty fix supersedes the earlier no-``on_navigate`` decision.
    """
    try:
        return _surface(page, app_config, on_navigate)
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't open Mapping",
            "Your nightly sync keeps running in the background.",
        )
