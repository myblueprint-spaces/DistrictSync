"""The launch page, the Settings section, and the Help echo (plan 0038 S4a).

These are VIEW-glue tests, and view glue is coverage-omitted — so they assert the things
coverage cannot: that every page STATE constructs, that each one has exactly one filled
primary and a way out, that the copy stays inside the register the product committed to,
and that the "deliberately absent" list is actually absent.

The vocabulary sweep is the load-bearing one. The whole feature rests on a promise that
this is IDENTIFICATION and not authentication, and the cheapest way to break that promise
is a single word — "verify", "account", "access" — written months from now by someone who
never read the plan. A word list is a blunt instrument, and it is the right one here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet import mapping_catalog
from src.ui_flet.screens import identity
from src.ui_flet.screens.help import build_help
from src.ui_flet.screens.identity import build_identity

# The banned vocabulary (plan 0038, Goals/Non-goals). Word-boundary matched, so "address"
# can never be read as "access" and a false red can never be argued away.
BANNED_WORDS = (
    "sign in",
    "sign-in",
    "log in",
    "login",
    "verify",
    "verified",
    "unlock",
    "authorized",
    "authorised",
    "authorize",
    "account",
    "credential",
    "credentials",
    "access",
)
# The list itself is never described as any of these — it is a PUBLIC fact used to shorten
# a picker, and dressing it as a security control would be the same lie in adjectives.
BANNED_DESCRIPTIONS = ("protected", "secured", "anonymous", "not harvestable", "encrypted")


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


def _iter_controls(control):  # noqa: ANN001, ANN202 - a walker over an untyped Flet tree
    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, ft.Control):
                yield from _iter_controls(item)


def _all_text(control) -> str:  # noqa: ANN001 - untyped Flet tree
    """Every user-visible string in the tree: text values, labels, helpers, hints, buttons."""
    chunks: list[str] = []
    for c in _iter_controls(control):
        for attr in ("value", "label", "helper", "hint_text", "content", "tooltip"):
            found = getattr(c, attr, None)
            if isinstance(found, str):
                chunks.append(found)
    return "\n".join(chunks)


def _button(control, label: str):  # noqa: ANN001, ANN202 - untyped Flet tree
    buttons = [c for c in _iter_controls(control) if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))]
    for candidate in buttons:
        if candidate.content == label:
            return candidate
    raise AssertionError(f"no button labelled {label!r}; found {[b.content for b in buttons]}")


def _error_texts(control) -> str:  # noqa: ANN001 - untyped Flet tree
    """Only the strings painted in the FAILED colour — the inline error, not the whole page.

    The field keeps whatever the admin typed (that is the point of a text field); what must
    never carry the value is the MESSAGE, because a caller that logs it would leak it.
    """
    from src.ui_flet import tokens

    return "\n".join(
        c.value
        for c in _iter_controls(control)
        if isinstance(c, ft.Text) and isinstance(c.value, str) and c.color == tokens.color_status_failed
    )


def _field(control, label: str) -> ft.TextField:  # noqa: ANN001 - untyped Flet tree
    for c in _iter_controls(control):
        if isinstance(c, ft.TextField) and c.label == label:
            return c
    raise AssertionError(f"no TextField labelled {label!r}")


def _assert_no_banned_vocabulary(text: str, where: str) -> None:
    lowered = text.lower()
    for word in BANNED_WORDS:
        assert not re.search(rf"\b{re.escape(word)}\b", lowered), f"banned word {word!r} in {where}: {text!r}"
    for word in BANNED_DESCRIPTIONS:
        assert word not in lowered, f"banned description {word!r} in {where}: {text!r}"


# A small, explicit index: one claimed district, one district claimed twice (the SD51 +
# attendance-tier shape that makes matched-several the LIVE case), two unclaimed configs.
INDEX = {
    "myedbc": (),
    "mbp_all": (),
    "sd48myedbc": ("sd48.bc.ca",),
    "sd51myedbc": ("sd51.bc.ca",),
    "sd51attendance": ("sd51.bc.ca",),
}


@pytest.fixture
def fixed_index(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str, ...]]:
    """Pin the page's index so a state test never depends on the shipped YAML rows."""
    monkeypatch.setattr(identity, "district_domain_index", lambda **_kw: dict(INDEX))
    return INDEX


def _page_at(page: MagicMock, address: str, *, cfg: AppConfig | None = None) -> ft.Control:
    """Build the page and drive it to the state ``address`` resolves to."""
    view = build_identity(page, app_config=cfg or AppConfig(), on_enter=lambda: None)
    _field(view, identity.EMAIL_LABEL).value = address
    _button(view, identity.CONTINUE_LABEL).on_click(None)
    return view


