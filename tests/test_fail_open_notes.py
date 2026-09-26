"""Every fail-open posture records a note, none is silent (plan 0053 S11).

``docs/developer/failure-policy.md`` §5 (c)/(d)/(e) and the (b) reads whose direction is still
open: each site keeps shipping exactly what it shipped before, and now logs at most ONE
aggregated line per entity per run and records ONE closed ``OutcomeNote`` counting what it
concerns. Pinned here, each absence with the positive twin that proves the mechanism fires:

* every note fires ONCE when its posture is taken and never when it is not;
* the student-status precedence (``ALL_ACTIVE_DEFAULT`` > ``CONFIGURED_STATUS_COLUMN_ABSENT`` >
  ``STATUS_COLUMN_ABSENT_DATE_ONLY``, one per run) and a Unity-shaped demographic export (no
  status column) recording ``STATUS_COLUMN_ABSENT_DATE_ONLY`` end to end;
* the delivered rows are what they were (the labels ``compute_enroll_status`` returns, the frame
  ``filter_to_active`` passes through, the staff kept);
* a sentinel NAME and EMAIL planted in cells reach no new log line and no note;
* the notes survive the record round trip, a legacy record reads as none, and the tiers: only
  ``ALL_ACTIVE_DEFAULT`` (and S10's co-teacher note) warns; every other note is Run History row
  detail only.

All data is synthetic.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.etl.errors import SourceSchemaError
from src.etl.outcomes import EntityOutcome, OutcomeNote, OutcomeReason, outcomes_from_record, outcomes_to_record
from src.etl.pipeline import run_pipeline
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import BlendedClassDetector
from src.etl.transformers.classes import ClassTransformer
from src.etl.transformers.context import TransformContext
from src.etl.transformers.course_codes import note_unapplied_exclusions
from src.etl.transformers.family import FamilyTransformer
from src.etl.transformers.notes import record_note
from src.etl.transformers.staff import StaffTransformer
from src.etl.transformers.student_attendance import StudentAttendanceTransformer
from src.etl.transformers.student_courses import StudentCoursesTransformer
from src.etl.transformers.students import StudentTransformer
from src.history.store import read_run_records
from src.ui_flet.failure_copy import (
    NOTE_TIER,
    detail_note_labels,
    outcome_tier,
    partial_copy,
    partial_label,
    warning_outcomes,
)
from src.ui_flet.home_status import LatestReason, classify_latest_reason
from src.ui_flet.run_history import to_run_row
from src.ui_flet.verdict import Verdict
from tests.test_contract import _create_unitychristian_inputs
from tests.test_etl_errors import SENTINEL_PII

#: A planted email address (IANA-reserved domain, so the tracked-file email scan allows it).
SENTINEL_EMAIL = "sentinel.pupil@example.org"

_N = OutcomeNote


def _ctx() -> TransformContext:
    return TransformContext()


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _no_sentinel(caplog: pytest.LogCaptureFixture, ctx: TransformContext) -> None:
    text = " ".join(_messages(caplog)) + repr(ctx.outcome_notes)
    assert SENTINEL_PII not in text and SENTINEL_EMAIL not in text


# --------------------------------------------------------------------------- #
# The one helper                                                               #
# --------------------------------------------------------------------------- #
class TestRecordNote:
    def test_first_call_records_and_logs_once_the_second_neither(self, caplog):
        ctx, log = _ctx(), logging.getLogger("tests.notes")
        with caplog.at_level(logging.WARNING, logger="tests.notes"):
            assert record_note(ctx, "Staff", _N.STAFF_FILTER_WOULD_EMPTY, 4, log=log, message="first")
            assert not record_note(ctx, "Staff", _N.STAFF_FILTER_WOULD_EMPTY, 9, log=log, message="second")
        assert _messages(caplog) == ["first"]
        assert ctx.outcome_notes_for("Staff") == ((_N.STAFF_FILTER_WOULD_EMPTY, 4),)

    def test_twin_another_entity_records_its_own(self, caplog):
        ctx, log = _ctx(), logging.getLogger("tests.notes")
        with caplog.at_level(logging.WARNING, logger="tests.notes"):
            record_note(ctx, "Family", _N.ACTIVE_ROSTER_UNAVAILABLE, 2, log=log, message="family")
            record_note(ctx, "Enrollments", _N.ACTIVE_ROSTER_UNAVAILABLE, 3, log=log, message="enrollments")
        assert _messages(caplog) == ["family", "enrollments"]

    def test_a_count_below_one_is_a_caller_bug(self):
        with pytest.raises(ValueError):
            record_note(_ctx(), "Staff", _N.STAFF_FILTER_WOULD_EMPTY, 0, log=logging.getLogger("t"), message="m")


# --------------------------------------------------------------------------- #
# Students: which signal decided "active" (§5 #17/#18)                          #
# --------------------------------------------------------------------------- #
def _demo(**columns: list) -> pd.DataFrame:
    base = {"student number": ["S1", "S2", "S3"], "legal first name": [SENTINEL_PII, "B", "C"]}
    return pd.DataFrame({**base, **columns})


class TestTheStudentStatusNotes:
    def test_a_status_column_that_decides_every_row_records_nothing(self):
        decision = BaseTransformer.decide_enroll_status(_demo(**{"enrollment status": ["Active"] * 3}), {})
        assert decision.notes == ()

    def test_twin_a_row_with_no_status_and_no_withdraw_date_is_counted(self):
        decision = BaseTransformer.decide_enroll_status(
            _demo(**{"enrollment status": ["Active", "", "Inactive"], "withdraw date": ["", "", ""]}), {}
        )
        assert decision.notes == ((_N.ACTIVE_WITHOUT_POSITIVE_SIGNAL, 1),)

    def test_no_status_column_is_date_only_and_counts_the_unsignalled(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.transformers.base"):
            decision = BaseTransformer.decide_enroll_status(_demo(**{"withdraw date": ["", "01-Jan-2000", ""]}), {})
        assert decision.notes == ((_N.STATUS_COLUMN_ABSENT_DATE_ONLY, 3), (_N.ACTIVE_WITHOUT_POSITIVE_SIGNAL, 2))
        assert list(decision.labels) == ["Active", "Inactive", "Active"]
        assert sum("No status column present" in m for m in _messages(caplog)) == 1

    def test_a_configured_status_column_that_is_absent_wins_over_date_only(self, caplog):
        field_map = {"EnrollStatus": {"status_column": "Pupil State"}}
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.base"):
            decision = BaseTransformer.decide_enroll_status(_demo(**{"withdraw date": ["x", "", ""]}), field_map)
        assert decision.notes[0] == (_N.CONFIGURED_STATUS_COLUMN_ABSENT, 3)
        assert _N.STATUS_COLUMN_ABSENT_DATE_ONLY not in dict(decision.notes)
        assert any("'Pupil State'" in m for m in _messages(caplog))

    def test_neither_column_is_the_all_active_default_alone(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.base"):
            decision = BaseTransformer.decide_enroll_status(_demo(), {})
        assert decision.notes == ((_N.ALL_ACTIVE_DEFAULT, 3),)
        assert list(decision.labels) == ["Active"] * 3
        assert sum("Defaulting all rows to 'Active'" in m for m in _messages(caplog)) == 1

    def test_the_all_active_default_outranks_a_configured_absent_status_column(self, caplog):
        field_map = {"EnrollStatus": {"status_column": "Pupil State"}}
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.base"):
            decision = BaseTransformer.decide_enroll_status(_demo(), field_map)
        assert decision.notes == ((_N.ALL_ACTIVE_DEFAULT, 3),)
        assert any("'Pupil State'" in m for m in _messages(caplog)), "the log still names the configured column"

    def test_the_labels_are_exactly_compute_enroll_status(self):
        frame = _demo(**{"enrollment status": ["Active", "", "PreReg"], "withdraw date": ["", "01-Jan-2000", ""]})
        assert list(BaseTransformer.decide_enroll_status(frame, {}).labels) == list(
            BaseTransformer.compute_enroll_status(frame, {})
        )

    def test_the_enroll_status_columns_resolve_through_the_one_resolver(self):
        """§9 (S11): a configured withdraw column spelled with case/space noise reads the same column."""
        field_map = {"EnrollStatus": {"withdraw_date_column": "  Left On  "}}
        status, withdraw, _values = BaseTransformer.resolve_active_config(field_map, ["left on"])
        assert (status, withdraw) == (None, "left on")
        assert BaseTransformer.resolve_active_config({"EnrollStatus": None}, ["enrolment status"])[:2] == (
            "enrolment status",
            "withdraw date",
        )

    def test_the_student_transformer_records_the_notes_on_students(self, caplog):
        ctx = _ctx()
        mapping = {"field_map": {"User ID": "Student Number", "First Name": "Legal First Name"}}
        with caplog.at_level(logging.INFO):
            out = StudentTransformer().transform(_demo(**{"withdraw date": ["", "", ""]}), mapping, ctx)
        assert len(out) == 3
        assert dict(ctx.outcome_notes_for("Students")) == {
            _N.STATUS_COLUMN_ABSENT_DATE_ONLY: 3,
            _N.ACTIVE_WITHOUT_POSITIVE_SIGNAL: 3,
            _N.EMAIL_OUTPUT_NOT_MAPPED: 3,
        }
        _no_sentinel(caplog, ctx)

    def test_twin_a_status_column_and_an_email_column_record_nothing(self):
        ctx = _ctx()
        mapping = {"field_map": {"User ID": "Student Number", "Email Address": "Email"}}
        frame = _demo(**{"enrollment status": ["Active"] * 3, "email": ["a@example.org"] * 3})
        StudentTransformer().transform(frame, mapping, ctx)
        assert ctx.outcome_notes_for("Students") == ()


@pytest.mark.integration
def test_a_unity_shaped_demographic_records_date_only_end_to_end(tmp_path: Path) -> None:
    """Unity's demographic export carries no Enrollment Status: the run records which signal decided.

    Its 20 alumni (blank withdraw date) are NOT countable by any signal — they are part of the
    `active_without_positive_signal` population, never a count of their own (plan honesty note)."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_unitychristian_inputs(inp)
    demo = pd.read_csv(inp / "StudentDemographicInformation.txt", dtype=str)
    status_columns = [c for c in demo.columns if c.strip().lower() in ("enrollment status", "enrolment status")]
    assert status_columns, "precondition: the fixture carries a status column to remove"
    # Unity's real export: no status column, a withdraw-date column that is blank for current pupils.
    demo = demo.drop(columns=status_columns).assign(**{"Withdraw date": ""})
    demo.to_csv(inp / "StudentDemographicInformation.txt", index=False)
    run_pipeline("unitychristianmyedbc", str(inp), str(out))
    (record, *_rest) = read_run_records()
    students = record["entity_outcomes"]["Students"]
    assert students["kind"] == "built"
    assert students["notes"][_N.STATUS_COLUMN_ABSENT_DATE_ONLY.value] >= 1
    assert classify_latest_reason(record, prior_build=None) is LatestReason.CLEAN, "row detail only — never amber"
    assert "no status column, withdraw dates used" in to_run_row(record, prior_build=None).notes


