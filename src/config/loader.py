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
"""

import copy
import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from src.config.models import MappingConfig
from src.utils.paths import bundle_mappings_dir, user_mappings_dir

logger = logging.getLogger(__name__)

# Supported mapping-config format version (derived from the bundled configs,
# which declare 1.0–1.9 today). Bump MINOR when the bundled configs start
# using new same-major features; bump MAJOR only on a breaking config-format
# change (and migrate every bundled config in the same release, so the
# bundled set always loads clean against these constants).
SUPPORTED_CONFIG_MAJOR = 1
SUPPORTED_CONFIG_MINOR = 9


def _search_dirs(explicit: Optional[Path]) -> list[Path]:
    """Return the ordered list of directories to search for mapping YAMLs.

    When ``explicit`` is given (tests / internal overrides), use only
    that. Otherwise search user overrides first, then the bundled
    defaults.
    """
    if explicit is not None:
        return [explicit]
    return [user_mappings_dir(), bundle_mappings_dir()]


def _find_mapping_file(sis_type: str, search_dirs: list[Path]) -> Optional[Path]:
    """Return the first existing ``<dir>/<sis_type>_mapping.yaml`` in search order.

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
            return candidate
    return None


def _parse_version(version: object, path: Path) -> tuple[int, int]:
    """Parse a config ``version`` value into ``(major, minor)`` integers.

    Accepts the forms the bundled configs use — quoted strings (``'1.0'``),
    bare YAML floats (``1.9``), a bare major (``1``), and an optional patch
    component (``1.9.2``, ignored). Anything else fails loudly: a config
    whose version cannot even be read must not drive a conversion.
    """
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.\d+)?", str(version).strip())
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

    base_path = _find_mapping_file(base_name, search_dirs)
    if base_path is None:
        tried = ", ".join(str(d) for d in search_dirs)
        raise FileNotFoundError(f"Base config '{base_name}_mapping.yaml' not found in any of: {tried}")

    base_raw = _load_yaml(base_path)
    # Recursively resolve if base also inherits (pass same visited set)
    base_raw = _resolve_inheritance(base_raw, search_dirs, visited)
    return _deep_merge(base_raw, raw)


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
            ``None`` (the default), search
            ``~/.districtsync/mappings/`` first, then the bundled
            ``config/mappings/``.

    Returns:
        Validated MappingConfig.

    Raises:
        FileNotFoundError: If the mapping file doesn't exist in any search path.
        ValueError: If validation fails (wraps Pydantic errors with clear
            messages), or if the resolved config's version is outside the
            supported major range (see ``_check_config_version``).
    """
    search_dirs = _search_dirs(config_dir)
    path = _find_mapping_file(sis_type, search_dirs)
    if path is None:
        tried = ", ".join(str(d) for d in search_dirs)
        raise FileNotFoundError(f"Mapping file '{sis_type}_mapping.yaml' not found in any of: {tried}")

    raw = _load_yaml(path)
    raw = _resolve_inheritance(raw, search_dirs)

    # Version-gate the RESOLVED config (a version may be inherited via _base)
    # BEFORE Pydantic validation, so an out-of-range config gets the actionable
    # version message rather than confusing field-level schema errors. A missing
    # version falls through to Pydantic's required-field error.
    if "version" in raw:
        _check_config_version(raw["version"], path)

    try:
        return MappingConfig(**raw)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            errors.append(f"  {loc}: {err['msg']}")
        msg = f"Invalid mapping config '{sis_type}':\n" + "\n".join(errors)
        raise ValueError(msg) from e
