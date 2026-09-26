"""Pydantic models for YAML mapping configuration.

Validates the full mapping file structure at load time so that typos,
missing fields, and schema violations surface as clear error messages
rather than cryptic KeyErrors deep in the pipeline.
"""

import difflib
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal, NamedTuple, Optional, Protocol, TypeVar, Union, cast, get_args

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Unknown-key vocabulary (plan 0053 S12, failure-policy.md P15). A config key
# nobody reads is a TYPO until proven otherwise: `enabled_entites` silently
# enables every entity, `transfrom:` silently ships an untransformed column.
# The known keys are always READ OFF THE MODEL (`model_keys`) — never restated —
# so a new field is known the moment it exists.
# -----------------------------------------------------------------------


class UnknownKey(NamedTuple):
    """One config key no model (or reader) knows: WHERE it sits, the key, the nearest known key.

    Config vocabulary only — never a value. ``suggestion`` is ``None`` when no known key is
    close enough to guess (``difflib`` ratio below 0.6).
    """

    location: str
    key: str
    suggestion: Optional[str]

    def describe(self) -> str:
        """``<location>: unknown key '<key>' — did you mean '<suggestion>'?`` (one line)."""
        hint = (
            f"did you mean '{self.suggestion}'?"
            if self.suggestion
            else "no known key is close to it — check the spelling against the documented keys"
        )
        return f"{self.location}: unknown key '{self.key}' — {hint}"


def model_keys(model: type[BaseModel]) -> frozenset[str]:
    """Every key ``model`` accepts in a YAML mapping: its field names plus their aliases.

    THE known-key vocabulary for a model, read off ``model_fields`` so it can never drift
    from what Pydantic actually accepts (``FieldNameConfig`` accepts both
    ``primary teacher flag`` and ``primary_teacher_flag`` — ``populate_by_name``).
    """
    keys: set[str] = set()
    for name, info in model.model_fields.items():
        keys.add(name)
        if info.alias:
            keys.add(info.alias)
    return frozenset(keys)


def nearest_key(key: str, known: Iterable[str]) -> Optional[str]:
    """The known key closest to ``key`` (``difflib``, ratio ≥ 0.6), or ``None``."""
    matches = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.6)
    return matches[0] if matches else None


def unknown_keys_in(mapping: Any, known: Iterable[str], *, location: str) -> list[UnknownKey]:
    """The keys of ``mapping`` not in ``known``, each with its nearest known key. PURE.

    A non-mapping ``mapping`` has no keys to judge (its SHAPE is the model's to refuse),
    so it answers ``[]``. Keys are reported in the mapping's own order, stringified (a YAML
    key may be an int).
    """
    if not isinstance(mapping, dict):
        return []
    vocabulary = frozenset(known)
    return [
        UnknownKey(location, str(key), nearest_key(str(key), vocabulary))
        for key in mapping
        if str(key) not in vocabulary
    ]


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
        "normalize_staff_role",
        "truncate_name",
        "normalize_iso_date",
    }
)

# -----------------------------------------------------------------------
# `global_config.class_rostering_grades` sugar for "the rostered grades ARE
# the homeroom grades" (an empty timetable scope). The config layer owns this
# vocabulary — the field's own `Literal` annotation below has to spell it
# anyway — and `src.etl.transformers.grades` imports it from here, keeping the
# dependency direction etl -> config (same arrangement as ALLOWED_TRANSFORMS).
# -----------------------------------------------------------------------
CLASS_ROSTERING_HOMEROOM_SENTINEL = "homeroom"


def _ceds_grade_codes() -> frozenset[str]:
    """The valid CEDS grade-code vocabulary, for grade-list validation messages.

    DEFERRED import, deliberately: ``src.etl.transformers.grades`` owns the CEDS
    table (single source of truth — the valid set is never restated here), but
    importing it at MODULE level would execute ``src/etl/transformers/__init__``
    → ``base`` → ``src.config.models``, i.e. a genuine circular import while
    this module is still initialising. By validation time every module is
    loaded, so the deferred import is safe and free (``sys.modules`` hit).
    """
    from src.etl.transformers.grades import CEDS_GRADE_CODES

    return CEDS_GRADE_CODES


def _session_time_roles() -> tuple[str, ...]:
    """The blended time-slot ROLES (``session_term`` …), in key order, for validation.

    DEFERRED import for the same reason as :func:`_ceds_grade_codes`:
    ``src.etl.transformers.blended`` owns ``SESSION_TIME_COMPONENTS`` (single source —
    never restated here) and importing it at module level would be circular.
    """
    from src.etl.transformers.blended import SESSION_TIME_COMPONENTS

    return tuple(SESSION_TIME_COMPONENTS)


