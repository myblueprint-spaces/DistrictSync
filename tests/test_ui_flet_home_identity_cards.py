"""The three Home identity cards (plan 0038 S4b).

VIEW-glue tests for coverage-omitted glue, so they assert what coverage cannot: that every
card STATE constructs, that the copy stays inside the register, and — above all — that an
ADVISORY card can never touch the thing Home exists to report on.

Three properties carry the whole slice, and each is asserted at the WRITE rather than at
the screen, because a file-level check also passes when the mechanism silently stopped
running:

* **no path writes ``sis_type``.** Every card interaction is driven with the
  ``identity_save`` choke point spied, and the spy records what was asked for — not what
  survived. (``identity_save`` refuses a non-identity key loudly; this pins that the cards
  never even ask.)
* **a refused write claims nothing.** The honest "couldn't save" note is the S4a constant,
  and the card stays up with its form so the admin can try again.
* **a bug in a card costs the card, never the verdict.** Home's own ``ErrorCard`` floor
  replaces the WHOLE dashboard; the cards carry their own floor above it.

``probe_schedule`` is stubbed on every Home build (the S1-era flake note: the real probe
spawns PowerShell), and ``components.ErrorCard`` is spied as a MODULE ATTRIBUTE — the
contract every render smoke in this repo relies on.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.config.loader import available_configs as real_available_configs
from src.ui_flet import components, tokens
from src.ui_flet.identity_gate import unmapped_sd_number
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.screens import home
from src.ui_flet.screens.home import build_home

# Single-sourced from the S4a sweep — a second hand-typed banned-word list is a list that
# drifts, and the whole identification-not-authentication promise rests on it.
from tests.test_ui_flet_identity_page import (
    INDEX,
    _all_text,
    _assert_no_banned_vocabulary,
    _button,
    _error_texts,
    _field,
    _iter_controls,
)

# The ONLY field names any card may ever ask to write (mirrors the dataclass-derived
# `_IDENTITY_FIELD_NAMES` the choke point enforces).
IDENTITY_FIELDS = ("identity_email", "identity_prompt_dismissed", "identity_sd_number")

# Read at import so a parametrize decorator can name it (decorators run at collection).
MISMATCH_CHANGE = home.MISMATCH_CHANGE_LABEL


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def _quiet_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic dashboard: no PowerShell probe, no run store, no bundled-YAML parse."""
    benign = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
    monkeypatch.setattr("src.ui_flet.schedule_probe.probe_schedule", lambda *a, **k: benign)
    monkeypatch.setattr(home, "read_run_records", lambda: [])
    monkeypatch.setattr(home, "store_meta", lambda: None)
    monkeypatch.setattr(home, "district_domain_index", lambda **_kw: dict(INDEX))
    monkeypatch.setattr(home, "available_configs", lambda *_a, **_kw: sorted(INDEX))


def _cfg(**over: object) -> AppConfig:
    """A configured install — Home's dashboard branch, the only branch the cards ride."""
    values: dict[str, object] = {
        "input_dir": "/in",
        "output_dir": "/out",
        "sis_type": "sd48myedbc",
        "setup_completed": True,
        "load_state": ConfigLoadState.LOADED,
    }
    values.update(over)
    return AppConfig(**values)  # type: ignore[arg-type]


def _home(
    page: MagicMock, cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, hops: list[str] | None = None
) -> ft.Control:
    """Build Home under the ErrorCard spy; fail if it fell to its floor."""
    real_errorcard = components.ErrorCard
    floor: dict[str, object] = {"obj": None}

    def spy(*args: object, **kwargs: object) -> ft.Control:
        obj = real_errorcard(*args, **kwargs)  # type: ignore[arg-type]
        floor["obj"] = obj
        return obj

    monkeypatch.setattr(components, "ErrorCard", spy)
    view = build_home(page, app_config=cfg, on_navigate=(hops.append if hops is not None else (lambda _d: None)))
    assert view is not floor["obj"], "Home fell to its ErrorCard floor — a masked render bug"
    return view


def _answer(view: ft.Control, address: str) -> None:
    _field(view, home.identity_screen.EMAIL_LABEL).value = address
    _button(view, home.IDENTITY_CARD_SAVE_LABEL).on_click(None)


def _card_host(view: ft.Control) -> ft.Control:
    """The identity-card block — the dashboard's only direct ``Column`` child."""
    hosts = [c for c in view.controls if isinstance(c, ft.Column)]
    assert len(hosts) == 1, f"expected exactly one identity-card host among Home's children, found {len(hosts)}"
    return hosts[0]


def _has_button(view: ft.Control, label: str) -> bool:
    """Is a button with this LABEL present? (``"Save" in text`` also matches "Saved.")"""
    return any(
        isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and c.content == label
        for c in _iter_controls(view)
    )


def _press_every_card_button(view: ft.Control) -> list[str]:
    """Press every button the cards currently offer; returns the labels pressed."""
    buttons = [
        c
        for c in _iter_controls(_card_host(view))
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))
    ]
    for button in buttons:
        button.on_click(None)
    return [button.content for button in buttons]


def _spy_saves(monkeypatch: pytest.MonkeyPatch, *, result: bool = True) -> list[dict]:
    """Record every ``identity_save`` CALL — what was asked for, not what survived."""
    writes: list[dict] = []

    def _save(self: AppConfig, **kw: object) -> bool:  # noqa: ANN001
        writes.append(kw)
        if result:
            for name, value in kw.items():
                setattr(self, name, value)
        return result

    monkeypatch.setattr(AppConfig, "identity_save", _save)
    return writes


