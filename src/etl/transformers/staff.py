"""Staff entity transformer — roster merge, departed-staff exclusion, row filters.

A MyEd BC staff GDE is UNFILTERED: it carries departed employees alongside
current ones, distinguished only by a status column. Shipping those rows creates
active SpacesEDU users for people who have left the district (SD74 district
report, 2026-09-08 drop: 13 Inactive of 163; SD40 32 of 1129; SD60 2 of 82;
Unity Christian 206 of 306).

The ``enroll_status`` machinery in :mod:`~src.etl.transformers.base` cannot serve
here: it is keyed on the *Students* field_map, and a staff export carries no
withdraw date for its per-row fallback. So this module owns a narrower rule —
see :meth:`StaffTransformer.filter_departed_staff`.

Beyond that universal rule, an entity may declare ``row_filters`` for a narrowing
only its own district needs (SD83 keeps only rows whose repurposed ``Prefix``
states a real role). The two are complementary, not alternatives: employment is
decided on the DATA for every district, while ``row_filters`` is opt-in config.
Role is ``map_role`` over a teaching flag by default, or ``normalize_staff_role``
over a column that states the role outright.

**No role is ever inferred from the absence of one** (plan 0051). A teaching
flag of ``"N"`` used to mean ``administrator``, which is a real privilege level
in SpacesEDU — so support staff were silently granted it at every district
(44.9% of SD40's export; 60% of the staff Unity Christian ships, found by that
school's own network administrator in their production tenant). Today a row
whose role the export does not state is RESCUED if the person is
teacher-of-record on a section, and otherwise DROPPED —
:meth:`StaffTransformer.resolve_staff_roles`.
"""

import logging
from typing import Any

import pandas as pd

from src.etl.column_names import STAFF_SOURCEID, STAFF_STATUS
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext
from src.etl.transformers.ids import is_blank_series, normalize_id_series

logger = logging.getLogger(__name__)

#: The status value that keeps a staff row (normalized: trimmed, lower-cased).
ACTIVE_STATUS_VALUE = "active"

#: The ``source_files`` roles whose teacher-id column ASSERTS a teaching
#: assignment, consulted by :meth:`StaffTransformer._teacher_of_record_ids`.
#:
#: ``student_demographic`` belongs here even though it is a student roster: its
#: teacher-id column names the pupil's HOMEROOM teacher, and ``enrollments.
#: _homeroom_enrollments`` builds real teacher rows from exactly that column.
#: Omitting it stranded a live Unity Christian homeroom teacher who holds no
#: timetabled section — measured, not theorised.
#:
#: Still excluded, and these are the load-bearing exclusions: ``staff_info``
#: (it lists every employee, so reading it would make each row its own evidence
#: of teaching, rescuing the entire export) and ``course_info`` (a catalogue of
#: courses, which asserts nothing about who delivers them). Widen only against
#: a real export.
TEACHING_ASSIGNMENT_SOURCE_ROLES: tuple[str, ...] = (
    "student_schedule",
    "class_info",
    "student_demographic",
)

#: The status vocabulary this filter RECOGNISES (normalized). Engaging ONLY on a
#: recognised vocabulary is the safety guard, not a formality: a district
#: spelling its statuses some other way ("A"/"I", "Employed"/"Terminated") would
#: match no value at all, and a blind ``== "active"`` would empty Staff.csv
#: entirely. An unrecognised vocabulary therefore WARNS and ships every row —
#: shipping a few departed staff is recoverable, deleting the whole roster is
#: not. Widen this set only against a real district export.
KNOWN_STATUS_VALUES = frozenset({ACTIVE_STATUS_VALUE, "inactive"})


class StaffTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})

        working = self._merge_roster(working, mapping, context)
        # AFTER _merge_roster, because that helper REPLACES `working` with a
        # frame rebuilt from context.raw_data when a roster file is configured;
        # filtering first would be silently discarded on that path. BEFORE the
        # field map, so an excluded staff member never reaches output.
        working = self.filter_departed_staff(working, mapping)

        # Then the district's OWN opt-in narrowing (SD83: a Prefix that states a
        # real role). Same post-merge placement, and for the same reason — Family
        # filters at transform entry only because it has no such rebuild. Pinned
        # by TestStaffRowFilters::test_filters_apply_AFTER_the_roster_merge.
        working = self.apply_row_filters(working, mapping.get("row_filters", []), "Staff")

        result = self.apply_field_map(working, result, field_map, "Staff", context)
        # AFTER the field map, because the rule is about the RESOLVED Role — the
        # one place both role transforms have already had their say. Doing it on
        # the source frame would need a third spelling of "which column is the
        # role", one per transform.
        return self.resolve_staff_roles(result, context)

    def resolve_staff_roles(self, result: pd.DataFrame, context: TransformContext) -> pd.DataFrame:
        """Rescue unroled staff who demonstrably teach; drop the rest (plan 0051).

        Runs on the OUTPUT frame, where ``Role`` holds whatever the district's
        configured transform resolved. A row whose ``Role`` is not one of
        :attr:`~BaseTransformer.STAFF_ROLES` has no publishable role — either
        ``map_role`` saw a teaching flag that was not ``"y"``, or
        ``normalize_staff_role`` raised on a value it did not recognise.

        Two things happen to those rows, in order:

        1. **Rescue.** If the person is teacher-of-record on a real section
           (:meth:`_teacher_of_record_ids`) they become ``teacher``. MyEd BC's
           teaching flag is demonstrably stale for some teachers — three of
           Unity Christian's carry 26, 26 and 16 sections while flagged ``"N"``,
           and without this 68 of that school's 186 sections would lose their
           only teacher. Positive evidence of teaching outranks a flag that
           merely failed to mention it.
        2. **Drop.** Everyone still unroled is removed. They are NOT shipped as
           ``administrator`` (the defect this whole change exists to remove) and
           NOT shipped with a blank ``Role`` (which the Advanced CSV contract
           does not accept). A district that wants its administrators rostered
           must state who they are — see :meth:`~BaseTransformer.map_role`.

        The rescue is deliberately one-directional: it can only ADD a teacher,
        never re-role or remove someone the district's own export already
        placed. An export that STATES ``administrator`` is always believed.

        PII rule: counts only — never a name, an email or an id.
        """
        if "Role" not in result.columns or result.empty:
            return result

        roles = result["Role"].astype(str).str.strip().str.lower()
        unroled = ~roles.isin(self.STAFF_ROLES)
        if not bool(unroled.any()):
            return result

        # Deliberately NOT writing `roles` back over the column: the lower-cased
        # series is for COMPARISON only. A config that maps `Role` from a bare
        # column (no transform) keeps whatever casing the district sent, exactly
        # as it does today — this method decides who ships, not how they read.
        result = result.copy()

        rescued = pd.Series(False, index=result.index)
        teaching_ids = self._teacher_of_record_ids(context)
        if teaching_ids and "User ID" in result.columns:
            user_ids = normalize_id_series(result["User ID"])
            rescued = unroled & user_ids.isin(teaching_ids)
            result.loc[rescued, "Role"] = self.STAFF_ROLE_TEACHER

        drop = unroled & ~rescued
        dropped = int(drop.sum())
        if int(rescued.sum()):
            logger.info(
                f"[Staff] {int(rescued.sum())} staff row(s) carry no stated role but are "
                f"teacher-of-record on a section — published as "
                f"'{self.STAFF_ROLE_TEACHER}' rather than dropped (the district's teaching "
                f"flag is out of date for them)."
            )
        if dropped:
            logger.info(
                f"[Staff] Excluded {dropped} of {len(result)} staff row(s) whose role this export "
                f"does not state and who hold no section. They are NOT published as "
                f"'{self.STAFF_ROLE_ADMINISTRATOR}' — a teaching flag says who TEACHES, never who "
                f"ADMINISTERS. To roster administrators, map 'Role' to a column that states it "
                f"(see docs/developer/adding-district.md)."
            )
        return result[~drop].copy()

    def _teacher_of_record_ids(self, context: TransformContext) -> set[str]:
        """Normalized staff ids with a teaching assignment in THIS run's input.

        Resolved from the **Classes** entity via ``context.entity_mappings``, not
        — Staff declares only ``staff_info``, and reading that would make every
        row its own evidence of teaching. Same cross-entity config read as
        ``context.get_teacher_id_col`` / ``get_demo_student_col``; config-driven
        end to end, so no filename or column name is spelled here.

        Only the roles in :data:`TEACHING_ASSIGNMENT_SOURCE_ROLES` are consulted
        — see that constant for why ``staff_info`` and ``course_info`` are not
        among them.

        Safe before Classes runs: ``raw_data`` is filled by the extractor ahead
        of every transformer. An empty set is a legitimate answer (a config
        declaring none of these roles rescues nobody) and never an error.
        """
        classes_config = context.entity_mappings.get("Classes", {}) or {}
        sources = self.normalize_source_config(classes_config.get("source_files", {}))
        teacher_id_col = context.get_teacher_id_col()

        found: set[str] = set()
        for role in TEACHING_ASSIGNMENT_SOURCE_ROLES:
            frame = context.raw_data.get(sources.get(role, ""))
            if frame is None or frame.empty or teacher_id_col not in frame.columns:
                continue
            values = normalize_id_series(frame[teacher_id_col])
            found.update(values[~is_blank_series(frame[teacher_id_col])].unique())
        return found

    @classmethod
    def filter_departed_staff(cls, working: pd.DataFrame, mapping: dict[str, Any]) -> pd.DataFrame:
        """Keep only currently-employed staff, when the export says who they are.

        Engages only when BOTH hold, and passes every row through otherwise:

        1. the resolved status column is PRESENT (a district whose export has no
           status column has told us nothing, so there is nothing to decide); and
        2. its non-blank values are a subset of :data:`KNOWN_STATUS_VALUES` — the
           fail-open guard described in that constant's docstring.

        A BLANK status is DROPPED (owner decision, 2026-09-09): unlike a student,
        a staff record has no withdraw date to fall back on, so a missing status
        is not a positive signal of employment. Note this is only reachable when
        the rest of the column IS a recognised vocabulary — an all-blank column
        has an empty observed set, keeps nobody, and so trips the floor below.

        Last line of defence: a filter that would keep ZERO rows never applies.
        An empty Staff.csv is a far worse partner-facing outcome than a few
        surplus users, and in practice means the column was misread rather than
        that a district employs nobody.

        PII rule: counts and status VOCABULARY only — never a name or an email.
        """
        status_col = cls.resolve_status_column(mapping)
        if status_col not in working.columns:
            return working

        values = normalize_id_series(working[status_col]).str.lower()
        blank = is_blank_series(working[status_col])
        observed = set(values[~blank].unique())

        unrecognised = observed - KNOWN_STATUS_VALUES
        if unrecognised:
            logger.warning(
                f"[Staff] '{status_col}' holds unrecognised value(s) {sorted(unrecognised)} — "
                f"expected {sorted(KNOWN_STATUS_VALUES)}. Keeping ALL {len(working)} staff row(s) rather than "
                f"risk emptying Staff.csv; departed staff may ship as active users until this mapping is taught "
                f"the district's vocabulary."
            )
            return working

        keep = values == ACTIVE_STATUS_VALUE
        kept = int(keep.sum())
        if kept == 0:
            logger.warning(
                f"[Staff] '{status_col}' marks none of {len(working)} staff row(s) as "
                f"'{ACTIVE_STATUS_VALUE}' — keeping all of them rather than delivering an empty Staff.csv. "
                f"Check the source export."
            )
            return working

        excluded = len(working) - kept
        if excluded:
            logger.info(
                f"[Staff] Excluded {excluded} of {len(working)} staff row(s) not marked "
                f"'{ACTIVE_STATUS_VALUE}' in '{status_col}' (departed staff must not ship as active users)."
            )
        return working[keep].copy()

    @staticmethod
    def resolve_status_column(mapping: dict[str, Any]) -> str:
        """Resolve the employment-status source column (Configurable Columns rule).

        The status column has no output counterpart, so it resolves through the
        entity-level ``source_columns`` block rather than the field_map — the
        ``student_courses.py`` auxiliary-input pattern. Defaults to the canonical
        MyEd BC spelling (:data:`~src.etl.column_names.STAFF_STATUS`); a blank or
        non-string override falls back to it rather than resolving to nothing.
        """
        aux = mapping.get("source_columns") or {}
        override = aux.get("staff_status")
        if override is None:
            return STAFF_STATUS
        return str(override).strip().lower() or STAFF_STATUS

    def _merge_roster(self, working: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        """Merge staff with roster to add 'staff sourceid' when available."""
        source_config = mapping.get("source_files", {})
        normalized = self.normalize_source_config(source_config)
        teacher_id_col = context.get_teacher_id_col()

        if len(normalized) <= 1:
            return working

        staff_filename = normalized.get("staff_info", "")
        roster_filename = list(normalized.values())[1] if len(normalized) > 1 else ""

        staff_df = context.raw_data.get(staff_filename, pd.DataFrame())
        roster_df = context.raw_data.get(roster_filename, pd.DataFrame())

        if (
            not staff_df.empty
            and not roster_df.empty
            and teacher_id_col in staff_df.columns
            and STAFF_SOURCEID in roster_df.columns
        ):
            working = staff_df.merge(
                roster_df[[teacher_id_col, STAFF_SOURCEID]].drop_duplicates(subset=[teacher_id_col]),  # type: ignore[call-overload]
                on=teacher_id_col,
                how="left",
            )
            working = self.normalize_columns(working)

        return working
