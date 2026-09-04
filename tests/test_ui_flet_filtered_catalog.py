"""The district-list filter — `mapping_catalog.filtered_catalog` (plan 0038 S5, COUNTED).

Written RED-FIRST per `docs/claugentic-CHARTER.md` → "Pure predicate / primitive modules
whose semantics the spec DECIDES": every judgment call below (which tier a state falls in,
whether an unclaimed config can be hidden, whether a broken YAML can exclude anyone) is
DECIDED by the plan, so it is stated as an assertion before any code can quietly make it.

**The two-tier rule under test** (plan 0038, Approach → "Filtering = one catalog-layer choke
point with an unclaimed-config rule", as reconciled at the R3 delta gate):

  (i)  no identity / no match  →  ALL configs, unclaimed included.
  (ii) matched (≥1 config's resolved NON-EMPTY `district_domains` contains the domain, by
       EXACT case-normalised equality)  →  exactly the matching configs PLUS `saved_sis`,
       unconditionally.

**The `show_all` tier retired on 2026-08-04** (owner decision — the per-surface "Show all
districts" row is gone from all four pickers). Tier (ii) is therefore unconditional, and the
only escape is at the INPUT: clearing the stored address in Settings empties `domain`, which
lands the admin in tier (i). `TestThereIsNoWideningKnob` pins that there is no longer any
argument that can widen a matched list, so the escape cannot silently grow back.

The failure this rule exists to make unrepresentable is **a district disappearing on its own
admin**. Every test below that asserts an absence is paired with a positive twin, because
"the row was not hidden" is equally satisfied by a working rule and by a filter that never
fired at all (see `docs/claugentic-standards/CANDIDATES.md`, 2026-07-29 vacuous-green lesson).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.ui_flet.mapping_catalog import (
    ConfigSummary,
    catalog,
    disambiguated_labels,
    district_domain_index,
    filtered_catalog,
    list_configs,
    reset_catalog_cache,
)
from src.utils.paths import bundle_mappings_dir


@pytest.fixture(autouse=True)
def _clean_catalog_cache() -> None:
    """The catalog build is memoised per SESSION — a test that writes YAMLs must start clean.

    Belt-and-braces: `tests/conftest.py` already resets the cache around every test in the
    suite. This local copy is kept deliberately, because THIS file is where a leaked entry
    would be hardest to spot — the bundle dir and `None` are shared keys, so a stale build
    would make a filter assertion pass for the wrong reason rather than fail. Clearing on
    both sides means neither an entry coming in nor one going out can do that.
    """
    reset_catalog_cache()
    yield
    reset_catalog_cache()


def _write(directory: Path, sis_type: str, body: str) -> None:
    (directory / f"{sis_type}_mapping.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def _config(directory: Path, sis_type: str, name: str, domains: list[str] | None = None) -> None:
    """A minimal, valid district config — optionally claiming `domains`.

    Built line-by-line rather than through `textwrap.dedent`, because an interpolated
    multi-line YAML block silently breaks dedent's common-prefix rule and yields a config
    that fails to load — which would make every "no match" row below pass for the WRONG
    reason (a degraded config matches nobody either).
    """
    lines = [
        "version: '1.0'",
        f"sis: {sis_type}",
        f"district_name: {name}",
    ]
    if domains is not None:
        lines.append("district_domains:")
        lines.extend(f"  - {d}" for d in domains)
    lines += [
        "global_config: {}",
        "mappings:",
        "  Students:",
        "    source_files:",
        "      a: A.txt",
        "    field_map:",
        '      "User ID": id',
    ]
    (directory / f"{sis_type}_mapping.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _broken(directory: Path, sis_type: str) -> None:
    _write(
        directory,
        sis_type,
        f"""
        version: '1.0'
        sis: {sis_type}
        mappings: [this never parses
        """,
    )


def _assert_all_loadable(directory: Path) -> None:
    """Guard against the fixture itself being the reason a list came back unfiltered."""
    broken = [s.sis_type for s in catalog(config_dir=directory) if not s.loaded_ok]
    assert not broken, f"fixture configs failed to load: {broken}"


@pytest.fixture
def three_districts(tmp_path: Path) -> Path:
    """Two claimed districts + one UNCLAIMED generic tier — the shipped shape in miniature."""
    _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])
    _config(tmp_path, "sd51myedbc", "SD51 - Boundary", ["sd51.bc.ca"])
    _config(tmp_path, "myedbc", "MyEd BC (generic)")  # unclaimed — no district_domains key
    _assert_all_loadable(tmp_path)
    reset_catalog_cache()  # the guard above filled the cache; every test starts from disk
    return tmp_path


def _ids(
    directory: Path,
    domain: str,
    *,
    saved_sis: str = "",
    picked_sis: str = "",
) -> list[str]:
    result = filtered_catalog(
        domain,
        saved_sis=saved_sis,
        picked_sis=picked_sis,
        config_dir=directory,
    )
    return [s.sis_type for s in result.summaries]


# --------------------------------------------------------------------------- #
# 1. ConfigSummary.district_domains — the tri-state the two-tier rule needs     #
# --------------------------------------------------------------------------- #
class TestTheTriState:
    def test_a_claimed_config_carries_its_domains(self, three_districts: Path) -> None:
        by_id = {s.sis_type: s for s in catalog(config_dir=three_districts)}

        assert by_id["sd48myedbc"].district_domains == ("sd48.bc.ca",)

    def test_a_config_with_no_key_is_DECLARED_EMPTY_not_unresolvable(self, three_districts: Path) -> None:
        """`()` — it loaded fine and claims nobody. Distinct from `None`, which means we could
        not read it at all. Only a NON-EMPTY tuple can ever exclude anyone."""
        by_id = {s.sis_type: s for s in catalog(config_dir=three_districts)}

        assert by_id["myedbc"].district_domains == ()
        assert by_id["myedbc"].loaded_ok is True

    def test_a_degraded_config_is_UNRESOLVABLE(self, tmp_path: Path) -> None:
        _broken(tmp_path, "wrecked")
        by_id = {s.sis_type: s for s in catalog(config_dir=tmp_path)}

        assert by_id["wrecked"].district_domains is None
        assert by_id["wrecked"].loaded_ok is False

    def test_unresolvable_and_not_loaded_ok_are_the_same_fact_in_both_directions(self, tmp_path: Path) -> None:
        """Pinned as an INVARIANT, not an accident: `district_domains is None ⟺ not loaded_ok`.

        Two fields encoding one fact is a DRY hazard, so the equivalence is asserted rather
        than assumed — if a future path ever produces a loaded config with `None` domains
        (or a degraded one with `()`), the filter's tier logic and `loaded_ok` would start
        telling different stories and this test says so.
        """
        _config(tmp_path, "fine", "Fine", ["fine.bc.ca"])
        _config(tmp_path, "bare", "Bare")
        _broken(tmp_path, "wrecked")

        for summary in catalog(config_dir=tmp_path):
            assert (summary.district_domains is None) is (not summary.loaded_ok), summary.sis_type

    def test_list_configs_carries_the_field_too(self, three_districts: Path) -> None:
        """`list_configs` is the unmemoised sibling — the field is resolved in the ONE build."""
        by_id = {s.sis_type: s for s in list_configs(config_dir=three_districts)}

        assert by_id["sd51myedbc"].district_domains == ("sd51.bc.ca",)


# --------------------------------------------------------------------------- #
# 2. Tier (i) — no identity / no match / show-all → EVERYTHING                  #
# --------------------------------------------------------------------------- #
class TestTierOneShowsEverything:
    def test_no_identity_shows_every_config(self, three_districts: Path) -> None:
        assert _ids(three_districts, "") == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_a_whitespace_only_domain_is_no_identity(self, three_districts: Path) -> None:
        assert _ids(three_districts, "   ") == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_no_match_shows_every_config(self, three_districts: Path) -> None:
        """A personal address, a board-wide address, a consultant — all land here, with the
        full list. A no-match is a first-class path, not an error path."""
        assert _ids(three_districts, "gmail.com") == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_tier_one_states_return_the_WHOLE_catalog(self, three_districts: Path) -> None:
        """Asserted on the ROWS, which is the only thing a picker paints.

        (There is deliberately no ``filtered`` companion field to interrogate, and since
        2026-08-04 no ``can_filter`` either: both existed for the show-all row, and a boolean
        whose only consumer is gone is a boolean that drifts.)
        """
        full = len(catalog(config_dir=three_districts))
        for domain in ("", "   ", "gmail.com"):
            result = filtered_catalog(domain, saved_sis="", config_dir=three_districts)
            assert len(result.summaries) == full, domain


# --------------------------------------------------------------------------- #
# 3. Tier (ii) — matched → the matching configs PLUS the saved district         #
# --------------------------------------------------------------------------- #
class TestTierTwoNarrows:
    def test_a_matched_admin_sees_their_district(self, three_districts: Path) -> None:
        assert _ids(three_districts, "sd48.bc.ca") == ["sd48myedbc"]

    def test_a_matched_admin_does_NOT_see_another_district(self, three_districts: Path) -> None:
        """The absence this feature exists for — paired with the positive above so it cannot
        pass by the filter never running."""
        assert "sd51myedbc" not in _ids(three_districts, "sd48.bc.ca")

    def test_the_generic_tier_is_hidden_from_a_MATCHED_admin(self, three_districts: Path) -> None:
        """Flag 4, as reconciled at R3: the unclaimed rule's "always shown" is scoped to the
        UNMATCHED / no-identity states. The generic base mapping is one more way to ship a
        subtly wrong roster, so a matched admin does not see it. Since 2026-08-04 that is
        FINAL for the session — the way back is clearing the stored address in Settings
        (`TestThereIsNoWideningKnob`), not a per-surface toggle."""
        assert "myedbc" not in _ids(three_districts, "sd48.bc.ca")

    def test_two_configs_sharing_one_domain_both_show(self, tmp_path: Path) -> None:
        """The LIVE common case: SD51's rostering + attendance tiers share one staff domain,
        so an SD51 admin matches BOTH (the matched-several state)."""
        _config(tmp_path, "sd51myedbc", "SD51 - Boundary", ["sd51.bc.ca"])
        _config(tmp_path, "sd51attendance", "SD51 - Boundary - Attendance", ["sd51.bc.ca"])
        _config(tmp_path, "myedbc", "MyEd BC (generic)")

        assert _ids(tmp_path, "sd51.bc.ca") == ["sd51attendance", "sd51myedbc"]


# --------------------------------------------------------------------------- #
# 3b. There is no widening knob — the escape is at the INPUT (2026-08-04)       #
# --------------------------------------------------------------------------- #
class TestThereIsNoWideningKnob:
    """The show-all row is gone from all four pickers, so a matched list is final.

    Two things have to stay true together, and each is worthless without the other: the
    toggle must really be gone (or the removal was cosmetic), and clearing the stored
    address must really widen the list (or a matched admin has NO way to reach another
    district and the removal has trapped them).
    """

    def test_no_argument_can_widen_a_matched_list(self) -> None:
        """Structural, not behavioural: a re-added ``show_all=`` parameter fails here even if
        no caller passes it yet, which is the point — the row grew back once as a parameter."""
        import inspect

        params = set(inspect.signature(filtered_catalog).parameters)

        assert params == {"domain", "saved_sis", "picked_sis", "config_dir"}, params

    def test_clearing_the_stored_address_is_the_escape(self, three_districts: Path) -> None:
        """The positive twin — and the reason the removal is not a trap.

        The same catalog, the same install: matched → one district; the address cleared
        (Settings → blank → Save, which empties the domain) → the whole list back.
        """
        assert _ids(three_districts, "sd48.bc.ca") == ["sd48myedbc"]
        assert _ids(three_districts, "") == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_the_view_layer_no_longer_ships_a_scope_row(self) -> None:
        """The factory retired with the rule. A screen cannot render a row that has no
        factory, and `components` is the ONLY sanctioned source of controls."""
        from src.ui_flet import components

        assert not hasattr(components, "list_scope_row")


# --------------------------------------------------------------------------- #
# 4. The saved district is present in EVERY rendered list                       #
# --------------------------------------------------------------------------- #
class TestTheSavedDistrictNeverDisappears:
    def test_the_saved_district_rides_a_matched_list_it_does_not_belong_to(self, three_districts: Path) -> None:
        """An admin set up for SD51 whose address matches SD48 must still see SD51 — their
        working sync's mapping can never vanish from the picker that edits it."""
        assert _ids(three_districts, "sd48.bc.ca", saved_sis="sd51myedbc") == ["sd48myedbc", "sd51myedbc"]

    def test_the_saved_GENERIC_tier_rides_a_matched_list(self, three_districts: Path) -> None:
        """Hiding the generic tier (flag 4) must never hide it from an install that USES it."""
        assert _ids(three_districts, "sd48.bc.ca", saved_sis="myedbc") == ["myedbc", "sd48myedbc"]

    def test_the_saved_district_is_not_duplicated_when_it_also_matched(self, three_districts: Path) -> None:
        assert _ids(three_districts, "sd48.bc.ca", saved_sis="sd48myedbc") == ["sd48myedbc"]

    def test_a_saved_id_that_is_not_a_real_config_is_never_fabricated(self, three_districts: Path) -> None:
        """`saved_sis` selects a row from the catalog; it never invents one. A hand-edited
        `config.json` naming a district we do not ship must not put a phantom option in a
        picker whose whole job is to be a structural allowlist."""
        assert _ids(three_districts, "sd48.bc.ca", saved_sis="sd99nonesuch") == ["sd48myedbc"]

    def test_saved_is_case_and_whitespace_normalised(self, three_districts: Path) -> None:
        """`sis_type` comes from a hand-editable settings file."""
        assert _ids(three_districts, "sd48.bc.ca", saved_sis="  SD51myedbc  ") == ["sd48myedbc", "sd51myedbc"]


class TestTheWORKINGPickNeverDisappears:
    """`picked_sis` — the district selected on this surface but not yet committed.

    The failure it closes: widen the list, pick a district outside your scope, narrow back —
    and the selection silently drops out of the list it is still the VALUE of, leaving a
    dropdown pointing at a row it no longer offers. `saved_sis` cannot cover this, because the
    whole point of the pick is that it has not been saved.
    """

    def test_a_picked_district_outside_the_match_SURVIVES_a_narrow(self, three_districts: Path) -> None:
        assert _ids(three_districts, "sd48.bc.ca", picked_sis="sd51myedbc") == ["sd48myedbc", "sd51myedbc"]

    def test_the_probe_is_not_vacuous(self, three_districts: Path) -> None:
        """Positive twin — WITHOUT the pick, that same district is correctly hidden."""
        assert _ids(three_districts, "sd48.bc.ca") == ["sd48myedbc"]

    def test_the_pick_and_the_saved_district_BOTH_ride(self, three_districts: Path) -> None:
        """Three different districts, all visible: matched + saved + picked."""
        visible = _ids(three_districts, "sd48.bc.ca", saved_sis="myedbc", picked_sis="sd51myedbc")

        assert visible == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_a_pick_equal_to_the_match_is_not_duplicated(self, three_districts: Path) -> None:
        assert _ids(three_districts, "sd48.bc.ca", picked_sis="sd48myedbc") == ["sd48myedbc"]

    def test_a_blank_pick_changes_nothing(self, three_districts: Path) -> None:
        """The `""` default is SAFE precisely because this parameter can only ever WIDEN — a
        caller with no transient selection passes nothing and loses nothing."""
        assert _ids(three_districts, "sd48.bc.ca", picked_sis="") == _ids(three_districts, "sd48.bc.ca")
        assert _ids(three_districts, "sd48.bc.ca", picked_sis="   ") == _ids(three_districts, "sd48.bc.ca")

    def test_a_pick_naming_no_real_config_is_never_fabricated(self, three_districts: Path) -> None:
        """Same rule as `saved_sis`: it SELECTS a catalog row, never invents one — so a stale
        widget value cannot put a phantom option into a structural allowlist."""
        assert _ids(three_districts, "sd48.bc.ca", picked_sis="sd99nonesuch") == ["sd48myedbc"]

    def test_the_pick_is_case_and_whitespace_normalised(self, three_districts: Path) -> None:
        assert _ids(three_districts, "sd48.bc.ca", picked_sis=" SD51MYEDBC ") == ["sd48myedbc", "sd51myedbc"]

    def test_a_pick_cannot_NARROW_anything(self, three_districts: Path) -> None:
        """The safety argument for the permissive default, asserted rather than asserted-in-prose:
        adding a pick never removes a row that was there without it."""
        without = set(_ids(three_districts, "sd48.bc.ca", saved_sis="sd51myedbc"))
        with_pick = set(_ids(three_districts, "sd48.bc.ca", saved_sis="sd51myedbc", picked_sis="myedbc"))

        assert without <= with_pick


# --------------------------------------------------------------------------- #
# 5. EXACT equality — never subdomain, suffix or wildcard                       #
# --------------------------------------------------------------------------- #
class TestMatchingIsExact:
    def test_a_subdomain_of_a_claimed_domain_does_NOT_match(self, three_districts: Path) -> None:
        """`mail.sd48.bc.ca` is not `sd48.bc.ca`. Over-matching is the dangerous direction
        under fail-open — it would scope an admin INTO a district that is not theirs — while
        under-matching lands them in tier (i) with the full list, which is always safe."""
        assert _ids(three_districts, "mail.sd48.bc.ca") == ["myedbc", "sd48myedbc", "sd51myedbc"]

    def test_the_ROOT_of_a_claimed_subdomain_does_NOT_match_either(self, tmp_path: Path) -> None:
        """The other direction of the same rule, pinned separately: a config claiming
        `mail.sd48.bc.ca` is not matched by `sd48.bc.ca`. A district needing a second domain
        gets it as its own row, never as a suffix rule."""
        _config(tmp_path, "sub", "Sub District", ["mail.sd48.bc.ca"])
        _config(tmp_path, "myedbc", "MyEd BC (generic)")

        assert _ids(tmp_path, "sd48.bc.ca") == ["myedbc", "sub"]

    def test_a_suffix_that_is_not_a_dotted_boundary_does_NOT_match(self, tmp_path: Path) -> None:
        _config(tmp_path, "target", "Target", ["sd48.bc.ca"])

        assert _ids(tmp_path, "notsd48.bc.ca") == ["target"]  # unmatched → tier (i), everything

    def test_the_TYPED_side_is_case_and_whitespace_normalised(self, tmp_path: Path) -> None:
        """The direction that actually carries traffic: a stored address is reduced by
        ``stored_identity_domain`` and compared case-insensitively, so an admin who typed
        `First.Last@SD48.BC.CA` still matches the lowercase row."""
        _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])
        _config(tmp_path, "myedbc", "MyEd BC (generic)")

        assert _ids(tmp_path, "  SD48.BC.CA ") == ["sd48myedbc"]

    def test_an_UPPERCASE_config_row_is_REFUSED_and_therefore_claims_nobody(self, tmp_path: Path) -> None:
        """A reality-read pin, not a guess: the config-side lowercase rule fails LOUD, so the
        catalog layer never sees a mixed-case row at all.

        `models._DISTRICT_DOMAIN_RE` refuses `SD48.BC.CA`, which fails the WHOLE config —
        `make validate-config` catches this for the bundled set, and a user-dropped YAML with
        the same mistake degrades. The two postures compose exactly as intended: fail LOUD at
        the config boundary, fail OPEN at the filter. The district is still listed, it claims
        nobody, and the admin whose domain it meant to claim sees everything.
        """
        _config(tmp_path, "loud", "Loud District", ["SD48.BC.CA"])
        _config(tmp_path, "myedbc", "MyEd BC (generic)")

        by_id = {s.sis_type: s for s in catalog(config_dir=tmp_path)}
        assert by_id["loud"].loaded_ok is False
        assert by_id["loud"].district_domains is None
        # `myedbc` leads every picker (`mapping_catalog._PINNED_FIRST`); "loud" claims nobody,
        # so the admin it meant to claim still sees BOTH rows.
        assert _ids(tmp_path, "sd48.bc.ca") == ["myedbc", "loud"]