# --------------------------------------------------------------------------- #
# 1. When the card appears at all                                              #
# --------------------------------------------------------------------------- #
class TestWhenTheCardAppears:
    def test_a_configured_install_with_no_identity_is_asked_once(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE in _all_text(view)
        assert _field(view, home.identity_screen.EMAIL_LABEL) is not None

    def test_the_ask_sits_BELOW_the_verdict(self, page, monkeypatch) -> None:
        """Verdict-first: the admin opened Home to learn whether the roster synced.

        Asserted as an ORDER over the dashboard's own children — indexed by the verdict's
        own derived headline, not by control type — so a future re-order that floats our
        ask above the band is a red rather than a design review someone has to notice.
        """
        cfg = _cfg()
        headline = home.derive_home_status([], cfg, store_created_at=None, schedule_status=None).headline
        view = _home(page, cfg, monkeypatch)
        children = list(view.controls)

        banner = next(i for i, c in enumerate(children) if headline in _all_text(c))
        card = next(i for i, c in enumerate(children) if home.IDENTITY_CARD_HEADLINE in _all_text(c))

        assert card > banner, "the identity ask rendered above the verdict block"

    def test_the_ask_sits_BELOW_the_verdicts_fix_CTA_in_the_FAULT_state(self, page, monkeypatch) -> None:
        """The placement deviation, pinned where it actually matters.

        The spec's literal wording puts the cards immediately after the banner; they attach
        one line later, AFTER the verdict's fix button, because a fault and its fix are one
        thought and an advisory ask may not be wedged into the middle of it. That is only
        observable in a FAULT state — the healthy state has no fix CTA at all, so the
        order test above would pass under either placement.
        """
        failed = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": "failed",
            "duration_s": 2.0,
            "sftp_attempted": False,
            "sftp_ok": False,
            "error": "",
            "error_category": "input_missing",
            "anomalies": [],
            "data_errors": {},
        }
        monkeypatch.setattr(home, "read_run_records", lambda: [failed])
        cfg = _cfg()
        status = home.derive_home_status([failed], cfg, store_created_at=None, schedule_status=None)
        assert status.fix is not None, "the fixture did not produce a fix CTA; the order below is vacuous"

        view = _home(page, cfg, monkeypatch)
        children = list(view.controls)
        banner = next(i for i, c in enumerate(children) if status.headline in _all_text(c))
        fix = next(i for i, c in enumerate(children) if _has_button(c, status.fix.label))
        card = next(i for i, c in enumerate(children) if home.IDENTITY_CARD_HEADLINE in _all_text(c))

        assert banner < fix < card, f"expected banner < fix CTA < card, got {banner} / {fix} / {card}"

    def test_no_card_headline_outshouts_the_verdict(self, page, monkeypatch) -> None:
        """Type hierarchy IS the verdict-first promise, restated in points.

        A card set in the page-title ramp (``type_title``/W_800) reads as the most important
        thing on Home, which is exactly what the placement exists to prevent. Nothing in the
        card block may exceed the verdict headline's ``type_section``/W_700.
        """
        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        texts = [c for c in _iter_controls(_card_host(view)) if isinstance(c, ft.Text)]
        sized = [c for c in texts if isinstance(getattr(c, "size", None), (int, float))]
        assert sized, "no sized text found in the cards; the bound below is vacuous"
        assert max(c.size for c in sized) <= tokens.type_section
        assert not [c for c in texts if c.weight == ft.FontWeight.W_800]

    def test_a_stored_identity_retires_the_ask(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(identity_email="admin@sd48.bc.ca"), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE not in _all_text(view)

    def test_a_dismissed_prompt_never_comes_back(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(identity_prompt_dismissed=True), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE not in _all_text(view)

    def test_an_unreadable_profile_is_never_asked(self, page, monkeypatch) -> None:
        """G2 — we could not persist the answer, so we must not ask for it."""
        view = _home(page, _cfg(load_state=ConfigLoadState.UNREADABLE), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE not in _all_text(view)

    def test_an_unconfigured_install_gets_the_hosted_WIZARD_not_a_card(self, page, monkeypatch) -> None:
        """The launch page owns that population (S4a); Home must not ask a second time.

        Branch (a) is the hosted setup wizard since S6 — asserted positively, because "no
        card" is equally satisfied by a Home that rendered nothing at all.
        """
        view = _home(page, _cfg(setup_completed=False, input_dir="", output_dir="", sis_type=""), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE not in _all_text(view)
        assert "Choose your district" in _all_text(view), "branch (a) did not host the wizard"


# --------------------------------------------------------------------------- #
# 2. The ask state                                                             #
# --------------------------------------------------------------------------- #
class TestTheAskState:
    def test_save_is_gated_while_blank_and_follows_the_keystrokes(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(), monkeypatch)
        save = _button(view, home.IDENTITY_CARD_SAVE_LABEL)
        assert save.disabled is True

        field = _field(view, home.identity_screen.EMAIL_LABEL)
        field.value = "a"
        field.on_change(None)

        assert save.disabled is False

    def test_the_card_never_steals_the_caret_from_the_verdict(self, page, monkeypatch) -> None:
        """Home's purpose is the band above this card; autofocus here would invert that."""
        view = _home(page, _cfg(), monkeypatch)

        assert _field(view, home.identity_screen.EMAIL_LABEL).autofocus is not True

    def test_the_card_adds_no_second_filled_primary(self, page, monkeypatch) -> None:
        """Home's ONE filled primary belongs to the verdict's fix CTA. Counted BOTH ways,
        so "no filled button" can never pass because the card silently stopped rendering."""
        with_card = _home(page, _cfg(), monkeypatch)
        without = _home(page, _cfg(identity_prompt_dismissed=True), monkeypatch)

        assert home.IDENTITY_CARD_HEADLINE in _all_text(with_card), "the card is missing; the count below is vacuous"
        filled_with = [c for c in _iter_controls(with_card) if isinstance(c, ft.FilledButton)]
        filled_without = [c for c in _iter_controls(without) if isinstance(c, ft.FilledButton)]
        assert len(filled_with) == len(filled_without), "the identity card added a filled primary"

    def test_a_format_error_is_NOT_shown_while_typing(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(), monkeypatch)
        field = _field(view, home.identity_screen.EMAIL_LABEL)

        field.value = "adm"
        field.on_change(None)

        assert not _error_texts(view)

    def test_a_format_error_IS_shown_on_blur_and_never_echoes_the_value(self, page, monkeypatch) -> None:
        """The positive twin — the mechanism really does fire, and the message carries the
        RULE, never the value (a caller that logged it would leak personal data)."""
        view = _home(page, _cfg(), monkeypatch)
        field = _field(view, home.identity_screen.EMAIL_LABEL)
        field.value = "notanemail"

        field.on_blur(None)

        error = _error_texts(view)
        assert "exactly one @" in error
        assert "notanemail" not in error

    def test_a_blank_field_blurred_is_not_yet_a_mistake(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(), monkeypatch)
        field = _field(view, home.identity_screen.EMAIL_LABEL)
        field.value = "   "

        field.on_blur(None)

        assert not _error_texts(view)

    def test_an_invalid_answer_stores_NOTHING(self, page, monkeypatch) -> None:
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(), monkeypatch)

        _answer(view, "wildly wrong@@thing")

        assert writes == [], "an unvalidated value reached the settings file"
        assert _error_texts(view), "no inline error was painted; the absence above is vacuous"


# --------------------------------------------------------------------------- #
# 3. The four answers                                                          #
# --------------------------------------------------------------------------- #
class TestTheAnswers:
    def test_a_matching_address_is_stored_and_names_the_district(self, page, monkeypatch) -> None:
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd48myedbc"), monkeypatch)

        _answer(view, "Admin.Person@SD48.bc.ca")

        assert writes == [{"identity_email": "Admin.Person@SD48.bc.ca"}], "stored AS TYPED, never normalised"
        card = _all_text(_card_host(view))
        assert "Sea to Sky" in card
        assert "the district this sync is set up for" in card
        # ...and the form retires: the question has been answered.
        assert not _has_button(view, home.IDENTITY_CARD_SAVE_LABEL)

    @pytest.mark.parametrize(
        ("sis_type", "address", "state"),
        [
            ("sd48myedbc", "admin@sd48.bc.ca", "matched-one"),
            ("sd51myedbc", "admin@sd51.bc.ca", "matched-several"),
        ],
    )
    def test_no_answered_note_promises_an_ACTION_this_card_cannot_cause(
        self, page, monkeypatch, sis_type: str, address: str, state: str
    ) -> None:
        """Two launch-page/Settings habits that are both wrong on a CONFIGURED Home.

        The launch page's ``matched_headline`` says "you'll confirm it on the next step" —
        there is no next step here. Settings' several-note says "you'll choose the right one
        under Folders & district" — nothing is pending to choose, and that instruction sends
        the admin into the district picker, which is the ONE action these cards promise
        never to cause. (Not hypothetical: SD51's two configs share a domain, so every SD51
        admin lands on the several branch.)
        """
        _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type=sis_type), monkeypatch)

        _answer(view, address)

        note = _all_text(_card_host(view)).lower()
        for phrase in ("you'll", "next step", "choose"):
            assert phrase not in note, f"the {state} note promises an action ({phrase!r}): {note}"

    def test_several_matches_state_the_fact_and_name_the_configured_district(self, page, monkeypatch) -> None:
        """SD51 + its attendance tier share one domain — the LIVE matched-several shape."""
        _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd51myedbc"), monkeypatch)

        _answer(view, "admin@sd51.bc.ca")

        assert home.IDENTITY_CARD_SEVERAL_NOTE.format(district="SD51 - Boundary School District") in _all_text(
            _card_host(view)
        )

    @pytest.mark.parametrize("address", ["admin@sd48.bc.ca", "admin@sd51.bc.ca"])
    def test_a_blank_configured_district_is_never_named_as_one(self, page, monkeypatch, address: str) -> None:
        """A hand-edited profile can carry ``setup_completed: true`` with NO district.

        Every "…this sync is set up for X" phrasing would then name a district the install
        does not run — so both matched branches drop the clause instead.
        """
        _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type=""), monkeypatch)

        _answer(view, address)

        note = _all_text(_card_host(view))
        assert "set up for" not in note, f"named a district for an install that has none: {note}"
        assert "Saved." in note, "the save was still confirmed"

    def test_no_match_is_calm_and_STORES_ANYWAY(self, page, monkeypatch) -> None:
        """The address is the support identity regardless of whether we recognise it."""
        from src.ui_flet.screens import setup as setup_screen

        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(), monkeypatch)

        _answer(view, "admin@some-district.example.com")

        assert writes == [{"identity_email": "admin@some-district.example.com"}]
        text = _all_text(view)
        assert setup_screen.IDENTITY_NO_MATCH_NOTE in text
        assert not _error_texts(view), "a no-match is not an error and is never painted as one"
        # The branch most likely to be a typo'd address is the one branch that names NO
        # district — so it is the one that most needs a stated way back.
        assert home.IDENTITY_CARD_CHANGE_CLAUSE in text
        assert "Settings" in text

    def test_a_refused_save_leaves_the_card_UP_and_claims_nothing(self, page, monkeypatch) -> None:
        from src.ui_flet.screens import setup as setup_screen

        _spy_saves(monkeypatch, result=False)
        view = _home(page, _cfg(), monkeypatch)

        _answer(view, "admin@sd48.bc.ca")

        # Scoped to the CARD: Home's own header chip names the configured district, and a
        # whole-tree assertion would be asserting that away rather than the card's honesty.
        card = _all_text(_card_host(view))
        assert setup_screen.IDENTITY_REFUSED_NOTE in card
        assert "Saved" not in card, "a refused write claimed a save"
        assert "Sea to Sky" not in card, "a refused write named a district it never stored"
        assert _has_button(view, home.IDENTITY_CARD_SAVE_LABEL), "the admin cannot try again"

    def test_a_RAISING_save_is_caught_and_the_card_survives(self, page, monkeypatch) -> None:
        """``identity_save`` swallows its own failures, but the floor may not depend on that."""

        def _boom(self: AppConfig, **_kw: object) -> bool:  # noqa: ANN001
            raise OSError("injected settings failure")

        monkeypatch.setattr(AppConfig, "identity_save", _boom)
        view = _home(page, _cfg(), monkeypatch)

        _answer(view, "admin@sd48.bc.ca")

        assert home.IDENTITY_CARD_HEADLINE in _all_text(view), "a handler crash took the card with it"


