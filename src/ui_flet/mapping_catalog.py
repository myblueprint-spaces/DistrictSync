"""Pure config-catalog derivation — "which district config is active, and what does it produce?"

PURE + COUNTED (no ``flet`` import): given a SIS id, load the district mapping config and
derive a PII-free ``ConfigSummary`` — the friendly district name, the plain-language list of
output CSVs it emits (from ``enabled_entities``), and the count of distinct GDE source files it
reads. The Mapping surface (``screens/mapping.py``) renders these to let an admin REVIEW the
active mapping and SWITCH to a different pre-built one, seeing what each produces first.

**Single-sourced with the pipeline.** ``output_labels`` is derived by the SAME empty-means-all
rule the core uses to decide which entities (→ CSVs) a config emits
(``MappingConfig.active_entities()`` — enabled ∩ defined; empty/absent = all), ordered by ``home_status``'s rostering-then-myBlueprint entity tuples and labelled through the
single-source ``home_status.ENTITY_LABELS`` map — so the Mapping summary can never disagree
with Home / Run History / the actual output CSV set.

**TOTAL over a failing config (reliability-resilience).** ``load_config`` is strict at the
boundary — a partner-authored broken YAML in ``~/.districtsync/mappings/`` raises
``FileNotFoundError`` / ``ValueError``. ``summarize_config`` wraps it: a raise → a SAFE degraded
``ConfigSummary`` (``loaded_ok=False``, ``district_name`` = the raw id via
``friendly_district_name``'s fallback, ``output_labels=()``, ``source_file_count=0``), NEVER a
crash. ``list_configs`` therefore always returns one summary per enumerated id, some degraded.

**Privacy (LIVE/top).** A ``ConfigSummary`` carries only config STRUCTURE — a district name,
output-CSV labels, a file count. It carries NO student PII (a config is a column-name mapping,
not data) and NEVER interpolates a raw exception string (a Pydantic/OS error text) into any
admin-facing field — a load failure is named by category (``loaded_ok=False``), never echoed.
``district_domains`` (0038) adds one more structural fact of the same kind: each config's
PUBLIC district staff email domains — an organisational fact the district itself publishes,
never personal data, and never a student's address.

**The district-list filter (0038 S5) — one choke point, two tiers.** ``filtered_catalog`` is
the SINGLE place that decides which district rows a picker shows, consumed by all four
pickers (the wizard District step, its auto-select seed, Settings' folders card, Convert and
Mapping). The rule, and why it is shaped this way, is on :func:`filtered_catalog`. Since
2026-08-04 the scoping is UNCONDITIONAL — the per-surface "Show all districts" row is
retired, and the only escape is clearing the stored address in Settings. Two structural
properties are worth knowing before reading anything else here:

* **the admin's email never enters this module.** The caller passes a bare DOMAIN
  (``identity_gate.stored_identity_domain(cfg)``); the plaintext address stays confined to
  the identity/Settings screens and ``AppConfig``. Nothing here logs the domain either;
* **a filter can only ever be wrong in the WIDENING direction.** Matching is exact equality
  against a non-empty list, so an unreadable config, an unknown domain and a typo all
  resolve to "no match", and no match means the FULL list.

Layer note vs CLAUDE.md's UI/ETL isolation: the matching rule sits in this UI-layer module
deliberately — it is presentation scoping (which rows a picker shows), it consumes config
data this module already loads, and the ETL structurally cannot see the key
(``MappingConfig.to_raw_dict`` emits only ``mappings`` + ``global_config``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.config.loader import available_configs, load_config
from src.ui_flet.home_status import (
    _MYBLUEPRINT_ENTITIES,
    _ROSTERING_ENTITIES,
    ENTITY_LABELS,
)
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.identity_gate import resolve_domain
from src.ui_flet.schedule_status import ScheduleState

logger = logging.getLogger(__name__)

# The canonical entity ORDER for the output-CSV summary — rostering entities first, then the
# myBlueprint+ / attendance keys — reusing `home_status`'s entity tuples so the label order
# matches Home / Run History. Any enabled entity NOT in these tuples (a non-standard key) is
# appended after, so a partner-defined extra entity still surfaces (total).
_ENTITY_ORDER: tuple[str, ...] = tuple(_ROSTERING_ENTITIES) + tuple(_MYBLUEPRINT_ENTITIES)


@dataclass(frozen=True)
class ConfigSummary:
    """A PII-free structural summary of one district mapping config.

    ``sis_type`` is the raw id (a secondary technical hint only — never the primary label).
    ``district_name`` is the friendly label (or the raw id when the config has no
    ``district_name`` / failed to load). ``output_labels`` is the plain-language list of output
    CSVs it emits, in the canonical order. ``source_file_count`` is how many distinct GDE files
    it reads. ``loaded_ok`` is ``False`` when the config failed to load — a SAFE degraded
    summary the view renders calmly (Apply disabled), NEVER a crash / a raw error.

    ``district_domains`` (0038 S5) is the config's PUBLIC district staff email domains — an
    organisational fact the district publishes itself, never personal data. It is a
    **tri-state**, and the filter needs all three apart:

    * ``None`` — UNRESOLVABLE. We could not read the config, so we do not know what it claims;
      it therefore claims nobody and can never exclude anyone (a broken YAML only widens).
    * ``()`` — DECLARED EMPTY. It loaded cleanly and claims nobody: an *unclaimed* config (the
      base mapping, the ``mbp_*`` tiers, a district before its domain row lands).
    * a non-empty tuple — CLAIMED. The only state that can ever narrow a list.

    ``None ⟺ not loaded_ok`` holds today and is pinned in both directions by
    ``tests/test_ui_flet_filtered_catalog.py`` — two fields, one fact, asserted rather than
    assumed, so a future path that breaks the equivalence surfaces as a red test instead of a
    tier-logic divergence.

    ``output_entities`` (0038 S7) is the same produced set as ``output_labels`` but as raw
    entity KEYS, in the same canonical order — the truth Home's roster-size clause needs
    (``home_status.size_clause`` counts by key, and a record's flat count keys are keys, not
    labels). ``output_labels`` is derived FROM it, so the ordering and the produced set are
    decided exactly once.
    """

    sis_type: str
    district_name: str
    output_entities: tuple[str, ...]
    output_labels: tuple[str, ...]
    source_file_count: int
    loaded_ok: bool
    district_domains: tuple[str, ...] | None


def _degraded(sis_type: str, *, config_dir: Path | None) -> ConfigSummary:
    """The safe degraded summary for a config that failed to load — no PII, no raw error text.

    ``district_name`` falls back to the raw id via ``friendly_district_name``'s totality (itself
    total — a nested load failure returns the raw id, never raises). ``district_domains`` is
    ``None`` (unresolvable), which is what keeps a broken YAML from ever matching anybody.
    """
    return ConfigSummary(
        sis_type=sis_type,
        district_name=friendly_district_name(sis_type, config_dir=config_dir) or sis_type,
        output_entities=(),
        output_labels=(),
        source_file_count=0,
        loaded_ok=False,
        district_domains=None,
    )


def summarize_config(sis_type: str, *, config_dir: Path | None = None) -> ConfigSummary:
    """Summarize one district config — TOTAL: a load failure → a safe degraded summary, never a raise.

    ``config_dir`` is a test seam passed straight through to ``load_config`` /
    ``friendly_district_name`` (overriding the ``~/.districtsync`` search dirs), so this is
    unit-testable against a fixture mappings dir with no home dependency.
    """
    try:
        cfg = load_config(sis_type, config_dir)
        # `active_entities` is enabled ∩ DEFINED: an entity enabled but absent from `mappings`
        # produces no CSV (the pipeline's own enforcement gates on `entity in mappings` too),
        # so the summary reflects only what actually gets produced (truthful, never a phantom CSV).
        produced = cfg.active_entities()
        output_entities = _ordered_entities(produced)
        source_file_count = _source_file_count(cfg, produced)
        return ConfigSummary(
            sis_type=sis_type,
            district_name=friendly_district_name(sis_type, config_dir=config_dir) or sis_type,
            output_entities=output_entities,
            output_labels=_output_labels(output_entities),
            source_file_count=source_file_count,
            loaded_ok=True,
            district_domains=tuple(cfg.district_domains or ()),
        )
    except Exception:  # noqa: BLE001 - total: any load failure degrades, never surfaces the raw error
        # ONE WARN naming the config ID — the support signal a silent degradation would cost,
        # and the only thing worth saying: an unreadable config matches nobody and produces
        # nothing. Never the raw error (privacy), and never the admin's domain (the filter
        # path holds one, and this line is on it).
        logger.warning(
            "The district mapping %r could not be read; it is shown but can match nobody.",
            sis_type,
        )
        return _degraded(sis_type, config_dir=config_dir)


def _ordered_entities(enabled: set[str]) -> tuple[str, ...]:
    """The produced entity KEYS in the canonical order — the one place that ordering is decided.

    Canonical keys (rostering then myBlueprint+) lead in ``_ENTITY_ORDER`` order; any enabled
    entity NOT in that spine (``StudentAttendance``, a non-standard partner key) is appended
    after, sorted for a stable order. ``_output_labels`` and ``ConfigSummary.output_entities``
    both come from here, so every PICKER's label order is decided in one place.

    **It does NOT decide Home's "which entity leads" rule.** ``home_status.size_clause``
    collects ``output_entities`` into a SET and then walks ``SIZE_NOUNS`` — that dict's order is
    the lead rule (as its own docstring says), and this ordering is discarded on that path:
    forward and reversed, the clause is identical. Claiming the two can never diverge would be
    claiming a coupling the code does not have.
    """
    ordered: list[str] = [key for key in _ENTITY_ORDER if key in enabled]
    ordered.extend(sorted(enabled - set(_ENTITY_ORDER)))
    return tuple(ordered)


def _output_labels(ordered: tuple[str, ...]) -> tuple[str, ...]:
    """Map already-ordered entity keys to plain-language CSV labels (raw-key fallback, total)."""
    return tuple(ENTITY_LABELS.get(key, key) for key in ordered)


def _source_file_count(cfg, produced: set[str]) -> int:  # type: ignore[no-untyped-def]
    """Count DISTINCT source filenames across the produced entities (the same file often feeds several).

    ``produced`` is always a subset of ``cfg.mappings.keys()`` (the caller intersects), so every
    key resolves to a defined ``EntityConfig``.
    """
    filenames: set[str] = set()
    for name in produced:
        filenames.update(cfg.mappings[name].source_files.values())
    return len(filenames)


# The config ids pinned to the TOP of every district picker, in this order. Everything else
# keeps ``available_configs`` order (alphabetical by id).
#
# ``myedbc`` is the generic MyEducationBC mapping — the row an admin whose district we cannot
# place is most likely to want, and the one the product documents as the starting point.
# Alphabetical order buried it FOURTH, behind the three ``mbp_*`` myBlueprint+ tiers, which are
# the rows fewest admins want (QA, 2026-08-18).
#
# **Pinned HERE and not in ``available_configs``** deliberately: that function is the ETL/CLI's
# enumeration (``--sis`` validation, ``make validate-config``, the pinned 12-config count) and
# its alphabetical order is depended on there. This is presentation ordering, so it belongs in
# the UI catalog with the rest of the picker rules.
_PINNED_FIRST: tuple[str, ...] = ("myedbc",)


def _pinned_order(sis_types: Sequence[str]) -> list[str]:
    """``sis_types`` reordered so any ``_PINNED_FIRST`` id leads, the rest keeping their order.

    Pure and TOTAL: a pinned id that is not present is simply absent (no phantom row), and an
    id that is present but unpinned can never be dropped — the result is a permutation of the
    input, which is what keeps this from ever hiding a district.
    """
    present = [sis for sis in _PINNED_FIRST if sis in sis_types]
    return present + [sis for sis in sis_types if sis not in present]


def list_configs(*, config_dir: Path | None = None) -> list[ConfigSummary]:
    """Summarize every discoverable district config — ``_PINNED_FIRST``, then ``available_configs`` order.

    One ``ConfigSummary`` per enumerated SIS id — some possibly degraded (a broken config is
    listed, never omitted or crashed on). ``config_dir`` is the test seam.

    This is the ONE place picker ORDER is decided, and every district list inherits it:
    ``catalog`` memoises this, ``_matched_subset`` preserves the order it is given, and all
    four pickers render through ``filtered_catalog``. Ordering never changes the row SET.
    """
    return [
        summarize_config(sis_type, config_dir=config_dir) for sis_type in _pinned_order(available_configs(config_dir))
    ]


# --------------------------------------------------------------------------- #
# The ONE catalog build — memoised for the session (0038 S5)                    #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def _cached_catalog(config_dir: Path | None) -> tuple[ConfigSummary, ...]:
    """The memoised build. Keyed on ``config_dir`` alone — see :func:`catalog`.

    Deliberately NOT total: the fail-open handling lives in :func:`catalog`, one level out,
    because ``lru_cache`` does not memoise a raise. A transient enumeration failure therefore
    costs one degraded call and is retried on the next one — caching the empty result would
    empty every picker for the rest of the session.
    """
    return tuple(list_configs(config_dir=config_dir))


def catalog(*, config_dir: Path | None = None) -> tuple[ConfigSummary, ...]:
    """Every discoverable config, summarised ONCE per session. TOTAL — never raises.

    The single resolution path behind the identity page, the Home cards, and all four
    district pickers, so those surfaces can never disagree about what exists or who claims
    what. Parsing eleven bundled YAMLs costs ~210 ms on a district server; that ran on the
    launch path and again per Settings Save before this cache existed.

    **What is cached, and what deliberately is not.** Only YAML-derived facts (the config
    ids and their summaries) are memoised. The admin's domain, the saved ``sis_type`` and
    the show-all toggle are all per-CALL arguments to :func:`filtered_catalog` — so a Mapping
    **Apply** (which changes ``sis_type`` and nothing else) is reflected on the next mount
    with nothing to invalidate. That is the whole invalidation rule, and it is pinned by
    ``tests/test_ui_flet_filtered_catalog.py::TestMemoisation``.

    The residual, stated: a YAML dropped into ``~/.districtsync/mappings/`` **while the app
    is running** is not picked up until the next launch (or an explicit
    :func:`reset_catalog_cache`). Installing a mapping is a restart-shaped act, and the
    alternative — re-parsing on every paint — is the cost this cache exists to remove.

    ``list_configs`` remains the unmemoised sibling for callers (and tests) that must read
    the disk right now.
    """
    try:
        return _cached_catalog(config_dir)
    except Exception:  # noqa: BLE001 - fail OPEN: an unreadable catalog still opens the app
        logger.warning("Could not list the district mappings; the full district list will be shown.", exc_info=True)
        return ()


@lru_cache(maxsize=8)
def _cached_summary(sis_type: str, config_dir: Path | None) -> ConfigSummary:
    """One config's summary, memoised for the session. ``summarize_config`` is already TOTAL."""
    return summarize_config(sis_type, config_dir=config_dir)


