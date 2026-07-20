"""Blended class detection service.

Identifies when a teacher teaches multiple course sections at the same time slot
with 2+ grade levels, and consolidates them into a single blended class.
"""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import (
    COURSE_CODE,
    COURSE_TITLE,
    MASTER_TIMETABLE_ID,
    SCHOOL_NUMBER,
)
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


class BlendedClassDetector(BaseTransformer):
    """Detects blended classes and populates context with blended mappings."""

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        """Not used directly — call detect() instead."""
        raise NotImplementedError("Use detect() instead")

    def detect(self, class_info_df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> None:
        """Run blended class detection and populate context.blended_* state."""
        if class_info_df.empty:
            logger.info("No class info data available for blended class detection")
            return

        normalized_sources = self.normalize_source_config(mapping.get("source_files", {}))
        field_map = mapping.get("field_map", {})
        teacher_id_col = context.get_teacher_id_col()

        schedule_df = self.get_source_file(context, normalized_sources, "student_schedule")
        course_df = self.get_source_file(context, normalized_sources, "course_info")

        if schedule_df.empty or course_df.empty:
            logger.warning("Student schedule or course info data is missing. Cannot detect blended classes.")
            return

        schedule_df = self.normalize_columns(schedule_df)
        course_df = self.normalize_columns(course_df)

        excluded_codes = context.global_config.get("excluded_course_codes", [])
        schedule_df = self.filter_excluded_course_codes(schedule_df, excluded_codes)

        if MASTER_TIMETABLE_ID in schedule_df.columns:
            schedule_df[MASTER_TIMETABLE_ID] = schedule_df[MASTER_TIMETABLE_ID].astype(str).str.strip()

        mtid_to_grade = self._build_grade_map(schedule_df)
        course_title_map = self._build_course_title_map(course_df)

        required = [teacher_id_col, MASTER_TIMETABLE_ID]
        if any(col not in class_info_df.columns for col in required):
            # Fall back to schedule data (e.g. non-enhanced ClassInformation).
            # Schedule has one row per student; deduplicate to one row per
            # section (Master Timetable ID) so the grouping logic is equivalent
            # to ClassInformation's one-row-per-section structure.
            if all(col in schedule_df.columns for col in required):
                logger.info(
                    "class_info missing required columns; falling back to student schedule for blended detection"
                )
                class_info_df = schedule_df.drop_duplicates(subset=[MASTER_TIMETABLE_ID])
            else:
                logger.warning(f"Cannot detect blended classes. Missing required columns: {required}")
                return

        working = class_info_df.copy()

        # Drop rows where teacher_id is blank/nan: a blended class is defined
        # as multiple sections taught by the SAME teacher at the same time, so
        # a section with no primary teacher can't participate. Without this
        # guard, all teacherless sections at a school collapse into a single
        # fake session_key (all blank components) and get "blended" together,
        # producing empty-userId enrollment rows and nonsense class groupings.
        if teacher_id_col in working.columns:
            teacher_series = working[teacher_id_col].astype(str).str.strip().str.lower()
            working = working[(teacher_series != "") & (teacher_series != "nan")]
            if working.empty:
                logger.info("[Blended Classes] No rows with a teacher id; skipping detection")
                return

        session_components = [SCHOOL_NUMBER, teacher_id_col, "term", "semester", "day", "period"]
        available = [col for col in session_components if col in working.columns]

        for col in available:
            working[col] = working[col].fillna("").astype(str)
        working["session_key"] = working[available].agg("_".join, axis=1)

        count = 0
        for session_key, group in working.groupby("session_key"):
            if len(group) <= 1:
                continue

            if not self.validate(group, mtid_to_grade):
                continue

            blended_id = f"BLENDED_{session_key}_{context.school_year}"
            all_mt_ids = sorted(set(group[MASTER_TIMETABLE_ID].tolist()))

            for mt_id in all_mt_ids:
                context.blended_class_map[mt_id] = blended_id

            all_teachers = working[working[MASTER_TIMETABLE_ID].isin(all_mt_ids)][teacher_id_col].unique().tolist()
            context.blended_teacher_map[blended_id] = all_teachers

            grade_str = self.get_grade_range(group, mtid_to_grade)
            class_name = self.create_name(group, field_map, grade_str, course_title_map, context)

            context.blended_class_metadata[blended_id] = {
                "Name": class_name,
                "Grade": grade_str,
                "School ID": group[SCHOOL_NUMBER].iloc[0] if SCHOOL_NUMBER in group.columns else "",
                "Original_MT_IDs": all_mt_ids,
            }
            count += 1

        logger.info(f"[Blended Classes] Detection completed: {count} blended classes identified")

    def validate(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> bool:
        """A valid blend requires 2+ unique sections with 2+ distinct CEDS grades."""
        unique_mt_ids = session_group[MASTER_TIMETABLE_ID].unique()
        if len(unique_mt_ids) <= 1:
            return False
        grades = set()
        for mt_id in unique_mt_ids:
            grade = mtid_to_grade.get(mt_id)
            if grade:
                grades.add(self.grade_to_ceds(grade))
        return len(grades) >= 2

    def get_grade_range(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> str:
        grades = set()
        for mt_id in session_group[MASTER_TIMETABLE_ID].unique():
            grade = mtid_to_grade.get(mt_id)
            if grade:
                grades.add(self.grade_to_ceds(grade))
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

        return self.truncate_name(full_name)

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
