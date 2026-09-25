"""Shared state passed between entity transformers during a pipeline run.

Classes publishes a single frozen :class:`ClassArtifacts` bundle (homeroom
lookup + blended maps) that Enrollments consumes. This context object is the
clean way to share that cross-entity state.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from src.etl.column_names import STUDENT_NUMBER, TEACHER_ID
from src.etl.transformers.columns import Previously, resolve_source_column
from src.etl.transformers.grades import schedule_grade_column


@dataclass(frozen=True)
class ClassArtifacts:
    """The Classes → Enrollments handoff, published as ONE frozen bundle.

    ``ClassTransformer`` is the only writer: it publishes exactly the
    cross-entity facts ``EnrollmentTransformer`` consumes (homeroom lookup for
    homeroom enrollments, normalized ClassInformation for co-teacher rows,
    and the blended-class maps for blended/subject teacher rows), in one
    assignment to ``TransformContext.class_artifacts`` — making the previously
    implicit temporal coupling an explicit, assertable contract. Frozen =
    no rebinding after publish; the frames/dicts inside are NOT deep-copied,
    so consumers must treat them as read-only.
    """

    homeroom_classes_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    class_info_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    blended_class_map: dict[str, str] = field(default_factory=dict)
    blended_class_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    blended_teacher_map: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class TransformContext:
    """Mutable shared state for a single ETL pipeline run."""

    school_year: int = 0
    academic_start: str = ""
    academic_end: str = ""

    raw_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    global_config: dict[str, Any] = field(default_factory=dict)

    # The WHOLE config's per-entity `mappings` block, published once per run by
    # `run_transform`. Distinct from `global_config` above, which is only the
    # config's `global_config` SECTION — it has never carried `mappings`. Every
    # cross-entity config read goes through it: the accessors below (the teacher
    # id, the Students config, the schedule grade column) and Staff's timetable
    # files. Before plan 0053 S9 the Students accessors read the dead
    # `global_config["mappings"]` path, so a renamed demographic column was
    # silently ignored on the Classes/Enrollments homeroom path. Empty in a
    # directly-constructed context, which callers must treat as "no cross-entity
    # config available" rather than as an error.
    entity_mappings: dict[str, Any] = field(default_factory=dict)

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

    # Cross-entity state: published ONCE by ClassTransformer as a frozen
    # bundle, asserted + consumed by EnrollmentTransformer. None until Classes
    # runs — the read-only properties below give safe empty defaults for the
    # shared helpers (e.g. assign_class_ids) that predate the bundle.
    class_artifacts: Optional[ClassArtifacts] = None

    @property
    def homeroom_classes_df(self) -> pd.DataFrame:
        """Homeroom lookup from :attr:`class_artifacts` (empty until Classes runs)."""
        return self.class_artifacts.homeroom_classes_df if self.class_artifacts else pd.DataFrame()

    @property
    def class_info_df(self) -> pd.DataFrame:
        """Normalized ClassInformation from :attr:`class_artifacts` (empty until Classes runs)."""
        return self.class_artifacts.class_info_df if self.class_artifacts else pd.DataFrame()

    @property
    def blended_class_map(self) -> dict[str, str]:
        """Master Timetable ID → blended Class ID (empty until Classes runs)."""
        return self.class_artifacts.blended_class_map if self.class_artifacts else {}

    @property
    def blended_class_metadata(self) -> dict[str, dict[str, Any]]:
        """Blended Class ID → Name/Grade/School metadata (empty until Classes runs)."""
        return self.class_artifacts.blended_class_metadata if self.class_artifacts else {}

    @property
    def blended_teacher_map(self) -> dict[str, list[str]]:
        """Blended Class ID → teacher ids (empty until Classes runs)."""
        return self.class_artifacts.blended_teacher_map if self.class_artifacts else {}

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

    def _mappings(self) -> dict[str, Any]:
        """The config's per-entity mappings: :attr:`entity_mappings` FIRST.

        ``global_config["mappings"]`` is still consulted as a fallback for a
        context a test builds by hand; production never populates it
        (`run_transform` passes the config's ``global_config`` SECTION).
        """
        return self.entity_mappings or self.global_config.get("mappings", {})

    def get_teacher_id_col(self) -> str:
        """Teacher-ID column name, resolved from the Enrollments ``User ID`` config.

        Reads :attr:`entity_mappings` FIRST (repointed at 0052, when the
        resolution started gating whether staff are DROPPED rather than merely
        joined), through the one resolver (plan 0053 S9).
        """
        enrollment_map = self._mappings().get("Enrollments", {}).get("field_map", {})
        user_id_map = enrollment_map.get("User ID", {})
        if not isinstance(user_id_map, dict):
            return TEACHER_ID
        return resolve_source_column(
            user_id_map, "staff_id_col", default=TEACHER_ID, previously=Previously.AS_CONFIGURED
        )

    def get_students_config(self) -> dict[str, Any]:
        """The Students entity's mapping, read from :attr:`entity_mappings` first (plan 0053 S9).

        Mirrors :meth:`get_teacher_id_col`. Before S9 it read only
        ``global_config["mappings"]`` — never populated in a real run — so the
        Classes/Enrollments homeroom path always used the hardcoded Grade /
        Homeroom / student-number defaults, whatever the district mapped.
        """
        return self._mappings().get("Students", {})

    def get_demo_student_col(self) -> str:
        """Demographic student-ID column, resolved from the Students ``User ID`` config.

        The demographic file uses a different student-ID column than the
        schedule (MyEd BC: "Student Number" vs "Student ID"), so the
        schedule-targeted Enrollments ID config can't be reused. This is the
        same value space as ``active_student_ids``; used by Classes (homeroom)
        and Enrollments (homeroom) to filter to the active roster — so a
        renamed student-number column must reach it (plan 0053 S9).
        """
        students_field_map = self.get_students_config().get("field_map", {})
        return resolve_source_column(
            students_field_map, "User ID", default=STUDENT_NUMBER, previously=Previously.DEFAULT
        )

    def get_schedule_grade_col(self) -> str:
        """The schedule's grade column — the Classes mapping's ``Grade`` (plan 0053 S9).

        For Enrollments' subject split, which must keep exactly the rows
        Classes' split keeps; both go through
        :func:`~src.etl.transformers.grades.schedule_grade_column`.
        """
        classes_field_map = self._mappings().get("Classes", {}).get("field_map", {})
        return schedule_grade_column(classes_field_map)
