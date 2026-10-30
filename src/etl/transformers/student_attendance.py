"""StudentAttendance entity transformer — SpacesEDU half-day attendance feed.

Produces the 4-column ``StudentAttendance.csv`` SpacesEDU requires (exact
case-sensitive header order): School Number, Absence Date, Absence Category,
Student Number. The SpacesEDU contract permits dropping every optional field
after Student Number, so only those four required columns are emitted.
``Absence Date`` is written in the format set by
``global_config.attendance.date_format`` (friendly tokens, e.g. ``yyyy-MM-dd``),
defaulting to ISO ``yyyy-MM-dd`` — the shape SpacesEDU's attendance importer
requires — which a district may override. Input GDE dates in any recognized
format (``dd-MMM-yyyy`` / ISO / ``m/d/yyyy`` / ``d/m/yyyy``) are parsed and
reformatted to it. The output is the UNION of two independent bands, each
resolved
from ``context.raw_data`` BY ITS ``source_files`` ROLE (``daily_absences`` /
``period_absences``) — order-independent, not by the pipeline's positional
primary frame. A district may declare only ``daily_absences``, only
``period_absences``, or both; either band may be absent/empty (graceful), and a
band's config is required ONLY when that band's data is present:

K-7 **Student Daily Absences** band. The Daily GDE records a single
``Absent Code`` (A = absent, T = tardy) plus an ``Authorized`` (Y/N) flag and
a ``Portion Absent`` fraction; from those the transformer DERIVES the SpacesEDU
``Absence Category`` and the row multiplicity, which follows the student's
``Grade`` because SpacesEDU weighs every row by it (K-7 = 0.5 day per row,
grade 8 and up = 0.25 day per row): a K-7 full day is TWO identical rows, a
grade 8+ full day FOUR (:meth:`StudentAttendanceTransformer._derive_row_count`).

8-12 **Student Period Absences** band — PER-PERIOD PASS-THROUGH. SpacesEDU
aggregates per-day itself via per-entry weights (8-12 absence = 0.25/entry, so
4 period entries = 1 day, capped at 1/day), so this band emits ONE output row
per period-absence row — no AM/PM collapse, no Period Id, no derivation. The
GDE ``Absence Category`` already carries the final codes (A, A-E, L, AD, AL,
OffSite, ISS...); SpacesEDU ignores non-accepted ones, so the category is
passed through AS-IS (no filtering, no dedup — per-period multiplicity is
intentional). Rows with a blank category OR a blank student number are dropped.

**The union is not naive.** The two bands are independent SOURCES, not
independent FACTS. A district whose schools take attendance in a
homeroom/attendance-only section (MyEd BC's ``ATT--AM`` / ``ATT--PM``) records
the same absence in BOTH files, and the output carries no band marker for
SpacesEDU to disambiguate with — it weighs each row by the student's GRADE and
adds them up, so emitting both turns a half-day absence into a full day. So a
period row whose (school, date, student) the daily band has already reported is
SUPPRESSED (:meth:`StudentAttendanceTransformer.suppress_overlapping_period_rows`),
daily winning because it is the band that carries the half-day portion. A
student-day seen ONLY in the period band is untouched, so a genuine 8-12
per-period district keeps its intentional per-period multiplicity in full.

Note this is deliberately NOT driven by ``excluded_course_codes``: a district
excludes ``ATT--*`` because it is not a real CLASS, but that section is exactly
where its attendance is taken — those rows are the attendance record, so the
rule keys on the student-day the bands share, never on a course code.

Configurable-Columns rule: every source column name and every K-7 derivation
knob (the (code, authorized) -> category map, the portion -> row-count rule) is
read at runtime from ``global_config.attendance`` — nothing is hardcoded as a
Python literal. An (absent_code, authorized) pair that is non-blank but absent
from the configured ``category_map`` FAILS LOUD (raises ``ValueError``) rather
than silently dropping or mis-bucketing, so SpacesEDU's review tunes config,
never code.
"""

