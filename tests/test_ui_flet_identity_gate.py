"""``needs_identity`` — the launch-page predicate truth table (plan 0038 S3).

The identity page is IDENTIFICATION for list-scoping, never a gate that can fail closed:
there are no accounts, nothing is unlocked, and every path leads INTO the app. This
predicate therefore only ever answers "is it worth ASKING right now?" — and it answers
False in every state where asking would be wrong:

* UNREADABLE settings (G2) — we cannot persist an answer, so asking would trap the admin
  in a question we can't record;
* an install that already finished setup — it gets the dismissible Home card (S4b), never
  a launch page in front of a working sync;
* an identity already on file — asked and answered.

Mirrors ``nav.needs_setup``'s shape deliberately (same module family, same
``settings_unreadable()`` guard), so the two gates cannot drift apart.

**S4b adds the other three decisions the Home cards rest on** — ``needs_identity_prompt``
(the same question asked of a CONFIGURED install), ``matched_excludes_saved`` (the G3
mismatch rule) and ``unmapped_sd_number`` (the durable not-listed rule). All three are
pure, total, and tested here rather than through the view, because the view is
coverage-omitted glue and these are the rules.
"""

from __future__ import annotations

import itertools

import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.identity_gate import (
    matched_excludes_saved,
    needs_identity,
    needs_identity_prompt,
    stored_identity_domain,
    unmapped_sd_number,
)
from src.ui_flet.nav import needs_setup


def _cfg(
    *,
    load_state: ConfigLoadState = ConfigLoadState.LOADED,
    setup_completed: bool = False,
    identity_email: str = "",
) -> AppConfig:
    return AppConfig(
        input_dir="/in",
        output_dir="/out",
        sis_type="sd48myedbc",
        setup_completed=setup_completed,
        identity_email=identity_email,
        load_state=load_state,
    )


# --------------------------------------------------------------------------- #
# The truth table — all three inputs, both values each                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("unreadable", "completed", "email", "expected", "why"),
    [
        (False, False, "", True, "the only True row: readable, unfinished setup, no identity"),
        (False, False, "admin@sd48.bc.ca", False, "already answered"),
        (False, False, "   ", True, "a whitespace-only stored value is NOT an answer"),
        (False, True, "", False, "a configured install gets the Home card, never a launch gate"),
        (False, True, "admin@sd48.bc.ca", False, "configured and answered"),
        (True, False, "", False, "G2 — UNREADABLE can never be asked (the answer could not be saved)"),
        (True, True, "", False, "UNREADABLE outranks everything"),
        (True, False, "admin@sd48.bc.ca", False, "UNREADABLE outranks a stored value too"),
    ],
)
def test_needs_identity_truth_table(unreadable, completed, email, expected, why):
    cfg = _cfg(
        load_state=ConfigLoadState.UNREADABLE if unreadable else ConfigLoadState.LOADED,
        setup_completed=completed,
        identity_email=email,
    )
    assert needs_identity(cfg) is expected, why


def test_absent_config_a_genuinely_fresh_install_is_asked():
    """ABSENT (no ``config.json`` at all) is the archetypal first launch — ask."""
    assert needs_identity(AppConfig(load_state=ConfigLoadState.ABSENT)) is True


def test_unreadable_never_asks_across_every_other_combination():
    """G2 as a SWEEP, not a sample: UNREADABLE ⟹ False for every other input combination."""
    for completed, email in itertools.product((False, True), ("", "   ", "admin@sd48.bc.ca")):
        cfg = _cfg(load_state=ConfigLoadState.UNREADABLE, setup_completed=completed, identity_email=email)
        assert needs_identity(cfg) is False, f"UNREADABLE asked with {completed=} {email=}"


def test_identity_alone_never_completes_setup():
    """Storing an identity must not make an unconfigured install look configured.

    ``identity_*`` is advisory metadata about WHO is looking after the sync; it is not a
    setup step. If it leaked into the finish-line, an admin who typed an email at the
    launch page would land on a dashboard for a sync that was never configured.
    """
    cfg = AppConfig(identity_email="admin@sd48.bc.ca", identity_sd_number="48", load_state=ConfigLoadState.LOADED)

    assert cfg.has_completed_setup() is False
    assert cfg.is_complete() is False
    assert needs_setup(cfg) is True


def test_the_two_gates_agree_on_the_unreadable_row():
    """``needs_identity`` and ``needs_setup`` share the ``settings_unreadable()`` guard.

    Both suppress under UNREADABLE for the same reason (we know this is not a fresh
    install, and we cannot record an answer). Pinned so a future edit to one is a visible
    divergence from the other rather than a silent one.
    """
    cfg = _cfg(load_state=ConfigLoadState.UNREADABLE)

    assert needs_identity(cfg) is False
    assert needs_setup(cfg) is False


# --------------------------------------------------------------------------- #
# S4b — the Home card predicate                                                #
# --------------------------------------------------------------------------- #
def _card_cfg(
    *,
    load_state: ConfigLoadState = ConfigLoadState.LOADED,
    setup_completed: bool = True,
    identity_email: str = "",
    identity_prompt_dismissed: bool = False,
) -> AppConfig:
    return AppConfig(
        input_dir="/in",
        output_dir="/out",
        sis_type="sd48myedbc",
        setup_completed=setup_completed,
        identity_email=identity_email,
        identity_prompt_dismissed=identity_prompt_dismissed,
        load_state=load_state,
    )