# --------------------------------------------------------------------------- #
# The roster filter (§5 #14, #27(i))                                            #
# --------------------------------------------------------------------------- #
class TestTheRosterFilterNotes:
    def test_an_empty_roster_is_recorded_once_on_the_caller(self, caplog):
        ctx = _ctx()
        frame = pd.DataFrame({"student number": ["S1", "S2"], "name": [SENTINEL_PII, "x"]})
        with caplog.at_level(logging.WARNING):
            first = BaseTransformer.filter_to_active(frame, "student number", ctx, caller="Enrollments")
            BaseTransformer.filter_to_active(frame, "student number", ctx, caller="Enrollments")
        assert first.equals(frame)
        assert ctx.outcome_notes_for("Enrollments") == ((_N.ACTIVE_ROSTER_UNAVAILABLE, 2),)
        assert sum("active_student_ids empty" in m for m in _messages(caplog)) == 1
        _no_sentinel(caplog, ctx)

    def test_an_absent_student_column_is_its_own_note(self):
        ctx = _ctx()
        ctx.active_student_ids = {"S1"}
        BaseTransformer.filter_to_active(pd.DataFrame({"other": ["S1"]}), "student number", ctx, caller="Family")
        assert ctx.outcome_notes_for("Family") == ((_N.ACTIVE_ROSTER_COLUMN_UNRESOLVABLE, 1),)

    def test_twin_a_roster_and_the_column_filter_and_record_nothing(self):
        ctx = _ctx()
        ctx.active_student_ids = {"S1"}
        out = BaseTransformer.filter_to_active(
            pd.DataFrame({"student number": ["S1", "S2"]}), "student number", ctx, caller="Enrollments"
        )
        assert list(out["student number"]) == ["S1"]
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# Staff (§5 #12, #13, #21)                                                      #
# --------------------------------------------------------------------------- #
def _staff(status: list[str] | None) -> pd.DataFrame:
    frame = {"teacher id": ["T1", "T2"], "email address": [SENTINEL_EMAIL, "b@example.org"]}
    if status is not None:
        frame["staff status"] = status
    return pd.DataFrame(frame)