# --------------------------------------------------------------------------- #
# 4. Dismissal — permanent, and recoverable only in Settings                   #
# --------------------------------------------------------------------------- #
class TestDismissal:
    def test_dont_ask_again_writes_only_the_flag_and_says_where_it_went(self, page, monkeypatch) -> None:
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(), monkeypatch)

        _button(view, home.IDENTITY_CARD_DISMISS_LABEL).on_click(None)

        assert writes == [{"identity_prompt_dismissed": True}]
        text = _all_text(view)
        assert home.IDENTITY_CARD_DISMISSED_NOTE in text
        assert "Settings" in text, "a permanent dismissal with no stated way back reads as a bug"
        assert home.IDENTITY_CARD_SAVE_LABEL not in text

    def test_a_refused_dismissal_claims_no_state_change(self, page, monkeypatch) -> None:
        from src.ui_flet.screens import setup as setup_screen

        _spy_saves(monkeypatch, result=False)
        view = _home(page, _cfg(), monkeypatch)

        _button(view, home.IDENTITY_CARD_DISMISS_LABEL).on_click(None)

        text = _all_text(view)
        assert setup_screen.IDENTITY_REFUSED_NOTE in text
        assert home.IDENTITY_CARD_DISMISSED_NOTE not in text, "claimed a dismissal that was refused"
        assert _button(view, home.IDENTITY_CARD_DISMISS_LABEL) is not None

    def test_the_dismissal_survives_a_remount_and_Settings_brings_it_back(
        self, page, monkeypatch, isolated_user_profile: Path
    ) -> None:
        """End to end through the REAL settings file: permanent, then recovered.

        ``identity_clear`` (Settings' blank-Save) resets the flag, which is what stops the
        states from wedging — no stored identity and no surface willing to ask again.
        """
        cfg = _cfg()
        cfg.save()

        dismissed_view = _home(page, cfg, monkeypatch)
        _button(dismissed_view, home.IDENTITY_CARD_DISMISS_LABEL).on_click(None)

        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_prompt_dismissed"] is True
        assert home.IDENTITY_CARD_HEADLINE not in _all_text(_home(page, AppConfig.load(), monkeypatch))

        AppConfig.load().identity_clear()

        assert home.IDENTITY_CARD_HEADLINE in _all_text(_home(page, AppConfig.load(), monkeypatch))


