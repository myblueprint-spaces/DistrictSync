"""Enrollments entity transformer — homeroom, subject, and blended teacher enrollments.

Consumes the :class:`~src.etl.transformers.context.ClassArtifacts` bundle that
ClassTransformer publishes (homeroom lookup, normalized ClassInformation,
blended maps) and FAILS LOUD when it is absent — the explicit ordering
assertion for the Classes → Enrollments handoff. Each enrollment source
(homeroom / subject+blended / ClassInformation co-teacher) is built by a
function returning a DataFrame (or None); ``transform`` concatenates them in
the fixed legacy order so ``Enrollments.csv`` row order is byte-identical.
"""

import logging
from typing import Any, Optional

import pandas as pd

from src.etl.column_names import MASTER_TIMETABLE_ID, SCHOOL_NUMBER
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.grades import split_by_homeroom_grades
from src.etl.transformers.ids import normalize_id_series

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

        # Ordering assertion for the Classes → Enrollments handoff: there is
        # schedule data to enroll, so the class artifacts MUST already exist.
        artifacts = context.class_artifacts
        if artifacts is None:
            raise ValueError(
                "[Enrollments] No class artifacts on the shared context: ClassTransformer "
                "must run before EnrollmentTransformer (it publishes the homeroom classes "
                "and blended-class maps that homeroom/subject/co-teacher enrollments "
                "consume). Fix the mapping config so 'Classes' is enabled and precedes "
                "'Enrollments' in entity_order/enabled_entities."
            )

        user_id_config = field_map.get("User ID", {})
        student_id_col = user_id_config.get("student_id_col", "student number").lower()
        staff_id_col = user_id_config.get("staff_id_col", "teacher id").lower()

        student_demo_df = self._load_student_demo(normalized_sources, staff_id_col, context)

        # Fixed legacy source order — concat order IS the CSV row order.
        sources = [
            self._homeroom_enrollments(student_demo_df, homeroom_grades, staff_id_col, artifacts, context),
            self._subject_enrollments(
                schedule_df, homeroom_grades, student_id_col, staff_id_col, field_map, artifacts, context
            ),
            self._classinfo_coteacher_enrollments(staff_id_col, artifacts, context),
        ]
        final = [frame for frame in sources if frame is not None]

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
            df[staff_id_col] = normalize_id_series(df[staff_id_col])
        return df

    # -------------------------------------------------------------------
    # Homeroom enrollments
    # -------------------------------------------------------------------
    def _homeroom_enrollments(
        self,
        student_demo_df: pd.DataFrame,
        homeroom_grades: list,
        staff_id_col: str,
        artifacts: ClassArtifacts,
        context: TransformContext,
    ) -> Optional[pd.DataFrame]:
        """Student + teacher homeroom rows (in that order), or None."""
        if student_demo_df.empty or artifacts.homeroom_classes_df.empty:
            return None

        # Work on a copy to avoid mutating the shared raw_data DataFrame
        student_demo_df = student_demo_df.copy()

        students_field_map = context.get_students_config().get("field_map", {})
        grade_col = self.resolve_column(students_field_map, "Grade", "grade")
        homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
        # The demographic student-ID column comes from Students config (not the
        # schedule-targeted Enrollments ID config) — see get_demo_student_col.
        demo_student_col = context.get_demo_student_col()

        homeroom_students = split_by_homeroom_grades(student_demo_df, grade_col, homeroom_grades, keep="homeroom")
        if homeroom_students.empty:
            return None

        parts: list[pd.DataFrame] = []
        try:
            hr_classes = artifacts.homeroom_classes_df.copy()
            if staff_id_col in hr_classes.columns:
                hr_classes[staff_id_col] = normalize_id_series(hr_classes[staff_id_col])

            merged = homeroom_students.merge(hr_classes, on=[SCHOOL_NUMBER, homeroom_col], how="left")
            valid = merged[merged["Class ID"].notna()]
            if valid.empty:
                return None

            # Student homeroom enrollments — filtered to the active roster so no
            # row references a student absent from Students.csv (zero-orphan
            # invariant). Teacher rows below derive from the UNfiltered `valid`
            # and are therefore byte-identical to the pre-filter output.
            active_students = self.filter_to_active(valid, demo_student_col, context, caller="Enrollments")
            student_enroll = active_students[["Class ID", demo_student_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={demo_student_col: "User ID"}, inplace=True)  # type: ignore[call-overload]
            student_enroll["Role"] = "student"
            parts.append(student_enroll)  # type: ignore[arg-type]
            logger.info(f"[Enrollments] Created {len(student_enroll)} student homeroom enrollments")

            # Teacher homeroom enrollments (unfiltered `valid` — students-only filter)
            teacher_id_y_col = staff_id_col + "_y"
            if teacher_id_y_col in valid.columns:
                teacher_enroll = valid.drop_duplicates(subset=["Class ID"])[
                    ["Class ID", teacher_id_y_col, SCHOOL_NUMBER]
                ].copy()
                teacher_enroll.rename(columns={teacher_id_y_col: "User ID"}, inplace=True)
                teacher_enroll["Role"] = "teacher"
                teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
                parts.append(teacher_enroll)
                logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher homeroom enrollments")

        except (KeyError, pd.errors.MergeError) as e:
            # Whatever was built before the error still ships (legacy behavior).
            logger.error(f"[Enrollments] Error merging homeroom data: {e}")

        if not parts:
            return None
        return pd.concat(parts, ignore_index=True)

    # -------------------------------------------------------------------
    # Subject enrollments
    # -------------------------------------------------------------------
    def _subject_enrollments(
        self,
        schedule_df: pd.DataFrame,
        homeroom_grades: list,
        student_id_col: str,
        staff_id_col: str,
        field_map: dict,
        artifacts: ClassArtifacts,
        context: TransformContext,
    ) -> Optional[pd.DataFrame]:
        """Student subject + blended teacher + non-blended teacher rows (in that order), or None."""
        # Work on a copy to avoid mutating the shared raw_data DataFrame
        schedule_df = schedule_df.copy()

        if staff_id_col in schedule_df.columns:
            schedule_df[staff_id_col] = normalize_id_series(schedule_df[staff_id_col])

        excluded_codes = context.global_config.get("excluded_course_codes", [])
        schedule_df = self.filter_excluded_course_codes(schedule_df, excluded_codes)
        if schedule_df.empty:
            return None

        non_homeroom = split_by_homeroom_grades(schedule_df, "grade", homeroom_grades, keep="subject")
        if non_homeroom.empty:
            return None

        non_homeroom = self._assign_class_ids(non_homeroom, field_map, context)  # type: ignore[assignment]

        parts: list[pd.DataFrame] = []

        # Student subject enrollments — filtered to the active roster (schedule
        # `Student ID`, same pupil-number value space as the roster). Teacher
        # derivations below use the UNfiltered `non_homeroom`, so teacher rows
        # stay byte-identical to the pre-filter output (students-only filter).
        if student_id_col in non_homeroom.columns and "Class ID" in non_homeroom.columns:
            active_students = self.filter_to_active(non_homeroom, student_id_col, context, caller="Enrollments")
            student_enroll = active_students[["Class ID", student_id_col, SCHOOL_NUMBER]].copy()
            student_enroll.rename(columns={student_id_col: "User ID"}, inplace=True)  # type: ignore[call-overload]
            student_enroll["Role"] = "student"
            parts.append(student_enroll)  # type: ignore[arg-type]
            logger.info(f"[Enrollments] Created {len(student_enroll)} student subject enrollments")

        # Blended teacher enrollments (artifact-derived; unaffected by the filter)
        blended_enroll = self._blended_teacher_enrollments(artifacts)
        if blended_enroll is not None:
            parts.append(blended_enroll)

        # Non-blended teacher enrollments (unfiltered `non_homeroom` — students-only filter)
        non_blended = non_homeroom[~non_homeroom["Class ID"].isin(artifacts.blended_teacher_map.keys())]
        if staff_id_col in non_blended.columns and "Class ID" in non_blended.columns:
            teacher_enroll = non_blended[["Class ID", staff_id_col, SCHOOL_NUMBER]].copy()
            teacher_enroll.rename(columns={staff_id_col: "User ID"}, inplace=True)
            teacher_enroll["Role"] = "teacher"
            teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
            parts.append(teacher_enroll)
            logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher subject enrollments")

        if not parts:
            return None
        return pd.concat(parts, ignore_index=True)

    def _assign_class_ids(self, df: pd.DataFrame, field_map: dict, context: TransformContext) -> pd.DataFrame:
        return self.assign_class_ids(df, field_map, context)

    @staticmethod
    def _blended_teacher_enrollments(artifacts: ClassArtifacts) -> Optional[pd.DataFrame]:
        rows = []
        for blended_id, teacher_list in artifacts.blended_teacher_map.items():
            school_id = artifacts.blended_class_metadata.get(blended_id, {}).get("School ID", "")
            for teacher_id in teacher_list:
                rows.append(
                    {
                        "Class ID": blended_id,
                        "User ID": teacher_id,
                        "Role": "teacher",
                        SCHOOL_NUMBER: school_id,
                    }
                )
        if not rows:
            return None
        blended_df = pd.DataFrame(rows).drop_duplicates()
        logger.info(f"[Enrollments] Created {len(blended_df)} blended class teacher enrollments")
        return blended_df

    # -------------------------------------------------------------------
    # ClassInformation co-teacher enrollments
    # -------------------------------------------------------------------
    def _classinfo_coteacher_enrollments(
        self,
        staff_id_col: str,
        artifacts: ClassArtifacts,
        context: TransformContext,
    ) -> Optional[pd.DataFrame]:
        """Teacher enrollments for ClassInformation rows with Primary Teacher=Y, or None.

        Captures teachers (e.g. MADST modular-program teachers) who are not
        derived from student_schedule. Matches by (school_number, section
        letter) against the homeroom lookup, and by Master Timetable ID
        against the blended class map for subject classes. Rows that don't
        resolve to any known class are skipped. The outer
        drop_duplicates(subset=["Class ID","User ID","Role"]) in transform()
        deduplicates against any teacher rows already produced by the
        student_schedule path.
        """
        class_info_df = artifacts.class_info_df
        if class_info_df.empty:
            return None

        # Columns are already normalized by ClassTransformer._run_blended_detection,
        # but take a copy so we don't mutate the published artifact frame.
        class_info_df = class_info_df.copy()

        primary_col = "primary teacher"
        section_col = "section letter"
        if primary_col not in class_info_df.columns or SCHOOL_NUMBER not in class_info_df.columns:
            return None
        if staff_id_col not in class_info_df.columns:
            return None

        primary_rows: pd.DataFrame = class_info_df[
            normalize_id_series(class_info_df[primary_col]).str.upper() == "Y"
        ].copy()  # type: ignore[assignment]
        if primary_rows.empty:
            return None

        primary_rows[staff_id_col] = normalize_id_series(primary_rows[staff_id_col])
        primary_rows[SCHOOL_NUMBER] = normalize_id_series(primary_rows[SCHOOL_NUMBER])
        if section_col in primary_rows.columns:
            primary_rows[section_col] = normalize_id_series(primary_rows[section_col])

        rows: list[dict[str, Any]] = []

        # Path 1: section-letter → homeroom class id
        hr_df = artifacts.homeroom_classes_df
        if not hr_df.empty and section_col in primary_rows.columns:
            students_field_map = context.get_students_config().get("field_map", {})
            homeroom_col = students_field_map.get("Homeroom", "homeroom").lower()
            if homeroom_col in hr_df.columns and SCHOOL_NUMBER in hr_df.columns:
                hr_lookup = hr_df[[SCHOOL_NUMBER, homeroom_col, "Class ID"]].copy()
                hr_lookup[SCHOOL_NUMBER] = normalize_id_series(hr_lookup[SCHOOL_NUMBER])
                hr_lookup[homeroom_col] = normalize_id_series(hr_lookup[homeroom_col])
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
        if MASTER_TIMETABLE_ID in primary_rows.columns and artifacts.blended_class_map:
            primary_rows[MASTER_TIMETABLE_ID] = normalize_id_series(primary_rows[MASTER_TIMETABLE_ID])
            for _, row in primary_rows.iterrows():
                mt_id = row[MASTER_TIMETABLE_ID]
                blended_id = artifacts.blended_class_map.get(mt_id)
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
            return None

        coteacher_df = pd.DataFrame(rows)
        coteacher_df = self.clean_invalid_ids(coteacher_df, "User ID")
        coteacher_df = coteacher_df.drop_duplicates(subset=["Class ID", "User ID", "Role"])
        if coteacher_df.empty:
            return None

        logger.info(f"[Enrollments] Created {len(coteacher_df)} ClassInformation co-teacher enrollments")
        return coteacher_df
