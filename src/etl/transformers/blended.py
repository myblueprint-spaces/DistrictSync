"""Blended class detection service.

Identifies when a teacher teaches multiple course sections at the same time slot
with 2+ grade levels, and consolidates them into a single blended class.

``BlendedClassDetector`` is a plain SERVICE class, not a transformer: it never
participates in the entity registry and produces no output frame — it RETURNS
the blended maps (:class:`BlendedDetection`) for ``ClassTransformer`` to
publish via ``ClassArtifacts``, never mutating shared context itself. (It
previously subclassed ``BaseTransformer`` solely to reach shared helpers, with
a ``transform`` that raised ``NotImplementedError`` — an LSP violation; the
helpers it needs are now imported from the focused helper modules.)
"""

import logging
from typing import Any, NamedTuple, Optional

import pandas as pd

from src.etl.column_names import (
    COURSE_CODE,
    COURSE_TITLE,
    MASTER_TIMETABLE_ID,
    SCHOOL_NUMBER,
)
from src.etl.transformers.context import TransformContext
from src.etl.transformers.course_codes import filter_excluded_course_codes
from src.etl.transformers.grades import grade_to_ceds
from src.etl.transformers.ids import normalize_id_series
from src.etl.transformers.naming import truncate_name
from src.etl.transformers.sources import get_source_file, normalize_source_config
from src.utils.helpers import normalize_columns

logger = logging.getLogger(__name__)


class BlendedDetection(NamedTuple):
    """Blended-class maps produced by :meth:`BlendedClassDetector.detect`.

    ``ClassTransformer`` publishes these (via ``ClassArtifacts``) for its own
    subject/missing-blended steps and for ``EnrollmentTransformer``.
    """

    class_map: dict[str, str]
    metadata: dict[str, dict[str, Any]]
    teacher_map: dict[str, list[str]]

    @staticmethod
    def empty() -> "BlendedDetection":
        """A fresh no-blends result (new dicts each call — never a shared mutable)."""
        return BlendedDetection({}, {}, {})


