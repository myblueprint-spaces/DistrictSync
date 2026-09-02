"""Config loading with validation and optional inheritance.

District configs can use `_base: myedbc` to inherit from the standard
mapping and only override what differs. This eliminates the full
duplication currently seen in sd48/sd51/sd74 configs.

Mapping YAMLs are discovered from two directories, in order:

1. ``~/.districtsync/mappings/`` — user-writable. Custom district configs
   (provided by the DistrictSync team) live here. A config here with the
   same SIS identifier as a built-in overrides the built-in.
2. Bundled ``config/mappings/`` — ships with the binary. Resolved
   relative to the PyInstaller bundle root so absolute paths work in
   both source-install and frozen-exe runs.

This lets partners customize a shipped config (e.g. override `sd40myedbc`)
without waiting for a new release, while built-ins remain available as
fallbacks and as `_base:` parents.

Two guardrails keep that override path honest:

- Every user-dir file that shadows a bundled one is named in an INFO log
  line, so a stale hotfix config can never *silently* drive a conversion.
- The resolved config's ``version`` is gated against the supported range
  (see ``SUPPORTED_CONFIG_MAJOR``): a different major fails loudly, a
  newer minor warns.

Which tier a config came from is therefore load-bearing, not bookkeeping, so it
is exposed once: :func:`resolve_config_path` returns ``(path, origin)`` and
:func:`load_config` acts on that same value. A third guardrail is DIRECTIONAL
rather than symmetric — the presentation-only ``district_domains`` list is
pre-screened for USER-dir configs (invalid rows dropped, one counts-only WARN,
never an echoed value; see :func:`_apply_user_dir_domains_floor`) while a BUNDLED
config keeps the model validator's loud raise, because a typo in a hand-edited
file must not kill a district's nightly sync but a shipped one is CI's to catch.

:func:`validate_overlay` runs the same resolve → gate → floor → validate pipeline
over an in-memory dict, for the authoring layer's load-back-before-write check.
"""

import copy
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional

import yaml
from pydantic import ValidationError

from src.config.models import MappingConfig, is_valid_district_domain
from src.utils.paths import bundle_mappings_dir, user_mappings_dir

logger = logging.getLogger(__name__)

# Supported mapping-config format version (derived from the bundled configs,
# which declare 1.0–1.11 today). Bump MINOR when the bundled configs start
# using new same-major ETL-AFFECTING features; bump MAJOR only on a breaking
# config-format change (and migrate every bundled config in the same release,
# so the bundled set always loads clean against these constants).
#
# SCOPE (amended 2026-07-29, plan 0038 S3 — see docs/claugentic-DECISIONS.md):
# "ETL-affecting" is the operative word. This gate exists so an OLDER build
# refuses (or warns about) a config whose behaviour it would silently get
# wrong — which can only happen for a key that changes what the pipeline
# READS, TRANSFORMS or EMITS. A purely PRESENTATION key (one that
# `MappingConfig.to_raw_dict` structurally cannot pass to a transformer, e.g.
# `district_name`, `district_domains`) is invisible to every build: an older
# one ignores it via `extra="ignore"` and produces byte-identical output. So
# adding one does NOT bump the minor. Bumping anyway would be actively worse
# than noise — half the bundled configs pin their own `version` and resolve to
# 1.0, so the bump would fire a newer-minor WARNING on configs that are
# perfectly compatible, training operators to ignore the one warning that
# matters.
#
# The prose above tracks what the BUNDLED CONFIGS DECLARE; the constant below
# tracks what THIS BUILD UNDERSTANDS. They MAY legitimately diverge (a
# forward-looking minor bump lands before its first consumer config): 1.11
# added `student_rostering_grades` (plan 0042 slice 1b) ahead of any consumer,
# and the ranges converged on 2026-08-31 when the first licensing districts
# (sd27/sd38, the phase-2 8-12 scopes) declared quoted `version: '1.11'` and
# moved this prose with them, exactly as the convention prescribes.
# (Pinned by tests/test_config_version_gate.py::TestDeclaredRangeVersusSupported.)
SUPPORTED_CONFIG_MAJOR = 1
SUPPORTED_CONFIG_MINOR = 11


def _search_dirs(explicit: Optional[Path]) -> list[Path]:
    """Return the ordered list of directories to search for mapping YAMLs.

    When ``explicit`` is given (tests / internal overrides), use only
    that. Otherwise search user overrides first, then the bundled
    defaults.
    """
    if explicit is not None:
        return [explicit]
    return [user_mappings_dir(), bundle_mappings_dir()]


