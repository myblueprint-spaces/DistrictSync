r"""The launch page's PURE matching layer (plan 0038 S4a) — the S4a→S5 seam.

Written RED-FIRST per `docs/claugentic-CHARTER.md` → "Pure predicate / primitive modules
whose semantics the spec DECIDES": every judgment call below (exact-vs-suffix matching,
what an empty domain does, which order matches come back in, what counts as an SD number)
is a DECISION the spec makes, so it is stated here as a requirement before any code can
quietly make it.

**Independence, stated honestly:** no sub-agent tool was available in this session, so
these tests are SELF-AUTHORED, not written by an independent test-author spawn. That is
the weaker form (see the charter's independence caveat). The compensation is a
perturb-and-restore pass over the rules below — each broken in the source, observed RED
here, then restored: exact-equality swapped for `endswith` (the subdomain rows), the
resolver re-sorted (the caller-order row), the SD boundary `(?!\d)` dropped (the
SD4-is-not-SD48 row), and the read-time re-validation removed (the hand-edited table).
Named inline rather than in a report, so the evidence lives where the assertions do.

The one rule that carries real risk: **matching is EXACT, case-normalised equality.**
Suffix matching over-matches (`mail.sd48.bc.ca` would scope an admin into SD48 who is not
in SD48), and over-matching is the dangerous direction under fail-open, because the whole
design guarantees a no-match yields the FULL list. Pinned in both directions.
"""

from __future__ import annotations

import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.identity_gate import (
    MatchOutcome,
    can_continue,
    gate_reason,
    matched_state,
    resolve_domain,
    resolve_sd_number,
    sd_number_digits,
    stored_identity_email,
)

# A miniature catalog shaped like the real one: two claimed districts sharing a domain
# (SD51 + its attendance tier — the LIVE matched-several case), one claimed alone, and
# two UNCLAIMED configs (the generic base + a myBlueprint+ tier carry no domains).
CATALOG: dict[str, tuple[str, ...]] = {
    "myedbc": (),
    "mbp_all": (),
    "sd48myedbc": ("sd48.bc.ca",),
    "sd51myedbc": ("sd51.bc.ca",),
    "sd51attendance": ("sd51.bc.ca",),
}


# --------------------------------------------------------------------------- #
# resolve_domain — exact, case-normalised, TOTAL                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("domain", "expected", "why"),
    [
        ("sd48.bc.ca", ("sd48myedbc",), "the plain match"),
        ("SD48.BC.CA", ("sd48myedbc",), "case-normalised — the admin types what they like"),
        ("  sd48.bc.ca  ", ("sd48myedbc",), "surrounding whitespace is not a different domain"),
        ("sd51.bc.ca", ("sd51myedbc", "sd51attendance"), "one domain, two configs (the LIVE several case)"),
        ("mail.sd48.bc.ca", (), "a SUBDOMAIN is a different domain — never a match"),
        ("sd48.bc.c", (), "a truncated domain is not a prefix match"),
        ("xsd48.bc.ca", (), "a superstring is not a suffix match"),
        ("gmail.com", (), "an unknown domain matches nothing → the caller shows everything"),
        ("", (), "no domain at all can never match"),
        ("   ", (), "whitespace-only is no domain"),
        ("@", (), "hostile input is total, not an exception"),
    ],
)
def test_resolve_domain_table(domain: str, expected: tuple[str, ...], why: str) -> None:
    assert resolve_domain(domain, CATALOG) == expected, why


def test_resolve_domain_never_matches_an_unclaimed_config() -> None:
    """A config with NO domains can never be *matched* — it is shown by the unclaimed rule.

    The distinction is load-bearing: `()` means "claims nobody", never "claims everybody".
    """
    for domain in ("sd48.bc.ca", "sd51.bc.ca", "anything.example.com"):
        assert "myedbc" not in resolve_domain(domain, CATALOG)
        assert "mbp_all" not in resolve_domain(domain, CATALOG)


def test_resolve_domain_preserves_the_callers_order() -> None:
    """Results come back in the MAPPING's iteration order — the caller owns the order.

    `available_configs()` is sorted, so the real index is deterministic; making the
    primitive re-sort would take that choice away from S5's catalog layer.
    """
    reversed_catalog = dict(reversed(list(CATALOG.items())))

    assert resolve_domain("sd51.bc.ca", CATALOG) == ("sd51myedbc", "sd51attendance")
    assert resolve_domain("sd51.bc.ca", reversed_catalog) == ("sd51attendance", "sd51myedbc")


