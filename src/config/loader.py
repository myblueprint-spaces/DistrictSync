"""Config loading with validation and optional inheritance.

District configs can use `_base: myedbc` to inherit from the standard
mapping and only override what differs. This eliminates the full
duplication currently seen in sd48/sd51/sd74 configs.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from src.config.models import MappingConfig

logger = logging.getLogger(__name__)

CONFIG_DIR = Path("config/mappings")


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
    config_dir: Path,
    visited: Optional[set[str]] = None,
) -> dict[str, Any]:
    """If the config has a `_base` key, load and deep-merge the parent.

    Args:
        raw: The raw YAML dict (will have '_base' popped if present).
        config_dir: Directory containing mapping YAML files.
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

    base_path = config_dir / f"{base_name}_mapping.yaml"
    if not base_path.exists():
        raise FileNotFoundError(
            f"Base config '{base_name}_mapping.yaml' not found at {base_path}"
        )

    base_raw = _load_yaml(base_path)
    # Recursively resolve if base also inherits (pass same visited set)
    base_raw = _resolve_inheritance(base_raw, config_dir, visited)
    return _deep_merge(base_raw, raw)


def load_config(
    sis_type: str,
    config_dir: Optional[Path] = None,
) -> MappingConfig:
    """Load and validate a mapping config by SIS type name.

    Args:
        sis_type: SIS identifier (e.g. "myedbc", "sd48myedbc").
        config_dir: Override the config directory (for testing).

    Returns:
        Validated MappingConfig.

    Raises:
        FileNotFoundError: If the mapping file doesn't exist.
        ValueError: If validation fails (wraps Pydantic errors with clear messages).
    """
    cdir = config_dir or CONFIG_DIR
    path = cdir / f"{sis_type}_mapping.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")

    raw = _load_yaml(path)
    raw = _resolve_inheritance(raw, cdir)

    try:
        return MappingConfig(**raw)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            errors.append(f"  {loc}: {err['msg']}")
        msg = f"Invalid mapping config '{sis_type}':\n" + "\n".join(errors)
        raise ValueError(msg) from e
