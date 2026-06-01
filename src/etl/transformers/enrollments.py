"""Enrollments entity transformer — homeroom, subject, and blended teacher enrollments."""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import MASTER_TIMETABLE_ID, SCHOOL_NUMBER
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

        self._homeroom_enrollments(final, student_demo_df, homeroom_grades, staff_id_col, context)
        self._subject_enrollments(final, schedule_df, homeroom_grades, student_id_col, staff_id_col, field_map, context)
        self._classinfo_coteacher_enrollments(final, staff_id_col, context)

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
        # The demographic file's student-ID column comes from Students config —
        # MyEd BC's StudentDemographicInformation uses "Student Number" while
        # StudentSchedule uses "Student ID", so the Enrollments staff/student
        # ID config (which targets the schedule) can't be reused here.
        user_id_config = students_field_map.get("User ID", "student number")
        if isinstance(user_id_config, dict):
            demo_student_col = str(user_id_config.get("column", "student number")).lower()
        else:
            demo_student_col = str(user_id_config).lower()

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
            student_enroll = valid[["Class ID", demo_student_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={demo_student_col: "User ID"}, inplace=True)
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

        excluded_codes = context.global_config.get("excluded_course_codes", [])
        schedule_df = self.filter_excluded_course_codes(schedule_df, excluded_codes)
        if schedule_df.empty:
            return

        schedule_df["grade_ceds"] = schedule_df["grade"].apply(self.grade_to_ceds)
        non_homeroom: pd.DataFrame = schedule_df[~schedule_df["grade_ceds"].isin(homeroom_grades)].copy()  # type: ignore[assignment]
        if non_homeroom.empty:
            return

        non_homeroom = self._assign_class_ids(non_homeroom, field_map, context)  # type: ignore[assignment]

        # Student subject enrollments
        if student_id_col in non_homeroom.columns and "Class ID" in non_homeroom.columns:
            student_enroll = non_homeroom[["Class ID", student_id_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)  # type: ignore[call-overload]
            student_enroll["Role"] = "student"
            final.append(student_enroll)  # type: ignore[arg-type]
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

    # -------------------------------------------------------------------
    # ClassInformation co-teacher enrollments
    # -------------------------------------------------------------------
    def _classinfo_coteacher_enrollments(
        self,
        final: list[pd.DataFrame],
        staff_id_col: str,
        context: TransformContext,
    ) -> None:
        """Emit teacher enrollments for ClassInformation rows with Primary Teacher=Y.

        Captures teachers (e.g. MADST modular-program teachers) who are not
        derived from student_schedule. Matches by (school_number, section
        letter) against homeroom_classes_df, and by Master Timetable ID
        against blended_class_map for subject classes. Rows that don't
        resolve to any known class are skipped. The outer
        drop_duplicates(subset=["Class ID","User ID","Role"]) in transform()
        deduplicates against any teacher rows already produced by the
        student_schedule path.
        """
        class_info_df = context.class_info_df
        if class_info_df.empty:
            return

        # Columns are already normalized by ClassTransformer._run_blended_detection,
        # but take a copy so we don't mutate the cached frame.
        class_info_df = class_info_df.copy()

        primary_col = "primary teacher"
        section_col = "section letter"
        if primary_col not in class_info_df.columns or SCHOOL_NUMBER not in class_info_df.columns:
            return
        if staff_id_col not in class_info_df.columns:
            return

        primary_rows: pd.DataFrame = class_info_df[
            class_info_df[primary_col].astype(str).str.strip().str.upper() == "Y"
        ].copy()  # type: ignore[assignment]
        if primary_rows.empty:
            return

        primary_rows[staff_id_col] = primary_rows[staff_id_col].astype(str).str.strip()
        primary_rows[SCHOOL_NUMBER] = primary_rows[SCHOOL_NUMBER].astype(str).str.strip()
        if section_col in primary_rows.columns:
            primary_rows[section_col] = primary_rows[section_col].astype(str).str.strip()

        rows: list[dict[str, Any]] = []

        # Path 1: section-letter → homeroom class id
        hr_df = context.homeroom_classes_df
        if not hr_df.empty and section_col in primary_rows.columns:
            students_field_map = context.get_students_config().get("field_map", {})
            homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
            if homeroom_col in hr_df.columns and SCHOOL_NUMBER in hr_df.columns:
                hr_lookup = hr_df[[SCHOOL_NUMBER, homeroom_col, "Class ID"]].copy()
                hr_lookup[SCHOOL_NUMBER] = hr_lookup[SCHOOL_NUMBER].astype(str).str.strip()
                hr_lookup[homeroom_col] = hr_lookup[homeroom_col].astype(str).str.strip()
                hr_lookup = hr_lookup.drop_duplicates(subset=[SCHOOL_NUMBER, homeroom_col])

                merged = primary_rows.merge(
                    hr_lookup.rename(columns={homeroom_col: section_col}),
                    on=[SCHOOL_NUMBER, section_col],
                    how="left",
                )
                hr_matches = merged[merged["Class ID"].notna()]
                for _, row in hr_matches.iterrows():
                    rows.append(
                        {
                            "Class ID": str(row["Class ID"]),
                            "User ID": str(row[staff_id_col]),
                            "Role": "teacher",
                            SCHOOL_NUMBER: str(row[SCHOOL_NUMBER]),
                        }
                    )

        # Path 2: Master Timetable ID → blended class id
        if MASTER_TIMETABLE_ID in primary_rows.columns and context.blended_class_map:
            primary_rows[MASTER_TIMETABLE_ID] = primary_rows[MASTER_TIMETABLE_ID].astype(str).str.strip()
            for _, row in primary_rows.iterrows():
                mt_id = row[MASTER_TIMETABLE_ID]
                blended_id = context.blended_class_map.get(mt_id)
                if blended_id:
                    rows.append(
                        {
                            "Class ID": blended_id,
                            "User ID": str(row[staff_id_col]),
                            "Role": "teacher",
                            SCHOOL_NUMBER: str(row[SCHOOL_NUMBER]),
                        }
                    )

        if not rows:
            return

        coteacher_df = pd.DataFrame(rows)
        coteacher_df = self.clean_invalid_ids(coteacher_df, "User ID")
        coteacher_df = coteacher_df.drop_duplicates(subset=["Class ID", "User ID", "Role"])
        if coteacher_df.empty:
            return

        final.append(coteacher_df)
        logger.info(f"[Enrollments] Created {len(coteacher_df)} ClassInformation co-teacher enrollments")
