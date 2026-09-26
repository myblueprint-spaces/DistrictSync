"""Enrollments entity transformer — homeroom, subject, and blended teacher enrollments.

Consumes the :class:`~src.etl.transformers.context.ClassArtifacts` bundle that
ClassTransformer publishes (homeroom lookup, normalized ClassInformation,
blended maps) and FAILS LOUD when it is absent — the explicit ordering
assertion for the Classes → Enrollments handoff. Each enrollment source
(homeroom / subject+blended / ClassInformation co-teacher) is built by a
function returning a DataFrame (or None); ``transform`` concatenates them in
the fixed legacy order so ``Enrollments.csv`` row order is byte-identical.

**Every linking column is required where it is read** (plan 0053 S10,
``failure-policy.md`` §5 (a)/(b)): a source that is present and non-empty but lacks a
column the homeroom or subject path joins on raises ONE typed ``SourceSchemaError``
through ``columns.require_columns`` — never a raw ``KeyError``, never a path silently left
out, never a partial set. ``Enrollments.csv`` is a DEACTIVATING file (a student missing
from it may be removed from the class — ``docs/partner/faq.md``, "What happens to
enrollments no longer in the file?"), so Enrollments is CRITICAL and such a fault fails
the run with the last good output untouched. **The one owner-approved exception** (ruling
2026-09-25, §5 #15): ClassInformation's CO-TEACHER columns are optional — missing, those
co-teacher rows are left out with ONE WARNING and ``OutcomeNote.COTEACHER_SOURCE_UNUSABLE``
on the outcome (a standing warning on Home), never silently and never as a failure. An
ABSENT or EMPTY optional source (no ClassInformation, no schedule) is not a missing column:
that path simply contributes nothing, as before.
"""

import logging
from typing import Any, Optional

import pandas as pd

from src.etl.column_names import (
    GRADE,
    HOMEROOM,
    MASTER_TIMETABLE_ID,
    PRIMARY_TEACHER,
    SCHOOL_NUMBER,
    SECTION_LETTER,
    STUDENT_NUMBER,
    TEACHER_ID,
)
from src.etl.errors import GuardKind
from src.etl.outcomes import OutcomeNote
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.columns import (
    Previously,
    absent_columns,
    require_columns,
    resolve_source_column,
    source_column_label,
)
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.course_codes import note_unapplied_exclusions
from src.etl.transformers.grades import resolve_timetable_scope, split_by_homeroom_grades
from src.etl.transformers.ids import normalize_id_series

logger = logging.getLogger(__name__)

#: The Enrollments ``source_columns`` ROLES for the two ClassInformation columns the
#: co-teacher path reads (plan 0053 S9, §5 #11) — named ONCE, read by
#: :meth:`EnrollmentTransformer._classinfo_coteacher_enrollments` and by the config
#: loader's unknown-key walker through ``SOURCE_COLUMN_ROLES`` (plan 0053 S12).
CLASS_INFO_PRIMARY_TEACHER_ROLE = "class_info_primary_teacher"
CLASS_INFO_SECTION_LETTER_ROLE = "class_info_section_letter"