import logging
import math
from typing import Any

import pandas as pd

from src.etl.errors import GuardKind
from src.etl.outcomes import OutcomeNote
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.columns import require_columns
from src.etl.transformers.context import TransformContext
from src.etl.transformers.grades import grade_to_ceds
from src.etl.transformers.notes import is_code_shaped, record_note

logger = logging.getLogger(__name__)

#: The configured columns each band reads (``global_config.attendance.daily`` / ``.period``) —
#: the keys ``_build_daily_rows`` / ``_build_period_rows`` resolve with ``_require``.
_DAILY_KEYS: tuple[str, ...] = (
    "daily_school_col",
    "daily_student_col",
    "daily_date_col",
    "daily_absent_code_col",
    "daily_authorized_col",
    "daily_portion_col",
    "daily_grade_col",
)
_PERIOD_KEYS: tuple[str, ...] = ("period_school_col", "period_student_col", "period_date_col", "period_category_col")


#: The importer's grade bands, read from its source on 2026-10-30 (``ReportingSpacesCollator.
#: CalculateAttendance``, ``GradeHelpers.TryParseCedsGrade``; DECISIONS 2026-10-30): ONLY
#: kindergarten and grades 1-7 weigh 0.5 day per row. ``UG`` — what ``grade_to_ceds`` makes of a
#: blank or unknown grade — is the importer's "no grade": it also weighs 0.5, but SpacesEDU shows
#: such a student no attendance at all. Every other CEDS code (``IT``, ``PR``, ``PK``, ``TK``,
#: ``PS``, ``Other``, ``08``-``13``) weighs 0.25.
_K7_NON_NUMERIC: frozenset[str] = frozenset({"KG"})
_NO_GRADE: str = "UG"


#: The CLOSED vocabulary of the authorized flag (compared upper-cased): yes, no, or blank.
#: Anything else is not a flag, so it is never echoed.
_FLAG_VOCABULARY: frozenset[str] = frozenset({"Y", "N", ""})


def _is_code(value: str) -> bool:
    """Whether ``value`` may be echoed as an absence code (:func:`notes.is_code_shaped`)."""
    return is_code_shaped(value)


def _is_flag(value: str) -> bool:
    """Whether ``value`` may be echoed as an authorized flag (see :data:`_FLAG_VOCABULARY`)."""
    return value.upper() in _FLAG_VOCABULARY


