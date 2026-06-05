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
        result["EnrollStatus"] = working["EnrollStatus"]
        self._generate_emails(working, result, field_map)

        result = self.apply_field_map(working, result, field_map, "Students", context)
        if "Date of Birth" in result.columns:
            result["Date of Birth"] = result["Date of Birth"].apply(self.normalize_iso_date)
        return result

    def _determine_enrollment_status(self, working: pd.DataFrame, field_map: dict[str, Any]) -> None:
        """Set the 'EnrollStatus' column in-place via the shared base predicate.

        Source column names (status / withdraw date) and the active-value set
        resolve from the Students ``EnrollStatus`` config (Configurable
        Columns); MyEd BC defaults apply when unconfigured. ``PreReg`` is
        retained as active; a past withdraw date is a hard override to
        Inactive. See ``BaseTransformer.compute_enroll_status``.
        """
        working["EnrollStatus"] = self.compute_enroll_status(working, field_map)

    @classmethod
    def _filter_active(cls, working: pd.DataFrame, field_map: dict[str, Any]) -> pd.DataFrame:
        """Keep rows whose EnrollStatus is not Inactive (Active + PreReg).

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