# --------------------------------------------------------------------------- #
# 6. A degraded config can only ever WIDEN a list                               #
# --------------------------------------------------------------------------- #
class TestDegradedConfigsOnlyWiden:
    def test_a_broken_config_is_still_listed(self, tmp_path: Path) -> None:
        _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])
        _broken(tmp_path, "sd51myedbc")

        assert "sd51myedbc" in _ids(tmp_path, "")

    def test_a_broken_config_matches_NOBODY(self, tmp_path: Path) -> None:
        """It cannot be resolved, so it cannot claim anyone — and therefore cannot narrow
        anyone's list to itself."""
        _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])
        _broken(tmp_path, "sd51myedbc")

        assert _ids(tmp_path, "sd48.bc.ca") == ["sd48myedbc"]

    def test_the_admin_of_a_BROKEN_district_sees_the_FULL_list(self, tmp_path: Path) -> None:
        """The load-bearing safety property. An SD51 admin whose own YAML is corrupt matches
        nothing BY CONSTRUCTION, so they fall to tier (i) and see everything — including the
        broken row itself. A broken YAML can never hide the right district."""
        _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])
        _broken(tmp_path, "sd51myedbc")

        assert _ids(tmp_path, "sd51.bc.ca") == ["sd48myedbc", "sd51myedbc"]

    def test_a_config_whose_domains_are_unresolvable_warns_by_ID_only(self, tmp_path: Path, caplog) -> None:
        """One WARN naming the CONFIG ID — never the admin's domain, which is personal-adjacent
        data this layer has no business logging."""
        _broken(tmp_path, "sd51myedbc")

        with caplog.at_level("WARNING"):
            filtered_catalog("sd51.bc.ca", saved_sis="", config_dir=tmp_path)

        assert "sd51myedbc" in caplog.text
        assert "sd51.bc.ca" not in caplog.text


