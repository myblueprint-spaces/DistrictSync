"""Staff entity transformer — optional roster merge for staff sourceid."""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import STAFF_SOURCEID
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


class StaffTransformer(BaseTransformer):

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any],
                  context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})

        working = self._merge_roster(working, mapping, context)

        return self.apply_field_map(working, result, field_map, "Staff", context)

    def _merge_roster(self, working: pd.DataFrame, mapping: dict[str, Any],
                      context: TransformContext) -> pd.DataFrame:
        """Merge staff with roster to add 'staff sourceid' when available."""
        source_config = mapping.get("source_files", {})
        normalized = self.normalize_source_config(source_config)
        teacher_id_col = context.get_teacher_id_col()

        if len(normalized) <= 1:
            return working

        staff_filename = normalized.get("staff_info", "")
        roster_filename = list(normalized.values())[1] if len(normalized) > 1 else ""

        staff_df = context.raw_data.get(staff_filename, pd.DataFrame())
        roster_df = context.raw_data.get(roster_filename, pd.DataFrame())

        if (not staff_df.empty and not roster_df.empty
                and teacher_id_col in staff_df.columns
                and STAFF_SOURCEID in roster_df.columns):
            working = staff_df.merge(
                roster_df[[teacher_id_col, STAFF_SOURCEID]].drop_duplicates(subset=teacher_id_col),
                on=teacher_id_col,
                how="left",
            )
            working = self.normalize_columns(working)

        return working