class TestTheStaffNotes:
    @pytest.mark.parametrize(
        ("status", "note"),
        [
            (None, _N.STAFF_STATUS_COLUMN_ABSENT),
            (["A", "I"], _N.STAFF_STATUS_VOCABULARY_UNRECOGNISED),
            (["Inactive", "Inactive"], _N.STAFF_FILTER_WOULD_EMPTY),
        ],
    )
    def test_each_pass_through_keeps_every_row_and_records_its_note(self, status, note, caplog):
        ctx = _ctx()
        with caplog.at_level(logging.WARNING):
            kept = StaffTransformer.filter_departed_staff(_staff(status), {}, context=ctx)
        assert len(kept) == 2
        assert ctx.outcome_notes_for("Staff") == ((note, 2),)
        assert len(_messages(caplog)) == 1
        _no_sentinel(caplog, ctx)

    def test_twin_a_recognised_vocabulary_filters_and_records_nothing(self):
        ctx = _ctx()
        kept = StaffTransformer.filter_departed_staff(_staff(["Active", "Inactive"]), {}, context=ctx)
        assert list(kept["teacher id"]) == ["T1"]
        assert ctx.outcome_notes == []

    def _roster_run(self, roster: pd.DataFrame) -> tuple[pd.DataFrame, TransformContext]:
        ctx = _ctx()
        staff = _staff(["Active", "Active"])
        ctx.raw_data = {"Staff.txt": staff, "Roster.txt": roster}
        mapping = {"source_files": {"staff_info": "Staff.txt", "roster": "Roster.txt"}}
        return StaffTransformer()._merge_roster(staff, mapping, ctx), ctx

    def test_a_skipped_roster_merge_over_present_files_is_recorded(self):
        merged, ctx = self._roster_run(pd.DataFrame({"teacher id": ["T1"], "other": ["x"]}))
        assert "staff sourceid" not in merged.columns
        assert ctx.outcome_notes_for("Staff") == ((_N.ROSTER_MERGE_SKIPPED, 2),)

    def test_twin_a_roster_with_its_source_id_merges_and_records_nothing(self):
        merged, ctx = self._roster_run(pd.DataFrame({"teacher id": ["T1"], "staff sourceid": ["R1"]}))
        assert "staff sourceid" in merged.columns
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# Family and Students email (§5 #20, #40)                                        #
# --------------------------------------------------------------------------- #
class TestTheEmailNotes:
    def test_contacts_excluded_for_a_blank_email_are_counted(self, caplog):
        ctx = _ctx()
        result = pd.DataFrame({"Email": [SENTINEL_EMAIL, "", None], "First Name": [SENTINEL_PII, "b", "c"]})
        with caplog.at_level(logging.WARNING):
            kept = FamilyTransformer._exclude_rows_without_email(result, ctx)
        assert len(kept) == 1
        assert ctx.outcome_notes_for("Family") == ((_N.CONTACTS_EXCLUDED_NO_EMAIL, 2),)
        _no_sentinel(caplog, ctx)

    def test_no_email_column_is_recorded_and_the_rows_pass(self):
        ctx = _ctx()
        kept = FamilyTransformer._exclude_rows_without_email(pd.DataFrame({"First Name": ["a", "b"]}), ctx)
        assert len(kept) == 2
        assert ctx.outcome_notes_for("Family") == ((_N.EMAIL_OUTPUT_NOT_MAPPED, 2),)

    def test_twin_every_contact_with_an_email_records_nothing(self):
        ctx = _ctx()
        FamilyTransformer._exclude_rows_without_email(pd.DataFrame({"Email": ["a@example.org"]}), ctx)
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# The field-map engine: identity fields (§5 #19)                                #
# --------------------------------------------------------------------------- #
class TestTheIdentityNote:
    def _apply(self, field_map: dict, frame: pd.DataFrame) -> tuple[pd.DataFrame, TransformContext]:
        ctx = _ctx()
        return FamilyTransformer().apply_field_map(frame, pd.DataFrame(), field_map, "Family", ctx), ctx

    def test_an_identity_field_with_no_source_column_ships_blank_and_is_recorded(self, caplog):
        frame = pd.DataFrame({"first name": [SENTINEL_PII, "b"]})
        with caplog.at_level(logging.WARNING):
            result, ctx = self._apply({"Student User ID": "Pupil No", "First Name": "First Name"}, frame)
        assert result["Student User ID"].isna().all()
        assert ctx.outcome_notes_for("Family") == ((_N.IDENTITY_FIELD_BLANKED, 2),)
        assert any("'Pupil No'" in m for m in _messages(caplog))
        _no_sentinel(caplog, ctx)

    def test_twin_present_column_fixed_value_and_non_identity_blank_record_nothing(self):
        frame = pd.DataFrame({"pupil no": ["1"]})
        _result, ctx = self._apply(
            {"Student User ID": "Pupil No", "School ID": {"value": "100"}, "Phone": "Absent Column"}, frame
        )
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# Course-code exclusions (§5 #32)                                               #
# --------------------------------------------------------------------------- #
class TestTheCourseCodeNote:
    def test_configured_exclusions_over_a_frame_without_the_column_are_recorded(self):
        ctx = _ctx()
        frame = pd.DataFrame({"title": ["a", "b", "c"]})
        note_unapplied_exclusions(ctx, "Classes", frame, configured=True)
        note_unapplied_exclusions(ctx, "Classes", frame.head(1), configured=True)
        assert ctx.outcome_notes_for("Classes") == ((_N.COURSE_CODE_EXCLUSIONS_NOT_APPLIED, 3),)

    def test_an_explicit_column_is_checked_as_given(self):
        ctx = _ctx()
        note_unapplied_exclusions(
            ctx, "StudentCourses", pd.DataFrame({"course code": ["X"]}), configured=True, column="code"
        )
        assert ctx.outcome_notes_for("StudentCourses") == ((_N.COURSE_CODE_EXCLUSIONS_NOT_APPLIED, 1),)

    @pytest.mark.parametrize(
        ("frame", "configured"),
        [
            (pd.DataFrame({"course code": ["X"]}), True),
            (pd.DataFrame({"district course code": ["X"]}), True),
            (pd.DataFrame({"title": ["a"]}), False),
            (pd.DataFrame(), True),
        ],
    )
    def test_twin_nothing_to_record(self, frame, configured):
        ctx = _ctx()
        note_unapplied_exclusions(ctx, "Classes", frame, configured=configured)
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# Classes display names and blended lookups (§5 #16/#31, #35, #37)              #
# --------------------------------------------------------------------------- #
class TestTheClassesNotes:
    def test_the_homeroom_teacher_name_absent_is_recorded(self):
        ctx = _ctx()
        assert ClassTransformer._homeroom_teacher_name_column(pd.DataFrame({"homeroom": ["A", "B"]}), ctx) is None
        assert ctx.outcome_notes_for("Classes") == ((_N.HOMEROOM_TEACHER_NAME_ABSENT, 2),)

    def test_twin_the_homeroom_teacher_name_present_records_nothing(self):
        ctx = _ctx()
        assert ClassTransformer._homeroom_teacher_name_column(pd.DataFrame({"teacher name": ["x"]}), ctx)
        assert ctx.outcome_notes == []

    def test_an_absent_class_name_column_is_recorded_per_subject_class(self, caplog):
        ctx = _ctx()
        merged = pd.DataFrame({"Class ID": ["C1", "C1", "C2"], "title": ["Math", "Math", "Art"]})
        with caplog.at_level(logging.WARNING):
            ClassTransformer._note_class_name_gaps(
                merged,
                {"section letter": "Section Letter"},
                ctx,
                course_title="course title",
                columns=("teacher name", "section letter"),
            )
        assert ctx.outcome_notes_for("Classes") == ((_N.CLASS_NAME_COLUMN_ABSENT, 2),)
        assert any("Section Letter" in m for m in _messages(caplog))

    def test_twin_every_name_column_present_records_nothing(self):
        ctx = _ctx()
        merged = pd.DataFrame({"Class ID": ["C1"], "title": ["M"], "teacher name": ["T"], "section letter": ["A"]})
        ClassTransformer._note_class_name_gaps(
            merged, {}, ctx, course_title="course title", columns=("teacher name", "section letter")
        )
        assert ctx.outcome_notes == []

    @staticmethod
    def _detect(schedule: pd.DataFrame) -> tuple[dict, TransformContext]:
        class_info = pd.DataFrame(
            {
                "school number": ["300", "300"],
                "teacher id": ["T010", "T010"],
                "master timetable id": ["MT100", "MT101"],
                "course code": ["ENG01", "ENG02"],
                "term": ["1", "1"],
                "semester": ["1", "1"],
                "day": ["1", "1"],
                "period": ["1", "1"],
            }
        )
        course = pd.DataFrame({"school number": ["300"] * 2, "course code": ["ENG01", "ENG02"], "title": ["E1", "E2"]})
        ctx = _ctx()
        ctx.set_school_year(2025, "08-25", "07-25")
        ctx.raw_data = {"StudentSchedule.txt": schedule, "CourseInformation.txt": course}
        mapping = {
            "source_files": {"student_schedule": "StudentSchedule.txt", "course_info": "CourseInformation.txt"},
            "field_map": {"Name": {"teacher last name": "Teacher Name"}},
        }
        result = BlendedClassDetector().detect(class_info, mapping, ctx)
        return result.metadata, ctx

    _SCHEDULE = {
        "student id": ["S100", "S101"],
        "school number": ["300", "300"],
        "grade": ["1", "2"],
        "master timetable id": ["MT100", "MT101"],
        "teacher id": ["T010", "T010"],
        "teacher name": [SENTINEL_PII, SENTINEL_PII],
    }

    def test_a_blend_named_without_a_teacher_name_column_warns_once_and_is_recorded(self, caplog):
        schedule = pd.DataFrame({k: v for k, v in self._SCHEDULE.items() if k != "teacher name"})
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.blended"):
            metadata, ctx = self._detect(schedule)
        assert len(metadata) == 1
        assert sum("omit the teacher's name" in m for m in _messages(caplog)) == 1
        assert ctx.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    def test_a_schedule_without_grades_records_the_lookup_gap(self):
        schedule = pd.DataFrame({k: v for k, v in self._SCHEDULE.items() if k != "grade"})
        metadata, ctx = self._detect(schedule)
        assert metadata == {}
        assert ctx.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    def test_twin_full_lookups_blend_named_with_the_teacher_and_record_nothing(self, caplog):
        with caplog.at_level(logging.WARNING):
            metadata, ctx = self._detect(pd.DataFrame(self._SCHEDULE))
        (meta,) = metadata.values()
        assert meta["Name"].startswith(SENTINEL_PII)  # the name itself is data, never logged
        assert ctx.outcome_notes == []
        _no_sentinel(caplog, ctx)


