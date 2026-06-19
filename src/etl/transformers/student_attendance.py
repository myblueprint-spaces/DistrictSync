"""StudentAttendance entity transformer — SpacesEDU half-day attendance feed.

Produces the 4-column ``StudentAttendance.csv`` SpacesEDU requires (exact
case-sensitive header order): School Number, Absence Date, Absence Category,
Student Number. The SpacesEDU contract permits dropping every optional field
after Student Number, so only those four required columns are emitted. The
output is the UNION of two independent bands, each resolved
from ``context.raw_data`` BY ITS ``source_files`` ROLE (``daily_absences`` /
``period_absences``) — order-independent, not by the pipeline's positional
primary frame. A district may declare only ``daily_absences``, only
``period_absences``, or both; either band may be absent/empty (graceful), and a
band's config is required ONLY when that band's data is present:

K-7 **Student Daily Absences** band. The Daily GDE records a single
``Absent Code`` (A = absent, T = tardy) plus an ``Authorized`` (Y/N) flag and
a ``Portion Absent`` fraction; from those the transformer DERIVES the SpacesEDU
``Absence Category`` and the row multiplicity (full day -> 2 identical rows, so
a full-day absence is intentionally TWO rows).

8-12 **Student Period Absences** band — PER-PERIOD PASS-THROUGH. SpacesEDU
aggregates per-day itself via per-entry weights (8-12 absence = 0.25/entry, so
4 period entries = 1 day, capped at 1/day), so this band emits ONE output row
per period-absence row — no AM/PM collapse, no Period Id, no derivation. The
GDE ``Absence Category`` already carries the final codes (A, A-E, L, AD, AL,
OffSite, ISS...); SpacesEDU ignores non-accepted ones, so the category is
passed through AS-IS (no filtering, no dedup — per-period multiplicity is
intentional). Rows with a blank category OR a blank student number are dropped.

Configurable-Columns rule: every source column name and every K-7 derivation
knob (the (code, authorized) -> category map, the portion -> row-count rule) is
read at runtime from ``global_config.attendance`` — nothing is hardcoded as a
Python literal. An (absent_code, authorized) pair that is non-blank but absent
from the configured ``category_map`` FAILS LOUD (raises ``ValueError``) rather
than silently dropping or mis-bucketing, so SpacesEDU's review tunes config,
never code.
"""