# --------------------------------------------------------------------------- #
# 7. Ordering + degenerate catalogs                                             #
# --------------------------------------------------------------------------- #
class TestOrderingAndDegenerateCatalogs:
    def test_the_visible_order_is_the_catalog_order(self, three_districts: Path) -> None:
        """Deterministic and STABLE across the two tiers: filtering removes rows, it never
        reorders them, so a scoped list reads as a shortened catalog rather than a re-sorted
        one, and a district keeps its relative position on every surface."""
        full = _ids(three_districts, "")
        narrowed = _ids(three_districts, "sd48.bc.ca", saved_sis="sd51myedbc")

        assert narrowed == [sis for sis in full if sis in set(narrowed)]

    def test_the_order_is_the_configured_enumeration_order(self, three_districts: Path) -> None:
        from src.config.loader import available_configs

        assert _ids(three_districts, "") == available_configs(three_districts)

    def test_an_empty_catalog_yields_an_empty_unfiltered_list(self, tmp_path: Path) -> None:
        """Nothing to show is not a filter — and nothing is fabricated to fill it either."""
        result = filtered_catalog("sd48.bc.ca", saved_sis="sd48myedbc", config_dir=tmp_path)

        assert result.summaries == ()

    def test_a_single_config_catalog_is_never_narrowed(self, tmp_path: Path) -> None:
        _config(tmp_path, "sd48myedbc", "SD48 - Sea to Sky", ["sd48.bc.ca"])

        result = filtered_catalog("sd48.bc.ca", saved_sis="", config_dir=tmp_path)

        assert [s.sis_type for s in result.summaries] == ["sd48myedbc"]