def active_output_entities(sis_type: str, *, config_dir: Path | None = None) -> tuple[str, ...]:
    """The ordered entity keys ``sis_type`` actually produces — TOTAL, ``()`` when unknowable.

    Routes through ``MappingConfig.active_entities()`` (via :func:`summarize_config`), which
    CLAUDE.md names as THE accessor for the ``enabled_entities`` selection — never a re-spelled
    ``enabled_entities or []``, and never a "non-zero counts" heuristic (that would read "this
    district produced no students" as "this config emits no Students", hiding the exact alarm
    Home's size clause exists to raise).

    **Why a second, narrower cache than :func:`catalog`.** The one caller is Home's mount, the
    flagship surface, which needs exactly ONE config; going through the eleven-YAML catalog
    build there is the ~210 ms cost the S4b note deliberately refuses to pay on that path
    (pinned by ``tests/test_ui_flet_home_identity_cards.py``). Keyed per ``sis_type`` so a
    Mapping **Apply** resolves the new district on the next mount with nothing to invalidate;
    :func:`reset_catalog_cache` clears both caches, so the support/test seam stays one call.

    Degrades to ``()`` on anything unreadable — which makes the size clause vanish rather than
    guess (``home_status.size_clause``).
    """
    sis = sis_type.strip()
    if not sis:
        return ()
    try:
        return _cached_summary(sis, config_dir).output_entities
    except Exception:  # noqa: BLE001 - defence in depth: an unknowable config costs a sentence, never Home
        logger.warning("Could not read the district mapping %r; Home's roster-size line is omitted.", sis)
        return ()


