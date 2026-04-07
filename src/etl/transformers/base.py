"""Base transformer with shared utilities and the generic field-mapping loop.

All entity-specific transformers inherit from this.
DRY: column normalization, date resolution, ID cleaning, source file access,
and the generic field_map application are defined once here.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from src.etl.column_names import MASTER_TIMETABLE_ID
from src.etl.transformers.context import TransformContext
from src.utils.helpers import normalize_columns as _normalize_columns

logger = logging.getLogger(__name__)


class BaseTransformer(ABC):

    # -----------------------------------------------------------------------
    # Allowlist of YAML-callable transform functions (security: prevents
    # arbitrary method invocation via getattr on user-supplied config)
    # -----------------------------------------------------------------------
    ALLOWED_TRANSFORMS: frozenset[str] = frozenset({
        "grade_to_ceds", "map_role", "truncate_name",
    })

    # -----------------------------------------------------------------------
    # CEDS grade mapping (class-level constant)
    # -----------------------------------------------------------------------
    CEDS_MAPPING: dict[str, str] = {
        "INFANT/TODDLER": "IT", "PRESCHOOL": "PR", "PRE-K": "PK",
        "PREKINDERGARTEN": "PK", "TK": "TK", "TRANSITIONAL KINDERGARTEN": "TK",
        "KINDERGARTEN": "KG", "K": "KG", "01": "01", "1": "01", "02": "02", "2": "02",
        "03": "03", "3": "03", "04": "04", "4": "04", "05": "05", "5": "05", "06": "06", "6": "06",
        "07": "07", "7": "07", "08": "08", "8": "08", "09": "09", "9": "09", "10": "10", "11": "11",
        "12": "12", "13": "13", "POSTSECONDARY": "PS", "UGRADED": "UG", "UNGRADED": "UG",
        "UG": "UG", "OTHER": "Other", "EL": "KG", "KF": "KG",
    }

    # -----------------------------------------------------------------------
    # Abstract interface
    # -----------------------------------------------------------------------
    @abstractmethod
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any],
                  context: TransformContext) -> pd.DataFrame:
        ...

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

    @staticmethod
    def truncate_name(name: str, max_len: int = 100) -> str:
        """Gracefully truncate a string, breaking at word boundaries."""
        if len(name) <= max_len:
            return name
        trunc_len = max_len - 3
        last_space = name.rfind(' ', 0, trunc_len)
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
        return df[df[id_col].notna() & (clean != "") & (clean != "nan")]

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

    def resolve_date(self, field_map: dict[str, Any], field_name: str,
                     context: TransformContext) -> str:
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
    def generate_class_id(self, row: pd.Series, mt_id_col: str,
                          append_year: bool, context: TransformContext) -> str:
        mt_id = row.get(mt_id_col, "")
        if mt_id and append_year:
            return f"{mt_id}_{context.school_year}"
        return mt_id

    def assign_class_ids(self, df: pd.DataFrame, field_map: dict,
                         context: TransformContext) -> pd.DataFrame:
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
                lambda row: self.generate_class_id(
                    row, mt_id_col=mt_id_col, append_year=True, context=context
                ),
                axis=1,
            )
            df["Class ID"] = df["Class ID"].fillna(fallback)
        else:
            df["Class ID"] = df.apply(
                lambda row: self.generate_class_id(
                    row, mt_id_col=mt_id_col, append_year=True, context=context
                ),
                axis=1,
            )
        return df

    def generate_class_name(self, row: pd.Series, teacher_flag_col: str,
                            teacher_last_col: str, course_title_col: str,
                            section_letter_col: str, context: TransformContext) -> str:
        course_title = str(row.get(course_title_col, row.get("title", "Unknown Course"))).strip()
        teacher_last = ""

        if teacher_flag_col and teacher_flag_col in row:
            if str(row.get(teacher_flag_col, "")).strip().lower() == "y":
                teacher_last = str(row.get(teacher_last_col, "")).strip()
        else:
            teacher_last = str(row.get(teacher_last_col, "")).strip()

        if pd.isna(teacher_last) or teacher_last.lower() == 'nan':
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
        try:
            row_lower = {k.lower(): v for k, v in row.to_dict().items()}
            return format_str.format(**row_lower)
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

    def determine_school_year(self, all_data: dict[str, pd.DataFrame], source_config: Any) -> int:
        normalized = self.normalize_source_config(source_config)
        for _role, filename in normalized.items():
            df = all_data.get(filename)
            if df is not None and "school year" in df.columns:
                years = df["school year"].dropna().astype(str).str[:4].unique()
                if len(years) > 0:
                    try:
                        return int(years[0])
                    except ValueError:
                        pass
        now = datetime.now()
        return now.year if now.month >= 8 else now.year - 1

    # -----------------------------------------------------------------------
    # Generic field-map application (used by Students, Staff, Family)
    # -----------------------------------------------------------------------
    def apply_field_map(self, working: pd.DataFrame, result: pd.DataFrame,
                        field_map: dict[str, Any], entity: str,
                        context: TransformContext) -> pd.DataFrame:
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
                        lambda row, c=col_name: self.generate_class_id(row, mt_id_col=c, append_year=True, context=context),
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