# --------------------------------------------------------------------------- #
# 5. The G3 mismatch card                                                      #
# --------------------------------------------------------------------------- #
class TestTheMismatchCard:
    def test_it_reports_the_difference_and_offers_both_ways_out(self, page, monkeypatch) -> None:
        _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd48myedbc"), monkeypatch)

        _answer(view, "admin@sd51.bc.ca")

        text = _all_text(view)
        assert "You're set up for" in text
        assert "Sea to Sky" in text and "Boundary" in text
        assert home.MISMATCH_DETAIL in text
        # It renders immediately after a SUCCESSFUL save, so it may not read as if nothing
        # was written — it says WHAT was saved and what was not.
        assert "We've saved your address" in home.MISMATCH_DETAIL
        assert _button(view, "Keep SD48 - Sea to Sky School District") is not None
        assert _button(view, home.MISMATCH_CHANGE_LABEL) is not None

    def test_it_never_appears_on_a_MOUNT_only_after_a_resolution(self, page, monkeypatch) -> None:
        """It is the answer to a question the admin just asked, not a standing accusation.

        A durable mismatch banner would nag a legitimately cross-district admin forever;
        Settings and Mapping own the durable story.
        """
        view = _home(page, _cfg(sis_type="sd48myedbc", identity_email="admin@sd51.bc.ca"), monkeypatch)

        assert "You're set up for" not in _all_text(view)

    def test_KEEP_retires_the_question_and_writes_NOTHING(self, page, monkeypatch) -> None:
        """The address was already stored, so only the question retires. No second write —
        ``identity_prompt_dismissed`` would be a lie (the ask WAS answered)."""
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd48myedbc"), monkeypatch)
        _answer(view, "admin@sd51.bc.ca")
        writes.clear()

        _button(view, "Keep SD48 - Sea to Sky School District").on_click(None)

        assert writes == []
        assert "You're set up for" not in _all_text(view)

    def test_CHANGE_DISTRICT_hands_the_switch_to_Mapping_and_changes_nothing_itself(self, page, monkeypatch) -> None:
        """Mapping owns the stale-schedule honesty of a district switch — so it owns the switch."""
        writes = _spy_saves(monkeypatch)
        hops: list[str] = []
        view = _home(page, _cfg(sis_type="sd48myedbc"), monkeypatch, hops)
        _answer(view, "admin@sd51.bc.ca")
        writes.clear()

        _button(view, home.MISMATCH_CHANGE_LABEL).on_click(None)

        assert hops == ["mapping"]
        assert writes == []

    @pytest.mark.parametrize("address", ["admin@sd48.bc.ca", "admin@sd51.bc.ca", "admin@nowhere.example.com", ""])
    def test_NO_card_path_ever_asks_to_write_sis_type(self, page, monkeypatch, address: str) -> None:
        """The product rule, swept over EVERY button any card offers, in every outcome.

        ``identity_save`` refuses a non-identity key loudly, so this could never SUCCEED —
        what is pinned here is that the cards never even ask, which is the difference
        between a guard that holds and a guard that is load-bearing.
        """
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd48myedbc", identity_sd_number="99"), monkeypatch)
        if address:
            _answer(view, address)

        pressed = _press_every_card_button(view)

        assert pressed, "no card button was pressed; the sweep below is vacuous"
        assert writes, "no write was attempted at all; the key check below is vacuous"
        assert all(set(call) <= set(IDENTITY_FIELDS) for call in writes), writes

    @pytest.mark.parametrize("resolution", ["Keep SD48 - Sea to Sky School District", MISMATCH_CHANGE])
    def test_the_configured_district_survives_a_mismatch_end_to_end(
        self, page, monkeypatch, isolated_user_profile: Path, resolution: str
    ) -> None:
        """Through the REAL settings file — the space the write-spy cannot see.

        The spy above records ``identity_save`` calls, so it is blind to the one shape that
        would actually re-point an install: ``cfg.sis_type = ...`` followed by any later
        ``save()``. Both resolutions are driven end to end and the file itself is read back,
        so "the cards never switch a district" rests on the district, not on a mock.
        """
        cfg = _cfg(sis_type="sd48myedbc")
        cfg.save()
        view = _home(page, cfg, monkeypatch)

        _answer(view, "admin@sd51.bc.ca")
        _button(view, resolution).on_click(None)

        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["sis_type"] == "sd48myedbc", "a mismatch card re-pointed the install"
        assert stored["identity_email"] == "admin@sd51.bc.ca"