from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class StudentAttendanceTransformer(BaseTransformer):
    # The four required SpacesEDU columns, in exact case-sensitive order. The
    # contract permits dropping every optional field after Student Number, so
    # this is the entire output — there are no blank trailing columns.
    OUTPUT_COLUMNS: tuple[str, ...] = (
        "School Number",
        "Absence Date",
        "Absence Category",
        "Student Number",
    )

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        rows: list[dict[str, str]] = []

        # Both bands are resolved from raw_data BY ROLE (order-independent), not
        # from the positional `df` the pipeline passes as the primary source. A
        # district may declare only `daily_absences`, only `period_absences`, or
        # both; whichever role it declares is whichever band runs. The positional
        # `df` is intentionally unused here — a period-only district would have
        # the period file as primary, so treating `df` as the daily frame would
        # mis-process it as K-7 daily data.

        # K-7 Daily band. Read via role `daily_absences`. Empty/absent -> no
        # daily rows; its config is required ONLY when its data is present.
        daily = self._daily_frame(mapping, context)
        if not daily.empty:
            daily_cfg = self._daily_config(context.global_config)
            rows.extend(self._build_daily_rows(daily, mapping, daily_cfg, context))

        # 8-12 Period band. Read via role `period_absences`. Empty/absent -> no
        # period rows; its config is required ONLY when its data is present.
        period = self._period_frame(mapping, context)
        if not period.empty:
            period_cfg = self._period_config(context.global_config)
            rows.extend(self._build_period_rows(period, period_cfg))

        return self._frame(rows)

    # ------------------------------------------------------------------
    # Config resolution (Configurable-Columns: no hardcoded source names)
    # ------------------------------------------------------------------
    @staticmethod
    def _daily_config(global_config: dict[str, Any]) -> dict[str, Any]:
        """Return the K-7 ``daily`` sub-block of ``global_config.attendance``.

        Fails loud when absent: the entity is only enabled where SD51 (or a
        future district) has supplied the derivation config. A silent default
        would mis-categorize attendance — exactly what the SpacesEDU review
        guards against.
        """
        attendance = global_config.get("attendance") or {}
        daily = attendance.get("daily")
        if not isinstance(daily, dict) or not daily:
            raise ValueError(
                "StudentAttendance is enabled but 'global_config.attendance.daily' is missing or empty. "
                "Configure the K-7 daily column names, category_map, and portion rule (see the base "
                "myedbc_mapping.yaml 'attendance:' block)."
            )
        return daily

    @staticmethod
    def _period_config(global_config: dict[str, Any]) -> dict[str, Any]:
        """Return the 8-12 ``period`` sub-block of ``global_config.attendance``.

        Fails loud when absent (only invoked when the period frame is non-empty,
        i.e. there is per-period data to pass through). Per-period is pass-through
        so the block carries only the four source column names — no derivation
        knobs.
        """
        attendance = global_config.get("attendance") or {}
        period = attendance.get("period")
        if not isinstance(period, dict) or not period:
            raise ValueError(
                "StudentAttendance: period-absences data is present but "
                "'global_config.attendance.period' is missing or empty. Configure the 8-12 period "
                "column names (see the base myedbc_mapping.yaml 'attendance:' block)."
            )
        return period

    def _daily_frame(self, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        """Resolve + normalize the daily-absences frame from raw_data.

        The filename is resolved from the entity ``source_files`` (role
        ``daily_absences``) — never hardcoded here and never inferred from the
        positional primary ``df``. Returns an empty frame when the source is
        absent (graceful: the band is optional). Resolving by role keeps the
        transform order-independent so a period-only district isn't
        mis-processed as daily.
        """
        source_files = mapping.get("source_files", {})
        daily = self.get_source_file(context, source_files, "daily_absences")
        if daily.empty:
            return daily
        return self.normalize_columns(daily)

    def _period_frame(self, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        """Resolve + normalize the period-absences frame from raw_data.

        The filename is resolved from the entity ``source_files`` (role
        ``period_absences``) — never hardcoded here. Returns an empty frame when
        the source is absent (graceful: the band is optional).
        """
        source_files = mapping.get("source_files", {})
        period = self.get_source_file(context, source_files, "period_absences")
        if period.empty:
            return period
        return self.normalize_columns(period)

    # ------------------------------------------------------------------
    # K-7 Daily Absences band
    # ------------------------------------------------------------------
    def _build_daily_rows(
        self,
        working: pd.DataFrame,
        mapping: dict[str, Any],
        daily_cfg: dict[str, Any],
        context: TransformContext,
    ) -> list[dict[str, str]]:
        if working.empty:
            return []

        school_col = self._require(daily_cfg, "daily_school_col")
        student_col = self._require(daily_cfg, "daily_student_col")
        date_col = self._require(daily_cfg, "daily_date_col")
        code_col = self._require(daily_cfg, "daily_absent_code_col")
        authorized_col = self._require(daily_cfg, "daily_authorized_col")
        portion_col = self._require(daily_cfg, "daily_portion_col")

        category_map = self._category_map(daily_cfg)
        portion_rule = self._portion_rule(daily_cfg)

        rows: list[dict[str, str]] = []
        for record in working.to_dict("records"):
            absent_code = self._str(record.get(code_col))
            if not absent_code:
                # Blank code → no absence to report (drop).
                continue

            authorized = self._str(record.get(authorized_col))
            category = self._derive_category(absent_code, authorized, category_map)
            row_count = self._derive_row_count(absent_code, record.get(portion_col), portion_rule)
            if row_count <= 0:
                continue

            row = {
                "School Number": self._str(record.get(school_col)),
                "Absence Date": self.format_dd_mmm_yyyy(record.get(date_col)),
                "Absence Category": category,
                "Student Number": self._str(record.get(student_col)),
            }
            rows.extend([dict(row) for _ in range(row_count)])

        return rows

    # ------------------------------------------------------------------
    # 8-12 Period Absences band (per-period PASS-THROUGH — no derivation)
    # ------------------------------------------------------------------
    def _build_period_rows(self, period: pd.DataFrame, period_cfg: dict[str, Any]) -> list[dict[str, str]]:
        """Emit ONE output row per period-absence row (no AM/PM collapse, no dedup).

        The GDE ``Absence Category`` already carries the final code, so it is
        passed through AS-IS (SpacesEDU ignores non-accepted codes). A row whose
        category OR student number is blank is dropped — everything else is
        intentional per-period multiplicity.
        """
        if period.empty:
            return []

        school_col = self._require(period_cfg, "period_school_col")
        student_col = self._require(period_cfg, "period_student_col")
        date_col = self._require(period_cfg, "period_date_col")
        category_col = self._require(period_cfg, "period_category_col")

        rows: list[dict[str, str]] = []
        for record in period.to_dict("records"):
            category = self._str(record.get(category_col))
            student = self._str(record.get(student_col))
            if not category or not student:
                # Blank category or blank student number → nothing to report.
                continue
            rows.append(
                {
                    "School Number": self._str(record.get(school_col)),
                    "Absence Date": self.format_dd_mmm_yyyy(record.get(date_col)),
                    "Absence Category": category,
                    "Student Number": student,
                }
            )
        return rows

    @staticmethod
    def _derive_category(absent_code: str, authorized: str, category_map: dict[str, str]) -> str:
        """Map (absent_code, authorized) → SpacesEDU category, failing loud on a gap.

        Lookup is case-insensitive on both keys. An unmapped non-blank pair
        raises so the gap is fixed in config, never silently dropped.
        """
        key = f"{absent_code.upper()}|{authorized.upper()}"
        category = category_map.get(key)
        if category is None:
            raise ValueError(
                f"StudentAttendance: no category mapping for (Absent Code={absent_code!r}, "
                f"Authorized={authorized!r}). Add '{key}' to "
                "global_config.attendance.daily.category_map."
            )
        return category

    @staticmethod
    def _derive_row_count(absent_code: str, portion_value: Any, portion_rule: dict[str, Any]) -> int:
        """Half-day row multiplicity from the portion rule.

        full-day portion (== ``full_day_value``) → ``full_day_rows`` (2);
        tardy code → ``tardy_rows`` (1); any other present absence →
        ``default_rows`` (1).
        """
        if absent_code.upper() == str(portion_rule["tardy_code"]).upper():
            return int(portion_rule["tardy_rows"])
        try:
            portion = float(portion_value)
        except (TypeError, ValueError):
            portion = None
        if portion is not None and portion == float(portion_rule["full_day_value"]):
            return int(portion_rule["full_day_rows"])
        return int(portion_rule["default_rows"])

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _require(cfg: dict[str, Any], key: str) -> str:
        value = cfg.get(key)
        if not value:
            # Point at the right sub-block (daily/period) by the key's prefix so
            # the fail-loud message is actionable for whichever band is missing it.
            sub_block = key.split("_", 1)[0] if "_" in key else "daily"
            raise ValueError(
                f"StudentAttendance: required key '{key}' missing from global_config.attendance.{sub_block}."
            )
        return str(value).strip().lower()

    @staticmethod
    def _category_map(daily_cfg: dict[str, Any]) -> dict[str, str]:
        raw = daily_cfg.get("category_map")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("StudentAttendance: global_config.attendance.daily.category_map is missing or empty.")
        # Normalize keys to upper-case so config casing doesn't matter.
        return {str(k).upper(): str(v) for k, v in raw.items()}

    @staticmethod
    def _portion_rule(daily_cfg: dict[str, Any]) -> dict[str, Any]:
        rule = daily_cfg.get("portion")
        if not isinstance(rule, dict):
            raise ValueError("StudentAttendance: global_config.attendance.daily.portion is missing.")
        for key in ("full_day_value", "full_day_rows", "tardy_code", "tardy_rows", "default_rows"):
            if key not in rule:
                raise ValueError(f"StudentAttendance: global_config.attendance.daily.portion is missing '{key}'.")
        return rule

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------
    def _frame(self, rows: list[dict[str, str]]) -> pd.DataFrame:
        """Build the 4-column frame in the exact SpacesEDU header order.

        The rows already carry exactly the four output keys, so no blank-column
        fill is needed. No ``drop_duplicates`` — a full-day absence is
        intentionally two identical rows.
        """
        return pd.DataFrame(rows, columns=list(self.OUTPUT_COLUMNS))

    @staticmethod
    def _str(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        s = str(value).strip()
        return "" if s.lower() == "nan" else s
