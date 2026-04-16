"""Config loading with validation and optional inheritance.

District configs can use `_base: myedbc` to inherit from the standard
mapping and only override what differs. This eliminates the full
duplication currently seen in sd48/sd51/sd74 configs.

Mapping YAMLs are discovered from two directories, in order:

1. ``~/.gde2acsv/mappings/`` — user-writable. Partner-created configs
   (saved via the Mapping Editor) live here. A config here with the
   same SIS identifier as a built-in overrides the built-in.
2. Bundled ``config/mappings/`` — ships with the binary. Resolved
   relative to the PyInstaller bundle root so absolute paths work in
   both source-install and frozen-exe runs.

This lets partners customize a shipped config (e.g. override `sd40myedbc`)
without waiting for a new release, while built-ins remain available as
fallbacks and as `_base:` parents.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from src.config.models import MappingConfig
from src.utils.paths import bundle_mappings_dir, user_mappings_dir

logger = logging.getLogger(__name__)


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
    """Return the first existing ``<dir>/<sis_type>_mapping.yaml`` in search order."""
    filename = f"{sis_type}_mapping.yaml"
    for directory in search_dirs:
        candidate = directory / filename
        if candidate.exists():
            return candidate
    return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Override values win."""
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

    Used by UI pages (Setup Wizard, Convert, Mapping Editor) to populate
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
            ``~/.gde2acsv/mappings/`` first, then the bundled
            ``config/mappings/``.

    Returns:
        Validated MappingConfig.

    Raises:
        FileNotFoundError: If the mapping file doesn't exist in any search path.
        ValueError: If validation fails (wraps Pydantic errors with clear messages).
    """
    search_dirs = _search_dirs(config_dir)
    path = _find_mapping_file(sis_type, search_dirs)
    if path is None:
        tried = ", ".join(str(d) for d in search_dirs)
        raise FileNotFoundError(f"Mapping file '{sis_type}_mapping.yaml' not found in any of: {tried}")

    raw = _load_yaml(path)
    raw = _resolve_inheritance(raw, search_dirs)

    try:
        return MappingConfig(**raw)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            errors.append(f"  {loc}: {err['msg']}")
        msg = f"Invalid mapping config '{sis_type}':\n" + "\n".join(errors)
        raise ValueError(msg) from e
