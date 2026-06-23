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

    # Active roster: normalized `User ID` strings of the students retained by
    # StudentTransformer (its filtered output). Published by Students and read
    # by Classes (homeroom) + Enrollments (homeroom + subject) to guarantee no
    # output row references a student absent from Students.csv (zero-orphan
    # invariant). Empty until Students runs — consumers must guard for that.
    active_student_ids: set[str] = field(default_factory=set)

    # Per-run data-error ledger (separate axis from ETL success/failure). Each
    # entry records a non-fatal field-transform problem surfaced loudly rather
    # than silently swallowed: a per-row transform exception (that one cell is
    # blanked, the rest of the column survives) or a column-level error (unknown
    # transform / structural failure → that column blanked). Appended by
    # `BaseTransformer.apply_field_map`; surfaced by `run_pipeline` into the
    # run-log `data_errors` summary and Run History ("Completed with N data
    # errors"). The ETL `status` stays `success` — the run still completes +
    # delivers. Intended-blank (absent config column) is NOT an error and is
    # NOT recorded here. Entry shape:
    #   {"entity": str, "field": str, "failed_rows": int, "sample": str}
    data_errors: list[dict[str, Any]] = field(default_factory=list)

    # Cross-entity state: populated by ClassTransformer, consumed by EnrollmentTransformer
    homeroom_classes_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    class_info_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    blended_class_map: dict[str, str] = field(default_factory=dict)
    blended_class_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    blended_teacher_map: dict[str, list[str]] = field(default_factory=dict)

    def set_school_year(self, year: int, start_month_day: str, end_month_day: str) -> None:
        """Set school_year (MyEd BC end-year convention) and compute academic bounds.

        ``year`` is the calendar year the academic period ENDS in — matching
        MyEd BC's "School Year" column convention (where "2026" means the
        2025-2026 academic year). academic_start uses ``year - 1``;
        academic_end uses ``year``.

        Both month-day parameters are REQUIRED — there are no in-code defaults.
        Callers must source these from the validated YAML config (or pass them
        explicitly in tests) so non-BC SIS configs cannot silently fall back
        to BC values.
        """
        self.school_year = year
        self.academic_start = f"{year - 1}-{start_month_day}"
        self.academic_end = f"{year}-{end_month_day}"

    def get_teacher_id_col(self) -> str:
        """Extract teacher ID column name from Enrollments config. Used by multiple entities."""
        enrollment_map = self.global_config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})
        user_id_map = enrollment_map.get("User ID", {})
        return user_id_map.get("staff_id_col", "teacher id").lower()

    def get_students_config(self) -> dict[str, Any]:
        return self.global_config.get("mappings", {}).get("Students", {})

    def get_demo_student_col(self) -> str:
        """Demographic student-ID column, resolved from the Students ``User ID`` config.

        The demographic file uses a different student-ID column than the
        schedule (MyEd BC: "Student Number" vs "Student ID"), so the
        schedule-targeted Enrollments ID config can't be reused. This is the
        same value space as ``active_student_ids``; used by Classes (homeroom)
        and Enrollments (homeroom) to filter to the active roster.
        """
        user_id_config = self.get_students_config().get("field_map", {}).get("User ID", "student number")
        if isinstance(user_id_config, dict):
            return str(user_id_config.get("column", "student number")).lower()
        return str(user_id_config).lower()
