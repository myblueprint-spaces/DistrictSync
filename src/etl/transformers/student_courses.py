"""StudentCourses entity transformer — joins course history + selection + info.

Ports the SD62 PowerShell `GDEprocessingscript.ps1` logic. Two passes:

1. History pass: iterate StudentCourseHistory rows, build sch_lookup metadata
   per (student, cleaned_course_code), and emit one output row per kept history
   record (W marks and pattern-excluded codes are skipped).

2. Selection pass: iterate StudentCourseSelection rows, consult sch_lookup
   to decide whether each selection should be emitted (no history -> include,
   already passed or in-progress -> exclude, null-date fallback or newer
   retake start date -> include).

Course-code cleaning has two layers:
  - Section stripping: if `Full Course Code` ends with "-{Section}", strip it.
  - Flavor truncation: if the code contains any configured flavor substring
    (HUB / HOL / DL / "---"), truncate to first 7 chars.

CourseInfo lookups use a two-tier strategy: exact match on
(course_code, school_number) first, then the cleaned code's 7-char prefix
against a single-entry-per-prefix dictionary. Falls back to credits=4 only
when the row is a pass.
"""

import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from src.etl.column_names import SCHOOL_NUMBER
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext
from src.utils.helpers import describe_value_for_log as _describe_value

logger = logging.getLogger(__name__)


