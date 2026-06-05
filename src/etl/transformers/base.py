"""Base transformer with shared utilities and the generic field-mapping loop.

All entity-specific transformers inherit from this.
DRY: column normalization, date resolution, ID cleaning, source file access,
and the generic field_map application are defined once here.
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from src.etl.column_names import COURSE_CODE, DISTRICT_COURSE_CODE, MASTER_TIMETABLE_ID
from src.etl.transformers.context import TransformContext
from src.utils.helpers import normalize_columns as _normalize_columns

logger = logging.getLogger(__name__)


class BaseTransformer(ABC):
    # -----------------------------------------------------------------------
    # Allowlist of YAML-callable transform functions (security: prevents
    # arbitrary method invocation via getattr on user-supplied config)
    # -----------------------------------------------------------------------
    ALLOWED_TRANSFORMS: frozenset[str] = frozenset(
        {
            "grade_to_ceds",
            "map_role",
            "truncate_name",
        }
    )

    # -----------------------------------------------------------------------
    # CEDS grade mapping (class-level constant)
    # -----------------------------------------------------------------------
    CEDS_MAPPING: dict[str, str] = {
        "INFANT/TODDLER": "IT",
        "PRESCHOOL": "PR",
        "PRE-K": "PK",
        "PREKINDERGARTEN": "PK",
        "TK": "TK",
        "TRANSITIONAL KINDERGARTEN": "TK",
        "KINDERGARTEN": "KG",
        "K": "KG",
        "01": "01",
        "1": "01",
        "02": "02",
        "2": "02",
        "03": "03",
        "3": "03",
        "04": "04",
        "4": "04",
        "05": "05",
        "5": "05",
        "06": "06",
        "6": "06",
        "07": "07",
        "7": "07",
        "08": "08",
        "8": "08",
        "09": "09",
        "9": "09",
        "10": "10",
        "11": "11",
        "12": "12",
        "13": "13",
        "POSTSECONDARY": "PS",
        "UGRADED": "UG",
        "UNGRADED": "UG",
        "UG": "UG",
        "OTHER": "Other",
        "EL": "KG",
        "KF": "KG",
    }

    # -----------------------------------------------------------------------
    # Abstract interface
    # -----------------------------------------------------------------------
    @abstractmethod
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame: ...

    # -----------------------------------------------------------------------
    # Static utility methods
    # -----------------------------------------------------------------------
    @staticmethod
    def grade_to_ceds(grade_value: Any) -> str:
        original = str(grade_value).strip().upper() if pd.notna(grade_value) else ""
        return BaseTransformer.CEDS_MAPPING.get(original, "UG")

    @staticmethod
    def map_role(teaching_flag: Any) -> str:
        val = str(teaching_flag).strip().lower()
        return "teacher" if val == "y" else "administrator"

    # -----------------------------------------------------------------------
    # Active-student detection (single source of truth — used by Students for
    # roster filtering and, in a later slice, by Classes/Enrollments to drop
    # orphan rows). Source column names resolve from the Students field_map
    # (Configurable Columns rule); MyEd BC defaults apply when unconfigured.
    # -----------------------------------------------------------------------
    # Default status-column alias. Resolution picks the first spelling present
    # in the (normalized, lower-cased) frame: real two-L MyEd exports
    # ("Enrollment status") AND the one-L spelling used by the repo fixtures /
    # SD40's injected headers ("Enrolment Status"). None when neither present.
    DEFAULT_STATUS_COLUMN_ALIASES: tuple[str, ...] = ("enrollment status", "enrolment status")
    DEFAULT_WITHDRAW_DATE_COLUMN: str = "withdraw date"
    DEFAULT_ACTIVE_VALUES: tuple[str, ...] = ("Active", "PreReg")
    _WITHDRAW_DATE_FORMATS: tuple[str, ...] = ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")

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
        """Classify a withdraw-date cell as ``(is_withdrawn, was_unparseable)``.

        - Blank / NaN → ``(False, False)`` (no withdrawal).
        - Parses to a date on/before ``today`` → ``(True, False)``.
        - Parses to a future date → ``(False, False)`` (still enrolled).
        - Non-blank but unparseable → ``(True, True)`` (fail-safe to Inactive;
          the caller aggregates these into one warning).
        """
        if pd.isna(value) or str(value).strip() == "":
            return False, False
        date_str = str(value).strip()
        for fmt in cls._WITHDRAW_DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt).date() <= today, False
            except ValueError:
                continue
        return True, True

    @classmethod
    def past_withdraw_date(cls, value: Any, today: date) -> bool:
        """True when ``value`` is a past/unparseable withdraw date.

        Thin per-value predicate over :meth:`_classify_withdraw` (a blank or
        future date is not a withdrawal). Exposed for reuse by callers that
        need the boolean directly.
        """
        return cls._classify_withdraw(value, today)[0]

    @classmethod
    def compute_enroll_status(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> pd.Series:
        """Per-row enrollment label in {``"Active"``, ``"PreReg"``, ``"Inactive"``}.

        Resolution (single source of truth for "is this student active"):

        1. If the resolved status column is present → label is the trimmed
           value when it is in ``active_values``, else ``"Inactive"``.
        2. Else if the withdraw-date column is present → ``"Inactive"`` for a
           past/unparseable date, ``"Active"`` otherwise.
        3. Else → ``"Active"`` (with one warning; nothing to detect on).

        A past (or unparseable) withdraw date is then a **hard override**: it
        downgrades any row to ``"Inactive"`` regardless of the status branch.
        """
        if df.empty:
            return pd.Series([], dtype="object")

        status_column, withdraw_date_column, active_values = cls.resolve_active_config(students_field_map, df.columns)
        allowed = set(active_values)
        today = datetime.now().date()

        if status_column is not None:

            def _label_from_status(value: Any) -> str:
                trimmed = str(value).strip()
                return trimmed if trimmed in allowed else "Inactive"

            labels = df[status_column].apply(_label_from_status)
        elif withdraw_date_column in df.columns:
            labels = pd.Series("Active", index=df.index, dtype="object")
        else:
            logger.warning(
                "[Students] Could not find an enrollment-status or withdraw-date column "
                f"(status aliases {list(cls.DEFAULT_STATUS_COLUMN_ALIASES)}, "
                f"withdraw column '{withdraw_date_column}'). Defaulting all rows to 'Active'."
            )
            return pd.Series("Active", index=df.index, dtype="object")

        # Hard override: a past/unparseable withdraw date wins over status.
        if withdraw_date_column in df.columns:
            classified = df[withdraw_date_column].apply(lambda v: cls._classify_withdraw(v, today))
            withdrawn = classified.apply(lambda t: t[0])
            unparseable = [str(v).strip() for v, (_, bad) in zip(df[withdraw_date_column], classified) if bad]
            if unparseable:
                logger.warning(
                    f"[Students] Could not parse {len(unparseable)} withdraw date(s); "
                    f"treated as Inactive. Sample formats: {set(unparseable[:10])}"
                )
            labels = labels.mask(withdrawn, "Inactive")

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

        Matching normalizes both sides with ``astype(str).str.strip()`` because
        the demographic ``Student Number`` and schedule ``Student ID`` carry the
        same pupil-number values but may differ in incidental whitespace.

        Fail-safe (never filter-to-empty): when the roster is empty (Students
        disabled or ran later) or ``student_col`` is absent, log a WARNING and
        return ``df`` unchanged rather than dropping every row. ``caller`` names
        the consumer in that warning.

        Returns a new frame (copy) so callers own it and can mutate columns
        without a ``SettingWithCopyWarning``, matching the other ``filter_*``
        helpers here.
        """
        if not context.active_student_ids or student_col not in df.columns:
            logger.warning(f"[{caller}] active_student_ids empty — skipping active filter")
            return df
        normalized = df[student_col].astype(str).str.strip()
        return df[normalized.isin(context.active_student_ids)].copy()  # type: ignore[return-value]

    @staticmethod
    def normalize_iso_date(value: Any) -> str:
        """Convert various date formats to ISO 8601 (yyyy-mm-dd).

        Accepts dd-MMM-yyyy (e.g. '15-Sep-2024'), already-ISO yyyy-mm-dd,
        and m/d/yyyy / d/m/yyyy. Returns the original trimmed string if
        no format matches, or '' for NaN/None/empty inputs.
        """
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        s = str(value).strip()
        if not s or s.lower() == "nan":
            return ""
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return s

    @staticmethod
    def truncate_name(name: str, max_len: int = 100) -> str:
        """Gracefully truncate a string, breaking at word boundaries."""
        if len(name) <= max_len:
            return name
        trunc_len = max_len - 3
        last_space = name.rfind(" ", 0, trunc_len)
        if last_space != -1:
            return name[:last_space] + "..."
        return name[:trunc_len] + "..."

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Strip whitespace and lowercase all column names."""
        return _normalize_columns(df)

    @staticmethod
    def clean_invalid_ids(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
        """Remove rows where id_col is NaN, empty, or the literal string 'nan'."""
        clean = df[id_col].astype(str).str.strip().str.lower()
        return df[df[id_col].notna() & (clean != "") & (clean != "nan")]  # type: ignore[return-value]

    @staticmethod
    def filter_excluded_course_codes(df: pd.DataFrame, excluded_codes: list[str]) -> pd.DataFrame:
        """Drop rows whose course code matches an entry in excluded_codes.

        Checks `course code` first, then `district course code`. Match is
        case-insensitive and whitespace-trimmed. Returns df unchanged when
        excluded_codes is empty or neither column is present.
        """
        if not excluded_codes or df.empty:
            return df
        exclusion_set = {str(c).strip().upper() for c in excluded_codes}
        for col in (COURSE_CODE, DISTRICT_COURSE_CODE):
            if col in df.columns:
                values = df[col].astype(str).str.strip().str.upper()
                return df[~values.isin(exclusion_set)].copy()  # type: ignore[return-value]
        return df

    @staticmethod
    def filter_excluded_course_code_patterns(
        df: pd.DataFrame,
        patterns: list[str],
        column: Optional[str] = None,
    ) -> pd.DataFrame:
        """Drop rows whose course code matches any regex in `patterns`.

        Patterns are combined into a single case-insensitive alternation
        and applied to the trimmed string value. When `column` is None,
        checks `course code` then `district course code` (first found
        wins), matching `filter_excluded_course_codes`. Patterns are
        expected to be pre-validated at config load time.
        """
        if not patterns or df.empty:
            return df
        combined = "|".join(f"(?:{p})" for p in patterns)
        candidate_cols = [column] if column else [COURSE_CODE, DISTRICT_COURSE_CODE]
        for col in candidate_cols:
            if col and col in df.columns:
                values = df[col].astype(str).str.strip()
                matches = values.str.contains(combined, regex=True, case=False, na=False)
                return df[~matches].copy()  # type: ignore[return-value]
        return df

    @staticmethod
    def early_grade_exclusion_pattern(start_grade: Any) -> Optional[str]:
        """Regex that drops MyEd BC course codes for grades below `start_grade`.

        MyEd BC encodes the grade in the 6th-7th characters of the course code
        as a two-digit number; single-digit grades 01-09 appear as "0X".
        This builds a pattern matching "0" followed by any digit strictly below
        `start_grade`, so grades >= start_grade (including 10-12, which begin
        with "1") survive. With start_grade=10 the result is equivalent to the
        legacy ``^.{5}0\\d`` pattern (excludes 00-09). Returns None when
        start_grade <= 1 (nothing to exclude).
        """
        try:
            sg = int(start_grade)
        except (TypeError, ValueError):
            sg = 10
        sg = min(sg, 10)
        if sg <= 1:
            return None
        return rf"^.{{5}}0[0-{sg - 1}]"

    @classmethod
    def effective_course_code_patterns(cls, global_config: dict) -> list[str]:
        """Configured exclusion patterns plus the grade floor derived from
        `course_start_grade` (default 10). Used by the CourseInfo and
        StudentCourses transformers so the minimum grade is a single,
        editable knob rather than a hand-written regex.
        """
        patterns = list(global_config.get("excluded_course_code_patterns", []))
        early = cls.early_grade_exclusion_pattern(global_config.get("course_start_grade", 10))
        if early:
            patterns.append(early)
        return patterns

    @staticmethod
    def clean_course_code_flavor(code: Any, flavors: list[str]) -> str:
        """Truncate course code to first 7 chars if it contains any flavor substring.

        Mirrors the PowerShell Get-CleanedCourseCode helper. Matching is
        case-insensitive substring (e.g., "DL" matches "MATH-DL01" -> "MATH-DL").
        Returns the original code as a string when no flavor matches, or ""
        for NaN/None inputs.
        """
        if code is None or (isinstance(code, float) and pd.isna(code)):
            return ""
        code_str = str(code)
        if not code_str or not flavors:
            return code_str
        upper = code_str.upper()
        for flavor in flavors:
            f = str(flavor).strip().upper()
            if f and f in upper:
                return code_str[:7]
        return code_str

    @staticmethod
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

    # -----------------------------------------------------------------------
    # Data access helpers
    # -----------------------------------------------------------------------
    def get_source_file(self, context: TransformContext, source_config: Any, role: str) -> pd.DataFrame:
        normalized = self.normalize_source_config(source_config)
        filename = normalized.get(role)
        if filename and filename in context.raw_data:
            return context.raw_data[filename].copy()
        logger.warning(f"Source file for role '{role}' not found in configuration")
        return pd.DataFrame()

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
        class_id_config = field_map.get("Class ID", {})
        mt_id_col = (
            class_id_config.get("column", MASTER_TIMETABLE_ID).lower()
            if isinstance(class_id_config, dict)
            else MASTER_TIMETABLE_ID
        )

        if mt_id_col in df.columns:
            df[mt_id_col] = df[mt_id_col].astype(str).str.strip()
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
        course_title = str(row.get(course_title_col, row.get("title", "Unknown Course"))).strip()
        teacher_last = ""

        if teacher_flag_col and teacher_flag_col in row:
            if str(row.get(teacher_flag_col, "")).strip().lower() == "y":
                teacher_last = str(row.get(teacher_last_col, "")).strip()
        else:
            teacher_last = str(row.get(teacher_last_col, "")).strip()

        if pd.isna(teacher_last) or teacher_last.lower() == "nan":
            teacher_last = ""

        section = str(row.get(section_letter_col, "")).strip()
        year = context.school_year

        parts = []
        if teacher_last:
            parts.append(teacher_last)
        parts.append(course_title)
        if section:
            if parts:
                parts[-1] = f"{parts[-1]} ({section})"
            else:
                parts.append(f"({section})")
        parts.append(str(year))

        return self.truncate_name(" ".join(parts).strip())

    @staticmethod
    def generate_student_email(row: pd.Series, format_str: str) -> str:
        """Interpolate row values into a lowercased email format string.

        StudentTransformer lowercases ``format_str`` before calling, so any
        template like ``{Legal Surname}.{Usual First Name}@sd54.bc.ca``
        becomes ``{legal surname}.{usual first name}@sd54.bc.ca`` — matching
        the lowercased column names. String row values are similarly
        normalised (lowercased, whitespace trimmed, internal spaces collapsed)
        so double-barrelled surnames like "Goodrick Hill" produce a
        deliverable local part ("goodrickhill"). NaN/None values become "".
        """
        try:
            normalised: dict[str, Any] = {}
            for k, v in row.to_dict().items():
                key = str(k).lower()
                if pd.isna(v):
                    normalised[key] = ""
                elif isinstance(v, str):
                    normalised[key] = v.strip().lower().replace(" ", "")
                else:
                    normalised[key] = v
            return format_str.format(**normalised)
        except KeyError as e:
            logger.warning(f"Could not generate email. Missing key: {e}")
            return ""

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

    def determine_school_year(
        self,
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
        """
        normalized = self.normalize_source_config(source_config)
        for _role, filename in normalized.items():
            df = all_data.get(filename)
            if df is not None and "school year" in df.columns:
                for raw in df["school year"].dropna().astype(str).str.strip().unique():
                    parsed = self._parse_school_year_to_end(str(raw), school_year_naming)
                    if parsed is not None:
                        return parsed

        return self._fallback_school_year(today or datetime.now().date(), rollover_month_day)

    @staticmethod
    def _parse_school_year_to_end(raw: str, naming: str = "end") -> Optional[int]:
        """Parse a 'school year' cell value to the academic-period END year.

        - ``YYYY/YYYY`` or ``YYYY-YYYY`` → second year (range is unambiguous;
          ``naming`` is ignored)
        - ``YYYY`` with ``naming="end"`` → year as-is
        - ``YYYY`` with ``naming="start"`` → ``year + 1``
        - anything else → None
        """
        raw = raw.strip()
        parts = re.split(r"[/-]", raw)
        if len(parts) == 2 and all(p.isdigit() and len(p) == 4 for p in parts):
            return int(parts[1])
        if raw.isdigit() and len(raw) == 4:
            year = int(raw)
            return year + 1 if naming == "start" else year
        return None

    @staticmethod
    def _fallback_school_year(today: date, rollover_month_day: str) -> int:
        """End-year fallback when no 'school year' source column is found.

        Returns ``today.year`` before the rollover (still in current academic
        year ending this calendar year) and ``today.year + 1`` from the
        rollover onwards (next academic year about to start, ending next
        calendar year).
        """
        try:
            month, day = map(int, rollover_month_day.split("-"))
            rollover = date(today.year, month, day)
        except (ValueError, TypeError):
            logger.warning(f"Invalid academic_year_rollover_month_day '{rollover_month_day}'; using 08-01 cutoff.")
            rollover = date(today.year, 8, 1)
        return today.year if today < rollover else today.year + 1

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
        """Apply the generic YAML field_map to produce output columns."""
        for tgt_field, src_info in field_map.items():
            try:
                if tgt_field in result.columns:
                    continue

                if isinstance(src_info, dict) and "value" in src_info:
                    result[tgt_field] = src_info["value"]
                elif isinstance(src_info, dict) and src_info.get("use_academic_year"):
                    result[tgt_field] = context.academic_start if tgt_field == "Start Date" else context.academic_end
                elif isinstance(src_info, dict) and src_info.get("append_year_to_id"):
                    col_name = src_info.get("column", "").lower()
                    result[tgt_field] = working.apply(
                        lambda row, c=col_name: self.generate_class_id(
                            row, mt_id_col=c, append_year=True, context=context
                        ),
                        axis=1,
                    )
                elif isinstance(src_info, dict):
                    column_name = src_info.get("column", "").lower()
                    transform_name = src_info.get("transform", "")
                    if column_name in working.columns:
                        series = working[column_name]
                        if transform_name:
                            if transform_name not in self.ALLOWED_TRANSFORMS:
                                raise ValueError(
                                    f"Unknown transform '{transform_name}' for field '{tgt_field}'. "
                                    f"Allowed: {sorted(self.ALLOWED_TRANSFORMS)}"
                                )
                            func = getattr(self, transform_name)
                            result[tgt_field] = series.apply(func)
                        else:
                            result[tgt_field] = series
                    else:
                        result[tgt_field] = pd.NA
                else:
                    col = str(src_info).lower()
                    if col in working.columns:
                        result[tgt_field] = working[col]
                    else:
                        result[tgt_field] = pd.NA

            except Exception as ex:
                logger.exception(f"Error transforming {entity}.{tgt_field}: {ex}")
                result[tgt_field] = pd.NA

        return result
