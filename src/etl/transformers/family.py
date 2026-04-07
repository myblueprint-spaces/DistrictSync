"""Family entity transformer — simple field mapping from emergency contacts."""

from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class FamilyTransformer(BaseTransformer):

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any],
                  context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})
        return self.apply_field_map(working, result, field_map, "Family", context)