def reset_catalog_cache() -> None:
    """Drop BOTH memoised builds (the full catalog and the per-config summaries).

    The support/test seam; the app never needs to call it. Both are cleared together so a
    caller that re-reads the disk after dropping a YAML cannot get a fresh catalog beside a
    stale single-config summary.
    """
    _cached_catalog.cache_clear()
    _cached_summary.cache_clear()


def district_domain_index(*, config_dir: Path | None = None) -> dict[str, tuple[str, ...]]:
    """``{config id: its PUBLIC district_domains}`` — the identity resolver's data. TOTAL.

    A thin projection of :func:`catalog`, NOT a second builder: S4a's launch page, the
    Settings identity section and S4b's Home cards keep calling this, and since S5 they
    share the ONE memoised build with the pickers. The pure resolver
    (``identity_gate.resolve_domain`` / ``matched_state``) is unchanged by that — it takes
    the index as DATA.

    The tri-state flattens to two here on purpose: the resolver only ever asks "does this
    config claim my domain?", and an UNRESOLVABLE config (``None``) claims nobody exactly as
    a declared-empty one does. The distinction that matters — *may this config exclude
    anyone?* — belongs to :func:`filtered_catalog`, which reads the summaries directly.

    Fail-open is structural in both directions: an enumeration failure yields ``{}`` (an
    empty index matches nobody, and no match means the FULL list), and a single config that
    fails to load reads as unclaimed with ONE WARN naming its id (emitted by
    :func:`summarize_config`).
    """
    return {summary.sis_type: summary.district_domains or () for summary in catalog(config_dir=config_dir)}