# --------------------------------------------------------------------------- #
# 6. The durable not-listed card                                               #
# --------------------------------------------------------------------------- #
class TestTheNotListedCard:
    def test_it_shows_while_the_stored_number_has_no_mapping(self, page, monkeypatch) -> None:
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)

        text = _all_text(view)
        assert home.not_listed_headline("99") in text
        assert home.NOT_LISTED_DETAIL in text
        assert _button(view, home.NOT_LISTED_EMAIL_LABEL) is not None

    def test_it_stays_QUIET_when_we_actually_ship_that_mapping(self, page, monkeypatch) -> None:
        """Telling an admin we have no mapping for a district that ships in their exe is a
        plain untruth — and the likeliest way to write one is to forget this branch.

        Indexed off ``not_listed_headline`` rather than a copied literal: a copied string
        goes vacuous the moment the copy changes, which is exactly what happened to this
        assertion once the headline was corrected.
        """
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="48"), monkeypatch)

        assert home.not_listed_headline("48") not in _all_text(view)

    def test_email_support_uses_the_EXISTING_subject_only_route(self, page, monkeypatch) -> None:
        """Flag 6 — the app never puts the admin's address into anything it sends."""
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)

        _button(view, home.NOT_LISTED_EMAIL_LABEL).on_click(None)

        (url,), _kw = page.launch_url.call_args
        assert url.startswith("mailto:")
        assert "body=" not in url
        assert "admin@x.example.com" not in url

    def test_the_support_address_is_readable_without_a_mail_client(self, page, monkeypatch) -> None:
        """This card's only action is a ``mailto:`` — a silent dead click on a locked-down
        district server with no mail client registered. The house pattern (``help.py``) is
        to ALSO show the address, selectable, with a copy button; the button label names it
        too, so the destination is never hidden behind a verb."""
        from src.ui_flet.screens.help import SUPPORT_EMAIL

        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)

        card = _card_host(view)
        assert SUPPORT_EMAIL in home.NOT_LISTED_EMAIL_LABEL, "the button hides where the click goes"
        selectable = [
            c
            for c in _iter_controls(card)
            if isinstance(c, ft.Text) and c.value == SUPPORT_EMAIL and getattr(c, "selectable", False)
        ]
        assert selectable, "the address is not readable/selectable off-screen-reader"
        assert [c for c in _iter_controls(card) if isinstance(c, ft.IconButton)], "no copy affordance"

    def test_dismiss_clears_the_stored_number(self, page, monkeypatch) -> None:
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)

        _button(view, home.NOT_LISTED_DISMISS_LABEL).on_click(None)

        assert writes == [{"identity_sd_number": ""}]
        assert home.not_listed_headline("99") not in _all_text(view)

    def test_dismiss_NEVER_purges_the_settings_recovery_copies(
        self, page, monkeypatch, isolated_user_profile: Path
    ) -> None:
        """Aimed at the space the write-spy does NOT cover — the S4a data-loss shape.

        ``identity_clear`` unlinks every ``config.corrupt-*.json`` sibling (an erasure of
        an address from the whole profile). Retiring an advisory card is NOT an erasure,
        so it must route through ``identity_save`` and leave those copies alone: they are
        the admin's only way back from an unreadable settings file.
        """
        cfg = _cfg(identity_email="admin@x.example.com", identity_sd_number="99")
        cfg.save()
        quarantine = isolated_user_profile / "config.corrupt-20260729-101500.json"
        quarantine.write_text(json.dumps({"input_dir": "C:/gde/in"}), encoding="utf-8")

        view = _home(page, cfg, monkeypatch)
        _button(view, home.NOT_LISTED_DISMISS_LABEL).on_click(None)

        assert quarantine.exists(), "retiring a card destroyed the admin's settings-recovery copy"
        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_sd_number"] == ""
        assert stored["identity_email"] == "admin@x.example.com", "an unrelated field was cleared"

    def test_a_dismiss_with_nothing_stored_asks_for_no_write_at_all(self, page, monkeypatch) -> None:
        """The S4a lesson: gate a side effect on its subject existing.

        Driven by pressing the button twice — the second press is the shape a re-render, a
        double-click or a future caller would produce.
        """
        writes = _spy_saves(monkeypatch)
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)
        button = _button(view, home.NOT_LISTED_DISMISS_LABEL)

        button.on_click(None)
        button.on_click(None)

        assert writes == [{"identity_sd_number": ""}], "a second dismiss fired a write for nothing"

    def test_a_refused_dismiss_keeps_the_card_and_says_so(self, page, monkeypatch) -> None:
        from src.ui_flet.screens import setup as setup_screen

        _spy_saves(monkeypatch, result=False)
        view = _home(page, _cfg(identity_email="admin@x.example.com", identity_sd_number="99"), monkeypatch)

        _button(view, home.NOT_LISTED_DISMISS_LABEL).on_click(None)

        text = _all_text(view)
        assert home.not_listed_headline("99") in text, "the card vanished on a write that never happened"
        assert setup_screen.IDENTITY_REFUSED_NOTE in text

    def test_both_cards_can_coexist(self, page, monkeypatch) -> None:
        """A hand-editable profile can hold a district number with no address."""
        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        text = _all_text(view)
        assert home.IDENTITY_CARD_HEADLINE in text
        assert home.not_listed_headline("99") in text