# --------------------------------------------------------------------------- #
# StudentAttendance and StudentCourses key reads (§5 #33, #34/#34a)              #
# --------------------------------------------------------------------------- #
_DAILY_CFG = {
    "daily_school_col": "School Number",
    "daily_student_col": "Student Number",
    "daily_date_col": "Absence Date",
    "daily_absent_code_col": "Absent Code",
    "daily_authorized_col": "Authorized",
    "daily_portion_col": "Portion Absent",
}


class TestTheKeyReadNotes:
    def test_an_attendance_band_missing_a_column_is_recorded(self, caplog):
        ctx = _ctx()
        daily = pd.DataFrame({"school number": ["1"], "student number": [SENTINEL_PII], "absence date": ["x"]})
        from src.etl.transformers.student_attendance import _DAILY_KEYS

        with caplog.at_level(logging.WARNING):
            StudentAttendanceTransformer._note_absent_columns(ctx, ((daily, _DAILY_CFG, _DAILY_KEYS),))
        assert ctx.outcome_notes_for("StudentAttendance") == ((_N.ATTENDANCE_SOURCE_COLUMN_ABSENT, 1),)
        assert any("absent code" in m for m in _messages(caplog))
        _no_sentinel(caplog, ctx)

    def test_twin_a_complete_band_or_an_empty_one_records_nothing(self):
        from src.etl.transformers.student_attendance import _DAILY_KEYS

        ctx = _ctx()
        complete = pd.DataFrame({column.lower(): ["v"] for column in _DAILY_CFG.values()})
        StudentAttendanceTransformer._note_absent_columns(
            ctx, ((complete, _DAILY_CFG, _DAILY_KEYS), (pd.DataFrame(), {}, ("period_school_col",)))
        )
        assert ctx.outcome_notes == []

    def test_a_transcript_source_missing_its_key_is_recorded(self):
        ctx = _ctx()
        cols = StudentCoursesTransformer._resolve_source_columns({})
        history = pd.DataFrame({"course code": ["MAT10"], "school number": ["1"]})
        StudentCoursesTransformer._note_absent_columns(
            ctx, {"history": history, "selection": pd.DataFrame(), "info": pd.DataFrame()}, cols
        )
        assert ctx.outcome_notes_for("StudentCourses") == ((_N.TRANSCRIPT_SOURCE_COLUMN_ABSENT, 1),)

    def test_twin_a_complete_transcript_source_records_nothing(self):
        ctx = _ctx()
        cols = StudentCoursesTransformer._resolve_source_columns({})
        selection = pd.DataFrame(
            {column: ["v"] for column in [cols[r] for r in StudentCoursesTransformer.SOURCE_READS["selection"]]}
            | {"school number": ["1"]}
        )
        StudentCoursesTransformer._note_absent_columns(
            ctx, {"history": pd.DataFrame(), "selection": selection, "info": pd.DataFrame()}, cols
        )
        assert ctx.outcome_notes == []


