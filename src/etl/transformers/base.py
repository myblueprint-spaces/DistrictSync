"""Base transformer: the per-entity contract + thin delegation to helper modules.

``BaseTransformer`` owns what is genuinely PER-ENTITY: the abstract
``transform`` interface, the generic ``apply_field_map`` dispatch, the shared
Class-ID assignment, the zero-orphan ``filter_to_active`` roster filter, the
config-driven ``row_filters``, the active-student predicate, and the fail-loud
data-error ledger.

The stateless helper families live in focused sibling modules and are
re-exposed here as SAME-SIGNATURE delegating wrappers (compatibility surface —
subclasses, tests, and the legacy ``DataTransformer`` facade keep calling
``self.<helper>`` / ``BaseTransformer.<helper>``):

- :mod:`src.etl.transformers.grades` — CEDS mapping + homeroom/subject split
- :mod:`src.etl.transformers.dates` — date parse/format grid + school-year math
- :mod:`src.etl.transformers.course_codes` — course-code exclusion/cleaning
- :mod:`src.etl.transformers.emails` — email template interpolation
- :mod:`src.etl.transformers.ids` — ID/join-key normalization
- :mod:`src.etl.transformers.naming` — class-name construction
- :mod:`src.etl.transformers.sources` — source_files normalization + access

``datetime.now()`` is resolved ONLY in this module (the established test seam
patches ``src.etl.transformers.base.datetime``); the helper modules take
``today`` as an explicit parameter.
"""

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from src.config.models import ALLOWED_TRANSFORMS as _ALLOWED_TRANSFORMS
from src.config.models import ConfiguredField, ensure_field_mapping
from src.etl.column_names import MASTER_TIMETABLE_ID
from src.etl.transformers import course_codes as _course_codes
from src.etl.transformers import dates as _dates
from src.etl.transformers import emails as _emails
from src.etl.transformers import grades as _grades
from src.etl.transformers import ids as _ids
from src.etl.transformers import naming as _naming
from src.etl.transformers import sources as _sources
from src.etl.transformers.context import TransformContext
from src.utils.helpers import normalize_columns as _normalize_columns

logger = logging.getLogger(__name__)