def _require_ceds_grade_list(
    field: str,
    value: list,
    *,
    valid: frozenset[str],
    empty_consequence: str,
    absent_meaning: str,
    remedy: str,
) -> list:
    """THE single spelling of "a non-empty list of CEDS grade codes" (SSOT).

    Shared by ``class_rostering_grades`` and ``student_rostering_grades`` so the
    two opt-in grade-scope keys cannot grow divergent messages for the same
    mistake — only the CONSEQUENCE clauses differ, and those are passed in.
    Codes are matched EXACTLY: the CEDS vocabulary is case-significant
    (``"Other"`` is its one mixed-case member) and a listed-but-mistyped code
    silently de-scopes a whole cohort, so a case-folding compare would be the
    dangerous direction.
    """
    if not value:
        raise ValueError(
            f"{field} is an EMPTY list, which would {empty_consequence}. "
            f"Remove the key entirely to keep the default ({absent_meaning}). {remedy}"
        )
    unknown = [entry for entry in value if not isinstance(entry, str) or entry not in valid]
    if unknown:
        raise ValueError(f"{field} contains non-CEDS grade code(s) {unknown!r}. {remedy}")
    return value


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

    ``extra="forbid"`` for EVERY variant and EVERY origin (plan 0053 S12, D11): a
    misspelled key inside a field mapping (``transfrom:``) is not a harmless stray key —
    it silently ships wrong output (the column untransformed). So unlike an unknown
    ``global_config``/entity key (origin-keyed — ``loader.unknown_config_keys``), it is
    refused everywhere, a user-authored config included; :func:`classify_field` turns
    the refusal into a message naming the nearest known key.
    """

    model_config = ConfigDict(extra="forbid")

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
        """The column with the school year appended — an ID, so its column is REQUIRED.

        With ``append_year_to_id`` on, the value is an identity (a Class ID), and an
        absent source column used to give every row a blank one (§5 #29). It now
        fails CLOSED with a typed ``SourceSchemaError`` (``JOIN_KEY``) naming the
        column as configured (plan 0053 S10); the field-map engine re-raises typed
        errors, so the orchestrator decides the scope. DEFERRED import, as in
        :func:`_ceds_grade_codes`: ``columns`` imports this module.
        """
        from src.etl.errors import GuardKind
        from src.etl.transformers.columns import require_columns

        col_name = self.column.lower()
        if not self.append_year_to_id:
            # Legacy fallthrough: append disabled reads the column directly
            # (absent column → intended blank, not recorded).
            return working[col_name] if col_name in working.columns else pd.NA
        # failure-policy: join_key
        require_columns(working.columns, [self.column], entity=entity, guard=GuardKind.JOIN_KEY)
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

    # `extra="forbid"` is inherited from ConfiguredField (Pydantic merges a subclass's
    # config with its parent's); restated so this class's own declaration says so.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


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


#: Every structured field-mapping variant, read off :data:`FieldMapping` (never restated).
FIELD_VARIANTS: tuple[type[ConfiguredField], ...] = tuple(
    member for member in get_args(FieldMapping) if isinstance(member, type) and issubclass(member, ConfiguredField)
)

_Variant = TypeVar("_Variant", bound=ConfiguredField)


def _unknown_keys_sentence(findings: Iterable[UnknownKey]) -> str:
    """``unknown key 'transfrom' (did you mean 'transform'?)`` for each finding, ``; ``-joined."""
    parts = []
    for finding in findings:
        hint = f" (did you mean '{finding.suggestion}'?)" if finding.suggestion else " (no known key is close to it)"
        parts.append(f"unknown key '{finding.key}'{hint}")
    return "; ".join(parts)


def _build_variant(variant: type[_Variant], raw: dict[str, Any]) -> _Variant:
    """Construct ``variant`` from ``raw``; an unknown key is refused naming its nearest known key.

    Every variant forbids extras (:class:`ConfiguredField`). Pydantic's own refusal says only
    "Extra inputs are not permitted", so a TOP-level extra is re-raised as a ``ValueError``
    naming the key and the variant's nearest known key (a nested extra — an email
    ``derived_dates`` entry — and every other validation error propagate unchanged).
    """
    try:
        return variant(**raw)
    except ValidationError as exc:
        extras = [
            str(error["loc"][0])
            for error in exc.errors()
            if error["type"] == "extra_forbidden" and len(error["loc"]) == 1
        ]
        if not extras:
            raise
        known = model_keys(variant)
        findings = [UnknownKey("field mapping", key, nearest_key(key, known)) for key in extras]
        raise ValueError(_unknown_keys_sentence(findings)) from exc


#: The DISTINGUISHING keys of each structured variant, in DETECTION ORDER (first match wins).
#: The ONE statement of "which shape does this dict name" — :func:`classify_field` dispatches
#: on it and the loader's ``_base`` merge (``loader._deep_merge`` via :func:`switch_field_shape`)
#: uses it to tell a shape SWITCH from a partial override. ``column`` is deliberately absent: it is shared by
#: ``FieldTransform`` and ``FieldAppendYear``, so on its own it names no switch (a bare
#: ``{column: ...}`` still classifies as ``FieldTransform``, via :func:`field_shape`'s fallback).
_SHAPE_KEYS: tuple[tuple[frozenset[str], type[ConfiguredField]], ...] = (
    (frozenset({"format"}), FieldEmailFormat),
    (frozenset({"append_year_to_id"}), FieldAppendYear),
    (frozenset({"use_academic_year"}), FieldAcademicYear),
    (frozenset({"value"}), FieldFixedValue),
    (frozenset({"transform"}), FieldTransform),
    (frozenset({"student_id_col", "staff_id_col"}), FieldIdRolePair),
    (frozenset({"primary teacher flag", "teacher last name"}), FieldNameConfig),
    # EnrollStatus active-detection overrides, keyed on its distinct fields.
    (frozenset({"active_values", "status_column", "withdraw_date_column"}), FieldEnrollStatus),
)


def distinguishing_shape(raw: Mapping[Any, Any]) -> Optional[type[ConfiguredField]]:
    """The variant ``raw``'s DISTINGUISHING keys name (first match, detection order), else ``None``.

    ``None`` means ``raw`` carries only keys several shapes share (``column``, ``sanitize``,
    ``course title`` ...) — as an ``_base`` override it is a PARTIAL edit of whatever the
    base entry already is, never a switch.
    """
    for keys, variant in _SHAPE_KEYS:
        if not keys.isdisjoint(raw):
            return variant
    return None


def field_shape(raw: Mapping[Any, Any]) -> Optional[type[ConfiguredField]]:
    """The variant :func:`classify_field` builds ``raw`` as — ``None`` when it names no shape.

    :func:`distinguishing_shape`, falling back to ``FieldTransform`` for a dict that carries
    a ``column`` and nothing distinguishing (a plain column mapping).
    """
    variant = distinguishing_shape(raw)
    if variant is None and "column" in raw:
        return FieldTransform
    return variant


def switch_field_shape(base: Mapping[Any, Any], override: Mapping[Any, Any]) -> Optional[dict[str, Any]]:
    """The merged entry when an ``_base`` override SWITCHES one ``field_map`` entry's shape, else ``None``.

    A switch — the override's distinguishing keys name a different variant than the base
    entry is (``{value: "09"}`` over an inherited ``{column, transform}``) — keeps ONLY the
    inherited keys the new variant accepts, then applies the override: ``{value: "09"}``;
    ``{append_year_to_id: true}`` over ``{column, transform}`` keeps the inherited ``column``.
    Merged key by key instead, the base's other keys would ride into a variant that forbids
    them (every variant does, plan 0053 S12) and the refusal would blame the author for keys
    they never typed. ``None`` — no switch — means an ordinary recursive merge: a partial
    override (``{column: "Gr"}`` over ``{column, transform}``, ``{active_values: [...]}``
    over ``{status_column: ...}``) names no different shape and keeps inheriting the rest.
    Only the accepted keys of the new variant are read (:func:`model_keys`), so a kept key
    can never re-route detection: no variant accepts an EARLIER shape's distinguishing key.
    """
    switched_to = distinguishing_shape(override)
    if switched_to is None or switched_to is field_shape(base):
        return None
    accepted = model_keys(switched_to)
    entry: dict[str, Any] = {str(key): value for key, value in base.items() if str(key) in accepted}
    entry.update(override)
    return entry


def classify_field(raw: Any) -> FieldMapping:
    """Classify a raw YAML field_map value into its typed variant.

    Detection is by DISTINGUISHING key (:func:`field_shape` over ``_SHAPE_KEYS`` — first
    match wins; a bare ``column`` is a ``FieldTransform``), and every variant forbids extras, so a misspelled key is refused naming its nearest known key
    rather than dropped (plan 0053 S12). A dict carrying NO distinguishing key RAISES
    ``ValueError`` too — before S12 it was logged and returned raw, and the field-map
    engine then shipped the field blank with nothing recorded (``{transfrom: ...}`` with
    no ``column`` was exactly that). A non-dict, non-string scalar is still its text.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return str(raw)

    variant = field_shape(raw)
    if variant is not None:
        # Every _SHAPE_KEYS variant is a FieldMapping member (the table is typed on the base class).
        return cast(FieldMapping, _build_variant(variant, raw))

    # No distinguishing key: a typo'd key (`colum:`, `transfrom:` alone) or an empty dict.
    known = frozenset().union(*(model_keys(member) for member in FIELD_VARIANTS))
    unknown = unknown_keys_in(raw, known, location="field mapping")
    if unknown:
        raise ValueError(_unknown_keys_sentence(unknown))
    raise ValueError(
        f"the field mapping {sorted(str(key) for key in raw)} names no mapping shape — it needs a "
        "'column', 'value', 'format', 'use_academic_year', 'append_year_to_id', 'student_id_col' + "
        "'staff_id_col', a class-name block ('primary teacher flag' / 'teacher last name') or an "
        "enrolment-status block ('status_column' / 'withdraw_date_column' / 'active_values')"
    )


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
    # Opt-in, Classes only (plan 0053 S10 — owner ruling 2026-09-25; config format 1.14): the
    # blended-class TIME-SLOT components this district's export actually CARRIES, named by their
    # Classes ``source_columns`` ROLES (``session_term`` / ``session_semester`` / ``session_day`` /
    # ``session_period``). ``None`` (absent) = all four, byte-identical. Blended detection keys
    # sections on — and REQUIRES (failure-policy §5 #39) — exactly the declared ones, in the fixed
    # term → semester → day → period order whatever order they are listed in. It exists because a
    # blank or null role reads its DEFAULT column (the resolver's one shape policy), so "this export
    # has no term column" had no other explicit spelling; a component is never dropped silently.
    session_components: Optional[list[str]] = None

    @field_validator("session_components")
    @classmethod
    def check_session_components(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        """A declared component list is non-empty, names only known roles, each once."""
        if value is None:
            return value
        roles = _session_time_roles()
        if not value:
            raise ValueError(
                "session_components is empty: blended classes would then be keyed on school and "
                f"teacher alone, merging every section a teacher teaches. Declare the roles your "
                f"export carries (from {list(roles)}), or remove the key to use all four."
            )
        unknown = [role for role in value if role not in roles]
        if unknown:
            raise ValueError(f"session_components names unknown role(s) {unknown}; the roles are {list(roles)}.")
        if len(set(value)) != len(value):
            raise ValueError("session_components lists a role more than once.")
        return value

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
            try:
                spec = ensure_field_mapping(raw)
            except ValidationError:
                # Pydantic's own error for a variant (a missing required key, a wrong type)
                # already carries its location — unchanged.
                raise
            except ValueError as exc:
                # classify_field's unknown-key / no-shape refusal (plan 0053 S12): name the FIELD.
                raise ValueError(f"field_map entry '{key}': {exc}. Fix this entity's field_map.") from exc
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
    # Opt-out of blended-class detection (session-key merge of same-teacher/
    # same-time sections spanning 2+ grades). When False, sections are never
    # merged into blended classes; ClassInformation still loads and still
    # feeds co-teacher enrollment rows. Default True keeps every existing
    # district byte-identical. Exists because `BlendedClassDetector`'s
    # session_key (school + teacher + term + semester + day + period) needs a
    # day rotation to disambiguate sections — an export with no rotation (Day
    # always "1", e.g. SD51) makes distinct secondary courses collide into
    # false blends. See `docs/claugentic-ROADMAP.md` for the general fix.
    blended_classes: bool = True
    # Opt-in CLASS-rostering scope: the COMPLETE set of grades that receive class
    # rostering, in CEDS OUTPUT space ("KG", "01", ... — NOT raw MyEd values like
    # "K"/"3"). `homeroom_grades` must be a SUBSET of it (validated below), which
    # makes the derived scopes total:
    #
    #   homeroom scope  = homeroom_grades                     (homeroom classes + enrollments)
    #   timetable scope = class_rostering_grades − homeroom_grades  (subject classes + enrollments)
    #   grades in neither → NOTHING
    #
    # `"homeroom"` is sugar for "rostered == homeroom_grades" (empty timetable
    # scope), so a district needn't restate its whole homeroom list and the two
    # keys cannot drift. None/absent → today's behaviour: the timetable side is
    # the unbounded complement of `homeroom_grades`, byte-identical output.
    # Consumed by `src.etl.transformers.grades.resolve_timetable_scope`.
    class_rostering_grades: Optional[Union[Literal["homeroom"], list[str]]] = None
    # Opt-in STUDENT-rostering scope: the COMPLETE set of grades whose students
    # reach the output at all, in CEDS OUTPUT space (same vocabulary as above).
    # It is the OUTERMOST bound — narrowing it narrows Students.csv, and through
    # the existing zero-orphan roster (`context.active_student_ids`) it narrows
    # Family, Classes, Enrollments and StudentCourses with it:
    #
    #   validated chain: homeroom_grades ⊆ class_rostering_grades ⊆ student_rostering_grades
    #
    # There is NO `"homeroom"` sentinel here (it is meaningless for students) and
    # `[]` is REJECTED — "send no students" can only end in the delivery-integrity
    # floor, so it is refused at load rather than at 2am. None/absent → every
    # grade, i.e. today's behaviour, byte-identical output.
    #
    # When this is set and `class_rostering_grades` is ABSENT, the class scope
    # INHERITS this list as its outer bound (timetable scope =
    # `student_rostering_grades − homeroom_grades`) — otherwise subject/blended
    # classes would be built for grades whose students are not being sent, i.e. a
    # class with a teacher and zero students. Consumed by
    # `src.etl.transformers.grades.resolve_student_scope` (and, for that
    # inherited bound, `resolve_timetable_scope`).
    student_rostering_grades: Optional[list[str]] = None

    @model_validator(mode="before")
    @classmethod
    def handle_missing(cls, data: Any) -> Any:
        if data is None:
            return {}
        return data

    @field_validator("class_rostering_grades", mode="before")
    @classmethod
    def check_class_rostering_grades_shape(cls, value: Any) -> Any:
        """Reject anything that is not the sentinel or a non-empty CEDS-code list.

        Runs BEFORE the union coercion so the district author gets one actionable
        message naming the valid vocabulary, not a two-branch Pydantic union
        error. The sentinel is accepted case-insensitively and normalised (the
        intent is unambiguous, and a cosmetic mismatch must not fail a nightly
        sync); the GRADE CODES are matched EXACTLY, because the CEDS vocabulary
        is case-significant ("Other" is mixed-case) and a listed-but-mistyped
        code silently de-rosters a whole cohort.
        """
        if value is None:
            return value
        valid = _ceds_grade_codes()
        remedy = (
            f"Use the string '{CLASS_ROSTERING_HOMEROOM_SENTINEL}' (roster exactly the homeroom "
            f"grades), or a non-empty list of CEDS grade codes: {sorted(valid)}."
        )
        if isinstance(value, str):
            if value.strip().lower() == CLASS_ROSTERING_HOMEROOM_SENTINEL:
                return CLASS_ROSTERING_HOMEROOM_SENTINEL
            raise ValueError(f"class_rostering_grades got the bare string {value!r}. {remedy}")
        if not isinstance(value, list):
            raise ValueError(
                f"class_rostering_grades must be a string or a list (got {type(value).__name__}). {remedy}"
            )
        return _require_ceds_grade_list(
            "class_rostering_grades",
            value,
            valid=valid,
            empty_consequence="roster no classes at all",
            absent_meaning="every non-homeroom grade",
            remedy=remedy,
        )

    @field_validator("student_rostering_grades", mode="before")
    @classmethod
    def check_student_rostering_grades_shape(cls, value: Any) -> Any:
        """Validator 6 — a non-empty list of CEDS codes, and NO sentinel form.

        Shares :func:`_require_ceds_grade_list` with ``class_rostering_grades``
        so the "non-empty list of CEDS codes" rule (and its message) has ONE
        spelling. The two differences are deliberate and are the whole reason
        this validator exists separately:

        - **No ``"homeroom"`` sugar.** "Roster exactly the homeroom grades" is
          meaningless for STUDENTS (a student's grade is not a class), so the
          string form is rejected outright rather than quietly accepted.
        - **The ``[]`` rejection is a SAFETY decision here, not just a shape
          one.** ``student_rostering_grades: []`` means "send no students",
          whose only possible outcome is an empty roster →
          ``filter_to_active`` correctly no-ops → the ``Students`` anchor is
          absent from ``outputs`` → ``check_delivery_integrity`` raises
          ``INCOMPLETE_ROSTER`` before any write. A config whose every run is
          guaranteed to fail belongs on the load-time floor, not at 2am.
          (``class_rostering_grades: []`` is likewise rejected, above — the two
          new keys are symmetric on ``[]``. The real asymmetry is with
          ``homeroom_grades: []``, which is LEGAL: "no homeroom classes".)
        """
        if value is None:
            return value
        valid = _ceds_grade_codes()
        remedy = (
            f"Use a non-empty list of CEDS grade codes: {sorted(valid)}. There is no "
            f"'{CLASS_ROSTERING_HOMEROOM_SENTINEL}' shortcut for students — remove the key "
            f"entirely to send every grade."
        )
        if not isinstance(value, list):
            raise ValueError(f"student_rostering_grades must be a list (got {type(value).__name__}). {remedy}")
        return _require_ceds_grade_list(
            "student_rostering_grades",
            value,
            valid=valid,
            empty_consequence="send no students at all (every run would then fail at the delivery gate)",
            absent_meaning="every grade",
            remedy=remedy,
        )

    @model_validator(mode="after")
    def check_rostering_grade_scopes(self):
        """Validate the grade-scope rules that only apply when scoping is opted into.

        All of it fail-loud at config LOAD (before any run) and all of it gated
        on at least one of the two opt-in scope keys being set, so no other
        district is affected:

        1. ``homeroom_grades`` must itself be CEDS. Generally it is unvalidated
           (a pre-existing gap → roadmap), but under the ``"homeroom"`` sentinel
           it BECOMES the rostered set, and under the chain below it is compared
           against the other lists — so nothing else would check it.
        2. The sentinel over an EMPTY ``homeroom_grades`` means "roster nobody".
        3. **The subset chain**
           ``homeroom_grades ⊆ class_rostering_grades ⊆ student_rostering_grades``,
           each link checked only when both its sides are configured. The links
           are checked INDIVIDUALLY rather than leaning on transitivity, because
           the middle term is optional: with ``class_rostering_grades`` absent
           and ``student_rostering_grades`` set (shape 4 — a district licensed
           for K-8), transitivity gives nothing and ``homeroom_grades ⊆
           student_rostering_grades`` is the link that matters.

        ``homeroom_grades`` defaults to ``[]`` (never ``None``), so it is always
        "configured" for chain purposes — ``∅ ⊆ anything`` holds harmlessly.

        A district whose INHERITED ``homeroom_grades`` is not a subset of a new
        list must restate ``homeroom_grades`` explicitly (``[]`` included, which
        the list form legitimately allows) — ``_deep_merge`` replaces lists
        wholesale, so base ``myedbc``'s twelve homeroom codes are inherited in
        full unless overridden. Every message therefore names BOTH offending
        sets and says which one to edit.

        A derived EMPTY scope is legitimate and must NOT raise:
        ``student == homeroom`` simply yields an empty timetable scope.
        """
        if self.class_rostering_grades is None and self.student_rostering_grades is None:
            return self
        valid = _ceds_grade_codes()
        homeroom = set(self.homeroom_grades)
        unknown = sorted(grade for grade in homeroom if grade not in valid)
        if unknown:
            raise ValueError(
                f"homeroom_grades contains non-CEDS grade code(s) {unknown} — these must be CEDS "
                f"codes whenever class_rostering_grades or student_rostering_grades is set, because "
                f"the lists are compared against each other and against the CONVERTED grade column. "
                f"Valid codes: {sorted(valid)}."
            )

        students = set(self.student_rostering_grades) if self.student_rostering_grades is not None else None
        rostered: Optional[set[str]]
        if self.class_rostering_grades == CLASS_ROSTERING_HOMEROOM_SENTINEL:
            if not homeroom:
                raise ValueError(
                    f"class_rostering_grades: '{CLASS_ROSTERING_HOMEROOM_SENTINEL}' means "
                    f"'roster exactly the homeroom grades', but homeroom_grades is EMPTY — that "
                    f"would roster nobody. Set homeroom_grades, or list the grades to roster "
                    f"explicitly in class_rostering_grades."
                )
            # Under the sentinel the rostered set IS homeroom_grades, so the
            # homeroom→class link is satisfied by definition and the class→student
            # link reduces to the homeroom→student link checked below.
            rostered = None
        elif self.class_rostering_grades is not None:
            rostered = set(self.class_rostering_grades)
        else:
            rostered = None

        if rostered is not None:
            self._require_grade_subset(
                inner_name="homeroom_grades",
                inner=homeroom,
                outer_name="class_rostering_grades",
                outer=rostered,
            )
        if students is not None:
            self._require_grade_subset(
                inner_name="homeroom_grades",
                inner=homeroom,
                outer_name="student_rostering_grades",
                outer=students,
            )
            if rostered is not None:
                self._require_grade_subset(
                    inner_name="class_rostering_grades",
                    inner=rostered,
                    outer_name="student_rostering_grades",
                    outer=students,
                )
        return self

    @staticmethod
    def _require_grade_subset(*, inner_name: str, inner: set[str], outer_name: str, outer: set[str]) -> None:
        """One link of the subset chain, with the message a district reads at 8am.

        Names WHICH link broke, both sets in full, and the two ways to fix it —
        including the deep-merge trap (an inherited ``homeroom_grades`` is
        replaced wholesale, never merged), which is the single most likely cause
        of a link failing on a config that looks right.
        """
        missing = sorted(inner - outer)
        if not missing:
            return
        raise ValueError(
            # ASCII only in the raised text: this message reaches a Windows console
            # (cp1252) via the CLI's error path, where a subset glyph would raise
            # UnicodeEncodeError INSTEAD of showing the district what to fix.
            f"{inner_name} must be a SUBSET of {outer_name} (the grade-scope subset chain is "
            f"homeroom_grades inside class_rostering_grades inside student_rostering_grades), "
            f"but {missing} "
            f"is/are missing from it. {inner_name}={sorted(inner)}, {outer_name}={sorted(outer)}. "
            f"Add the missing grade(s) to {outer_name}, or restate {inner_name} (deep merge "
            f"REPLACES lists, so an inherited list must be restated in full — `[]` is allowed for "
            f"homeroom_grades)."
        )

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


# The shape a `district_domains:` entry must have — a LOWERCASE domain name: starts
# alphanumeric, at least one dot, an alphabetic TLD of 2+, and NO `@`. The `@` exclusion
# is the load-bearing part: it means a full email address pasted into the list fails
# `make validate-config` LOUDLY, in CI, before it could ship a real person's address in a
# public repo. Lowercase-only because the author writes this value by hand and matching
# compares it against an already-normalised (lowercased) domain — a mixed-case row would
# silently never match. Its case-INSENSITIVE twin, for the value the admin TYPES, is
# ``validators._IDENTITY_DOMAIN_RE``: two rules for two jobs, tied together by a parity
# test (tests/test_config_district_domains.py) asserting every shipped domain satisfies
# both.
_DISTRICT_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


def is_valid_district_domain(value: object) -> bool:
    """TOTAL predicate: is ``value`` a bare lowercase domain per ``_DISTRICT_DOMAIN_RE``?

    The ONE spelling of the `district_domains` entry rule (plan 0044 S1), shared by the
    model validator below (bundled configs — raise), the loader's user-dir pre-screen
    (WARN-and-drop) and, later, the creator form's boundary check. Total over any
    object so a non-string entry is simply invalid, never a TypeError.
    """
    # ``fullmatch``, not ``match``: ``$`` also matches BEFORE a trailing newline, so
    # ``match`` would accept ``"sd48.bc.ca\n"`` — a value that can never equal a
    # normalised domain and would therefore silently never match an admin.
    return isinstance(value, str) and _DISTRICT_DOMAIN_RE.fullmatch(value) is not None


class MappingConfig(BaseModel):
    """Root config model — validated representation of the YAML mapping file."""

    # DECLARED EXPLICITLY (plan 0038 S3) rather than left to Pydantic's default.
    # ``extra="ignore"`` is what makes a mapping YAML FORWARD-COMPATIBLE: a config
    # authored against a newer build — carrying a root key this build has never heard of
    # — still loads and runs instead of failing the whole district's nightly sync over a
    # key it does not need. That is a deliberate contract, not an accident, and it was
    # holding only by Pydantic's default. The leaf models that declare
    # ``extra="forbid"`` are exactly: `EmailDerivedDate`, `RowFilter`,
    # `CrossEnrollmentConfig` and — since plan 0053 S12, for EVERY origin — every
    # field-mapping variant (`ConfiguredField` and all its subclasses, `FieldTransform`
    # and `FieldNameConfig` included). `GlobalConfig` and `EntityConfig` still do NOT
    # forbid at the Pydantic level: an unknown key there is judged by ORIGIN in
    # `loader.unknown_config_keys` — a BUNDLED config raises at load, a USER-dir overlay
    # warns and runs, authoring refuses — so a typo'd `enabled_entities` is no longer
    # silently dropped (see `docs/developer/output-contract.md` -> Config schema). Pinned by
    # `tests/test_config_district_domains.py::test_the_forbid_and_ignore_models_are_exactly_as_documented`,
    # because this list has been written down wrong from memory twice.
    model_config = ConfigDict(extra="ignore")

    version: Union[str, float]
    sis: str
    district_name: str = ""
    # The district's PUBLIC staff email domain(s) — e.g. ``["sd48.bc.ca"]``. Presentation
    # metadata only: it scopes which rows a district picker shows to an admin whose work
    # email is at one of these domains (plan 0038). It is NOT access control (every
    # mapping ships in the binary; an unmatched admin sees the full list) and it is NOT
    # personal data (a school district publishes its own staff domain).
    #
    # STRUCTURALLY invisible to the ETL: ``to_raw_dict`` emits only ``mappings`` and
    # ``global_config``, so no top-level field here can reach a transformer — which is
    # why this is the right home for it and why adding it changes no output byte.
    # Deliberately top-level beside ``district_name`` (its precedent) rather than inside
    # ``global_config``, which is the ETL's own namespace. NOTE the tripwire recorded when
    # ``district_name`` was the only one: a FOURTH non-ETL field here should trigger
    # splitting these into a nested presentation section.
    district_domains: list[str] = Field(default_factory=list)
    global_config: GlobalConfig = Field(default_factory=GlobalConfig)
    mappings: dict[str, EntityConfig]

    @model_validator(mode="after")
    def validate_district_domains(self):
        """Fail LOUDLY on a `district_domains:` entry that is not a lowercase domain.

        The gate that keeps a plaintext personal email address out of this public repo:
        an entry containing ``@`` — i.e. someone pasted a whole address where a domain
        belongs — fails `make validate-config`, in CI, before it can be merged. Also
        rejects uppercase (which could never match a normalised domain), whitespace, and
        anything without a dot + alphabetic TLD.

        Raises rather than warns, on purpose. A silently-dropped bad row would leave the
        district *unclaimed* — which under the fail-open list rule looks completely
        normal (its admin just sees every district), so the mistake would never surface.

        **The message NEVER echoes the offending value.** The single most likely thing to
        trip this validator is a pasted personal email address — and this error surfaces in
        `make validate-config` output and a PUBLIC CI log, so quoting the value would
        republish the exact leak the check exists to stop (the same rule
        `validators.validate_identity_email` and `scripts/check_no_emails.py` already
        follow). The entry is located by its INDEX and described by shape; the author has
        the file open.
        """
        for index, entry in enumerate(self.district_domains, start=1):
            if not is_valid_district_domain(entry):
                shape = "a full email address (it contains '@')" if isinstance(entry, str) and "@" in entry else "not"
                raise ValueError(
                    f"district_domains entry {index} of {len(self.district_domains)} in config "
                    f"'{self.sis}' is {shape} a bare lowercase domain name. Use the bare domain "
                    "(e.g. 'sd48.bc.ca') — never a full email address, never uppercase. This list "
                    "holds a district's PUBLIC staff email domain; a personal address must never be "
                    "committed to this repository, so the offending value is deliberately NOT quoted "
                    "here (this message reaches a public CI log)."
                )
        return self

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

    @model_validator(mode="after")
    def check_student_scope_requires_the_students_entity(self):
        """Validator 9 — ``student_rostering_grades`` needs the ``Students`` entity.

        Without it the key is SILENTLY INERT, which is the one failure mode this
        opt-in exclusion key cannot afford. The narrowing works entirely through
        the roster set: ``StudentTransformer`` is the only publisher of
        ``context.active_student_ids`` (``students.py``), and
        ``BaseTransformer.filter_to_active`` deliberately fails SAFE on an empty
        roster (it must never filter-to-empty). So on a Students-less tier
        (``mbponly`` today, any future myBlueprint+-only tier) a config could
        declare a grade restriction while ``StudentCourses`` shipped every
        student's transcript.

        This rejects only the CONTRADICTION — a grade restriction on a config
        that cannot apply one. Students-less tiers themselves stay entirely
        legal. Entity-conditional global check, same shape as
        ``check_dates_required_for_classes``.
        """
        if self.global_config.student_rostering_grades is None:
            return self
        if "Students" in self.active_entities():
            return self
        raise ValueError(
            "global_config.student_rostering_grades restricts which grades of STUDENTS are sent, "
            "but this config does not produce the 'Students' entity (enabled_entities="
            f"{sorted(self.active_entities())}). The restriction is applied through the active "
            "roster, which only the Students entity publishes, so it would silently apply to "
            "NOTHING — course/attendance feeds would still carry every student. Enable 'Students' "
            "in enabled_entities, or remove student_rostering_grades."
        )

    @model_validator(mode="after")
    def check_session_components_placement(self):
        """``session_components`` is read by Classes' blended detection ONLY — and must agree with it.

        Rejects the two ways the key could be silently inert or contradicted: declared on any
        other entity (nothing reads it there), and a time-slot role CONFIGURED in the Classes
        ``source_columns`` while left out of ``session_components`` (the column would be named
        and then never used). Plan 0053 S10 (owner ruling 2026-09-25).
        """
        for name, entity in self.mappings.items():
            if name != "Classes" and entity.session_components is not None:
                raise ValueError(
                    f"mappings.{name}.session_components: only the Classes entity reads session_components "
                    f"(blended class detection); here it would do nothing. Move it to Classes or remove it."
                )
        classes = self.mappings.get("Classes")
        if classes is None or classes.session_components is None:
            return self
        undeclared = [
            role
            for role in _session_time_roles()
            if role in classes.source_columns and role not in classes.session_components
        ]
        if undeclared:
            raise ValueError(
                f"mappings.Classes.source_columns configures {undeclared}, but session_components leaves "
                f"{'it' if len(undeclared) == 1 else 'them'} out, so the column would never be read. Add "
                f"{'it' if len(undeclared) == 1 else 'them'} to session_components or remove the source_columns entry."
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
            if entity_cfg.session_components is not None:
                entry["session_components"] = list(entity_cfg.session_components)
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
            "blended_classes": self.global_config.blended_classes,
            # The sentinel passes through as the string; a list is copied. None
            # (absent) must survive as None — the ETL distinguishes "not set"
            # from an empty scope, so never collapse it to [].
            "class_rostering_grades": (
                list(self.global_config.class_rostering_grades)
                if isinstance(self.global_config.class_rostering_grades, list)
                else self.global_config.class_rostering_grades
            ),
            # Same None-preserving rule as above: absent means "every grade",
            # an empty list is rejected at validation, so None must never be
            # collapsed to [] (that would read as "send nobody").
            "student_rostering_grades": (
                list(self.global_config.student_rostering_grades)
                if self.global_config.student_rostering_grades is not None
                else None
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
                if not es:
                    # An all-default block must still NAME its shape: a bare `{}` is refused by
                    # classify_field ("names no mapping shape"), so the raw dict would not
                    # re-classify. `status_column: None` is read as unconfigured everywhere.
                    es = {"status_column": None}
                raw[key] = es
            else:
                raw[key] = val
        return raw