# --------------------------------------------------------------------------- #
# The record and the tiers                                                     #
# --------------------------------------------------------------------------- #
class TestTheRecordAndTheTiers:
    def test_every_note_survives_the_record_round_trip(self):
        outcomes = tuple(EntityOutcome.built(f"E{i}", 3, notes=((note, i + 1),)) for i, note in enumerate(OutcomeNote))
        assert outcomes_from_record({"entity_outcomes": outcomes_to_record(outcomes)}) == outcomes

    def test_a_legacy_record_reads_no_notes(self):
        (outcome,) = outcomes_from_record(
            {"entity_outcomes": {"Students": {"kind": "built", "reason": "none", "rows": 4}}}
        )
        assert outcome.notes == ()

    def test_only_the_all_active_default_and_the_coteacher_note_warn(self):
        warning = {note for note, tier in NOTE_TIER.items() if tier is Verdict.WARNING}
        assert warning == {OutcomeNote.ALL_ACTIVE_DEFAULT, OutcomeNote.COTEACHER_SOURCE_UNUSABLE}

    def test_the_all_active_default_makes_the_run_partial_with_its_own_headline(self):
        students = EntityOutcome.built("Students", 5, notes=((OutcomeNote.ALL_ACTIVE_DEFAULT, 5),))
        assert warning_outcomes([students]) == (students,)
        headline, _detail = partial_copy([students], delivered=True)
        assert headline == "Your roster synced without an enrollment status check"
        assert partial_label([students]) == "every student sent as active"

    def test_twin_a_healthy_note_is_row_detail_only(self):
        family = EntityOutcome.built("Family", 5, notes=((OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL, 2),))
        assert outcome_tier(family) is Verdict.HEALTHY
        assert warning_outcomes([family]) == ()
        assert detail_note_labels([family]) == ("contacts without email left out",)

    def test_the_run_history_row_shows_healthy_notes_under_an_unchanged_label(self):
        outcomes = (
            EntityOutcome.built("Students", 5, notes=((OutcomeNote.STATUS_COLUMN_ABSENT_DATE_ONLY, 5),)),
            EntityOutcome.built("Family", 2, notes=((OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL, 1),)),
        )
        record = {
            "timestamp": "2026-09-25T02:00:00",
            "status": "success",
            "error_category": "none",
            "entity_outcomes": outcomes_to_record(outcomes),
        }
        row = to_run_row(record, prior_build=None)
        assert row.status_label == "Completed"
        assert row.notes == ("no status column, withdraw dates used", "contacts without email left out")

    def test_twin_a_failed_row_shows_no_note_detail(self):
        outcomes = (EntityOutcome.built("Students", 5, notes=((OutcomeNote.STATUS_COLUMN_ABSENT_DATE_ONLY, 5),)),)
        record = {"status": "failed", "error_category": "data", "entity_outcomes": outcomes_to_record(outcomes)}
        assert to_run_row(record, prior_build=None).notes == ()

    def test_a_warning_note_is_the_row_suffix_never_the_detail(self):
        outcomes = (EntityOutcome.built("Students", 5, notes=((OutcomeNote.ALL_ACTIVE_DEFAULT, 5),)),)
        record = {"status": "success", "error_category": "none", "entity_outcomes": outcomes_to_record(outcomes)}
        row = to_run_row(record, prior_build=None)
        assert row.status_label.endswith("every student sent as active")
        assert row.notes == ()

    def test_an_empty_outcome_carries_its_note(self):
        """Every contact excluded ⇒ Family EMPTY — and the count still rides on it."""
        family = EntityOutcome.empty(
            "Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM, notes=((OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL, 9),)
        )
        assert outcomes_from_record({"entity_outcomes": outcomes_to_record((family,))}) == (family,)