class BaseTransformer(ABC):
    # -----------------------------------------------------------------------
    # Allowlist of YAML-callable transform functions (security: prevents
    # arbitrary method invocation via getattr on user-supplied config).
    # Canonical set lives in src/config/models.py (single source of truth —
    # enforced fail-fast at config load by EntityConfig.validate_fields);
    # kept as a class attribute here so subclasses/tests can allow extra
    # transform methods for the defensive runtime check.
    # -----------------------------------------------------------------------
    ALLOWED_TRANSFORMS: frozenset[str] = _ALLOWED_TRANSFORMS

    # CEDS grade mapping — canonical table lives in grades.py (same object).
    CEDS_MAPPING: dict[str, str] = _grades.CEDS_MAPPING

    # -----------------------------------------------------------------------
    # Abstract interface
    # -----------------------------------------------------------------------
    @abstractmethod
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame: ...

    # -----------------------------------------------------------------------
    # Grade helpers (delegates → grades.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def grade_to_ceds(grade_value: Any) -> str:
        """Map a raw source grade to its CEDS code (see :func:`grades.grade_to_ceds`)."""
        return _grades.grade_to_ceds(grade_value)

    @staticmethod
    def map_role(teaching_flag: Any) -> str:
        val = str(teaching_flag).strip().lower()
        return "teacher" if val == "y" else "administrator"

    # -----------------------------------------------------------------------
    # Active-student detection (single source of truth — used by Students for
    # roster filtering and by Classes/Enrollments to drop orphan rows).
    # Source column names resolve from the Students field_map (Configurable
    # Columns rule); MyEd BC defaults apply when unconfigured.
    # -----------------------------------------------------------------------
    # Default status-column alias. Resolution picks the first spelling present
    # in the (normalized, lower-cased) frame: real two-L MyEd exports
    # ("Enrollment status") AND the one-L spelling used by the repo fixtures /
    # SD40's injected headers ("Enrolment Status"). None when neither present.
    DEFAULT_STATUS_COLUMN_ALIASES: tuple[str, ...] = ("enrollment status", "enrolment status")
    DEFAULT_WITHDRAW_DATE_COLUMN: str = "withdraw date"
    DEFAULT_ACTIVE_VALUES: tuple[str, ...] = ("Active", "PreReg")

    @classmethod
    def resolve_active_config(
        cls,
        students_field_map: dict[str, Any],
        df_columns: Any,
    ) -> tuple[Optional[str], str, list[str]]:
        """Resolve (status_column, withdraw_date_column, active_values).

        Reads the Students ``EnrollStatus`` config. When it is a dict, pulls
        ``status_column`` / ``withdraw_date_column`` / ``active_values`` (any
        absent key falls back to the default). When it is the bare-null
        sentinel (or absent), MyEd BC defaults apply:

        - ``status_column``: first of :attr:`DEFAULT_STATUS_COLUMN_ALIASES`
          **present in** ``df_columns`` (``None`` if neither → date-only).
        - ``withdraw_date_column``: :attr:`DEFAULT_WITHDRAW_DATE_COLUMN`.
        - ``active_values``: list(:attr:`DEFAULT_ACTIVE_VALUES`).

        A *configured* ``status_column`` string is honored verbatim (lower-cased
        to match normalized frames) and still presence-checked against the
        frame; it resolves to ``None`` when absent so detection falls through
        to the withdraw-date branch rather than raising.
        """
        present = {str(c).strip().lower() for c in df_columns}
        config = students_field_map.get("EnrollStatus")

        status_column: Optional[str] = None
        withdraw_date_column = cls.DEFAULT_WITHDRAW_DATE_COLUMN
        active_values = list(cls.DEFAULT_ACTIVE_VALUES)
        configured_status_col = False

        if isinstance(config, dict):
            raw_status = config.get("status_column")
            if raw_status:
                status_column = str(raw_status).strip().lower()
                configured_status_col = True
            raw_withdraw = config.get("withdraw_date_column")
            if raw_withdraw:
                withdraw_date_column = str(raw_withdraw).strip().lower()
            raw_active = config.get("active_values")
            if raw_active:
                active_values = [str(v) for v in raw_active]

        if not configured_status_col:
            status_column = next((alias for alias in cls.DEFAULT_STATUS_COLUMN_ALIASES if alias in present), None)
        elif status_column not in present:
            # Configured but absent from this frame — fall through to date branch.
            status_column = None

        return status_column, withdraw_date_column, active_values

    @classmethod
    def _classify_withdraw(cls, value: Any, today: date) -> tuple[bool, bool]:
        """Classify a withdraw-date cell (see :func:`dates.classify_withdraw`)."""
        return _dates.classify_withdraw(value, today)

    @classmethod
    def past_withdraw_date(cls, value: Any, today: date) -> bool:
        """True when ``value`` is a past/unparseable withdraw date (see :func:`dates.past_withdraw_date`)."""
        return _dates.past_withdraw_date(value, today)

    @classmethod
    def compute_enroll_status(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> pd.Series:
        """Per-row enrollment label (``"Active"`` / ``"Inactive"`` / any ``active_values``).

        Single source of truth for "is this student active". The live status
        value **wins**; the withdraw date is only a fallback:

        1. If the row has a **non-blank status value** (resolved status column) →
           status decides: the trimmed value when it is in ``active_values``,
           else ``"Inactive"``. The withdraw date is NOT consulted — an
           authoritative live status beats a lingering withdraw date (e.g. a
           re-enrolled student whose prior withdraw date is still on the record).
        2. Else (no status column, or a blank status value on that row) → fall
           back to the withdraw-date column: ``"Inactive"`` for a
           past/unparseable date, ``"Active"`` otherwise.
        3. Else (neither column present) → ``"Active"`` (with one warning).
        """
        if df.empty:
            return pd.Series([], dtype="object")

        status_column, withdraw_date_column, active_values = cls.resolve_active_config(students_field_map, df.columns)
        allowed = set(active_values)
        today = datetime.now().date()
        has_withdraw = withdraw_date_column in df.columns

        # Withdraw-date label — used for any row without a usable status value.
        if has_withdraw:
            classified = df[withdraw_date_column].apply(lambda v: cls._classify_withdraw(v, today))
            date_label = classified.apply(lambda t: "Inactive" if t[0] else "Active")
        else:
            classified = None
            date_label = pd.Series("Active", index=df.index, dtype="object")

        if status_column is not None:
            logger.info(
                f"[Students] Active-status resolved via status column '{status_column}' "
                f"(active values {active_values}); withdraw date used only as a per-row fallback."
            )
            status_vals = _ids.normalize_id_series(df[status_column])
            has_status = status_vals.ne("") & status_vals.str.lower().ne("nan")
            status_label = status_vals.apply(lambda v: v if v in allowed else "Inactive")
            labels = status_label.where(has_status, date_label)
            date_used = ~has_status
        elif has_withdraw:
            logger.info(
                f"[Students] No status column present; active-status resolved via "
                f"withdraw-date column '{withdraw_date_column}'."
            )
            labels = date_label
            date_used = pd.Series(True, index=df.index, dtype=bool)
        else:
            logger.warning(
                "[Students] Could not find an enrollment-status or withdraw-date column "
                f"(status aliases {list(cls.DEFAULT_STATUS_COLUMN_ALIASES)}, "
                f"withdraw column '{withdraw_date_column}'). Defaulting all rows to 'Active'."
            )
            return date_label

        # Warn about unparseable withdraw dates only where the date was actually used.
        if has_withdraw and classified is not None:
            unparseable = [
                str(v).strip()
                for v, (_, bad), used in zip(df[withdraw_date_column], classified, date_used)
                if bad and used
            ]
            if unparseable:
                logger.warning(
                    f"[Students] Could not parse {len(unparseable)} withdraw date(s); "
                    f"treated as Inactive. Sample formats: {set(unparseable[:10])}"
                )

        return labels

    @classmethod
    def is_active_mask(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> pd.Series:
        """Boolean mask of active rows: ``compute_enroll_status(...) != "Inactive"``.

        Label and mask share one function, so a district that drops ``"Active"``
        from ``active_values`` is honored (no implicit union with ``"Active"``).
        """
        return cls.compute_enroll_status(df, students_field_map) != "Inactive"

    @staticmethod
    def filter_to_active(
        df: pd.DataFrame,
        student_col: str,
        context: TransformContext,
        caller: str = "Enrollments",
    ) -> pd.DataFrame:
        """Keep only rows whose ``student_col`` is in the active roster.

        Single source of truth for the zero-orphan filter: both the homeroom
        (demographic) and subject (schedule) student-row derivations route
        through here so no emitted student row references a ``User ID`` absent
        from ``Students.csv``. The roster is ``context.active_student_ids`` —
        published by :class:`StudentTransformer` from its filtered output.

        Matching normalizes both sides with :func:`ids.normalize_id_series`
        because the demographic ``Student Number`` and schedule ``Student ID``
        carry the same pupil-number values but may differ in incidental
        whitespace.

        Fail-safe (never filter-to-empty): when the roster is empty (Students
        disabled or ran later) or ``student_col`` is absent, log a WARNING and
        return ``df`` unchanged rather than dropping every row. ``caller`` names
        the consumer in that warning.

        When rows ARE dropped, one aggregate WARNING per call reports the row
        count and the count of DISTINCT students involved — a mixed-vintage
        input set (e.g. a schedule referencing students missing from the
        demographic) is loudly visible instead of silently shrinking output.
        Counts only: student ids/names never appear in the message (PII rule).

        Returns a new frame (copy) so callers own it and can mutate columns
        without a ``SettingWithCopyWarning``, matching the other ``filter_*``
        helpers here.
        """
        if not context.active_student_ids or student_col not in df.columns:
            logger.warning(f"[{caller}] active_student_ids empty — skipping active filter")
            return df
        normalized = _ids.normalize_id_series(df[student_col])
        keep = normalized.isin(context.active_student_ids)
        dropped_rows = int((~keep).sum())
        if dropped_rows:
            distinct_students = int(normalized[~keep].nunique())
            logger.warning(
                f"[{caller}] Dropped {dropped_rows} row(s) referencing {distinct_students} "
                "student(s) absent from the active Students roster."
            )
        return df[keep].copy()  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Date helpers (delegates → dates.py; kept as methods because
    # normalize_iso_date is an ALLOWED_TRANSFORMS name resolved via getattr)
    # -----------------------------------------------------------------------
    @staticmethod
    def normalize_iso_date(value: Any) -> str:
        """Convert various date formats to ISO 8601 (see :func:`dates.normalize_iso_date`)."""
        return _dates.normalize_iso_date(value)

    @staticmethod
    def format_date(value: Any, strftime_format: str) -> str:
        """Reformat a flexible GDE date (see :func:`dates.format_date`)."""
        return _dates.format_date(value, strftime_format)

    @staticmethod
    def friendly_date_format_to_strftime(fmt: str) -> str:
        """Translate friendly tokens to strftime (see :func:`dates.friendly_date_format_to_strftime`)."""
        return _dates.friendly_date_format_to_strftime(fmt)

    @staticmethod
    def derive_date_part(value: Any, strftime_fmt: str) -> str:
        """Date part for derived email fields (see :func:`dates.derive_date_part`)."""
        return _dates.derive_date_part(value, strftime_fmt)

    # -----------------------------------------------------------------------
    # Text / column / ID helpers (delegates)
    # -----------------------------------------------------------------------
    @staticmethod
    def truncate_name(name: str, max_len: int = 100) -> str:
        """Word-boundary truncation (see :func:`naming.truncate_name`)."""
        return _naming.truncate_name(name, max_len)

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Strip whitespace and lowercase all column names."""
        return _normalize_columns(df)

    @staticmethod
    def clean_invalid_ids(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
        """Remove rows with NaN/empty/'nan' ids (see :func:`ids.clean_invalid_ids`)."""
        return _ids.clean_invalid_ids(df, id_col)

    # -----------------------------------------------------------------------
    # Course-code helpers (delegates → course_codes.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def filter_excluded_course_codes(df: pd.DataFrame, excluded_codes: list[str]) -> pd.DataFrame:
        """Drop rows by exact excluded course code (see :func:`course_codes.filter_excluded_course_codes`)."""
        return _course_codes.filter_excluded_course_codes(df, excluded_codes)

    @staticmethod
    def filter_excluded_course_code_patterns(
        df: pd.DataFrame,
        patterns: list[str],
        column: Optional[str] = None,
    ) -> pd.DataFrame:
        """Drop rows by course-code regex (see :func:`course_codes.filter_excluded_course_code_patterns`)."""
        return _course_codes.filter_excluded_course_code_patterns(df, patterns, column)

    @staticmethod
    def early_grade_exclusion_pattern(start_grade: Any) -> Optional[str]:
        """Early-grade course-code floor regex (see :func:`course_codes.early_grade_exclusion_pattern`)."""
        return _course_codes.early_grade_exclusion_pattern(start_grade)

    @staticmethod
    def effective_course_code_patterns(global_config: dict) -> list[str]:
        """Configured patterns + derived grade floor (see :func:`course_codes.effective_course_code_patterns`)."""
        return _course_codes.effective_course_code_patterns(global_config)

    @staticmethod
    def clean_course_code_flavor(code: Any, flavors: list[str]) -> str:
        """Flavor-substring truncation (see :func:`course_codes.clean_course_code_flavor`)."""
        return _course_codes.clean_course_code_flavor(code, flavors)

    # -----------------------------------------------------------------------
    # Config-driven row filtering (per-entity contract)
    # -----------------------------------------------------------------------
    @staticmethod
    def apply_row_filters(
        df: pd.DataFrame,
        filters: list[dict[str, Any]],
        entity_name: str,
    ) -> pd.DataFrame:
        """Keep only rows matching every config-driven ``row_filter`` (AND-combined).

        Each filter is a raw dict ``{"column": str, "include": [str, ...]}`` (as
        produced by ``MappingConfig.to_raw_dict``). A row survives when, for EVERY
        filter, its ``column`` value (trimmed + lower-cased) is in that filter's
        ``include`` set (also trimmed + lower-cased) — successive filters further
        narrow the frame. Columns are matched against the already-normalized
        (lowercase) frame, so the filter column is resolved with the same
        strip+lower treatment.

        Fail-loud (validate at boundary): a filter naming a column absent from
        the frame raises ``ValueError`` — a renamed source column must never
        silently keep everyone or no one. Empty/absent ``filters`` return ``df``
        unchanged. Only the kept/total COUNT is logged (never row values / PII).
        """
        if not filters:
            return df
        total = len(df)
        mask = pd.Series(True, index=df.index)
        for row_filter in filters:
            col = str(row_filter["column"]).strip().lower()
            if col not in df.columns:
                raise ValueError(
                    f"[{entity_name}] row_filter column '{col}' not found in source columns. "
                    f"Available: {sorted(df.columns)}"
                )
            include = {str(v).strip().lower() for v in row_filter.get("include", [])}
            values = _ids.normalize_id_series(df[col]).str.lower()
            mask &= values.isin(include)
        kept = int(mask.sum())
        logger.info(f"[{entity_name}] row_filters kept {kept}/{total} rows")
        return df[mask].copy()  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Source-file access (delegates → sources.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def normalize_source_config(source_config: Any) -> dict[str, str]:
        """Canonicalize source_files config to {role: filename} (see :func:`sources.normalize_source_config`)."""
        return _sources.normalize_source_config(source_config)

    def get_source_file(self, context: TransformContext, source_config: Any, role: str) -> pd.DataFrame:
        """Resolve a role to a copied frame from raw_data (see :func:`sources.get_source_file`)."""
        return _sources.get_source_file(context, source_config, role)

    # -----------------------------------------------------------------------
    # Field-map resolution + date resolution (per-entity contract)
    # -----------------------------------------------------------------------
    @staticmethod
    def resolve_column(field_map: dict[str, Any], key: str, default: str) -> str:
        """Resolve a source-column name from a field_map entry, with a default.

        The shared spelling of the repeated resolve-with-default idiom
        (Configurable Columns rule): a dict entry contributes its ``column``
        value (lower-cased, ``default`` when the key is absent); ANY other
        shape — missing entry, bare string, null sentinel — yields ``default``.
        NOTE: a bare string is deliberately NOT honored here, matching the
        legacy inline sites this replaces (Class ID / Grade / School ID); use
        the entity's own resolver where a bare string must win (e.g.
        ``FamilyTransformer._student_number_col``).
        """
        config = field_map.get(key, {})
        if isinstance(config, dict):
            return str(config.get("column", default)).lower()
        return default

    def resolve_date(self, field_map: dict[str, Any], field_name: str, context: TransformContext) -> str:
        """Resolve a date field from config — either a fixed value or academic year date.

        Eliminates the 4x repeated use_academic_year / value / fallback pattern.
        """
        config = field_map.get(field_name, {})
        if isinstance(config, dict) and "value" in config and not config.get("use_academic_year"):
            return config["value"]
        return context.academic_start if field_name == "Start Date" else context.academic_end

    # -----------------------------------------------------------------------
    # Field generation helpers
    # -----------------------------------------------------------------------
    def generate_class_id(self, row: pd.Series, mt_id_col: str, append_year: bool, context: TransformContext) -> str:
        mt_id = row.get(mt_id_col, "")
        if mt_id and append_year:
            return f"{mt_id}_{context.school_year}"
        return mt_id

    def assign_class_ids(self, df: pd.DataFrame, field_map: dict, context: TransformContext) -> pd.DataFrame:
        """Assign Class ID column using blended_class_map with generate_class_id fallback.

        Shared by ClassTransformer and EnrollmentTransformer to ensure IDs
        are computed identically across Classes and Enrollments output.
        """
        mt_id_col = self.resolve_column(field_map, "Class ID", MASTER_TIMETABLE_ID)

        if mt_id_col in df.columns:
            df[mt_id_col] = _ids.normalize_id_series(df[mt_id_col])
            df["Class ID"] = df[mt_id_col].map(context.blended_class_map)
            fallback = df.apply(
                lambda row: self.generate_class_id(row, mt_id_col=mt_id_col, append_year=True, context=context),
                axis=1,
            )
            df["Class ID"] = df["Class ID"].fillna(fallback)
        else:
            df["Class ID"] = df.apply(
                lambda row: self.generate_class_id(row, mt_id_col=mt_id_col, append_year=True, context=context),
                axis=1,
            )
        return df

    def generate_class_name(
        self,
        row: pd.Series,
        teacher_flag_col: str,
        teacher_last_col: str,
        course_title_col: str,
        section_letter_col: str,
        context: TransformContext,
    ) -> str:
        """Build a subject-class display name (see :func:`naming.generate_class_name`)."""
        return _naming.generate_class_name(
            row, teacher_flag_col, teacher_last_col, course_title_col, section_letter_col, context
        )

    @staticmethod
    def generate_student_email(row: pd.Series, format_str: str, sanitize: bool = False) -> str:
        """Interpolate row values into an email template (see :func:`emails.generate_student_email`)."""
        return _emails.generate_student_email(row, format_str, sanitize=sanitize)

    @staticmethod
    def generate_user_role(row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return "teacher"
        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return "student"
        return "unknown"

    @staticmethod
    def generate_user_id(row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return str(staff_val)
        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return str(student_val)
        return "UNKNOWN_ID"

    # -----------------------------------------------------------------------
    # School-year determination (delegates → dates.py; now() resolved HERE so
    # the `src.etl.transformers.base.datetime` test seam keeps working)
    # -----------------------------------------------------------------------
    @classmethod
    def determine_school_year(
        cls,
        all_data: dict[str, pd.DataFrame],
        source_config: Any,
        rollover_month_day: str,
        today: Optional[date] = None,
        school_year_naming: str = "end",
    ) -> int:
        """Return the academic year's END year (MyEd BC "School Year" convention).

        The pipeline always works in end-year semantics internally. Source
        formats are detected and translated:

        - ``YYYY/YYYY`` or ``YYYY-YYYY`` → second year (unambiguous)
        - ``YYYY`` → depends on ``school_year_naming``:

            - ``"end"`` (default, MyEd BC): treat as end year, return as-is
            - ``"start"`` (Ontario / US): treat as start year, return ``year + 1``

        Falls back to ``today`` (or now) when no source has a recognised
        value. Past ``rollover_month_day`` (default 07-25, the typical
        academic_end) the fallback rolls forward to the next academic
        year — accommodating districts that load upcoming-year exports a
        few weeks before the new year officially starts. Districts that
        upload even earlier can lower the rollover via the
        ``academic_year_rollover_month_day`` global_config field.

        All configured sources are scanned; the FIRST parsed value is used
        (behavior-preserving), but when the sources disagree — a mixed-vintage
        input set that would silently produce wrong academic dates and Class
        IDs — one loud WARNING names every end year found and which was chosen
        (see :func:`dates.determine_school_year`).
        """
        return _dates.determine_school_year(
            all_data,
            cls.normalize_source_config(source_config),
            rollover_month_day,
            today or datetime.now().date(),
            school_year_naming,
        )

    @staticmethod
    def _parse_school_year_to_end(raw: str, naming: str = "end") -> Optional[int]:
        """Parse a 'school year' cell to the END year (see :func:`dates.parse_school_year_to_end`)."""
        return _dates.parse_school_year_to_end(raw, naming)

    # -----------------------------------------------------------------------
    # Generic field-map application (used by Students, Staff, Family)
    # -----------------------------------------------------------------------
    def apply_field_map(
        self,
        working: pd.DataFrame,
        result: pd.DataFrame,
        field_map: dict[str, Any],
        entity: str,
        context: TransformContext,
    ) -> pd.DataFrame:
        """Apply the generic YAML field_map to produce output columns.

        Thin TYPED dispatch: each entry is normalized ONCE at this boundary
        via ``ensure_field_mapping`` (already-typed variants from the validated
        ``MappingConfig`` pass through untouched; raw YAML-shaped values from
        direct callers are classified) and then dispatched to the variant's
        ``.apply(...)`` Strategy (``src/config/models.py``). The legacy
        per-branch dict sniffing is gone; the semantics are unchanged:

        Fail-loud, never silent, never fails the run (data errors are a
        separate axis from ETL success). NOTE: only the ``transform:`` path is
        per-row resilient — other computed branches (e.g. ``append_year_to_id``)
        blank the whole column on any row failure (recorded as a column-level
        error, still never silent):

        - **Per-row resilience (``transform:`` path).** A ``transform:`` is applied
          per-row; a row whose ``func`` raises gets ``pd.NA`` in **that cell only**
          while every other row keeps its correct value. Those per-row failures are
          recorded.
        - **Column-level errors** — an unknown transform name (config error — now
          also rejected fail-fast at CONFIG LOAD by ``EntityConfig``, so on the
          pipeline path it cannot reach this loop; the check in
          ``FieldTransform.apply`` is defensive for direct callers), the
          ``append_year_to_id`` row-wise branch, or any structural failure — blank
          the whole column and continue (do NOT raise), recorded the same loud way.
          The ``append_year_to_id`` branch is deliberately **column-level**, not
          per-row: its helper ``generate_class_id`` performs no fallible
          operation (a ``row.get`` plus an f-string — it cannot raise on a single
          row), so a per-row try/except would defend a failure that cannot occur
          and only add a second near-duplicate resilience loop. Promote it to
          per-row ONLY if ``generate_class_id`` ever gains a parse/IO step that
          can raise on one row (Plan 0008, won't-fix-by-decision).
        - Every recorded failure appends a record to ``context.data_errors`` and
          logs at ERROR; ``run_pipeline`` surfaces a summary into the run-log
          (``data_errors``) and Run History — never swallowed.
        - **Intended blank** (the config column is simply absent from the frame)
          is NOT an error: it stays ``pd.NA`` and is NOT recorded.
        """
        for tgt_field, raw_spec in field_map.items():
            try:
                if tgt_field in result.columns:
                    continue

                spec = ensure_field_mapping(raw_spec)
                if isinstance(spec, ConfiguredField):
                    result[tgt_field] = spec.apply(working, self, tgt_field, entity, context)
                elif isinstance(spec, dict):
                    # classify_field's warn-passthrough (unrecognized dict
                    # structure): no usable 'column' key by definition — the
                    # legacy loop yielded an intended blank. NOT recorded.
                    result[tgt_field] = pd.NA
                else:
                    # Bare column name (str) or the auto-detect None sentinel —
                    # the direct read. An absent column is an intended blank
                    # (NOT an error — do not record).
                    col = str(spec).lower()
                    result[tgt_field] = working[col] if col in working.columns else pd.NA

            except Exception as ex:
                # Column-level error (unknown transform or any structural
                # failure). Blank the column, record loudly, continue — never
                # silently swallow, never fail the run.
                logger.error(f"Error transforming {entity}.{tgt_field}: {ex}")
                self._record_data_error(context, entity, tgt_field, failed_rows=len(working), sample=str(ex))
                result[tgt_field] = pd.NA

        return result

    def _apply_transform_resilient(
        self,
        series: pd.Series,
        func: Any,
        entity: str,
        tgt_field: str,
        context: TransformContext,
    ) -> list[Any]:
        """Apply ``func`` per-row so a single bad row blanks only that cell.

        A row whose ``func`` raises gets ``pd.NA`` for that cell; every other
        row keeps its correctly-transformed value. Per-row failures are
        aggregated into ``context.data_errors`` and logged at ERROR — never
        silently swallowed, never aborting the whole column or the run.
        """
        out: list[Any] = []
        failures = 0
        first_sample = ""
        for value in series:
            try:
                out.append(func(value))
            except Exception as ex:  # noqa: BLE001 — per-row isolation; recorded below
                out.append(pd.NA)
                failures += 1
                if not first_sample:
                    first_sample = f"{value!r}: {ex}"
        if failures:
            logger.error(
                f"Error transforming {entity}.{tgt_field}: {failures} row(s) failed "
                f"(blanked that cell only) — sample {first_sample}"
            )
            self._record_data_error(context, entity, tgt_field, failed_rows=failures, sample=first_sample)
        return out

    @staticmethod
    def _record_data_error(
        context: TransformContext,
        entity: str,
        field_name: str,
        failed_rows: int,
        sample: str,
    ) -> None:
        """Append one fail-loud data-error record to the run's shared ledger."""
        context.data_errors.append(
            {
                "entity": entity,
                "field": field_name,
                "failed_rows": failed_rows,
                "sample": sample,
            }
        )