@pytest.mark.parametrize(
    ("unreadable", "completed", "dismissed", "email", "expected", "why"),
    [
        (False, True, False, "", True, "the only True row: a working install we have never asked"),
        (False, True, False, "admin@sd48.bc.ca", False, "asked and answered"),
        (False, True, False, "   ", True, "a whitespace-only stored value is NOT an answer"),
        (False, True, False, "<script>@sd48.bc.ca", True, "a hand-edited value reads UNANSWERED (never echoed)"),
        (False, True, True, "", False, "dismissal is permanent — Settings is the way back, not the card"),
        (False, True, True, "admin@sd48.bc.ca", False, "dismissed AND answered"),
        (False, False, False, "", False, "setup unfinished — the LAUNCH PAGE asks, so the card must not"),
        (True, True, False, "", False, "G2 — UNREADABLE can never be asked (the answer could not be saved)"),
        (True, True, True, "", False, "UNREADABLE outranks the dismissal flag too"),
        (True, False, False, "", False, "UNREADABLE outranks everything"),
    ],
)
def test_needs_identity_prompt_truth_table(unreadable, completed, dismissed, email, expected, why):
    cfg = _card_cfg(
        load_state=ConfigLoadState.UNREADABLE if unreadable else ConfigLoadState.LOADED,
        setup_completed=completed,
        identity_email=email,
        identity_prompt_dismissed=dismissed,
    )
    assert needs_identity_prompt(cfg) is expected, why


def test_the_two_asks_are_never_BOTH_true_for_one_settings_state():
    """The launch page and the Home card can never fire off the SAME settings state.

    The two predicates differ on exactly one input (``has_completed_setup()``), so this is
    structural rather than coincidental — but it is the property the whole "never a gate in
    front of a working sync" promise rests on, so it is swept rather than sampled.

    Be precise about the scope, because the looser reading ("nobody is ever asked twice")
    is FALSE: someone who declines at the launch page stores nothing, and finishing setup
    CHANGES the settings state, so they legitimately meet the ask once more on Home — where
    it is dismissible forever. That transition is an owner-facing product judgment (tracked
    in the ROADMAP), not something this sweep claims to prevent.
    """
    for state, completed, dismissed, email in itertools.product(
        (ConfigLoadState.LOADED, ConfigLoadState.ABSENT, ConfigLoadState.UNREADABLE),
        (False, True),
        (False, True),
        ("", "   ", "admin@sd48.bc.ca"),
    ):
        cfg = _card_cfg(
            load_state=state, setup_completed=completed, identity_email=email, identity_prompt_dismissed=dismissed
        )
        assert not (needs_identity(cfg) and needs_identity_prompt(cfg)), (
            f"both asks fired for one settings state: {state=} {completed=} {dismissed=} {email=}"
        )


def test_the_dismissal_flag_is_the_ONLY_new_input():
    """The positive twin of the row above: with everything else held fixed, the flag decides.

    Without this, "dismissed ⇒ False" is equally satisfied by a predicate that returns
    False for some unrelated reason on that row.
    """
    asked = _card_cfg(identity_prompt_dismissed=False)
    dismissed = _card_cfg(identity_prompt_dismissed=True)

    assert needs_identity_prompt(asked) is True
    assert needs_identity_prompt(dismissed) is False


# --------------------------------------------------------------------------- #
# S4b — the G3 mismatch rule                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("saved", "configs", "expected", "why"),
    [
        ("sd48myedbc", (), False, "no match at all — there is nothing to disagree with"),
        ("", ("sd48myedbc",), False, "nothing configured — nothing to differ FROM"),
        ("   ", ("sd48myedbc",), False, "a blank district is not a disagreement"),
        ("sd48myedbc", ("sd48myedbc",), False, "the address matches what this install runs"),
        ("sd48myedbc", ("sd51myedbc",), True, "the ONE True shape: matched, and not what is configured"),
        ("SD48MyEdBC", ("sd48myedbc",), False, "case-normalised on the saved side"),
        ("sd48myedbc", (" SD48MYEDBC ",), False, "case- and whitespace-normalised on the matched side"),
        ("sd51myedbc", ("sd51myedbc", "sd51attendance"), False, "saved is among several — SD51's live shape"),
        ("myedbc", ("sd51myedbc", "sd51attendance"), True, "several matched, none of them configured"),
    ],
)
def test_matched_excludes_saved_truth_table(saved, configs, expected, why):
    assert matched_excludes_saved(saved, configs) is expected, why


# --------------------------------------------------------------------------- #
# S4b — the durable not-listed rule                                            #
# --------------------------------------------------------------------------- #
BUNDLED = ("myedbc", "mbp_all", "sd48myedbc", "sd51myedbc", "sd51attendance", "sd74myedbc")