# --------------------------------------------------------------------------- #
# 10. Label disambiguation — no two rendered rows may read identically          #
# --------------------------------------------------------------------------- #
class TestDisambiguatedLabels:
    def _summaries(self, *pairs: tuple[str, str]) -> tuple[ConfigSummary, ...]:
        return tuple(
            ConfigSummary(
                sis_type=sis,
                district_name=name,
                output_entities=(),
                output_labels=(),
                source_file_count=0,
                loaded_ok=True,
                district_domains=(),
                # A SHIPPED row: the provenance marker is `mapping_catalog`'s own axis and is
                # exercised in `tests/test_ui_flet_mapping_catalog.py` through the real
                # two-dir pair. These rows keep it off so the collision rule is read alone.
                origin="bundled",
            )
            for sis, name in pairs
        )

    def test_distinct_names_are_left_alone(self) -> None:
        labels = disambiguated_labels(self._summaries(("a", "Alpha"), ("b", "Beta")))

        assert labels == {"a": "Alpha", "b": "Beta"}

    def test_a_collision_appends_the_raw_sis_id_to_EVERY_member(self) -> None:
        """Suffixing only the second row would leave the first still reading ambiguously —
        the admin cannot tell which of the two the bare label refers to."""
        labels = disambiguated_labels(self._summaries(("sd51myedbc", "SD51"), ("sd51attendance", "SD51")))

        assert labels == {"sd51myedbc": "SD51 (sd51myedbc)", "sd51attendance": "SD51 (sd51attendance)"}

    def test_a_three_way_collision_is_fully_disambiguated(self) -> None:
        labels = disambiguated_labels(self._summaries(("a", "X"), ("b", "X"), ("c", "X")))

        assert len(set(labels.values())) == 3

    def test_collisions_are_detected_after_whitespace_and_case_folding(self) -> None:
        """Two labels differing only in case or padding read identically on screen."""
        labels = disambiguated_labels(self._summaries(("a", "SD51 - Boundary"), ("b", " sd51 - boundary ")))

        assert len(set(labels.values())) == 2
        assert all("(" in label for label in labels.values())

    def test_every_summary_gets_a_label(self) -> None:
        summaries = self._summaries(("a", "Alpha"), ("b", "Beta"), ("c", "Beta"))

        assert set(disambiguated_labels(summaries)) == {"a", "b", "c"}

    def test_a_blank_district_name_still_yields_something_readable(self) -> None:
        labels = disambiguated_labels(self._summaries(("solo", "")))

        assert labels["solo"] == "solo"

    def test_the_bundled_catalog_needs_no_disambiguation_today(self) -> None:
        """The shipped-set guarantee stays G13's (the `district_name` lines are distinct since
        S3); this runtime pass covers user-dropped YAMLs we do not control. If it ever starts
        firing on the bundled set, G13 has regressed and THAT is the bug to fix.

        Asserted as "every label is the district's own name, untouched" rather than by looking
        for the suffix's parentheses — three bundled names legitimately contain their own
        (`myBlueprint+ (full)`), so a punctuation probe would have been a false positive
        dressed as a guarantee.
        """
        summaries = catalog(config_dir=bundle_mappings_dir())
        labels = disambiguated_labels(summaries)

        assert labels == {s.sis_type: s.district_name for s in summaries}
        assert len(set(labels.values())) == len(summaries)