def _find_mapping_file(sis_type: str, search_dirs: list[Path]) -> Optional[tuple[Path, int]]:
    """Return the first existing ``<dir>/<sis_type>_mapping.yaml`` + its dir INDEX.

    The index is returned (rather than just the path) because it is the ONE
    fact that decides a config's ORIGIN — see :func:`resolve_config_path`.
    Deriving origin from the path's parent instead would be a second spelling
    of the same rule, and a wrong one the moment two search dirs coincide.

    When the winning file shadows a same-named file in a later search dir
    (i.e. a user-dir override hides a bundled config), an INFO line names
    both paths — visibility even when the versions match, so a stale
    override can never take effect silently.
    """
    filename = f"{sis_type}_mapping.yaml"
    for index, directory in enumerate(search_dirs):
        candidate = directory / filename
        if candidate.exists():
            for later_dir in search_dirs[index + 1 :]:
                shadowed = later_dir / filename
                if shadowed.exists():
                    logger.info("Mapping config '%s' loaded from '%s' — shadows '%s'", filename, candidate, shadowed)
            return candidate, index
    return None


#: Which of the two search dirs a resolved mapping config came out of.
#: ``"user"`` = the hand-editable app-data ``mappings/`` dir (index 0);
#: ``"bundled"`` = the read-only shipped ``config/mappings/`` (index 1).
#: This is a SAFETY-RELEVANT distinction, not bookkeeping: the user-dir
#: ``district_domains`` floor (see :func:`_apply_user_dir_domains_floor`) applies
#: to ``"user"`` only, so a mis-typed origin would either kill a district's
#: nightly sync or defeat the CI gate on the shipped configs.
ConfigOrigin = Literal["user", "bundled"]

#: The index-to-origin rule, spelled ONCE.
_ORIGIN_BY_INDEX: tuple[ConfigOrigin, ConfigOrigin] = ("user", "bundled")


class ResolvedConfigPath(NamedTuple):
    """A located mapping config: where it is, and which tier it came from."""

    path: Path
    origin: ConfigOrigin


def _require_search_pair(search_dirs: Optional[Sequence[Path]]) -> list[Path]:
    """Normalise a search-dir override into the contractual USER-then-BUNDLED pair.

    ``None`` → the real pair ``[user_mappings_dir(), bundle_mappings_dir()]``.

    Anything else MUST be a two-element sequence whose FIRST element is, by
    contract, the user-tier dir. The length is enforced fail-loud rather than
    defaulted because a one-element override cannot EXPRESS an origin: every
    lookup through it would report the same tier, which would make each origin
    test vacuously green and (worse) silently decide whether the user-dir
    domains floor applies (plan 0044 review #9).
    """
    if search_dirs is None:
        return [user_mappings_dir(), bundle_mappings_dir()]
    dirs = list(search_dirs)
    if len(dirs) != len(_ORIGIN_BY_INDEX):
        raise ValueError(
            f"search_dirs must be exactly {len(_ORIGIN_BY_INDEX)} directories "
            f"(user dir first, bundled dir second) — got {len(dirs)}. A single-dir "
            f"search cannot express a config's origin, and origin decides whether the "
            f"user-dir district_domains floor applies."
        )
    return dirs


def resolve_config_path(
    sis_type: str,
    *,
    search_dirs: Optional[Sequence[Path]] = None,
) -> Optional[ResolvedConfigPath]:
    """Locate a mapping config and report WHICH tier won.

    Args:
        sis_type: SIS identifier (e.g. ``"myedbc"``, ``"sd93custom"``).
        search_dirs: Test seam — a two-element sequence, USER dir first,
            BUNDLED dir second (see :func:`_require_search_pair`; a wrong
            length raises ``ValueError``). ``None`` (the default) uses the
            real pair.

    Returns:
        ``ResolvedConfigPath(path, origin)``, or ``None`` when no search dir
        holds ``<sis_type>_mapping.yaml``. Deliberately does NOT raise on a
        miss — :func:`load_config` owns the actionable ``FileNotFoundError``
        so its message stays the single spelling operators see.
    """
    dirs = _require_search_pair(search_dirs)
    found = _find_mapping_file(sis_type, dirs)
    if found is None:
        return None
    path, index = found
    return ResolvedConfigPath(path, _ORIGIN_BY_INDEX[index])