# --------------------------------------------------------------------------- #
# 6b. plan 0044 slice 2 — an overlay (shipped OR added) retires the card       #
# --------------------------------------------------------------------------- #
# `unmapped_sd_number(cfg, available_configs())` already counts user-dir ids and
# already ships this behaviour; slice 2's job is to pin it, not to change it. These
# tests deliberately UNDO `_quiet_home`'s `available_configs` stub (that stub reads
# a small fixture INDEX, not the disk) and route Home through the REAL loader
# function instead, over the REAL two-directory search `write_overlay` (plan 0044
# S1) writes into. `isolated_user_profile` is autouse, so the overlay lands in a
# per-test tmp dir, never the real profile.
class TestAnOverlayRetiresTheNotListedCard:
    def test_a_written_overlay_retires_the_card(self, page, monkeypatch) -> None:
        from src.config.authoring import OverlaySpec, write_overlay

        monkeypatch.setattr(home, "available_configs", real_available_configs)
        write_overlay(
            OverlaySpec(sd_number=93, district_name="SD93 test", district_domains=("sd93.bc.ca",), base="myedbc"),
            overwrite=False,
        )

        view = _home(page, _cfg(identity_sd_number="93"), monkeypatch)

        assert home.not_listed_headline("93") not in _all_text(view)

    def test_the_twin_without_the_overlay_still_shows_the_card(self, page, monkeypatch) -> None:
        """Same config, same real loader, no overlay written — the card must still fire.

        The positive twin above is meaningless without this: it proves the card is not
        simply gone because we swapped `available_configs`.
        """
        monkeypatch.setattr(home, "available_configs", real_available_configs)

        view = _home(page, _cfg(identity_sd_number="93"), monkeypatch)

        assert home.not_listed_headline("93") in _all_text(view)

    def test_unmapped_sd_number_level_pin_sd48custom_retires_while_sd4_stays_unmapped(self, monkeypatch) -> None:
        """The reader `unmapped_sd_number` itself, isolated from the card's rendering.

        `sd48custom` retires the SD48 ask (an added config is as good as a shipped one);
        `SD4` is untouched by it — the `(?!\\d)` boundary the resolver already enforces
        (`SD4` must never match `sd48*`), asserted here at the exact call the card makes.
        """
        from src.config.authoring import OverlaySpec, write_overlay

        write_overlay(
            OverlaySpec(sd_number=48, district_name="SD48 test", district_domains=(), base="myedbc"),
            overwrite=False,
        )

        ids = real_available_configs()

        assert unmapped_sd_number(_cfg(identity_sd_number="48"), ids) == ""
        assert unmapped_sd_number(_cfg(identity_sd_number="4"), ids) == "4"


