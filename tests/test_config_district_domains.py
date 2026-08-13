"""`MappingConfig.district_domains` — the schema key, its validator, and the SHIPPED data.

Three concerns, deliberately together because they only make sense as one contract:

1. **the key** — top-level, presentation-only, structurally unable to reach the ETL;
2. **the validator** — a pasted email address must fail `make validate-config` LOUDLY,
   in CI, before it can put a real person's address in a public repository;
3. **the six shipped rows** — the values ship LIT in S3 (plan 0038, flag 3), so unlike
   the retired hashed-allowlist design this batch CAN be tested end to end: each shipped
   domain is asserted to resolve to exactly its own district.

The matching rule these feed is `mapping_catalog.filtered_catalog` (LIVE since S5, with its
own truth table in `test_ui_flet_filtered_catalog.py`); what is pinned HERE is the DATA layer
it consumes, plus the exact-equality semantics it implements.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from src.config.loader import available_configs, load_config
from src.config.models import MappingConfig
from src.utils.identity import extract_domain, normalize_email
from src.utils.paths import bundle_mappings_dir
from src.utils.validators import _IDENTITY_DOMAIN_RE

# --------------------------------------------------------------------------- #
# The SHIPPED data, declared. Reviewed at the pre-release owner gate.           #
# --------------------------------------------------------------------------- #
# domain -> the ONE district lineage that claims it. `sd51attendance` is absent on
# purpose: it inherits SD51's row via `_base` and is the single sanctioned duplicate
# (same district, two tiers) — pinned separately below so the inheritance is deliberate
# rather than incidental.
SHIPPED_DOMAINS: dict[str, str] = {
    "sd40.bc.ca": "sd40myedbc",
    "sd48.bc.ca": "sd48myedbc",
    "sd51.bc.ca": "sd51myedbc",
    "sd54.bc.ca": "sd54myedbc",
    "prn.bc.ca": "sd60myedbc",  # the STAFF domain, NOT the generated student domain
    "sd74.bc.ca": "sd74myedbc",
}

# Configs that deliberately carry NO domains — "unclaimed", shown in every state.
# sd83myedbc is unclaimed for a different reason than the base/mbp_* tiers below:
# its real public staff domain hasn't been provided yet. Move it into
# SHIPPED_DOMAINS once a verified domain is on hand (see docs/developer/adding-district.md).
UNCLAIMED_CONFIGS: frozenset[str] = frozenset({"myedbc", "mbp_all", "mbp_core", "mbponly", "sd83myedbc"})

# The one config that inherits its list rather than declaring it.
INHERITING_CONFIG = "sd51attendance"
INHERITS_FROM = "sd51myedbc"


@pytest.fixture(scope="module")
def bundle_dir() -> Path:
    return bundle_mappings_dir()


@pytest.fixture(scope="module")
def resolved(bundle_dir: Path) -> dict[str, list[str]]:
    """Every bundled config id -> its RESOLVED (post-`_base`-merge) domain list."""
    return {sis: load_config(sis, bundle_dir).district_domains for sis in available_configs(bundle_dir)}


# --------------------------------------------------------------------------- #
# The shipped rows                                                             #
# --------------------------------------------------------------------------- #
def test_the_declared_table_covers_every_claimed_config(resolved):
    """The table above is the reviewed source — a new domain row must appear in it.

    Asserted in BOTH directions, so neither a new undeclared row nor a quietly deleted
    one can pass: "fixing" a red test by editing only the YAML fails here.
    """
    claimed = {sis for sis, domains in resolved.items() if domains and sis != INHERITING_CONFIG}
    assert claimed == set(SHIPPED_DOMAINS.values())
    assert set(resolved) - claimed - {INHERITING_CONFIG} == UNCLAIMED_CONFIGS


@pytest.mark.parametrize(("domain", "sis"), sorted(SHIPPED_DOMAINS.items()))
def test_each_shipped_domain_resolves_to_exactly_its_district(domain, sis, resolved):
    """The live end-to-end match: a work email at this domain claims THIS district.

    Walks the real call path a picker will use — ``extract_domain(normalize_email(...))``
    then exact membership — rather than reading the YAML back, so a normalisation change
    that broke matching would surface here and not only in the primitives' own tests.
    """
    typed = f"A.Person@{domain.upper()}"
    admin_domain = extract_domain(normalize_email(typed))

    matches = {s for s, domains in resolved.items() if admin_domain in domains}

    assert sis in matches
    # SD51's two tiers legitimately share a domain; every other domain claims exactly one.
    expected = {sis, INHERITING_CONFIG} if sis == INHERITS_FROM else {sis}
    assert matches == expected


def test_no_domain_is_claimed_by_two_different_districts(resolved):
    """Domain OWNERSHIP: each domain belongs to ONE district (tiers of it aside).

    A domain claimed by two genuinely different districts would scope an admin into a
    district that is not theirs — the one failure direction the fail-open design cannot
    absorb, because the wrong district would be pre-selected rather than merely offered.
    """
    lineage = {INHERITING_CONFIG: INHERITS_FROM}
    owners: dict[str, set[str]] = {}
    for sis, domains in resolved.items():
        for domain in domains:
            owners.setdefault(domain, set()).add(lineage.get(sis, sis))

    shared = {domain: sorted(who) for domain, who in owners.items() if len(who) > 1}
    assert not shared, f"domains claimed by more than one district lineage: {shared}"


def test_sd60_ships_the_staff_domain_not_the_student_one(bundle_dir):
    """SD60's row is `prn.bc.ca`, deliberately NOT its generated student domain.

    The domain rows come from the owner's verified staff-contact sheet. Deriving them
    from the student email templates already in these YAMLs would have shipped the wrong
    domain for SD60 — an admin at the staff domain would then match nothing. Pinned so a
    future "tidy-up" cannot re-derive the rows from the templates.
    """
    cfg = load_config("sd60myedbc", bundle_dir)
    student_template = str(cfg.mappings["Students"].field_map.get("Email Address"))

    assert cfg.district_domains == ["prn.bc.ca"]
    assert "learn60.ca" in student_template  # the student domain really is different
    assert "learn60.ca" not in cfg.district_domains


@pytest.mark.parametrize("sis", sorted(UNCLAIMED_CONFIGS))
def test_unclaimed_configs_carry_no_domains(sis, resolved):
    """The base and the myBlueprint+ tiers stay UNCLAIMED — and the base especially.

    A domain on the base ``myedbc`` would deep-merge into EVERY descendant config, so one
    district's domain would claim all eleven. The `mbp_*` tiers are cross-district
    product tiers with no single owner, so they stay unclaimed too — which under the
    fail-open rule means "shown in every unmatched / no-identity / show-all state".
    `sd83myedbc` is unclaimed for a THIRD reason (not a cross-district tier, just a real
    district whose domain hasn't been supplied yet) — same test, same fail-open outcome.
    """
    assert resolved[sis] == []


def test_sd51attendance_inherits_sd51s_domains_via_base(resolved, bundle_dir):
    """The ONE sanctioned duplicate, and it must come from inheritance, not a copy.

    Both SD51 tiers serve the same district and the same staff, so an SD51 admin
    legitimately matches two configs (the matched-several path). Asserted as INHERITANCE:
    the attendance YAML must not declare its own list, or the two could silently diverge.
    """
    assert resolved[INHERITING_CONFIG] == resolved[INHERITS_FROM] == ["sd51.bc.ca"]

    raw = yaml.safe_load((bundle_dir / f"{INHERITING_CONFIG}_mapping.yaml").read_text(encoding="utf-8"))
    assert "district_domains" not in raw, "sd51attendance must INHERIT the list, never restate it"


def test_a_subdomain_of_a_shipped_domain_matches_nothing(resolved):
    """Exact equality, never suffix: `mail.sd48.bc.ca` claims no district.

    Under fail-open, over-matching is the dangerous direction (it pre-selects a district
    that may not be the admin's) and under-matching is safe (the full list). A district
    needing a subdomain gets it as its own row.
    """
    admin_domain = extract_domain(normalize_email("admin@mail.sd48.bc.ca"))

    assert admin_domain == "mail.sd48.bc.ca"
    assert not [sis for sis, domains in resolved.items() if admin_domain in domains]


@pytest.mark.parametrize("domain", sorted(SHIPPED_DOMAINS))
def test_shipped_domains_satisfy_both_domain_rules(domain):
    """Parity between the two deliberately-different domain rules.

    ``models._DISTRICT_DOMAIN_RE`` (lowercase-only, for an AUTHORED config value) and
    ``validators._IDENTITY_DOMAIN_RE`` (case-insensitive, for a TYPED address) exist for
    different jobs. They may differ — but not in a way that lets a shipped row be
    unreachable by a typed address, which is what this pins.
    """
    from src.config.models import _DISTRICT_DOMAIN_RE

    assert _DISTRICT_DOMAIN_RE.match(domain)
    assert _IDENTITY_DOMAIN_RE.match(domain)


# --------------------------------------------------------------------------- #
# The validator — a pasted address must fail LOUD                              #
# --------------------------------------------------------------------------- #
def _config_yaml(domains: str) -> str:
    return textwrap.dedent(f"""
        version: '1.0'
        sis: probe
        district_name: Probe District
        district_domains: {domains}
        global_config: {{}}
        mappings:
          Students:
            source_files:
              demo: Demo.txt
            field_map:
              "User ID": id
        """)


def _load_from(tmp_path: Path, domains: str) -> MappingConfig:
    (tmp_path / "probe_mapping.yaml").write_text(_config_yaml(domains), encoding="utf-8")
    return load_config("probe", tmp_path)


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ('["someone@sd48.bc.ca"]', "a whole email address pasted where a domain belongs — THE case"),
        ('["SD48.BC.CA"]', "uppercase could never match a normalised domain"),
        ('["Sd48.bc.ca"]', "mixed case, same reason"),
        ('[" sd48.bc.ca"]', "leading whitespace"),
        ('["sd48.bc.ca "]', "trailing whitespace"),
        ('["sd48"]', "no dot — not a domain"),
        ('["sd48.bc.c"]', "single-character TLD"),
        ('["-sd48.bc.ca"]', "leading hyphen"),
        ('["sd48.bc.ca/path"]', "a URL, not a domain"),
        ('["https://sd48.bc.ca"]', "a URL scheme"),
        ('[""]', "empty entry"),
        ('["sd48.bc.ca", "someone@sd54.bc.ca"]', "one good entry does not excuse a bad one"),
    ],
)
def test_a_bad_domain_entry_fails_config_load_loudly(tmp_path, entry, why):
    """`make validate-config` (and CI, and every app start) refuses the config.

    Raises rather than warning ON PURPOSE: a silently-dropped bad row would leave the
    district *unclaimed*, which under the fail-open list rule looks completely normal —
    its admin would simply see every district — so the mistake would never surface.
    """
    with pytest.raises((ValidationError, ValueError)) as excinfo:
        _load_from(tmp_path, entry)
    assert "district_domains" in str(excinfo.value), why


def test_the_rejection_message_NEVER_echoes_the_offending_value(tmp_path):
    """INVERTED at the Stage-7 gate — this test previously PINNED the echo.

    The original assertion reasoned "it is config, not PII". That is backwards for this
    particular field: the single most likely thing to trip this validator is a pasted
    PERSONAL email address, and the error surfaces in `make validate-config` output and a
    PUBLIC CI log — so quoting it republishes the exact leak the check exists to stop. It
    also contradicted the two no-republish rules S3 had already adopted
    (`validate_identity_email`'s value-free messages, and the scanner's redacted findings).

    The message must still be ACTIONABLE without the value: it names the entry's position
    and the rule.
    """
    leaked_local, leaked_domain = "aparticularperson", "somedistrict.bc.ca"
    with pytest.raises((ValidationError, ValueError)) as excinfo:
        _load_from(tmp_path, f'["{leaked_local}@{leaked_domain}"]')
    message = str(excinfo.value)

    assert leaked_local not in message, "the pasted address reached a message that lands in a public CI log"
    assert f"{leaked_local}@{leaked_domain}" not in message
    # ...but it still teaches the rule and locates the entry.
    assert "never a full email address" in message
    assert "entry 1 of 1" in message


def test_the_rejection_message_locates_the_entry_by_index(tmp_path):
    """Without the value, POSITION is what makes the message actionable in a long list."""
    with pytest.raises((ValidationError, ValueError)) as excinfo:
        _load_from(tmp_path, '["sd48.bc.ca", "sd51.bc.ca", "aparticularperson@somedistrict.bc.ca"]')
    message = str(excinfo.value)

    assert "entry 3 of 3" in message
    assert "aparticularperson" not in message


def test_the_no_echo_pin_would_catch_a_regression():
    """Falsification twin — prove the assertion above is not vacuous."""
    leaky = "district_domains entry 'aparticularperson@somedistrict.bc.ca' is not a domain."
    assert "aparticularperson" in leaky


@pytest.mark.parametrize(
    "entry",
    ['["sd48.bc.ca"]', '["sd48.bc.ca", "sd48-schools.ca"]', "[]", '["a1.co"]', '["x.district.gov.bc.ca"]'],
)
def test_valid_domain_entries_load(tmp_path, entry):
    assert _load_from(tmp_path, entry).district_domains == yaml.safe_load(entry)


def test_absent_key_defaults_to_empty_list(tmp_path):
    """No `district_domains:` means UNCLAIMED — never ``None``, so callers need no guard."""
    (tmp_path / "nokey_mapping.yaml").write_text(
        _config_yaml('["x.ca"]').replace('district_domains: ["x.ca"]\n', ""), encoding="utf-8"
    )
    assert load_config("nokey", tmp_path).district_domains == []


# --------------------------------------------------------------------------- #
# Structural guarantees                                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sis", sorted(SHIPPED_DOMAINS.values()))
def test_district_domains_cannot_reach_the_etl(sis, bundle_dir):
    """`to_raw_dict` — the pipeline boundary — emits only mappings + global_config.

    This is WHY a presentation key is safe at the top level, why it needs no config
    `version:` bump, and why S3 changes no output byte. Asserted over a config that
    actually HAS domains, so the check cannot pass by accident.
    """
    cfg = load_config(sis, bundle_dir)
    raw = cfg.to_raw_dict()

    assert cfg.district_domains  # the key really is populated on this config
    assert set(raw) == {"mappings", "global_config"}
    assert "district_domains" not in raw["global_config"]
    assert "district_domains" not in repr(raw)


def test_a_child_restating_district_domains_REPLACES_the_parent_list(tmp_path):
    """`_deep_merge` semantics for a list: the child REPLACES, never appends.

    Pinned now, for Phase 2's mapping creator — which will expose this key to users, at
    which point "does my child config add to or replace the parent's domains?" becomes a
    question someone can get wrong. (The `sd51attendance` test above only covers
    inherit-by-ABSENCE; this is the restated case.)
    """
    (tmp_path / "parent_mapping.yaml").write_text(_config_yaml('["parent.ca", "shared.ca"]'), encoding="utf-8")
    (tmp_path / "child_mapping.yaml").write_text(
        textwrap.dedent("""
            _base: parent
            sis: child
            district_domains: ["child.ca"]
            """),
        encoding="utf-8",
    )

    assert load_config("child", tmp_path).district_domains == ["child.ca"]
    assert load_config("parent", tmp_path).district_domains == ["parent.ca", "shared.ca"]


def test_root_model_ignores_unknown_keys_forward_compatibility(tmp_path):
    """`extra="ignore"` on `MappingConfig` — now DECLARED, and pinned.

    A config authored against a newer build must still load here rather than failing a
    whole district's nightly sync over a root key this build does not need. It held only
    by Pydantic's default while five leaf models declare ``extra="forbid"`` — close
    enough to look like an oversight that someone would "fix" it.
    """
    (tmp_path / "future_mapping.yaml").write_text(
        _config_yaml('["x.ca"]') + "\nsome_key_from_a_newer_build: {a: 1}\n", encoding="utf-8"
    )
    cfg = load_config("future", tmp_path)

    assert cfg.district_domains == ["x.ca"]
    assert not hasattr(cfg, "some_key_from_a_newer_build")


def test_extra_ignore_is_declared_not_merely_defaulted():
    """The declaration itself is the contract — pinned so a refactor cannot drop it."""
    assert MappingConfig.model_config.get("extra") == "ignore"


def test_the_forbid_and_ignore_models_are_exactly_as_documented():
    """The forbid/ignore split is pinned, because it has now been written down wrong TWICE.

    The S2b panel corrected a doc that claimed nested models reject unknown keys; S3 then
    named five forbidders from memory and got three of them wrong (two of the names did
    not even exist). Prose about a mechanism is not a mechanism — this asserts BOTH
    directions against the real `model_config`, so `models.py`'s comment, CLAUDE.md and
    the contract doc can be checked against one place instead of against recollection.

    The negatives are the consequential half: `GlobalConfig` and `EntityConfig` inheriting
    `ignore` is exactly why a typo'd `enabled_entities` is silently dropped — leaving
    `[]`, which means ALL entities enabled.
    """
    from src.config import models

    forbidders = {
        name
        for name, obj in vars(models).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.model_config.get("extra") == "forbid"
    }

    assert forbidders == {
        "EmailDerivedDate",
        "FieldEmailFormat",
        "FieldEnrollStatus",
        "RowFilter",
        "CrossEnrollmentConfig",
    }
    for permissive in ("FieldTransform", "FieldNameConfig", "GlobalConfig", "EntityConfig", "MappingConfig"):
        assert getattr(models, permissive).model_config.get("extra", "ignore") == "ignore", (
            f"{permissive} started forbidding extras — that is a compatibility change, "
            "not a tidy-up; see models.MappingConfig's comment."
        )
