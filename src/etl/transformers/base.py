"""Base transformer: the per-entity contract + thin delegation to helper modules.

``BaseTransformer`` owns what is genuinely PER-ENTITY: the abstract
``transform`` interface, the generic ``apply_field_map`` dispatch, the shared
Class-ID assignment, the zero-orphan ``filter_to_active`` roster filter, the
config-driven ``row_filters``, the active-student predicate, and the fail-loud
data-error ledger.

The stateless helper families live in focused sibling modules and are
re-exposed here as SAME-SIGNATURE delegating wrappers (compatibility surface —
subclasses, tests, and the legacy ``DataTransformer`` facade keep calling
``self.<helper>`` / ``BaseTransformer.<helper>``):

- :mod:`src.etl.transformers.grades` — CEDS mapping + homeroom/subject split
- :mod:`src.etl.transformers.dates` — date parse/format grid + school-year math
- :mod:`src.etl.transformers.course_codes` — course-code exclusion/cleaning
- :mod:`src.etl.transformers.emails` — email template interpolation
- :mod:`src.etl.transformers.ids` — ID/join-key normalization
- :mod:`src.etl.transformers.naming` — class-name construction
- :mod:`src.etl.transformers.sources` — source_files normalization + access

``datetime.now()`` is resolved ONLY in this module (the established test seam
patches ``src.etl.transformers.base.datetime``); the helper modules take
``today`` as an explicit parameter.
"""

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, NamedTuple, Optional

import pandas as pd

from src.config.models import ALLOWED_TRANSFORMS as _ALLOWED_TRANSFORMS
from src.config.models import ConfiguredField, ensure_field_mapping
from src.etl.column_names import MASTER_TIMETABLE_ID, normalize_column_name
from src.etl.errors import EtlError, GuardKind
from src.etl.outcomes import Note, OutcomeNote
from src.etl.transformers import course_codes as _course_codes
from src.etl.transformers import dates as _dates
from src.etl.transformers import emails as _emails
from src.etl.transformers import grades as _grades
from src.etl.transformers import ids as _ids
from src.etl.transformers import naming as _naming
from src.etl.transformers import sources as _sources
from src.etl.transformers.columns import Previously, require_columns, resolve_source_column, source_column_label
from src.etl.transformers.context import TransformContext
from src.etl.transformers.notes import note_identity_blanks, record_note
from src.utils.helpers import describe_exception_for_log as _describe_exception
from src.utils.helpers import describe_value_for_log as _describe_value
from src.utils.helpers import normalize_columns as _normalize_columns

logger = logging.getLogger(__name__)


class EnrollStatusDecision(NamedTuple):
    """:meth:`BaseTransformer.decide_enroll_status`'s answer: the labels, and which signal decided them."""

    labels: pd.Series
    #: ``(OutcomeNote, rows)`` for each fail-open branch taken — ``()`` when a status column decided
    #: every row that has a value and no row went Active without a signal.
    notes: tuple[Note, ...]