def _for_message(value: str, *, echoable: bool) -> str:
    """``value`` as the unmapped-code message may show it — never a value outside its vocabulary.

    The two cells the message echoes are CODES by contract, and a code is what the admin
    needs to add to ``category_map``. But a daily-absences file whose columns do not line up
    with the declared ``headers:`` (the SD51 2026-09-17 shape) puts ANY cell in the code's
    position — a pupil's name included, and a short name (``Li``, ``Wong``) is as short as a
    code — and this message reaches the log, since plan 0053 S4 on the ``ENTITY NOT BUILT``
    line's traceback (before it, on ``Pipeline failed:``). So only a value inside the closed
    vocabulary (:func:`_is_code` / :func:`_is_flag`) is echoed; any other is described by its
    length only (§8: no observed value in a log line).
    """
    if echoable:
        return repr(value)
    return f"<a {len(value)}-character value that is not a code — not shown>"


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

    #: The identity a student's attendance for ONE day has in the OUTPUT space.
    #: Deliberately excludes Absence Category: two bands describing the same
    #: student-day may label it differently (a derived "A-E" against a
    #: passed-through "AD-E"), and they are still the same day off school.
    STUDENT_DAY_KEY: tuple[str, ...] = ("School Number", "Absence Date", "Student Number")

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        rows: list[dict[str, str]] = []

        # Output date shape for Absence Date — configurable per district via
        # global_config.attendance.date_format (friendly tokens; default ISO
        # yyyy-MM-dd, which SpacesEDU's attendance importer requires). Resolved +
        # validated ONCE here (fail-loud on an unsupported token) and reused for
        # both bands; input GDE dates in any recognized format are parsed and
        # reformatted to this shape.
        attendance_cfg = context.global_config.get("attendance") or {}
        date_format = attendance_cfg.get("date_format") or "yyyy-MM-dd"
        strftime_fmt = self.friendly_date_format_to_strftime(date_format)

        # Both bands are resolved from raw_data BY ROLE (order-independent), not
        # from the positional `df` the pipeline passes as the primary source. A
        # district may declare only `daily_absences`, only `period_absences`, or
        # both; whichever role it declares is whichever band runs. The positional
        # `df` is intentionally unused here — a period-only district would have
        # the period file as primary, so treating `df` as the daily frame would
        # mis-process it as K-7 daily data.

        # K-7 Daily band. Read via role `daily_absences`. Empty/absent -> no
        # daily rows; its config is required ONLY when its data is present.
        daily_rows: list[dict[str, str]] = []
        daily = self._daily_frame(mapping, context)
        period = self._period_frame(mapping, context)
        daily_cfg = self._daily_config(context.global_config) if not daily.empty else {}
        period_cfg = self._period_config(context.global_config) if not period.empty else {}
        self._note_absent_columns(context, ((daily, daily_cfg, _DAILY_KEYS), (period, period_cfg, _PERIOD_KEYS)))
        if not daily.empty:
            daily_rows = self._build_daily_rows(daily, mapping, daily_cfg, context, strftime_fmt)

        # 8-12 Period band. Read via role `period_absences`. Empty/absent -> no
        # period rows; its config is required ONLY when its data is present.
        period_rows: list[dict[str, str]] = []
        if not period.empty:
            period_rows = self._build_period_rows(period, period_cfg, strftime_fmt)

        # The two bands are independent SOURCES, not independent FACTS: a
        # district may record the same student-day in both. Union them only
        # after the daily band has claimed its student-days.
        period_rows, suppressed = self.suppress_overlapping_period_rows(daily_rows, period_rows)
        if suppressed:
            logger.info(
                f"[StudentAttendance] Suppressed {suppressed} period row(s) whose "
                f"(school, date, student) the daily band already reported — counting a student-day "
                f"in both bands would inflate it. Daily wins: it carries the half-day portion."
            )

        rows.extend(daily_rows)
        rows.extend(period_rows)
        return self._frame(rows)

    @classmethod
    def _note_absent_columns(
        cls,
        context: TransformContext,
        bands: tuple[tuple[pd.DataFrame, dict[str, Any], tuple[str, ...]], ...],
    ) -> None:
        """Record a band whose configured column is absent (§5 #33, plan 0053 S11).

        Each band reads its configured columns with ``record.get``, so an absent one is silent
        today — and what it costs differs (an absent code or student drops every row, an absent
        school or date ships that field blank, an absent portion counts every day as one row).
        Those DIRECTIONS stay. What changes is that the absence is never silent: ONE WARNING
        naming the absent columns as configured (config vocabulary — never a header or a cell)
        and ``OutcomeNote.ATTENDANCE_SOURCE_COLUMN_ABSENT`` counting the rows of the band(s)
        concerned. Checked only for a band whose data is present (its config is required then).

        The daily band's authorized flag is the exception: since the owner ruling of 2026-09-26
        it is REQUIRED (``_build_daily_rows``' ``require_columns`` guard, §5 #33), so the entity
        fails as FAILED/``missing_source_column`` — and, CRITICAL since owner decision 2026-09-28,
        that FAILED outcome stops the night. The WARNING above is still
        logged first — naming every absent column, the authorized one included — but the NOTE
        cannot survive: a FAILED outcome carries no notes (``outcomes.NOTE_BEARING_KINDS``), so
        the record says only what the failure says.
        """
        missing: list[str] = []
        rows = 0
        for frame, cfg, keys in bands:
            if frame.empty:
                continue
            absent = [column for column in (cls._require(cfg, key) for key in keys) if column not in frame.columns]
            if absent:
                missing += absent
                rows += len(frame)
        if missing:
            # failure-policy: join_key
            record_note(
                context,
                "StudentAttendance",
                OutcomeNote.ATTENDANCE_SOURCE_COLUMN_ABSENT,
                rows,
                log=logger,
                message=(
                    f"[StudentAttendance] ABSENCE COLUMNS MISSING — the absence export has no column {missing}, "
                    f"so its {rows} row(s) are read without {'it' if len(missing) == 1 else 'them'}."
                ),
            )

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
        strftime_fmt: str,
    ) -> list[dict[str, str]]:
        if working.empty:
            return []

        school_col = self._require(daily_cfg, "daily_school_col")
        student_col = self._require(daily_cfg, "daily_student_col")
        date_col = self._require(daily_cfg, "daily_date_col")
        code_col = self._require(daily_cfg, "daily_absent_code_col")
        authorized_col = self._require(daily_cfg, "daily_authorized_col")
        portion_col = self._require(daily_cfg, "daily_portion_col")
        grade_col = self._require(daily_cfg, "daily_grade_col")

        # The authorized flag is half of every category-map key (§5 #23): without the column,
        # every present code misses the map and the band cannot build a single row. So it is
        # REQUIRED once the band has rows (§5 #33, owner ruling 2026-09-26) — one typed
        # `SourceSchemaError` naming it in config spelling, so the entity reads
        # FAILED/`missing_source_column` with a label, never #23's `transform_error` — and, CRITICAL
        # since 2026-09-28, that FAILED outcome stops the night (run scope). The other
        # band columns keep their recorded S11 directions (`_note_absent_columns`).
        # failure-policy: join_key
        require_columns(
            working.columns,
            [self._configured(daily_cfg, "daily_authorized_col")],
            entity="StudentAttendance",
            guard=GuardKind.JOIN_KEY,
        )

        category_map = self._category_map(daily_cfg)
        portion_rule = self._portion_rule(daily_cfg)
        upper_grade_from = self._upper_grade_from(portion_rule)
        # An ABSENT grade column is recorded once by `_note_absent_columns` (every row then counts
        # at the K-7 weighting, as before 2026-10-30); only unreadable VALUES are counted here.
        grade_present = grade_col in working.columns
        ungraded = 0

        rows: list[dict[str, str]] = []
        for record in working.to_dict("records"):
            absent_code = self._str(record.get(code_col))
            if not absent_code:
                # Blank code → no absence to report (drop).
                continue

            authorized = self._str(record.get(authorized_col))
            category = self._derive_category(absent_code, authorized, category_map)
            upper_grade = self._is_upper_grade(record.get(grade_col), upper_grade_from)
            if upper_grade is None:
                ungraded += grade_present
                upper_grade = False
            row_count = self._derive_row_count(
                absent_code, record.get(portion_col), portion_rule, upper_grade=upper_grade
            )
            if row_count <= 0:
                continue

            row = {
                "School Number": self._str(record.get(school_col)),
                "Absence Date": self.format_date(record.get(date_col), strftime_fmt),
                "Absence Category": category,
                "Student Number": self._str(record.get(student_col)),
            }
            rows.extend([dict(row) for _ in range(row_count)])

        if ungraded:
            # Counts only — a grade value beside a pupil number is attendance PII (§8).
            logger.warning(
                f"[StudentAttendance] {ungraded} daily absence row(s) carry a blank or unrecognised grade, "
                f"so they are counted at the K-7 weighting (0.5 day per row), as SpacesEDU weighs a student "
                f"with no grade. SpacesEDU shows no attendance for a student without a recognised grade."
            )
        return rows

    # ------------------------------------------------------------------
    # 8-12 Period Absences band (per-period PASS-THROUGH — no derivation)
    # ------------------------------------------------------------------
    @classmethod
    def suppress_overlapping_period_rows(
        cls,
        daily_rows: list[dict[str, str]],
        period_rows: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], int]:
        """Drop period rows for a student-day the DAILY band already reported.

        WHY (recognising the data rather than configuring around it): the two
        GDE files are two SOURCES, not two independent facts. A district whose
        schools take attendance in a homeroom/attendance-only section emits the
        SAME absence twice — once as a daily AM/PM record and again as a period
        record against that section. SpacesEDU has no way to tell: the output
        carries no band marker, so it weighs every row by the student's GRADE
        and simply adds them up. Emitting both then turns a half-day absence
        into a full day (0.5 daily + 0.5 period), which is silently wrong.

        DAILY WINS, and that direction is the point: the daily record is the
        authoritative half-day summary — it is the band carrying
        ``Portion Absent`` and the AM/PM codes, and the band whose category we
        DERIVE rather than pass through. A period row for a day the daily band
        already describes adds no information it does not already hold.

        This narrows nothing else. A student-day present ONLY in the period band
        survives untouched, so a genuine 8-12 per-period district — several
        period rows for a day with no daily record — is completely unaffected,
        including its intentional per-period multiplicity.

        Pure and total: no config, no district knowledge, no I/O. Returns the
        kept rows and how many were suppressed (the caller logs the count; row
        values are attendance PII and are never logged).
        """
        if not daily_rows or not period_rows:
            return period_rows, 0
        claimed = {cls._student_day(row) for row in daily_rows}
        kept = [row for row in period_rows if cls._student_day(row) not in claimed]
        return kept, len(period_rows) - len(kept)

    @classmethod
    def _student_day(cls, row: dict[str, str]) -> tuple[str, ...]:
        """The row's (school, date, student) identity — see :data:`STUDENT_DAY_KEY`."""
        return tuple(row.get(column, "") for column in cls.STUDENT_DAY_KEY)

    def _build_period_rows(
        self, period: pd.DataFrame, period_cfg: dict[str, Any], strftime_fmt: str
    ) -> list[dict[str, str]]:
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
                    "Absence Date": self.format_date(record.get(date_col), strftime_fmt),
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
            code_ok, flag_ok = _is_code(absent_code), _is_flag(authorized)
            both_codes = code_ok and flag_ok
            advice = (
                f"Add '{key}' to global_config.attendance.daily.category_map."
                if both_codes
                else "A value that is not a code usually means the daily absences file's columns are "
                "not in the order this district's mapping declares."
            )
            # failure-policy: join_key
            raise ValueError(
                f"StudentAttendance: no category mapping for (Absent Code={_for_message(absent_code, echoable=code_ok)}, "
                f"Authorized={_for_message(authorized, echoable=flag_ok)}). {advice}"
            )
        return category

    @staticmethod
    def _is_upper_grade(grade_value: Any, upper_grade_from: int) -> bool | None:
        """Whether a daily row's grade takes the 0.25-day weighting — ``None`` for "no grade".

        The raw grade goes through :func:`grades.grade_to_ceds` (once — it is not idempotent), so
        MyEd BC's ``KF``/``K``/``01``…``12`` and the unpadded ``1``…``9`` all read the same way.
        Mirrors the importer (:data:`_K7_NON_NUMERIC`): a numeric CEDS grade at or above
        ``upper_grade_from`` is upper (``13`` included) and one below it is not; kindergarten is
        not; blank/unknown (``UG``) is ``None``; every other code (pre-kindergarten, ``PS``,
        ``Other``) is upper, as the importer weighs it.
        """
        ceds = grade_to_ceds(grade_value)
        if ceds.isdigit():
            return int(ceds) >= upper_grade_from
        if ceds in _K7_NON_NUMERIC:
            return False
        if ceds == _NO_GRADE:
            return None
        return True

    @staticmethod
    def _derive_row_count(
        absent_code: str, portion_value: Any, portion_rule: dict[str, Any], *, upper_grade: bool
    ) -> int:
        """Row multiplicity from the portion rule and the student's grade band, checked IN THIS ORDER:

        1. tardy code (== ``tardy_code``) → ``tardy_rows`` (1), at EVERY grade. Checked FIRST,
           so a tardy recorded against a full-day portion stays ONE row — the portion is never
           consulted for a tardy (the importer counts tardy rows one each, unweighted).
        2. K-7 (``upper_grade`` false — each row is 0.5 day):
           full-day portion (== ``full_day_value``) → ``full_day_rows`` (2); anything else
           (partial day, or an unparseable portion) → ``default_rows`` (1). Unchanged since
           before 2026-10-30, so K-7 output is byte-identical.
        3. Grade 8+ (each row is 0.25 day):
           full-day portion → ``upper_full_day_rows`` (4); a partial portion strictly between 0
           and ``full_day_value`` → the nearest whole number of rows, halves rounding UP
           (``floor(portion / full_day_value × upper_full_day_rows + 0.5)``), kept between 1 and
           ``upper_full_day_rows - 1`` so a partial absence is never reported as nothing or as a
           whole day; anything else (a zero, out-of-range or unparseable portion) →
           ``upper_default_rows`` (2 — half a day, the same day-value K-7's ``default_rows``
           carries).

        The precedence is load-bearing, not incidental: stating it the other way
        round describes a different feed. Callers drop blank-Absent-Code rows
        before reaching here.
        """
        if absent_code.upper() == str(portion_rule["tardy_code"]).upper():
            return int(portion_rule["tardy_rows"])
        try:
            portion = float(portion_value)
        except (TypeError, ValueError):
            portion = None
        full_day_value = float(portion_rule["full_day_value"])
        if not upper_grade:
            if portion is not None and portion == full_day_value:
                return int(portion_rule["full_day_rows"])
            return int(portion_rule["default_rows"])
        upper_full_day_rows = int(portion_rule["upper_full_day_rows"])
        if portion is not None and portion == full_day_value:
            return upper_full_day_rows
        if portion is not None and 0 < portion < full_day_value:
            nearest = math.floor(portion / full_day_value * upper_full_day_rows + 0.5)
            return min(max(nearest, 1), upper_full_day_rows - 1)
        return int(portion_rule["upper_default_rows"])

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

    @classmethod
    def _configured(cls, cfg: dict[str, Any], key: str) -> str:
        """The configured column for ``key`` in CONFIG spelling (trimmed, never lower-cased) — what
        a ``require_columns`` guard names, so the outcome's label is the config's own word
        (plan 0053 S7: ``preflight.label_vocabulary_by_entity`` declares these spellings)."""
        cls._require(cfg, key)  # the same presence refusal as every other key
        return str(cfg[key]).strip()

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
        for key in (
            "full_day_value",
            "full_day_rows",
            "tardy_code",
            "tardy_rows",
            "default_rows",
            "upper_grade_from",
            "upper_full_day_rows",
            "upper_default_rows",
        ):
            if key not in rule:
                raise ValueError(f"StudentAttendance: global_config.attendance.daily.portion is missing '{key}'.")
        return rule

    @staticmethod
    def _upper_grade_from(portion_rule: dict[str, Any]) -> int:
        """``portion.upper_grade_from`` as a grade number — fails loud unless it names a grade 01-13."""
        ceds = grade_to_ceds(portion_rule["upper_grade_from"])
        if not ceds.isdigit():
            raise ValueError(
                "StudentAttendance: global_config.attendance.daily.portion.upper_grade_from must be a "
                f"grade from 01 to 13 (got {portion_rule['upper_grade_from']!r})."
            )
        return int(ceds)

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------
    def _frame(self, rows: list[dict[str, str]]) -> pd.DataFrame:
        """Build the 4-column frame in the exact SpacesEDU header order.

        The rows already carry exactly the four output keys, so no blank-column
        fill is needed. No ``drop_duplicates`` — a full-day absence is
        intentionally two (K-7) or four (grade 8+) identical rows.
        """
        return pd.DataFrame(rows, columns=list(self.OUTPUT_COLUMNS))

    @staticmethod
    def _str(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        s = str(value).strip()
        return "" if s.lower() == "nan" else s
