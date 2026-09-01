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
from src.etl.transformers.course_codes import strip_trailing_hyphens


class CourseInfoTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)

        patterns = self.effective_course_code_patterns(context.global_config)
        working = self.filter_excluded_course_code_patterns(working, patterns)

        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})
        result = self.apply_field_map(working, result, field_map, "CourseInfo", context)

        # MyEd BC pads codes to a fixed width with trailing hyphens (MAPPR12--- is
        # MAPPR12). Strip the padding BEFORE the dedup so the catalog carries clean
        # codes and a padded/unpadded pair of the same course collapses (2026-08-31;
        # StudentCourses strips the same way, so its exact-match lookups line up).
        if "Course Code" in result.columns:
            result["Course Code"] = result["Course Code"].map(strip_trailing_hyphens)

        dedup_keys = [k for k in ("Course Code", "School ID") if k in result.columns]
        if dedup_keys:
            result = result.drop_duplicates(subset=dedup_keys).reset_index(drop=True)

        return result
