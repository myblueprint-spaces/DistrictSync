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

from src.config.bc_district_domains import DOMAINS_BY_SD, domains_for, presumptive_domain
from src.config.loader import available_configs, load_config
from src.config.models import MappingConfig, is_valid_district_domain
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
    for sd_number, domains in DOMAINS_BY_SD.items():
        result = domains_for(sd_number)
        assert result == domains
        assert isinstance(result, tuple)
        assert len(result) > 0
        assert all(isinstance(d, str) for d in result)


# --------------------------------------------------------------------------- #
# Multi-domain rows                                                            #
# --------------------------------------------------------------------------- #


def test_sd63_has_exactly_three_domains_in_order():
    assert domains_for(63) == ("saanichschools.ca", "sides.ca", "sd63.bc.ca")


def test_sd70_has_exactly_two_domains_in_order():
    assert domains_for(70) == ("sd70.bc.ca", "kackaamin.org")


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
    """63 domains in total: the owner CSV's 64 one-per-domain rows minus SD78's artifact.

    Pins the OTHER axis of the table — a key count alone would not notice a domain
    silently added to or dropped from a multi-domain district (SD63/SD70).
    """
    assert sum(len(domains) for domains in DOMAINS_BY_SD.values()) == 63


# --------------------------------------------------------------------------- #
# Cross-checks against shipped configs (literal spot-checks)                   #
# --------------------------------------------------------------------------- #


def test_sd60_matches_the_shipped_staff_domain():
    assert domains_for(60) == ("prn.bc.ca",)


def test_sd48_matches_the_shipped_domain():
    assert domains_for(48) == ("sd48.bc.ca",)


def test_sd75_matches_the_shipped_staff_domain():
    assert domains_for(75) == ("mpsd.ca",)


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
    """
    checked_sd_numbers: set[int] = set()
    checked_any = False

    for sis in available_configs():
        match = _SD_ID_RE.match(sis)
        if not match:
            continue  # no SD number in the id (myedbc, mbp_*, unitychristianmyedbc)

        sd_number = int(match.group(1))
        if sd_number in checked_sd_numbers:
            continue  # sd51attendance-style duplicate of an already-checked number
        checked_sd_numbers.add(sd_number)

        cfg = load_config(sis)
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
