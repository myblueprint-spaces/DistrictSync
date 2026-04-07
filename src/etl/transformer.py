"""Backward-compatible facade over the refactored transformers package.

All business logic now lives in src.etl.transformers.*.
This module preserves the DataTransformer API so existing tests and imports
continue to work without changes.
"""

from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import BlendedClassDetector
from src.etl.transformers.context import TransformContext
from src.etl.transformers.registry import get_transformer


class DataTransformer:
    """Facade that delegates to entity-specific transformers."""

    CEDS_MAPPING = BaseTransformer.CEDS_MAPPING

    def __init__(self):
        self._context = TransformContext()
        self._blended_detector = BlendedClassDetector()

    # --- Context state properties (tests access these directly) ---

    @property
    def school_year(self) -> int:
        return self._context.school_year

    @school_year.setter
    def school_year(self, value: int):
        self._context.school_year = value

    @property
    def academic_start(self) -> str:
        return self._context.academic_start

    @academic_start.setter
    def academic_start(self, value: str):
        self._context.academic_start = value

    @property
    def academic_end(self) -> str:
        return self._context.academic_end

    @academic_end.setter
    def academic_end(self, value: str):
        self._context.academic_end = value

    @property
    def homeroom_classes_df(self) -> pd.DataFrame:
        return self._context.homeroom_classes_df

    @homeroom_classes_df.setter
    def homeroom_classes_df(self, value):
        self._context.homeroom_classes_df = value

    @property
    def blended_class_map(self) -> dict[str, str]:
        return self._context.blended_class_map

    @blended_class_map.setter
    def blended_class_map(self, value):
        self._context.blended_class_map = value

    @property
    def blended_class_metadata(self) -> dict[str, dict[str, Any]]:
        return self._context.blended_class_metadata

    @blended_class_metadata.setter
    def blended_class_metadata(self, value):
        self._context.blended_class_metadata = value

    @property
    def blended_teacher_map(self) -> dict[str, list[str]]:
        return self._context.blended_teacher_map

    @blended_teacher_map.setter
    def blended_teacher_map(self, value):
        self._context.blended_teacher_map = value

    # --- Core methods ---

    def set_school_year(self, year: int, start_month_day: str = "08-25", end_month_day: str = "07-25") -> None:
        self._context.set_school_year(year, start_month_day, end_month_day)

    def determine_school_year(self, all_data: dict[str, pd.DataFrame], source_config: Any) -> int:
        return self._blended_detector.determine_school_year(all_data, source_config)

    def transform(
        self,
        df: pd.DataFrame,
        mapping: dict[str, Any],
        entity: str,
        raw_data: dict[str, pd.DataFrame],
        global_config: dict[str, Any],
    ) -> pd.DataFrame:
        self._context.raw_data = raw_data
        self._context.global_config = global_config
        transformer = get_transformer(entity)
        return transformer.transform(df, mapping, self._context)

    # --- Static utility delegates ---

    @staticmethod
    def grade_to_ceds(grade_value: Any) -> str:
        return BaseTransformer.grade_to_ceds(grade_value)

    @staticmethod
    def map_role(teaching_flag: Any) -> str:
        return BaseTransformer.map_role(teaching_flag)

    @staticmethod
    def _truncate_name(name: str, max_len: int = 100) -> str:
        return BaseTransformer.truncate_name(name, max_len)

    @staticmethod
    def normalize_source_config(source_config: Any) -> dict[str, str]:
        return BaseTransformer.normalize_source_config(source_config)

    def get_source_file(self, raw_data: dict[str, pd.DataFrame], source_config: Any, role: str) -> pd.DataFrame:
        # Temporarily set raw_data on context for the base method
        old = self._context.raw_data
        self._context.raw_data = raw_data
        result = self._blended_detector.get_source_file(self._context, source_config, role)
        self._context.raw_data = old
        return result

    # --- Instance utility delegates ---

    def generate_class_id(self, row: pd.Series, mt_id_col: str, append_year: bool = False) -> str:
        mt_id = row.get(mt_id_col, "")
        if mt_id and append_year:
            return f"{mt_id}_{self._context.school_year}"
        return mt_id

    def generate_class_name(
        self,
        row: pd.Series,
        teacher_flag_col: str,
        teacher_last_col: str,
        course_title_col: str,
        section_letter_col: str,
    ) -> str:
        return self._blended_detector.generate_class_name(
            row, teacher_flag_col, teacher_last_col, course_title_col, section_letter_col, self._context
        )

    def generate_user_role(self, row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        return BaseTransformer.generate_user_role(row, staff_id_col, student_id_col)

    def generate_user_id(self, row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        return BaseTransformer.generate_user_id(row, staff_id_col, student_id_col)

    def generate_student_email(self, row: pd.Series, format_str: str) -> str:
        return BaseTransformer.generate_student_email(row, format_str)

    # --- Blended class delegates ---

    def _validate_blended_class(self, session_group: pd.DataFrame, mtid_to_grade_map: dict[str, str]) -> bool:
        return self._blended_detector.validate(session_group, mtid_to_grade_map)

    def _get_blended_grade_range(self, session_group: pd.DataFrame, mtid_to_grade_map: dict[str, str]) -> str:
        return self._blended_detector.get_grade_range(session_group, mtid_to_grade_map)

    def _create_blended_class_name(
        self,
        session_group: pd.DataFrame,
        field_map: dict[str, Any],
        grade_str: str,
        course_code_to_title_map: dict[str, str],
    ) -> str:
        return self._blended_detector.create_name(
            session_group, field_map, grade_str, course_code_to_title_map, self._context
        )

    def _detect_blended_classes(
        self,
        class_info_df: pd.DataFrame,
        mapping: dict[str, Any],
        raw_data: dict[str, pd.DataFrame],
        global_config: dict[str, Any],
    ) -> None:
        self._context.raw_data = raw_data
        self._context.global_config = global_config
        self._blended_detector.detect(class_info_df, mapping, self._context)