class EnrollmentTransformer(BaseTransformer):
    SOURCE_COLUMN_ROLES = frozenset({CLASS_INFO_PRIMARY_TEACHER_ROLE, CLASS_INFO_SECTION_LETTER_ROLE})

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        source_config = mapping.get("source_files", {})
        normalized_sources = self.normalize_source_config(source_config)
        field_map = mapping.get("field_map", {})
        homeroom_grades = context.global_config.get("homeroom_grades", [])

        # Ordering assertion for the Classes → Enrollments handoff: ALL THREE
        # enrollment sources below (homeroom / subject / ClassInformation
        # co-teacher) read the class artifacts bundle, so this must fire
        # UNCONDITIONALLY — not only when a schedule happens to be present.
        artifacts = context.class_artifacts
        if artifacts is None:
            # failure-policy: join_key
            raise ValueError(
                "[Enrollments] No class artifacts on the shared context: ClassTransformer "
                "must run before EnrollmentTransformer (it publishes the homeroom classes "
                "and blended-class maps that homeroom/subject/co-teacher enrollments "
                "consume). Fix the mapping config so 'Classes' is enabled and precedes "
                "'Enrollments' in entity_order/enabled_entities."
            )

        # student_schedule is fetched and normalized UNCONDITIONALLY. Only
        # _subject_enrollments needs it (and self-guards on an empty frame
        # before touching any schedule-only column) — homeroom and
        # ClassInformation co-teacher enrollments never depended on schedule
        # data and must not be suppressed just because a district's
        # StudentSchedule.txt is empty or was never supplied.
        schedule_df = self.get_source_file(context, normalized_sources, "student_schedule")
        schedule_df = self.normalize_columns(schedule_df)

        user_id_config = field_map.get("User ID", {})
        student_id_col = resolve_source_column(
            user_id_config, "student_id_col", default=STUDENT_NUMBER, previously=Previously.AS_CONFIGURED
        )
        staff_id_col = resolve_source_column(
            user_id_config, "staff_id_col", default=TEACHER_ID, previously=Previously.AS_CONFIGURED
        )
        # The same two columns in CONFIG spelling — what a typed error names (S10).
        student_id_label = source_column_label(user_id_config, "student_id_col", default=STUDENT_NUMBER)
        staff_id_label = source_column_label(user_id_config, "staff_id_col", default=TEACHER_ID)

        student_demo_df = self._load_student_demo(normalized_sources, staff_id_col, context)

        # Fixed legacy source order — concat order IS the CSV row order.
        sources = [
            self._homeroom_enrollments(
                student_demo_df, homeroom_grades, staff_id_col, staff_id_label, artifacts, context
            ),
            self._subject_enrollments(
                schedule_df,
                homeroom_grades,
                (student_id_col, student_id_label),
                (staff_id_col, staff_id_label),
                field_map,
                artifacts,
                context,
            ),
            self._classinfo_coteacher_enrollments(
                staff_id_col, staff_id_label, mapping.get("source_columns") or {}, artifacts, context
            ),
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
        staff_id_label: str,
        artifacts: ClassArtifacts,
        context: TransformContext,
    ) -> Optional[pd.DataFrame]:
        """Student + teacher homeroom rows (in that order), or None.

        Every column the two row sets are built from is REQUIRED once homeroom
        students exist (§5 #5/#10/#28): the grade that picks them (``PII_SCOPE``), and
        the school + homeroom the merge joins on, the student number the student rows
        carry and the teacher id the teacher rows carry (``JOIN_KEY``). This replaced a
        ``try/except (KeyError, MergeError)`` that logged and shipped whatever rows had
        been built before the error — a partial homeroom set in a deactivating file
        (plan 0053 S10; on pandas 2.3 the ``MergeError`` arm was unreachable anyway —
        a merge-key dtype mismatch raises a plain ``ValueError`` — and the reachable
        ``KeyError`` was a missing column). An SD83 export without its teacher-id column
        once shipped every homeroom with no teacher this way (ROADMAP, 2026-09-14).
        """
        if student_demo_df.empty or artifacts.homeroom_classes_df.empty:
            return None

        # Work on a copy to avoid mutating the shared raw_data DataFrame
        student_demo_df = student_demo_df.copy()

        # The Students mapping names the DEMOGRAPHIC columns (plan 0053 S9: read
        # through `context.entity_mappings`; before S9 this path never saw it).
        students_field_map = context.get_students_config().get("field_map", {})
        grade_col = resolve_source_column(students_field_map, "Grade", default=GRADE, previously=Previously.DEFAULT)
        homeroom_col = resolve_source_column(
            students_field_map, "Homeroom", default=HOMEROOM, previously=Previously.DEFAULT
        )
        # The demographic student-ID column comes from Students config (not the
        # schedule-targeted Enrollments ID config) — see get_demo_student_col.
        demo_student_col = context.get_demo_student_col()

        # failure-policy: pii_scope
        require_columns(
            student_demo_df.columns,
            [source_column_label(students_field_map, "Grade", default=GRADE)],
            entity="Enrollments",
            guard=GuardKind.PII_SCOPE,
        )
        homeroom_students = split_by_homeroom_grades(student_demo_df, grade_col, homeroom_grades, keep="homeroom")
        if homeroom_students.empty:
            return None

        # failure-policy: join_key
        require_columns(
            homeroom_students.columns,
            [
                SCHOOL_NUMBER,
                source_column_label(students_field_map, "Homeroom", default=HOMEROOM),
                context.get_demo_student_label(),
                staff_id_label,
            ],
            entity="Enrollments",
            guard=GuardKind.JOIN_KEY,
        )
        # The homeroom lookup Classes published carries the teacher id only when ITS
        # demographic source did — the same file in every bundled config; a mapping
        # pointing the two entities at different files must not lose the teacher rows.
        # failure-policy: join_key
        require_columns(
            artifacts.homeroom_classes_df.columns, [staff_id_label], entity="Enrollments", guard=GuardKind.JOIN_KEY
        )

        hr_classes = artifacts.homeroom_classes_df.copy()
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
        logger.info(f"[Enrollments] Created {len(student_enroll)} student homeroom enrollments")

        # Teacher homeroom enrollments (unfiltered `valid` — students-only filter). The
        # teacher id is on BOTH sides of the merge (required above), so the homeroom
        # class's own teacher is the `_y` column.
        teacher_id_y_col = staff_id_col + "_y"
        teacher_enroll = valid.drop_duplicates(subset=["Class ID"])[
            ["Class ID", teacher_id_y_col, SCHOOL_NUMBER]
        ].copy()
        teacher_enroll.rename(columns={teacher_id_y_col: "User ID"}, inplace=True)
        teacher_enroll["Role"] = "teacher"
        teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
        logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher homeroom enrollments")

        return pd.concat([student_enroll, teacher_enroll], ignore_index=True)

    # -------------------------------------------------------------------
    # Subject enrollments
    # -------------------------------------------------------------------
    def _subject_enrollments(
        self,
        schedule_df: pd.DataFrame,
        homeroom_grades: list,
        student_id: tuple[str, str],
        staff_id: tuple[str, str],
        field_map: dict,
        artifacts: ClassArtifacts,
        context: TransformContext,
    ) -> Optional[pd.DataFrame]:
        """Student subject + blended teacher + non-blended teacher rows (in that order), or None.

        ``student_id`` / ``staff_id`` are ``(normalised column, config label)`` pairs. Once
        timetable rows exist, the schedule's student id, teacher id and school number
        are REQUIRED (§5 #28/#36, ``JOIN_KEY``) — a schedule without its student-ID
        column used to ship every timetable class with teachers only, unenrolling every
        timetable student — as are its grade (§5 #5, ``PII_SCOPE``) and its Class ID
        column (§5 #29, in ``assign_class_ids``).
        """
        student_id_col, student_id_label = student_id
        staff_id_col, staff_id_label = staff_id
        # Work on a copy to avoid mutating the shared raw_data DataFrame
        schedule_df = schedule_df.copy()

        if staff_id_col in schedule_df.columns:
            schedule_df[staff_id_col] = normalize_id_series(schedule_df[staff_id_col])

        excluded_codes = context.global_config.get("excluded_course_codes", [])
        note_unapplied_exclusions(context, "Enrollments", schedule_df, configured=bool(excluded_codes))
        schedule_df = self.filter_excluded_course_codes(schedule_df, excluded_codes)
        if schedule_df.empty:
            return None

        # The SAME schedule grade column Classes' split reads (the Classes
        # mapping's `Grade`), so the two keep the same rows (zero-orphan).
        # failure-policy: pii_scope
        require_columns(
            schedule_df.columns, [context.get_schedule_grade_label()], entity="Enrollments", guard=GuardKind.PII_SCOPE
        )
        non_homeroom = split_by_homeroom_grades(
            schedule_df,
            context.get_schedule_grade_col(),
            homeroom_grades,
            keep="subject",
            timetable_scope=resolve_timetable_scope(context.global_config, homeroom_grades),
        )
        if non_homeroom.empty:
            return None

        # The Class ID column is listed here too (``assign_class_ids`` checks it again as
        # its own guard), so ONE call names every linking column the schedule lacks.
        # failure-policy: join_key
        require_columns(
            non_homeroom.columns,
            [
                student_id_label,
                staff_id_label,
                SCHOOL_NUMBER,
                source_column_label(field_map, "Class ID", default=MASTER_TIMETABLE_ID),
            ],
            entity="Enrollments",
            guard=GuardKind.JOIN_KEY,
        )
        non_homeroom = self._assign_class_ids(non_homeroom, field_map, context)  # type: ignore[assignment]

        parts: list[pd.DataFrame] = []

        # Student subject enrollments — filtered to the active roster (schedule
        # `Student ID`, same pupil-number value space as the roster). Teacher
        # derivations below use the UNfiltered `non_homeroom`, so teacher rows
        # stay byte-identical to the pre-filter output (students-only filter).
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
        teacher_enroll = non_blended[["Class ID", staff_id_col, SCHOOL_NUMBER]].copy()
        teacher_enroll.rename(columns={staff_id_col: "User ID"}, inplace=True)
        teacher_enroll["Role"] = "teacher"
        teacher_enroll = self.clean_invalid_ids(teacher_enroll, "User ID")
        parts.append(teacher_enroll)
        logger.info(f"[Enrollments] Created {len(teacher_enroll)} teacher subject enrollments")

        return pd.concat(parts, ignore_index=True)

    def _assign_class_ids(self, df: pd.DataFrame, field_map: dict, context: TransformContext) -> pd.DataFrame:
        return self.assign_class_ids(df, field_map, context, entity="Enrollments")

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
        staff_id_label: str,
        source_columns: dict[str, Any],
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

        The two ClassInformation columns it reads resolve from the Enrollments
        entity's ``source_columns`` block (plan 0053 S9, §5 #11): roles
        ``class_info_primary_teacher`` and ``class_info_section_letter``, MyEd BC
        defaults ``primary teacher`` / ``section letter`` when unset.

        **The co-teacher columns are OPTIONAL, never silent** (§5 #15 — owner ruling
        2026-09-25, reversing Gate A answer 2 for these columns only). With
        ClassInformation present and non-empty, a missing primary-teacher flag or teacher
        id leaves every co-teacher row out; with primary-teacher rows found, a missing
        section column leaves Path 1's rows out (when homeroom classes exist) and a
        missing Master Timetable ID Path 2's (when blended classes exist) — the other
        path still runs. Each case logs ONE aggregated WARNING (config-spelled column
        names + a count) and records ``OutcomeNote.COTEACHER_SOURCE_UNUSABLE`` on the
        Enrollments outcome, which Home and Run History show as a standing WARNING every
        night it persists (``failure_copy.NOTE_TIER``). Every other linking column stays
        fail-CLOSED — here, ClassInformation's school number once co-teacher rows are
        being built. An ABSENT or EMPTY ClassInformation contributes nothing and records
        nothing: there is no co-teacher source to have been unusable.
        """
        class_info_df = artifacts.class_info_df
        if class_info_df.empty:
            return None

        # Columns are already normalized by ClassTransformer._run_blended_detection,
        # but take a copy so we don't mutate the published artifact frame.
        class_info_df = class_info_df.copy()

        primary_col = resolve_source_column(
            source_columns, CLASS_INFO_PRIMARY_TEACHER_ROLE, default=PRIMARY_TEACHER, previously=Previously.DEFAULT
        )
        section_col = resolve_source_column(
            source_columns, CLASS_INFO_SECTION_LETTER_ROLE, default=SECTION_LETTER, previously=Previously.DEFAULT
        )
        section_label = source_column_label(source_columns, CLASS_INFO_SECTION_LETTER_ROLE, default=SECTION_LETTER)

        # The two columns EVERY co-teacher row is built from: without either, no row can be
        # (§5 #15, owner ruling 2026-09-25 — left out with a standing warning, never a failure).
        # failure-policy: optional_field
        entry_missing = absent_columns(
            class_info_df.columns,
            [
                source_column_label(source_columns, CLASS_INFO_PRIMARY_TEACHER_ROLE, default=PRIMARY_TEACHER),
                staff_id_label,
            ],
        )
        if entry_missing:
            self._note_coteachers_left_out(context, entry_missing, unused_rows=len(class_info_df))
            return None

        # Every co-teacher row is about to read the school: that one stays a linking column.
        # failure-policy: join_key
        require_columns(class_info_df.columns, [SCHOOL_NUMBER], entity="Enrollments", guard=GuardKind.JOIN_KEY)

        primary_rows: pd.DataFrame = class_info_df[
            normalize_id_series(class_info_df[primary_col]).str.upper() == "Y"
        ].copy()  # type: ignore[assignment]
        if primary_rows.empty:
            return None

        # Path 1 reads the section column when homeroom classes exist, Path 2 the Master
        # Timetable ID when blended classes exist. A path whose column is missing is left
        # out — the other still runs — and ONE note + warning names every missing column.
        use_path_1 = not artifacts.homeroom_classes_df.empty
        use_path_2 = bool(artifacts.blended_class_map)
        # failure-policy: optional_field
        path_missing = absent_columns(
            primary_rows.columns,
            [*([section_label] if use_path_1 else []), *([MASTER_TIMETABLE_ID] if use_path_2 else [])],
        )
        if path_missing:
            self._note_coteachers_left_out(context, path_missing, unused_rows=len(primary_rows))
            use_path_1 = use_path_1 and section_label not in path_missing
            use_path_2 = use_path_2 and MASTER_TIMETABLE_ID not in path_missing

        primary_rows[staff_id_col] = normalize_id_series(primary_rows[staff_id_col])
        primary_rows[SCHOOL_NUMBER] = normalize_id_series(primary_rows[SCHOOL_NUMBER])
        if section_col in primary_rows.columns:
            primary_rows[section_col] = normalize_id_series(primary_rows[section_col])

        rows: list[dict[str, Any]] = []

        # Path 1: section-letter → homeroom class id
        hr_df = artifacts.homeroom_classes_df
        if use_path_1:
            students_field_map = context.get_students_config().get("field_map", {})
            homeroom_col = resolve_source_column(
                students_field_map, "Homeroom", default=HOMEROOM, previously=Previously.DEFAULT
            )
            # School + homeroom are on the lookup BY CONSTRUCTION: Classes builds it from
            # exactly these two columns (and required both — §5 #30).
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
        if use_path_2:
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

    @staticmethod
    def _note_coteachers_left_out(context: TransformContext, missing: tuple[str, ...], *, unused_rows: int) -> None:
        """ONE aggregated WARNING + the Enrollments outcome note for co-teacher rows left out (§5 #15).

        ``missing`` is the absent columns in CONFIG spelling (never an observed header) and
        ``unused_rows`` the ClassInformation rows affected — the whole file when an entry column
        is missing, the primary-teacher rows when a path column is (DECISIONS 2026-09-25 (f)).
        With only ONE path's column missing the other path still reads those same rows, so the
        wording claims only that the links needing the missing column(s) were not made — never
        that the rows went unused. Both values are bounded, so the line names no person and no
        value. The note makes the run PARTIAL
        on Home and Run History every night it persists (``failure_copy.NOTE_TIER``).
        """
        logger.warning(
            f"[Enrollments] CO-TEACHERS LEFT OUT — Class Information has no column(s) {list(missing)} "
            f"for linking co-teachers, so the co-teacher links that need them were not made "
            f"({unused_rows} Class Information row(s) affected). Everything else is built as usual; the "
            f"run shows a warning until the export carries the column(s)."
        )
        context.record_outcome_note("Enrollments", OutcomeNote.COTEACHER_SOURCE_UNUSABLE, unused_rows)