def test_resolve_domain_normalises_the_config_side_too() -> None:
    """A stray uppercase/whitespace row in a user-dropped YAML still matches.

    The bundled rows are lowercase (the config validator enforces it), but a config in
    `~/.districtsync/mappings/` is not ours — and silently failing to match an admin's own
    district is the one failure this design makes unrepresentable.
    """
    assert resolve_domain("sd60.bc.ca", {"local": (" SD60.BC.CA ",)}) == ("local",)


def test_resolve_domain_is_total_over_hostile_values() -> None:
    """Never raises — the consumers fail OPEN, so an exception here would be a trap."""
    hostile = {"weird": ("‮", "", "  ", "sd48.bc.ca")}
    assert resolve_domain("sd48.bc.ca", hostile) == ("weird",)
    assert resolve_domain("‮", {"a": ()}) == ()
    assert resolve_domain("x" * 10_000, CATALOG) == ()


# --------------------------------------------------------------------------- #
# matched_state — the page-state discriminant                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("domain", "outcome", "configs"),
    [
        ("sd48.bc.ca", MatchOutcome.MATCHED_ONE, ("sd48myedbc",)),
        ("sd51.bc.ca", MatchOutcome.MATCHED_SEVERAL, ("sd51myedbc", "sd51attendance")),
        ("gmail.com", MatchOutcome.NO_MATCH, ()),
        ("", MatchOutcome.NO_MATCH, ()),
    ],
)
def test_matched_state_table(domain: str, outcome: MatchOutcome, configs: tuple[str, ...]) -> None:
    state = matched_state(domain, CATALOG)

    assert (state.outcome, state.configs) == (outcome, configs)


def test_matched_state_over_an_empty_catalog_is_no_match_not_an_error() -> None:
    """An unreadable catalog degrades to "we know of nobody" → the full list. Fail OPEN."""
    state = matched_state("sd48.bc.ca", {})

    assert state.outcome is MatchOutcome.NO_MATCH
    assert state.configs == ()


# --------------------------------------------------------------------------- #
# SD numbers — the no-match fallback path                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "digits"),
    [
        ("48", "48"),
        ("SD48", "48"),
        ("sd 48", "48"),
        ("#48", "48"),
        ("SD-48", "48"),
        ("048", "48"),
        ("0", "0"),
        ("", ""),
        ("   ", ""),
        ("no digits here", ""),
        ("48 (Sea to Sky)", "48"),
    ],
)
def test_sd_number_digits_table(raw: str, digits: str) -> None:
    assert sd_number_digits(raw) == digits


@pytest.mark.parametrize(
    ("raw", "expected", "why"),
    [
        ("48", ("sd48myedbc",), "the plain SD number"),
        ("SD48", ("sd48myedbc",), "the way an admin actually writes it"),
        ("51", ("sd51myedbc", "sd51attendance"), "a district with two tiers"),
        ("4", (), "SD4 is NOT SD48 — a digit boundary, never a prefix"),
        ("5", (), "SD5 is NOT SD51"),
        ("99", (), "no mapping for it yet → the not-listed path"),
        ("", (), "nothing typed matches nothing"),
        ("banana", (), "unparseable is a no-match, never a crash"),
    ],
)
def test_resolve_sd_number_table(raw: str, expected: tuple[str, ...], why: str) -> None:
    assert resolve_sd_number(raw, CATALOG) == expected, why


def test_resolve_sd_number_ignores_configs_that_are_not_sd_shaped() -> None:
    assert resolve_sd_number("48", {"myedbc": (), "mbp_all": (), "sd48myedbc": ()}) == ("sd48myedbc",)


# --------------------------------------------------------------------------- #
# can_continue — the Continue gate (setup_gates-style pure predicate)          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("typed", "expected"),
    [("", False), ("   ", False), ("\n\t", False), ("a", True), ("admin@sd48.bc.ca", True)],
)
def test_can_continue_table(typed: str, expected: bool) -> None:
    """Structural only — format validation happens on blur/submit, never while typing."""
    assert can_continue(typed) is expected