# --------------------------------------------------------------------------- #
# 11. Memoisation — the launch path must not re-parse 11 YAMLs per paint        #
# --------------------------------------------------------------------------- #
class TestMemoisation:
    def test_the_catalog_is_built_once_per_session(self, three_districts: Path, monkeypatch) -> None:
        import src.ui_flet.mapping_catalog as mc

        calls: list[str] = []
        real = mc.summarize_config
        monkeypatch.setattr(mc, "summarize_config", lambda sis, **kw: calls.append(sis) or real(sis, **kw))

        catalog(config_dir=three_districts)
        catalog(config_dir=three_districts)
        catalog(config_dir=three_districts)

        assert len(calls) == 3, "one build of three configs, not three builds"

    def test_reset_forces_a_rebuild(self, three_districts: Path, monkeypatch) -> None:
        """The positive twin: the counter above really is counting a mechanism that runs."""
        import src.ui_flet.mapping_catalog as mc

        calls: list[str] = []
        real = mc.summarize_config
        monkeypatch.setattr(mc, "summarize_config", lambda sis, **kw: calls.append(sis) or real(sis, **kw))

        catalog(config_dir=three_districts)
        reset_catalog_cache()
        catalog(config_dir=three_districts)

        assert len(calls) == 6

    def test_the_saved_district_is_NEVER_memoised(self, three_districts: Path) -> None:
        """The invalidation rule, pinned. A Mapping Apply changes `sis_type` and nothing else;
        `saved_sis` is a per-CALL argument, never part of the cached build, so the next mount
        reflects the new district with no cache to invalidate. The cache holds YAML-derived
        facts only."""
        first = _ids(three_districts, "sd48.bc.ca", saved_sis="sd51myedbc")
        second = _ids(three_districts, "sd48.bc.ca", saved_sis="myedbc")

        assert first == ["sd48myedbc", "sd51myedbc"]
        assert second == ["myedbc", "sd48myedbc"]

    def test_different_config_dirs_do_not_share_a_cache_entry(self, tmp_path: Path) -> None:
        one, two = tmp_path / "one", tmp_path / "two"
        one.mkdir()
        two.mkdir()
        _config(one, "alpha", "Alpha")
        _config(two, "beta", "Beta")

        assert [s.sis_type for s in catalog(config_dir=one)] == ["alpha"]
        assert [s.sis_type for s in catalog(config_dir=two)] == ["beta"]


