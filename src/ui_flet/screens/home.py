"""The three-way Home health dashboard — the flagship trust surface (IA model IA-3).

VIEW glue (coverage-omitted): the trust-critical *decision* lives COUNTED in the pure
modules (``history.store.read_run_records`` reads the run store; ``home_status.derive_home_status``
derives the verdict). This file only RENDERS that already-tested output, verdict-first,
so a non-technical admin's deep question — *"is my sync OK?"* — is answered in one
plain-language banner before any metric.

Three-way dispatch (mirrors the IA model; branch (a) is the first-run surface):
  * **(a) not set up yet** — ``nav.needs_setup(app_config)`` → Home **HOSTS the setup
    wizard itself** (0038 S6), under one state-aware welcome line. There is no longer a
    hero pointing at another rail item: the old ``screens/onboarding.py`` was a door into
    the room you were standing in, so it retired and its CTA's destination moved here.
  * **(b) configured + healthy** — a green ``HealthVerdictBanner`` whose detail carries one
    roster-SIZE number, then the quick-action strip. The metric-tile row retired at 0038 S7:
    it only ever rendered on the happy path, and the one thing it carried that the verdict
    does not — "does this roster look the right size?" — folded into the healthy line
    (``home_status.size_clause``). Per-entity counts live in Run History for the rostering +
    myBlueprint+ entities; an attendance district's row count reaches exactly ONE surface — that
    healthy size clause — because Run History's columns exclude ``StudentAttendance``, and the
    clause is HEALTHY-branch only. An OPEN item in ``docs/claugentic-ROADMAP.md``.
  * **(c) configured + broken / attention / empty / unavailable** — an amber/red banner
    NAMING the fault (from the pure derivation, never a raw ``error``/path) + a concrete
    fix-path CTA (``status.fix``), then the same strip minus the destination that CTA owns.

**One filled primary, in EVERY dashboard state** (Direction B): the fix CTA when there is a
fault, "Convert now" when there is not — decided by the pure ``home_status.quick_actions``,
never by this file. Both were previously under-satisfied: the healthy dashboard carried NO
filled action at all.

Built as a **callback-driven factory** — ``build_home`` owns NO navigation or lifecycle
(``on_navigate(dest_id)`` is injected by the shell = ``select_by_id``), mirroring
``nav_rail``'s discipline.

Assembled ENTIRELY from ``components.py`` (cards/buttons/banner) + ``tokens`` — never
hand-rolled controls (the ``FilledButton(text=)`` trap; see ``docs/FLET_1.0_CONVENTIONS.md``).

**Never-crash floor — TWO of them, with DIFFERENT copy on purpose.** The configured-branch
read/derive/render is wrapped in ``try/except`` → ``components.ErrorCard`` on any unexpected
error, so even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth
— the parser + derivation are already TOTAL). Branch (a) has its own, because the dashboard
floor's reassurance ("your nightly sync keeps running in the background") is FALSE for an
install that has not finished setup: there is no schedule and there has never been a run.
Its copy says what is true instead — nothing has been changed — and routes to Help.

**The identity cards (0038 S4b)** ride the configured branch, immediately under the verdict
block: the one-time upgrade ask, the G3 mismatch question, and the durable "we don't have a
mapping for SD## yet" card (see ``NOT_LISTED_HEADLINE`` — never a claim about vendor work
nobody has been told about). They are ADVISORY — none of them writes anything but an
``identity_*`` field (through the ``identity_save`` choke point, which structurally cannot
touch ``sis_type``), none of them blocks, and they carry their OWN floor so a bug in them
can never replace the verdict with Home's ``ErrorCard``.

**Sync read on mount** (no loading state): the run store is a small local SQLite DB read to
a ``list[dict]`` (microseconds), so it is read inline in the factory — the worker-thread
convention is scoped to ``run_pipeline`` (see ``docs/FLET_1.0_CONVENTIONS.md``), and an
async path here would add the doc's #1 concurrency trap for no user-perceptible gain
(YAGNI). The empty state IS a real reachable state and is rendered (via the pure derivation's
"no runs yet" branch); a loading skeleton is deliberately NOT built (nothing async to load).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Sequence
from enum import Enum

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs
from src.history.store import read_run_records, store_meta
from src.scheduler import get_scheduler
from src.ui_flet import about, components, nav, tokens
from src.ui_flet.home_status import (
    FixAction,
    derive_home_status,
    quick_actions,
    sync_window_paused,
    welcome_band,
)
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.identity_gate import (
    can_continue,
    matched_excludes_saved,
    matched_state,
    needs_identity_prompt,
    unmapped_sd_number,
)
from src.ui_flet.mapping_catalog import active_output_entities, district_domain_index
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.screens import identity as identity_screen
from src.ui_flet.screens import setup as setup_screen
from src.ui_flet.screens.help import SUPPORT_EMAIL
from src.ui_flet.screens.identity import NOT_LISTED_NOTE_TAIL as UNMATCHED_DISTRICT_NOTE
from src.ui_flet.verdict import Verdict
from src.utils.identity import extract_domain, normalize_email
from src.utils.validators import IDENTITY_EMAIL_MAX_LEN, validate_identity_email
from src.utils.version import app_version

logger = logging.getLogger(__name__)


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


# --------------------------------------------------------------------------- #
# Branch (a) — Home HOSTS the setup wizard (0038 S6). Copy first.              #
#                                                                             #
# The floor copy here is branch-SPECIFIC and that is the whole reason it       #
# exists. Home's dashboard floor reassures the admin that "your nightly sync   #
# keeps running in the background" — true of a working install whose STATUS    #
# view broke, and false in every particular for an install that has no         #
# schedule, no run and no district yet. The only reassurance we can honestly   #
# offer this admin is that we changed nothing, and the only route worth        #
# offering is the one that reaches a person.                                   #
# --------------------------------------------------------------------------- #
SETUP_UNAVAILABLE_HEADLINE = "We couldn't open setup right now"
SETUP_UNAVAILABLE_DETAIL = "Nothing has been changed. Close DistrictSync and open it again, or contact support."
SETUP_UNAVAILABLE_HELP_LABEL = "Get help"


def _quick_actions_row(fix: FixAction | None, on_navigate: Callable[[str], None]) -> ft.Control | None:
    """Slim Home's quick-action strip (0038 S7) — the few places an admin actually goes next.

    Replaces the metric-tile row that stood here. The TIERING is decided by the pure
    ``home_status.quick_actions`` (which action is filled, and which destination the verdict's
    fix CTA already owns); this function is assembly only. Exactly one filled button exists on
    the surface in every state — the fix when there is a fault, "Convert now" when there is
    not — so the design system's one-primary rule holds without the view having to reason
    about it.

    The ``dest_id`` is bound as a DEFAULT ARGUMENT, not captured from the loop variable: a
    late-binding closure here would wire every button to the last destination in the strip.
    """
    actions = quick_actions(fix)
    if not actions:
        return None
    controls: list[ft.Control] = []
    for action in actions:
        factory = components.primary_button if action.filled else components.secondary_button
        controls.append(factory(action.label, lambda _e, dest=action.dest_id: on_navigate(dest)))
    return ft.Row(spacing=tokens.space_md, wrap=True, controls=controls)


def _fix_button(fix: FixAction, on_navigate: Callable[[str], None]) -> ft.Control:
    """The concrete fix-path CTA under the verdict (only when a ``FixAction`` is present)."""
    return ft.Container(
        padding=_pad_sym(0, 2),
        content=components.primary_button(
            fix.label,
            lambda _e: on_navigate(fix.dest_id),
        ),
    )


# --------------------------------------------------------------------------- #
# The identity cards (0038 S4b) — copy first, controls after.                  #
#                                                                             #
# Three cards on the CONFIGURED branch, and one rule above all of them: none   #
# may touch the sync. They ask, they report, and they hop to the surface that  #
# owns a change — they never make one. Every write goes through               #
# ``AppConfig.identity_save``, which structurally cannot write ``sis_type``.   #
#                                                                             #
# COPY DISCIPLINE. The register is IDENTIFICATION, never authentication: the   #
# banned vocabulary (sign in / log in / verify / unlock / authorized /         #
# account / credentials / access) is absent by construction, and the district- #
# domain list is never called protected / secured / anonymous / encrypted.     #
# Where a fact is IDENTICAL to one S4a already worded — the honest helper, the #
# matched-several note, the calm no-match, the "couldn't save" — the S4a       #
# constant is IMPORTED rather than retyped. Two wordings of one fact is how    #
# surfaces start to disagree with each other.                                  #
# --------------------------------------------------------------------------- #
IDENTITY_CARD_HEADLINE = identity_screen.HERO_HEADLINE
# The new fact this card carries that the launch page does not: it is an ASK on a working
# install, so the first thing it must say is that answering (or not) is free.
IDENTITY_CARD_DETAIL = f"{identity_screen.HERO_DETAIL} Nothing about your nightly sync changes."
IDENTITY_CARD_SAVE_LABEL = "Save"
IDENTITY_CARD_DISMISS_LABEL = "Don't ask again"
# Permanent, and the copy says where it is recoverable — a dismissal with no stated way
# back is indistinguishable from a bug the next time the admin wants to answer.
IDENTITY_CARD_DISMISSED_NOTE = (
    f'We won\'t ask again. You can add it any time in Settings, under "{setup_screen.IDENTITY_TITLE}".'
)
# NOT the launch page's ``matched_headline`` — that one promises "you'll confirm it on the
# next step", and on Home there is no next step. The fact here is genuinely different: this
# install is ALREADY set up, and the address agrees with it.
IDENTITY_CARD_MATCHED_NOTE = "Saved. That's {district} — the district this sync is set up for."
# ...and NOT Settings' several-note either ("you'll choose the right one under Folders &
# district"). On a CONFIGURED Home nothing is pending to choose, and that instruction would
# send an SD51 admin — whose two configs share one domain, so this is the LIVE case, not a
# hypothetical — into the district picker: the single action this card promises never to
# cause. Home's version states the fact and stops.
IDENTITY_CARD_SEVERAL_NOTE = "Saved. That email matches more than one setup — this sync is set up for {district}."
# The blank-`sis_type` companions. A hand-edited profile can carry `setup_completed: true`
# with no district, and "the district this sync is set up for" would then name a district
# the install does not use.
IDENTITY_CARD_MATCHED_NO_DISTRICT_NOTE = "Saved. That's {district}."
IDENTITY_CARD_SEVERAL_NO_DISTRICT_NOTE = "Saved. That email matches more than one setup."
# The way back from an address that is VALID but wrong — the dismissed line already names
# Settings, and an answered card must not be the one state with no stated route.
IDENTITY_CARD_CHANGE_CLAUSE = f'You can change it in Settings, under "{setup_screen.IDENTITY_TITLE}".'

# The G3 mismatch card. It reports a difference and offers two ways to resolve it; it never
# resolves one itself. The detail is precise about WHAT was saved, because it renders
# immediately after a SUCCESSFUL `identity_save` — a bare "Nothing has been changed." there
# would contradict the write that just happened.
MISMATCH_HEADLINE = "You're set up for {saved}, and your address matches {matched}."
MISMATCH_DETAIL = (
    "That can be perfectly normal. We've saved your address; your district and sync settings are unchanged."
)
MISMATCH_KEEP_LABEL = "Keep {saved}"
MISMATCH_CHANGE_LABEL = "Change district"

# The durable not-listed card — the only reader ``identity_sd_number`` has.
#
# It says what is TRUE OF US, not what is happening at a vendor. The district number lives
# only in this computer's `config.json`, the support mail is subject-only, and the detail
# line asks the ADMIN to start the conversation — so "We're building the mapping for SD##"
# would assert work nobody has been told about. (`identity_gate.unmapped_sd_number` calls
# the opposite error — claiming to be "building" a mapping that already ships — a plain
# untruth; this is the same error pointed the other way.)
# The way BACK to the launch page, on the ONE surface that had none (QA 2026-08-18).
#
# The launch page was strictly one-way: it renders only while `needs_identity` holds, and
# answering it stores an address that makes the predicate false from then on. The only surface
# that can change or clear that answer is the Settings scroll — which is on the far side of the
# wizard. An admin who mistyped, or who realises they answered for someone else, therefore had
# no correction for the whole of first-run. This is that correction, and it is text-tier
# deliberately: the wizard's own Continue is the screen's single filled primary, and this must
# read as an escape hatch, not as a step.
RESTART_IDENTITY_LABEL = "Start over with a different address"
# The write is `identity_save`, NEVER `identity_clear` — the same distinction the not-listed
# card's dismiss makes. `identity_clear`'s quarantine purge deletes the `config.corrupt-*.json`
# settings-recovery copies, which is an ERASURE; "I typed the wrong address" is not a request to
# destroy anything. Both identity fields go, because the SD number was collected in the same
# breath as the address and would otherwise outlive the answer it belonged to.
RESTART_IDENTITY_FIELDS: dict[str, str] = {"identity_email": "", "identity_sd_number": ""}

NOT_LISTED_HEADLINE = "We don't have a mapping for SD{digits} yet"
# IMPORTED, not re-typed (QA 2026-08-18): this card and the launch page answer the same
# question, and they said different things before. The address it names is the same one the
# button below it opens and the line beside it copies — help.py's house pattern, because a bare
# mailto is a dead click on a locked-down district server.
NOT_LISTED_DETAIL = UNMATCHED_DISTRICT_NOTE
NOT_LISTED_EMAIL_LABEL = f"Email {SUPPORT_EMAIL}"
NOT_LISTED_COPY_TOOLTIP = "Copy email address"
NOT_LISTED_DISMISS_LABEL = "Don't show this again"


class _CardStage(str, Enum):
    """What the identity card is currently showing. The test seam for its states."""

    ASK = "ask"
    ANSWERED = "answered"
    MISMATCH = "mismatch"
    DISMISSED = "dismissed"
    RETIRED = "retired"


def join_district_names(names: Sequence[str]) -> str:
    """ "A" / "A and B" / "A, B and C" — the mismatch headline's matched-side list.

    The spec's sentence is singular, but a single domain legitimately claims several
    configs (SD51 and its attendance tier share one), so the plural case is real and must
    read as a sentence rather than as a tuple repr.
    """
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"


def mismatch_headline(saved: str, matched: Sequence[str]) -> str:
    return MISMATCH_HEADLINE.format(saved=saved, matched=join_district_names(matched))


def not_listed_headline(digits: str) -> str:
    return NOT_LISTED_HEADLINE.format(digits=digits)


def _card_note(text: str, *, failed: bool = False, muted: bool = False) -> ft.Control:
    """One inline note under a card's controls — never colour-alone.

    A failure carries the error glyph beside the words; the words themselves always say
    what happened, so the colour is a second cue and never the only one.
    """
    if failed:
        return ft.Row(
            spacing=tokens.space_sm,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=tokens.type_section, color=tokens.color_status_failed),
                ft.Text(text, size=tokens.type_body, color=tokens.color_status_failed),
            ],
        )
    color = tokens.color_muted if muted else tokens.color_status_healthy
    return ft.Text(text, size=tokens.type_body, weight=ft.FontWeight.W_600, color=color)


def _build_identity_cards(
    page: ft.Page,
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
) -> ft.Control | None:
    """The identity card block, or ``None`` when neither card applies.

    Built ONCE per Home mount and handed to ``_render`` as an already-assembled control,
    so the off-thread schedule read-back's re-derive cannot destroy an address the admin
    is halfway through typing.

    **Cost, stated rather than assumed.** This runs on the flagship surface's mount, so it
    reads ``available_configs()`` (a directory listing) and NOT ``district_domain_index()``
    (~210 ms — eleven YAMLs). The domain index is built lazily inside the Save handler, an
    event that happens at most once in an install's life. Home's mount cost is unchanged
    by this slice.
    """
    show_prompt = needs_identity_prompt(app_config)
    sd_digits = unmapped_sd_number(app_config, available_configs())
    if not show_prompt and not sd_digits:
        return None

    host = ft.Column(spacing=tokens.space_xl)
    state: dict[str, object] = {
        "stage": _CardStage.ASK if show_prompt else _CardStage.RETIRED,
        "note": "",
        "note_failed": False,
        "matched": (),
        "sd_shown": bool(sd_digits),
        "sd_note": "",
    }

    # NOT autofocus: Home's purpose is the verdict above this card. Stealing the caret for
    # our own question on a surface the admin opened to check their sync would invert
    # exactly the priority this card's placement is meant to express.
    field = ft.TextField(
        label=identity_screen.EMAIL_LABEL,
        # No placeholder — retired with the launch page's (2026-08-05): it presupposed a
        # `.bc.ca` domain. This card keeps its HELPER, which explains what is stored;
        # that is a different job from showing an example address.
        helper=identity_screen.EMAIL_HELPER,
        width=420,
        max_length=IDENTITY_EMAIL_MAX_LEN,
        border_color=tokens.color_border,
    )

    # A PERSISTENT slot for the card's note, so a blur can repaint the note without
    # rebuilding the card around a button that is mid-press (see `_on_blur`).
    note_slot = ft.Column(spacing=0, controls=[])
    shown_note = {"text": ""}

    def _render_note() -> bool:
        """Sync the note slot; ``True`` only if it actually CHANGED (see `_on_blur`)."""
        if str(state["note"]) == shown_note["text"]:
            return False
        shown_note["text"] = str(state["note"])
        note_slot.controls = (
            [_card_note(str(state["note"]), failed=bool(state["note_failed"]))] if state["note"] else []
        )
        return True

    def _guard(work: Callable[[], None]) -> bool:
        """Run one handler's work; ``True`` on success, ``False`` after logging a failure.

        The card-level half of the identity floor. Identity is advisory metadata: a bug in
        it may cost the admin this card, never their view of whether the roster synced.
        """
        try:
            work()
            return True
        except Exception:  # noqa: BLE001 - the floor: an advisory card never breaks Home
            logger.warning(
                "The 'who looks after this sync' card hit a problem; your sync status is unaffected.", exc_info=True
            )
            return False

    # ----------------------------------------------------------------- #
    # The one-time ask                                                   #
    # ----------------------------------------------------------------- #
    def _resolve(validated: str) -> None:
        """Name the district the just-saved address resolves to — or raise the G3 question.

        The index is built HERE (see the cost note above), and the same pure rules the
        launch page and Settings use decide the outcome: one match, several, none, or a
        match that disagrees with the configured district.
        """
        index = district_domain_index()
        match = matched_state(extract_domain(normalize_email(validated)), index)
        names = [friendly_district_name(sis_type) or sis_type for sis_type in match.configs]
        log_outcome = "matched" if match.configs else "no_match"
        identity_screen.log_resolve(log_outcome, len(match.configs), index)

        if matched_excludes_saved(app_config.sis_type, match.configs):
            state["stage"] = _CardStage.MISMATCH
            state["matched"] = tuple(names)
            state["note"] = ""
            return
        state["stage"] = _CardStage.ANSWERED
        # A blank `sis_type` is reachable on a hand-edited profile (`setup_completed: true`
        # with no district), and every "…this sync is set up for X" phrasing would then name
        # a district the install does not run.
        configured = friendly_district_name(app_config.sis_type) or app_config.sis_type.strip()
        if len(match.configs) > 1:
            state["note"] = (
                IDENTITY_CARD_SEVERAL_NOTE.format(district=configured)
                if configured
                else IDENTITY_CARD_SEVERAL_NO_DISTRICT_NOTE
            )
        elif match.configs:
            state["note"] = (
                IDENTITY_CARD_MATCHED_NOTE.format(district=names[0])
                if configured
                else IDENTITY_CARD_MATCHED_NO_DISTRICT_NOTE.format(district=names[0])
            )
        else:
            # The one branch with no district named at all: we recognised nothing, so the
            # address may simply be the wrong one. Say where it can be changed.
            state["note"] = f"Saved. {setup_screen.IDENTITY_NO_MATCH_NOTE} {IDENTITY_CARD_CHANGE_CLAUSE}"

    def _save(_e: ft.ControlEvent | None = None) -> None:
        def work() -> None:
            typed = (field.value or "").strip()
            if not can_continue(typed):
                return
            try:
                validated = validate_identity_email(typed)
            except ValueError as exc:
                # The validator's messages carry the RULE, never the value (personal data).
                identity_screen.log_resolve("invalid", 0, {})
                state["note"] = str(exc)
                state["note_failed"] = True
                return
            if not app_config.identity_save(identity_email=validated):
                # Refused (an UNREADABLE profile) or an OSError: the card STAYS, the form
                # stays, and nothing claims a save that did not happen.
                state["note"] = setup_screen.IDENTITY_REFUSED_NOTE
                state["note_failed"] = True
                return
            state["note_failed"] = False
            _resolve(validated)

        _guard(work)
        _render()

    def _on_change(_e: ft.ControlEvent | None = None) -> None:
        """Only the Save GATE follows the keystrokes — the format error never does."""

        def work() -> None:
            save_btn.disabled = not can_continue(field.value or "")
            page.update()

        _guard(work)

    def _on_blur(_e: ft.ControlEvent | None = None) -> None:
        """Validate on BLUR only. An error after the third keystroke is an accusation.

        **It must never call `_render()`** (v3.10.1). Clicking Save blurs the field first,
        and `_render()` replaces `host.controls` — the card holding the button that is
        mid-press. Flutter delivers a tap to the widget that received the DOWN; that widget
        is gone, so the press is discarded and Save does nothing. The launch page shipped
        the identical bug on its Continue button, and `identity._on_email_blur` carries the
        full account with the measured click-hold table.

        This card was fixed by inspection, not by measurement — the launch page is where the
        bug was reproduced, and this is the same construction (blur → rebuild the card the
        button lives in). Note the ONE behavioural difference that made it less visible: this
        field is deliberately not autofocused, so an admin who never puts the caret in it
        never triggers the blur at all.
        """

        def work() -> None:
            typed = (field.value or "").strip()
            if not can_continue(typed):
                state["note"] = ""  # an empty field is not yet a mistake
                state["note_failed"] = False
                return
            try:
                validate_identity_email(typed)
            except ValueError as exc:
                identity_screen.log_resolve("invalid", 0, {})
                state["note"] = str(exc)
                state["note_failed"] = True
                return
            state["note"] = ""
            state["note_failed"] = False

        _guard(work)
        if _render_note():
            page.update()

    def _dismiss(_e: ft.ControlEvent | None = None) -> None:
        """ "Don't ask again" — permanent, and recoverable only in Settings."""

        def work() -> None:
            if not app_config.identity_save(identity_prompt_dismissed=True):
                state["note"] = setup_screen.IDENTITY_REFUSED_NOTE
                state["note_failed"] = True
                return
            # DISMISSED, not RETIRED: the card stays for this session carrying the one line
            # that says where the ask went, then the predicate hides it from the next mount
            # on. A card that simply vanished would leave the admin no way to learn that.
            state["stage"] = _CardStage.DISMISSED
            state["note"] = ""
            state["note_failed"] = False

        _guard(work)
        _render()

    # ----------------------------------------------------------------- #
    # The G3 mismatch card                                               #
    # ----------------------------------------------------------------- #
    def _keep_saved(_e: ft.ControlEvent | None = None) -> None:
        """Keep the configured district: the card retires, and NOTHING is written.

        The address was stored before the resolution ran, so the identity ask is already
        answered — only the question retires. There is deliberately no write here at all:
        ``identity_prompt_dismissed`` would be dishonest (the ask WAS answered) and
        ``sis_type`` is exactly what this card promises never to touch.
        """

        def work() -> None:
            state["stage"] = _CardStage.RETIRED
            state["note"] = ""

        _guard(work)
        _render()

    def _change_district(_e: ft.ControlEvent | None = None) -> None:
        """Hand the change to Mapping, which owns the stale-schedule honesty of a switch."""
        _guard(lambda: on_navigate("mapping"))

    # ----------------------------------------------------------------- #
    # The durable not-listed card                                        #
    # ----------------------------------------------------------------- #
    def _email_support(_e: ft.ControlEvent | None = None) -> None:
        """The EXISTING Help route, untouched: ``about.support_mailto`` is subject-only.

        Flag 6 — the app never puts the admin's address into anything it sends. The
        subject carries the version and the district DISPLAY name and nothing else.
        """

        def work() -> None:
            mailto = about.support_mailto(SUPPORT_EMAIL, app_version(), friendly_district_name(app_config.sis_type))
            page.launch_url(mailto)

        _guard(work)  # inside the floor: building the URL reads the version + the config too

    def _dismiss_not_listed(_e: ft.ControlEvent | None = None) -> None:
        """Clear the stored district number — GATED on there being one to clear.

        The S4a lesson, applied: an operation with a side effect must be gated on its
        subject existing, so a dismiss that reaches this card by any other route can never
        fire a write for nothing. ``identity_save`` (not ``identity_clear``) — this is one
        advisory field going away, not an erasure, and the settings-recovery copies are
        none of this card's business.
        """

        def work() -> None:
            if not (app_config.identity_sd_number or "").strip():
                state["sd_shown"] = False
                return
            if not app_config.identity_save(identity_sd_number=""):
                state["sd_note"] = setup_screen.IDENTITY_REFUSED_NOTE
                return
            state["sd_shown"] = False
            state["sd_note"] = ""

        _guard(work)
        _render()

    field.on_change = _on_change
    field.on_blur = _on_blur
    field.on_submit = _save

    # OUTLINED, not filled: Home's ONE filled primary belongs to the verdict's fix CTA
    # whenever there is a fault to fix, and an advisory ask may never compete with it.
    save_btn = components.secondary_button(
        IDENTITY_CARD_SAVE_LABEL,
        _save,
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CHECK_ROUNDED,
    )

    def _ask_card() -> ft.Control:
        # `type_section`/W_700 — never larger or heavier than the verdict headline above it.
        # An advisory ask set in the page-title ramp would out-shout the one line the admin
        # opened Home to read, which is the same inversion the placement exists to prevent.
        controls: list[ft.Control] = [
            ft.Text(
                IDENTITY_CARD_HEADLINE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
            ),
            ft.Text(IDENTITY_CARD_DETAIL, size=tokens.type_emphasis, color=tokens.color_muted),
        ]
        if state["stage"] is _CardStage.ASK:
            save_btn.disabled = not can_continue(field.value or "")
            controls += [
                field,
                ft.Row(
                    spacing=tokens.space_lg,
                    controls=[save_btn, components.text_button(IDENTITY_CARD_DISMISS_LABEL, _dismiss)],
                ),
            ]
        # The note rides a PERSISTENT slot rather than being appended conditionally, so a
        # blur can repaint it without rebuilding this card mid-click (see `_on_blur`).
        _render_note()
        controls.append(note_slot)
        return components.card(content=ft.Column(spacing=tokens.space_lg, controls=controls))

    def _mismatch_card() -> ft.Control:
        saved = friendly_district_name(app_config.sis_type) or app_config.sis_type
        return components.card(
            content=ft.Column(
                spacing=tokens.space_lg,
                controls=[
                    ft.Text(
                        mismatch_headline(saved, tuple(state["matched"])),  # type: ignore[arg-type]
                        size=tokens.type_section,
                        weight=ft.FontWeight.W_700,
                        color=tokens.color_text,
                    ),
                    ft.Text(MISMATCH_DETAIL, size=tokens.type_emphasis, color=tokens.color_muted),
                    ft.Row(
                        spacing=tokens.space_lg,
                        controls=[
                            components.secondary_button(MISMATCH_KEEP_LABEL.format(saved=saved), _keep_saved),
                            components.text_button(MISMATCH_CHANGE_LABEL, _change_district),
                        ],
                    ),
                ],
            )
        )

    def _dismissed_card() -> ft.Control:
        return components.card(content=ft.Column(controls=[_card_note(IDENTITY_CARD_DISMISSED_NOTE, muted=True)]))

    def _not_listed_card() -> ft.Control:
        controls: list[ft.Control] = [
            ft.Text(
                not_listed_headline(sd_digits),
                size=tokens.type_section,
                weight=ft.FontWeight.W_700,
                color=tokens.color_text,
            ),
            ft.Text(NOT_LISTED_DETAIL, size=tokens.type_emphasis, color=tokens.color_muted),
            ft.Row(
                spacing=tokens.space_lg,
                controls=[
                    # PHASE 2 SEAM (D-0037-5): the mapping creator replaces this button with
                    # "Build my mapping". The card, its copy and its dismiss stay as they are.
                    components.secondary_button(
                        NOT_LISTED_EMAIL_LABEL, _email_support, icon=ft.Icons.MAIL_OUTLINE_ROUNDED
                    ),
                    components.text_button(NOT_LISTED_DISMISS_LABEL, _dismiss_not_listed),
                ],
            ),
            # The house pattern (`help.py`'s `_copyable_line`): this card's ONLY action is a
            # `mailto:`, which is a silent dead click on a locked-down district server with
            # no mail client registered. The address is therefore also on screen, selectable
            # and copyable, so the admin can act on it by hand.
            _support_address_line(),
        ]
        if state["sd_note"]:
            controls.append(_card_note(str(state["sd_note"]), failed=True))
        return components.card(content=ft.Column(spacing=tokens.space_lg, controls=controls))

    def _support_address_line() -> ft.Control:
        return ft.Row(
            spacing=tokens.space_xs,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(SUPPORT_EMAIL, size=tokens.type_body, selectable=True, color=tokens.color_muted),
                components.copy_button(page, SUPPORT_EMAIL, tooltip=NOT_LISTED_COPY_TOOLTIP),
            ],
        )

    def _render() -> None:
        cards: list[ft.Control] = []
        stage = state["stage"]
        if stage is _CardStage.MISMATCH:
            cards.append(_mismatch_card())
        elif stage is _CardStage.DISMISSED:
            cards.append(_dismissed_card())
        elif stage is not _CardStage.RETIRED:
            cards.append(_ask_card())
        if state["sd_shown"]:
            cards.append(_not_listed_card())
        host.controls = cards
        page.update()

    _render()
    return host


def _identity_cards(
    page: ft.Page,
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
) -> ft.Control | None:
    """The identity cards behind their own floor — a raise here never reaches Home's.

    Home's ``ErrorCard`` floor replaces the WHOLE dashboard, so letting an advisory card's
    bug fall through to it would trade the admin's answer to "did the roster sync?" for a
    question about who looks after it. This floor is therefore not defence-in-depth: it is
    the boundary that keeps the two concerns separable.
    """
    try:
        return _build_identity_cards(page, app_config, on_navigate)
    except Exception:  # noqa: BLE001 - identity never fails closed, and never fails LOUDLY here
        logger.warning(
            "Could not show the 'who looks after this sync' card; your sync status is unaffected.", exc_info=True
        )
        return None


def _restart_identity_controls(
    page: ft.Page,
    app_config: AppConfig,
    on_restart_identity: Callable[[], None] | None,
) -> list[ft.Control]:  # pragma: no cover - Flet view glue
    """The "start over with a different address" link, or nothing at all.

    Returns a LIST so the caller can splat it: with no callback wired (the Setup rail item's
    own mount, and every test that builds Home directly) there is no affordance rather than a
    dead one — a link that cannot go anywhere is worse than no link on the surface whose whole
    job is getting someone unstuck.

    The click order is load-bearing: **clear first, re-mount second**. ``needs_identity`` is
    what decides whether the launch page renders, and it reads the stored address — so
    re-mounting before the write lands would show a page the gate would bounce straight back
    out of. A REFUSED write (unreadable settings) therefore leaves the admin exactly where they
    were, with the calm note Settings already uses for this, rather than in a loop between a
    wizard and a launch page that cannot remember anything.
    """
    if on_restart_identity is None:
        return []

    note = ft.Text("", size=tokens.type_body, color=tokens.color_status_failed, visible=False)

    def _restart(_e: ft.ControlEvent | None = None) -> None:
        try:
            saved = app_config.identity_save(**RESTART_IDENTITY_FIELDS)
        except Exception:  # noqa: BLE001 - identity is advisory; it may never trap the admin mid-wizard
            logger.warning("Could not clear the stored address for a restart.", exc_info=True)
            saved = False
        if not saved:
            note.value = setup_screen.IDENTITY_REFUSED_NOTE
            note.visible = True
            page.update()
            return
        on_restart_identity()

    # ONE control, not two: the note sits UNDER the link rather than beside it, so a refusal
    # never squeezes the welcome band it shares a row with.
    return [
        ft.Column(
            spacing=tokens.space_xs,
            horizontal_alignment=ft.CrossAxisAlignment.END,
            controls=[components.text_button(RESTART_IDENTITY_LABEL, _restart), note],
        )
    ]


def _wizard_host(
    page: ft.Page,
    app_config: AppConfig,
    on_navigate: Callable[[str], None],
    *,
    on_schedule_changed: Callable[[], None] | None,
    on_restart_identity: Callable[[], None] | None = None,
) -> ft.Control:
    """Branch (a): Home IS the setup wizard until the finish line is reached (0038 S6).

    A host, not a copy — ``setup.build_setup`` is mounted here verbatim, so the rail's
    Setup item and this surface run ONE wizard with one resume rule and one set of steps.
    The two differences are deliberate and both live outside the wizard: the welcome band
    above it, and where the finish line hands off (``on_complete`` → a Home re-render into
    the health view, instead of the in-place Settings graduation the rail item keeps).

    ``on_complete`` re-enters Home through the injected navigation rather than swapping
    anything itself: the shell's Home factory re-reads ``AppConfig``, so the fresh load is
    what routes the now-completed install to branch (b)/(c). The wizard fires it only after
    a VERIFIED save, so this re-render can never land back on branch (a) — the bounce that
    would read as "it undid my setup". It ALSO re-probes the Setup badge on the way out (see
    ``_on_setup_complete``).

    The floor is branch-(a)-specific (see the copy block above) and covers the band and the
    wizard TOGETHER, on purpose: this branch has exactly one thing to offer, so a bare band
    over nothing, or a wizard under a line we failed to derive, are both worse than the
    honest card. All-or-nothing is the state to be in when the only surface is the task.
    """

    def _on_setup_complete() -> None:
        """Re-probe the rail badge, THEN re-enter Home.

        The badge probe runs once, at boot, and S6 suppresses it while ``needs_setup`` — so
        an admin who finishes setup in this session has a rail whose badge was deliberately
        silenced and never re-asked. The case that matters is the one this wizard makes
        easy: skip the Schedule step on a machine that still carries a leftover task, and
        the very fault the badge exists to raise stays invisible until a restart.

        Ordered BEFORE the navigation so the probe still fires if the navigation raises —
        NOT because the re-render would race it (``shell._refresh_setup_badge`` does its own
        ``AppConfig.load()`` off-thread at probe time, so the order cannot change what it
        reads). The probe is the advisory half and goes first for the same reason it is
        suppressed: it must never be the thing that stops the admin reaching Home.
        """
        # Advisory, like the register/unregister callers: a stale badge is a blemish, an
        # escaping raise here would skip the navigation AND leave the wizard's finish latch
        # closed, deadening the button for the rest of the mount.
        with contextlib.suppress(Exception):
            if on_schedule_changed is not None:
                on_schedule_changed()
        on_navigate("home")

    try:
        line = welcome_band(app_config, records=read_run_records(), store_created_at=_store_created_at())
        return ft.Column(
            spacing=tokens.space_xl,
            controls=[
                # Calm caption tier, NOT a heading: the wizard's own step header owns the
                # title ramp — and the step COUNT, which is why this line carries none.
                # (The gradient hero this replaces retired with the first-run module; the
                # gradient's one home is the launch page.)
                ft.Row(
                    spacing=tokens.space_md,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(line, size=tokens.type_emphasis, color=tokens.color_muted, expand=True),
                        *_restart_identity_controls(page, app_config, on_restart_identity),
                    ],
                ),
                setup_screen.build_setup(
                    page,
                    on_schedule_changed=on_schedule_changed,
                    on_complete=_on_setup_complete,
                ),
            ],
        )
    except Exception:  # noqa: BLE001 - the first-run floor: never a stack trace, never a false reassurance
        logger.warning("Could not open the setup wizard on Home.", exc_info=True)
        return components.ErrorCard(
            SETUP_UNAVAILABLE_HEADLINE,
            SETUP_UNAVAILABLE_DETAIL,
            action=components.secondary_button(
                SETUP_UNAVAILABLE_HELP_LABEL,
                lambda _e: on_navigate("help"),
                icon=ft.Icons.HELP_OUTLINE_ROUNDED,
            ),
        )


def _store_created_at() -> str | None:
    """The run store's birth stamp, or ``None`` when it was never created."""
    meta = store_meta()
    return meta.get("created_at") if meta else None


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
    # The store's birth stamp feeds the fresh-start empty copy AND the missed-run fresh-start
    # guard (which must also hold when a populated table's newest run is old) — fetched
    # unconditionally; a second tiny SQLite read on mount is the honest price.
    store_created_at = _store_created_at()
    latest_ts = records[0].get("timestamp") if records else None

    # 0038 S4b: built ONCE, here, OUTSIDE ``_render`` — the schedule read-back re-derives
    # the whole control list below, and rebuilding the card there would wipe an address the
    # admin was halfway through typing when the probe returned.
    identity_cards = _identity_cards(page, app_config, on_navigate)

    # 0038 S7: what THIS district's config actually emits — the only honest way to know
    # whether "0 students" would mean "the roster collapsed" (an alarm worth raising) or
    # "this config doesn't emit Students at all" (a lie). Resolved ONCE per mount, outside
    # ``_render``, and memoised per session by ``mapping_catalog``; it reads ONE config, never
    # the eleven-YAML catalog the S4b cost note keeps off this path.
    output_entities = active_output_entities(app_config.sis_type)

    container = ft.Column(spacing=22)

    def _render(schedule_status: ScheduleStatus | None) -> None:
        status = derive_home_status(
            records,
            app_config,
            store_created_at=store_created_at,
            schedule_status=schedule_status,
            output_entities=output_entities,
        )
        # Verdict-first (Direction B): a slim page header, then the health band as the FIRST
        # content element, then the detail (fix / identity cards / quick actions / the
        # clean-schedule confirmation card). ``status.metrics`` is deliberately NOT read — the
        # tile row it fed retired at 0038 S7.
        controls: list[ft.Control] = [
            _header(app_config, on_refresh),
            components.HealthVerdictBanner(status.verdict, headline=status.headline, detail=status.detail),
        ]
        if status.fix is not None:
            controls.append(_fix_button(status.fix, on_navigate))
        # 0038 S4b: the identity cards ride HERE — anchored to the verdict block, never to
        # the tile row that used to sit below, which is why S7's subtraction of those tiles
        # did not move them. They sit immediately after the verdict's own fix CTA rather than
        # between the two: a fault and its fix are one thought, and OUR ask may not be wedged
        # into the middle of it. Below the verdict either way — the verdict is why the admin
        # opened the app; this is what we would like to know.
        if identity_cards is not None:
            controls.append(identity_cards)
        # 0038 S7: the quick-action strip replaces the "Latest roster" tile row. It sits BELOW
        # the identity cards so the verdict / fix / ask block stays one uninterrupted thought;
        # the roster-size number the tiles used to carry now rides the healthy verdict's own
        # detail line (``home_status.size_clause``).
        quick_row = _quick_actions_row(status.fix, on_navigate)
        if quick_row is not None:
            controls.append(quick_row)
        # The clean-schedule row-card surfaces the LIVE read-back only — an attention state is
        # already named above (as the dominant WARNING band + fix button, or — when the latest
        # record is FAILED and outranks it, W3-B — as the secondary clause on the FAILED band),
        # so a reassuring schedule card never shows alongside a schedule fault.
        #
        # FIX 4: it is ALSO suppressed during a seasonal pause. The verdict band above already reads
        # "Paused for the summer"; a LIVE "next run at 3:00 AM — Confirmed" card beneath it would make
        # Home contradict itself on the flagship surface. ``sync_window_paused`` is the SAME pure fact
        # the banner derives (single source), read with ``now=None`` to match the record-based paint.
        if (
            schedule_status is not None
            and schedule_status.state is ScheduleState.LIVE
            and not schedule_status.attention
            and not sync_window_paused(app_config, now=None)
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
    """Fetch the schedule read-back OFF the UI thread and re-render on the loop (where supported).

    Mirrors the SFTP-test marshalling (``page.run_thread`` → ``page.run_task``): the bounded
    PowerShell probe runs on a worker thread; ``on_status`` + ``page.update()`` fire only inside
    the loop-owned coroutine. A probe/thread failure is swallowed — the record-based paint stays.
    Gated on the scheduler's honest read-back capability (W4a T2.3), not ``sys.platform``.
    """
    if not get_scheduler().supports_read_schedule:
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
    on_schedule_changed: Callable[[], None] | None = None,
    on_restart_identity: Callable[[], None] | None = None,
) -> ft.Control:
    """Build the three-way Home surface. ``on_navigate(dest_id)`` is injected by the shell.

    Branch (a) HOSTS the setup wizard (0038 S6); branches (b)/(c) render the health
    dashboard from the pure trust core (with the off-thread schedule read-back injected),
    wrapped in a never-crash ``ErrorCard`` fallback. ``build_home`` keeps owning the branch
    decision — the shell mounts it unconditionally and forwards the callbacks either branch
    might need, rather than branching itself. The trade-off is stated: the shell hands over
    one callback (``on_schedule_changed``) that only branch (a) uses, and in exchange there
    is exactly ONE place that decides which Home an admin gets.

    ``on_refresh`` (injected by the shell) adds a Refresh affordance on the dashboard
    branches for the leaves-it-open Watcher; branch (a) has no status to refresh.
    ``on_schedule_changed`` (also shell-owned) is forwarded to the hosted wizard so
    registering the nightly task from HERE re-probes the rail's Setup badge, exactly as it
    does from the Setup rail item. ``on_restart_identity`` (QA 2026-08-18) is likewise
    branch-(a)-only: it re-mounts the launch page, and the dashboard branches have Settings
    for that.
    """
    if nav.needs_setup(app_config):
        return _wizard_host(
            page,
            app_config,
            on_navigate,
            on_schedule_changed=on_schedule_changed,
            on_restart_identity=on_restart_identity,
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
