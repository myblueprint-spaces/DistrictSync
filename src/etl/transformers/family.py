"""Family entity transformer — simple field mapping from emergency contacts."""

from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class FamilyTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        # Config-driven row inclusion (e.g. SD60 keeps only guardian rows) —
        # applied BEFORE the field map so excluded contacts never reach output.
        working = self.apply_row_filters(working, mapping.get("row_filters", []), "Family")
        field_map = mapping.get("field_map", {})
        # Zero-orphan invariant: keep only contacts whose student is on the
        # active roster (Students.csv), so a withdrawn student's guardians never
        # ship. Students runs before Family (base mapping / enabled_entities
        # order); when the roster is unavailable (e.g. a tier without the
        # Students entity), filter_to_active warns and returns the frame
        # unchanged — the same convention as Enrollments.
        working = self.filter_to_active(working, self._student_number_col(field_map), context, caller="Family")
        result = pd.DataFrame()
        return self.apply_field_map(working, result, field_map, "Family", context)

    @staticmethod
    def _student_number_col(field_map: dict[str, Any]) -> str:
        """Source student-number column, resolved from the entity field_map.

        Configurable Columns rule: the ``Student User ID`` output maps from a
        district-configurable source column (default MyEd BC "Student Number").
        """
        config = field_map.get("Student User ID", "student number")
        if isinstance(config, dict):
            return str(config.get("column", "student number")).strip().lower()
        return str(config).strip().lower()