def _parse_version(version: object, path: Path) -> tuple[int, int]:
    """Parse a config ``version`` value into ``(major, minor)`` integers.

    The version MUST be a quoted YAML string (``'1.9'``; an optional patch
    component ``'1.9.2'`` is ignored). A bare YAML float is REJECTED fail-loud:
    PyYAML collapses ``version: 1.10`` to the float ``1.1``, silently reading
    minor 10 as minor 1 and skipping the newer-minor warning — the information
    is unrecoverable once parsed, so the string form is required at the source.
    """
    if not isinstance(version, str):
        raise ValueError(
            f"Mapping config '{path}' declares its version as a bare YAML scalar "
            f"({version!r}) — quote it as a string (e.g. version: "
            f"'{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}'). Bare floats lose "
            f"trailing zeros (1.10 reads as 1.1), so the version gate cannot trust them."
        )
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.\d+)?", version.strip())
    if match is None:
        raise ValueError(
            f"Mapping config '{path}' declares an unreadable version {version!r} — "
            f"expected '<major>.<minor>' (e.g. '{SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}'). "
            f"Fix the 'version' field, or obtain a current config from the DistrictSync team."
        )
    return int(match.group(1)), int(match.group(2) or 0)


def _check_config_version(version: object, path: Path) -> None:
    """Gate the resolved config's version against the supported range.

    - Same major, minor <= supported: silent (in range).
    - Same major, newer minor: loud WARNING — the config may use features
      this build ignores, but same-major semantics are still safe to run.
    - Different major (older OR newer): fail-loud ValueError — an
      out-of-major-range config cannot silently drive a conversion.
    """
    major, minor = _parse_version(version, path)
    if major != SUPPORTED_CONFIG_MAJOR:
        raise ValueError(
            f"Mapping config '{path}' declares version {version} (major {major}), but this "
            f"DistrictSync build supports major version {SUPPORTED_CONFIG_MAJOR} "
            f"(up to {SUPPORTED_CONFIG_MAJOR}.{SUPPORTED_CONFIG_MINOR}). A config from a different "
            f"major version cannot drive a conversion. Obtain a major-{SUPPORTED_CONFIG_MAJOR} config "
            f"from the DistrictSync team, or install the DistrictSync release that matches this config."
        )
    if minor > SUPPORTED_CONFIG_MINOR:
        logger.warning(
            "Mapping config '%s' declares version %s, newer than the supported %s.%s — "
            "newer config features may be ignored; consider upgrading DistrictSync.",
            path,
            version,
            SUPPORTED_CONFIG_MAJOR,
            SUPPORTED_CONFIG_MINOR,
        )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``. Override values win.

    Only dicts merge key-by-key (recursively). Every other value type —
    **including lists — REPLACES the base value wholesale**; there is no
    list concatenation or element-wise merge. E.g. a district config that
    sets ``global_config.enabled_entities: [Students]`` over a base
    declaring all seven entities ends up with exactly ``[Students]``, not
    a union — an override must restate the FULL list it wants.
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _resolve_inheritance(
    raw: dict[str, Any],
    search_dirs: list[Path],
    visited: Optional[set[str]] = None,
) -> dict[str, Any]:
    """If the config has a `_base` key, load and deep-merge the parent.

    Args:
        raw: The raw YAML dict (will have '_base' popped if present).
        search_dirs: Ordered list of directories to search for the base config.
        visited: Set of base names already seen — prevents infinite loops.

    Raises:
        ValueError: If a circular inheritance chain is detected.
        FileNotFoundError: If the referenced base config file doesn't exist.
    """
    if visited is None:
        visited = set()

    base_name = raw.pop("_base", None)
    if base_name is None:
        return raw

    if base_name in visited:
        chain = " -> ".join(sorted(visited)) + f" -> {base_name}"
        raise ValueError(f"Config inheritance cycle detected: {chain}")

    visited.add(base_name)

    found = _find_mapping_file(base_name, search_dirs)
    if found is None:
        tried = ", ".join(str(d) for d in search_dirs)
        raise FileNotFoundError(f"Base config '{base_name}_mapping.yaml' not found in any of: {tried}")

    base_path, _base_origin_index = found
    base_raw = _load_yaml(base_path)
    # Recursively resolve if base also inherits (pass same visited set)
    base_raw = _resolve_inheritance(base_raw, search_dirs, visited)
    return _deep_merge(base_raw, raw)


#: Label used in place of a real path when validating an overlay that has no file yet
#: (see :func:`validate_overlay`). A path-shaped placeholder keeps the version-gate and
#: floor messages one shape, and reads honestly in a log.
_UNSAVED_OVERLAY_LABEL = "<unsaved overlay>"

