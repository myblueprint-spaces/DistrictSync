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
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})
        return self.apply_field_map(working, result, field_map, "Family", context)