# --------------------------------------------------------------------------- #
# The district-list filter (0038 S5) — the one choke point every picker uses    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FilteredCatalog:
    """What one picker should render: the rows, in catalog order.

    A one-field wrapper rather than a bare tuple, deliberately — it names the ONE return
    type of the choke point, so a reader of any of the four pickers can see that the rows
    came through :func:`filtered_catalog` and not from ``catalog()`` directly.

    **The ``can_filter`` companion retired with the show-all row** (2026-08-04, owner
    decision): it existed only to decide whether that row rendered, and a boolean whose
    single consumer is gone is a boolean that will drift. "Is this list narrowed?" is now
    answered where it matters — by comparing against :func:`catalog` — and nowhere in the
    view layer.
    """

    summaries: tuple[ConfigSummary, ...]


def _normalise_sis(value: str) -> str:
    """Trim + lowercase a config id for comparison — ``sis_type`` is hand-editable."""
    return (value or "").strip().lower()


def filtered_catalog(
    domain: str,
    *,
    saved_sis: str,
    picked_sis: str = "",
    config_dir: Path | None = None,
) -> FilteredCatalog:
    """Which district rows to show this admin. TOTAL — never raises, never fails closed.

    **The rule, in two tiers** (plan 0038, reconciled at the R3 delta gate):

    (i)  **no identity / no match → ALL configs**, unclaimed ones included.
         Fail-open is the default state of the world, and it is where every admin whose
         address we cannot place lands: a personal or board-wide address, a consultant, a
         typo, a district whose domain row has not shipped yet.
    (ii) **matched** — at least one config's *resolved, non-empty* ``district_domains``
         contains ``domain`` — → exactly the matching configs, PLUS ``saved_sis`` and
         ``picked_sis``, unconditionally.

    **Matching is EXACT, case-normalised domain equality** (``identity_gate.resolve_domain``)
    — never subdomain, suffix or wildcard. ``mail.sd48.bc.ca`` does not match ``sd48.bc.ca``
    in either direction. Over-matching is the dangerous direction under fail-open, because it
    scopes an admin INTO a district that is not theirs; under-matching drops them into tier
    (i) with the full list, which is always safe.

    **A row is hidden only when some OTHER list claims it.** Four consequences, each the
    answer to a way this could hurt someone:

    * a config with no domains (``()``) or unreadable ones (``None``) can never match, so a
      broken or not-yet-claimed YAML only ever WIDENS a list;
    * an admin whose own district's row is missing or broken matches nothing BY
      CONSTRUCTION and therefore sees everything — *a district disappearing on its own
      admin* is the failure this rule makes unrepresentable;
    * **the SAVED district is present in every rendered list.** ``saved_sis`` is a required
      keyword-only parameter for exactly that reason (CLAUDE.md: no permissive default on a
      safety-relevant parameter) — the escape is a property of the choke point, not a thing
      each of the four call sites has to remember;
    * **the WORKING pick is too.** ``picked_sis`` is the district the admin has selected on
      this surface but not yet committed — it unions in exactly like ``saved_sis``, so a
      dropdown can never end up pointing at a row it no longer offers. Its original job was
      surviving a show-all → pick → narrow-back round trip; with that toggle retired it now
      guards the remaining re-scope paths (a mid-visit Settings/Mapping change under a
      surface that re-derives its list) and costs nothing where a surface has no pending pick.

    Both escapes SELECT a catalog row and never fabricate one, so a hand-edited
    ``config.json`` (or a stale widget value) naming a district we do not ship cannot put a
    phantom option into what is structurally an allowlist.

    ``picked_sis`` defaults to ``""``, and that default is SAFE where ``saved_sis``'s would
    not be: this parameter can only ever WIDEN the result. Omitting it can cost a caller the
    retention of a transient selection it may not even have; omitting ``saved_sis`` would
    hide a district the install is actively converting. Different blast radii, different
    rules — so a read-only surface may leave it out, and every surface that lets an admin
    PICK passes it.

    **There is no per-surface "Show all districts" escape any more** (2026-08-04, owner
    decision — see ``docs/claugentic-DECISIONS.md``). A matched admin sees their own
    district's rows and no other district's, on all four pickers, so the scoping is a
    property of the install rather than of whichever screen was last toggled. The escape
    that remains is the HONEST one and it is at the input, not the output: clearing the
    stored address in Settings ("Who looks after this sync" → blank → Save) removes the one
    input this filter has, and every list widens on the next mount.

    Read that alongside tier (i): this narrows what a picker OFFERS; it withholds nothing.
    Every mapping still ships in the executable, and an admin whose address we cannot place
    still sees all of them. It is list scoping, not access control — the district domains
    are public, so typing one is not a claim anybody has checked.

    ``domain`` is a bare domain, normally ``identity_gate.stored_identity_domain(cfg)``. The
    plaintext address never reaches this module, and nothing here logs the domain.
    """
    summaries = catalog(config_dir=config_dir)
    if not summaries:
        return FilteredCatalog(summaries=())

    try:
        visible = _matched_subset(summaries, domain, saved_sis=saved_sis, picked_sis=picked_sis)
    except Exception:  # noqa: BLE001 - the fail-OPEN floor: a raise costs a short list, never a district
        logger.warning("Could not scope the district list; showing every district.", exc_info=True)
        return FilteredCatalog(summaries=summaries)

    return FilteredCatalog(summaries=visible)


