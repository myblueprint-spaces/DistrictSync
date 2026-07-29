"""The launch page — "who looks after this sync?" (plan 0038 S4a).

VIEW glue (coverage-omitted): every DECISION lives COUNTED in ``identity_gate``
(``needs_identity`` · ``stored_identity_email`` · ``can_continue`` · ``resolve_domain`` /
``matched_state`` / ``resolve_sd_number``) and at the ``validators.validate_identity_email``
boundary. This file assembles them into ``components.py`` factories and owns no rule.

**This is IDENTIFICATION, not authentication.** There are no accounts, nothing is
unlocked, and every district mapping ships in the executable no matter what is typed here.
The page exists for exactly one reason: the highest-consequence wrong click in this product
is picking the wrong district, because a wrong mapping ships a wrong roster. Knowing which
district an admin belongs to lets the pickers show theirs first (S5), and that is the whole
of it.

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
from src.utils.validators import IDENTITY_EMAIL_MAX_LEN, validate_identity_email

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Copy — every user-facing string on this page, in one place.                  #
# The banned vocabulary (sign in / log in / verify / unlock / authorized /     #
# account / credentials / access) is absent BY CONSTRUCTION here, and a test   #
# sweeps the rendered tree of every state to keep it that way. The contact     #
# list is likewise never described as protected / secured / anonymous /        #
# encrypted — it is a list of public district domains used to shorten a list.  #
# --------------------------------------------------------------------------- #
HERO_HEADLINE = "Who looks after this sync?"
HERO_DETAIL = "Tell us their work email and we'll show you your district's settings."

EMAIL_LABEL = "Work email address"
EMAIL_HINT = "name@yourdistrict.bc.ca"
EMAIL_HELPER = "We use only the part after the @ — your district's email domain. It stays on this computer."

CONTINUE_LABEL = "Continue"
GET_STARTED_LABEL = "Get started"
SKIP_LABEL = "I'm not the person who set this up"

CORRECTION_LABEL = "Not your district? Choose a different one."
SEVERAL_HEADLINE = "Which district are you setting up?"
SEVERAL_DETAIL = "That email matches more than one setup — you'll choose the right one in a moment."

NO_MATCH_HEADLINE = "We don't have that address on file yet — no problem."
NO_MATCH_DETAIL = (
    "You can carry on and choose your district in a moment. "
    "If you know your district number, tell us and we'll look for it."
)
SD_LABEL = "District number (optional)"
SD_HINT = "e.g. 48"
NOT_LISTED_LABEL = "My district isn't listed yet"
NOT_LISTED_NOTE_TAIL = "Choose the closest district for now — Help has our support address if none of them fit."


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
    """
    return f"We'll show you {district}'s settings."


def sd_resolved_note(district: str) -> str:
    return f"That's {district}. We'll show you its settings."


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

    body = ft.Column(spacing=tokens.space_xl)

    email_field = ft.TextField(
        label=EMAIL_LABEL,
        hint_text=EMAIL_HINT,
        helper=EMAIL_HELPER,
        width=420,
        autofocus=True,
        max_length=IDENTITY_EMAIL_MAX_LEN,
        border_color=tokens.color_border,
    )
    sd_field = ft.TextField(
        label=SD_LABEL,
        hint_text=SD_HINT,
        width=220,
        max_length=16,
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

    def _skip(_e: ft.ControlEvent | None = None) -> None:
        """The escape (flag 1) — enter with the FULL list and store NOTHING.

        Without it, the person at the console who is not the admin is trapped in front of a
        question only someone else can answer. Nothing is written, so the gate simply asks
        again next launch.
        """

        _guard(lambda: log_resolve("show_all", 0, index))
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
                components.district_chip(names[0]),
                ft.Text(
                    matched_headline(names[0]),
                    size=tokens.type_section,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_text,
                ),
                components.text_button(CORRECTION_LABEL, _persist_and_enter),
            ]
        else:
            controls = [
                ft.Text(
                    SEVERAL_HEADLINE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
                ),
                _muted(SEVERAL_DETAIL),
                ft.Row(spacing=tokens.space_sm, wrap=True, controls=[components.district_chip(n) for n in names]),
            ]
        controls.append(
            components.primary_button(GET_STARTED_LABEL, _persist_and_enter, icon=ft.Icons.ARROW_FORWARD_ROUNDED)
        )
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
        return components.card(content=ft.Column(spacing=tokens.space_lg, controls=controls))

    body.controls = [_hero(), _card()]
    return body