#: ONE counts-only warning for the user-dir ``district_domains`` floor. It NEVER
#: interpolates an offending value: this key holds a district's PUBLIC staff email
#: domains, and the likeliest bad row is a pasted PERSONAL email address, which must
#: never reach an ops log (the same PII rule the model validator's raise obeys).
_DOMAINS_FLOOR_WARNING = (
    "Mapping config '%s' at '%s' is user-authored: dropped %d of %d 'district_domains' "
    "%s. The offending value is deliberately NOT logged (this key holds PUBLIC district "
    "email domains, and a mis-pasted personal address must never reach a log). "
    "Consequence: this district will show in every district picker regardless of who is "
    "signed in, and its admins will not be matched to it, until the row is fixed. The "
    "conversion itself is unaffected — a domain is presentation only."
)


def _apply_user_dir_domains_floor(
    raw: dict[str, Any],
    *,
    sis_type: str,
    path: Path,
) -> dict[str, Any]:
    """Drop invalid ``district_domains`` entries from a USER-dir config, with ONE WARN.

    DIRECTION IS DELIBERATE AND MUST NOT INVERT:

    - **Bundled** config → UNTOUCHED, so ``MappingConfig``'s own validator still
      RAISES. A bundled bad row is caught by ``make validate-config`` in CI before
      the release ever ships, where a loud failure costs nothing.
    - **User** config (hand-editable, authored on a district server, never seen by
      CI) → WARN and drop. ``district_domains`` is a PRESENTATION key the ETL
      structurally cannot read (``MappingConfig.to_raw_dict`` emits only ``mappings``
      + ``global_config``), so a typo in it must never kill that district's nightly
      sync. Failing open costs a picker-scoping nicety; failing closed costs the roster.

    Two shapes are handled, both counts-only: a list with some invalid entries (those
    entries are dropped, the good ones kept), and a non-list value (e.g. a bare string
    — the whole key is dropped, so the model's ``list`` default applies).

    Note on ``_base``: this screens the MERGED raw, so a user overlay inheriting from a
    base that itself carries a bad row sees it here too. That is correct — the merged
    dict is what would be validated, and the user-dir file is the one an admin can fix.

    Returns a new dict when something was dropped; the input dict otherwise (never
    mutates the caller's dict either way).
    """
    if "district_domains" not in raw:
        return raw

    value = raw["district_domains"]
    if not isinstance(value, list):
        logger.warning(
            _DOMAINS_FLOOR_WARNING,
            sis_type,
            path,
            1,
            1,
            "— the key was not a list of domains at all, so the whole key was dropped",
        )
        updated = dict(raw)
        del updated["district_domains"]
        return updated

    kept = [entry for entry in value if is_valid_district_domain(entry)]
    if len(kept) == len(value):
        return raw

    logger.warning(
        _DOMAINS_FLOOR_WARNING,
        sis_type,
        path,
        len(value) - len(kept),
        len(value),
        "entries that are not a bare lowercase domain name (e.g. 'sd48.bc.ca')",
    )
    updated = dict(raw)
    updated["district_domains"] = kept
    return updated


def _resolve_gate_and_validate(
    raw: dict[str, Any],
    *,
    sis_type: str,
    path: Path,
    origin: ConfigOrigin,
    search_dirs: list[Path],
) -> MappingConfig:
    """Resolve ``_base`` → version-gate → user-dir domains floor → Pydantic validate.

    The ONE spelling of that four-step pipeline, shared by :func:`load_config` (a file
    on disk) and :func:`validate_overlay` (a dict that has no file yet), so the two can
    never drift on an error message, a gate or the floor.

    Mutates ``raw`` (``_resolve_inheritance`` pops ``_base``) — callers own the copy.
    """
    resolved = _resolve_inheritance(raw, search_dirs)

    # Version-gate the RESOLVED config (a version may be inherited via _base)
    # BEFORE Pydantic validation, so an out-of-range config gets the actionable
    # version message rather than confusing field-level schema errors. A missing
    # version falls through to Pydantic's required-field error.
    if "version" in resolved:
        _check_config_version(resolved["version"], path)

    if origin == "user":
        resolved = _apply_user_dir_domains_floor(resolved, sis_type=sis_type, path=path)

    try:
        return MappingConfig(**resolved)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            errors.append(f"  {loc}: {err['msg']}")
        msg = f"Invalid mapping config '{sis_type}':\n" + "\n".join(errors)
        raise ValueError(msg) from e


