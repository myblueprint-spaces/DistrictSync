"""The launch page — "who looks after this sync?" (plan 0038 S4a).

VIEW glue (coverage-omitted): every DECISION lives COUNTED in ``identity_gate``
(``needs_identity`` · ``stored_identity_email`` · ``can_continue`` · ``resolve_domain`` /
``matched_state`` / ``resolve_sd_number``) and at the ``validators.validate_identity_email``
boundary. This file assembles them into ``components.py`` factories and owns no rule.

**This is IDENTIFICATION, not authentication.** There are no accounts, nothing is
unlocked, and every district mapping ships in the executable no matter what is typed here.
The page exists for exactly one reason: the highest-consequence wrong click in this product
is picking the wrong district, because a wrong mapping ships a wrong roster. Knowing which
district an admin belongs to lets the pickers show theirs first — LIVE since S5, via
``mapping_catalog.filtered_catalog`` — and that is the whole of it.

**Deliberately absent — each absence IS the register.** None of the following exists here,
and none should be added:

* **no lockout** — nothing can be locked; there is nothing behind this page to lock.
* **no attempt counter** — a wrong answer has no consequence, so counting is theatre that
  would tell the admin they are being judged.
* **no artificial delay** — resolution is a dictionary lookup over bundled YAML. A
  progress spinner would imply a server, a check, and a verdict, none of which exist.
* **no lock glyph, no shield, no padlock** — a security signifier for a list filter is a
  lie told in iconography.
* **no password field** — there is no secret, so asking for one would invent a threat.
* **no network / "connecting…" state** — nothing leaves this machine. The typed address is
  written to ``config.json`` on this computer and nowhere else.

**No ``ErrorCard`` floor, on purpose.** Every other screen falls back to
``components.ErrorCard`` so a view bug shows a calm surface instead of a trace. That would
be exactly wrong HERE: an ErrorCard on the launch page is a dead end in front of the app,
with no rail and no way past it. The floor for this page is *entering the app* — the shell
wraps the whole identity layer and calls ``_enter_app()`` on any raise (see ``shell.main``'s
boot-order block), and every handler below is guarded the same way. A crash in identity
costs the admin a filtered list, never their sync.

Closing the window here orphans nothing: the shell binds ``page.window.on_event`` and
``page.on_disconnect`` BEFORE this page is built (the PLAT-0 zero-orphan guarantees), so
the title-bar close is a supported exit from this page even though the rail — and its Exit
button — is not rendered yet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import components, tokens
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.identity_gate import (
    MatchOutcome,
    can_continue,
    matched_state,
    resolve_sd_number,
    sd_number_digits,
)
from src.ui_flet.mapping_catalog import district_domain_index
from src.utils.identity import extract_domain, normalize_email
from src.utils.validators import validate_identity_email

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Copy — every user-facing string on this page, in one place.                  #
#                                                                              #
# Three standing constraints, each with a test behind it:                      #
#                                                                              #
# 1. BANNED VOCABULARY (sign in / log in / verify / unlock / authorized /      #
#    account / credentials / access) is absent by construction, and the        #
#    district-domain list is never described as protected / secured /          #
#    anonymous / encrypted. It is a PUBLIC list used to shorten a picker.      #
# 2. NO PROMISE THE BUILD HAS NOT KEPT. Written before S5 scoped the district  #
#    pickers, deliberately worded to stay true on BOTH sides of that change —  #
#    and it did: S5 landed and **not one string below needed an edit.** It     #
#    says what is still exactly true (we recognise the district; you confirm   #
#    it next), which the District step now delivers literally, pre-selected.   #
#    Keep the property: never promise a SURFACE behaviour this page cannot     #
#    see for itself.                                                           #
# 3. NO REGISTRY LANGUAGE. Matching is by a district's public DOMAIN — there   #
#    is no per-person list, so nothing may read as "we have you on file".      #
# --------------------------------------------------------------------------- #
HERO_HEADLINE = "Who looks after this sync?"
HERO_DETAIL = "Tell us their work email and we'll recognise your district."

EMAIL_LABEL = "Work email address"
EMAIL_HINT = "name@yourdistrict.bc.ca"
# Minimisation honesty: the DOMAIN is what we match on, but the WHOLE address is stored
# and rendered — so "we use only the part after the @" would understate what is kept.
#
# **Not rendered on THIS page since 2026-08-04** (owner decision): the launch page asks for
# an address and nothing else — no mechanism, no caveat, no character counter under a field
# that has one thing to say. The constant stays here because this module owns the identity
# COPY, and its live consumer is Home's one-time ask card (``screens/home.py``), where the
# admin is being interrupted mid-product and the explanation is owed. Settings' own
# ``IDENTITY_EXPLAINER`` carries the same fact on the surface that edits and clears it, so
# the storage promise is still made twice in the product — just not in front of the door.
EMAIL_HELPER = (
    "We match on the part after the @ — your district's email domain. "
    "The whole address is saved on this computer and nowhere else."
)

CONTINUE_LABEL = "Continue"
GET_STARTED_LABEL = "Get started"
SKIP_LABEL = "I'm not the person who looks after this sync"

RETRY_LABEL = "That's not my address — try again"
CORRECTION_LABEL = "That's not my district"
SEVERAL_HEADLINE = "Your district has more than one setup."
SEVERAL_DETAIL = "You'll choose the right one in a moment."

NO_MATCH_HEADLINE = "We don't have a district on file for that address yet — no problem."
NO_MATCH_DETAIL = (
    "You can carry on and choose your district in a moment. "
    "If you know your district number, tell us and we'll look for it."
)
SD_LABEL = "District number (optional)"
SD_HINT = "e.g. 48"
NOT_LISTED_LABEL = "My district isn't listed yet"
# NOT "choose the closest district" — picking a district that is not yours is the
# highest-consequence wrong click in this product (a wrong mapping ships a wrong roster).
NOT_LISTED_NOTE_TAIL = (
    "We'll need to build a mapping for your district — Help has our support address, "
    "and we'll ask for a sample extract."
)


class Stage(str, Enum):
    """Which question the page is currently asking. The test seam for the page states."""

    ASK = "ask"
    MATCHED = "matched"
    NO_MATCH = "no_match"


@dataclass
class _PageState:
    """Everything the page remembers between repaints — session-local, never persisted."""

    stage: Stage = Stage.ASK
    error: str = ""  # the inline format error (blur/submit only, never while typing)
    note: str = ""  # the calm inline SD-number note
    configs: tuple[str, ...] = field(default_factory=tuple)
    validated: str = ""  # the address as the boundary validator returned it (as typed)
    sd_digits: str = ""


def matched_headline(district: str) -> str:
    """The matched-one line — a CORRECTABLE PRE-SELECTION, never a finding.

    Deliberately not "We found your district" and emphatically not "You're authorized":
    the page has recognised a public email domain, which is a hint about which list to
    show, not a fact about who this person is. The correction affordance rides beside it
    precisely because the hint can be wrong (a shared district domain, a consultant, a
    board-wide address).

    It also does not over-promise the picker. This line was authored before S5 scoped the
    district lists and deliberately survives it UNCHANGED: "you'll confirm it on the next
    step" was true when that step showed all eleven, and it is true now that the step opens
    on the matched district alone, pre-selected and still correctable (D9 auto-selects a
    single VISIBLE option). "We'll show you X's settings" was avoided because it described a
    surface behaviour this page cannot verify — keep it that way.
    """
    return f"That's {district} — you'll confirm it on the next step."


def sd_resolved_note(district: str) -> str:
    return f"That's {district} — you'll confirm it on the next step."


def sd_unknown_note(digits: str) -> str:
    return f"We don't have a mapping for SD{digits} yet."


def not_listed_note(digits: str) -> str:
    """Honest about what "noted" means TODAY: it is written to this computer's settings.

    It does not promise an email, a ticket, or a build — S4b adds the durable Home card
    that offers the support path, and Phase 2 adds "Build my mapping". Until then the
    admin is pointed at Help, which is true right now.
    """
    subject = f"SD{digits}" if digits else "that"
    return f"Thanks — we've made a note of {subject}. {NOT_LISTED_NOTE_TAIL}"


def log_resolve(outcome: str, matched: int, index: dict[str, tuple[str, ...]]) -> None:
    """The counts-only ops trace. The address, its LOCAL PART and its DOMAIN are all banned.

    ``matched_districts`` already carries the signal a domain would, without carrying a
    value that identifies an organisation — and ``configs_with_domains`` says whether the
    bundled rows are actually present, which is the other thing a support log needs.
    """
    claimed = sum(1 for domains in index.values() if domains)
    logger.info(
        "identity resolve: outcome=%s matched_districts=%d configs_with_domains=%d/%d",
        outcome,
        matched,
        claimed,
        len(index),
    )


def _muted(text: str) -> ft.Text:
    return ft.Text(text, size=tokens.type_body, color=tokens.color_muted)


def _error_line(message: str) -> ft.Control:
    """The inline format error — red text on white (an AA-gated pair), with an icon.

    Never colour-alone, and never a banner: a mistyped address is a small correction, not
    a fault the sync needs to report.
    """
    return ft.Row(
        spacing=tokens.space_sm,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=tokens.type_section, color=tokens.color_status_failed),
            ft.Text(message, size=tokens.type_body, color=tokens.color_status_failed),
        ],
    )


def _hero() -> ft.Control:
    """The one gradient surface in the app (DESIGN_SYSTEM: reallocated here from onboarding)."""
    return components.card(
        content=ft.Column(
            spacing=tokens.space_sm,
            controls=[
                ft.Text(
                    HERO_HEADLINE,
                    size=tokens.type_title,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_on_action,
                ),
                ft.Text(HERO_DETAIL, size=tokens.type_emphasis, color=tokens.color_on_action_muted),
            ],
        ),
        gradient=components.hero_gradient(),
    )


def build_identity(
    page: ft.Page,
    *,
    app_config: AppConfig,
    on_enter: Callable[[], None],
    config_dir: Path | None = None,
) -> ft.Control:
    """Build the launch page. ``on_enter`` opens the app — it is the ONLY way out, and
    every path reaches it.

    ``app_config`` is the instance the answer is persisted onto (through the
    ``identity_save`` choke point, which re-checks ``settings_unreadable()`` at WRITE time
    and swallows a refusal). ``on_enter`` is the shell's idempotent ``_enter_app``: it
    re-reads ``AppConfig`` fresh, so the app body sees the just-persisted identity from the
    FIRST paint. **Persist first, then enter — but a persist failure still enters**, because
    advisory metadata may never trap an admin in front of their own sync.
    """
    index = district_domain_index(config_dir=config_dir)
    st = _PageState()

    # STRETCH so the hero and the form card are the SAME width — the frame
    # (``shell._gate_frame``) owns that width and centres the pair in the window. Without it
    # each card hugs its own content and the two sit ragged against the left edge.
    body = ft.Column(spacing=tokens.space_xl, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    email_field = ft.TextField(
        label=EMAIL_LABEL,
        hint_text=EMAIL_HINT,
        # No helper, no counter, no length cap ON THE WIDGET (2026-08-04): see EMAIL_HELPER
        # for the helper. `max_length` went with it because on 0.85.3 a TextField that has
        # one reserves the sub-text row for its "0/254" counter, and a blank `counter=` does
        # not collapse it — leaving a dead 24px band under the field on a page whose whole
        # job is one input. The LENGTH RULE IS UNCHANGED: `validate_identity_email` is the
        # boundary (CLAUDE.md: validate at boundaries) and still refuses anything over
        # `IDENTITY_EMAIL_MAX_LEN` on blur/submit, with a message that never echoes the
        # value. The widget cap was a convenience that silently truncated a paste; the
        # validator says so out loud instead.
        autofocus=True,
        border_color=tokens.color_border,
    )
    sd_field = ft.TextField(
        label=SD_LABEL,
        hint_text=SD_HINT,
        # No explicit width — the card's column STRETCHES it to match the email field above.
        # `max_length` stays here (unlike the email field): `sd_number_digits` takes the first
        # digit RUN with no upper bound, and that value is persisted, so this is a real cap on
        # what a paste can write to `config.json`, not a typing convenience.
        max_length=16,
        counter=ft.Text(""),
        border_color=tokens.color_border,
    )

    def _paint() -> None:
        body.controls = [_hero(), _card()]
        page.update()

    def _guard(work: Callable[[], None]) -> bool:
        """Run one handler's work; ``True`` on success, ``False`` after logging a failure.

        The identity-layer floor at the handler level (the shell holds the other half,
        around the predicate and the page build). **``on_enter`` is deliberately NOT called
        from in here, and never runs inside this ``try``.** Wrapping it would swallow a
        failure in the APP BODY — the one failure that must stay loud — and leave the admin
        on a launch page whose buttons quietly do nothing: a trap, which is precisely what
        this whole design exists to make unrepresentable. Callers below decide what a
        ``False`` means, and every one of them means "enter anyway".
        """
        try:
            work()
            return True
        except Exception:  # noqa: BLE001 - the floor: identity never fails closed
            logger.warning(
                "The launch page hit a problem; opening DistrictSync with the full district list.", exc_info=True
            )
            return False

    # ----------------------------------------------------------------- #
    # Actions                                                            #
    # ----------------------------------------------------------------- #
    def _validate_now() -> str | None:
        """Validate the typed address; record the error and return ``None`` on refusal.

        Called on BLUR and on SUBMIT only — never on change. An error that appears after
        the third keystroke of a correct address is an accusation, not help.
        """
        try:
            validated = validate_identity_email(email_field.value or "")
        except ValueError as exc:
            st.error = str(exc)  # the validator's messages never echo the value
            st.stage = Stage.ASK
            log_resolve("invalid", 0, index)
            return None
        st.error = ""
        return validated

    def _on_email_change(_e: ft.ControlEvent | None = None) -> None:
        """Only the Continue GATE follows the keystrokes — the error never does."""

        def work() -> None:
            continue_btn.disabled = not can_continue(email_field.value or "")
            page.update()

        _guard(work)

    def _on_email_blur(_e: ft.ControlEvent | None = None) -> None:
        def work() -> None:
            if not can_continue(email_field.value or ""):
                st.error = ""  # an empty field is not yet a mistake
                _paint()
                return
            _validate_now()
            _paint()

        _guard(work)

    def _resolve(_e: ft.ControlEvent | None = None) -> None:
        """ASK → the result state. Local, instant, and never a network call.

        A failure here means we could not work out which districts to offer — so the admin
        goes IN, with all of them. That is the fail-open direction and the only safe one.
        """

        def work() -> None:
            validated = _validate_now()
            if validated is None:
                _paint()
                return
            st.validated = validated
            match = matched_state(extract_domain(normalize_email(validated)), index)
            st.configs = match.configs
            if match.outcome is MatchOutcome.NO_MATCH:
                st.stage = Stage.NO_MATCH
                log_resolve("no_match", 0, index)
            else:
                st.stage = Stage.MATCHED
                log_resolve("matched", len(match.configs), index)
            _paint()

        if not _guard(work):
            on_enter()

    def _read_sd() -> str:
        """Read the district number out of the field (no painting) — the shared reduction."""
        st.sd_digits = sd_number_digits(sd_field.value or "")
        return st.sd_digits

    def _check_sd(_e: ft.ControlEvent | None = None) -> None:
        """Resolve the typed district number (on blur/submit) into a calm inline note."""

        def work() -> None:
            digits = _read_sd()
            if not digits:
                st.note = ""
            elif hits := resolve_sd_number(digits, index):
                st.note = sd_resolved_note(friendly_district_name(hits[0]) or hits[0])
            else:
                st.note = sd_unknown_note(digits)
            _paint()

        _guard(work)

    def _not_listed(_e: ft.ControlEvent | None = None) -> None:
        def work() -> None:
            st.note = not_listed_note(_read_sd())
            _paint()

        _guard(work)

    def _persist_and_enter(_e: ft.ControlEvent | None = None) -> None:
        """Persist best-effort, THEN enter — a refused or failed save still enters.

        ``identity_save`` is the choke point: it re-checks ``settings_unreadable()`` on the
        instance it is about to write, validates key AND value, refuses any non-identity
        key (so this can never rewrite ``sis_type``), and swallows a refusal into a bool.
        """

        def work() -> None:
            if st.stage is Stage.NO_MATCH:
                _read_sd()  # a number typed but never blurred must still be honoured
            updates: dict[str, object] = {"identity_email": st.validated}
            if st.sd_digits:
                updates["identity_sd_number"] = st.sd_digits
            app_config.identity_save(**updates)

        _guard(work)  # a failed persist is logged and ignored...
        on_enter()  # ...and we enter either way, OUTSIDE the guard (see `_guard`).

    def _wrong_district(_e: ft.ControlEvent | None = None) -> None:
        """ "That's not my district" — enter with the FULL list, storing NOTHING.

        This is the affordance that makes the matched state a *correctable* pre-selection
        rather than a verdict, so it must not persist the very domain the admin has just
        told us is wrong. That address is the ONE input to the district-list scoping: keeping
        it would turn a correction into a durable mis-scope (every picker would open on the
        rejected district), and the correction would have to be made twice — here and again
        in Settings.

        So it behaves like the escape: enter unfiltered, store nothing, ask again next
        launch. The admin who wants to record a DIFFERENT address can retype it (the
        "try again" affordance) or set it later in Settings.
        """
        _guard(lambda: log_resolve("unscoped", 0, index))
        on_enter()

    def _try_again(_e: ft.ControlEvent | None = None) -> None:
        """Back to the field with the typed value intact — a typo's cheapest fix.

        Without it, the only way out of a wrong resolution is to skip the page entirely
        and go hunting in Settings; a mistyped domain (``sd84`` for ``sd48``) is by far the
        likeliest way to land in the wrong state, and re-typing is the obvious remedy.
        """

        def work() -> None:
            st.stage = Stage.ASK
            st.error = ""
            st.note = ""
            st.configs = ()
            _paint()

        _guard(work)

    def _skip(_e: ft.ControlEvent | None = None) -> None:
        """The escape (flag 1) — enter with the FULL list and store NOTHING.

        Without it, the person at the console who is not the admin is trapped in front of a
        question only someone else can answer. Nothing is written, so the gate simply asks
        again next launch.
        """

        _guard(lambda: log_resolve("unscoped", 0, index))
        on_enter()

    email_field.on_change = _on_email_change
    email_field.on_blur = _on_email_blur
    email_field.on_submit = _resolve
    sd_field.on_blur = _check_sd
    sd_field.on_submit = _check_sd

    # The ONE filled primary of the ASK state, built once so the keystroke gate can flip
    # its `disabled` in place without a repaint (a repaint would steal focus mid-typing).
    continue_btn = components.primary_button(
        CONTINUE_LABEL,
        _resolve,
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.ARROW_FORWARD_ROUNDED,
    )

    # ----------------------------------------------------------------- #
    # The three page states                                              #
    # ----------------------------------------------------------------- #
    def _ask_controls() -> list[ft.Control]:
        controls: list[ft.Control] = [email_field]
        if st.error:
            controls.append(_error_line(st.error))
        continue_btn.disabled = not can_continue(email_field.value or "")
        controls.append(continue_btn)
        return controls

    def _matched_controls() -> list[ft.Control]:
        configs = st.configs
        names = [friendly_district_name(sis_type) or sis_type for sis_type in configs]
        if len(configs) == 1:
            controls: list[ft.Control] = [
                # In a Row so the pill keeps its intrinsic width — the card's column STRETCHES
                # its children, and a full-width "pill" is a banner.
                ft.Row(controls=[components.district_chip(names[0])]),
                ft.Text(
                    matched_headline(names[0]),
                    size=tokens.type_section,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_text,
                ),
                # Enters WITHOUT storing — a correction may never persist the rejected
                # domain (see `_wrong_district`).
                components.text_button(CORRECTION_LABEL, _wrong_district),
            ]
        else:
            controls = [
                ft.Text(
                    SEVERAL_HEADLINE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
                ),
                _muted(SEVERAL_DETAIL),
                ft.Row(spacing=tokens.space_sm, wrap=True, controls=[components.district_chip(n) for n in names]),
                components.text_button(CORRECTION_LABEL, _wrong_district),
            ]
        controls.append(
            components.primary_button(GET_STARTED_LABEL, _persist_and_enter, icon=ft.Icons.ARROW_FORWARD_ROUNDED)
        )
        controls.append(components.text_button(RETRY_LABEL, _try_again))
        return controls

    def _no_match_controls() -> list[ft.Control]:
        controls: list[ft.Control] = [
            ft.Text(NO_MATCH_HEADLINE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text),
            _muted(NO_MATCH_DETAIL),
            sd_field,
        ]
        if st.note:
            controls.append(_muted(st.note))
        controls.append(components.text_button(NOT_LISTED_LABEL, _not_listed))
        controls.append(
            components.primary_button(GET_STARTED_LABEL, _persist_and_enter, icon=ft.Icons.ARROW_FORWARD_ROUNDED)
        )
        controls.append(components.text_button(RETRY_LABEL, _try_again))
        return controls

    def _card() -> ft.Control:
        if st.stage is Stage.MATCHED:
            controls = _matched_controls()
        elif st.stage is Stage.NO_MATCH:
            controls = _no_match_controls()
        else:
            controls = _ask_controls()
        # The escape rides EVERY state — there is no page from which the person at the
        # console cannot leave without answering.
        controls.append(components.text_button(SKIP_LABEL, _skip))
        return components.card(
            content=ft.Column(
                spacing=tokens.space_lg,
                # STRETCH: the field and the one filled primary run the full width of the
                # card, which is what makes this read as a single centred form rather than a
                # left-hugging stack of differently-sized controls.
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=controls,
            )
        )

    body.controls = [_hero(), _card()]
    return body
