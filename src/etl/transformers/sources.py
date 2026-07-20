"""Source-file config normalization + role-based frame access.

Entity ``source_files`` blocks arrive in three YAML shapes (dict,
list-of-dicts, legacy list-of-strings); :func:`normalize_source_config`
canonicalizes them to ``{role: filename}`` once. :func:`get_source_file`
resolves a role to its loaded frame from ``TransformContext.raw_data``
(always a copy, so callers can mutate freely). Shared by every transformer
(via the ``BaseTransformer`` wrappers) and by the plain
``BlendedClassDetector`` service.
"""

import logging
from typing import Any

import pandas as pd

from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


def normalize_source_config(source_config: Any) -> dict[str, str]:
    """Convert various config formats (dict, list-of-dicts, list-of-strings) to {role: filename}."""
    if isinstance(source_config, dict):
        return source_config

    normalized: dict[str, str] = {}
    if isinstance(source_config, list):
        if all(isinstance(item, dict) for item in source_config):
            for item in source_config:
                if "role" in item and "file" in item:
                    normalized[item["role"]] = item["file"]
        elif all(isinstance(item, str) for item in source_config):
            roles = ["student_schedule", "course_info", "staff_info", "student_demographic"]
            for i, filename in enumerate(source_config):
                if i < len(roles):
                    normalized[roles[i]] = filename
    return normalized


def get_source_file(context: TransformContext, source_config: Any, role: str) -> pd.DataFrame:
    """Return a COPY of the frame configured for ``role`` (empty frame + warning when unresolved)."""
    normalized = normalize_source_config(source_config)
    filename = normalized.get(role)
    if filename and filename in context.raw_data:
        return context.raw_data[filename].copy()
    logger.warning(f"Source file for role '{role}' not found in configuration")
    return pd.DataFrame()
