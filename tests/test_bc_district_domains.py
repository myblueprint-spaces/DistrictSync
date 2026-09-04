"""`src.config.bc_district_domains` — the vendored BC district-domain prefill table.

Plan 0044 slice 1. This module is PLACEHOLDER-quality prefill data (owner-supplied
CSV, 2026-08-27), never a source of truth — these tests pin its SHAPE (total lookup,
lowercase/no-@ domains, the SD78 grouping-artifact drop) and its PARITY with the two
places that already carry independently-reviewed truth about a handful of these
districts: the shipped `district_domains:` config values (a config is stronger
evidence than this table, so the table must at least contain what we ship) and the
two case-twin domain-shape predicates the rest of the codebase already relies on.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from src.config.bc_district_domains import (
    DOMAINS_BY_SD,
    NAMES_BY_SD,
    domains_for,
    name_for,
    presumptive_domain,
)
from src.config.loader import available_configs, load_config
from src.config.models import MappingConfig, is_valid_district_domain
from src.utils.paths import bundle_mappings_dir
from src.utils.validators import _IDENTITY_DOMAIN_RE

# --------------------------------------------------------------------------- #
# Total lookup                                                                 #
# --------------------------------------------------------------------------- #


def test_unknown_sd_number_returns_empty_tuple():
    assert domains_for(1) == ()
    assert domains_for(999) == ()


def test_negative_and_zero_sd_number_return_empty_tuple_never_raise():
    # domains_for is TOTAL — unlike presumptive_domain, it never validates its input.
    assert domains_for(0) == ()
    assert domains_for(-5) == ()


def test_every_known_key_returns_a_non_empty_tuple_of_strings():
    """`domains_for` EXTENDS the sheet; it never reorders or drops from it.

    Since 2026-09-03 the literal is the SHEET and `domains_for` is the effective answer
    (it appends the conventional `sd<N>.bc.ca` when the sheet omits it). This pins both
    halves of that: the sheet's own domains still come first, in their own order, and the
    conventional form is present for every known district either way.
    """
    for sd_number, domains in DOMAINS_BY_SD.items():
        result = domains_for(sd_number)
        assert isinstance(result, tuple)
        assert all(isinstance(d, str) for d in result)
        assert result[: len(domains)] == domains, "the sheet's rows must lead, in sheet order"
        assert f"sd{sd_number}.bc.ca" in result
        assert len(result) - len(domains) in (0, 1), "at most ONE domain may be appended"


# --------------------------------------------------------------------------- #
# Multi-domain rows                                                            #
# --------------------------------------------------------------------------- #


def test_sd63_has_exactly_three_domains_in_order():
    assert domains_for(63) == ("saanichschools.ca", "sides.ca", "sd63.bc.ca")


def test_sd70_carries_only_its_real_domain():
    """`kackaamin.org` was dropped 2026-09-03 (OWNER): an artifact, not an SD70 domain.

    Same grouping-artifact shape as SD78's dropped `sd48.bc.ca`, and absent from the
    cleaner 2026-09-03 sheet. Pinned in BOTH directions so a future re-vendoring from the
    older CSV cannot quietly put it back.
    """
    assert domains_for(70) == ("sd70.bc.ca",)
    assert "kackaamin.org" not in domains_for(70)
    assert "kackaamin.org" not in set(_all_vendored_domains())


# --------------------------------------------------------------------------- #
# SD78 grouping-artifact drop                                                  #
# --------------------------------------------------------------------------- #


def test_sd78_drops_the_sd48_grouping_artifact():
    """SD78's source row carries a second domain, sd48.bc.ca — dropped at vendoring.

    Pinned both ways: the artifact is absent from SD78's tuple, AND (the positive
    twin, so the absence assertion isn't vacuous) sd48.bc.ca genuinely IS a real
    domain in this table — just under SD48's own key, never SD78's.
    """
    assert domains_for(78) == ("sd78.bc.ca",)
    assert "sd48.bc.ca" not in domains_for(78)
    assert "sd48.bc.ca" in domains_for(48)


# --------------------------------------------------------------------------- #
# presumptive_domain                                                           #
# --------------------------------------------------------------------------- #


def test_presumptive_domain_follows_the_sd_convention():
    assert presumptive_domain(93) == "sd93.bc.ca"
    assert presumptive_domain(1) == "sd1.bc.ca"


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_presumptive_domain_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        presumptive_domain(bad)


def test_presumptive_domain_rejects_non_int():
    with pytest.raises(ValueError):
        presumptive_domain("93")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        presumptive_domain(True)  # bool is an int subclass — deliberately excluded


# --------------------------------------------------------------------------- #
# Exact key count                                                              #
# --------------------------------------------------------------------------- #


def test_exact_key_count_is_pinned():
    """60 SD numbers, counted from the plan's owner-supplied table.

    A future edit that silently drops or duplicates a row changes this count — the
    pin forces the edit to be deliberate and reviewed, not accidental.
    """
    assert len(DOMAINS_BY_SD) == 60


def test_exact_domain_total_is_pinned():
    """62 domains in the SHEET: 64 one-per-domain CSV rows minus TWO dropped artifacts.

    SD78's `sd48.bc.ca` (2026-08-27) and SD70's `kackaamin.org` (2026-09-03, owner). Pins
    the OTHER axis of the literal — a key count alone would not notice a domain silently
    added to or dropped from the one remaining multi-domain row (SD63).
    """
    assert sum(len(domains) for domains in DOMAINS_BY_SD.values()) == 62


def test_effective_domain_total_is_pinned():
    """79 domains once the conventional form is applied: the sheet's 62 plus 17.

    The twin of the pin above, on the axis that actually decides what an admin matches.
    Without it the sheet count could stay green while the rule that augments it silently
    stopped firing — a "no domains were lost" assertion with nothing proving the mechanism
    still works (CLAUDE.md's no-vacuous-greens rule).
    """
    assert sum(len(domains_for(sd_number)) for sd_number in DOMAINS_BY_SD) == 79
    augmented = [sd for sd, row in DOMAINS_BY_SD.items() if f"sd{sd}.bc.ca" not in row]
    assert len(augmented) == 17


# --------------------------------------------------------------------------- #
# Cross-checks against shipped configs (literal spot-checks)                   #
# --------------------------------------------------------------------------- #


def test_sd60_matches_the_shipped_staff_domain():
    """The verified staff domain LEADS; the conventional form follows it."""
    assert domains_for(60) == ("prn.bc.ca", "sd60.bc.ca")


def test_sd48_matches_the_shipped_domain():
    assert domains_for(48) == ("sd48.bc.ca",)


def test_sd75_matches_the_shipped_staff_domain():
    """The verified staff domain LEADS; the conventional form follows it."""
    assert domains_for(75) == ("mpsd.ca", "sd75.bc.ca")


# --------------------------------------------------------------------------- #
# The conventional-form rule (owner, 2026-09-03)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sd_number", "expected"),
    [
        (34, ("abbyschools.ca", "sd34.bc.ca")),  # a wholly custom domain
        (58, ("365.sd58.bc.ca", "sd58.bc.ca")),  # a SUBDOMAIN of the conventional form
        (84, ("viw.sd84.bc.ca", "sd84.bc.ca")),  # ditto
        (42, ("sd42.ca", "sd42.bc.ca")),  # a near-miss: .ca, not .bc.ca
        (63, ("saanichschools.ca", "sides.ca", "sd63.bc.ca")),  # already present — unchanged
        (48, ("sd48.bc.ca",)),  # already conventional — no duplicate appended
    ],
)
def test_a_custom_domain_row_gains_the_conventional_form(sd_number, expected):
    """A district with its own domain still matches `sd<N>.bc.ca` (owner, 2026-09-03).

    SD58 and SD84 are the reason this is a RULE and not two hand-edits: their sheet rows
    are SUBDOMAINS of the conventional form, and this project matches by EXACT string
    equality (never suffix), so `365.sd58.bc.ca` claimed nothing for a plain `@sd58.bc.ca`
    admin. SD58 was a flagged prefill residual before this rule closed it.

    The last two rows pin the no-op direction: a district already carrying the
    conventional form gets no duplicate and no reordering.
    """
    assert domains_for(sd_number) == expected


def test_the_conventional_form_is_not_invented_for_an_unknown_district():
    """The rule EXTENDS known rows; it never manufactures one.

    `domains_for` and `presumptive_domain` answer deliberately different questions — known
    domains vs an outright guess — and `config_editor.derive_domains` relies on that split
    to avoid presenting a guess as something we were told. Collapsing them would put an
    invented domain in the creator's field looking exactly as authoritative as a real one.
    """
    assert domains_for(1) == ()
    assert domains_for(999) == ()
    assert presumptive_domain(999) == "sd999.bc.ca"


# --------------------------------------------------------------------------- #
# Full parity sweep against every bundled config that declares district_domains #
# --------------------------------------------------------------------------- #

_SD_ID_RE = re.compile(r"^sd(\d+)")


def test_every_shipped_district_domain_is_in_the_vendored_table():
    """A shipped config is stronger evidence than this table — it must be a subset.

    For every bundled config id that resolves to an SD number and declares
    `district_domains`, each declared domain must appear in this table's tuple for
    that SD number. `sd51attendance` duplicates sd51myedbc's SD number (51) and is
    skipped once that number has already been checked via sd51myedbc — same
    district, two tiers, not a second data point.

    Enumerated via the BUNDLE dir explicitly (never the plain `available_configs()`,
    which also globs the user dir): this test's claim is about the shipped,
    vendor-reviewed table, and a self-service overlay written into the user dir by
    an unrelated test (e.g. an `sd93custom` config with `district_domains=
    ["sd93.bc.ca"]`) would otherwise be matched against this table's real SD93 row
    (`csf.bc.ca`) and fail for a reason that has nothing to do with the bundle.
    """
    bundle_dir = bundle_mappings_dir()
    checked_sd_numbers: set[int] = set()
    checked_any = False

    for sis in available_configs(bundle_dir):
        match = _SD_ID_RE.match(sis)
        if not match:
            continue  # no SD number in the id (myedbc, mbp_*, unitychristianmyedbc)

        sd_number = int(match.group(1))
        if sd_number in checked_sd_numbers:
            continue  # sd51attendance-style duplicate of an already-checked number
        checked_sd_numbers.add(sd_number)

        cfg = load_config(sis, config_dir=bundle_dir)
        if not cfg.district_domains:
            continue

        checked_any = True
        vendored = domains_for(sd_number)
        for declared_domain in cfg.district_domains:
            assert declared_domain in vendored, (
                f"{sis} (SD{sd_number}) declares district_domains={cfg.district_domains!r} "
                f"but the vendored table only has {vendored!r} for SD{sd_number}"
            )

    assert checked_any, "no bundled config with an SD number declared district_domains — parity sweep is vacuous"


# --------------------------------------------------------------------------- #
# The two-regex parity convention                                             #
# --------------------------------------------------------------------------- #


def _all_vendored_domains():
    for domains in DOMAINS_BY_SD.values():
        yield from domains


@pytest.mark.parametrize("domain", sorted(set(_all_vendored_domains())))
def test_every_vendored_domain_is_a_valid_district_domain(domain):
    assert is_valid_district_domain(domain)


@pytest.mark.parametrize("domain", sorted(set(_all_vendored_domains())))
def test_every_vendored_domain_matches_the_identity_domain_regex(domain):
    assert _IDENTITY_DOMAIN_RE.match(domain) is not None


def test_every_vendored_domain_builds_a_valid_mapping_config():
    """Placing every vendored domain in one config's district_domains must not raise."""
    all_domains = sorted(set(_all_vendored_domains()))
    cfg = MappingConfig(
        version="1.0", sis="bc-district-domains-parity-check", district_domains=all_domains, mappings={}
    )
    assert cfg.district_domains == all_domains


def test_a_bad_domain_would_fail_mapping_config_validation():
    """Sanity twin: the constructor above genuinely enforces the rule, not vacuously."""
    with pytest.raises(ValidationError):
        MappingConfig(version="1.0", sis="x", district_domains=["not an email domain@"], mappings={})


# --------------------------------------------------------------------------- #
# Domain hygiene: lowercase, no @, no whitespace                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("domain", sorted(set(_all_vendored_domains())))
def test_domain_hygiene(domain):
    assert domain == domain.lower()
    assert "@" not in domain
    assert domain == domain.strip()
    assert " " not in domain
    assert "\t" not in domain


# --------------------------------------------------------------------------- #
# NAMES_BY_SD / name_for — the district NAME table (owner sheet, 2026-09-03)   #
# --------------------------------------------------------------------------- #
# Mirrors the domain-table sections above deliberately: same posture (placeholder
# prefill, corrected in the creator form), so the same shapes are pinned.


def test_name_for_is_total_and_quiet_for_an_unknown_district():
    """`""` is the ordinary answer, never an exception and never a fabrication.

    Most BC district numbers are not in the owner's sheet, and this feeds a PREFILL — a
    prefill that raises is worse than one that stays quiet. Unlike `presumptive_domain`
    there is no fallback to guess with: `sd<N>.bc.ca` is a real naming convention, but
    nothing would let us invent a district's NAME, and "District 34" in the picker would
    look exactly like a name somebody chose.
    """
    assert name_for(1) == ""
    assert name_for(999) == ""
    assert name_for(0) == ""
    assert name_for(-5) == ""


def test_the_name_table_covers_exactly_the_districts_the_domain_table_does():
    """Both tables come from the same owner sheets, which cover the same 60 districts.

    Pinned as a SET comparison rather than two counts: a row added to one table and not
    the other is the realistic drift, and two `== 60` assertions would both stay green
    through it.
    """
    assert set(NAMES_BY_SD) == set(DOMAINS_BY_SD)
    assert len(NAMES_BY_SD) == 60


@pytest.mark.parametrize("sd_number", sorted(NAMES_BY_SD))
def test_every_vendored_name_is_bare_and_usable(sd_number):
    """BARE: non-blank, stripped, no `SD<N>` prefix, no `School District` suffix.

    The prefix is put back at READ time by `humanize.friendly_district_name` and the
    creator's name field wants the name alone, so a prefix vendored here would be
    doubled on screen. The suffix is absent by owner decision (2026-09-03) — SD92
    "Nisga'a" and SD93 "Conseil scolaire francophone" are not that shape, so a blanket
    suffix would be wrong for them and merely redundant for the rest.
    """
    name = NAMES_BY_SD[sd_number]
    assert name and name == name.strip()
    assert name_for(sd_number) == name
    assert not re.match(r"(?i)^sd\s*\d", name)
    assert "school district" not in name.lower()


def test_sd83_keeps_its_exact_secwepemc_spelling():
    """The ONE non-ASCII row, pinned by CODEPOINT so no "tidy-up" can flatten it.

    Asserted against escapes rather than a pasted literal on purpose: the combining comma
    above is invisible next to the K in most editors, and the apostrophe is U+2019, which
    an autocorrect or a well-meaning ASCII sweep would silently replace. A literal here
    would be corrupted by exactly the edit this test exists to catch.

    The district is renamed to this in `config/mappings/sd83myedbc_mapping.yaml` too
    (owner, 2026-09-03); the parity sweep below is what keeps the two together.
    """
    expected = "K̓wsaltktnéws ne Secwepemcúl’ecw"

    assert name_for(83) == expected
    assert [n for n, name in NAMES_BY_SD.items() if not name.isascii()] == [83]


def test_vendored_names_agree_with_every_shipped_district_config():
    """Parity against data this repo already trusts — the NAME twin of the domain sweep.

    Every bundled config whose id carries an SD number must contain the vendored bare
    name inside its own `district_name`. That is what makes the table more than an
    unchecked paste: fifteen shipped configs agree with it today, and a future rename on
    either side goes red instead of leaving the creator prefilling a name the product no
    longer uses anywhere else.
    """
    checked = 0
    for sis in available_configs(bundle_mappings_dir()):
        match = re.match(r"sd(\d+)", sis)
        if not match:
            continue
        sd_number = int(match.group(1))
        vendored = name_for(sd_number)
        assert vendored, f"{sis} is a shipped SD{sd_number} config but the name table has no SD{sd_number}"

        shipped = load_config(sis, config_dir=bundle_mappings_dir()).district_name
        assert vendored in shipped, f"{sis} ships district_name={shipped!r}, which does not contain {vendored!r}"
        checked += 1

    assert checked >= 15, "the shipped-name parity sweep found too few configs to be meaningful"