# --------------------------------------------------------------------------- #
# Review round (S11): the pins the first pass left open                         #
# --------------------------------------------------------------------------- #
class TestAnEmptyFrameRecordsNothingAndNeverRaises:
    """A header-only source reaches every site (the pipeline skips an entity only when EVERY
    source is empty): a note counts at least one row, so each site must return the frame and
    record nothing — never raise (a raise on CRITICAL Staff would fail the whole run)."""

    def test_staff_with_the_status_column_but_no_rows(self, caplog):
        ctx = _ctx()
        with caplog.at_level(logging.WARNING):
            kept = StaffTransformer.filter_departed_staff(
                pd.DataFrame({"teacher id": [], "staff status": []}), {}, context=ctx
            )
        assert kept.empty
        assert ctx.outcome_notes == []
        assert _messages(caplog) == []

    def test_staff_without_the_status_column_and_no_rows(self):
        ctx = _ctx()
        assert StaffTransformer.filter_departed_staff(pd.DataFrame({"teacher id": []}), {}, context=ctx).empty
        assert ctx.outcome_notes == []

    @pytest.mark.parametrize("roster", [set(), {"S1"}], ids=["no-roster", "no-student-column"])
    def test_filter_to_active_both_postures(self, roster):
        ctx = _ctx()
        ctx.active_student_ids = roster
        assert BaseTransformer.filter_to_active(
            pd.DataFrame({"other": []}), "student number", ctx, caller="Family"
        ).empty
        assert ctx.outcome_notes == []

    def test_family_and_students_email_output_not_mapped(self):
        ctx = _ctx()
        assert FamilyTransformer._exclude_rows_without_email(pd.DataFrame({"First Name": []}), ctx).empty
        StudentTransformer._warn_rows_without_email(pd.DataFrame({"First Name": []}), ctx)
        assert ctx.outcome_notes == []

    def test_roster_merge_skipped_over_an_empty_working_frame(self):
        ctx = _ctx()
        staff = pd.DataFrame({"teacher id": ["T1"]})
        ctx.raw_data = {"Staff.txt": staff, "Roster.txt": pd.DataFrame({"teacher id": ["T1"], "other": ["x"]})}
        mapping = {"source_files": {"staff_info": "Staff.txt", "roster": "Roster.txt"}}
        merged = StaffTransformer()._merge_roster(staff.iloc[0:0], mapping, ctx)
        assert merged.empty
        assert ctx.outcome_notes == []

    def test_positive_twin_the_same_site_records_over_one_row(self):
        ctx = _ctx()
        StaffTransformer.filter_departed_staff(
            pd.DataFrame({"teacher id": ["T1"], "staff status": ["Inactive"]}), {}, context=ctx
        )
        assert ctx.outcome_notes_for("Staff") == ((_N.STAFF_FILTER_WOULD_EMPTY, 1),)