# --------------------------------------------------------------------------- #
# 7. Mount cost, and surviving the schedule re-render                           #
# --------------------------------------------------------------------------- #
class TestTheMountIsCheapAndTheCardIsStable:
    def test_mounting_home_never_parses_the_eleven_bundled_yamls(self, page, monkeypatch) -> None:
        """The cost claim, asserted instead of asserted-in-a-comment.

        ``district_domain_index()`` reads every bundled mapping (~210 ms). Home is the
        flagship surface and is mounted on every rail hop, so the index is built lazily in
        the Save handler — an event that happens at most once in an install's life.
        """
        calls: list[int] = []
        monkeypatch.setattr(home, "district_domain_index", lambda **_kw: calls.append(1) or dict(INDEX))

        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        assert calls == [], "mounting Home parsed the bundled mappings"
        # The positive twin: the same seam IS used when the admin actually answers.
        _spy_saves(monkeypatch)
        _answer(view, "admin@sd48.bc.ca")
        assert calls == [1], "the resolution never consulted the domain index at all"

    def test_the_S7_size_clause_reads_ONE_config_not_the_whole_catalog(self, page, monkeypatch) -> None:
        """0038 S7 kept the same promise while adding a config read to Home's mount.

        The roster-size clause needs the ACTIVE district's produced entities. That is one
        config (memoised per session), resolved ONCE outside ``_render``; going through the
        eleven-YAML ``catalog()`` build here would re-introduce exactly the cost the row above
        exists to refuse — and the schedule probe re-derives the control list, so a per-render
        resolution would pay it repeatedly.
        """
        from src.ui_flet import mapping_catalog

        catalog_calls: list[int] = []
        entity_calls: list[str] = []
        monkeypatch.setattr(mapping_catalog, "catalog", lambda **_kw: catalog_calls.append(1) or ())
        real = home.active_output_entities
        monkeypatch.setattr(
            home,
            "active_output_entities",
            lambda sis, **kw: (entity_calls.append(sis), real(sis, **kw))[1],
        )

        _home(page, _cfg(sis_type="sd48myedbc"), monkeypatch)

        assert catalog_calls == [], "mounting Home built the eleven-config catalog"
        assert entity_calls == ["sd48myedbc"], f"expected ONE per-mount resolution, got {entity_calls}"

    def test_a_half_typed_address_survives_the_schedule_read_back(self, monkeypatch) -> None:
        """The card is built ONCE, outside ``_render``.

        The off-thread schedule probe re-derives Home's whole control list when it returns.
        Rebuilding the card there would silently wipe an address the admin was midway
        through typing — a data-loss bug with no error and no trace.

        ``get_scheduler`` is stubbed because ``_probe_schedule_async`` returns EARLY on a
        scheduler with no read-back (``supports_read_schedule`` is False for Linux cron), so
        on CI the probe never marshalled and this row failed on its own vacuity guard —
        red on every Linux run since it was written. Stubbing the capability rather than
        skipping the platform is deliberate: the subject is the card surviving a re-render,
        which is OS-independent, so the coverage should be too. Same seam and shape as
        ``tests/test_ui_flet_shell_boot.py``'s badge-probe rows.
        """
        live = ScheduleStatus(
            state=ScheduleState.LIVE, headline="Nightly sync is scheduled", detail="Next run at 3:00 AM"
        )
        monkeypatch.setattr(home, "get_scheduler", lambda: MagicMock(supports_read_schedule=True))
        monkeypatch.setattr("src.ui_flet.schedule_probe.probe_schedule", lambda *a, **k: live)
        captured: list = []
        page = MagicMock()
        page.run_thread = lambda fn: fn()
        page.run_task = lambda coro, *args: captured.append((coro, args))

        view = _home(page, _cfg(), monkeypatch)
        field = _field(view, home.identity_screen.EMAIL_LABEL)
        field.value = "admin@sd4"  # mid-keystroke

        assert captured, "the probe never marshalled a result; the assertion below is vacuous"
        for coro_fn, _args in captured:
            asyncio.run(coro_fn())

        assert _field(view, home.identity_screen.EMAIL_LABEL).value == "admin@sd4"