def available_configs(config_dir: Optional[Path] = None) -> list[str]:
    """Return sorted unique SIS identifiers discoverable across all search dirs.

    Used by the UI surfaces (Setup, Convert, Mapping) to populate
    district-picker dropdowns. User-dir and bundle entries are
    deduplicated by identifier (user wins by virtue of being listed
    first in the search order).
    """
    seen: set[str] = set()
    results: list[str] = []
    for directory in _search_dirs(config_dir):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*_mapping.yaml")):
            ident = path.stem.removesuffix("_mapping")
            if ident not in seen:
                seen.add(ident)
                results.append(ident)
    return sorted(results)


def load_config(
    sis_type: str,
    config_dir: Optional[Path] = None,
) -> MappingConfig:
    """Load and validate a mapping config by SIS type name.

    Args:
        sis_type: SIS identifier (e.g. "myedbc", "sd48myedbc").
        config_dir: Override the config directory (for testing). When
            ``None`` (the default), search the user app-data
            ``mappings/`` dir first, then the bundled ``config/mappings/``
            — and the winning tier decides whether the user-dir
            ``district_domains`` floor applies (see
            :func:`_apply_user_dir_domains_floor`). A single explicit
            ``config_dir`` cannot express a tier, so it is treated as
            ``"bundled"``-equivalent: NO floor, and an invalid domain row
            keeps its loud raise.

    Returns:
        Validated MappingConfig.

    Raises:
        FileNotFoundError: If the mapping file doesn't exist in any search path.
        ValueError: If validation fails (wraps Pydantic errors with clear
            messages), or if the resolved config's version is outside the
            supported major range (see ``_check_config_version``).
    """
    origin: ConfigOrigin
    if config_dir is None:
        # Resolve through the PUBLIC seam so the origin this function acts on is
        # byte-for-byte the one `resolve_config_path` reports (single source).
        search_dirs = _search_dirs(None)
        resolved = resolve_config_path(sis_type, search_dirs=search_dirs)
        if resolved is None:
            tried = ", ".join(str(d) for d in search_dirs)
            raise FileNotFoundError(f"Mapping file '{sis_type}_mapping.yaml' not found in any of: {tried}")
        path, origin = resolved
    else:
        # LEGACY single-dir override (tests / internal callers). One dir cannot
        # express an origin, so it is defined explicitly as "bundled"-equivalent:
        # NO user-dir domains floor applies, and an invalid `district_domains` row
        # keeps the model validator's loud raise. That is the safe assignment —
        # the floor exists for HAND-EDITABLE user files on a district server, and
        # this seam is a test/CI path whose whole job is to fail loudly.
        search_dirs = _search_dirs(config_dir)
        found = _find_mapping_file(sis_type, search_dirs)
        if found is None:
            tried = ", ".join(str(d) for d in search_dirs)
            raise FileNotFoundError(f"Mapping file '{sis_type}_mapping.yaml' not found in any of: {tried}")
        path = found[0]
        origin = "bundled"

    raw = _load_yaml(path)
    return _resolve_gate_and_validate(
        raw,
        sis_type=sis_type,
        path=path,
        origin=origin,
        search_dirs=search_dirs,
    )


def validate_overlay(
    raw: dict[str, Any],
    *,
    search_dirs: Optional[Sequence[Path]] = None,
) -> MappingConfig:
    """Validate an IN-MEMORY overlay dict exactly as :func:`load_config` would.

    This is the authoring layer's load-back check (plan 0044 S1): the overlay is
    validated BEFORE any file exists, which is the whole point — a build that
    cannot load must never reach the user's ``mappings/`` dir.

    ``_base`` is resolved against the real search dirs (or the injected
    USER-then-BUNDLED pair), the resolved raw is version-gated, the user-dir
    ``district_domains`` floor applies (an overlay is by definition destined for
    the user dir, so its origin is ``"user"``), and Pydantic errors are wrapped
    with the SAME message shape ``load_config`` produces.

    Reads NO file for the overlay itself and does not mutate ``raw`` (a deepcopy
    is validated, because ``_resolve_inheritance`` pops ``_base``).

    Raises:
        FileNotFoundError: unknown ``_base``.
        ValueError: version outside the supported major range, or schema invalid.
    """
    dirs = _require_search_pair(search_dirs)
    overlay = copy.deepcopy(raw)
    sis = overlay.get("sis")
    label = sis.strip() if isinstance(sis, str) and sis.strip() else _UNSAVED_OVERLAY_LABEL
    return _resolve_gate_and_validate(
        overlay,
        sis_type=label,
        path=Path(_UNSAVED_OVERLAY_LABEL),
        origin="user",
        search_dirs=dirs,
    )
