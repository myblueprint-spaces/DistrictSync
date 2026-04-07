"""Classes entity transformer — homeroom classes, subject classes, blended class integration."""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import (
    COURSE_CODE,
    COURSE_TITLE,
    DISTRICT_COURSE_CODE,
    LAST_NAME,
    MASTER_TIMETABLE_ID,
    SCHOOL_NUMBER,
    TEACHER_NAME,
)
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import BlendedClassDetector
from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


class ClassTransformer(BaseTransformer):
    def __init__(self):
        self._blended_detector = BlendedClassDetector()

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        source_config = mapping.get("source_files", {})
        normalized_sources = self.normalize_source_config(source_config)
        field_map = mapping.get("field_map", {})
        homeroom_grades = context.global_config.get("homeroom_grades", [])
        teacher_id_col = context.get_teacher_id_col()

        final_classes: list[pd.DataFrame] = []

        self._run_blended_detection(normalized_sources, mapping, context, teacher_id_col)
        self._create_homeroom_classes(
            final_classes, normalized_sources, field_map, homeroom_grades, teacher_id_col, context
        )
        self._create_subject_classes(
            final_classes, normalized_sources, field_map, homeroom_grades, teacher_id_col, context
        )

        if final_classes:
            result = pd.concat(final_classes, ignore_index=True).drop_duplicates(subset=["Class ID"])
            logger.info(f"[Classes] Total classes created: {len(result)}")
            return result

        return pd.DataFrame()

    # -------------------------------------------------------------------
    # Blended detection
    # -------------------------------------------------------------------
    def _run_blended_detection(
        self, normalized_sources: dict, mapping: dict, context: TransformContext, teacher_id_col: str
    ) -> None:
        class_info_df = self.get_source_file(context, normalized_sources, "class_info")
        if class_info_df.empty:
            logger.info("[Classes] No class info data found for blended class detection")
            return

        class_info_df = self.normalize_columns(class_info_df)
        if teacher_id_col in class_info_df.columns:
            class_info_df[teacher_id_col] = class_info_df[teacher_id_col].astype(str).str.strip()
        if MASTER_TIMETABLE_ID in class_info_df.columns:
            class_info_df[MASTER_TIMETABLE_ID] = class_info_df[MASTER_TIMETABLE_ID].astype(str).str.strip()

        logger.info(f"[Classes] Class info data loaded: {len(class_info_df)} records")
        self._blended_detector.detect(class_info_df, mapping, context)

    # -------------------------------------------------------------------
    # Homeroom classes
    # -------------------------------------------------------------------
    def _create_homeroom_classes(
        self,
        final_classes: list[pd.DataFrame],
        normalized_sources: dict,
        field_map: dict,
        homeroom_grades: list,
        teacher_id_col: str,
        context: TransformContext,
    ) -> None:
        student_demo_df = self.get_source_file(context, normalized_sources, "student_demographic")
        if student_demo_df.empty:
            return

        student_demo_df = self.normalize_columns(student_demo_df)
        if teacher_id_col in student_demo_df.columns:
            student_demo_df[teacher_id_col] = student_demo_df[teacher_id_col].astype(str).str.strip()

        students_field_map = context.get_students_config().get("field_map", {})
        grade_config = students_field_map.get("Grade", {})
        grade_col = grade_config.get("column", "grade").lower() if isinstance(grade_config, dict) else "grade"
        student_demo_df[grade_col] = student_demo_df[grade_col].apply(self.grade_to_ceds)
        homeroom_students: pd.DataFrame = student_demo_df[student_demo_df[grade_col].isin(homeroom_grades)]

        if homeroom_students.empty:
            return

        homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
        dedup_cols = [SCHOOL_NUMBER, homeroom_col]
        if teacher_id_col in homeroom_students.columns:
            dedup_cols.append(teacher_id_col)
        unique_homerooms = homeroom_students.drop_duplicates(subset=dedup_cols)

        if unique_homerooms.empty or homeroom_col not in unique_homerooms.columns:
            return

        hc = unique_homerooms.copy()
        hc["Class ID"] = (
            hc[SCHOOL_NUMBER].astype(str)
            + "_"
            + hc[homeroom_col].fillna("UnassignedHomeroom").astype(str)
            + f"_{context.school_year}"
        )

        hc["Name"] = hc.apply(
            lambda row: self._homeroom_name(row, homeroom_col, TEACHER_NAME, context.school_year),
            axis=1,
        )
        hc["Grade"] = hc[grade_col]
        hc["School ID"] = hc[SCHOOL_NUMBER]
        hc["Start Date"] = self.resolve_date(field_map, "Start Date", context)
        hc["End Date"] = self.resolve_date(field_map, "End Date", context)

        # Store for Enrollments
        hr_cols = [SCHOOL_NUMBER, homeroom_col, "Class ID"]
        if teacher_id_col in hc.columns:
            hr_cols.append(teacher_id_col)
        context.homeroom_classes_df = hc[hr_cols].copy()

        homeroom_output = pd.DataFrame()
        for tgt_field in field_map:
            homeroom_output[tgt_field] = hc[tgt_field] if tgt_field in hc.columns else pd.NA
        final_classes.append(homeroom_output)
        logger.info(f"[Classes] Created {len(hc)} homeroom classes")

    @staticmethod
    def _homeroom_name(row, homeroom_col: str, teacher_name_col: str, year: int) -> str:
        homeroom = row[homeroom_col]
        teacher = row[teacher_name_col]
        has_hr = pd.notna(homeroom) and str(homeroom).strip() != ""
        has_teacher = pd.notna(teacher) and str(teacher).strip() != ""
        parts = [str(homeroom) if has_hr else "Unassigned Homeroom"]
        if has_teacher:
            parts.append(f"- {teacher}")
        parts.append(f"({year})")
        return " ".join(parts)

    # -------------------------------------------------------------------
    # Subject classes
    # -------------------------------------------------------------------
    def _create_subject_classes(
        self,
        final_classes: list[pd.DataFrame],
        normalized_sources: dict,
        field_map: dict,
        homeroom_grades: list,
        teacher_id_col: str,
        context: TransformContext,
    ) -> None:
        schedule_df = self.get_source_file(context, normalized_sources, "student_schedule")
        if schedule_df.empty:
            return

        schedule_df = self.normalize_columns(schedule_df)
        if teacher_id_col in schedule_df.columns:
            schedule_df[teacher_id_col] = schedule_df[teacher_id_col].astype(str).str.strip()
        if MASTER_TIMETABLE_ID in schedule_df.columns:
            schedule_df[MASTER_TIMETABLE_ID] = schedule_df[MASTER_TIMETABLE_ID].astype(str).str.strip()

        schedule_df["grade_ceds"] = schedule_df["grade"].apply(self.grade_to_ceds)
        non_homeroom_df: pd.DataFrame = schedule_df[~schedule_df["grade_ceds"].isin(homeroom_grades)].copy()
        if non_homeroom_df.empty:
            return

        merged = self._merge_course_and_staff(non_homeroom_df, normalized_sources, teacher_id_col, context)
        merged = self._assign_class_ids(merged, field_map, context)

        subject_output = pd.DataFrame()
        subject_output["Class ID"] = merged["Class ID"]
        self._assign_class_names(subject_output, merged, field_map, context)
        self._assign_grades(subject_output, merged, field_map, context)

        school_id_config = field_map.get("School ID", {})
        school_col = (
            school_id_config.get("column", SCHOOL_NUMBER).lower()
            if isinstance(school_id_config, dict)
            else SCHOOL_NUMBER
        )
        subject_output["School ID"] = merged.get(school_col, "")
        subject_output["Start Date"] = self.resolve_date(field_map, "Start Date", context)
        subject_output["End Date"] = self.resolve_date(field_map, "End Date", context)

        final_classes.append(subject_output)
        logger.info(f"[Classes] Created {len(subject_output)} subject classes")

    def _merge_course_and_staff(
        self, df: pd.DataFrame, normalized_sources: dict, teacher_id_col: str, context: TransformContext
    ) -> pd.DataFrame:
        merged = df
        course_df = self.get_source_file(context, normalized_sources, "course_info")
        if not course_df.empty:
            course_df = self.normalize_columns(course_df)
            if DISTRICT_COURSE_CODE in merged.columns and COURSE_CODE not in merged.columns:
                merged = merged.rename(columns={DISTRICT_COURSE_CODE: COURSE_CODE})
            merged = merged.merge(
                course_df[[SCHOOL_NUMBER, COURSE_CODE, COURSE_TITLE]],
                on=[SCHOOL_NUMBER, COURSE_CODE],
                how="left",
            )

        staff_df = self.get_source_file(context, normalized_sources, "staff_info")
        if not staff_df.empty:
            staff_df = self.normalize_columns(staff_df)
            if teacher_id_col in staff_df.columns:
                staff_df[teacher_id_col] = staff_df[teacher_id_col].astype(str).str.strip()
            merged = merged.merge(
                staff_df[[teacher_id_col, LAST_NAME]],
                on=teacher_id_col,
                how="left",
            )
        return merged

    def _assign_class_ids(self, merged: pd.DataFrame, field_map: dict, context: TransformContext) -> pd.DataFrame:
        return self.assign_class_ids(merged, field_map, context)

    def _assign_class_names(
        self, output: pd.DataFrame, merged: pd.DataFrame, field_map: dict, context: TransformContext
    ) -> None:
        name_config = field_map.get("Name", {})
        if not isinstance(name_config, dict):
            return

        def get_name(row):
            blended_id = row["Class ID"]
            if blended_id in context.blended_class_metadata:
                return context.blended_class_metadata[blended_id]["Name"]
            teacher_flag = name_config.get("primary_teacher_flag", "").lower()
            teacher_last = name_config.get("teacher_last_name", "last name").lower()
            course_title = name_config.get("course_title", "title").lower()
            section = name_config.get("section_letter", "section letter").lower()
            return self.generate_class_name(row, teacher_flag, teacher_last, course_title, section, context)

        output["Name"] = merged.apply(get_name, axis=1)

    def _assign_grades(
        self, output: pd.DataFrame, merged: pd.DataFrame, field_map: dict, context: TransformContext
    ) -> None:
        grade_config = field_map.get("Grade", "grade")

        def get_grade(row):
            if row["Class ID"] in context.blended_class_metadata:
                return ""
            if isinstance(grade_config, dict):
                col = grade_config.get("column", "grade").lower()
            elif isinstance(grade_config, str):
                col = grade_config.lower()
            else:
                col = ""
            return self.grade_to_ceds(row.get(col, "")) if col else ""

        output["Grade"] = merged.apply(get_grade, axis=1)
