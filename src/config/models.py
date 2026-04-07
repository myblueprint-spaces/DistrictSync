"""Pydantic models for YAML mapping configuration.

Validates the full mapping file structure at load time so that typos,
missing fields, and schema violations surface as clear error messages
rather than cryptic KeyErrors deep in the pipeline.
"""

import logging
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Field mapping variants — the polymorphic heart of the config
# -----------------------------------------------------------------------

class FieldTransform(BaseModel):
    """Column mapping with an optional transform function (e.g., grade_to_ceds)."""
    column: str
    transform: str = ""


class FieldFixedValue(BaseModel):
    """Fixed literal value injected into every row."""
    value: str


class FieldAcademicYear(BaseModel):
    """Date resolved from the computed academic year bounds."""
    use_academic_year: bool = True
    value: Optional[str] = None

    @model_validator(mode="after")
    def check_consistency(self):
        if not self.use_academic_year and not self.value:
            raise ValueError(
                "When use_academic_year is false, a 'value' must be provided"
            )
        return self


class FieldAppendYear(BaseModel):
    """Column whose value gets the school year appended (e.g., MTID_2025)."""
    column: str
    append_year_to_id: bool = True


class FieldEmailFormat(BaseModel):
    """Template-based email generation using row fields."""
    format: str


class FieldNameConfig(BaseModel):
    """Class Name config — references multiple source columns."""
    primary_teacher_flag: str = Field(alias="primary teacher flag", default="")
    teacher_last_name: str = Field(alias="teacher last name", default="")
    course_title: str = Field(alias="course title", default="")
    section_letter: str = Field(alias="section letter", default="")

    model_config = {"populate_by_name": True}


class FieldIdRolePair(BaseModel):
    """Paired student/staff ID columns for User ID or Role resolution."""
    student_id_col: str
    staff_id_col: str


# Union of all field mapping types
FieldMapping = Union[
    str,                # Direct column name
    None,               # Auto-detected (e.g., EnrollStatus)
    FieldTransform,
    FieldFixedValue,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldNameConfig,
    FieldIdRolePair,
]