# --------------------------------------------------------------------------- #
# 12. Any raise degrades to unfiltered — the filter never fails closed          #
# --------------------------------------------------------------------------- #
class TestFailOpen:
    def test_an_enumeration_failure_degrades_instead_of_raising(self, monkeypatch, tmp_path: Path) -> None:
        import src.ui_flet.mapping_catalog as mc

        def _boom(*_a, **_kw):
            raise OSError("the mappings dir went away")

        monkeypatch.setattr(mc, "available_configs", _boom)

        result = filtered_catalog("sd48.bc.ca", saved_sis="sd48myedbc", config_dir=tmp_path)

        assert result.summaries == ()

    def test_a_matching_failure_degrades_to_the_FULL_list(self, monkeypatch, three_districts: Path, caplog) -> None:
        """A raise in the matching layer costs the admin a short list, never a district."""
        import src.ui_flet.mapping_catalog as mc

        def _boom(*_a, **_kw):
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr(mc, "resolve_domain", _boom)

        with caplog.at_level("DEBUG"):
            result = filtered_catalog("sd48.bc.ca", saved_sis="", config_dir=three_districts)

        assert [s.sis_type for s in result.summaries] == ["myedbc", "sd48myedbc", "sd51myedbc"]
        # This branch logs with `exc_info=True`, and the traceback it renders is the ONE place
        # on this path that has the domain in a live frame — so the PII bar is asserted right
        # where the temptation to "just include the value for diagnostics" lives.
        assert "sd48.bc.ca" not in caplog.text, "the domain reached the fail-open log"
        assert "showing every district" in caplog.text, "the WARN is missing; the check above is vacuous"

    def test_a_transient_build_failure_is_NOT_cached(self, monkeypatch, three_districts: Path) -> None:
        """`lru_cache` does not memoise a raise — a claim two documents lean on, so pinned.

        Caching the degraded empty result would empty every picker for the rest of the
        session over one blip. The first call degrades; the second, with the fault gone, is
        whole again.
        """
        import src.ui_flet.mapping_catalog as mc

        calls = {"n": 0}
        real = mc.list_configs

        def _once(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("the mappings dir blinked")
            return real(*a, **kw)

        monkeypatch.setattr(mc, "list_configs", _once)

        first = filtered_catalog("", saved_sis="", config_dir=three_districts)
        second = filtered_catalog("", saved_sis="", config_dir=three_districts)

        assert first.summaries == (), "the transient failure really did degrade"
        assert len(second.summaries) == 3, "the raise was cached — every picker would stay empty"

    def test_the_failure_probe_is_not_vacuous(self, three_districts: Path) -> None:
        """Positive twin for both tests above — with nothing patched, the filter DOES narrow."""
        result = filtered_catalog("sd48.bc.ca", saved_sis="", config_dir=three_districts)

        assert [s.sis_type for s in result.summaries] == ["sd48myedbc"]


# --------------------------------------------------------------------------- #
# 13. district_domain_index rides the SAME build (one resolution path)          #
# --------------------------------------------------------------------------- #
class TestOneResolutionPath:
    def test_the_index_agrees_with_the_catalog(self, three_districts: Path) -> None:
        index = district_domain_index(config_dir=three_districts)

        assert index == {s.sis_type: s.district_domains or () for s in catalog(config_dir=three_districts)}

    def test_a_degraded_config_reads_as_unclaimed_in_the_index(self, tmp_path: Path) -> None:
        """`None` (unresolvable) flattens to `()` for the resolver, which only ever asks
        "does this config claim my domain?" — and an unreadable one claims nobody."""
        _broken(tmp_path, "wrecked")

        assert district_domain_index(config_dir=tmp_path) == {"wrecked": ()}

    def test_the_index_reuses_the_memoised_build(self, three_districts: Path, monkeypatch) -> None:
        import src.ui_flet.mapping_catalog as mc

        calls: list[str] = []
        real = mc.summarize_config
        monkeypatch.setattr(mc, "summarize_config", lambda sis, **kw: calls.append(sis) or real(sis, **kw))

        district_domain_index(config_dir=three_districts)
        filtered_catalog("sd48.bc.ca", saved_sis="", config_dir=three_districts)

        assert len(calls) == 3, "the identity page and the pickers share ONE catalog build"

    def test_an_enumeration_failure_yields_an_empty_index(self, monkeypatch, tmp_path: Path) -> None:
        import src.ui_flet.mapping_catalog as mc

        monkeypatch.setattr(mc, "available_configs", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("gone")))

        assert district_domain_index(config_dir=tmp_path) == {}


