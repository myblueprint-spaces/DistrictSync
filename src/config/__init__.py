"""Configuration validation and loading."""

from src.config.loader import load_config
from src.config.models import (
    EntityConfig,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    GlobalConfig,
    MappingConfig,
)

__all__ = [
    "MappingConfig",
    "GlobalConfig",
    "EntityConfig",
    "FieldTransform",
    "FieldFixedValue",
    "FieldAcademicYear",
    "FieldAppendYear",
    "FieldEmailFormat",
    "FieldNameConfig",
    "FieldIdRolePair",
    "load_config",
]
