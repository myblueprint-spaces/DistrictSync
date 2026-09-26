"""Student entity transformer — enrollment status, active filtering, email generation."""

import logging
from typing import Any

import pandas as pd

from src.config.models import FieldAppendYear, FieldTransform, ensure_field_mapping
from src.etl.column_names import GRADE, SCHOOL_NUMBER, STUDENT_NUMBER
from src.etl.errors import GuardKind
from src.etl.outcomes import OutcomeNote
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.columns import Previously, require_columns, resolve_source_column, source_column_label
from src.etl.transformers.context import TransformContext
from src.etl.transformers.grades import filter_to_grade_scope, resolve_student_scope
from src.etl.transformers.ids import clean_invalid_ids, normalize_id_series
from src.etl.transformers.notes import record_note

logger = logging.getLogger(__name__)

#: The OUTPUT column name from the Advanced CSV contract
#: (``docs/developer/output-contract.md`` → ``Students.csv``). Output names ARE
#: the contract; the SOURCE spelling stays configurable through the field_map.
EMAIL_OUTPUT_COLUMN = "Email Address"


class StudentTransformer(BaseTransformer):
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})

        self._determine_enrollment_status(working, field_map, context)
        working = self._filter_active(working, field_map)
        working = self._collapse_cross_enrollment(working, field_map, context)
        working = self._filter_to_rostered_grades(working, field_map, context)
        result["EnrollStatus"] = working["EnrollStatus"]
        self._generate_emails(working, result, field_map, context)

        self._require_user_id_source(working, field_map)
        result = self.apply_field_map(working, result, field_map, "Students", context)
        if "Date of Birth" in result.columns:
            result["Date of Birth"] = result["Date of Birth"].apply(self.normalize_iso_date)
        self._coalesce_required_names(result)
        self._warn_rows_without_email(result, context)

        # Publish the active roster (zero-orphan invariant). `result` is already
        # filtered to active-only, so this IS the Students.csv `User ID` set by
        # construction — Classes (homeroom) and Enrollments (homeroom + subject)
        # filter their student rows against it so none references a non-rostered
        # student. Same value space as the schedule's `Student ID` (pupil
        # numbers); normalized so the cross-frame join matches.
        if "User ID" in result.columns:
            context.active_student_ids = set(normalize_id_series(result["User ID"]))

        return result

    @staticmethod
    def _require_user_id_source(working: pd.DataFrame, field_map: dict[str, Any]) -> None:
        """The mapped ``User ID`` SOURCE column must exist (§5 #27(ii), plan 0053 S10).

        It is the student's identity AND the roster every other entity filters against.
        Absent, ``apply_field_map`` wrote a blank ``User ID`` column and the published
        roster became ``{'<NA>'}`` — which the empty-roster guard does not catch, so
        every downstream active-roster filter dropped EVERY student row in Family,
        homeroom classes, Enrollments and StudentCourses while ``Students.csv`` shipped
        with blank IDs: a silent shrink of deactivating files (H2). Now the entity fails
        with a typed error naming the column; Students is CRITICAL, so the run fails.

        Only when the mapping names a ``User ID`` at all: a config mapping none publishes
        no roster, which is §5 #27(i)'s fail-open posture (S11), not this one. And only
        when that entry READS a column (a bare string or a ``column:`` entry): a fixed
        ``value:`` or an email ``format:`` reads none, so nothing is required.
        """
        if "User ID" not in field_map:
            return
        if not isinstance(ensure_field_mapping(field_map["User ID"]), (str, FieldTransform, FieldAppendYear)):
            return
        # failure-policy: join_key
        require_columns(
            working.columns,
            [source_column_label(field_map, "User ID", default=STUDENT_NUMBER)],
            entity="Students",
            guard=GuardKind.JOIN_KEY,
        )

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
            is_blank = result[primary].isna() | normalize_id_series(result[primary]).str.lower().isin(["", "nan"])
            result.loc[is_blank, primary] = result.loc[is_blank, fallback]

    @staticmethod
    def _warn_rows_without_email(result: pd.DataFrame, context: TransformContext) -> None:
        """Count students with no ``Email Address`` and warn once — never drop.

        WHY (importer behaviour): SpacesEDU DOES import a student without an
        email address — unlike a family contact, which it rejects — so the row
        is KEPT. What the district loses is the ability to invite that student by
        email, which it can only act on if it is told, hence one aggregate
        WARNING. A missing address is a data FACT, not a transform failure, so it
        is deliberately NOT recorded in ``context.data_errors`` (that axis is for
        a mapping/transform that raised) and it never affects the run status.

        Blank = NaN / empty / whitespace-only, via the shared blank-value
        semantics of :func:`~src.etl.transformers.ids.clean_invalid_ids`.

        Runs on the OUTPUT frame (after ``apply_field_map``, so a generated
        ``email format`` address counts as present) and resolves the column by
        its CONTRACT OUTPUT name (:data:`EMAIL_OUTPUT_COLUMN`), never a
        hardcoded source column. A config that maps no ``Email Address`` cannot
        be counted; the contract requires the column, so that is surfaced as its
        own WARNING rather than hidden — and, since plan 0053 S11, recorded
        (``OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED``, §5 #40, counting the rows).

        PII rule: counts only — never a student name, id or address.
        """
        if EMAIL_OUTPUT_COLUMN not in result.columns:
            if result.empty:
                return
            # failure-policy: contract_field
            record_note(
                context,
                "Students",
                OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED,
                len(result),
                log=logger,
                message=(
                    f"[Students] No '{EMAIL_OUTPUT_COLUMN}' output column — the no-email count could not be "
                    f"taken. The Advanced CSV contract requires it for Students.csv; check the config field_map."
                ),
            )
            return
        total = len(result)
        missing = total - len(clean_invalid_ids(result, EMAIL_OUTPUT_COLUMN))
        if missing > 0:
            logger.warning(
                f"[Students] {missing} of {total} student row(s) have no email address — "
                f"kept (SpacesEDU imports them), but they cannot be invited by email."
            )

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
        Columns) through the one resolver: ``User ID`` and ``SchoolCode`` — a
        bare string or a ``{column: ...}`` entry alike. Fail-loud (validate at
        boundary, ``columns.require_columns``): the ``User ID``, ``SchoolCode``
        and ``home_school_column`` columns are all checked before any row is
        touched, and every absent one is named in ONE
        :class:`~src.etl.errors.SourceSchemaError` (guard ``JOIN_KEY``, config
        spelling, the source's column COUNT only — never its header names; §5
        #2/#2a — the first two used to be raw pandas ``KeyError``s). Never drops a
        student entirely (always ≥ 1 row per User ID). Logs only the collapsed
        COUNT (no PII).
        """
        cc = (context.global_config or {}).get("cross_enrollment") or {}
        if not cc.get("collapse"):
            return working

        user_id_col = resolve_source_column(
            field_map, "User ID", default=STUDENT_NUMBER, previously=Previously.AS_CONFIGURED
        )
        school_col = resolve_source_column(
            field_map, "SchoolCode", default=SCHOOL_NUMBER, previously=Previously.AS_CONFIGURED
        )
        home_col = resolve_source_column(cc, "home_school_column", default="", previously=Previously.UNCHANGED)

        # failure-policy: join_key
        require_columns(
            working.columns,
            [
                source_column_label(field_map, "User ID", default=STUDENT_NUMBER),
                source_column_label(field_map, "SchoolCode", default=SCHOOL_NUMBER),
                str(cc.get("home_school_column", "")),
            ],
            entity="Students",
            guard=GuardKind.JOIN_KEY,
        )

        before = len(working)
        working = working.copy()
        school_norm = normalize_id_series(working[school_col])
        home_norm = normalize_id_series(working[home_col])
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

    def _filter_to_rostered_grades(
        self, working: pd.DataFrame, field_map: dict[str, Any], context: TransformContext
    ) -> pd.DataFrame:
        """Keep only students whose CEDS grade is in ``student_rostering_grades``.

        Opt-in (``global_config.student_rostering_grades``; absent → every grade,
        so every district without the key is byte-identical). It composes WITH
        the active-status filter rather than replacing it: an inactive in-scope
        student and an active out-of-scope student are both dropped.

        **Placed after** ``_collapse_cross_enrollment`` (one row per ``User ID``
        by then, so exactly one grade decision is made per student, taken from
        the HOME-school row) **and before** ``apply_field_map``, which converts
        the raw grade column to the output ``Grade``. It must therefore never
        rewrite that column: ``grade_to_ceds`` is not idempotent, so a converted
        value converted again ships Kindergarten as ``"UG"``.
        :func:`~src.etl.transformers.grades.filter_to_grade_scope` derives a
        temporary column and drops it, which is why that function — not
        ``split_by_homeroom_grades(keep="homeroom")`` — is the one used here.

        Fail-loud (Configurable Columns + validate-at-boundary): the grade column
        resolves through the one resolver
        (:func:`~src.etl.transformers.columns.resolve_source_column` — a bare
        string or a ``{column: ...}`` entry alike), and an unresolvable one
        RAISES, naming the column in the config's spelling. Keeping everyone
        would deliver the PII of students the district is not licensed to send,
        and "column absent" is reachable in ordinary config (a field mapped to a
        fixed ``{value: ""}`` resolves to the default, which the export may not
        carry).

        Logs the kept/total COUNT only — a per-student or per-grade breakdown
        would put student data in ``etl_tool.log`` (grade is itself student
        data); the configured scope is config, not PII, so it is named.
        """
        scope = resolve_student_scope(context.global_config or {})
        if scope is None:
            return working
        grade_col = resolve_source_column(field_map, "Grade", default=GRADE, previously=Previously.COLUMN_KEY_ONLY)
        total = len(working)
        filtered = filter_to_grade_scope(
            working,
            grade_col,
            scope,
            caller="Students",
            column_label=source_column_label(field_map, "Grade", default=GRADE),
        )
        logger.info(
            f"[Students] student_rostering_grades kept {len(filtered)}/{total} students (scope: {sorted(scope)})"
        )
        return filtered

    def _determine_enrollment_status(
        self, working: pd.DataFrame, field_map: dict[str, Any], context: TransformContext
    ) -> None:
        """Set the 'EnrollStatus' column in-place via the shared base predicate.

        Source column names (status / withdraw date) and the active-value set
        resolve from the Students ``EnrollStatus`` config (Configurable
        Columns); MyEd BC defaults apply when unconfigured. ``Active`` and
        ``PreReg`` are both retained by default (the Advanced CSV spec's expected
        ``EnrollStatus`` values; overridable via ``active_values``). The live
        status value wins; the withdraw date is only a fallback for rows with no
        status value. See ``BaseTransformer.decide_enroll_status``, whose notes — which
        signal decided, and how many rows went Active with none (plan 0053 S11, §5
        #17/#18) — are recorded here on the Students outcome.
        """
        decision = self.decide_enroll_status(working, field_map)
        working["EnrollStatus"] = decision.labels
        for note, count in decision.notes:
            context.record_outcome_note("Students", note, count)

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
        counts = normalize_id_series(dropped[status_column]).value_counts()
        return {str(k): int(v) for k, v in counts.items()}

    def _generate_emails(
        self,
        working: pd.DataFrame,
        result: pd.DataFrame,
        field_map: dict[str, Any],
        context: TransformContext,
    ) -> None:
        """Generate the ``Email Address`` column from the template, if configured.

        Opt-in extensions (default off → every existing district byte-identical):
        - ``sanitize``: reduce each substituted string to ``[a-z0-9]``.
        - ``derived_dates``: inject pseudo template fields (e.g. ``admission yy``)
          computed from a source date column (fail-loud on a missing column,
          empty on a blank/unparseable value — no garbage suffix).

        Derived pseudo-columns are injected into a LOCAL copy only, so the
        caller's ``working`` frame (later fed to ``apply_field_map``) never sees
        them.

        Row-resilient + loud (same convention as ``apply_field_map``): a row
        whose template raises ``KeyError`` (template key absent from the row)
        gets ``""`` for that cell only; every failure is aggregated into ONE
        ERROR log + one ``context.data_errors`` record — never silently blanked.
        """
        email_config = field_map.get("Email Address", {})
        if not isinstance(email_config, dict):
            return
        email_format = email_config.get("format")
        if not email_format:
            return

        sanitize = bool(email_config.get("sanitize", False))
        derived = email_config.get("derived_dates") or {}

        if derived:
            src = working.copy()
            # Every derived-date column is checked before any is derived, so one run
            # names them all (§5 #3).
            # failure-policy: join_key
            require_columns(
                src.columns,
                [str(spec["column"]) for spec in derived.values()],
                entity="Students",
                guard=GuardKind.JOIN_KEY,
            )
            for pseudo, spec in derived.items():
                col = str(spec["column"]).strip().lower()
                strf = self.friendly_date_format_to_strftime(str(spec["date_format"]))
                src[str(pseudo).strip().lower()] = src[col].apply(lambda v, f=strf: self.derive_date_part(v, f))
        else:
            src = working

        fmt = email_format.lower()
        emails: list[str] = []
        failures = 0
        first_sample = ""
        for _, row in src.iterrows():
            try:
                emails.append(self.generate_student_email(row, format_str=fmt, sanitize=sanitize))
            except KeyError as ex:
                emails.append("")
                failures += 1
                if not first_sample:
                    first_sample = f"missing template key {ex}"
        if failures:
            logger.error(
                f"Error transforming Students.Email Address: {failures} row(s) failed "
                f"(email left blank) — sample {first_sample}"
            )
            self._record_data_error(context, "Students", "Email Address", failed_rows=failures, sample=first_sample)
        result["Email Address"] = pd.Series(emails, index=src.index, dtype="object")
