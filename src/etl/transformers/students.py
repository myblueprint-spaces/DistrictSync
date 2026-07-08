"""Student entity transformer — enrollment status, active filtering, email generation."""

import logging
from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext

logger = logging.getLogger(__name__)


class StudentTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})

        self._determine_enrollment_status(working, field_map)
        working = self._filter_active(working, field_map)
        working = self._collapse_cross_enrollment(working, field_map, context)
        result["EnrollStatus"] = working["EnrollStatus"]
        self._generate_emails(working, result, field_map)

        result = self.apply_field_map(working, result, field_map, "Students", context)
        if "Date of Birth" in result.columns:
            result["Date of Birth"] = result["Date of Birth"].apply(self.normalize_iso_date)
        self._coalesce_required_names(result)

        # Publish the active roster (zero-orphan invariant). `result` is already
        # filtered to active-only, so this IS the Students.csv `User ID` set by
        # construction — Classes (homeroom) and Enrollments (homeroom + subject)
        # filter their student rows against it so none references a non-rostered
        # student. Same value space as the schedule's `Student ID` (pupil
        # numbers); normalized so the cross-frame join matches.
        if "User ID" in result.columns:
            context.active_student_ids = set(result["User ID"].astype(str).str.strip())

        return result

    @staticmethod
    def _coalesce_required_names(result: pd.DataFrame) -> None:
        """Fill blank First/Last Name from the preferred-name columns, in place.

        First Name and Last Name are required by the Advanced CSV spec. Some
        districts (e.g. SD74) map the primary name to the Usual/preferred columns,
        which can be blank, and map the Preferred-name output to the populated Legal
        columns. When that leaves a required name blank but a preferred-name value is
        available, fall back to it so the required field is never empty needlessly.
        """
        for primary, fallback in (
            ("First Name", "Preferred First Name"),
            ("Last Name", "Preferred Last Name"),
        ):
            if primary not in result.columns or fallback not in result.columns:
                continue
            is_blank = result[primary].isna() | result[primary].astype(str).str.strip().str.lower().isin(["", "nan"])
            result.loc[is_blank, primary] = result.loc[is_blank, fallback]

    def _collapse_cross_enrollment(
        self, working: pd.DataFrame, field_map: dict[str, Any], context: TransformContext
    ) -> pd.DataFrame:
        """Collapse duplicate ``User ID`` rows to one, keeping the home-school row.

        Opt-in via ``global_config.cross_enrollment`` (``collapse`` +
        ``home_school_column``). A pupil Active at two schools has one Students
        row per school (identical demographics bar School Number); this keeps a
        single row — the one whose School equals the student's home school, else
        the first — so Students.csv carries one identity per pupil. Enrollments
        are built from the schedule and matched by User ID, so class enrolments
        at BOTH schools are unaffected. Off by default → every other district is
        unchanged.

        Source column names resolve from the Students ``field_map`` (Configurable
        Columns): ``User ID`` and ``SchoolCode``. Fail-loud (validate at
        boundary): a configured ``home_school_column`` absent from the frame
        raises ``ValueError``. Never drops a student entirely (always ≥ 1 row per
        User ID). Logs only the collapsed COUNT (no PII).
        """
        cc = (context.global_config or {}).get("cross_enrollment") or {}
        if not cc.get("collapse"):
            return working

        user_id_col = str(field_map.get("User ID", "")).strip().lower()
        school_col = str(field_map.get("SchoolCode", "")).strip().lower()
        home_col = str(cc.get("home_school_column", "")).strip().lower()

        if home_col not in working.columns:
            raise ValueError(
                f"[Students] cross_enrollment home_school_column '{home_col}' not found in "
                f"source columns. Available: {sorted(working.columns)}"
            )

        before = len(working)
        working = working.copy()
        school_norm = working[school_col].astype(str).str.strip()
        home_norm = working[home_col].astype(str).str.strip()
        # Priority 0 = home-school row (School == Home School), 1 otherwise. A
        # stable sort brings the home row first within each User ID group, so
        # keep="first" retains it (or the first row when no home match exists).
        working["__ce_priority"] = (school_norm != home_norm).astype(int)
        working = working.sort_values(by=[user_id_col, "__ce_priority"], kind="stable")
        working = working.drop_duplicates(subset=[user_id_col], keep="first")
        working = working.drop(columns="__ce_priority")
        after = len(working)
        if before != after:
            logger.info(f"[Students] cross_enrollment collapsed {before - after} duplicate rows to home school")
        return working

    def _determine_enrollment_status(self, working: pd.DataFrame, field_map: dict[str, Any]) -> None:
        """Set the 'EnrollStatus' column in-place via the shared base predicate.

        Source column names (status / withdraw date) and the active-value set
        resolve from the Students ``EnrollStatus`` config (Configurable
        Columns); MyEd BC defaults apply when unconfigured. ``Active`` and
        ``PreReg`` are both retained by default (the Advanced CSV spec's expected
        ``EnrollStatus`` values; overridable via ``active_values``). The live
        status value wins; the withdraw date is only a fallback for rows with no
        status value. See ``BaseTransformer.compute_enroll_status``.
        """
        working["EnrollStatus"] = self.compute_enroll_status(working, field_map)

    @classmethod
    def _filter_active(cls, working: pd.DataFrame, field_map: dict[str, Any]) -> pd.DataFrame:
        """Keep rows whose EnrollStatus is not Inactive (Active by default).

        Logs the dropped count with a per-source-status breakdown so a district
        can see *why* rows were removed (e.g. Withdrawn vs Graduate) when a
        status column is present.
        """
        inactive_mask = working["EnrollStatus"] == "Inactive"
        dropped: pd.DataFrame = working[inactive_mask].copy()  # type: ignore[assignment]
        active: pd.DataFrame = working[~inactive_mask].copy()  # type: ignore[assignment]
        if len(dropped) > 0:
            breakdown = cls._status_breakdown(dropped, field_map)
            suffix = f" Breakdown: {breakdown}." if breakdown else ""
            logger.info(f"[Students] Filtered out {len(dropped)} inactive students.{suffix}")
        return active

    @classmethod
    def _status_breakdown(cls, dropped: pd.DataFrame, field_map: dict[str, Any]) -> dict[str, int]:
        """Count dropped rows by their raw source-status value.

        Returns an empty dict when no status column is present (date-only
        path), in which case the log omits the breakdown.
        """
        status_column, _, _ = cls.resolve_active_config(field_map, dropped.columns)
        if status_column is None or dropped.empty:
            return {}
        counts = dropped[status_column].astype(str).str.strip().value_counts()
        return {str(k): int(v) for k, v in counts.items()}

    def _generate_emails(self, working: pd.DataFrame, result: pd.DataFrame, field_map: dict[str, Any]) -> None:
        email_config = field_map.get("Email Address", {})
        if isinstance(email_config, dict):
            email_format = email_config.get("format")
            if email_format:
                result["Email Address"] = working.apply(
                    self.generate_student_email, format_str=email_format.lower(), axis=1
                )