# --------------------------------------------------------------------------- #
# stored_identity_email — the READ-time boundary (S3 gate carry-forward #1)    #
# --------------------------------------------------------------------------- #
# `config.json` is hand-editable untrusted input, and `_value_fits` checks the TYPE, not
# the SHAPE — so a stored value must clear `validate_identity_email` before any surface
# renders it, and a failure means UNANSWERED (re-ask), never "echo it anyway".
HOSTILE_STORED_VALUES = [
    ("<script>alert(1)</script>@sd48.bc.ca", "markup smuggled into the local part"),
    ("admin@sd48.bc.ca<b>", "markup after a legitimate-looking address"),
    ("adm‮in@sd48.bc.ca", "a bidi override that re-orders what the admin sees"),
    ("adm​in@sd48.bc.ca", "a zero-width character"),
    ("admin\nsecond@sd48.bc.ca", "a smuggled newline (log-injection shaped)"),
    ("a" * 250 + "@sd48.bc.ca", "oversize — past the RFC 5321 ceiling"),
    ("admin@@sd48.bc.ca", "two @ signs"),
    ("admin@sd48", "no dot in the domain"),
    ("not an email at all", "free text"),
    ("   ", "whitespace only"),
    ("", "empty"),
]


@pytest.mark.parametrize(("stored", "why"), HOSTILE_STORED_VALUES)
def test_a_hand_edited_stored_value_reads_as_unanswered(stored: str, why: str) -> None:
    cfg = AppConfig(identity_email=stored, load_state=ConfigLoadState.LOADED)

    assert stored_identity_email(cfg) == "", why


@pytest.mark.parametrize("stored", [None, 42, ["admin@sd48.bc.ca"], {"a": 1}, b"admin@sd48.bc.ca"])
def test_a_NON_STRING_stored_value_reads_as_unanswered_rather_than_raising(stored: object) -> None:
    """A non-``str`` raises ``AttributeError``/``TypeError`` inside the validator, not ``ValueError``.

    ``AppConfig._value_fits`` should keep a non-``str`` out of this field — but that is a
    guarantee made in a DIFFERENT module, and a directly-constructed instance never went
    through a load at all. A reader whose whole contract is "never fail closed" must not be
    hostage to someone else's invariant, so the catch is widened rather than relying on it.
    """
    cfg = AppConfig(load_state=ConfigLoadState.LOADED)
    cfg.identity_email = stored  # type: ignore[assignment]  - deliberately bypassing the choke point

    assert stored_identity_email(cfg) == ""


def test_a_good_stored_value_reads_back_as_typed() -> None:
    """The POSITIVE twin: the read-time check is not simply refusing everything."""
    cfg = AppConfig(identity_email="  Admin.Person@SD48.bc.ca  ", load_state=ConfigLoadState.LOADED)

    assert stored_identity_email(cfg) == "Admin.Person@SD48.bc.ca"


def test_needs_identity_re_asks_for_a_hand_edited_stored_value() -> None:
    """The gate itself keys off the VALIDATED read, so a garbage value re-asks."""
    from src.ui_flet.identity_gate import needs_identity

    poisoned = AppConfig(identity_email="not an email at all", load_state=ConfigLoadState.LOADED)
    good = AppConfig(identity_email="admin@sd48.bc.ca", load_state=ConfigLoadState.LOADED)

    assert needs_identity(poisoned) is True
    assert needs_identity(good) is False


# --------------------------------------------------------------------------- #
# gate_reason — the PII-free ops trace                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cfg", "reason"),
    [
        (AppConfig(load_state=ConfigLoadState.UNREADABLE), "settings-unreadable"),
        (AppConfig(setup_completed=True, load_state=ConfigLoadState.LOADED), "setup-complete"),
        (AppConfig(identity_email="admin@sd48.bc.ca", load_state=ConfigLoadState.LOADED), "identity-on-file"),
        (AppConfig(identity_email="junk", load_state=ConfigLoadState.LOADED), "stored-value-unusable"),
        (AppConfig(load_state=ConfigLoadState.ABSENT), "no-identity"),
    ],
)
def test_gate_reason_table(cfg: AppConfig, reason: str) -> None:
    assert gate_reason(cfg) == reason


def test_gate_reason_is_a_bounded_vocabulary_that_never_carries_the_address() -> None:
    """The reason is logged verbatim, so it must be a fixed word — never a value."""
    address = "someone.private@sd48.bc.ca"
    cfg = AppConfig(identity_email=address, load_state=ConfigLoadState.LOADED)

    reason = gate_reason(cfg)

    assert reason == "identity-on-file"
    assert address not in reason
    assert "sd48.bc.ca" not in reason
