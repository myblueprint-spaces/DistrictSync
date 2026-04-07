"""Enrollments entity transformer — homeroom, subject, and blended teacher enrollments."""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import SCHOOL_NUMBER
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


class EnrollmentTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        source_config = mapping.get("source_files", {})
        normalized_sources = self.normalize_source_config(source_config)
        field_map = mapping.get("field_map", {})
        homeroom_grades = context.global_config.get("homeroom_grades", [])

        schedule_df = self.get_source_file(context, normalized_sources, "student_schedule")
        if schedule_df.empty:
            return pd.DataFrame()
        schedule_df = self.normalize_columns(schedule_df)

        user_id_config = field_map.get("User ID", {})
        student_id_col = user_id_config.get("student_id_col", "student number").lower()
        staff_id_col = user_id_config.get("staff_id_col", "teacher id").lower()

        student_demo_df = self._load_student_demo(normalized_sources, staff_id_col, context)

        final: list[pd.DataFrame] = []

        self._homeroom_enrollments(final, student_demo_df, homeroom_grades, student_id_col, staff_id_col, context)
        self._subject_enrollments(final, schedule_df, homeroom_grades, student_id_col, staff_id_col, field_map, context)

        if final:
            result = pd.concat(final, ignore_index=True).drop_duplicates(subset=["Class ID", "User ID", "Role"])
            if SCHOOL_NUMBER in result.columns:
                result.rename(columns={SCHOOL_NUMBER: "School ID"}, inplace=True)
            logger.info(f"[Enrollments] Created {len(result)} total enrollments")
            return result

        return pd.DataFrame()

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------
    def _load_student_demo(
        self, normalized_sources: dict, staff_id_col: str, context: TransformContext
    ) -> pd.DataFrame:
        df = self.get_source_file(context, normalized_sources, "student_demographic")
        if df.empty:
            logger.warning("[Enrollments] Student demographic data not available")
            return df
        df = self.normalize_columns(df)
        if staff_id_col in df.columns:
            df[staff_id_col] = df[staff_id_col].astype(str).str.strip()
        return df

    # -------------------------------------------------------------------
    # Homeroom enrollments
    # -------------------------------------------------------------------
    def _homeroom_enrollments(
        self,
        final: list[pd.DataFrame],
        student_demo_df: pd.DataFrame,
        homeroom_grades: list,
        student_id_col: str,
        staff_id_col: str,
        context: TransformContext,
    ) -> None:
        if student_demo_df.empty or context.homeroom_classes_df.empty:
            return

        # Work on a copy to avoid mutating the shared raw_data DataFrame
        student_demo_df = student_demo_df.copy()

        students_field_map = context.get_students_config().get("field_map", {})
        grade_col = students_field_map.get("Grade", {}).get("column", "grade").lower()
        homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()

        if grade_col in student_demo_df.columns:
            student_demo_df[grade_col] = student_demo_df[grade_col].apply(self.grade_to_ceds)

        homeroom_students = student_demo_df[student_demo_df[grade_col].isin(homeroom_grades)]
        if homeroom_students.empty:
            return

        try:
            hr_classes = context.homeroom_classes_df.copy()
            if staff_id_col in hr_classes.columns:
                hr_classes[staff_id_col] = hr_classes[staff_id_col].astype(str).str.strip()

            merged = homeroom_students.merge(hr_classes, on=[SCHOOL_NUMBER, homeroom_col], how="left")
            valid = merged[merged["Class ID"].notna()]
            if valid.empty:
                return

            # Student homeroom enrollments
            student_enroll = valid[["Class ID", student_id_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)
            student_enroll["Role"] = "student"
            final.append(student_enroll)
            logger.info(f"[Enrollments] Created {len(student_enroll)} student homeroom enrollments")

            # Teacher homeroom enrollments
            teacher_id_y_col = staff_id_col + "_y"
            if teacher_id_y_col in valid.columns:
                teacher_enroll = valid.drop_duplicates(subset=["Class ID"])[
                    ["Class ID", teacher_id_y_col, SCHOOL_NUMBER]
                ].copy()
                teacher_enroll.rename(columns={teacher_id_y_col: "User ID"}, inplace=True)
                teacher_enroll["Role"] = "teacher"
                teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
                final.append(teacher_enroll)
                logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher homeroom enrollments")

        except (KeyError, pd.errors.MergeError) as e:
            logger.error(f"[Enrollments] Error merging homeroom data: {e}")

    # -------------------------------------------------------------------
    # Subject enrollments
    # -------------------------------------------------------------------
    def _subject_enrollments(
        self,
        final: list[pd.DataFrame],
        schedule_df: pd.DataFrame,
        homeroom_grades: list,
        student_id_col: str,
        staff_id_col: str,
        field_map: dict,
        context: TransformContext,
    ) -> None:
        # Work on a copy to avoid mutating the shared raw_data DataFrame
        schedule_df = schedule_df.copy()

        if staff_id_col in schedule_df.columns:
            schedule_df[staff_id_col] = schedule_df[staff_id_col].astype(str).str.strip()

        schedule_df["grade_ceds"] = schedule_df["grade"].apply(self.grade_to_ceds)
        non_homeroom = schedule_df[~schedule_df["grade_ceds"].isin(homeroom_grades)].copy()
        if non_homeroom.empty:
            return

        non_homeroom = self._assign_class_ids(non_homeroom, field_map, context)

        # Student subject enrollments
        if student_id_col in non_homeroom.columns and "Class ID" in non_homeroom.columns:
            student_enroll = non_homeroom[["Class ID", student_id_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)
            student_enroll["Role"] = "student"
            final.append(student_enroll)
            logger.info(f"[Enrollments] Created {len(student_enroll)} student subject enrollments")

        # Blended teacher enrollments
        self._blended_teacher_enrollments(final, context)

        # Non-blended teacher enrollments
        non_blended = non_homeroom[~non_homeroom["Class ID"].isin(context.blended_teacher_map.keys())]
        if staff_id_col in non_blended.columns and "Class ID" in non_blended.columns:
            teacher_enroll = non_blended[["Class ID", staff_id_col, SCHOOL_NUMBER]].copy()
            teacher_enroll.rename(columns={staff_id_col: "User ID"}, inplace=True)
            teacher_enroll["Role"] = "teacher"
            teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
            final.append(teacher_enroll)
            logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher subject enrollments")

    def _assign_class_ids(self, df: pd.DataFrame, field_map: dict, context: TransformContext) -> pd.DataFrame:
        return self.assign_class_ids(df, field_map, context)

    @staticmethod
    def _blended_teacher_enrollments(final: list[pd.DataFrame], context: TransformContext) -> None:
        rows = []
        for blended_id, teacher_list in context.blended_teacher_map.items():
            school_id = context.blended_class_metadata.get(blended_id, {}).get("School ID", "")
            for teacher_id in teacher_list:
                rows.append(
                    {
                        "Class ID": blended_id,
                        "User ID": teacher_id,
                        "Role": "teacher",
                        SCHOOL_NUMBER: school_id,
                    }
                )
        if rows:
            blended_df = pd.DataFrame(rows).drop_duplicates()
            final.append(blended_df)
            logger.info(f"[Enrollments] Created {len(blended_df)} blended class teacher enrollments")