# --------------------------------------------------------------------------- #
# 8. The floor — a card bug costs the card, never the verdict                  #
# --------------------------------------------------------------------------- #
class TestTheFloor:
    def test_a_raise_in_the_card_PREDICATE_leaves_the_dashboard_intact(self, page, monkeypatch) -> None:
        def _boom(*_a: object, **_kw: object) -> bool:
            raise RuntimeError("injected predicate failure")

        monkeypatch.setattr(home, "needs_identity_prompt", _boom)

        view = _home(page, _cfg(), monkeypatch)  # _home fails if Home fell to its ErrorCard

        text = _all_text(view)
        assert "Home" in text, "the dashboard did not render"
        assert home.IDENTITY_CARD_HEADLINE not in text

    def test_a_raise_ENUMERATING_the_mappings_leaves_the_dashboard_intact(self, page, monkeypatch) -> None:
        """The not-listed card's input. Failing to list the configs must not tell an admin
        we have no mapping for a district we may well already ship — silence is safe."""

        def _boom(*_a: object, **_kw: object) -> list[str]:
            raise OSError("the mappings dir is gone")

        monkeypatch.setattr(home, "available_configs", _boom)

        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        text = _all_text(view)
        assert "Home" in text
        assert home.not_listed_headline("99") not in text

    def test_the_positive_twin_the_same_build_DOES_render_a_card(self, page, monkeypatch) -> None:
        """Without which both absences above are equally satisfied by a card that stopped
        rendering for an entirely different reason."""
        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        text = _all_text(view)
        assert home.IDENTITY_CARD_HEADLINE in text
        assert home.not_listed_headline("99") in text


# --------------------------------------------------------------------------- #
# 9. The register                                                              #
# --------------------------------------------------------------------------- #
class TestTheRegister:
    def test_every_card_constant_stays_inside_the_register(self) -> None:
        names = (
            "IDENTITY_CARD_HEADLINE",
            "IDENTITY_CARD_DETAIL",
            "IDENTITY_CARD_SAVE_LABEL",
            "IDENTITY_CARD_DISMISS_LABEL",
            "IDENTITY_CARD_DISMISSED_NOTE",
            "IDENTITY_CARD_MATCHED_NOTE",
            "MISMATCH_HEADLINE",
            "MISMATCH_DETAIL",
            "MISMATCH_KEEP_LABEL",
            "MISMATCH_CHANGE_LABEL",
            "NOT_LISTED_HEADLINE",
            "NOT_LISTED_DETAIL",
            "NOT_LISTED_EMAIL_LABEL",
            "NOT_LISTED_DISMISS_LABEL",
        )
        text = "\n".join(getattr(home, name) for name in names)

        assert home.IDENTITY_CARD_DISMISSED_NOTE in text, "the sweep missed a constant; it may be vacuous"
        _assert_no_banned_vocabulary(text, "the Home identity-card constants")

    def test_the_not_listed_card_claims_no_work_nobody_was_told_about(self) -> None:
        """The card renders BEFORE any request exists.

        The district number lives only in this computer's ``config.json``, the support mail
        is subject-only (flag 6), and the card's own detail line asks the ADMIN to send the
        extract — so a headline asserting that a mapping is being built would describe work
        nobody outside this machine knows about. It says what is true of US instead.
        (Pinned so the plan's original sketch literal cannot come back silently.)
        """
        from src.ui_flet.screens.help import SUPPORT_EMAIL

        headline = home.NOT_LISTED_HEADLINE.lower()

        for claim in ("building", "we're working", "in progress", "underway"):
            assert claim not in headline, f"the not-listed headline asserts vendor work: {home.NOT_LISTED_HEADLINE!r}"
        assert "don't have a mapping" in headline
        # ...and the ACTION still lives on the card, so the correction did not remove the path.
        # The detail names the support ADDRESS directly now (owner copy, QA 2026-08-18); the
        # button and the copyable line beside it open/copy that same address.
        assert SUPPORT_EMAIL.lower() in home.NOT_LISTED_DETAIL.lower()
        # The detail must not assert vendor work either — the headline is not the only place
        # this card could over-promise.
        for claim in ("building", "we're working", "in progress", "underway"):
            assert claim not in home.NOT_LISTED_DETAIL.lower()

    @pytest.mark.parametrize(
        ("address", "state"),
        [
            ("", "idle"),
            ("nonsense", "invalid"),
            ("admin@sd48.bc.ca", "matched"),
            ("admin@sd51.bc.ca", "mismatch"),
            ("admin@nowhere.example.com", "no-match"),
        ],
    )
    def test_no_rendered_card_state_uses_the_banned_vocabulary(
        self, page, monkeypatch, address: str, state: str
    ) -> None:
        _spy_saves(monkeypatch)
        view = _home(page, _cfg(sis_type="sd48myedbc", identity_sd_number="99"), monkeypatch)
        if address:
            _answer(view, address)

        _assert_no_banned_vocabulary(_all_text(_card_host(view)), f"the Home identity cards ({state})")

    def test_the_cards_carry_no_security_theatre(self, page, monkeypatch) -> None:
        """The launch page's "deliberately absent" list applies here too — this is the same
        question, asked on a different surface."""
        view = _home(page, _cfg(identity_sd_number="99"), monkeypatch)

        controls = list(_iter_controls(view))
        assert not [c for c in controls if isinstance(c, ft.TextField) and getattr(c, "password", False)]
        assert not [c for c in controls if isinstance(c, ft.ProgressRing)]
        icons = [str(getattr(c, "name", "")) for c in controls if isinstance(c, ft.Icon)]
        assert not [i for i in icons if "LOCK" in i.upper() or "SHIELD" in i.upper()], icons


# --------------------------------------------------------------------------- #
# 10. The composed copy helpers (pure)                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ((), ""),
        (("A",), "A"),
        (("A", "B"), "A and B"),
        (("A", "B", "C"), "A, B and C"),
    ],
)
def test_join_district_names(names, expected) -> None:
    """A single domain legitimately claims several configs, so the plural case is real and
    must read as a sentence rather than as a tuple repr."""
    assert home.join_district_names(names) == expected