class TestTheStaffVocabularyLineNamesOnlyCodes:
    def test_a_name_in_the_status_column_is_counted_never_shown(self, caplog):
        ctx = _ctx()
        frame = pd.DataFrame(
            {"teacher id": ["T1", "T2", "T3"], "staff status": [SENTINEL_PII, "Active", SENTINEL_EMAIL]}
        )
        with caplog.at_level(logging.WARNING):
            kept = StaffTransformer.filter_departed_staff(frame, {}, context=ctx)
        assert len(kept) == 3
        assert ctx.outcome_notes_for("Staff") == ((_N.STAFF_STATUS_VOCABULARY_UNRECOGNISED, 3),)
        (line,) = _messages(caplog)
        assert "2 unrecognised value(s)" in line
        _no_sentinel(caplog, ctx)

    def test_twin_short_upper_case_codes_are_still_named(self, caplog):
        ctx = _ctx()
        with caplog.at_level(logging.WARNING):
            StaffTransformer.filter_departed_staff(_staff(["A", "I"]), {}, context=ctx)
        (line,) = _messages(caplog)
        assert "['A', 'I']" in line

    def test_one_name_among_codes_hides_them_all(self, caplog):
        ctx = _ctx()
        frame = pd.DataFrame({"teacher id": ["T1", "T2"], "staff status": ["A", "Wong"]})
        with caplog.at_level(logging.WARNING):
            StaffTransformer.filter_departed_staff(frame, {}, context=ctx)
        (line,) = _messages(caplog)
        assert "Wong" not in line and "'A'" not in line
        assert "2 unrecognised value(s)" in line


class TestSafetyRelevantParametersHaveNoDefault:
    @pytest.mark.parametrize(
        ("function", "name"),
        [
            (StaffTransformer.filter_departed_staff, "context"),
            (BaseTransformer.filter_to_active, "caller"),
        ],
    )
    def test_required_keyword_only(self, function, name):
        parameter = inspect.signature(function).parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


class TestAWhitespaceOnlyEnrollStatusKeyKeepsItsMeaning:
    """Review S11-A1: the resolver reads a blank as "use the default", but the pre-S11 reader
    tested the RAW value — so a whitespace-only value keeps reading what it read before."""

    _FRAME = {"enrollment status": ["Inactive", "Active"], "withdraw date": ["", ""]}

    def test_a_blank_status_column_is_configured_and_absent_never_the_aliases(self):
        field_map = {"EnrollStatus": {"status_column": "  "}}
        status, withdraw, _values = BaseTransformer.resolve_active_config(field_map, list(self._FRAME))
        assert (status, withdraw) == (None, "withdraw date")
        decision = BaseTransformer.decide_enroll_status(pd.DataFrame(self._FRAME), field_map)
        assert list(decision.labels) == ["Active", "Active"]  # the date branch, exactly as before
        assert decision.notes[0] == (_N.CONFIGURED_STATUS_COLUMN_ABSENT, 2)

    def test_a_blank_withdraw_date_column_reads_no_column(self):
        field_map = {"EnrollStatus": {"withdraw_date_column": "  "}}
        status, withdraw, _values = BaseTransformer.resolve_active_config(field_map, list(self._FRAME))
        assert (status, withdraw) == ("enrollment status", "")

    def test_twin_an_empty_string_is_unconfigured_as_before(self):
        field_map = {"EnrollStatus": {"status_column": "", "withdraw_date_column": ""}}
        status, withdraw, _values = BaseTransformer.resolve_active_config(field_map, list(self._FRAME))
        assert (status, withdraw) == ("enrollment status", "withdraw date")