# --------------------------------------------------------------------------- #
# 1. Every state constructs, has ONE filled primary, and has a way out         #
# --------------------------------------------------------------------------- #
class TestPageStates:
    def test_initial_state_focuses_the_field_and_gates_continue(self, page: MagicMock) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)

        field = _field(view, identity.EMAIL_LABEL)
        assert field.autofocus is True, "the admin should be able to type immediately"
        assert _button(view, identity.CONTINUE_LABEL).disabled is True, "Continue is gated while blank"

    def test_the_continue_gate_follows_the_keystrokes(self, page: MagicMock) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)

        field.value = "a"
        field.on_change(None)

        assert _button(view, identity.CONTINUE_LABEL).disabled is False

    def test_an_invalid_format_is_NOT_shown_while_typing(self, page: MagicMock) -> None:
        """The error fires on blur/submit only.

        An error that appears after the third keystroke of a correct address is an
        accusation, not help — and it is the single most common way a form like this is
        made to feel hostile.
        """
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)

        field.value = "adm"
        field.on_change(None)

        # Nothing is discounted any more: the page renders no helper (2026-08-04) and no
        # placeholder (2026-08-05), so the assertion is now the plain, strongest form —
        # an "@" anywhere on screen while typing means an error leaked.
        assert "@" not in _all_text(view)

    def test_an_invalid_format_IS_shown_on_blur(self, page: MagicMock) -> None:
        """The positive twin of the test above — the error mechanism really does fire.

        Also pins the message's shape: it carries the RULE and a canned example, never the
        value the admin typed (a caller that logged it would leak personal data).
        """
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)
        field.value = "notanemail"

        field.on_blur(None)

        error = _error_texts(view)
        assert "exactly one @" in error
        assert "notanemail" not in error

    def test_a_blank_field_blurred_is_not_yet_a_mistake(self, page: MagicMock) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)
        field.value = "   "

        field.on_blur(None)

        assert "exactly one @" not in _all_text(view)

    def test_matched_one_is_a_correctable_pre_selection(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@sd48.bc.ca")

        text = _all_text(view)
        assert "Sea to Sky" in text
        assert "you'll confirm it on the next step" in text
        # The register: a hint about which list to show, never a finding about a person.
        assert "We found your district" not in text
        assert _button(view, identity.CORRECTION_LABEL) is not None

    def test_no_state_promises_a_SURFACE_this_page_cannot_see(self, page: MagicMock, fixed_index) -> None:
        """The launch page describes what IT did, never what another screen will do.

        Authored when the pickers were unscoped, to stay true on both sides of S5 — and it
        did: S5 landed and no string on this page changed. The rule outlives its occasion,
        so the assertion stays: no state may say "we'll show you your district's settings",
        because this page cannot verify what the District step renders.
        """
        for address in ("admin@sd48.bc.ca", "admin@sd51.bc.ca", "admin@gmail.com"):
            view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
            _field(view, identity.EMAIL_LABEL).value = address
            _button(view, identity.CONTINUE_LABEL).on_click(None)

            text = _all_text(view).lower()
            assert "we'll show you" not in text, f"{address} promises a surface this page cannot see"

    def test_no_state_claims_a_per_person_registry(self, page: MagicMock, fixed_index) -> None:
        """Matching is by a district's public DOMAIN — there is no list of people.

        "We don't have that address on file" describes the hashed per-person allowlist the
        owner retired on 2026-07-28; saying it would misdescribe the whole design and imply
        a database of admins that does not exist.
        """
        view = _page_at(page, "admin@gmail.com")

        text = _all_text(view)
        assert "have a district on file for that address" in text
        assert "have that address on file" not in text

    def test_matched_several_asks_which_and_names_them(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@sd51.bc.ca")

        text = _all_text(view)
        assert identity.SEVERAL_HEADLINE in text
        assert "Boundary" in text, "the matched districts must be named, not counted"

    def test_no_match_is_calm_and_offers_the_sd_number(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@gmail.com")

        text = _all_text(view)
        assert identity.NO_MATCH_HEADLINE in text
        assert "no problem" in text
        assert _field(view, identity.SD_LABEL) is not None

    def test_an_sd_number_that_resolves_names_the_district(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@gmail.com")
        sd = _field(view, identity.SD_LABEL)
        sd.value = "SD48"

        sd.on_blur(None)

        assert "Sea to Sky" in _all_text(view)

    def test_an_sd_number_that_does_not_resolve_says_so_without_a_dead_end(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@gmail.com")
        sd = _field(view, identity.SD_LABEL)
        sd.value = "99"

        sd.on_blur(None)

        assert identity.sd_unknown_note("99") in _all_text(view)
        assert _button(view, identity.NOT_LISTED_LABEL) is not None
        assert _button(view, identity.GET_STARTED_LABEL) is not None

    def test_not_listed_notes_the_number_and_points_somewhere_real(self, page: MagicMock, fixed_index) -> None:
        view = _page_at(page, "admin@gmail.com")
        _field(view, identity.SD_LABEL).value = "99"

        _button(view, identity.NOT_LISTED_LABEL).on_click(None)

        text = _all_text(view)
        assert "we've made a note of SD99" in text
        assert "Help" in text, "a not-listed district must still be told where to go"

    @pytest.mark.parametrize(
        ("address", "state"),
        [
            ("", "initial"),
            ("admin@sd48.bc.ca", "matched-one"),
            ("admin@sd51.bc.ca", "matched-several"),
            ("admin@gmail.com", "no-match"),
        ],
    )
    def test_every_state_has_exactly_one_filled_primary(
        self, page: MagicMock, fixed_index, address: str, state: str
    ) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        if address:
            _field(view, identity.EMAIL_LABEL).value = address
            _button(view, identity.CONTINUE_LABEL).on_click(None)

        filled = [c for c in _iter_controls(view) if isinstance(c, ft.FilledButton)]

        assert len(filled) == 1, f"{state} rendered {len(filled)} filled primaries"

    @pytest.mark.parametrize(
        ("address", "state"),
        [
            ("", "initial"),
            ("admin@sd48.bc.ca", "matched-one"),
            ("admin@sd51.bc.ca", "matched-several"),
            ("admin@gmail.com", "no-match"),
        ],
    )
    def test_every_state_offers_the_escape(self, page: MagicMock, fixed_index, address: str, state: str) -> None:
        """There is no page from which the person at the console cannot leave unanswered."""
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        if address:
            _field(view, identity.EMAIL_LABEL).value = address
            _button(view, identity.CONTINUE_LABEL).on_click(None)

        assert _button(view, identity.SKIP_LABEL) is not None, f"{state} has no way out"


# --------------------------------------------------------------------------- #
# 2. The register — vocabulary and the deliberately-absent list                #
# --------------------------------------------------------------------------- #
class TestTheRegister:
    @pytest.mark.parametrize("address", ["", "not an email", "admin@sd48.bc.ca", "admin@sd51.bc.ca", "admin@gmail.com"])
    def test_no_state_uses_the_banned_vocabulary(self, page: MagicMock, fixed_index, address: str) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        if address:
            _field(view, identity.EMAIL_LABEL).value = address
            _button(view, identity.CONTINUE_LABEL).on_click(None)

        _assert_no_banned_vocabulary(_all_text(view), f"the launch page after {address!r}")

    def test_the_sweep_would_catch_a_violation(self) -> None:
        """Falsification twin — the word list is matched, not merely iterated."""
        with pytest.raises(AssertionError):
            _assert_no_banned_vocabulary("Sign in to verify your account", "a planted string")

    def test_the_sweep_does_not_fire_on_the_words_we_do_use(self) -> None:
        """The other direction: a word-boundary match, so "address" is not "access"."""
        _assert_no_banned_vocabulary("Work email address — it stays on this computer.", "our own copy")

    def test_the_explainers_say_what_is_STORED_not_only_what_is_matched(self) -> None:
        """Minimisation honesty, on both surfaces that explain the field.

        The DOMAIN is what we match on — but the WHOLE address is what we keep, render in
        Settings and echo on Help. Copy that says "we use only the part after the @"
        describes a data-minimising design we did not build, which is the most defensible-
        sounding kind of untrue.

        **The LAUNCH PAGE is no longer one of those surfaces** (2026-08-04): it renders no
        helper at all, so it makes no claim to be honest or dishonest about. The rule binds
        the two surfaces that DO explain — Home's one-time ask card (which renders
        ``identity.EMAIL_HELPER``) and the Settings section. That is asserted below rather
        than assumed, so this test cannot go on quietly checking a string nobody paints.
        """
        from src.ui_flet.screens import home, setup

        assert home.identity_screen.EMAIL_HELPER is identity.EMAIL_HELPER, "Home is the helper's live consumer"

        for where, text in (("Home's ask card", identity.EMAIL_HELPER), ("Settings", setup.IDENTITY_EXPLAINER)):
            assert "only the part after the @" not in text, f"{where} under-states what is stored"
            assert "whole address is saved" in text, f"{where} never says the whole address is kept"

    def test_the_not_listed_note_never_suggests_a_district_that_is_not_theirs(
        self, page: MagicMock, fixed_index
    ) -> None:
        """ "Choose the closest district for now" would advise the worst click in the product.

        A wrong mapping ships a wrong roster — real students, to a real partner. An admin
        whose district has no mapping must be routed to support for one, never nudged into
        someone else's config as a stopgap.
        """
        view = _page_at(page, "admin@gmail.com")
        _field(view, identity.SD_LABEL).value = "99"
        _button(view, identity.NOT_LISTED_LABEL).on_click(None)

        text = _all_text(view).lower()
        assert "closest district" not in text
        assert "build a mapping" in text, "the honest route (ask support for a mapping) is missing"

    @pytest.mark.parametrize("address", ["", "admin@sd48.bc.ca", "admin@gmail.com"])
    def test_no_password_field_no_spinner_no_lock_glyph(self, page: MagicMock, fixed_index, address: str) -> None:
        """Three of the "deliberately absent" list, asserted where they are testable.

        Each would import a threat model this page does not have: a secret to protect, a
        server to wait for, a thing to be locked out of.
        """
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        if address:
            _field(view, identity.EMAIL_LABEL).value = address
            _button(view, identity.CONTINUE_LABEL).on_click(None)

        controls = list(_iter_controls(view))
        assert not [c for c in controls if isinstance(c, ft.TextField) and getattr(c, "password", False)]
        assert not [c for c in controls if isinstance(c, ft.ProgressRing)]
        icons = [str(getattr(c, "name", "")) for c in controls if isinstance(c, ft.Icon)]
        assert not [i for i in icons if "LOCK" in i.upper() or "SHIELD" in i.upper()], icons

    def test_there_is_no_attempt_counter(self, page: MagicMock, fixed_index) -> None:
        """Ten wrong answers leave the page exactly where one does. Nothing is counted."""
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)

        for _ in range(10):
            field.value = "still not an email"
            field.on_blur(None)

        assert _button(view, identity.CONTINUE_LABEL).disabled is False
        text = _all_text(view)
        assert "attempt" not in text.lower()
        assert "too many" not in text.lower()


# --------------------------------------------------------------------------- #
# 3. The escape stores nothing                                                 #
# --------------------------------------------------------------------------- #
def test_the_escape_never_writes_a_single_field(page: MagicMock, fixed_index, monkeypatch) -> None:
    """G7 (flag 1) — asserted at the WRITE, not at the file.

    A file-level check would also pass if the save silently failed; spying the choke point
    proves the page never even asked.
    """
    writes: list[dict] = []
    monkeypatch.setattr(AppConfig, "identity_save", lambda self, **kw: writes.append(kw) or True)
    entered: list[int] = []

    view = build_identity(page, app_config=AppConfig(), on_enter=lambda: entered.append(1))
    _field(view, identity.EMAIL_LABEL).value = "admin@sd48.bc.ca"
    _button(view, identity.SKIP_LABEL).on_click(None)

    assert writes == [], "the escape must store nothing at all"
    assert entered == [1], "the escape must still open the app"


def test_get_started_writes_through_the_choke_point(page: MagicMock, fixed_index, monkeypatch) -> None:
    """The POSITIVE twin: the same page, the same spy, one field written."""
    writes: list[dict] = []
    monkeypatch.setattr(AppConfig, "identity_save", lambda self, **kw: writes.append(kw) or True)

    view = _page_at(page, "Admin.Person@SD48.bc.ca")
    _button(view, identity.GET_STARTED_LABEL).on_click(None)

    assert writes == [{"identity_email": "Admin.Person@SD48.bc.ca"}], "stored AS TYPED, never normalised"


@pytest.mark.parametrize("address", ["admin@sd48.bc.ca", "admin@sd51.bc.ca"])
def test_the_correction_affordance_stores_NOTHING(page: MagicMock, fixed_index, monkeypatch, address: str) -> None:
    """ "That's not my district" must not persist the domain the admin just rejected.

    This is the affordance that makes the matched state a *correctable* pre-selection
    rather than a verdict. The stored address is the ONE input to S5's scoping, so keeping
    it would turn a correction into a durable mis-scope — the admin would have to make the
    same correction twice, here and again in Settings, and every picker would be wrong in
    between. So it behaves like the escape: enter unfiltered, store nothing, ask next launch.
    """
    writes: list[dict] = []
    monkeypatch.setattr(AppConfig, "identity_save", lambda self, **kw: writes.append(kw) or True)
    entered: list[int] = []

    view = build_identity(page, app_config=AppConfig(), on_enter=lambda: entered.append(1))
    _field(view, identity.EMAIL_LABEL).value = address
    _button(view, identity.CONTINUE_LABEL).on_click(None)
    _button(view, identity.CORRECTION_LABEL).on_click(None)

    assert writes == [], "the rejected domain was persisted anyway"
    assert entered == [1], "the correction must still open the app"


def test_try_again_returns_to_the_field_without_storing(page: MagicMock, fixed_index, monkeypatch) -> None:
    """A typo's cheapest fix is re-typing — so there must be a way back to the field.

    Without it the only exit from a wrong resolution is to skip the page entirely and go
    hunting in Settings, and a mistyped domain (`sd84` for `sd48`) is the likeliest way to
    land in a wrong state at all.
    """
    writes: list[dict] = []
    monkeypatch.setattr(AppConfig, "identity_save", lambda self, **kw: writes.append(kw) or True)
    entered: list[int] = []

    view = build_identity(page, app_config=AppConfig(), on_enter=lambda: entered.append(1))
    _field(view, identity.EMAIL_LABEL).value = "admin@sd48.bc.ca"
    _button(view, identity.CONTINUE_LABEL).on_click(None)
    _button(view, identity.RETRY_LABEL).on_click(None)

    assert _button(view, identity.CONTINUE_LABEL) is not None, "we're back at the ask state"
    assert _field(view, identity.EMAIL_LABEL).value == "admin@sd48.bc.ca", "the typed value survives for editing"
    assert (writes, entered) == ([], []), "retrying stores nothing and does not enter"


def test_try_again_is_offered_from_the_no_match_state_too(page: MagicMock, fixed_index) -> None:
    """The state a typo lands in MOST often is no-match — it needs the way back most."""
    view = _page_at(page, "admin@gmial.com")

    _button(view, identity.RETRY_LABEL).on_click(None)

    assert _button(view, identity.CONTINUE_LABEL) is not None


def test_not_listed_without_a_number_still_reads_as_a_complete_sentence(page: MagicMock, fixed_index) -> None:
    """The branch a state-by-state sweep misses: "isn't listed" pressed with the box EMPTY.

    Reachable in one click from the no-match state, and the obvious implementation
    (f-string an empty digits value) paints "we've made a note of SD." at the admin.
    """
    view = _page_at(page, "admin@gmail.com")

    _button(view, identity.NOT_LISTED_LABEL).on_click(None)

    text = _all_text(view)
    assert "SD." not in text and "of SD " not in text, f"an empty district number leaked into the copy: {text}"
    assert "we've made a note of that" in text


def test_a_not_listed_answer_persists_the_sd_number(page: MagicMock, fixed_index, monkeypatch) -> None:
    writes: list[dict] = []
    monkeypatch.setattr(AppConfig, "identity_save", lambda self, **kw: writes.append(kw) or True)

    view = _page_at(page, "admin@gmail.com")
    _field(view, identity.SD_LABEL).value = "SD99"
    _button(view, identity.GET_STARTED_LABEL).on_click(None)

    assert writes == [{"identity_email": "admin@gmail.com", "identity_sd_number": "99"}]


def test_resolution_never_touches_the_configured_district(page: MagicMock, fixed_index, isolated_user_profile) -> None:
    """A product rule enforced structurally by ``identity_save``, pinned end to end here."""
    cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="sd74myedbc")
    cfg.save()

    view = _page_at(page, "admin@sd48.bc.ca", cfg=cfg)
    _button(view, identity.GET_STARTED_LABEL).on_click(None)

    stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
    assert stored["sis_type"] == "sd74myedbc", "matching SD48 must not re-point a SD74 install"
    assert stored["identity_email"] == "admin@sd48.bc.ca"


# --------------------------------------------------------------------------- #
# 4. The domain index — the effectful half of the S4a→S5 seam                  #
# --------------------------------------------------------------------------- #
class TestTheDomainIndex:
    def test_the_shipped_rows_resolve_end_to_end(self) -> None:
        """The LIVE check: a real bundled domain resolves to its real district.

        Possible only because the values ship in the clear (flag 3). It is what stops the
        whole feature from shipping dark and being discovered broken in the field.
        """
        from src.ui_flet.identity_gate import resolve_domain

        index = mapping_catalog.district_domain_index()

        assert resolve_domain("sd48.bc.ca", index) == ("sd48myedbc",)
        assert set(resolve_domain("sd51.bc.ca", index)) == {"sd51myedbc", "sd51attendance"}
        assert resolve_domain("prn.bc.ca", index) == ("sd60myedbc",), "SD60's STAFF domain, not the student one"

    def test_the_generic_tiers_claim_nobody(self) -> None:
        index = mapping_catalog.district_domain_index()

        assert index["myedbc"] == ()
        assert index["mbp_all"] == ()

    def test_a_failure_listing_the_configs_degrades_to_an_empty_index(self, monkeypatch) -> None:
        monkeypatch.setattr(
            mapping_catalog, "available_configs", MagicMock(side_effect=OSError("the mappings dir is gone"))
        )

        assert mapping_catalog.district_domain_index() == {}

    def test_one_broken_config_is_unclaimed_and_named_in_the_log(self, monkeypatch, caplog) -> None:
        """Fail-open, per config: a broken YAML can only ever WIDEN a list.

        Its own admin then matches nothing and sees everything — which is exactly why the
        district can never disappear on the person who needs it.
        """
        real_load = mapping_catalog.load_config

        def _load(sis_type, config_dir=None):  # noqa: ANN001, ANN202
            if sis_type == "sd48myedbc":
                raise ValueError("this YAML is broken")
            return real_load(sis_type, config_dir)

        monkeypatch.setattr(mapping_catalog, "load_config", _load)

        with caplog.at_level("WARNING"):
            index = mapping_catalog.district_domain_index()

        assert index["sd48myedbc"] == ()
        assert index["sd51myedbc"] == ("sd51.bc.ca",), "the other districts survive one bad config"
        assert "sd48myedbc" in caplog.text


# --------------------------------------------------------------------------- #
# 5. The counts-only logging                                                   #
# --------------------------------------------------------------------------- #
class TestIdentityLogging:
    SECRET = "someone.private@sd48.bc.ca"

    def test_the_resolve_line_carries_counts_and_never_the_address(self, page: MagicMock, fixed_index, caplog) -> None:
        with caplog.at_level("INFO"):
            _page_at(page, self.SECRET)

        lines = [r.getMessage() for r in caplog.records if "identity resolve:" in r.getMessage()]
        assert lines, "no resolve line was logged; the absence checks below would be vacuous"
        assert "outcome=matched matched_districts=1 configs_with_domains=3/5" in lines[-1]
        for probe in (self.SECRET, "someone.private", "sd48.bc.ca"):
            assert probe not in caplog.text, f"{probe!r} reached the log"

    def test_a_no_match_logs_no_match_and_still_no_domain(self, page: MagicMock, fixed_index, caplog) -> None:
        with caplog.at_level("INFO"):
            _page_at(page, "someone@private-domain.example.com")

        assert "outcome=no_match matched_districts=0" in caplog.text
        assert "private-domain.example.com" not in caplog.text

    def test_an_invalid_address_logs_invalid_and_never_echoes_it(self, page: MagicMock, fixed_index, caplog) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        _field(view, identity.EMAIL_LABEL).value = "wildly wrong@@thing"

        with caplog.at_level("INFO"):
            _button(view, identity.CONTINUE_LABEL).on_click(None)

        assert "outcome=invalid matched_districts=0" in caplog.text
        assert "wildly wrong" not in caplog.text

    def test_the_escape_logs_the_unfiltered_outcome(self, page: MagicMock, fixed_index, caplog) -> None:
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)

        with caplog.at_level("INFO"):
            _button(view, identity.SKIP_LABEL).on_click(None)

        # "unscoped", not the retired "show_all" — the word named a UI row that no longer
        # exists, and a log vocabulary that outlives its surface misleads whoever reads it.
        assert "outcome=unscoped matched_districts=0" in caplog.text

    def test_the_gate_line_carries_a_bounded_reason(self, page: MagicMock, isolated_user_profile, caplog) -> None:
        from src.ui_flet import shell

        isolated_user_profile.mkdir(parents=True, exist_ok=True)
        (isolated_user_profile / "config.json").write_text(
            json.dumps({"identity_email": self.SECRET, "setup_completed": True}), encoding="utf-8"
        )

        with caplog.at_level("INFO"):
            shell.main(page)

        assert "identity gate: shown=False reason=setup-complete" in caplog.text
        for probe in (self.SECRET, "someone.private", "sd48.bc.ca"):
            assert probe not in caplog.text


# --------------------------------------------------------------------------- #
# 6. The Settings section — changeable AND clearable                           #
# --------------------------------------------------------------------------- #
def _settings_tree(page: MagicMock, monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> ft.Control:
    from src.ui_flet.screens.setup import build_setup

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))
    return build_setup(page)


def _completed(**over: object) -> AppConfig:
    values: dict[str, object] = {
        "input_dir": "/in",
        "output_dir": "/out",
        "sis_type": "myedbc",
        "setup_completed": True,
        "load_state": ConfigLoadState.LOADED,
    }
    values.update(over)
    return AppConfig(**values)  # type: ignore[arg-type]


class TestTheSettingsSection:
    def test_a_stored_address_is_shown_plainly(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        from src.ui_flet.screens import setup

        tree = _settings_tree(page, monkeypatch, _completed(identity_email="admin@sd48.bc.ca"))

        text = _all_text(tree)
        assert setup.IDENTITY_TITLE in text
        assert "admin@sd48.bc.ca" in text

    def test_a_hand_edited_stored_value_is_never_echoed(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """S3-gate carry-forward #1, at the surface that renders it.

        The file is untrusted input and the load-time check validates the TYPE, not the
        shape — so markup in a profile must read as UNANSWERED, not be painted back.
        """
        poison = "<script>alert(1)</script>@sd48.bc.ca"

        tree = _settings_tree(page, monkeypatch, _completed(identity_email=poison))

        text = _all_text(tree)
        assert poison not in text
        assert "<script>" not in text
        from src.ui_flet.screens import setup

        assert setup.IDENTITY_NONE in text or setup.IDENTITY_FIELD_LABEL in text

    def test_changing_the_address_persists_it_and_names_the_district(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        from src.ui_flet.screens import setup

        cfg = _completed(identity_email="old@sd74.bc.ca")
        tree = _settings_tree(page, monkeypatch, cfg)
        _button(tree, setup.IDENTITY_CHANGE_LABEL).on_click(None)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = "new@sd48.bc.ca"

        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        assert cfg.identity_email == "new@sd48.bc.ca"
        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_email"] == "new@sd48.bc.ca"
        assert "Sea to Sky" in _all_text(tree), "the same resolution runs inline"

    def test_an_invalid_change_persists_nothing_and_never_echoes_the_value(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        from src.ui_flet.screens import setup

        cfg = _completed(identity_email="good@sd48.bc.ca")
        cfg.save()
        tree = _settings_tree(page, monkeypatch, cfg)
        _button(tree, setup.IDENTITY_CHANGE_LABEL).on_click(None)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = "nonsense value"

        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_email"] == "good@sd48.bc.ca", "a refused edit changed the stored value"
        error = _error_texts(tree)
        assert error, "no inline error was painted; the echo assertion would be vacuous"
        assert "nonsense value" not in error, "the error message must not echo the value back"

    def test_blank_clears_all_three_fields_and_re_arms_the_ask(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Clearing must not WEDGE the states: with the prompt still dismissed there would
        be no stored identity and no surface willing to ask for one again."""
        from src.ui_flet.screens import setup

        cfg = _completed(identity_email="admin@sd48.bc.ca", identity_sd_number="48", identity_prompt_dismissed=True)
        cfg.save()
        tree = _settings_tree(page, monkeypatch, cfg)
        _button(tree, setup.IDENTITY_CHANGE_LABEL).on_click(None)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = "   "

        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_email"] == ""
        assert stored["identity_sd_number"] == ""
        assert stored["identity_prompt_dismissed"] is False

    def test_clearing_also_unlinks_the_quarantine_copies(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """S3-gate carry-forward #2 — "blank clears" is only true if the copies go too.

        `config.corrupt-*.json` holds a byte-for-byte duplicate of whatever `config.json`
        contained when it was taken, and nothing else ever prunes them.
        """
        from src.ui_flet.screens import setup

        cfg = _completed(identity_email="admin@sd48.bc.ca")
        cfg.save()
        quarantine = isolated_user_profile / "config.corrupt-20260728-101500.json"
        quarantine.write_text(json.dumps({"identity_email": "admin@sd48.bc.ca"}), encoding="utf-8")
        assert quarantine.exists(), "the fixture itself must exist or the assertion is vacuous"

        tree = _settings_tree(page, monkeypatch, cfg)
        _button(tree, setup.IDENTITY_CHANGE_LABEL).on_click(None)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = ""
        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        assert not quarantine.exists(), "the address survived in a quarantine copy"
        assert not list(isolated_user_profile.glob("config.corrupt-*.json"))
        # The side effect is STATED — and stated accurately: this run really did delete a copy.
        assert setup.IDENTITY_CLEARED_WITH_COPIES_NOTE in _all_text(tree)

    def test_a_blank_save_with_nothing_stored_keeps_the_copies_and_claims_nothing(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The data-loss row, at the surface: an empty Save on an empty field destroys nothing.

        The admin most likely to own a settings-recovery copy is the one whose settings file
        went unreadable — who may never have answered the identity question. Sweeping their
        copies because they pressed Save on a blank field would delete real data to erase a
        value that was never there.
        """
        from src.ui_flet.screens import setup

        cfg = _completed()
        cfg.save()
        quarantine = isolated_user_profile / "config.corrupt-20260728-101500.json"
        quarantine.write_text(json.dumps({"input_dir": "C:/gde/in"}), encoding="utf-8")

        # With nothing on file the section opens already in edit mode (there is nothing to
        # display), so the blank field and its Save are on screen from the start.
        tree = _settings_tree(page, monkeypatch, cfg)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = ""
        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        assert quarantine.exists(), "an empty Save deleted the admin's settings-recovery copy"
        text = _all_text(tree)
        assert setup.IDENTITY_CLEARED_NOTE in text
        assert "including the older copies" not in text, "claimed a deletion that never happened"

    def test_a_locked_copy_is_reported_honestly_in_the_note(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """When every unlink fails, the note must NOT say the copies are gone."""
        from src.ui_flet.screens import setup

        cfg = _completed(identity_email="admin@sd48.bc.ca")
        cfg.save()
        (isolated_user_profile / "config.corrupt-20260728-101500.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "unlink", lambda self, **kw: (_ for _ in ()).throw(OSError("locked")))

        tree = _settings_tree(page, monkeypatch, cfg)
        _button(tree, setup.IDENTITY_CHANGE_LABEL).on_click(None)
        _field(tree, setup.IDENTITY_FIELD_LABEL).value = ""
        _button(tree, setup.IDENTITY_SAVE_LABEL).on_click(None)

        text = _all_text(tree)
        assert "couldn't remove 1 older copy" in text
        assert "including the older copies" not in text

    def test_the_section_uses_no_banned_vocabulary(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """Both halves: every ``IDENTITY_*`` constant, AND the tree as actually rendered.

        The constants alone would miss a string written inline in the render function; the
        rendered tree alone would miss a constant only reachable from a state this test
        does not drive (the refused-save note, for one).
        """
        from src.ui_flet.screens import setup

        section_text = "\n".join(
            value for name, value in vars(setup).items() if name.startswith("IDENTITY_") and isinstance(value, str)
        )
        assert setup.IDENTITY_REFUSED_NOTE in section_text, "the sweep missed a constant; it may be vacuous"
        _assert_no_banned_vocabulary(section_text, "the Settings identity constants")

        # Scoped to the identity CARD, not the whole scroll: the Delivery section talks
        # about a stored credential and the Schedule section about a Windows account,
        # because those features genuinely have both. The ban is about not dressing a list
        # filter in that language — it is not a repo-wide word ban.
        tree = _settings_tree(page, monkeypatch, _completed(identity_email="admin@sd48.bc.ca"))
        card = next(c for c in tree.controls if setup.IDENTITY_TITLE in _all_text(c))
        _assert_no_banned_vocabulary(_all_text(card), "the rendered Settings identity section")

    def test_the_section_adds_no_second_filled_primary(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Settings is a stack of sections, each with at most ONE filled action. The
        identity section rides the existing saves rather than adding a fourth."""
        from src.ui_flet.screens import setup

        with_identity = _settings_tree(page, monkeypatch, _completed(identity_email="admin@sd48.bc.ca"))
        filled = [c for c in _iter_controls(with_identity) if isinstance(c, ft.FilledButton)]
        labels = [c.content for c in filled]

        assert setup.IDENTITY_SAVE_LABEL not in labels


# --------------------------------------------------------------------------- #
# 7. The Help echo                                                             #
# --------------------------------------------------------------------------- #
class TestTheHelpEcho:
    def test_a_valid_stored_address_is_echoed_with_a_route_to_settings(self, page: MagicMock) -> None:
        from src.ui_flet.screens import help as help_screen

        hops: list[str] = []
        view = build_help(page, app_config=AppConfig(identity_email="admin@sd48.bc.ca"), on_navigate=hops.append)

        text = _all_text(view)
        assert help_screen.IDENTITY_TITLE in text
        assert "admin@sd48.bc.ca" in text
        _button(view, help_screen.IDENTITY_CHANGE_LABEL).on_click(None)
        assert hops == ["setup"]

    def test_it_does_not_promise_the_address_is_used_in_support_mail(self, page: MagicMock) -> None:
        """Flag 6: the mailto is subject-only, so the copy must not imply otherwise."""
        from src.ui_flet.screens import help as help_screen

        assert "don't send it anywhere" in help_screen.IDENTITY_DETAIL
        _assert_no_banned_vocabulary(help_screen.IDENTITY_DETAIL, "the Help echo")

    def test_the_support_mailto_is_untouched(self, page: MagicMock) -> None:
        """The echo must not have quietly grown a body or a recipient parameter."""
        from src.ui_flet import about
        from src.ui_flet.screens.help import SUPPORT_EMAIL

        mailto = about.support_mailto(SUPPORT_EMAIL, "3.9.0", "SD48 - Sea to Sky School District")

        assert "admin@" not in mailto
        assert "body=" not in mailto

    def test_the_echo_renders_without_a_router(self, page: MagicMock) -> None:
        """Help owns no lifecycle: without ``on_navigate`` the card still renders, minus
        the shortcut. A surface that needed a router to show a fact would be a fact hidden
        by a plumbing detail."""
        from src.ui_flet.screens import help as help_screen

        view = build_help(page, app_config=AppConfig(identity_email="admin@sd48.bc.ca"))

        text = _all_text(view)
        assert help_screen.IDENTITY_TITLE in text
        assert "admin@sd48.bc.ca" in text
        assert help_screen.IDENTITY_CHANGE_LABEL not in text

    def test_no_card_when_nothing_is_stored(self, page: MagicMock) -> None:
        from src.ui_flet.screens import help as help_screen

        view = build_help(page, app_config=AppConfig())

        assert help_screen.IDENTITY_TITLE not in _all_text(view)

    def test_no_card_for_a_hand_edited_stored_value(self, page: MagicMock) -> None:
        from src.ui_flet.screens import help as help_screen

        view = build_help(page, app_config=AppConfig(identity_email="adm‮in@sd48.bc.ca"))

        assert help_screen.IDENTITY_TITLE not in _all_text(view)
        assert "‮" not in _all_text(view)


# --------------------------------------------------------------------------- #
# 8. Fail-open over a broken index                                             #
# --------------------------------------------------------------------------- #
def test_an_unreadable_catalog_still_reaches_a_state_with_a_way_forward(page: MagicMock, monkeypatch) -> None:
    monkeypatch.setattr(identity, "district_domain_index", lambda **_kw: {})

    view = _page_at(page, "admin@sd48.bc.ca")

    assert identity.NO_MATCH_HEADLINE in _all_text(view), "an empty index must degrade to no-match, not to a dead end"
    assert _button(view, identity.GET_STARTED_LABEL) is not None


def test_the_page_never_reads_the_real_user_profile(page: MagicMock, fixed_index, tmp_path: Path) -> None:
    """Building the page is a pure-ish act: it reads bundled YAML, never the settings file.

    The instance it writes to is handed in, so a test (or the shell) fully controls it.
    """
    cfg = AppConfig()
    build_identity(page, app_config=cfg, on_enter=lambda: None)

    assert cfg.identity_email == ""


# --------------------------------------------------------------------------- #
# 9. The blur must not rebuild the card (v3.10.1 regression)                    #
# --------------------------------------------------------------------------- #
class TestABlurNeverRebuildsTheCard:
    """The launch page's Continue button did nothing for two releases. Pinned here.

    **The bug.** Clicking Continue blurs the email field first. ``_on_email_blur`` called
    ``_paint()``, which replaced ``body.controls`` — and with it the very button the mouse
    was pressing. Flutter delivers a tap to the widget that received the pointer-down; that
    widget no longer existed, so the press was discarded. Continue did nothing, for every
    admin, on the first screen of the product. Pressing Enter always worked, because
    ``on_submit`` fires with no blur first — that asymmetry is the bug's signature.

    **Why the existing tests were green.** Every one of them calls ``on_click(None)``
    directly, which is not a click: it skips focus, blur, the gesture arena and the frame
    the rebuild happened in. A scripted browser click passed too, because a synthetic
    down+up lands in a single frame. Only a HELD click reproduced it.

    **So this pins the structural invariant instead of the gesture**, which is the part a
    unit test can actually hold: a blur may not swap out the controls the card is built
    from. The gesture-level proof lives in the measured hold table in
    ``identity._on_email_blur``'s docstring, which a test cannot replay in-process.
    """

    def _card_controls(self, view):  # noqa: ANN001, ANN202 - untyped Flet tree
        """The identity of the control list the Continue button lives in."""
        return [c for c in _iter_controls(view) if isinstance(c, ft.Column) and self._holds_continue(c)][0].controls

    def _holds_continue(self, column) -> bool:  # noqa: ANN001 - untyped Flet tree
        return any(
            isinstance(c, ft.FilledButton) and c.content == identity.CONTINUE_LABEL for c in (column.controls or [])
        )

    def test_a_blur_on_a_VALID_address_leaves_the_button_in_place(self, page: MagicMock, fixed_index) -> None:
        """The path every admin takes: type a good address, then reach for Continue."""
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)
        button_before = _button(view, identity.CONTINUE_LABEL)
        controls_before = self._card_controls(view)

        field.value = "roster.admin@sd48.bc.ca"
        field.on_change(None)
        field.on_blur(None)

        assert _button(view, identity.CONTINUE_LABEL) is button_before, (
            "the blur replaced the Continue button — a click held across it is discarded"
        )
        assert self._card_controls(view) is controls_before, "the blur rebuilt the card the button lives in"

    def test_a_blur_on_an_INVALID_address_also_leaves_the_button_in_place(self, page: MagicMock, fixed_index) -> None:
        """The branch that DOES repaint still may not rebuild — it fills a persistent slot.

        Asserted on the CONTROLS LIST, not on the button object. ``continue_btn`` is built
        once and re-used across repaints, so its identity survives a full ``_paint()`` and
        an identity check on it alone passes against the buggy code — a test that cannot
        fail. The card's controls list is what the rebuild actually replaces.
        """
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)
        controls_before = self._card_controls(view)

        field.value = "notanemail"
        field.on_change(None)
        field.on_blur(None)

        assert self._card_controls(view) is controls_before, "the invalid-address blur rebuilt the card"
        # The ask state is identified by its LABEL now — the placeholder that used to mark
        # it retired on 2026-08-05.
        assert identity.EMAIL_LABEL in _all_text(view), "still the ask state"

    def test_the_error_STILL_appears_on_blur(self, page: MagicMock, fixed_index) -> None:
        """The positive twin, and the one that stops this suite going vacuous.

        Every assertion above is satisfied by a blur handler that does nothing whatsoever.
        This one fails unless the handler still does its job: a bad address, blurred, shows
        the format error.
        """
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)

        field.value = "notanemail"
        field.on_change(None)
        field.on_blur(None)

        assert _error_texts(view), "the blur no longer reports a bad address at all"

    def test_the_error_CLEARS_when_the_address_is_corrected(self, page: MagicMock, fixed_index) -> None:
        """The other half of the slot's contract — a stale error is its own bug."""
        view = build_identity(page, app_config=AppConfig(), on_enter=lambda: None)
        field = _field(view, identity.EMAIL_LABEL)
        field.value = "notanemail"
        field.on_blur(None)
        assert _error_texts(view)

        field.value = "roster.admin@sd48.bc.ca"
        field.on_blur(None)

        assert not _error_texts(view), "the error survived a correction"

    def test_the_district_number_blur_leaves_GET_STARTED_in_place(self, page: MagicMock, fixed_index) -> None:
        """The same trap one screen along: the SD field sits directly above Get started."""
        view = _page_at(page, "someone@nowhere.example.org")
        sd_field = _field(view, identity.SD_LABEL)
        button_before = _button(view, identity.GET_STARTED_LABEL)

        sd_field.value = "48"
        sd_field.on_blur(None)

        assert _button(view, identity.GET_STARTED_LABEL) is button_before
        assert "Sea to Sky" in _all_text(view), "the note still resolves the number (positive twin)"