def _matched_subset(
    summaries: Sequence[ConfigSummary],
    domain: str,
    *,
    saved_sis: str,
    picked_sis: str,
) -> tuple[ConfigSummary, ...]:
    """Tier (ii)'s row set: the matching configs plus the saved and picked ones, in CATALOG order.

    Order is preserved rather than re-derived, so every surface lists a district in the same
    relative position, and a scoped list reads as a shortened catalog rather than a re-sorted
    one. Only configs whose domains RESOLVED (a non-empty ``district_domains``) are offered to the
    matcher — an unreadable one (``None``) and a declared-empty one (``()``) both claim
    nobody, and the ``if s.district_domains`` guard covers both because each is falsy.
    """
    claimed = {s.sis_type: s.district_domains for s in summaries if s.district_domains}
    matched = set(resolve_domain(domain, claimed))
    if not matched:
        return tuple(summaries)  # tier (i) — no match, everything shows
    keep = {_normalise_sis(saved_sis), _normalise_sis(picked_sis)} - {""}
    return tuple(s for s in summaries if s.sis_type in matched or _normalise_sis(s.sis_type) in keep)


def disambiguated_labels(summaries: Sequence[ConfigSummary]) -> dict[str, str]:
    """``{config id: the label a picker row renders}`` — no two of which may read identically.

    A picker row is a decision, and two rows with the same words are a coin flip the admin
    cannot see losing (the highest-consequence wrong click in this product is picking the
    wrong district, because a wrong mapping ships a wrong roster). Where display text
    collides, EVERY member of the colliding group gets its raw config id appended — suffixing
    only the later ones would leave the first still reading ambiguously.

    Collision is judged after case-folding and whitespace-trimming, because two labels
    differing only in padding or case read identically on screen.

    Scope, stated: the BUNDLED set is guaranteed collision-free by distinct ``district_name``
    lines (G13, pinned at ``test_ui_flet_mapping_catalog``). This runtime pass exists for the
    YAMLs we do NOT control — a partner-authored mapping dropped into
    ``~/.districtsync/mappings/``. If it ever starts firing on the bundled catalog, G13 has
    regressed and that is the bug to fix, not this.
    """
    seen: dict[str, list[str]] = {}
    for summary in summaries:
        display = (summary.district_name or "").strip() or summary.sis_type
        seen.setdefault(display.strip().lower(), []).append(summary.sis_type)

    labels: dict[str, str] = {}
    for summary in summaries:
        display = (summary.district_name or "").strip() or summary.sis_type
        collides = len(seen[display.strip().lower()]) > 1
        labels[summary.sis_type] = f"{display} ({summary.sis_type})" if collides else display
    return labels