# --------------------------------------------------------------------------- #
# 14. The shipped rows, end to end                                              #
# --------------------------------------------------------------------------- #
class TestAgainstTheShippedCatalog:
    def test_each_shipped_domain_narrows_to_exactly_its_district(self) -> None:
        """The instrument the hashed design could never have had: the real bundled values,
        resolved through the real filter."""
        bundle = bundle_mappings_dir()
        expected = {
            "sd40.bc.ca": ["sd40myedbc"],
            "sd48.bc.ca": ["sd48myedbc"],
            "sd54.bc.ca": ["sd54myedbc"],
            "prn.bc.ca": ["sd60myedbc"],
            "sd74.bc.ca": ["sd74myedbc"],
            # SD51 ships two tiers behind one staff domain — the live matched-several case.
            "sd51.bc.ca": ["sd51attendance", "sd51myedbc"],
        }

        for domain, ids in expected.items():
            assert _ids(bundle, domain) == ids, domain

    def test_an_unknown_domain_sees_all_eleven(self) -> None:
        """Fail-open: an unplaceable admin sees the WHOLE catalog. The SET is the property here.

        Compared as a set, not a sequence: picker ORDER is `mapping_catalog._PINNED_FIRST`'s
        business (and pinned there), while what this test defends is that no district is
        withheld from someone we could not place.
        """
        from src.config.loader import available_configs

        bundle = bundle_mappings_dir()

        assert sorted(_ids(bundle, "someone.example.com")) == sorted(available_configs(bundle))

    def test_a_matched_admin_never_sees_a_myblueprint_tier_they_did_not_choose(self) -> None:
        bundle = bundle_mappings_dir()

        visible = _ids(bundle, "sd74.bc.ca")

        assert visible == ["sd74myedbc"]
        for generic in ("myedbc", "mbp_all", "mbp_core", "mbponly"):
            assert generic not in visible