def classify_field(raw: Any) -> FieldMapping:
    """Classify a raw YAML field_map value into its typed variant.

    This is intentionally lenient — it validates structure without being
    so strict that existing configs break.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return str(raw)

    # Detect by distinguishing keys
    if "format" in raw:
        return FieldEmailFormat(**raw)
    if "append_year_to_id" in raw:
        return FieldAppendYear(**raw)
    if "use_academic_year" in raw:
        return FieldAcademicYear(**raw)
    if "value" in raw:
        return FieldFixedValue(**raw)
    if "transform" in raw:
        return FieldTransform(**raw)
    if "student_id_col" in raw and "staff_id_col" in raw:
        return FieldIdRolePair(**raw)
    if "primary teacher flag" in raw or "teacher last name" in raw:
        return FieldNameConfig(**raw)
    if "column" in raw:
        return FieldTransform(**raw)

    # Fallback: unrecognized dict structure — likely a typo in the YAML config
    logger.warning(f"Unrecognized field config structure: {raw}")
    return raw


# -----------------------------------------------------------------------
# Entity and top-level config
# -----------------------------------------------------------------------

class EntityConfig(BaseModel):
    """Config for a single output entity (Students, Staff, etc.)."""
    source_files: dict[str, str]
    field_map: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def coerce_source_files(cls, data: Any) -> Any:
        """Normalize legacy list-of-strings source_files to dict format."""
        if not isinstance(data, dict):
            return data
        sf = data.get("source_files")
        if isinstance(sf, list):
            roles = ["student_schedule", "course_info", "staff_info", "student_demographic"]
            if all(isinstance(item, dict) for item in sf):
                data["source_files"] = {
                    item["role"]: item["file"] for item in sf
                    if "role" in item and "file" in item
                }
            elif all(isinstance(item, str) for item in sf):
                data["source_files"] = {
                    roles[i]: filename
                    for i, filename in enumerate(sf)
                    if i < len(roles)
                }
        return data

    @model_validator(mode="after")
    def validate_fields(self):
        """Classify and validate each field_map entry."""
        validated = {}
        for key, raw in self.field_map.items():
            validated[key] = classify_field(raw)
        self.field_map = validated
        return self


class GlobalConfig(BaseModel):
    """Top-level global_config section."""
    school_year_sources: dict[str, str] = Field(default_factory=dict)
    homeroom_grades: list[str] = Field(default_factory=list)
    entity_order: list[str] = Field(default_factory=list)
    academic_start_month_day: str = "08-25"
    academic_end_month_day: str = "07-25"

    @model_validator(mode="before")
    @classmethod
    def handle_missing(cls, data: Any) -> Any:
        if data is None:
            return {}
        return data


class MappingConfig(BaseModel):
    """Root config model — validated representation of the YAML mapping file."""
    version: Union[str, float]
    sis: str
    global_config: GlobalConfig = Field(default_factory=GlobalConfig)
    mappings: dict[str, EntityConfig]

    @model_validator(mode="after")
    def check_required_entities(self):
        """Log which standard entities are present — non-standard entities are valid."""
        standard = {"Students", "Staff", "Family", "Classes", "Enrollments"}
        present = set(self.mappings.keys())
        extra = present - standard
        missing = standard - present
        if missing:
            self._missing_standard_entities = missing
        if extra:
            self._extra_entities = extra
        return self

    def get_entity(self, name: str) -> Optional[EntityConfig]:
        return self.mappings.get(name)

    def to_raw_dict(self) -> dict[str, Any]:
        """Return the full raw dict for backward compatibility with the pipeline.

        Produces the same structure that yaml.safe_load() would return, so
        callers no longer need to re-open the YAML file after validation.
        """
        mappings_raw: dict[str, Any] = {}
        for entity_name, entity_cfg in self.mappings.items():
            mappings_raw[entity_name] = {
                "source_files": dict(entity_cfg.source_files),
                "field_map": self.get_raw_field_map(entity_name),
            }

        global_raw: dict[str, Any] = {
            "school_year_sources": dict(self.global_config.school_year_sources),
            "homeroom_grades": list(self.global_config.homeroom_grades),
            "entity_order": list(self.global_config.entity_order),
            "academic_start_month_day": self.global_config.academic_start_month_day,
            "academic_end_month_day": self.global_config.academic_end_month_day,
        }

        return {"mappings": mappings_raw, "global_config": global_raw}

    def get_raw_field_map(self, entity: str) -> dict[str, Any]:
        """Return raw field_map dict for backward compatibility with transformers."""
        entity_cfg = self.mappings.get(entity)
        if entity_cfg is None:
            return {}
        # Convert typed field mappings back to the raw dict format transformers expect
        raw = {}
        for key, val in entity_cfg.field_map.items():
            if val is None or isinstance(val, (str, dict)):
                raw[key] = val
            elif isinstance(val, FieldTransform):
                raw[key] = {"column": val.column}
                if val.transform:
                    raw[key]["transform"] = val.transform
            elif isinstance(val, FieldFixedValue):
                raw[key] = {"value": val.value}
            elif isinstance(val, FieldAcademicYear):
                d = {"use_academic_year": val.use_academic_year}
                if val.value is not None:
                    d["value"] = val.value
                raw[key] = d
            elif isinstance(val, FieldAppendYear):
                raw[key] = {"column": val.column, "append_year_to_id": val.append_year_to_id}
            elif isinstance(val, FieldEmailFormat):
                raw[key] = {"format": val.format}
            elif isinstance(val, FieldNameConfig):
                raw[key] = {
                    "primary teacher flag": val.primary_teacher_flag,
                    "teacher last name": val.teacher_last_name,
                    "course title": val.course_title,
                    "section letter": val.section_letter,
                }
            elif isinstance(val, FieldIdRolePair):
                raw[key] = {"student_id_col": val.student_id_col, "staff_id_col": val.staff_id_col}
            else:
                raw[key] = val
        return raw