@pytest.mark.parametrize(
    ("stored", "config_ids", "expected", "why"),
    [
        ("", BUNDLED, "", "nothing was ever told to us"),
        ("   ", BUNDLED, "", "whitespace is not a district number"),
        ("not a number", BUNDLED, "", "no digits at all resolves to nothing, never to everything"),
        ("48", BUNDLED, "", "we HAVE SD48 — never tell an admin we are building what we ship"),
        ("SD48", BUNDLED, "", "the same, however they typed it"),
        ("99", BUNDLED, "99", "the card's reason to exist"),
        ("SD99", BUNDLED, "99", "normalised to bare digits for the copy"),
        ("099", BUNDLED, "99", "leading zeros dropped — 099 and 99 are one district"),
        ("4", BUNDLED, "4", "SD4 must NOT be served by sd48myedbc (a prefix match would hide it)"),
        ("99", (), "99", "an empty catalog cannot serve SD99 either"),
    ],
)
def test_unmapped_sd_number_truth_table(stored, config_ids, expected, why):
    cfg = AppConfig(identity_sd_number=stored, load_state=ConfigLoadState.LOADED)
    assert unmapped_sd_number(cfg, config_ids) == expected, why


def test_unmapped_sd_number_is_total_over_a_hand_edited_value():
    """``config.json`` is hand-editable and this reader must never fail closed.

    ``AppConfig._value_fits`` should keep a non-``str`` out of the field, but that is a
    guarantee made in a different module — and a directly-constructed instance never went
    through a load at all. Anything unusable means "we were told nothing".
    """
    cfg = AppConfig(load_state=ConfigLoadState.LOADED)
    cfg.identity_sd_number = 99  # type: ignore[assignment]

    assert unmapped_sd_number(cfg, BUNDLED) == ""


# --------------------------------------------------------------------------- #
# stored_identity_domain — the ONE reduction every filtered picker consumes     #
# (plan 0038 S5)                                                                #
# --------------------------------------------------------------------------- #
class TestStoredIdentityDomain:
    """The stored address, reduced to the thing the catalog layer actually compares.

    It exists so the four filtered pickers do not each re-spell
    ``extract_domain(normalize_email(stored_identity_email(cfg)))`` — three chances per call
    site to drop the re-validation or the normalisation, and four places to fix a rule that
    should live once. TOTAL and fail-OPEN: anything unusable reduces to ``""``, which the
    filter reads as "no identity" and answers with the FULL list.
    """

    def test_a_stored_address_reduces_to_its_domain(self):
        assert stored_identity_domain(_cfg(identity_email="admin@sd48.bc.ca")) == "sd48.bc.ca"

    def test_nothing_stored_reduces_to_nothing(self):
        assert stored_identity_domain(_cfg(identity_email="")) == ""

    def test_case_and_padding_are_normalised(self):
        assert stored_identity_domain(_cfg(identity_email="  Admin@SD48.BC.CA  ")) == "sd48.bc.ca"

    def test_the_dns_root_dot_is_stripped(self):
        assert stored_identity_domain(_cfg(identity_email="admin@sd48.bc.ca.")) == "sd48.bc.ca"

    def test_plus_addressing_does_not_disturb_the_domain(self):
        assert stored_identity_domain(_cfg(identity_email="admin+roster@sd48.bc.ca")) == "sd48.bc.ca"

    def test_a_value_that_FAILS_read_time_validation_reduces_to_nothing(self):
        """``config.json`` is hand-editable and the load check is TYPE-only. A value that
        cannot pass the boundary validator means UNANSWERED — and an unanswered identity must
        widen the list, never narrow it on a domain nobody typed."""
        assert stored_identity_domain(_cfg(identity_email="admin at sd48 dot ca")) == ""
        assert stored_identity_domain(_cfg(identity_email="two@@sd48.bc.ca")) == ""

    def test_an_UNREADABLE_profile_scopes_NOTHING(self):
        """G2, applied to the filter and for a sharper reason than the ask predicates have.

        Under UNREADABLE nothing on the instance came off disk — including ``sis_type``, so
        the saved-district escape that normally keeps a working install's own mapping visible
        is gone too. An identity we could not read PLUS no saved district to fall back on is
        the one combination that could hide the right district, so we do not narrow at all.
        """
        cfg = _cfg(load_state=ConfigLoadState.UNREADABLE, identity_email="admin@sd48.bc.ca")

        assert stored_identity_domain(cfg) == ""

    def test_the_UNREADABLE_probe_is_not_vacuous(self):
        """Positive twin — the SAME address on a readable profile does reduce."""
        cfg = _cfg(load_state=ConfigLoadState.LOADED, identity_email="admin@sd48.bc.ca")

        assert stored_identity_domain(cfg) == "sd48.bc.ca"

    def test_a_non_string_stored_value_reduces_to_nothing(self):
        cfg = _cfg()
        cfg.identity_email = 42  # type: ignore[assignment]

        assert stored_identity_domain(cfg) == ""


def test_module_imports_no_flet():
    """PURE + COUNTED: the gate is importable with no UI toolkit in the graph."""
    import ast
    import inspect

    import src.ui_flet.identity_gate as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "flet" not in imported
