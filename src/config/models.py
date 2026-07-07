"""Pydantic models for YAML mapping configuration.

Validates the full mapping file structure at load time so that typos,
missing fields, and schema violations surface as clear error messages
rather than cryptic KeyErrors deep in the pipeline.
"""

import logging
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
            raise ValueError("When use_academic_year is false, a 'value' must be provided")
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


class FieldEnrollStatus(BaseModel):
    """Active-student detection overrides for the Students ``EnrollStatus`` field.

    All keys are optional — a bare ``null`` keeps MyEd BC defaults (status
    column auto-resolved from an alias, withdraw-date hard override,
    ``active_values=["Active","PreReg"]``). ``extra="forbid"`` makes an
    unknown/typo'd key fail loudly at config load (the bug this closes: the
    previous raw-dict passthrough only warned).
    """

    model_config = ConfigDict(extra="forbid")

    status_column: Optional[str] = None
    withdraw_date_column: Optional[str] = None
    active_values: Optional[list[str]] = None


# Union of all field mapping types
FieldMapping = Union[
    str,  # Direct column name
    None,  # Auto-detected (e.g., EnrollStatus)
    FieldTransform,
    FieldFixedValue,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldNameConfig,
    FieldIdRolePair,
    FieldEnrollStatus,
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
    # EnrollStatus active-detection overrides. Keyed on its distinct fields
    # (collision-free vs the branches above) and BEFORE the warn-passthrough
    # fallback so an unknown/typo'd key raises (extra="forbid") instead of
    # silently passing through.
    if "active_values" in raw or "status_column" in raw or "withdraw_date_column" in raw:
        return FieldEnrollStatus(**raw)
    if "column" in raw:
        return FieldTransform(**raw)

    # Fallback: unrecognized dict structure — likely a typo in the YAML config
    logger.warning(f"Unrecognized field config structure: {raw}")
    return raw  # type: ignore[return-value]


# -----------------------------------------------------------------------
# Entity and top-level config
# -----------------------------------------------------------------------


class RowFilter(BaseModel):
    """Config-driven row inclusion for an entity.

    Keep only rows whose (trimmed, lower-cased) value in ``column`` is present
    in ``include`` (matching is case-insensitive on both the column name and the
    values). Successive filters AND-combine. Used, e.g., by SD60 Family to keep
    only true guardian rows. ``extra="forbid"`` fails loudly on a typo'd key.
    """

    model_config = ConfigDict(extra="forbid")

    column: str
    include: list[str] = Field(default_factory=list)


class EntityConfig(BaseModel):
    """Config for a single output entity (Students, Staff, etc.)."""

    source_files: dict[str, str]
    field_map: dict[str, Any]
    headers: dict[str, list[str]] = Field(default_factory=dict)
    # Optional config-driven row filters applied at transform entry (before
    # apply_field_map). Empty = keep every row (default, back-compatible).
    row_filters: list[RowFilter] = Field(default_factory=list)

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
                data["source_files"] = {item["role"]: item["file"] for item in sf if "role" in item and "file" in item}
            elif all(isinstance(item, str) for item in sf):
                data["source_files"] = {roles[i]: filename for i, filename in enumerate(sf) if i < len(roles)}
        return data

    @model_validator(mode="after")
    def validate_fields(self):
        """Classify and validate each field_map entry."""
        validated = {}
        for key, raw in self.field_map.items():
            validated[key] = classify_field(raw)
        self.field_map = validated
        return self


class CrossEnrollmentConfig(BaseModel):
    """Opt-in Students cross-enrollment collapse (one Students row per User ID).

    When ``collapse`` is true, :class:`StudentTransformer` deduplicates Students
    rows that share a ``User ID`` (a pupil Active at two schools, identical
    demographics bar School Number), keeping the row whose School equals the
    student's home school (``home_school_column``). Off by default — every other
    district is unaffected. Enrollments are built from the schedule and matched
    by User ID, so class enrolments at BOTH schools are preserved.
    """

    model_config = ConfigDict(extra="forbid")

    collapse: bool = False
    home_school_column: str = ""


class GlobalConfig(BaseModel):
    """Top-level global_config section."""

    school_year_sources: dict[str, str] = Field(default_factory=dict)
    homeroom_grades: list[str] = Field(default_factory=list)
    entity_order: list[str] = Field(default_factory=list)
    # Academic-period dates have NO Python default — the value must come from a
    # mapping YAML (either directly or via `_base:` inheritance). This makes the
    # source of truth the YAML and prevents non-BC districts from silently
    # inheriting BC-specific defaults. ``check_dates_required_for_classes``
    # rejects configs that enable Classes without these set.
    academic_start_month_day: Optional[str] = None
    academic_end_month_day: Optional[str] = None
    # Date past which the today's-date fallback for school_year rolls forward
    # to the next academic year. Only used when no source file has a 'school
    # year' column. When None, falls back to ``academic_end_month_day`` at the
    # pipeline layer.
    academic_year_rollover_month_day: Optional[str] = None
    # How a bare ``YYYY`` value in the source 'school year' column should be
    # interpreted. The pipeline internally uses end-year semantics, so this
    # affects parsing only:
    #
    # - ``"end"`` (default): a bare ``2026`` means the academic year ENDING
    #   in 2026 (i.e. 2025-2026). MyEd BC / BC convention.
    # - ``"start"``: a bare ``2025`` means the academic year STARTING in 2025
    #   (i.e. 2025-2026). Common Ontario / US convention. The parser will
    #   translate to end-year semantics (year + 1) before use.
    #
    # Ranges like ``2025/2026`` or ``2025-2026`` are unambiguous and ignore
    # this setting (second year always wins).
    school_year_naming: Literal["end", "start"] = "end"
    excluded_course_codes: list[str] = Field(default_factory=list)
    excluded_course_code_patterns: list[str] = Field(default_factory=list)
    excluded_course_flavors: list[str] = Field(default_factory=list)
    # Lowest grade to include in the CourseInfo / StudentCourses CSVs. MyEd BC
    # encodes the grade in the course code; courses below this grade are
    # dropped. Default 10 (grades 10-12). Set to 8 or 9 to include those
    # grade levels too — never lower. Drives a derived early-grade exclusion
    # pattern at transform time (see BaseTransformer.early_grade_exclusion_pattern).
    course_start_grade: int = 10
    # Subset of entity names from `mappings:` that should actually be produced.
    # Empty list means "all defined mappings are enabled" (backward-compatible).
    # Lets one config file define more entity templates than it activates.
    enabled_entities: list[str] = Field(default_factory=list)
    # StudentAttendance derivation knobs — read at runtime by
    # `StudentAttendanceTransformer`. Kept as an OPEN, lightly-typed dict (not a
    # nested model) so SpacesEDU/SD51 can tune the category map, portion→row
    # thresholds, and source-column names WITHOUT a code release, and so a later
    # slice can add the 8-12 (Enhanced Period) keys without a model change.
    # Empty/absent when the entity is not enabled (inert).
    attendance: dict[str, Any] = Field(default_factory=dict)
    # Opt-in Students cross-enrollment collapse (see CrossEnrollmentConfig).
    # None/absent → disabled (default); every non-opted-in district is unaffected.
    cross_enrollment: Optional[CrossEnrollmentConfig] = None

    @model_validator(mode="before")
    @classmethod
    def handle_missing(cls, data: Any) -> Any:
        if data is None:
            return {}
        return data

    @model_validator(mode="after")
    def check_course_code_patterns(self):
        for pat in self.excluded_course_code_patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"Invalid regex in excluded_course_code_patterns: {pat!r} ({exc})") from exc
        if self.course_start_grade not in (8, 9, 10):
            raise ValueError(f"course_start_grade must be 8, 9, or 10 (got {self.course_start_grade!r})")
        return self

    @model_validator(mode="after")
    def check_month_day_fields(self):
        """Validate MM-DD format for month-day config fields that are set.

        None is allowed at this layer — ``MappingConfig.check_dates_required_for_classes``
        decides whether a None value is acceptable given which entities will run.
        """
        for fname in ("academic_start_month_day", "academic_end_month_day", "academic_year_rollover_month_day"):
            value = getattr(self, fname)
            if value is None:
                continue
            if not re.fullmatch(r"\d{2}-\d{2}", value):
                raise ValueError(f"{fname} must be in MM-DD format (got {value!r})")
            month, day = map(int, value.split("-"))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise ValueError(f"{fname} has invalid month/day: {value!r}")
        return self