class BaseTransformer(ABC):
    # -----------------------------------------------------------------------
    # Allowlist of YAML-callable transform functions (security: prevents
    # arbitrary method invocation via getattr on user-supplied config).
    # Canonical set lives in src/config/models.py (single source of truth —
    # enforced fail-fast at config load by EntityConfig.validate_fields);
    # kept as a class attribute here so subclasses/tests can allow extra
    # transform methods for the defensive runtime check.
    # -----------------------------------------------------------------------
    ALLOWED_TRANSFORMS: frozenset[str] = _ALLOWED_TRANSFORMS

    # CEDS grade mapping — canonical table lives in grades.py (same object).
    CEDS_MAPPING: dict[str, str] = _grades.CEDS_MAPPING

    # -----------------------------------------------------------------------
    # Abstract interface
    # -----------------------------------------------------------------------
    @abstractmethod
    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame: ...

    # -----------------------------------------------------------------------
    # Grade helpers (delegates → grades.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def grade_to_ceds(grade_value: Any) -> str:
        """Map a raw source grade to its CEDS code (see :func:`grades.grade_to_ceds`)."""
        return _grades.grade_to_ceds(grade_value)

    # -----------------------------------------------------------------------
    # Staff role (Advanced CSV contract vocabulary)
    # -----------------------------------------------------------------------
    #: The ONLY two values `Staff.csv` → `Role` may carry (the Advanced CSV
    #: contract — `docs/developer/output-contract.md`). Single-sourced here so
    #: the two role transforms below cannot drift from each other or from the
    #: contract; `tests/test_contract.py` asserts the shipped outputs against
    #: the same vocabulary.
    STAFF_ROLE_TEACHER: str = "teacher"
    STAFF_ROLE_ADMINISTRATOR: str = "administrator"
    STAFF_ROLES: frozenset[str] = frozenset({STAFF_ROLE_TEACHER, STAFF_ROLE_ADMINISTRATOR})

    #: "This export does not say what this person is." NOT a third role — it is
    #: the absence of one, and `StaffTransformer.resolve_staff_roles` removes
    #: every row still carrying it rather than publishing a blank `Role` (which
    #: the Advanced CSV contract does not accept) or guessing at one.
    #:
    #: Empty string rather than `pd.NA` deliberately: `apply_field_map`'s
    #: per-row `transform:` path already uses `pd.NA` for a row whose transform
    #: RAISED, and those two facts must stay tellable apart in the frame — a
    #: raise is a recorded data error, an unstated role is not an error at all.
    NO_STAFF_ROLE: str = ""

    @staticmethod
    def map_role(teaching_flag: Any) -> str:
        """Map a teaching FLAG (MyEd BC's `Teaching Staff` Y/N) to a contract role.

        Exactly `"y"` (case/whitespace-insensitive) is a teacher. EVERY other
        value — `"N"`, blank, `nan`, `"Yes"` — yields :attr:`NO_STAFF_ROLE`,
        because the flag answers *does this person teach*, never *is this person
        an administrator*. A secretary, an education assistant and a principal
        are all `"N"`.

        **This used to return `administrator` for every non-`"y"` value, and
        that was a live defect** (plan 0052). `administrator` is a real
        privilege level in SpacesEDU, so the fallback silently granted it to
        support staff at every district: 44.9% of SD40's export, 49.3% of
        SD74's, and 60% of the staff Unity Christian actually ships — which is
        how it was found, by the district's own network administrator, in their
        production tenant. Absence of a teaching flag is not evidence of
        anything; the only honest answer is "this export does not say".

        Rows left with no role do NOT ship. `StaffTransformer.resolve_staff_roles`
        first rescues anyone who is teacher-of-record on a real section (MyEd's
        flag is demonstrably stale for some teachers — three of Unity's carry 26,
        26 and 16 sections between them while flagged `"N"`), then drops the rest.

        A district that wants its administrators rostered must SAY which people
        they are: a column stating the role outright, read through
        :meth:`normalize_staff_role` (SD83 repurposes MyEd BC's `Prefix` for
        exactly this). There is no way back to inferring it from this flag.
        """
        val = str(teaching_flag).strip().lower()
        return BaseTransformer.STAFF_ROLE_TEACHER if val == "y" else BaseTransformer.NO_STAFF_ROLE

    @staticmethod
    def normalize_staff_role(role_value: Any) -> str:
        """Pass through a source column that already STATES the contract role.

        For a district that populates a column with the role itself rather than a
        teaching flag (SD83 repurposes MyEd BC's `Prefix` this way). Normalizes
        whitespace and case, then requires an exact member of :attr:`STAFF_ROLES`.

        **Raises** `ValueError` on anything else — deliberately, and this is the
        whole point of the function. `apply_field_map` is per-row resilient on the
        `transform:` path, so a raise blanks THAT CELL only, records a data error
        (ERROR log + run-log `data_errors` + Run History) and lets every valid row
        keep its value. The alternative — falling back to a default role — is the
        silent-miscategorisation failure this transform exists to avoid: a value
        we do not understand must never be guessed into "administrator".

        Such a row is excluded from the output either way — a blank `Role` is not
        a value the contract accepts, so `StaffTransformer.resolve_staff_roles`
        drops it (plan 0052). What `row_filters` on the Staff entity ADDS is
        removing it BEFORE the field map, so no data error is recorded at all for
        a value the district already knows is not a role (SD83 does this for
        courtesy titles in `Prefix`).

        The message names the accepted vocabulary but NEVER echoes the cell — a
        staff-file cell can hold a person's title and the message reaches the log.
        """
        val = str(role_value).strip().lower()
        if val in BaseTransformer.STAFF_ROLES:
            return val
        raise ValueError(
            "staff role value is not one of the accepted roles "
            f"({', '.join(sorted(BaseTransformer.STAFF_ROLES))}) — check the configured "
            "source column and this entity's row_filters"
        )

    # -----------------------------------------------------------------------
    # Active-student detection (single source of truth — used by Students for
    # roster filtering and by Classes/Enrollments to drop orphan rows).
    # Source column names resolve from the Students field_map (Configurable
    # Columns rule); MyEd BC defaults apply when unconfigured.
    # -----------------------------------------------------------------------
    # Default status-column alias. Resolution picks the first spelling present
    # in the (normalized, lower-cased) frame: real two-L MyEd exports
    # ("Enrollment status") AND the one-L spelling used by the repo fixtures /
    # SD40's injected headers ("Enrolment Status"). None when neither present.
    DEFAULT_STATUS_COLUMN_ALIASES: tuple[str, ...] = ("enrollment status", "enrolment status")
    DEFAULT_WITHDRAW_DATE_COLUMN: str = "withdraw date"
    DEFAULT_ACTIVE_VALUES: tuple[str, ...] = ("Active", "PreReg")

    @classmethod
    def resolve_active_config(
        cls,
        students_field_map: dict[str, Any],
        df_columns: Any,
    ) -> tuple[Optional[str], str, list[str]]:
        """Resolve (status_column, withdraw_date_column, active_values).

        Reads the Students ``EnrollStatus`` config. When it is a dict, pulls
        ``status_column`` / ``withdraw_date_column`` / ``active_values`` (any
        absent key falls back to the default). When it is the bare-null
        sentinel (or absent), MyEd BC defaults apply:

        - ``status_column``: first of :attr:`DEFAULT_STATUS_COLUMN_ALIASES`
          **present in** ``df_columns`` (``None`` if neither → date-only).
        - ``withdraw_date_column``: :attr:`DEFAULT_WITHDRAW_DATE_COLUMN`.
        - ``active_values``: list(:attr:`DEFAULT_ACTIVE_VALUES`).

        A *configured* ``status_column`` is honored as configured and still
        presence-checked against the frame; it resolves to ``None`` when absent
        so detection falls through to the withdraw-date branch rather than
        raising — a fail-open posture :meth:`decide_enroll_status` records
        (``OutcomeNote.CONFIGURED_STATUS_COLUMN_ABSENT``, §5 #17).

        Both column keys resolve through the one resolver,
        :func:`~src.etl.transformers.columns.resolve_source_column` (plan 0053 S11
        — they were §9's one named exception): the EnrollStatus block is a
        key → column-spelling mapping like any ``source_columns`` block, and its
        keys are validated as strings at load (``FieldEnrollStatus``). A
        WHITESPACE-ONLY value keeps its pre-S11 meaning rather than the
        resolver's "blank reads the default" (no direction change): a blank
        ``status_column`` is configured-and-absent (date branch, never the
        aliases) and a blank ``withdraw_date_column`` reads no column — so every
        valid config, blank values included, reads exactly the column it read before.
        """
        present = {normalize_column_name(str(c)) for c in df_columns}
        enroll_cfg = cls._enroll_status_block(students_field_map)

        withdraw_date_column: str
        if cls._configured_but_blank(enroll_cfg, "withdraw_date_column"):
            withdraw_date_column = ""  # pre-S11: a blank configured spelling reads no column
        else:
            withdraw_date_column = resolve_source_column(
                enroll_cfg,
                "withdraw_date_column",
                default=cls.DEFAULT_WITHDRAW_DATE_COLUMN,
                previously=Previously.UNCHANGED,
            )
        raw_active = enroll_cfg.get("active_values")
        active_values = [str(v) for v in raw_active] if raw_active else list(cls.DEFAULT_ACTIVE_VALUES)

        status_column: Optional[str]
        if cls._configured_but_blank(enroll_cfg, "status_column"):
            status_column = None  # pre-S11: configured (truthy) but names no column — the date branch
        elif cls._configured_status_label(students_field_map):
            configured = resolve_source_column(
                enroll_cfg,
                "status_column",
                default=cls.DEFAULT_STATUS_COLUMN_ALIASES[0],
                previously=Previously.UNCHANGED,
            )
            # Configured but absent from this frame — fall through to the date branch.
            status_column = configured if configured in present else None
        else:
            status_column = next((alias for alias in cls.DEFAULT_STATUS_COLUMN_ALIASES if alias in present), None)

        return status_column, withdraw_date_column, active_values

    @staticmethod
    def _configured_but_blank(enroll_cfg: dict[str, Any], key: str) -> bool:
        """True when ``key`` is set to a truthy value that names no column (whitespace only).

        The pre-S11 reader tested the RAW value for truthiness (``if raw:``), so ``"  "``
        counted as configured; the resolver would read it as "use the default". Kept apart
        so neither EnrollStatus key changes which column it reads.
        """
        raw = enroll_cfg.get(key)
        return bool(raw) and isinstance(raw, str) and not raw.strip()

    @classmethod
    def _status_column_configured(cls, students_field_map: dict[str, Any]) -> bool:
        """True when the EnrollStatus block configures a ``status_column`` (a truthy value, blank or not)."""
        return bool(cls._enroll_status_block(students_field_map).get("status_column"))

    @staticmethod
    def _enroll_status_block(students_field_map: dict[str, Any]) -> dict[str, Any]:
        """The Students ``EnrollStatus`` block as a mapping (``{}`` for the bare-null sentinel)."""
        config = students_field_map.get("EnrollStatus")
        return config if isinstance(config, dict) else {}

    @classmethod
    def _configured_status_label(cls, students_field_map: dict[str, Any]) -> str:
        """The CONFIGURED status column in config spelling, or ``""`` when none is configured."""
        return source_column_label(cls._enroll_status_block(students_field_map), "status_column", default="")

    @classmethod
    def _classify_withdraw(cls, value: Any, today: date) -> tuple[bool, bool]:
        """Classify a withdraw-date cell (see :func:`dates.classify_withdraw`)."""
        return _dates.classify_withdraw(value, today)

    @classmethod
    def past_withdraw_date(cls, value: Any, today: date) -> bool:
        """True when ``value`` is a past/unparseable withdraw date (see :func:`dates.past_withdraw_date`)."""
        return _dates.past_withdraw_date(value, today)

    @classmethod
    def compute_enroll_status(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> pd.Series:
        """Per-row enrollment label (``"Active"`` / ``"Inactive"`` / any ``active_values``).

        :meth:`decide_enroll_status`'s labels — the single source of truth for
        "is this student active" (see there for the rule). Kept as the
        label-only view for callers that record no notes.
        """
        return cls.decide_enroll_status(df, students_field_map).labels

    @classmethod
    def decide_enroll_status(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> "EnrollStatusDecision":
        """Per-row enrollment labels AND the notes saying which signal decided them.

        The live status value **wins**; the withdraw date is only a fallback:

        1. If the row has a **non-blank status value** (resolved status column) →
           status decides: the trimmed value when it is in ``active_values``,
           else ``"Inactive"``. The withdraw date is NOT consulted — an
           authoritative live status beats a lingering withdraw date (e.g. a
           re-enrolled student whose prior withdraw date is still on the record).
        2. Else (no status column, or a blank status value on that row) → fall
           back to the withdraw-date column: ``"Inactive"`` for a
           past/unparseable date, ``"Active"`` otherwise.
        3. Else (neither column present) → ``"Active"`` (with one warning).

        Every fail-open branch is RECORDED (plan 0053 S11, ``failure-policy.md``
        §5 #17/#18), each note counting demographic rows, with ONE log line per
        call: at most one of ``ALL_ACTIVE_DEFAULT`` (neither column — H1, a
        WARNING-tier note) > ``CONFIGURED_STATUS_COLUMN_ABSENT`` (the configured
        status column is not in the export) > ``STATUS_COLUMN_ABSENT_DATE_ONLY``
        (no status column at all), plus ``ACTIVE_WITHOUT_POSITIVE_SIGNAL`` for the
        rows kept Active with neither a status value nor a withdraw date. The
        precedence puts ``ALL_ACTIVE_DEFAULT`` FIRST even when a configured status
        column is what is missing: a run that shipped every student Active is the
        H1 exposure, and a quieter note must never stand in for it (the log line
        still names the configured column). The DIRECTION of every branch is
        unchanged (D10 is open) — the notes only make it visible.
        """
        if df.empty:
            return EnrollStatusDecision(pd.Series([], dtype="object"), ())

        status_column, withdraw_date_column, active_values = cls.resolve_active_config(students_field_map, df.columns)
        # A truthy-but-blank configured status column is configured-and-absent (see resolve_active_config).
        status_configured = cls._status_column_configured(students_field_map)
        configured_status = cls._configured_status_label(students_field_map) or "(blank)"
        allowed = set(active_values)
        today = datetime.now().date()
        has_withdraw = withdraw_date_column in df.columns
        rows = len(df)

        # Withdraw-date label — used for any row without a usable status value.
        if has_withdraw:
            withdraw = df[withdraw_date_column]
            classified = withdraw.apply(lambda v: cls._classify_withdraw(v, today))
            date_label = classified.apply(lambda t: "Inactive" if t[0] else "Active")
            # Blank exactly as `dates.classify_withdraw` reads blank (NaN / whitespace-only).
            withdraw_blank = withdraw.isna() | withdraw.astype(str).str.strip().eq("")
        else:
            classified = None
            date_label = pd.Series("Active", index=df.index, dtype="object")
            withdraw_blank = pd.Series(True, index=df.index, dtype=bool)

        notes: list[Note] = []
        if status_column is not None:
            status_vals = _ids.normalize_id_series(df[status_column])
            has_status = status_vals.ne("") & status_vals.str.lower().ne("nan")
            status_label = status_vals.apply(lambda v: v if v in allowed else "Inactive")
            labels = status_label.where(has_status, date_label)
            date_used = ~has_status
            no_signal = int((~has_status & withdraw_blank).sum())
            unsignalled = (
                f" {no_signal} row(s) have neither a status value nor a withdraw date and are kept Active."
                if no_signal
                else ""
            )
            logger.info(
                f"[Students] Active-status resolved via status column '{status_column}' "
                f"(active values {active_values}); withdraw date used only as a per-row fallback.{unsignalled}"
            )
        elif has_withdraw:
            labels = date_label
            date_used = pd.Series(True, index=df.index, dtype=bool)
            no_signal = int(withdraw_blank.sum())
            if status_configured:
                # failure-policy: safety_heuristic
                notes.append((OutcomeNote.CONFIGURED_STATUS_COLUMN_ABSENT, rows))
                logger.warning(
                    f"[Students] The configured status column '{configured_status}' is not in the export; "
                    f"active-status for all {rows} row(s) resolved via withdraw-date column "
                    f"'{withdraw_date_column}' instead ({no_signal} with no withdraw date, kept Active)."
                )
            else:
                # failure-policy: safety_heuristic
                notes.append((OutcomeNote.STATUS_COLUMN_ABSENT_DATE_ONLY, rows))
                logger.info(
                    f"[Students] No status column present; active-status for all {rows} row(s) resolved via "
                    f"withdraw-date column '{withdraw_date_column}' ({no_signal} with no withdraw date, kept Active)."
                )
        else:
            configured = f" (configured status column '{configured_status}')" if status_configured else ""
            # failure-policy: safety_heuristic
            logger.warning(
                "[Students] Could not find an enrollment-status or withdraw-date column "
                f"(status aliases {list(cls.DEFAULT_STATUS_COLUMN_ALIASES)}{configured}, "
                f"withdraw column '{withdraw_date_column}'). Defaulting all rows to 'Active' ({rows} row(s))."
            )
            return EnrollStatusDecision(date_label, ((OutcomeNote.ALL_ACTIVE_DEFAULT, rows),))

        if no_signal:
            # failure-policy: safety_heuristic
            notes.append((OutcomeNote.ACTIVE_WITHOUT_POSITIVE_SIGNAL, no_signal))

        # Warn about unparseable withdraw dates only where the date was actually used.
        if has_withdraw and classified is not None:
            unparseable = [
                str(v).strip()
                for v, (_, bad), used in zip(df[withdraw_date_column], classified, date_used)
                if bad and used
            ]
            if unparseable:
                # Shapes, not values: a mis-mapped withdraw_date_column would
                # otherwise dump ten students' cells into the support log. The
                # distinct shapes are what actually diagnose the format mismatch.
                shapes = sorted({_describe_value(v) for v in unparseable[:10]})
                logger.warning(
                    f"[Students] Could not parse {len(unparseable)} withdraw date(s); "
                    f"treated as Inactive. Sample value shapes: {shapes}"
                )

        return EnrollStatusDecision(labels, tuple(notes))

    @classmethod
    def is_active_mask(cls, df: pd.DataFrame, students_field_map: dict[str, Any]) -> pd.Series:
        """Boolean mask of active rows: ``compute_enroll_status(...) != "Inactive"``.

        Label and mask share one function, so a district that drops ``"Active"``
        from ``active_values`` is honored (no implicit union with ``"Active"``).
        """
        return cls.compute_enroll_status(df, students_field_map) != "Inactive"

    @staticmethod
    def filter_to_active(
        df: pd.DataFrame,
        student_col: str,
        context: TransformContext,
        *,
        caller: str,
    ) -> pd.DataFrame:
        """Keep only rows whose ``student_col`` is in the active roster.

        Single source of truth for the zero-orphan filter: both the homeroom
        (demographic) and subject (schedule) student-row derivations route
        through here so no emitted student row references a ``User ID`` absent
        from ``Students.csv``. The roster is ``context.active_student_ids`` —
        published by :class:`StudentTransformer` from its filtered output.

        Matching normalizes both sides with :func:`ids.normalize_id_series`
        because the demographic ``Student Number`` and schedule ``Student ID``
        carry the same pupil-number values but may differ in incidental
        whitespace.

        Fail-safe (never filter-to-empty): when the roster is empty (Students
        disabled or ran later) or ``student_col`` is absent, return ``df``
        unchanged rather than dropping every row — and RECORD which (plan 0053
        S11): ``OutcomeNote.ACTIVE_ROSTER_UNAVAILABLE`` or
        ``ACTIVE_ROSTER_COLUMN_UNRESOLVABLE`` on the ``caller`` entity, counting
        the rows kept by the FIRST such call for that entity this run (a second
        call — Enrollments' subject filter after its homeroom one — adds nothing:
        the first count stands), with one WARNING per entity per run. ``caller``
        is the consuming ENTITY's name — the note lands on its outcome, so it is
        REQUIRED keyword-only (a default would attribute a note to an entity that
        never asked for the filter).

        When rows ARE dropped, one aggregate WARNING per call reports the row
        count and the count of DISTINCT students involved — a mixed-vintage
        input set (e.g. a schedule referencing students missing from the
        demographic) is loudly visible instead of silently shrinking output.
        Counts only: student ids/names never appear in the message (PII rule).

        Returns a new frame (copy) so callers own it and can mutate columns
        without a ``SettingWithCopyWarning``, matching the other ``filter_*``
        helpers here.
        """
        # Two fail-open postures, told apart (plan 0053 S11, §5 #14/#27(i)): each keeps the frame
        # (never filter-to-empty) and records ONE note + ONE line per entity per run.
        if not context.active_student_ids:
            if len(df):
                # failure-policy: safety_heuristic
                record_note(
                    context,
                    caller,
                    OutcomeNote.ACTIVE_ROSTER_UNAVAILABLE,
                    len(df),
                    log=logger,
                    message=(
                        f"[{caller}] active_student_ids empty — no active Students roster was published this run, "
                        f"so {len(df)} row(s) were kept without the active filter."
                    ),
                )
            return df
        if student_col not in df.columns:
            if len(df):
                # failure-policy: safety_heuristic
                record_note(
                    context,
                    caller,
                    OutcomeNote.ACTIVE_ROSTER_COLUMN_UNRESOLVABLE,
                    len(df),
                    log=logger,
                    message=(
                        f"[{caller}] ACTIVE FILTER SKIPPED — this source has no '{student_col}' column to match "
                        f"against the active Students roster, so {len(df)} row(s) were kept unfiltered."
                    ),
                )
            return df
        normalized = _ids.normalize_id_series(df[student_col])
        keep = normalized.isin(context.active_student_ids)
        dropped_rows = int((~keep).sum())
        if dropped_rows:
            distinct_students = int(normalized[~keep].nunique())
            logger.warning(
                f"[{caller}] Dropped {dropped_rows} row(s) referencing {distinct_students} "
                "student(s) absent from the active Students roster."
            )
        return df[keep].copy()  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Date helpers (delegates → dates.py; kept as methods because
    # normalize_iso_date is an ALLOWED_TRANSFORMS name resolved via getattr)
    # -----------------------------------------------------------------------
    @staticmethod
    def normalize_iso_date(value: Any) -> str:
        """Convert various date formats to ISO 8601 (see :func:`dates.normalize_iso_date`)."""
        return _dates.normalize_iso_date(value)

    @staticmethod
    def format_date(value: Any, strftime_format: str) -> str:
        """Reformat a flexible GDE date (see :func:`dates.format_date`)."""
        return _dates.format_date(value, strftime_format)

    @staticmethod
    def friendly_date_format_to_strftime(fmt: str) -> str:
        """Translate friendly tokens to strftime (see :func:`dates.friendly_date_format_to_strftime`)."""
        return _dates.friendly_date_format_to_strftime(fmt)

    @staticmethod
    def derive_date_part(value: Any, strftime_fmt: str) -> str:
        """Date part for derived email fields (see :func:`dates.derive_date_part`)."""
        return _dates.derive_date_part(value, strftime_fmt)

    # -----------------------------------------------------------------------
    # Text / column / ID helpers (delegates)
    # -----------------------------------------------------------------------
    @staticmethod
    def truncate_name(name: str, max_len: int = 100) -> str:
        """Word-boundary truncation (see :func:`naming.truncate_name`)."""
        return _naming.truncate_name(name, max_len)

    @staticmethod
    def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Strip whitespace and lowercase all column names."""
        return _normalize_columns(df)

    @staticmethod
    def clean_invalid_ids(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
        """Remove rows with NaN/empty/'nan' ids (see :func:`ids.clean_invalid_ids`)."""
        return _ids.clean_invalid_ids(df, id_col)

    # -----------------------------------------------------------------------
    # Course-code helpers (delegates → course_codes.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def filter_excluded_course_codes(df: pd.DataFrame, excluded_codes: list[str]) -> pd.DataFrame:
        """Drop rows by exact excluded course code (see :func:`course_codes.filter_excluded_course_codes`)."""
        return _course_codes.filter_excluded_course_codes(df, excluded_codes)

    @staticmethod
    def filter_excluded_course_code_patterns(
        df: pd.DataFrame,
        patterns: list[str],
        column: Optional[str] = None,
    ) -> pd.DataFrame:
        """Drop rows by course-code regex (see :func:`course_codes.filter_excluded_course_code_patterns`)."""
        return _course_codes.filter_excluded_course_code_patterns(df, patterns, column)

    @staticmethod
    def early_grade_exclusion_pattern(start_grade: Any) -> Optional[str]:
        """Early-grade course-code floor regex (see :func:`course_codes.early_grade_exclusion_pattern`)."""
        return _course_codes.early_grade_exclusion_pattern(start_grade)

    @staticmethod
    def effective_course_code_patterns(global_config: dict) -> list[str]:
        """Configured patterns + derived grade floor (see :func:`course_codes.effective_course_code_patterns`)."""
        return _course_codes.effective_course_code_patterns(global_config)

    @staticmethod
    def clean_course_code_flavor(code: Any, flavors: list[str]) -> str:
        """Flavor-substring truncation (see :func:`course_codes.clean_course_code_flavor`)."""
        return _course_codes.clean_course_code_flavor(code, flavors)

    # -----------------------------------------------------------------------
    # Config-driven row filtering (per-entity contract)
    # -----------------------------------------------------------------------
    @staticmethod
    def apply_row_filters(
        df: pd.DataFrame,
        filters: list[dict[str, Any]],
        entity_name: str,
    ) -> pd.DataFrame:
        """Keep only rows matching every config-driven ``row_filter`` (AND-combined).

        Each filter is a raw dict ``{"column": str, "include": [str, ...]}`` (as
        produced by ``MappingConfig.to_raw_dict``). A row survives when, for EVERY
        filter, its ``column`` value (trimmed + lower-cased) is in that filter's
        ``include`` set (also trimmed + lower-cased) — successive filters further
        narrow the frame. Columns are matched against the already-normalized
        (lowercase) frame, so the filter column is resolved with the same
        strip+lower treatment.

        Fail-loud (validate at boundary): EVERY filter column is checked before
        any filtering, through :func:`~src.etl.transformers.columns.require_columns`
        — one or more absent columns raise ONE
        :class:`~src.etl.errors.SourceSchemaError` (guard ``PII_SCOPE``) naming
        them all in the CONFIG's spelling — a renamed source column must never
        silently keep everyone or no one, and an admin fixing the export should
        learn every missing column from one run, not one per night. The message
        carries the COUNT of source columns, never their names (§8: an observed
        header can be a pupil). Empty/absent ``filters`` return ``df``
        unchanged. Only the kept/total COUNT is logged (never row values / PII).
        """
        if not filters:
            return df
        # failure-policy: pii_scope
        require_columns(
            df.columns,
            [str(row_filter["column"]) for row_filter in filters],
            entity=entity_name,
            guard=GuardKind.PII_SCOPE,
        )
        total = len(df)
        mask = pd.Series(True, index=df.index)
        for row_filter in filters:
            col = str(row_filter["column"]).strip().lower()
            include = {str(v).strip().lower() for v in row_filter.get("include", [])}
            values = _ids.normalize_id_series(df[col]).str.lower()
            mask &= values.isin(include)
        kept = int(mask.sum())
        logger.info(f"[{entity_name}] row_filters kept {kept}/{total} rows")
        return df[mask].copy()  # type: ignore[return-value]

    # -----------------------------------------------------------------------
    # Source-file access (delegates → sources.py)
    # -----------------------------------------------------------------------
    @staticmethod
    def normalize_source_config(source_config: Any) -> dict[str, str]:
        """Canonicalize source_files config to {role: filename} (see :func:`sources.normalize_source_config`)."""
        return _sources.normalize_source_config(source_config)

    def get_source_file(self, context: TransformContext, source_config: Any, role: str) -> pd.DataFrame:
        """Resolve a role to a copied frame from raw_data (see :func:`sources.get_source_file`)."""
        return _sources.get_source_file(context, source_config, role)

    # -----------------------------------------------------------------------
    # Date resolution (per-entity contract). Source-COLUMN resolution is
    # ``columns.resolve_source_column`` — the one resolver (plan 0053 S9).
    # -----------------------------------------------------------------------
    def resolve_date(self, field_map: dict[str, Any], field_name: str, context: TransformContext) -> str:
        """Resolve a date field from config — either a fixed value or academic year date.

        Eliminates the 4x repeated use_academic_year / value / fallback pattern.
        """
        config = field_map.get(field_name, {})
        if isinstance(config, dict) and "value" in config and not config.get("use_academic_year"):
            return config["value"]
        return context.academic_start if field_name == "Start Date" else context.academic_end

    # -----------------------------------------------------------------------
    # Field generation helpers
    # -----------------------------------------------------------------------
    def generate_class_id(self, row: pd.Series, mt_id_col: str, append_year: bool, context: TransformContext) -> str:
        mt_id = row.get(mt_id_col, "")
        if mt_id and append_year:
            return f"{mt_id}_{context.school_year}"
        return mt_id

    def assign_class_ids(
        self, df: pd.DataFrame, field_map: dict, context: TransformContext, *, entity: str
    ) -> pd.DataFrame:
        """Assign Class ID column using blended_class_map with generate_class_id fallback.

        Shared by ClassTransformer and EnrollmentTransformer to ensure IDs
        are computed identically across Classes and Enrollments output.

        Fail-closed (plan 0053 S10, ``failure-policy.md`` §5 #29): the Class ID
        source column (the ``Class ID`` mapping's column, default Master Timetable
        ID) is REQUIRED — without it every subject ``Class ID`` used to ship
        blank. ``entity`` is the caller's (required keyword-only: the error names
        whose source lacks the column; ``run_transform`` decides the scope).
        """
        mt_id_col = resolve_source_column(
            field_map, "Class ID", default=MASTER_TIMETABLE_ID, previously=Previously.COLUMN_KEY_ONLY
        )
        # failure-policy: join_key
        require_columns(
            df.columns,
            [source_column_label(field_map, "Class ID", default=MASTER_TIMETABLE_ID)],
            entity=entity,
            guard=GuardKind.JOIN_KEY,
        )
        df[mt_id_col] = _ids.normalize_id_series(df[mt_id_col])
        df["Class ID"] = df[mt_id_col].map(context.blended_class_map)
        fallback = df.apply(
            lambda row: self.generate_class_id(row, mt_id_col=mt_id_col, append_year=True, context=context),
            axis=1,
        )
        df["Class ID"] = df["Class ID"].fillna(fallback)
        return df

    def generate_class_name(
        self,
        row: pd.Series,
        teacher_flag_col: str,
        teacher_last_col: str,
        course_title_col: str,
        section_letter_col: str,
        context: TransformContext,
    ) -> str:
        """Build a subject-class display name (see :func:`naming.generate_class_name`)."""
        return _naming.generate_class_name(
            row, teacher_flag_col, teacher_last_col, course_title_col, section_letter_col, context
        )

    @staticmethod
    def generate_student_email(row: pd.Series, format_str: str, sanitize: bool = False) -> str:
        """Interpolate row values into an email template (see :func:`emails.generate_student_email`)."""
        return _emails.generate_student_email(row, format_str, sanitize=sanitize)

    @staticmethod
    def generate_user_role(row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return "teacher"
        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return "student"
        return "unknown"

    @staticmethod
    def generate_user_id(row: pd.Series, staff_id_col: str, student_id_col: str) -> str:
        staff_val = row.get(staff_id_col, "")
        if pd.notna(staff_val) and str(staff_val).strip() != "":
            return str(staff_val)
        student_val = row.get(student_id_col, "")
        if pd.notna(student_val) and str(student_val).strip() != "":
            return str(student_val)
        return "UNKNOWN_ID"

    # -----------------------------------------------------------------------
    # School-year determination (delegates → dates.py; now() resolved HERE so
    # the `src.etl.transformers.base.datetime` test seam keeps working)
    # -----------------------------------------------------------------------
    @classmethod
    def determine_school_year(
        cls,
        all_data: dict[str, pd.DataFrame],
        source_config: Any,
        rollover_month_day: str,
        today: Optional[date] = None,
        school_year_naming: str = "end",
    ) -> int:
        """Return the academic year's END year (MyEd BC "School Year" convention).

        The pipeline always works in end-year semantics internally. Source
        formats are detected and translated:

        - ``YYYY/YYYY`` or ``YYYY-YYYY`` → second year (unambiguous)
        - ``YYYY`` → depends on ``school_year_naming``:

            - ``"end"`` (default, MyEd BC): treat as end year, return as-is
            - ``"start"`` (Ontario / US): treat as start year, return ``year + 1``

        Falls back to ``today`` (or now) when no source has a recognised
        value. Past ``rollover_month_day`` (default 07-25, the typical
        academic_end) the fallback rolls forward to the next academic
        year — accommodating districts that load upcoming-year exports a
        few weeks before the new year officially starts. Districts that
        upload even earlier can lower the rollover via the
        ``academic_year_rollover_month_day`` global_config field.

        All configured sources are scanned; the FIRST parsed value is used
        (behavior-preserving), but when the sources disagree — a mixed-vintage
        input set that would silently produce wrong academic dates and Class
        IDs — one loud WARNING names every end year found and which was chosen
        (see :func:`dates.determine_school_year`).
        """
        return cls.determine_school_year_detailed(
            all_data,
            source_config,
            rollover_month_day,
            today,
            school_year_naming,
        ).resolved_year

    @classmethod
    def determine_school_year_detailed(
        cls,
        all_data: dict[str, pd.DataFrame],
        source_config: Any,
        rollover_month_day: str,
        today: Optional[date] = None,
        school_year_naming: str = "end",
    ) -> _dates.SchoolYearDetermination:
        """Full provenance for this run's school-year determination — diagnostics only.

        Same ``today``/``normalize_source_config`` resolution as
        :meth:`determine_school_year` (this is the one place ``today`` is
        resolved, so the ``src.etl.transformers.base.datetime`` test seam
        keeps working for both methods); delegates to
        :func:`dates.determine_school_year_detailed` for the actual scan.
        """
        return _dates.determine_school_year_detailed(
            all_data,
            cls.normalize_source_config(source_config),
            rollover_month_day,
            today or datetime.now().date(),
            school_year_naming,
        )

    @staticmethod
    def _parse_school_year_to_end(raw: str, naming: str = "end") -> Optional[int]:
        """Parse a 'school year' cell to the END year (see :func:`dates.parse_school_year_to_end`)."""
        return _dates.parse_school_year_to_end(raw, naming)

    # -----------------------------------------------------------------------
    # Generic field-map application (used by Students, Staff, Family)
    # -----------------------------------------------------------------------
    def apply_field_map(
        self,
        working: pd.DataFrame,
        result: pd.DataFrame,
        field_map: dict[str, Any],
        entity: str,
        context: TransformContext,
    ) -> pd.DataFrame:
        """Apply the generic YAML field_map to produce output columns.

        Thin TYPED dispatch: each entry is normalized ONCE at this boundary
        via ``ensure_field_mapping`` (already-typed variants from the validated
        ``MappingConfig`` pass through untouched; raw YAML-shaped values from
        direct callers are classified) and then dispatched to the variant's
        ``.apply(...)`` Strategy (``src/config/models.py``). The legacy
        per-branch dict sniffing is gone; the semantics are unchanged:

        Fail-loud, never silent, never fails the run (data errors are a
        separate axis from ETL success). NOTE: only the ``transform:`` path is
        per-row resilient — other computed branches (e.g. ``append_year_to_id``)
        blank the whole column on any row failure (recorded as a column-level
        error, still never silent):

        - **Per-row resilience (``transform:`` path).** A ``transform:`` is applied
          per-row; a row whose ``func`` raises gets ``pd.NA`` in **that cell only**
          while every other row keeps its correct value. Those per-row failures are
          recorded.
        - **Column-level errors** — an unknown transform name (config error — now
          also rejected fail-fast at CONFIG LOAD by ``EntityConfig``, so on the
          pipeline path it cannot reach this loop; the check in
          ``FieldTransform.apply`` is defensive for direct callers), the
          ``append_year_to_id`` row-wise branch, or any structural failure — blank
          the whole column and continue (do NOT raise), recorded the same loud way.
          The ``append_year_to_id`` branch is deliberately **column-level**, not
          per-row: its helper ``generate_class_id`` performs no fallible
          operation (a ``row.get`` plus an f-string — it cannot raise on a single
          row), so a per-row try/except would defend a failure that cannot occur
          and only add a second near-duplicate resilience loop. Promote it to
          per-row ONLY if ``generate_class_id`` ever gains a parse/IO step that
          can raise on one row (Plan 0008, won't-fix-by-decision).
        - Every recorded failure appends a record to ``context.data_errors`` and
          logs at ERROR; ``run_pipeline`` surfaces a summary into the run-log
          (``data_errors``) and Run History — never swallowed.
        - **Intended blank** (the config column is simply absent from the frame)
          is NOT an error: it stays ``pd.NA`` and is NOT recorded.
        - **Typed failures propagate.** An :class:`~src.etl.errors.EtlError`
          raised inside a strategy or a per-row transform (e.g. a
          ``SourceSchemaError`` from a guard) is re-raised at BOTH levels, never
          demoted to a blank cell or column — its scope is the orchestrator's
          decision (plan 0053 S1, ``failure-policy.md`` §6).
        """
        # The fields a caller filled before the loop: the loop skips them, and so does the note.
        prefilled = frozenset(result.columns)
        for tgt_field, raw_spec in field_map.items():
            try:
                if tgt_field in result.columns:
                    continue

                spec = ensure_field_mapping(raw_spec)
                if isinstance(spec, ConfiguredField):
                    result[tgt_field] = spec.apply(working, self, tgt_field, entity, context)
                elif isinstance(spec, dict):
                    # classify_field's warn-passthrough (unrecognized dict
                    # structure): no usable 'column' key by definition — the
                    # legacy loop yielded an intended blank. NOT recorded.
                    result[tgt_field] = pd.NA
                else:
                    # Bare column name (str) or the auto-detect None sentinel —
                    # the direct read. An absent column is an intended blank
                    # (NOT an error — do not record).
                    col = str(spec).lower()
                    result[tgt_field] = working[col] if col in working.columns else pd.NA

            except EtlError:
                # A TYPED failure (e.g. a SourceSchemaError from a guard inside a
                # transform) is a decision already made about scope — it must
                # reach the orchestrator, never be demoted to a blank column.
                raise
            except Exception as ex:
                # Column-level error (unknown transform or any structural
                # failure). Blank the column, record loudly, continue — never
                # silently swallow, never fail the run.
                logger.error(f"Error transforming {entity}.{tgt_field}: {ex}")
                self._record_data_error(context, entity, tgt_field, failed_rows=len(working), sample=str(ex))
                result[tgt_field] = pd.NA

        # §5 #19 (plan 0053 S11): an identity field the loop could only blank is recorded (notes.py).
        note_identity_blanks(working.columns, len(working), field_map, entity, context, prefilled=prefilled)
        return result

    def _apply_transform_resilient(
        self,
        series: pd.Series,
        func: Any,
        entity: str,
        tgt_field: str,
        context: TransformContext,
    ) -> list[Any]:
        """Apply ``func`` per-row so a single bad row blanks only that cell.

        A row whose ``func`` raises gets ``pd.NA`` for that cell; every other
        row keeps its correctly-transformed value. Per-row failures are
        aggregated into ``context.data_errors`` and logged at ERROR — never
        silently swallowed, never aborting the whole column or the run.
        """
        out: list[Any] = []
        failures = 0
        first_sample = ""
        for value in series:
            try:
                out.append(func(value))
            except EtlError:
                raise  # typed: never demoted to a blank cell (see ``apply_field_map``)
            except Exception as ex:  # noqa: BLE001 — per-row isolation; recorded below
                out.append(pd.NA)
                failures += 1
                if not first_sample:
                    # The failing cell is student PII (a district field_map may
                    # attach normalize_iso_date to a DOB, truncate_name to a
                    # legal name) and this log ships to support — so BOTH the
                    # value and the exception message (stdlib parsers echo their
                    # input) go through the log-safety seam. Shape + error type
                    # only; never the content.
                    first_sample = f"{_describe_value(value)} → {_describe_exception(ex)}"
        if failures:
            logger.error(
                f"Error transforming {entity}.{tgt_field}: {failures} row(s) failed "
                f"(blanked that cell only) — sample {first_sample}"
            )
            self._record_data_error(context, entity, tgt_field, failed_rows=failures, sample=first_sample)
        return out

    @staticmethod
    def _record_data_error(
        context: TransformContext,
        entity: str,
        field_name: str,
        failed_rows: int,
        sample: str,
    ) -> None:
        """Append one fail-loud data-error record to the run's shared ledger."""
        context.data_errors.append(
            {
                "entity": entity,
                "field": field_name,
                "failed_rows": failed_rows,
                "sample": sample,
            }
        )
