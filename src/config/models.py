"""Pydantic models for YAML mapping configuration.

Validates the full mapping file structure at load time so that typos,
missing fields, and schema violations surface as clear error messages
rather than cryptic KeyErrors deep in the pipeline.
"""

import logging
import re
from collections.abc import Iterable
from typing import Any, Literal, Optional, Protocol, Union, cast

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Allowlist of YAML-callable transform functions (security: prevents
# arbitrary method invocation via getattr on user-supplied config).
# Single source of truth — enforced at CONFIG LOAD by
# ``EntityConfig.validate_fields`` and referenced by
# ``BaseTransformer.ALLOWED_TRANSFORMS`` for the defensive runtime check
# (subclass-overridable there, so tests/extensions can allow extra names).
# -----------------------------------------------------------------------
ALLOWED_TRANSFORMS: frozenset[str] = frozenset(
    {
        "grade_to_ceds",
        "map_role",
        "truncate_name",
        "normalize_iso_date",
    }
)


# -----------------------------------------------------------------------
# Structural protocols for the field-apply Strategy. The config layer
# depends on these ABSTRACTIONS only — never on the ETL classes — so the
# dependency direction stays etl -> config (SOLID: DIP).
# -----------------------------------------------------------------------


class TransformContextLike(Protocol):
    """The slice of ``TransformContext`` the field Strategies read."""

    school_year: int
    academic_start: str
    academic_end: str


class FieldApplyHost(Protocol):
    """The transformer surface the field Strategies call back into.

    Structurally satisfied by ``BaseTransformer``: the allowlist for the
    defensive runtime transform check (subclass-overridable), the transform
    methods themselves (resolved by name via ``getattr``), the row-resilient
    per-row applicator, and the Class-ID generator.
    """

    ALLOWED_TRANSFORMS: frozenset[str]

    def generate_class_id(self, row: pd.Series, mt_id_col: str, append_year: bool, context: Any) -> str: ...

    def _apply_transform_resilient(
        self, series: pd.Series, func: Any, entity: str, tgt_field: str, context: Any
    ) -> list[Any]: ...


# -----------------------------------------------------------------------
# Field mapping variants — the polymorphic heart of the config.
# Each structured variant is a Strategy: ``.apply(...)`` produces exactly
# the value the generic field-map loop assigns for that variant, so
# ``BaseTransformer.apply_field_map`` is a thin typed dispatch with no
# dict sniffing.
# -----------------------------------------------------------------------


class ConfiguredField(BaseModel):
    """Common base of all structured (dict-shaped) field-mapping variants.

    Subclasses MUST implement :meth:`apply` — the Strategy the generic
    field-map loop dispatches to. Fail-loud: a future variant that forgets
    to implement it raises instead of silently blanking a column.
    """

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        """Return the value ``apply_field_map`` assigns to ``result[tgt_field]``."""
        raise NotImplementedError(f"{type(self).__name__} must implement apply()")


class ConfigCarrierField(ConfiguredField):
    """A variant that CONFIGURES dedicated machinery elsewhere (email
    generation, class naming, id/role pairing, active-status detection)
    rather than producing a column in the generic loop.

    In the generic loop these yield an intended blank (``pd.NA``) — exactly
    what the legacy dict-sniffing produced for them (no ``column`` key to
    read). The entity transformers that consume them fill the real column
    BEFORE ``apply_field_map`` runs, so the loop's already-present check
    skips them on the normal path.
    """

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        return pd.NA


class FieldTransform(ConfiguredField):
    """Column mapping with an optional transform function (e.g., grade_to_ceds)."""

    column: str
    transform: str = ""

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        column_name = self.column.lower()
        if column_name not in working.columns:
            # Intended blank: the config column is absent from the frame.
            # NOT an error — the caller does not record it.
            return pd.NA
        series = working[column_name]
        if not self.transform:
            return series
        if self.transform not in host.ALLOWED_TRANSFORMS:
            # Defensive runtime check. Config load already rejects unknown
            # names (EntityConfig.validate_fields), so on the pipeline path
            # this is unreachable; direct callers get the legacy column-level
            # error (caught + recorded by apply_field_map, never raised out).
            raise ValueError(
                f"Unknown transform '{self.transform}' for field '{tgt_field}'. "
                f"Allowed: {sorted(host.ALLOWED_TRANSFORMS)}"
            )
        func = getattr(host, self.transform)
        return host._apply_transform_resilient(series, func, entity, tgt_field, context)


class FieldFixedValue(ConfiguredField):
    """Fixed literal value injected into every row."""

    value: str

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        return self.value


class FieldAcademicYear(ConfiguredField):
    """Date resolved from the computed academic year bounds."""

    use_academic_year: bool = True
    value: Optional[str] = None

    @model_validator(mode="after")
    def check_consistency(self):
        if not self.use_academic_year and not self.value:
            raise ValueError("When use_academic_year is false, a 'value' must be provided")
        return self

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        # An explicit value wins (mirrors the legacy loop, where a raw dict
        # carrying a 'value' key hit the fixed-value branch first); otherwise
        # the field resolves from the computed academic-year bounds.
        if self.value is not None:
            return self.value
        return context.academic_start if tgt_field == "Start Date" else context.academic_end


class FieldAppendYear(ConfiguredField):
    """Column whose value gets the school year appended (e.g., MTID_2025)."""

    column: str
    append_year_to_id: bool = True

    def apply(
        self,
        working: pd.DataFrame,
        host: FieldApplyHost,
        tgt_field: str,
        entity: str,
        context: TransformContextLike,
    ) -> Any:
        col_name = self.column.lower()
        if not self.append_year_to_id:
            # Legacy fallthrough: append disabled reads the column directly
            # (absent column → intended blank, not recorded).
            return working[col_name] if col_name in working.columns else pd.NA
        return working.apply(
            lambda row: host.generate_class_id(row, mt_id_col=col_name, append_year=True, context=context),
            axis=1,
        )


class EmailDerivedDate(BaseModel):
    """A pseudo-field derived from a date column for use in an email template.

    Substitutes a formatted date part (e.g. a 2-digit year) into the email
    ``format`` string. ``column`` is the source date column (resolved from the
    normalized/lower-cased frame); ``date_format`` is a friendly token string
    (``yyyy``/``yy``/``MMMM``/``MMM``/``MM``/``dd``) translated at transform
    time by ``BaseTransformer.friendly_date_format_to_strftime``. Both are
    ``min_length=1`` so an empty value fails loudly at config load rather than
    silently producing a constant/garbled part. ``extra="forbid"`` catches
    typo'd keys.
    """

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    date_format: str = Field(min_length=1)


class FieldEmailFormat(ConfigCarrierField):
    """Template-based email generation using row fields.

    ``sanitize`` (opt-in, default off) reduces each substituted string value to
    ``[a-z0-9]`` (lowercase) so apostrophes/hyphens/spaces in names never leak
    into a local part. ``derived_dates`` (opt-in, default empty) maps a pseudo
    template field (e.g. ``"admission yy"``) to a date part derived from a
    source column — see :class:`EmailDerivedDate`. Both default off →
    every non-opted-in district's email output is byte-identical.
    """

    model_config = ConfigDict(extra="forbid")

    format: str
    sanitize: bool = False
    derived_dates: dict[str, EmailDerivedDate] = Field(default_factory=dict)


class FieldNameConfig(ConfigCarrierField):
    """Class Name config — references multiple source columns."""

    primary_teacher_flag: str = Field(alias="primary teacher flag", default="")
    teacher_last_name: str = Field(alias="teacher last name", default="")
    course_title: str = Field(alias="course title", default="")
    section_letter: str = Field(alias="section letter", default="")

    model_config = {"populate_by_name": True}


class FieldIdRolePair(ConfigCarrierField):
    """Paired student/staff ID columns for User ID or Role resolution."""

    student_id_col: str
    staff_id_col: str


class FieldEnrollStatus(ConfigCarrierField):
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


def ensure_field_mapping(raw: Any) -> FieldMapping:
    """Normalize one field_map value to its typed variant — idempotent.

    The single boundary through which apply-time consumers accept EITHER an
    already-typed variant (the pipeline's validated ``MappingConfig``) OR a raw
    YAML-shaped value (direct callers and tests). An already-typed value passes
    through by identity — it must never re-enter :func:`classify_field`, whose
    non-dict fallback would stringify a model instance (the re-entry hazard).
    """
    if raw is None or isinstance(raw, str):
        return raw
    if isinstance(raw, ConfiguredField):
        # Every concrete ConfiguredField subclass is a FieldMapping member;
        # the base class itself is never instantiated (apply() raises).
        return cast(FieldMapping, raw)
    return classify_field(raw)


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
    # Optional overrides for AUXILIARY source columns an entity reads but never
    # emits (no output-key counterpart in field_map — e.g. StudentCourses'
    # full-course-code / section / DL-start-date inputs). Keys are the
    # transformer's documented logical role names; values are the district's
    # source column names. Empty = the transformer's MyEd BC defaults apply
    # (default, back-compatible). Output-keyed source columns are configured
    # through field_map entries instead (string or {column: ...}).
    source_columns: dict[str, str] = Field(default_factory=dict)

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
        """Classify and validate each field_map entry.

        Also enforces :data:`ALLOWED_TRANSFORMS` at CONFIG LOAD (fail-fast):
        an unknown ``transform:`` name is a config error and must never reach
        the transform loop. The error names the field, the bad name, and the
        allowed set; Pydantic's error location supplies the entity name when
        raised through ``MappingConfig`` (``mappings.<Entity>``).
        """
        validated = {}
        for key, raw in self.field_map.items():
            spec = ensure_field_mapping(raw)
            if isinstance(spec, FieldTransform) and spec.transform and spec.transform not in ALLOWED_TRANSFORMS:
                raise ValueError(
                    f"Unknown transform '{spec.transform}' for field '{key}'. "
                    f"Allowed transforms: {sorted(ALLOWED_TRANSFORMS)}. "
                    "Fix this entity's field_map in the mapping YAML."
                )
            validated[key] = spec
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


def filter_enabled_entities(names: Iterable[str], enabled: Optional[Iterable[str]]) -> list[str]:
    """Apply the ``enabled_entities`` inclusion contract to an ordered entity list.

    THE single spelling of the selection rule (SSOT): an empty/None ``enabled``
    means ALL names pass (back-compat — mirror of the ``entity_order`` gotcha:
    both default to ``[]``, never ``None``, at the model layer); otherwise only
    names in the enabled set pass, preserving input order. Used by
    :meth:`MappingConfig.active_entities` (model-shaped callers) and the raw-dict
    pipeline boundary (``src.etl.pipeline.configured_entity_order``).
    """
    if not enabled:
        return list(names)
    enabled_set = set(enabled)
    return [name for name in names if name in enabled_set]


class MappingConfig(BaseModel):
    """Root config model — validated representation of the YAML mapping file."""

    version: Union[str, float]
    sis: str
    district_name: str = ""
    global_config: GlobalConfig = Field(default_factory=GlobalConfig)
    mappings: dict[str, EntityConfig]

    @model_validator(mode="after")
    def check_required_entities(self):
        """LOG (never raise) which standard rostering entities are absent.

        A partial config is legitimate (tiers like ``mbponly`` run without the
        rostering entities, and ``enabled_entities`` governs what is actually
        produced), so a missing standard entity is a load-time WARNING — an
        operator hand-rolling a new YAML sees the gap immediately instead of
        discovering a missing CSV at upload. Non-standard entities (CourseInfo,
        StudentCourses, StudentAttendance, ...) are valid and logged at DEBUG
        only. (Previously this set private attributes nothing read — dead code;
        now it actually emits the log its docstring promised.)
        """
        standard = {"Students", "Staff", "Family", "Classes", "Enrollments"}
        present = set(self.mappings.keys())
        missing = standard - present
        extra = present - standard
        if missing:
            logger.warning(
                f"Mapping config '{self.sis}' does not define standard rostering entities "
                f"{sorted(missing)} (defined: {sorted(present)}). Valid for partial tiers — "
                "enabled_entities decides output — but the SpacesEDU rostering upload needs all five."
            )
        if extra:
            logger.debug(f"Mapping config '{self.sis}' defines non-standard entities: {sorted(extra)}")
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
        if "Classes" not in self.active_entities():
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

    def active_entities(self) -> set[str]:
        """Entity names this config will actually produce (→ which CSVs are emitted).

        THE single accessor for the ``enabled_entities`` selection: empty
        ``enabled_entities`` = ALL defined mappings (back-compat), otherwise the
        enabled subset — always intersected with the DEFINED ``mappings`` so an
        enabled-but-undefined name (possible under ``_base`` inheritance or a
        partner-only entity like ``StudentAttendance``) never reports as
        produced (the pipeline gates on ``entity in mappings`` too). Ordering is
        a separate concern — see ``src.etl.pipeline.configured_entity_order``.
        """
        return set(filter_enabled_entities(self.mappings, self.global_config.enabled_entities))

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
            if entity_cfg.source_columns:
                entry["source_columns"] = dict(entity_cfg.source_columns)
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
                # Conditional-omit (mirrors the FieldEnrollStatus branch): only
                # emit sanitize/derived_dates when non-default so districts that
                # carry a bare `format:` round-trip to exactly {"format": ...}
                # (keeps SD40/48/51/54/74 transform output byte-identical and
                # test_sd51_custom_email green). Emit plain dicts via model_dump.
                ef: dict[str, Any] = {"format": val.format}
                if val.sanitize:
                    ef["sanitize"] = True
                if val.derived_dates:
                    ef["derived_dates"] = {k: v.model_dump() for k, v in val.derived_dates.items()}
                raw[key] = ef
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