class MappingConfig(BaseModel):
    """Root config model — validated representation of the YAML mapping file."""

    version: Union[str, float]
    sis: str
    district_name: str = ""
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

    @model_validator(mode="after")
    def check_dates_required_for_classes(self):
        """If Classes is enabled, academic period dates must be set in the YAML.

        Run AFTER `_base:` inheritance is resolved (load_config merges before
        instantiating MappingConfig). Districts inheriting from a base that
        sets the dates (e.g. myedbc) pass automatically; standalone non-BC
        configs that forget to set them fail loudly so they don't silently
        get BC defaults.
        """
        enabled = (
            set(self.global_config.enabled_entities)
            if self.global_config.enabled_entities
            else set(self.mappings.keys())
        )
        if "Classes" not in enabled or "Classes" not in self.mappings:
            return self
        gc = self.global_config
        missing = [name for name in ("academic_start_month_day", "academic_end_month_day") if getattr(gc, name) is None]
        if missing:
            raise ValueError(
                f"global_config is missing required field(s) {missing} — these have NO Python defaults. "
                f"Set them in your mapping YAML, e.g.:\n"
                f"  global_config:\n"
                f'    academic_start_month_day: "08-25"\n'
                f'    academic_end_month_day: "07-25"\n'
                f"or inherit from a base config that defines them (e.g. `_base: myedbc`)."
            )
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
            entry: dict[str, Any] = {
                "source_files": dict(entity_cfg.source_files),
                "field_map": self.get_raw_field_map(entity_name),
            }
            if entity_cfg.headers:
                entry["headers"] = dict(entity_cfg.headers)
            if entity_cfg.row_filters:
                entry["row_filters"] = [rf.model_dump() for rf in entity_cfg.row_filters]
            mappings_raw[entity_name] = entry

        global_raw: dict[str, Any] = {
            "school_year_sources": dict(self.global_config.school_year_sources),
            "homeroom_grades": list(self.global_config.homeroom_grades),
            "entity_order": list(self.global_config.entity_order),
            "academic_start_month_day": self.global_config.academic_start_month_day,
            "academic_end_month_day": self.global_config.academic_end_month_day,
            "academic_year_rollover_month_day": self.global_config.academic_year_rollover_month_day,
            "school_year_naming": self.global_config.school_year_naming,
            "excluded_course_codes": list(self.global_config.excluded_course_codes),
            "excluded_course_code_patterns": list(self.global_config.excluded_course_code_patterns),
            "excluded_course_flavors": list(self.global_config.excluded_course_flavors),
            "course_start_grade": self.global_config.course_start_grade,
            "enabled_entities": list(self.global_config.enabled_entities),
            "attendance": dict(self.global_config.attendance),
            "cross_enrollment": (
                self.global_config.cross_enrollment.model_dump() if self.global_config.cross_enrollment else None
            ),
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
                ft_dict: dict[str, Any] = {"column": val.column}
                if val.transform:
                    ft_dict["transform"] = val.transform
                raw[key] = ft_dict
            elif isinstance(val, FieldFixedValue):
                raw[key] = {"value": val.value}
            elif isinstance(val, FieldAcademicYear):
                d: dict[str, Any] = {"use_academic_year": val.use_academic_year}
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
            elif isinstance(val, FieldEnrollStatus):
                es: dict[str, Any] = {}
                if val.status_column is not None:
                    es["status_column"] = val.status_column
                if val.withdraw_date_column is not None:
                    es["withdraw_date_column"] = val.withdraw_date_column
                if val.active_values is not None:
                    es["active_values"] = list(val.active_values)
                raw[key] = es
            else:
                raw[key] = val
        return raw