def can_apply(pending: ConfigSummary | None, persisted_sis: str) -> bool:
    """The Mapping Apply-gate — is switching to ``pending`` both SAFE and meaningful?

    Structural gate (mirrors Setup's SFTP-host allowlist pattern): ``True`` only when the
    pending config LOADED cleanly (never apply a broken config — the next run would fail) AND
    differs from the ``persisted_sis`` current value (never a no-op). Compared against the
    PERSISTED current (a fresh ``AppConfig.load`` read, never a captured mount instance), so
    after an Apply the previous mapping can be re-selected and reverted — the whole point of the
    pure extraction. ``pending`` is ``None`` when nothing is selected → not applyable.
    """
    return pending is not None and pending.loaded_ok and pending.sis_type != persisted_sis


# --------------------------------------------------------------------------- #
# Post-Apply schedule honesty — does the switch leave a stale nightly task?      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StaleScheduleNotice:
    """The post-Apply warning that the registered nightly task still carries the OLD district.

    A registered task bakes ``--sis <district>`` into its action args, so a Mapping switch
    leaves a LIVE task converting the old district until Settings re-registers it. The copy
    names only district DISPLAY names (Mapping already shows them) — never a path, a task
    name, or a raw error.
    """

    headline: str
    detail: str


@dataclass(frozen=True)
class PostApplyPresentation:
    """Everything the post-Apply confirmation paints — honest in every schedule branch.

    ``healthy_detail`` supports the "Now using <new district>" HEALTHY band; ``notice`` is the
    stale-schedule warning (``None`` when no schedule could be running the old district).
    """

    healthy_detail: str
    notice: StaleScheduleNotice | None


