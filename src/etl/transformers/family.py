"""Family entity transformer — simple field mapping from emergency contacts."""

import logging
from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext
from src.etl.transformers.ids import clean_invalid_ids

logger = logging.getLogger(__name__)

#: The OUTPUT column name from the Advanced CSV contract
#: (``docs/developer/output-contract.md`` → ``Family.csv``). Output names ARE the
#: contract; the SOURCE spelling stays configurable through the entity field_map.
EMAIL_OUTPUT_COLUMN = "Email"


class FamilyTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        # Config-driven row inclusion (e.g. SD60 keeps only guardian rows) —
        # applied BEFORE the field map so excluded contacts never reach output.
        working = self.apply_row_filters(working, mapping.get("row_filters", []), "Family")
        field_map = mapping.get("field_map", {})
        # Zero-orphan invariant: keep only contacts whose student is on the
        # active roster (Students.csv), so a withdrawn student's guardians never
        # ship. Students runs before Family (base mapping / enabled_entities
        # order); when the roster is unavailable (e.g. a tier without the
        # Students entity), filter_to_active warns and returns the frame
        # unchanged — the same convention as Enrollments.
        working = self.filter_to_active(working, self._student_number_col(field_map), context, caller="Family")
        result = pd.DataFrame()
        result = self.apply_field_map(working, result, field_map, "Family", context)
        # Last, on the OUTPUT frame: a contact with no email cannot be imported.
        return self._exclude_rows_without_email(result)

    @staticmethod
    def _exclude_rows_without_email(result: pd.DataFrame) -> pd.DataFrame:
        """Drop contact rows whose output ``Email`` is blank, warning once.

        WHY (importer behaviour): SpacesEDU does not import a family contact
        without an email address, so a blank-email row can only be rejected on
        ingest — shipping it inflates the delivered row count and hides how many
        contacts the district really provided. Excluding it here makes the loss
        LOUD (one aggregate WARNING) instead of a silent partner-side reject.

        Blank = NaN / empty / whitespace-only, via the shared blank-value
        semantics of :func:`~src.etl.transformers.ids.clean_invalid_ids` (which
        also treats a stringified-NaN literal as blank).

        Runs on the OUTPUT frame, so it is necessarily after ``filter_to_active``
        and after ``apply_field_map``: the column is resolved by its CONTRACT
        OUTPUT name (:data:`EMAIL_OUTPUT_COLUMN`), never a hardcoded source
        column. A config that maps no ``Email`` at all cannot be filtered — the
        contract requires the column, so that is surfaced as its own WARNING
        rather than hidden, and the rows pass through untouched.

        PII rule: counts only — never a contact name, address or student id.
        """
        if EMAIL_OUTPUT_COLUMN not in result.columns:
            logger.warning(
                f"[Family] No '{EMAIL_OUTPUT_COLUMN}' output column — the no-email exclusion could not be "
                f"applied. The Advanced CSV contract requires it for Family.csv; check the config field_map."
            )
            return result
        total = len(result)
        kept: pd.DataFrame = clean_invalid_ids(result, EMAIL_OUTPUT_COLUMN).copy()
        excluded = total - len(kept)
        if excluded > 0:
            logger.warning(
                f"[Family] Excluded {excluded} of {total} contact row(s) with no email address — "
                f"SpacesEDU does not import a family contact without one."
            )
        return kept

    @staticmethod
    def _student_number_col(field_map: dict[str, Any]) -> str:
        """Source student-number column, resolved from the entity field_map.

        Configurable Columns rule: the ``Student User ID`` output maps from a
        district-configurable source column (default MyEd BC "Student Number").
        """
        config = field_map.get("Student User ID", "student number")
        if isinstance(config, dict):
            return str(config.get("column", "student number")).strip().lower()
        return str(config).strip().lower()
