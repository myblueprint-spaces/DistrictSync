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
    DISTRICT_COURSE_CODE,
    MASTER_TIMETABLE_ID,
    SCHOOL_NUMBER,
    TEACHER_NAME,
)
from src.etl.transformers.columns import Previously, resolve_source_column
from src.etl.transformers.context import TransformContext
from src.etl.transformers.course_codes import filter_excluded_course_codes, resolve_course_code_column
from src.etl.transformers.grades import (
    ceds_grade_series,
    grade_to_ceds,
    resolve_timetable_scope,
    schedule_grade_column,
    timetable_rostered_grades,
)
from src.etl.transformers.ids import normalize_id_series
from src.etl.transformers.naming import MAX_CLASS_NAME_LENGTH, truncate_name
from src.etl.transformers.sources import get_source_file, normalize_source_config
from src.utils.helpers import normalize_columns

logger = logging.getLogger(__name__)

#: The session-key components that identify a blend's TIME SLOT, in key order:
#: the Classes ``source_columns`` role → the MyEd BC column it defaults to (plan 0053
#: S9, §5 #39 — a district whose export spells one differently sets that role).
#:
#: Resolved ONCE per detection (:func:`session_time_components`) and read by BOTH
#: :meth:`BlendedClassDetector._add_session_key` (which prefixes school + teacher)
#: and :meth:`BlendedClassDetector._block_label`, so a component added to the key
#: automatically reaches the class NAME and the two cannot drift. School and
#: teacher are deliberately NOT here: they are constant within a blend and already
#: carried by the Class ID.
SESSION_TIME_COMPONENTS: dict[str, str] = {
    "session_term": "term",
    "session_semester": "semester",
    "session_day": "day",
    "session_period": "period",
}


def session_time_components(source_columns: dict[str, Any]) -> tuple[str, ...]:
    """The resolved time-slot columns, in key order, from the Classes ``source_columns``."""
    return tuple(
        resolve_source_column(source_columns, role, default=default, previously=Previously.DEFAULT)
        for role, default in SESSION_TIME_COMPONENTS.items()
    )