def post_apply_presentation(
    old_district_name: str,
    *,
    schedule_state: ScheduleState | None,
    hint_registered: bool,
) -> PostApplyPresentation:
    """Decide the post-Apply banner copy from the schedule truth (pure, TOTAL).

    Branches (the D4 honesty invariant — UNKNOWN never asserts):

    - ``LIVE`` → an assertive notice naming the old district (the read-back definitively
      proves the task EXISTS; the named district is an inference from the pre-Apply
      persisted config — after 2+ un-reconciled switches the task may carry an even older
      district, but the guidance stays correct; the config hint is irrelevant).
    - ``UNKNOWN`` / ``None`` (probe pending, failed, or non-Windows) while the config hint
      says a schedule is registered → the SAME notice with hedged copy ("may still use") —
      a live schedule is never asserted from the hint alone.
    - ``MISSING``, or unconfirmed without the hint → no notice: there is no schedule to speak
      of (an expected-but-missing schedule is Home/Setup's attention, not a stale-district risk).

    ``healthy_detail`` claims only what is true in EVERY branch — the folders are untouched —
    and never reassures about the schedule (the old "schedule ... unchanged" line was literally
    true and exactly the hazard). A blank ``old_district_name`` (an unset pre-Apply district)
    falls back to "the previous district" (total).
    """
    old_name = (old_district_name or "").strip() or "the previous district"
    healthy_detail = "Your folders are unchanged."
    if schedule_state is ScheduleState.LIVE:
        return PostApplyPresentation(
            healthy_detail=healthy_detail,
            notice=StaleScheduleNotice(
                headline=f"Your nightly schedule still uses {old_name}",
                detail="Open Settings and Save to update it to the new district.",
            ),
        )
    if schedule_state is not ScheduleState.MISSING and hint_registered:
        return PostApplyPresentation(
            healthy_detail=healthy_detail,
            notice=StaleScheduleNotice(
                headline=f"Your nightly schedule may still use {old_name}",
                detail=(
                    "We couldn't confirm the nightly schedule right now — "
                    "open Settings and Save to make sure it uses the new district."
                ),
            ),
        )
    return PostApplyPresentation(healthy_detail=healthy_detail, notice=None)