class TestTheNoteBranchesEachFireOrStayQuiet:
    """Review PT-5: every branch whose docstring makes a claim, fired and not fired."""

    def test_a_present_configured_status_column_records_no_absence(self):
        field_map = {"EnrollStatus": {"status_column": "  Pupil State "}}
        decision = BaseTransformer.decide_enroll_status(_demo(**{"pupil state": ["Active"] * 3}), field_map)
        assert decision.notes == ()

    @staticmethod
    def _identity(spec: object, frame: pd.DataFrame, result: pd.DataFrame | None = None) -> TransformContext:
        ctx = _ctx()
        FamilyTransformer().apply_field_map(
            frame, pd.DataFrame() if result is None else result, {"Student User ID": spec}, "Family", ctx
        )
        return ctx

    @pytest.mark.parametrize(
        "spec",
        [
            {"column": "Pupil No", "transform": "grade_to_ceds"},
            {"column": "Pupil No", "append_year_to_id": False},
        ],
        ids=["column-transform", "append-year-off"],
    )
    def test_an_identity_field_read_through_a_column_entry_is_recorded(self, spec):
        ctx = self._identity(spec, pd.DataFrame({"first name": ["a", "b"]}))
        assert ctx.outcome_notes_for("Family") == ((_N.IDENTITY_FIELD_BLANKED, 2),)

    def test_twin_append_year_on_fails_closed_and_is_not_this_note(self):
        ctx = _ctx()
        ctx.set_school_year(2025, "08-25", "07-25")
        spec = {"column": "Pupil No", "append_year_to_id": True}
        with pytest.raises(SourceSchemaError):
            FamilyTransformer().apply_field_map(
                pd.DataFrame({"first name": ["a"]}), pd.DataFrame(), {"Student User ID": spec}, "Family", ctx
            )
        assert ctx.outcome_notes == []

    def test_twin_an_empty_frame_is_not_an_identity_blank(self):
        assert self._identity("Pupil No", pd.DataFrame({"first name": []})).outcome_notes == []

    def test_twin_a_field_the_caller_filled_first_is_not_blanked_by_the_engine(self):
        frame = pd.DataFrame({"first name": ["a", "b"]})
        ctx = self._identity("Pupil No", frame, result=pd.DataFrame({"Student User ID": ["1", "2"]}))
        assert ctx.outcome_notes == []

    @staticmethod
    def _detect(class_info: pd.DataFrame, schedule: pd.DataFrame, course: pd.DataFrame) -> TransformContext:
        ctx = _ctx()
        ctx.set_school_year(2025, "08-25", "07-25")
        ctx.raw_data = {"StudentSchedule.txt": schedule, "CourseInformation.txt": course}
        mapping = {
            "source_files": {"student_schedule": "StudentSchedule.txt", "course_info": "CourseInformation.txt"},
            "field_map": {"Name": {"teacher last name": "Teacher Name"}},
        }
        BlendedClassDetector().detect(class_info, mapping, ctx)
        return ctx

    _CLASS_INFO = {
        "school number": ["300", "300"],
        "teacher id": ["T010", "T010"],
        "master timetable id": ["MT100", "MT101"],
        "course code": ["ENG01", "ENG02"],
        "term": ["1", "1"],
        "semester": ["1", "1"],
        "day": ["1", "1"],
        "period": ["1", "1"],
    }
    _SCHEDULE = {
        "student id": ["S100", "S101"],
        "school number": ["300", "300"],
        "grade": ["1", "2"],
        "master timetable id": ["MT100", "MT101"],
        "teacher id": ["T010", "T010"],
        "teacher name": ["N", "N"],
    }
    _COURSE = {"school number": ["300"] * 2, "course code": ["ENG01", "ENG02"], "title": ["E1", "E2"]}

    @staticmethod
    def _without(table: dict, *columns: str) -> pd.DataFrame:
        return pd.DataFrame({k: v for k, v in table.items() if k not in columns})

    def test_twin_full_lookups_record_nothing(self):
        ctx = self._detect(pd.DataFrame(self._CLASS_INFO), pd.DataFrame(self._SCHEDULE), pd.DataFrame(self._COURSE))
        assert ctx.outcome_notes == []

    def test_no_sections_to_group_over_records_the_gap(self):
        ctx = self._detect(
            self._without(self._CLASS_INFO, "master timetable id"),
            self._without(self._SCHEDULE, "master timetable id"),
            pd.DataFrame(self._COURSE),
        )
        assert ctx.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    def test_course_info_without_titles_records_the_gap(self):
        ctx = self._detect(
            pd.DataFrame(self._CLASS_INFO), pd.DataFrame(self._SCHEDULE), self._without(self._COURSE, "title")
        )
        assert ctx.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    def test_a_class_info_without_course_codes_records_the_gap(self):
        ctx = self._detect(
            self._without(self._CLASS_INFO, "course code"), pd.DataFrame(self._SCHEDULE), pd.DataFrame(self._COURSE)
        )
        assert ctx.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    def test_teacherless_sections_record_only_a_real_gap(self):
        teacherless = {**self._CLASS_INFO, "teacher id": ["", ""]}
        quiet = self._detect(pd.DataFrame(teacherless), pd.DataFrame(self._SCHEDULE), pd.DataFrame(self._COURSE))
        assert quiet.outcome_notes == []
        noted = self._detect(
            pd.DataFrame(teacherless), pd.DataFrame(self._SCHEDULE), self._without(self._COURSE, "title")
        )
        assert noted.outcome_notes_for("Classes") == ((_N.BLENDED_LOOKUP_COLUMN_ABSENT, 2),)

    @pytest.mark.parametrize("roster", [None, pd.DataFrame(columns=["teacher id", "other"])], ids=["absent", "empty"])
    def test_an_absent_or_empty_roster_file_is_not_a_skipped_merge(self, roster):
        ctx = _ctx()
        staff = _staff(["Active", "Active"])
        ctx.raw_data = {"Staff.txt": staff} if roster is None else {"Staff.txt": staff, "Roster.txt": roster}
        mapping = {"source_files": {"staff_info": "Staff.txt", "roster": "Roster.txt"}}
        StaffTransformer()._merge_roster(staff, mapping, ctx)
        assert ctx.outcome_notes == []
