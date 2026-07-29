"""The launch page's decisions — should we ASK, and what does the answer resolve to?

PURE + COUNTED (no ``flet`` import, no I/O). Two families live here:

* **the gate** — ``needs_identity``/``gate_reason``/``stored_identity_email``, shaped like
  ``nav.needs_setup`` (one predicate, one ``AppConfig``, one boolean, the same
  ``settings_unreadable()`` honesty guard) so the two launch gates cannot drift apart;
* **the matching** — ``resolve_domain``/``matched_state``/``resolve_sd_number``, the
  **S4a→S5 seam**. S4a's launch page (``screens/identity.py``) builds the
  ``{config id: domains}`` index ad hoc for one page mount; S5 replaces that builder with
  ``mapping_catalog.filtered_catalog`` reading ``ConfigSummary.district_domains``. **The
  pure resolver below is the shared piece and does not change when that happens** — it
  takes the index as DATA, so swapping the source is a caller-side edit.

**What this gates, and what it emphatically is not.** The launch page is IDENTIFICATION —
it scopes a district list so the highest-consequence wrong click in the product (picking
the wrong district, which ships a wrong roster) is harder to make. It is NOT
authentication: there are no accounts, nothing is unlocked, every mapping ships in the
executable regardless, and every path — a match, no match, a typo, a skip, a crash in the
identity layer itself — leads INTO the app. So nothing here can withhold anything, and
nothing here may fail closed.

**Matching is EXACT, case-normalised equality — never subdomain, suffix, or wildcard.**
``mail.sd48.bc.ca`` does NOT match ``sd48.bc.ca``. Suffix matching is the dangerous
direction under fail-open (it OVER-matches, scoping an admin into a district that is not
theirs); exact matching under-matches into the FULL list, which is always safe. A district
needing a second domain gets it as its own row in ``district_domains``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from src.config.app_config import AppConfig
from src.utils.validators import validate_identity_email

__all__ = [
    "DomainMatch",
    "MatchOutcome",
    "can_continue",
    "gate_reason",
    "matched_excludes_saved",
    "matched_state",
    "needs_identity",
    "needs_identity_prompt",
    "resolve_domain",
    "resolve_sd_number",
    "sd_number_digits",
    "stored_identity_email",
    "unmapped_sd_number",
]

# An SD id, as a config file is named: ``sd`` + the district number + anything. The
# ``(?!\d)`` is the whole point — ``SD4`` must never match ``sd48myedbc``, and a prefix
# comparison would say it does.
_SD_CONFIG_RE = "^sd0*{digits}(?!\\d)"

# The first run of digits in whatever the admin typed ("SD48", "#48", "48 (Sea to Sky)").
_FIRST_DIGITS_RE = re.compile(r"\d+")


def stored_identity_email(app_config: AppConfig) -> str:
    """The stored address, RE-VALIDATED at read time — or ``""`` when it is unusable.

    ``config.json`` is hand-editable, untrusted input and ``AppConfig``'s load-time check
    validates the TYPE, not the SHAPE: a profile can carry markup, a bidi-override
    character, a smuggled newline or a 10 KB string and still load cleanly. So every
    surface that RENDERS or STORES the value runs it through the same boundary validator
    the keyboard goes through, and a failure means **UNANSWERED** — re-ask through the
    normal gate; never echo the raw value into the UI, and never log it.

    This is "validate at boundaries" applied to the boundary S3 did not have: the FILE,
    not the keyboard. Returns the value exactly as ``validate_identity_email`` returns it
    (trimmed, un-normalised) so what the admin sees is what the admin typed.

    The caught set is deliberately wider than ``ValueError``. A non-``str`` value raises
    ``AttributeError`` (no ``.strip``) or ``TypeError`` inside the validator, and while
    ``AppConfig._value_fits`` should keep a non-``str`` out of this field, that is a
    guarantee made in a DIFFERENT module — a reader that must never fail closed should not
    be hostage to someone else's invariant, or to a directly-constructed instance that
    never went through a load. Anything unusable reads as UNANSWERED, which is the safe
    answer in every case.
    """
    try:
        return validate_identity_email(app_config.identity_email)
    except (ValueError, AttributeError, TypeError):
        return ""


def needs_identity(app_config: AppConfig) -> bool:
    """True when the launch page should ask for the admin's work email.

    Three conditions, all of which must hold — each one a state in which asking would be
    the wrong thing to do if it were absent:

    * **the settings file is readable.** Under ``settings_unreadable()`` we could not
      persist the answer (``AppConfig.save`` refuses a settings-free write, and
      ``identity_save`` re-checks at write time), so asking would put a question in front
      of an admin whose answer we would silently drop. G2: no gate, no card, no prompt.
    * **setup is not finished.** A working install is never stopped at a launch page in
      front of its own sync — it gets the dismissible Home card instead (S4b).
    * **no USABLE identity is on file.** Asked and answered; a whitespace-only stored
      value is not an answer, and neither is one that fails the boundary validator (see
      :func:`stored_identity_email` — the read-time re-validation).

    Note what is deliberately absent: no attempt counter, no lockout, no expiry, no
    network check. Each absence is part of the register — this is a question, not a door.
    """
    return (
        not app_config.settings_unreadable()
        and not app_config.has_completed_setup()
        and not stored_identity_email(app_config)
    )


def needs_identity_prompt(app_config: AppConfig) -> bool:
    """True when HOME should show the one-time identity card (plan 0038 S4b).

    The SAME question :func:`needs_identity` asks, put to the population it cannot ask:
    an install that already finished setup and is therefore never stopped at a launch
    page in front of its own working sync. Four conditions, all of which must hold:

    * **the settings file is readable** — same reason as the gate (G2): we could not
      persist the answer, so we must not ask for it;
    * **setup IS finished** — the exact inversion of the gate's second condition, which is
      what makes the two asks mutually exclusive: no install is ever asked twice;
    * **the ask has not been dismissed** — "Don't ask again" is PERMANENT. The way back is
      Settings ("Who looks after this sync"), which is also where clearing the address
      resets this flag (:meth:`AppConfig.identity_clear`) so the states cannot wedge;
    * **no USABLE identity is on file** — same read-time re-validation as the gate (see
      :func:`stored_identity_email`).

    Note what is deliberately absent, exactly as on the launch page: nothing here blocks,
    counts, expires or re-asks on a schedule. It is one card, once, under the verdict.
    """
    return (
        not app_config.settings_unreadable()
        and app_config.has_completed_setup()
        and not app_config.identity_prompt_dismissed
        and not stored_identity_email(app_config)
    )


def gate_reason(app_config: AppConfig) -> str:
    """WHY the gate decided as it did — a bounded, PII-free word for the ops log.

    Logged verbatim beside ``needs_identity``'s answer, so it must be a fixed vocabulary
    term and never a value: the address, its local part and its domain are all banned from
    the log. The branches are in the same precedence order the predicate uses, and
    ``stored-value-unusable`` is deliberately distinguished from ``no-identity`` — a
    hand-edited profile that reads back as unanswered is worth seeing in a support log.
    """
    if app_config.settings_unreadable():
        return "settings-unreadable"
    if app_config.has_completed_setup():
        return "setup-complete"
    if stored_identity_email(app_config):
        return "identity-on-file"
    if app_config.identity_email.strip():
        return "stored-value-unusable"
    return "no-identity"


def can_continue(typed: str) -> bool:
    """The Continue gate — STRUCTURAL only (mirrors ``setup_gates``' predicates).

    Non-blank is the whole rule. Format validation is a separate, LATER event: it fires on
    blur or submit, never while the admin is still typing, because an error that appears
    after the third keystroke of a correct address is an accusation, not help.
    """
    return bool(typed.strip())


# --------------------------------------------------------------------------- #
# Matching — the S4a→S5 seam                                                    #
# --------------------------------------------------------------------------- #
class MatchOutcome(str, Enum):
    """What the admin's domain resolved to — the page-state discriminant."""

    MATCHED_ONE = "matched_one"
    MATCHED_SEVERAL = "matched_several"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class DomainMatch:
    """One resolution: the outcome plus the config ids it resolved to (possibly none)."""

    outcome: MatchOutcome
    configs: tuple[str, ...]


def _normalise_domain(value: str) -> str:
    """Trim + lowercase — the comparable form on BOTH sides of the equality.

    The bundled rows are already lowercase (the config-side validator enforces it), but a
    YAML dropped into ``~/.districtsync/mappings/`` is not ours, and failing to match an
    admin against their OWN district is the failure this design makes unrepresentable.
    """
    return value.strip().lower()


def resolve_domain(domain: str, domains_by_config: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Every config whose ``district_domains`` contains ``domain`` EXACTLY. TOTAL.

    ``domains_by_config`` is the index as DATA — S4a builds it per page mount, S5 hands it
    over from the catalog build. Results come back in the mapping's ITERATION order, so
    the caller owns the ordering (``available_configs()`` is sorted, so the real index is
    deterministic); re-sorting here would take that choice away from S5's catalog layer.

    A blank/whitespace domain matches nothing — "we could not reduce this to a domain"
    must never be mistaken for "this matches everything". A config with an EMPTY domain
    list can never be matched either: ``()`` means *claims nobody*, and such a config is
    UNCLAIMED — shown in every unmatched state by the caller's rule, never by this one.
    """
    wanted = _normalise_domain(domain)
    if not wanted:
        return ()
    return tuple(
        sis_type
        for sis_type, domains in domains_by_config.items()
        if any(_normalise_domain(row) == wanted for row in domains)
    )


def matched_state(domain: str, domains_by_config: Mapping[str, Sequence[str]]) -> DomainMatch:
    """Resolve ``domain`` and label the result with the page state it drives.

    Three outcomes, three different pages: exactly one match is a CORRECTABLE
    pre-selection, several is the "which of these?" question, and none is the calm
    "we don't have that address on file yet" — which is a fully valid way to carry on,
    not an error. An empty/unreadable index degrades to ``NO_MATCH``: fail OPEN.
    """
    configs = resolve_domain(domain, domains_by_config)
    if len(configs) == 1:
        outcome = MatchOutcome.MATCHED_ONE
    elif configs:
        outcome = MatchOutcome.MATCHED_SEVERAL
    else:
        outcome = MatchOutcome.NO_MATCH
    return DomainMatch(outcome=outcome, configs=configs)


def matched_excludes_saved(saved_sis: str, configs: Sequence[str]) -> bool:
    """True when a NON-EMPTY match set does not contain the configured district (G3).

    The mismatch card's whole rule, in one place. It is deliberately narrow, because both
    "empty" cases are questions we must NOT put to an admin:

    * **no match** — we recognised nothing, so there is no second opinion to report. The
      admin hears the calm "we don't have a district on file for that address yet"
      instead;
    * **nothing configured** — there is no saved district to differ FROM.

    Comparison is case- and whitespace-normalised on BOTH sides: the bundled ids are
    lowercase, but ``sis_type`` comes from a settings file an admin can hand-edit, and a
    spurious mismatch card would tell a correctly-configured install that it disagrees
    with itself.

    What this rule can NEVER do is act. The card it drives reports a difference, offers
    "Keep <saved>" and a hop to Mapping, and changes nothing either way — resolution never
    rewrites ``sis_type`` (structurally enforced by ``AppConfig.identity_save``).
    """
    if not configs:
        return False
    saved = _normalise_domain(saved_sis)
    if not saved:
        return False
    return saved not in {_normalise_domain(sis_type) for sis_type in configs}


def unmapped_sd_number(app_config: AppConfig, config_ids: Iterable[str]) -> str:
    """The stored district number when NO bundled config serves it — else ``""``. TOTAL.

    Drives the durable "we're building the mapping for SD##" card (plan 0038 S4b): the
    reader that finally earns ``identity_sd_number`` its persistence. It is written by the
    launch page's not-listed path and, until Phase 2 ships the mapping creator, this card
    is the only thing that ever reads it.

    Two directions, both mattering:

    * a number we DO serve returns ``""`` — telling an admin we are "building" a mapping
      that ships in the executable they are running would be a plain untruth, and the
      ``resolve_sd_number`` boundary (``SD4`` never matches ``sd48myedbc``) is what keeps
      the opposite mistake unrepresentable too;
    * anything unusable returns ``""`` — no digits, blank, or (a hand-edited profile) not
      even a string. ``AppConfig._value_fits`` should keep a non-``str`` out of the field,
      but that is a guarantee made in another module, and this reader must never fail on
      someone else's invariant. Nothing stored means nothing to say.
    """
    raw = app_config.identity_sd_number
    digits = sd_number_digits(raw if isinstance(raw, str) else "")
    if not digits or resolve_sd_number(digits, config_ids):
        return ""
    return digits


def sd_number_digits(raw: str) -> str:
    """The district number out of whatever the admin typed ("SD48" / "#48" / "48"). TOTAL.

    The first run of digits, with leading zeros dropped ("048" and "48" are one district).
    No digits at all → ``""``, which resolves to nothing rather than to everything.
    """
    found = _FIRST_DIGITS_RE.search(raw or "")
    if not found:
        return ""
    return found.group().lstrip("0") or "0"


def resolve_sd_number(raw: str, config_ids: Iterable[str]) -> tuple[str, ...]:
    """Every config id naming that district number — ``48`` → ``sd48myedbc``. TOTAL.

    The boundary matters as much as it does for domains: ``SD4`` must never resolve to
    ``sd48myedbc``, so the id must be followed by a NON-digit (or end). Configs that are
    not SD-shaped (``myedbc``, ``mbp_all``) never resolve — they are the generic tiers, not
    a district.
    """
    digits = sd_number_digits(raw)
    if not digits:
        return ()
    pattern = re.compile(_SD_CONFIG_RE.format(digits=digits))
    return tuple(sis_type for sis_type in config_ids if pattern.match(sis_type.strip().lower()))