#: Smallest course-title budget :meth:`BlendedClassDetector.create_name` will
#: hand to :func:`~src.etl.transformers.naming.truncate_name`.
#:
#: That function's ``len(result) <= max_len`` guarantee is only proven for
#: ``max_len >= 10`` (``tests/test_property_based.py``), and below 3 it inverts
#: outright — ``truncate_name("AAAA BBBB CCCC", 2)`` returns 12 characters.
#: create_name is its first caller with a COMPUTED budget, so a budget under
#: this floor drops the course segment entirely rather than leaving the cap to
#: a band nothing proves.
_MIN_COURSE_SEGMENT_BUDGET = 10


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
        load the schedule/course reference frames, build the TWO section→grade
        lookups (MODE, for qualification + naming; ENROLLABLE, for the
        suppression gate — see :meth:`_build_enrollable_grade_map`), resolve the
        working frame (ClassInformation or the deduplicated schedule fallback),
        drop teacherless sections, build session keys, then collect every valid
        blend into the returned :class:`BlendedDetection`.

        Both lookups are ONE grouping pass each over the schedule frame — the
        largest in the pipeline — so a blend never rescans it.
        """
        if class_info_df.empty:
            logger.info("No class info data available for blended class detection")
            return BlendedDetection.empty()

        field_map = mapping.get("field_map", {})
        teacher_id_col = context.get_teacher_id_col()
        # The SAME schedule grade column Classes' and Enrollments' subject splits
        # read — the enrollable map below must classify exactly their rows.
        grade_col = schedule_grade_column(field_map)
        components = session_time_components(mapping.get("source_columns") or {})

        loaded = self._load_reference_frames(mapping, context)
        if loaded is None:
            return BlendedDetection.empty()
        schedule_df, course_df = loaded

        mtid_to_grade = self._build_grade_map(schedule_df, grade_col=grade_col)
        mtid_to_enrollable_grades = self._build_enrollable_grade_map(schedule_df, grade_col=grade_col)
        course_title_map = self._build_course_title_map(course_df)
        teacher_name_map = self._build_teacher_name_map(schedule_df, field_map, teacher_id_col)

        working = self._resolve_working_frame(class_info_df, schedule_df, teacher_id_col)
        if working is None:
            return BlendedDetection.empty()

        working = self._drop_teacherless_sections(working, teacher_id_col)
        if working is None:
            return BlendedDetection.empty()

        working = self._add_session_key(working, teacher_id_col, components=components)
        return self._register_blends(
            working,
            field_map,
            teacher_id_col,
            mtid_to_grade,
            mtid_to_enrollable_grades,
            course_title_map,
            teacher_name_map,
            context,
            components=components,
        )

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
    def _add_session_key(working: pd.DataFrame, teacher_id_col: str, *, components: tuple[str, ...]) -> pd.DataFrame:
        """Join the available session components into a ``session_key`` column.

        Sections sharing a session_key (school + teacher + time slot) are
        candidates for blending. Only components present in the frame
        participate; they are stringified with NaN → "" first. ``components`` is
        :func:`session_time_components` — keyword-only with no default, because
        the key decides which sections become ONE class.
        """
        session_components = [SCHOOL_NUMBER, teacher_id_col, *components]
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
        mtid_to_enrollable_grades: dict[str, set[str]],
        course_title_map: dict[str, str],
        teacher_name_map: dict[str, str],
        context: TransformContext,
        *,
        components: tuple[str, ...],
    ) -> BlendedDetection:
        """Validate each multi-section session and collect it into the returned maps.

        A blend NONE of whose enrollable grades receives subject (timetable)
        rostering is SUPPRESSED — for every district, whether or not one
        configured a rostering scope. Such a class could only ever be emitted
        with a teacher and zero students: every pupil in it is rostered through
        the homeroom path instead, and the subject path (which applies the same
        split) produces no enrollment for any of them.

        Two sets decide it, and BOTH are derived, never configured:

        - ``rostered`` = :func:`~src.etl.transformers.grades.timetable_rostered_grades`
          — the configured scope when a district set one, else the CEDS
          complement of ``homeroom_grades``. Keyed to the RESOLVED scope, never
          to which config key produced it (two keys can produce one), which is
          why ``None`` is read as "nothing configured" and not as "suppress
          nothing";
        - ``enrollable`` = the union of the blend's sections' schedule-row
          grades (:meth:`_build_enrollable_grade_map`) — PER ROW, not the
          per-section MODE. This is the ROW-SET IDENTITY invariant: the gate
          must classify exactly the rows ``split_by_homeroom_grades`` will,
          including blank/NaN grades (which become ``"UG"``, are timetable-side,
          and are real students). Gating on the mode instead would suppress
          blends that DO have a student, re-keying them to a per-section class
          — ``Classes.csv`` would GROW and a live Class ID would move.

        **Suppression happens BEFORE the first ``result.*`` write, and that
        ordering is load-bearing.** ``class_map``/``teacher_map`` are populated
        before the grade range is known, so skipping later would leave the class
        referenced by ``assign_class_ids`` and the co-teacher path while
        ``_emit_missing_blended_classes`` (which iterates ``metadata``) omitted
        it — orphan Class IDs in Enrollments.csv, the exact partner-ingest
        rejection commit ``e187ac8`` fixed.

        The rule is NECESSARY, not sufficient — it is exactly the subject path's
        class-EXISTENCE condition. Whether an in-scope row becomes a student
        ENROLLMENT additionally depends on ``filter_to_active``, so a SURVIVING
        blend can still end up studentless when its in-scope pupils are all
        inactive (tracked on the roadmap). Intersecting this gate with
        ``context.active_student_ids`` is NOT a safe extension: ``filter_to_active``
        fails SAFE on an empty roster (keeps everyone) while a gate doing so
        would fail CLOSED (suppress every blend).

        ``validate`` and :meth:`get_grade_range` deliberately keep reading the
        MODE map: blend QUALIFICATION and blend NAMES are untouched by this
        rule, so no district's blend set changes for a reason other than
        "nobody could ever be in it".

        The course-code column is resolved ONCE here (via the shared
        :func:`~src.etl.transformers.course_codes.resolve_course_code_column`,
        which owns the alias precedence for every consumer) and threaded into
        :meth:`create_name`. Its absence is warned about ONCE, after the loop
        and only when at least one blend was actually named: resolving per blend
        would have emitted 411 identical warnings on SD40's FY2026 run, and
        warning before the loop would have claimed degraded names on a district
        that produced no blended classes at all.
        """
        teacher_positions = self._teacher_positions(working, teacher_id_col)
        result = BlendedDetection.empty()
        homeroom_grades = context.global_config.get("homeroom_grades", [])
        timetable_scope = resolve_timetable_scope(context.global_config, homeroom_grades)
        rostered = timetable_rostered_grades(homeroom_grades, timetable_scope=timetable_scope)

        course_code_col = resolve_course_code_column(working)

        count = 0
        suppressed = 0
        for session_key, group in working.groupby("session_key"):
            if len(group) <= 1:
                continue

            if not self.validate(group, mtid_to_grade):
                continue

            # `validate` already required 2+ sections resolvable in the MODE
            # map, and every MT ID in that map is in the enrollable map too
            # (same frame, no dropna), so this union is never empty — the rule
            # can never suppress on "grades unknown".
            enrollable = self._enrollable_grades(group, mtid_to_enrollable_grades)
            if not (enrollable & rostered):
                suppressed += 1
                if timetable_scope is None:
                    logger.info(
                        "[Blended Classes] Suppressed blend for session '%s': every grade in this "
                        "blend %s is a homeroom grade, so no student in it would ever receive a "
                        "subject enrollment",
                        session_key,
                        sorted(enrollable),
                    )
                else:
                    logger.info(
                        "[Blended Classes] Suppressed blend for session '%s': none of its grades %s "
                        "is inside the configured timetable rostering scope %s",
                        session_key,
                        sorted(enrollable),
                        sorted(timetable_scope),
                    )
                continue

            blended_id = f"BLENDED_{session_key}_{context.school_year}"
            all_mt_ids = sorted(set(group[MASTER_TIMETABLE_ID].tolist()))

            for mt_id in all_mt_ids:
                result.class_map[mt_id] = blended_id

            result.teacher_map[blended_id] = self._collect_teachers(teacher_positions, all_mt_ids)

            grade_str = self.get_grade_range(group, mtid_to_grade)
            class_name = self.create_name(
                group,
                field_map,
                grade_str,
                course_title_map,
                context,
                course_code_col=course_code_col,
                teacher_name=self._session_teacher_name(group, teacher_id_col, teacher_name_map),
                session_components=components,
            )

            result.metadata[blended_id] = {
                "Name": class_name,
                "Grade": grade_str,
                "School ID": group[SCHOOL_NUMBER].iloc[0] if SCHOOL_NUMBER in group.columns else "",
                "Original_MT_IDs": all_mt_ids,
            }
            count += 1

        logger.info(f"[Blended Classes] Detection completed: {count} blended classes identified")
        # Warned HERE, not before the loop: the column is resolved once (so a
        # 411-blend district gets one warning, not 411), but a warning about
        # names is only true if names were built. A district with no blended
        # sessions at all has nothing degraded to report, and this line lands in
        # the log partners are asked to send to support.
        if count and course_code_col is None:
            logger.warning(
                "Missing '%s' (and its '%s' fallback) in the blended-detection frame; "
                "the %d blended class name(s) above omit their course titles.",
                COURSE_CODE,
                DISTRICT_COURSE_CODE,
                count,
            )
        if suppressed:
            logger.info(
                "[Blended Classes] %d blend(s) suppressed: no grade in them receives subject "
                "rostering, so each could only ever be emitted with a teacher and zero students",
                suppressed,
            )
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
    @staticmethod
    def _blend_grades(session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> set[str]:
        """The distinct CEDS grades a candidate blend spans (THE single spelling).

        One derivation consumed by both sites that need it — :meth:`validate`
        (2+ grades qualifies a blend) and :meth:`get_grade_range` (the displayed
        range). MT IDs absent from ``mtid_to_grade``, and blank grades,
        contribute nothing.

        NOTE these are per-section MODE grades (see :meth:`_build_grade_map`) —
        the set says what the section is MOSTLY, not who is in it. That is why
        the suppression gate in :meth:`_register_blends` reads
        :meth:`_enrollable_grades` instead: qualification and naming are a
        question about the section, suppression is a question about its pupils.
        """
        grades: set[str] = set()
        for mt_id in session_group[MASTER_TIMETABLE_ID].unique():
            grade = mtid_to_grade.get(mt_id)
            if grade:
                grades.add(grade_to_ceds(grade))
        return grades

    @staticmethod
    def _enrollable_grades(session_group: pd.DataFrame, mtid_to_enrollable_grades: dict[str, set[str]]) -> set[str]:
        """Every CEDS grade the candidate blend's schedule ROWS carry.

        The union of :meth:`_build_enrollable_grade_map`'s per-section sets over
        the group's sections — deliberately NOT :meth:`_blend_grades`, which
        reports per-section MODE grades and therefore answers "what is this
        section mostly?" rather than "who is actually in it?".

        The two must not be merged: the gate needs the row set, while
        ``validate`` and :meth:`get_grade_range` need the mode (merging them
        would change which blends qualify and what they are named).
        """
        grades: set[str] = set()
        for mt_id in session_group[MASTER_TIMETABLE_ID].unique():
            grades |= mtid_to_enrollable_grades.get(mt_id, set())
        return grades

    def validate(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> bool:
        """A valid blend requires 2+ unique sections with 2+ distinct CEDS grades."""
        if len(session_group[MASTER_TIMETABLE_ID].unique()) <= 1:
            return False
        return len(self._blend_grades(session_group, mtid_to_grade)) >= 2

    def get_grade_range(self, session_group: pd.DataFrame, mtid_to_grade: dict[str, str]) -> str:
        grades = self._blend_grades(session_group, mtid_to_grade)
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
        *,
        course_code_col: Optional[str],
        teacher_name: str,
        session_components: tuple[str, ...],
    ) -> str:
        """Build the blend's display name from the parts the frame actually has.

        Composed as ``<Teacher> <Course titles> (Block <slot>) (<Grades>)
        <Year>`` — the regular path's word order plus the grade range, so a
        district's blended and subject class lists read alike.

        ``teacher_name`` is the schedule-resolved name
        (:meth:`_build_teacher_name_map`) and takes precedence; ``""`` falls
        back to :meth:`_teacher_from_frame`. Keyword-only with no default so no
        caller silently re-acquires the frame-only behaviour this replaced —
        which produced blended names with no teacher on every bundled district
        while their regular names carried one.

        ``course_code_col`` is the resolved course-code column (see
        :func:`~src.etl.transformers.course_codes.resolve_course_code_column`),
        or ``None`` when the frame carries neither spelling — in which case the
        course segment is SKIPPED, exactly as the teacher segment below is
        skipped when its column is absent.

        Skipping is deliberately NOT the ``"Unknown Course"`` substitution the
        per-code ``.get`` default performs: that default answers "this code has
        no title", a different question, and printing it for every section of a
        district whose export simply omits the column would put a fabricated
        title on the partner's class list. Keyword-only with no default so no
        caller can silently re-acquire the unguarded lookup this replaced (it
        raised ``KeyError`` and killed the run at the Classes entity).

        ``session_components`` is :func:`session_time_components` — the SAME
        tuple the session key was built from, so the block label names exactly
        the slot the blend was grouped on.
        """
        teacher = teacher_name.strip() or self._teacher_from_frame(session_group, field_map)
        block = self._block_label(session_group, session_components)

        head_parts = [teacher] if teacher else []
        tail_parts = []
        if block:
            tail_parts.append(f"(Block {block})")
        if grade_str:
            tail_parts.append(f"({grade_str})")
        tail_parts.append(str(context.school_year))

        course_segment = ""
        if course_code_col is not None:
            unique_titles = sorted(
                {course_title_map.get(code, "Unknown Course") for code in session_group[course_code_col]}
            )
            if unique_titles:
                course_segment = " / ".join(unique_titles)

        if course_segment:
            # The one unbounded segment is budgeted against what the IDENTIFYING
            # parts already cost, so truncation eats course text instead of the
            # block/grades/year tail (which used to vanish on 22% of SD54's
            # blends, leaving a class with no grade signal anywhere — `Grade` is
            # deliberately blank on a blended row). The +1 is the space that
            # joins this segment to its neighbours.
            spent = len(" ".join(head_parts + tail_parts))
            budget = MAX_CLASS_NAME_LENGTH - spent - 1
            course_segment = "" if budget < _MIN_COURSE_SEGMENT_BUDGET else truncate_name(course_segment, budget)

        name_parts = head_parts + ([course_segment] if course_segment else []) + tail_parts

        full_name = " ".join(name_parts).strip()
        if not full_name or len(name_parts) <= 1:
            full_name = f"Blended Class {grade_str} {context.school_year}".strip()

        # Unconditional, so the cap is guaranteed by the one call that owns it
        # in EVERY branch — including the budget-floor and fallback paths.
        return truncate_name(full_name)

    @staticmethod
    def _teacher_from_frame(session_group: pd.DataFrame, field_map: dict[str, Any]) -> str:
        """The blend's teacher name as the GROUPED frame carries it, or ``""``.

        The documented FALLBACK to the schedule map (see :meth:`create_name`),
        not the preferred source: no bundled ``ClassInformation`` carries a
        teacher-name column, so this path serves the schedule-fallback frame
        (where it is the same fact) and a district whose class-info export
        happens to include one.
        """
        teacher_col = BlendedClassDetector._teacher_name_column(field_map)
        if teacher_col is None or teacher_col not in session_group.columns:
            return ""
        value = session_group[teacher_col].iloc[0]
        return str(value).strip() if pd.notna(value) else ""

    @staticmethod
    def _teacher_name_column(field_map: dict[str, Any]) -> Optional[str]:
        """The schedule's teacher-name column from the Classes ``Name`` block, or ``None``.

        The spaced YAML authoring key (see ``ClassTransformer._assign_class_names``),
        through the one resolver; ``None`` when ``Name`` is not a block at all.
        """
        name_config = field_map.get("Name", {})
        if not isinstance(name_config, dict):
            return None
        return resolve_source_column(
            name_config, "teacher last name", default=TEACHER_NAME, previously=Previously.AS_CONFIGURED
        )

    @staticmethod
    def _block_label(session_group: pd.DataFrame, components: tuple[str, ...]) -> str:
        """The blend's time slot as a space-joined string, or ``""``.

        Built from the components PRESENT in the frame — ``_add_session_key``
        keys on the available subset only, so indexing a column a district's
        export omits would raise ``KeyError`` and kill the Classes entity.
        Every row of the group shares these values for that subset (they are
        the group key), so the first row answers for all of them.

        Values are joined and never labelled individually: the components'
        meanings differ per district (one SD54 school numbers days and letters
        periods; another does the reverse), so a "Day A Period 1" rendering
        would assert an order the data does not guarantee. The ``Block`` prefix
        that :meth:`create_name` wraps this in carries the meaning instead.
        """
        available = [col for col in components if col in session_group.columns]
        values = (str(session_group[col].iloc[0]).strip() for col in available)
        return " ".join(value for value in values if value)

    @staticmethod
    def _build_teacher_name_map(
        schedule_df: pd.DataFrame, field_map: dict[str, Any], teacher_id_col: str
    ) -> dict[str, str]:
        """Map each teacher id to the display name the SCHEDULE carries.

        The regular class path reads its teacher name off the schedule (base
        declares ``"teacher last name": "Teacher Name"``, inherited by every
        bundled config; the staff merge contributes only ``LAST_NAME``, which no
        config selects). Building the blended name off the same frame is what
        makes a district's blended and regular names agree, and it costs one
        grouping pass over a frame :meth:`_load_reference_frames` already holds.

        The id is normalized on THIS side too. ``_load_reference_frames``
        normalizes only the Master Timetable ID, while ClassInformation's
        teacher id arrives already normalized — so a raw key here would miss on
        any padding difference, and the miss is indistinguishable from "this
        district has no teacher name", i.e. it would fail silently.

        Missing columns → ``{}``; the segment is then simply omitted.
        """
        teacher_col = BlendedClassDetector._teacher_name_column(field_map)
        if teacher_col is None or teacher_col not in schedule_df.columns or teacher_id_col not in schedule_df.columns:
            return {}

        pairs = schedule_df[[teacher_id_col, teacher_col]].dropna().drop_duplicates(subset=[teacher_id_col])
        normalized_ids = normalize_id_series(pairs[teacher_id_col])
        return {
            teacher_id: str(name).strip()
            for teacher_id, name in zip(normalized_ids, pairs[teacher_col])
            if teacher_id and teacher_id.lower() != "nan" and str(name).strip()
        }

    @staticmethod
    def _session_teacher_name(group: pd.DataFrame, teacher_id_col: str, teacher_name_map: dict[str, str]) -> str:
        """The blend's teacher name from the schedule map, or ``""`` if unknown.

        The whole group shares one teacher id — it is a session-key component —
        so the first row answers for all of them. Normalized on LOOKUP for the
        same reason :meth:`_build_teacher_name_map` normalizes on build: the two
        frames reach this point with differently-padded ids.
        """
        if not teacher_name_map or teacher_id_col not in group.columns:
            return ""
        return teacher_name_map.get(normalize_id_series(group[teacher_id_col]).iloc[0], "")

    # ------------------------------------------------------------------
    # Reference lookup tables
    # ------------------------------------------------------------------
    @staticmethod
    def _build_grade_map(schedule_df: pd.DataFrame, *, grade_col: str) -> dict[str, str]:
        """Map each Master Timetable ID to its most common grade.

        Uses mode (most frequent grade) to handle cases where the same
        section has students from multiple grades in the schedule data.
        ``grade_col`` is :func:`~src.etl.transformers.grades.schedule_grade_column`.
        """
        if MASTER_TIMETABLE_ID in schedule_df.columns and grade_col in schedule_df.columns:
            pairs = schedule_df[[MASTER_TIMETABLE_ID, grade_col]].dropna()
            # Use most frequent grade per MT ID (mode) to handle multi-grade enrollment
            mode = pairs.groupby(MASTER_TIMETABLE_ID)[grade_col].agg(lambda x: x.mode().iloc[0])
            return mode.to_dict()  # type: ignore[return-value]
        logger.warning(f"Missing '{MASTER_TIMETABLE_ID}' or '{grade_col}' in student schedule.")
        return {}

    @staticmethod
    def _build_enrollable_grade_map(schedule_df: pd.DataFrame, *, grade_col: str) -> dict[str, set[str]]:
        """Map each Master Timetable ID to EVERY CEDS grade its schedule rows carry.

        A SIBLING OF ``split_by_homeroom_grades``, NOT of :meth:`_build_grade_map`.
        Two differences from the mode map, and both are load-bearing:

        - **no ``dropna``.** A blank/NaN grade converts to ``"UG"``, ``"UG"`` is
          not a homeroom grade, so a blank-grade schedule row SURVIVES the subject
          split and is a real student in the blend. Dropping it here would
          suppress a blend that has a pupil, re-key that pupil to a per-section
          class and GROW ``Classes.csv`` — the opposite of what the suppression
          rule promises. This is the ROW-SET IDENTITY invariant;
        - grades derived through
          :func:`~src.etl.transformers.grades.ceds_grade_series`, i.e. the very
          function the subject split uses, so the two cannot drift apart in
          their null handling.

        Cheap by construction: the distinct ``(section, grade)`` pairs are taken
        FIRST, so the per-row conversion and the grouping both run over a frame
        bounded by sections × distinct grades rather than over the raw schedule
        (hundreds of thousands of rows for the largest district).

        ``grade_col`` is :func:`~src.etl.transformers.grades.schedule_grade_column`
        — the column ``split_by_homeroom_grades`` is handed on the subject side,
        which the row-set identity requires.

        Returns ``{}`` when the columns are absent, WITHOUT a second warning:
        :meth:`_build_grade_map` has already warned about the same two columns
        on the same frame, and with an empty mode map ``validate`` rejects every
        session, so the gate this feeds is unreachable.
        """
        if MASTER_TIMETABLE_ID in schedule_df.columns and grade_col in schedule_df.columns:
            pairs = schedule_df[[MASTER_TIMETABLE_ID, grade_col]].drop_duplicates()
            per_row = ceds_grade_series(pairs[grade_col])
            return {mt_id: set(grades) for mt_id, grades in per_row.groupby(pairs[MASTER_TIMETABLE_ID])}
        return {}

    @staticmethod
    def _build_course_title_map(course_df: pd.DataFrame) -> dict[str, str]:
        if COURSE_CODE in course_df.columns and COURSE_TITLE in course_df.columns:
            pairs = course_df[[COURSE_CODE, COURSE_TITLE]].dropna().drop_duplicates(subset=[COURSE_CODE])  # type: ignore[call-overload]
            return pd.Series(pairs[COURSE_TITLE].values, index=pairs[COURSE_CODE]).to_dict()  # type: ignore[return-value]
        logger.warning(f"Missing '{COURSE_CODE}' or '{COURSE_TITLE}' in course info.")
        return {}
