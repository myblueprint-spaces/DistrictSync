"""Student entity transformer — enrollment status, active filtering, email generation."""

import logging
from datetime import datetime
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

        self._determine_enrollment_status(working)
        working = self._filter_active(working)
        result["EnrollStatus"] = working["EnrollStatus"]
        self._generate_emails(working, result, field_map)

        return self.apply_field_map(working, result, field_map, "Students", context)

    @staticmethod
    def _determine_enrollment_status(working: pd.DataFrame) -> None:
        """Set 'EnrollStatus' column on working DataFrame in-place."""
        if "enrolment status" in working.columns:
            working["EnrollStatus"] = working["enrolment status"].apply(
                lambda x: str(x).strip() if str(x).strip() in ["Active", "PreReg"] else "Inactive"
            )
        elif "withdraw date" in working.columns:
            today = datetime.now().date()
            unparseable_dates = []

            def check_withdraw_date(x):
                if pd.isna(x) or str(x).strip() == "":
                    return "Active"
                try:
                    date_str = str(x).strip()
                    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                        try:
                            withdraw_date = datetime.strptime(date_str, fmt).date()
                            return "Inactive" if withdraw_date <= today else "Active"
                        except ValueError:
                            continue
                    unparseable_dates.append(date_str)
                    return "Inactive"
                except (TypeError, ValueError, AttributeError):
                    return "Inactive"

            working["EnrollStatus"] = working["withdraw date"].apply(check_withdraw_date)

            if unparseable_dates:
                unique_formats = set(unparseable_dates[:10])
                logger.warning(
                    f"[Students] Could not parse {len(unparseable_dates)} withdraw date(s). "
                    f"Sample formats: {unique_formats}"
                )
        else:
            logger.warning(
                "[Students] Could not find 'enrolment status' or 'withdraw date' column. " "Defaulting to 'Active'."
            )
            working["EnrollStatus"] = "Active"

    @staticmethod
    def _filter_active(working: pd.DataFrame) -> pd.DataFrame:
        initial_count = len(working)
        working = working[working["EnrollStatus"] == "Active"].copy()
        filtered_count = initial_count - len(working)
        if filtered_count > 0:
            logger.info(f"[Students] Filtered out {filtered_count} inactive students.")
        return working

    def _generate_emails(self, working: pd.DataFrame, result: pd.DataFrame, field_map: dict[str, Any]) -> None:
        email_config = field_map.get("Email Address", {})
        if isinstance(email_config, dict):
            email_format = email_config.get("format")
            if email_format:
                result["Email Address"] = working.apply(
                    self.generate_student_email, format_str=email_format.lower(), axis=1
                )
