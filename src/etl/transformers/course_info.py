"""CourseInfo entity transformer — rearrangement of the Course Information GDE.

Filters out course codes matching configured regex patterns (typically
kindergarten / early-grade / X-prefix / ATT-prefix rows that don't belong
in the SpacesEDU course catalog), then deduplicates on Course Code +
School ID so the same course offered at multiple schools each gets its
own row but accidental duplicates within a school collapse.
"""

from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class CourseInfoTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)

        patterns = context.global_config.get("excluded_course_code_patterns", [])
        working = self.filter_excluded_course_code_patterns(working, patterns)

        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})
        result = self.apply_field_map(working, result, field_map, "CourseInfo", context)

        dedup_keys = [k for k in ("Course Code", "School ID") if k in result.columns]
        if dedup_keys:
            result = result.drop_duplicates(subset=dedup_keys).reset_index(drop=True)

        return result
