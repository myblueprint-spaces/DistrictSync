"""Staff entity transformer — optional roster merge, departed-staff exclusion.

A MyEd BC staff GDE is UNFILTERED: it carries departed employees alongside
current ones, distinguished only by a status column. Shipping those rows creates
active SpacesEDU users for people who have left the district (SD74 district
report, 2026-09-08 drop: 13 Inactive of 163; SD40 32 of 1129; SD60 2 of 82;
Unity Christian 206 of 306).

The ``enroll_status`` machinery in :mod:`~src.etl.transformers.base` cannot serve
here: it is keyed on the *Students* field_map, and a staff export carries no
withdraw date for its per-row fallback. So this module owns a narrower rule —
see :meth:`StaffTransformer.filter_departed_staff`.
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

        return self.apply_field_map(working, result, field_map, "Staff", context)

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