class BlendedClassDetector:
    """Detects blended classes and returns the blended mappings."""

    def detect(
        self, class_info_df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext
    ) -> BlendedDetection:
        """Run blended class detection and return the resulting maps.

        Named steps (each fail-safe with its own log message, preserving the
        original early-exit behavior — an early exit returns an EMPTY result):
        load the schedule/course reference frames, resolve the working frame
        (ClassInformation or the deduplicated schedule fallback), drop
        teacherless sections, build session keys, then collect every valid
        blend into the returned :class:`BlendedDetection`.
        """
        if class_info_df.empty:
            logger.info("No class info data available for blended class detection")
            return BlendedDetection.empty()

        field_map = mapping.get("field_map", {})
        teacher_id_col = context.get_teacher_id_col()

        loaded = self._load_reference_frames(mapping, context)
        if loaded is None:
            return BlendedDetection.empty()
        schedule_df, course_df = loaded

        mtid_to_grade = self._build_grade_map(schedule_df)
        course_title_map = self._build_course_title_map(course_df)

        working = self._resolve_working_frame(class_info_df, schedule_df, teacher_id_col)
        if working is None:
            return BlendedDetection.empty()

        working = self._drop_teacherless_sections(working, teacher_id_col)
        if working is None:
            return BlendedDetection.empty()

        working = self._add_session_key(working, teacher_id_col)
        return self._register_blends(working, field_map, teacher_id_col, mtid_to_grade, course_title_map, context)

    # ------------------------------------------------------------------
    # detect() steps
    # ------------------------------------------------------------------
    def _load_reference_frames(
        self, mapping: dict[str, Any], context: TransformContext
    ) -> Optional[tuple[pd.DataFrame, pd.DataFrame]]:
        """Load + normalize the schedule and course-info frames (None → cannot detect).

        The schedule is filtered by ``excluded_course_codes`` and its Master
        Timetable ID normalized, exactly as the downstream Classes path does,
        so grade lookups and the fallback frame share the same value space.
        """
        normalized_sources = normalize_source_config(mapping.get("source_files", {}))
        schedule_df = get_source_file(context, normalized_sources, "student_schedule")
        course_df = get_source_file(context, normalized_sources, "course_info")

        if schedule_df.empty or course_df.empty:
            logger.warning("Student schedule or course info data is missing. Cannot detect blended classes.")
            return None

        schedule_df = normalize_columns(schedule_df)
        course_df = normalize_columns(course_df)

        excluded_codes = context.global_config.get("excluded_course_codes", [])
        schedule_df = filter_excluded_course_codes(schedule_df, excluded_codes)

        if MASTER_TIMETABLE_ID in schedule_df.columns:
            schedule_df[MASTER_TIMETABLE_ID] = normalize_id_series(schedule_df[MASTER_TIMETABLE_ID])

        return schedule_df, course_df

    @staticmethod
    def _resolve_working_frame(
        class_info_df: pd.DataFrame, schedule_df: pd.DataFrame, teacher_id_col: str
    ) -> Optional[pd.DataFrame]:
        """Pick the frame sessions are grouped over (a COPY; None → cannot detect).

        ClassInformation when it carries the required columns; otherwise fall
        back to the schedule deduplicated to one row per section (Master
        Timetable ID), which is equivalent to ClassInformation's
        one-row-per-section structure (e.g. non-enhanced exports).
        """
        required = [teacher_id_col, MASTER_TIMETABLE_ID]
        if any(col not in class_info_df.columns for col in required):
            if all(col in schedule_df.columns for col in required):
                logger.info(
                    "class_info missing required columns; falling back to student schedule for blended detection"
                )
                class_info_df = schedule_df.drop_duplicates(subset=[MASTER_TIMETABLE_ID])
            else:
                logger.warning(f"Cannot detect blended classes. Missing required columns: {required}")
                return None
        return class_info_df.copy()

    @staticmethod
    def _drop_teacherless_sections(working: pd.DataFrame, teacher_id_col: str) -> Optional[pd.DataFrame]:
        """Drop rows with a blank/nan teacher id (None → nothing left to detect).

        A blended class is defined as multiple sections taught by the SAME
        teacher at the same time, so a section with no primary teacher can't
        participate. Without this guard, all teacherless sections at a school
        collapse into a single fake session_key (all blank components) and get
        "blended" together, producing empty-userId enrollment rows and
        nonsense class groupings.
        """
        if teacher_id_col in working.columns:
            teacher_series = normalize_id_series(working[teacher_id_col]).str.lower()
            working = working[(teacher_series != "") & (teacher_series != "nan")]
            if working.empty:
                logger.info("[Blended Classes] No rows with a teacher id; skipping detection")
                return None
        return working

    @staticmethod
    def _add_session_key(working: pd.DataFrame, teacher_id_col: str) -> pd.DataFrame:
        """Join the available session components into a ``session_key`` column.

        Sections sharing a session_key (school + teacher + time slot) are
        candidates for blending. Only components present in the frame
        participate; they are stringified with NaN → "" first.
        """
        session_components = [SCHOOL_NUMBER, teacher_id_col, "term", "semester", "day", "period"]
        available = [col for col in session_components if col in working.columns]

        for col in available:
            working[col] = working[col].fillna("").astype(str)
        working["session_key"] = working[available].agg("_".join, axis=1)
        return working

    def _register_blends(
        self,
        working: pd.DataFrame,
        field_map: dict[str, Any],
        teacher_id_col: str,
        mtid_to_grade: dict[str, str],
        course_title_map: dict[str, str],
        context: TransformContext,
    ) -> BlendedDetection:
        """Validate each multi-section session and collect it into the returned maps."""
        teacher_positions = self._teacher_positions(working, teacher_id_col)
        result = BlendedDetection.empty()

        count = 0
        for session_key, group in working.groupby("session_key"):
            if len(group) <= 1:
                continue

            if not self.validate(group, mtid_to_grade):
                continue

            blended_id = f"BLENDED_{session_key}_{context.school_year}"
            all_mt_ids = sorted(set(group[MASTER_TIMETABLE_ID].tolist()))

            for mt_id in all_mt_ids:
                result.class_map[mt_id] = blended_id

            result.teacher_map[blended_id] = self._collect_teachers(teacher_positions, all_mt_ids)

            grade_str = self.get_grade_range(group, mtid_to_grade)
            class_name = self.create_name(group, field_map, grade_str, course_title_map, context)

            result.metadata[blended_id] = {
                "Name": class_name,
                "Grade": grade_str,
                "School ID": group[SCHOOL_NUMBER].iloc[0] if SCHOOL_NUMBER in group.columns else "",
                "Original_MT_IDs": all_mt_ids,
            }
            count += 1

        logger.info(f"[Blended Classes] Detection completed: {count} blended classes identified")
        return result

    @staticmethod
    def _teacher_positions(working: pd.DataFrame, teacher_id_col: str) -> dict[Any, list[tuple[int, Any]]]:
        """ONE grouping pass: Master Timetable ID → [(row_position, teacher_id), ...].

        Replaces the legacy per-blend ``isin`` scan of the whole frame (O(rows)
        per blended session) with a single precomputed index. Row positions are
        kept so :meth:`_collect_teachers` can reproduce the frame-order
        first-appearance semantics of the original
        ``working[working[MT].isin(mt_ids)][teacher].unique()`` exactly.
        """
        frame = working.reset_index(drop=True)
        grouped = frame.groupby(MASTER_TIMETABLE_ID, sort=False)[teacher_id_col]
        return {mt_id: list(zip(series.index, series)) for mt_id, series in grouped}

    @staticmethod
    def _collect_teachers(teacher_positions: dict[Any, list[tuple[int, Any]]], mt_ids: list) -> list:
        """Teachers of every row whose MT ID is in ``mt_ids``, deduped in frame order."""
        pairs: list[tuple[int, Any]] = []
        for mt_id in mt_ids:
            pairs.extend(teacher_positions.get(mt_id, []))
        pairs.sort(key=lambda p: p[0])
        seen: set = set()
        teachers: list = []
        for _pos, teacher in pairs:
            if teacher not in seen:
                seen.add(teacher)
                teachers.append(teacher)
        return teachers

    # ------------------------------------------------------------------
    # Blend qualification + naming
    # ------------------------------------------------------------------
    def validate(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> bool:
        """A valid blend requires 2+ unique sections with 2+ distinct CEDS grades."""
        unique_mt_ids = session_group[MASTER_TIMETABLE_ID].unique()
        if len(unique_mt_ids) <= 1:
            return False
        grades = set()
        for mt_id in unique_mt_ids:
            grade = mtid_to_grade.get(mt_id)
            if grade:
                grades.add(grade_to_ceds(grade))
        return len(grades) >= 2

    def get_grade_range(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> str:
        grades = set()
        for mt_id in session_group[MASTER_TIMETABLE_ID].unique():
            grade = mtid_to_grade.get(mt_id)
            if grade:
                grades.add(grade_to_ceds(grade))
        if not grades:
            return ""
        try:
            return "/".join(sorted(grades, key=int))
        except ValueError:
            return "/".join(sorted(grades))

    def create_name(
        self,
        session_group: pd.DataFrame,
        field_map: dict[str, Any],
        grade_str: str,
        course_title_map: dict[str, str],
        context: TransformContext,
    ) -> str:
        name_parts = []

        name_config = field_map.get("Name", {})
        if isinstance(name_config, dict):
            # Spaced YAML authoring key (see ClassTransformer._assign_class_names).
            teacher_col = name_config.get("teacher last name", "teacher name").lower()
            if teacher_col in session_group.columns:
                teacher_name = session_group[teacher_col].iloc[0]
                if pd.notna(teacher_name) and str(teacher_name).strip():
                    name_parts.append(str(teacher_name).strip())

        unique_titles = sorted({course_title_map.get(code, "Unknown Course") for code in session_group[COURSE_CODE]})
        if unique_titles:
            name_parts.append(" / ".join(unique_titles))
        if grade_str:
            name_parts.append(f"({grade_str})")
        name_parts.append(str(context.school_year))

        full_name = " ".join(name_parts).strip()
        if not full_name or len(name_parts) <= 1:
            full_name = f"Blended Class {grade_str} {context.school_year}".strip()

        return truncate_name(full_name)

    # ------------------------------------------------------------------
    # Reference lookup tables
    # ------------------------------------------------------------------
    @staticmethod
    def _build_grade_map(schedule_df: pd.DataFrame) -> dict[str, str]:
        """Map each Master Timetable ID to its most common grade.

        Uses mode (most frequent grade) to handle cases where the same
        section has students from multiple grades in the schedule data.
        """
        if MASTER_TIMETABLE_ID in schedule_df.columns and "grade" in schedule_df.columns:
            pairs = schedule_df[[MASTER_TIMETABLE_ID, "grade"]].dropna()
            # Use most frequent grade per MT ID (mode) to handle multi-grade enrollment
            mode = pairs.groupby(MASTER_TIMETABLE_ID)["grade"].agg(lambda x: x.mode().iloc[0])
            return mode.to_dict()  # type: ignore[return-value]
        logger.warning(f"Missing '{MASTER_TIMETABLE_ID}' or 'grade' in student schedule.")
        return {}

    @staticmethod
    def _build_course_title_map(course_df: pd.DataFrame) -> dict[str, str]:
        if COURSE_CODE in course_df.columns and COURSE_TITLE in course_df.columns:
            pairs = course_df[[COURSE_CODE, COURSE_TITLE]].dropna().drop_duplicates(subset=[COURSE_CODE])  # type: ignore[call-overload]
            return pd.Series(pairs[COURSE_TITLE].values, index=pairs[COURSE_CODE]).to_dict()  # type: ignore[return-value]
        logger.warning(f"Missing '{COURSE_CODE}' or '{COURSE_TITLE}' in course info.")
        return {}
