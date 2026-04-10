"""Shared state passed between entity transformers during a pipeline run.

Classes populates homeroom_classes_df and blended_* maps that Enrollments reads.
This context object is the clean way to share that cross-entity state.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class TransformContext:
    """Mutable shared state for a single ETL pipeline run."""

    school_year: int = 0
    academic_start: str = ""
    academic_end: str = ""

    raw_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    global_config: dict[str, Any] = field(default_factory=dict)

    # Cross-entity state: populated by ClassTransformer, consumed by EnrollmentTransformer
    homeroom_classes_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    class_info_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    blended_class_map: dict[str, str] = field(default_factory=dict)
    blended_class_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    blended_teacher_map: dict[str, list[str]] = field(default_factory=dict)

    def set_school_year(self, year: int, start_month_day: str = "08-25", end_month_day: str = "07-25") -> None:
        self.school_year = year
        self.academic_start = f"{year}-{start_month_day}"
        self.academic_end = f"{year + 1}-{end_month_day}"

    def get_teacher_id_col(self) -> str:
        """Extract teacher ID column name from Enrollments config. Used by multiple entities."""
        enrollment_map = self.global_config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})
        user_id_map = enrollment_map.get("User ID", {})
        return user_id_map.get("staff_id_col", "teacher id").lower()

    def get_students_config(self) -> dict[str, Any]:
        return self.global_config.get("mappings", {}).get("Students", {})