class StudentCoursesTransformer(BaseTransformer):
    DATE_FORMAT = "%d-%b-%Y"  # e.g., "15-Sep-2024"
    PREFIX_LEN = 7

    # Fallback output shape when a mapping carries no field_map. The real
    # output columns are derived from field_map.keys() at transform time so
    # the YAML config stays the single source of truth for column order.
    DEFAULT_OUTPUT_COLUMNS: list[str] = [
        "Student ID",
        "Course Code",
        "IntegrationId",
        "Course Name",
        "Completion Date",
        "Final Mark",
        "Credits Earned",
        "Alternate Course Code",
        "Potential Credits Earned",
        "Term Grade",
    ]

    # Configurable Columns rule: every source-column read resolves through the
    # district config with the MyEd BC literals as defaults (bundled configs
    # declare no overrides -> byte-identical output).
    #
    # Output-keyed reads resolve through the entity field_map (the family.py
    # pattern): logical role -> (field_map output key, MyEd BC default column).
    # One resolution per role is applied across all three source files
    # (history / selection / course-info), matching MyEd BC's shared GDE
    # column vocabulary.
    FIELD_MAP_SOURCE_DEFAULTS: dict[str, tuple[str, str]] = {
        "student_id": ("Student ID", "student number"),
        "course_code": ("Course Code", "course code"),
        "title": ("Course Name", "title"),
        "completion_date": ("Completion Date", "dl completion date"),
        "final_mark": ("Final Mark", "final mark"),
        "credit_value": ("Credits Earned", "credit value"),
    }
    # Auxiliary inputs with NO output counterpart resolve through the entity's
    # optional `source_columns` block (see EntityConfig.source_columns).
    # School Number is intentionally NOT configurable here: it is the shared
    # structural join key from src/etl/column_names.py.
    AUX_SOURCE_DEFAULTS: dict[str, str] = {
        "full_course_code": "full course code",
        "section": "section",
        "dl_start_date": "dl start date",
    }

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        source_files = mapping.get("source_files", {})
        field_map = mapping.get("field_map", {})
        output_columns = list(field_map.keys()) if field_map else list(self.DEFAULT_OUTPUT_COLUMNS)
        cols = self._resolve_source_columns(mapping)

        history_df = self._load(context, source_files, "course_history")
        selection_df = self._load(context, source_files, "course_selection")
        info_df = self._load(context, source_files, "course_info")

        if history_df.empty and selection_df.empty:
            return pd.DataFrame(columns=output_columns)

        patterns = self.effective_course_code_patterns(context.global_config)
        flavors = context.global_config.get("excluded_course_flavors", [])

        info_exact, info_prefix = self._build_info_lookups(info_df, cols)

        rows: list[dict[str, Any]] = []
        sch_lookup: dict[tuple[str, str], dict[str, Any]] = {}

        self._process_history(history_df, patterns, flavors, info_exact, info_prefix, sch_lookup, rows, context, cols)
        self._process_selection(selection_df, patterns, flavors, info_exact, info_prefix, sch_lookup, rows, cols)

        result = pd.DataFrame(rows, columns=output_columns)
        # Zero-orphan invariant: emit transcripts only for students on the
        # active roster (Students.csv). When the roster is unavailable (e.g.
        # the mbponly tier runs without the Students entity), filter_to_active
        # warns and returns the frame unchanged — same convention as Enrollments.
        result = self.filter_to_active(result, "Student ID", context, caller="StudentCourses")
        if not result.empty:
            # Match PowerShell's lexical sort (Completion Date is a string here).
            # Output columns follow field_map.keys(), so sort only on the sort
            # keys a config actually kept.
            sort_keys = [col for col in ("Student ID", "Completion Date") if col in result.columns]
            if sort_keys:
                result = result.sort_values(sort_keys, kind="stable").reset_index(drop=True)
        return result

    # ------------------------------------------------------------------
    # Source-column resolution (Configurable Columns)
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_source_columns(cls, mapping: dict[str, Any]) -> dict[str, str]:
        """Resolve every configurable source-column read for this entity.

        Output-keyed columns resolve through the entity ``field_map`` — a plain
        string value or a ``{column: ...}`` dict overrides the MyEd BC default
        (the family.py pattern; the base config's ``{value: ""}`` placeholders
        keep the defaults). Auxiliary inputs with no output counterpart resolve
        through the entity-level ``source_columns`` block. All resolved names
        are lower-cased to match ``normalize_columns`` output.
        """
        field_map = mapping.get("field_map", {})
        resolved: dict[str, str] = {}
        for role, (fm_key, default) in cls.FIELD_MAP_SOURCE_DEFAULTS.items():
            resolved[role] = cls._field_map_source(field_map, fm_key, default)
        aux = mapping.get("source_columns") or {}
        for role, default in cls.AUX_SOURCE_DEFAULTS.items():
            resolved[role] = str(aux.get(role) or default).strip().lower() or default
        return resolved

    @staticmethod
    def _field_map_source(field_map: dict[str, Any], key: str, default: str) -> str:
        config = field_map.get(key, default)
        if isinstance(config, dict):
            return str(config.get("column") or default).strip().lower() or default
        if isinstance(config, str) and config.strip():
            return config.strip().lower()
        return default

    # ------------------------------------------------------------------
    # Source loading
    # ------------------------------------------------------------------
    def _load(self, context: TransformContext, source_files: Any, role: str) -> pd.DataFrame:
        df = self.get_source_file(context, source_files, role)
        if df.empty:
            return df
        return self.normalize_columns(df)

    # ------------------------------------------------------------------
    # CourseInfo lookup tables
    # ------------------------------------------------------------------
    def _build_info_lookups(
        self, info_df: pd.DataFrame, cols: dict[str, str]
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
        exact: dict[tuple[str, str], dict[str, Any]] = {}
        prefix: dict[str, dict[str, Any]] = {}
        if info_df.empty:
            return exact, prefix
        for record in info_df.to_dict("records"):
            code = self._str(record.get(cols["course_code"]))
            if not code:
                continue
            school = self._str(record.get(SCHOOL_NUMBER))
            entry = {"title": self._str(record.get(cols["title"])), "credit_value": record.get(cols["credit_value"])}
            exact[(code, school)] = entry
            pre = code[: self.PREFIX_LEN]
            if pre not in prefix:
                prefix[pre] = entry
        return exact, prefix

    # ------------------------------------------------------------------
    # History pass
    # ------------------------------------------------------------------
    def _process_history(
        self,
        history_df: pd.DataFrame,
        patterns: list[str],
        flavors: list[str],
        info_exact: dict[tuple[str, str], dict[str, Any]],
        info_prefix: dict[str, dict[str, Any]],
        sch_lookup: dict[tuple[str, str], dict[str, Any]],
        rows: list[dict[str, Any]],
        context: TransformContext,
        cols: dict[str, str],
    ) -> None:
        if history_df.empty:
            return
        filtered = self.filter_excluded_course_code_patterns(history_df, patterns, column=cols["course_code"])

        non_numeric_marks = 0
        first_sample = ""
        for record in filtered.to_dict("records"):
            mark_str = self._str(record.get(cols["final_mark"]))
            if mark_str.upper() == "W":
                continue

            course_code = self._str(record.get(cols["course_code"]))
            student_id = self._str(record.get(cols["student_id"]))
            if not student_id or not course_code:
                continue

            school_number = self._str(record.get(SCHOOL_NUMBER))
            full_code = self._str(record.get(cols["full_course_code"]))
            section = self._str(record.get(cols["section"]))
            raw_completion = self._str(record.get(cols["completion_date"]))
            raw_start = self._str(record.get(cols["dl_start_date"]))
            iso_completion = self.normalize_iso_date(raw_completion)

            cleaned = self._derive_history_code(course_code, full_code, section, flavors)
            is_pass = self._parse_mark_passing(mark_str)
            # A non-blank, non-"W", non-numeric mark (letter grades, "Pass") is
            # scored as not-passing for legacy-PowerShell parity — record it as
            # a data error so an alpha-marks district sees "Completed with N
            # data errors" instead of silently nulled credits.
            if mark_str and self._parse_mark_numeric(mark_str) is None:
                non_numeric_marks += 1
                if not first_sample:
                    # A final mark is an education record — same log-safety seam
                    # as the base transformer: shape, never the mark itself.
                    first_sample = f"{_describe_value(mark_str)}: non-numeric mark scored as not-passing"
            start_date = self._parse_date(raw_start)
            is_in_progress = not raw_completion

            self._update_sch_lookup(sch_lookup, student_id, cleaned, is_pass, start_date, is_in_progress)

            title, credits, potential = self._lookup_credits(cleaned, school_number, is_pass, info_exact, info_prefix)

            rows.append(
                {
                    "Student ID": student_id,
                    "Course Code": cleaned,
                    "IntegrationId": "",
                    "Course Name": title,
                    "Completion Date": iso_completion,
                    "Final Mark": mark_str,
                    "Credits Earned": credits,
                    "Alternate Course Code": "",
                    "Potential Credits Earned": potential,
                    "Term Grade": "",
                }
            )

        if non_numeric_marks:
            logger.error(
                f"[StudentCourses] {non_numeric_marks} history row(s) carry a non-numeric Final Mark "
                f"(scored as not-passing; credits not earned) — sample {first_sample}"
            )
            self._record_data_error(
                context, "StudentCourses", "Final Mark", failed_rows=non_numeric_marks, sample=first_sample
            )

    @staticmethod
    def _derive_history_code(course_code: str, full_code: str, section: str, flavors: list[str]) -> str:
        """Strip trailing -section from full_code, fall back to course_code, then truncate flavors."""
        if full_code and section and full_code.endswith("-" + section):
            base = full_code[: -(len(section) + 1)]
        elif not full_code:
            base = course_code
        else:
            base = full_code
        return BaseTransformer.clean_course_code_flavor(base, flavors)

    @staticmethod
    def _update_sch_lookup(
        sch_lookup: dict[tuple[str, str], dict[str, Any]],
        student_id: str,
        cleaned: str,
        is_pass: bool,
        start_date: Optional[datetime],
        is_in_progress: bool,
    ) -> None:
        key = (student_id, cleaned)
        meta = sch_lookup.get(key)
        if meta is None:
            sch_lookup[key] = {
                "has_passed": is_pass,
                "latest_start_date": start_date,
                "has_null_start_date": start_date is None,
                "is_in_progress": is_in_progress,
            }
            return
        if is_pass:
            meta["has_passed"] = True
        if is_in_progress:
            meta["is_in_progress"] = True
        if start_date is None:
            meta["has_null_start_date"] = True
        elif meta["latest_start_date"] is None or start_date > meta["latest_start_date"]:
            meta["latest_start_date"] = start_date

    # ------------------------------------------------------------------
    # Selection pass
    # ------------------------------------------------------------------
    def _process_selection(
        self,
        selection_df: pd.DataFrame,
        patterns: list[str],
        flavors: list[str],
        info_exact: dict[tuple[str, str], dict[str, Any]],
        info_prefix: dict[str, dict[str, Any]],
        sch_lookup: dict[tuple[str, str], dict[str, Any]],
        rows: list[dict[str, Any]],
        cols: dict[str, str],
    ) -> None:
        if selection_df.empty:
            return
        filtered = self.filter_excluded_course_code_patterns(selection_df, patterns, column=cols["course_code"])

        for record in filtered.to_dict("records"):
            course_code = self._str(record.get(cols["course_code"]))
            student_id = self._str(record.get(cols["student_id"]))
            if not student_id or not course_code:
                continue

            school_number = self._str(record.get(SCHOOL_NUMBER))
            raw_start = self._str(record.get(cols["dl_start_date"]))

            cleaned = self.clean_course_code_flavor(course_code, flavors)
            sel_start = self._parse_date(raw_start)

            if not self._should_include_selection(sch_lookup, student_id, cleaned, sel_start):
                continue

            # Title lookup uses raw code (matches PowerShell selection-pass behavior).
            title_entry = info_exact.get((course_code, school_number))
            title = title_entry["title"] if title_entry else ""

            # Potential credits use the full lookup chain (exact/prefix/fallback) on cleaned code.
            _, _, potential = self._lookup_credits(
                cleaned, school_number, is_pass=False, info_exact=info_exact, info_prefix=info_prefix
            )

            rows.append(
                {
                    "Student ID": student_id,
                    "Course Code": cleaned,
                    "IntegrationId": "",
                    "Course Name": title,
                    "Completion Date": "",
                    "Final Mark": "",
                    "Credits Earned": "",
                    "Alternate Course Code": "",
                    "Potential Credits Earned": potential,
                    "Term Grade": "",
                }
            )

    @staticmethod
    def _should_include_selection(
        sch_lookup: dict[tuple[str, str], dict[str, Any]],
        student_id: str,
        cleaned: str,
        sel_start: Optional[datetime],
    ) -> bool:
        meta = sch_lookup.get((student_id, cleaned))
        if meta is None:
            return True
        if meta["has_passed"] or meta["is_in_progress"]:
            return False
        if meta["has_null_start_date"] or sel_start is None:
            return True
        latest = meta["latest_start_date"]
        return latest is not None and sel_start > latest

    # ------------------------------------------------------------------
    # CourseInfo credit lookup (shared by history + selection)
    # ------------------------------------------------------------------
    def _lookup_credits(
        self,
        cleaned: str,
        school_number: str,
        is_pass: bool,
        info_exact: dict[tuple[str, str], dict[str, Any]],
        info_prefix: dict[str, dict[str, Any]],
    ) -> tuple[str, Any, Any]:
        """Return (title, credits_earned, potential_credits_earned).

        credits_earned is None when not a pass (matches PowerShell `$null`);
        potential_credits_earned ignores pass/fail. Both fall back to 4
        when neither an exact nor a 7-char prefix match exists.
        """
        entry = info_exact.get((cleaned, school_number))
        if entry is None:
            entry = info_prefix.get(cleaned[: self.PREFIX_LEN])

        if entry is not None:
            title = entry["title"]
            value = entry["credit_value"]
            credits = value if is_pass else None
            potential = value
        else:
            title = ""
            credits = 4 if is_pass else None
            potential = 4
        return title, credits, potential

    # ------------------------------------------------------------------
    # Small parsing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _str(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return str(value).strip()

    @staticmethod
    def _parse_mark_numeric(mark_str: str) -> Optional[float]:
        """Numeric value of a mark, or None when it does not parse as a number."""
        try:
            return float(mark_str)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse_mark_passing(cls, mark_str: str) -> bool:
        """Passing = numeric mark >= 50. Non-numeric marks (letter grades,
        "Pass") score as not-passing — legacy-PowerShell parity; the history
        pass records those as data errors rather than changing the scoring.
        """
        value = cls._parse_mark_numeric(mark_str)
        return value is not None and value >= 50

    @classmethod
    def _parse_date(cls, raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, cls.DATE_FORMAT)
        except (ValueError, TypeError):
            return None
