"""Fail-closed conformance for guard classes (a)/(b) — plan 0053 S10 (``failure-policy.md`` §5).

``columns.require_columns`` is the ONE presence check a fail-closed guard makes before it
reads a column that decides WHO may be delivered (``PII_SCOPE``) or LINKS rows
(``JOIN_KEY``). This module pins:

* the helper itself — every missing column named at once, in config spelling and order;
  case/whitespace-insensitive on both sides; entity + guard carried; the source's column
  COUNT and a ``header_looks_like_data`` flag in the message, never an observed header;
* every §5 site S10 made fail CLOSED, each with the twin that proves the site still
  BUILDS when the column is present (and, where the rule is "only when the file is
  present", the twin that an absent file still contributes nothing without raising);
* §5 #35 — a site S10 made fail OPEN: a missing homeroom teacher NAME blanks that
  name segment with ONE aggregated WARNING and never fails the run (Gate A, answer 3);
* §5 #15 — the co-teacher columns, OPTIONAL by owner ruling 2026-09-25: left out with ONE
  aggregated WARNING and an outcome note, never a failure; ClassInformation's school stays
  fail-closed;
* the enrollments partial ship is gone — an AST pin that no handler under
  ``src/etl/transformers`` catches ``KeyError`` or pandas ``MergeError`` (the shape the
  deleted ``except (KeyError, pd.errors.MergeError)`` had), with a doctored twin;
* end to end: a CRITICAL entity's guard fails the run with ``source_schema`` and the
  entity named, the previous outputs untouched.

The §5 ``require-columns`` table ↔ call-site parity lives in
``tests/test_failure_policy_parity.py`` with the other doc↔code pins. All data is synthetic.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.models import FieldAppendYear
from src.etl.errors import GuardKind, RunErrorCategory, SourceSchemaError, classify_error_category
from src.etl.outcomes import OutcomeKind, OutcomeNote, OutcomeReason
from src.etl.pipeline import run_pipeline
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.columns import GUARD_CONSEQUENCE, header_looks_like_data, require_columns
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.enrollments import EnrollmentTransformer
from src.etl.transformers.staff import StaffTransformer
from src.etl.transformers.students import StudentTransformer
from src.history.store import read_run_records
from tests.test_contract import _create_myedbc_inputs

_REPO = Path(__file__).resolve().parents[1]
_TRANSFORMERS_DIR = _REPO / "src" / "etl" / "transformers"

#: Stands in for a pupil's name read as a header (a headerless file without its `headers:`).
_SENTINEL_HEADER = "zzsentinelpupil"


# --------------------------------------------------------------------------- #
# The helper                                                                    #
# --------------------------------------------------------------------------- #
class TestRequireColumns:
    def test_every_missing_column_is_named_at_once_in_config_spelling_and_order(self) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            require_columns(
                ["school number", "grade"],
                ["Lives With", "Grade", "Custody Flag"],
                entity="Family",
                guard=GuardKind.PII_SCOPE,
            )
        err = exc.value
        assert err.columns == ("Lives With", "Custody Flag"), "every one, config spelling, config order"
        assert (err.entity, err.guard) == ("Family", GuardKind.PII_SCOPE)
        assert err.category is RunErrorCategory.SOURCE_SCHEMA
        assert classify_error_category(err) is RunErrorCategory.SOURCE_SCHEMA
        assert isinstance(err, ValueError), "every existing `except ValueError` keeps working"
        assert "'Lives With'" in str(err) and "'Custody Flag'" in str(err)

    def test_the_twin_all_present_returns_none(self) -> None:
        assert require_columns(["a", "b"], ["A", "b"], entity="Classes", guard=GuardKind.JOIN_KEY) is None

    def test_case_and_surrounding_whitespace_never_decide_presence_on_either_side(self) -> None:
        require_columns(
            ["  School Number ", "GRADE"], [" school number", "Grade  "], entity="X", guard=GuardKind.JOIN_KEY
        )

    def test_a_repeated_column_is_named_once(self) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            require_columns(["x"], ["Home School", "Home School"], entity="Students", guard=GuardKind.JOIN_KEY)
        assert exc.value.columns == ("Home School",)

    def test_nothing_required_is_nothing_missing(self) -> None:
        assert require_columns([], [], entity="Students", guard=GuardKind.JOIN_KEY) is None

    def test_the_message_carries_the_count_and_the_flag_never_an_observed_header(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG), pytest.raises(SourceSchemaError) as exc:
            require_columns(
                [_SENTINEL_HEADER, "school number", "grade"], ["Homeroom"], entity="Classes", guard=GuardKind.JOIN_KEY
            )
        message = str(exc.value)
        assert "the source has 3 columns" in message
        assert "header_looks_like_data=no" in message
        assert GUARD_CONSEQUENCE[GuardKind.JOIN_KEY] in message
        assert _SENTINEL_HEADER not in message.lower() and _SENTINEL_HEADER not in caplog.text.lower()
        assert "school number" not in message, "not even a harmless observed header is echoed"

    def test_a_headerless_file_read_as_data_says_so(self) -> None:
        """Row 1 of a headerless export (numbers, a date, an email) became the header."""
        pupil_row = ["2025/2026", "100", "S001", "Alice", "2010-01-15", "pupil@example.org", "3"]
        with pytest.raises(SourceSchemaError) as exc:
            require_columns(pupil_row, ["Grade"], entity="Students", guard=GuardKind.PII_SCOPE)
        message = str(exc.value)
        assert "header_looks_like_data=yes" in message and "`headers:` block" in message
        assert "Alice" not in message and "S001" not in message, "the flag, never the values"

    def test_non_string_labels_are_read_not_refused(self) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            require_columns([0, 1, 2], ["Grade"], entity="Students", guard=GuardKind.PII_SCOPE)
        assert "header_looks_like_data=yes" in str(exc.value)

    def test_the_consequence_copy_is_total_over_guard_kind(self) -> None:
        assert set(GUARD_CONSEQUENCE) == set(GuardKind)
        assert all(text.strip() for text in GUARD_CONSEQUENCE.values())


class TestHeaderLooksLikeData:
    @pytest.mark.parametrize(
        "names",
        [
            ["2025/2026", "100", "S001", "Alice"],
            ["12:30", "1.0", "Unnamed: 2", "Grade"],
            ["someone@example.org", "x", "y", "z"],
        ],
    )
    def test_value_like_names_flag(self, names) -> None:
        assert header_looks_like_data(names) is True

    @pytest.mark.parametrize(
        "names",
        [
            ["School Number", "Student Number", "Grade", "Homeroom"],
            [],
            # one blank trailing column in a 20-column export stays well under a quarter
            [f"Column {chr(65 + i)}" for i in range(19)] + ["Unnamed: 19"],
        ],
    )
    def test_real_headers_do_not(self, names) -> None:
        assert header_looks_like_data(names) is False


# --------------------------------------------------------------------------- #
# Per-site fixtures                                                             #
# --------------------------------------------------------------------------- #
#: What a real run builds before each entity (Classes needs the roster; Enrollments the artifacts).
_PREREQUISITES = {"Students": (), "Staff": (), "Classes": ("Students",), "Enrollments": ("Students", "Classes")}


@pytest.fixture
def run_entity(published_transformer, base_mapping, global_config):
    """Run ONE entity the way a run does: its prerequisites first, on the same context.

    ``prior_raw`` (default: ``raw``) feeds the prerequisites, so a test can hand the entity
    under test a file its prerequisites would themselves refuse (Classes reads the
    schedule's school, grade and Class ID columns too).
    """

    def _run(
        entity: str,
        raw: dict,
        *,
        mappings: dict | None = None,
        gc: dict | None = None,
        prior_raw: dict | None = None,
    ) -> pd.DataFrame:
        maps = mappings or base_mapping["mappings"]
        config = gc or global_config
        published_transformer.set_entity_mappings(maps)
        published_transformer.set_school_year(2025, "08-25", "07-25")
        before_raw = prior_raw or raw
        for before in _PREREQUISITES[entity]:
            published_transformer.transform(before_raw[_primary(before)], maps[before], before, before_raw, config)
        return published_transformer.transform(raw[_primary(entity)], maps[entity], entity, raw, config)

    return _run


def _primary(entity: str) -> str:
    return {
        "Students": "StudentDemographicInformation.txt",
        "Staff": "StaffInformationEnhanced.txt",
        "Classes": "StudentSchedule.txt",
        "Enrollments": "StudentSchedule.txt",
    }[entity]


def _drop(raw: dict, filename: str, *columns: str) -> dict:
    out = dict(raw)
    out[filename] = raw[filename].drop(columns=list(columns))
    return out


def _assert_schema_error(exc: pytest.ExceptionInfo, *, entity: str, guard: GuardKind, columns: tuple[str, ...]) -> None:
    """EXACT comparison, never case-folded: ``columns`` must be the CONFIG's spelling, because
    ``outcomes.safe_label`` matches config spelling only — a lower-cased label is silently
    dropped from Home / Run History (D4). Structural joins name their ``column_names``
    constant, which is lower-case by definition."""
    err = exc.value
    assert (err.entity, err.guard, err.columns) == (entity, guard, columns)


# --------------------------------------------------------------------------- #
# Classes                                                                       #
# --------------------------------------------------------------------------- #
class TestClassesSites:
    def test_twin_the_standard_frames_build(self, run_entity, raw_data) -> None:
        classes = run_entity("Classes", raw_data)
        assert not classes.empty and classes["Class ID"].notna().all()

    def test_8_course_info_without_school_number_fails_closed(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "CourseInformation.txt", "school number"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("school number",))

    def test_8_course_info_without_title_fails_closed_naming_both_missing(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "CourseInformation.txt", "title", "course code"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("course code", "title"))

    def test_8_a_schedule_without_any_course_code_fails_closed(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentSchedule.txt", "district course code"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("course code",))

    def test_8_twin_no_course_file_skips_the_join(self, run_entity, raw_data) -> None:
        raw = {**raw_data, "CourseInformation.txt": pd.DataFrame()}
        assert not run_entity("Classes", raw).empty

    def test_9_staff_file_without_last_name_fails_closed(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StaffInformationEnhanced.txt", "last name"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("last name",))

    def test_9_a_schedule_without_teacher_id_fails_the_staff_join_closed(self, run_entity, raw_data) -> None:
        """The staff file carries the teacher id; the SCHEDULE side of the join lacks it."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentSchedule.txt", "teacher id"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("Teacher ID",))

    def test_29_one_error_names_class_id_and_school_id_at_once(self, run_entity, raw_data, base_mapping) -> None:
        """The two subject-ID columns are one check (they used to be two failed nights)."""
        mappings = copy.deepcopy(base_mapping["mappings"])
        mappings["Classes"]["field_map"]["School ID"] = "School Code"
        with pytest.raises(SourceSchemaError) as exc:
            run_entity(
                "Classes",
                _drop(raw_data, "StudentSchedule.txt", "master timetable id"),
                mappings=mappings,
                prior_raw=raw_data,
            )
        _assert_schema_error(
            exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("Master Timetable ID", "School Code")
        )

    def test_9_twin_no_staff_file_skips_the_join(self, run_entity, raw_data) -> None:
        raw = {**raw_data, "StaffInformationEnhanced.txt": pd.DataFrame()}
        assert not run_entity("Classes", raw).empty

    def test_5_a_schedule_without_its_grade_fails_closed_as_pii_scope(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentSchedule.txt", "grade"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.PII_SCOPE, columns=("Grade",))

    def test_29_a_schedule_without_its_class_id_column_fails_closed(self, run_entity, raw_data) -> None:
        """Every subject Class ID used to ship BLANK."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentSchedule.txt", "master timetable id"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("Master Timetable ID",))

    def test_29_a_school_id_column_the_schedule_lacks_fails_closed(self, run_entity, raw_data, base_mapping) -> None:
        """Every subject School ID used to ship BLANK (`merged.get(school_col, "")`)."""
        mappings = copy.deepcopy(base_mapping["mappings"])
        mappings["Classes"]["field_map"]["School ID"] = "School Code"
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", raw_data, mappings=mappings)
        assert exc.value.columns == ("School Code",), "named as the district configured it"

    def test_5_a_demographic_without_its_grade_fails_closed(self, run_entity, raw_data) -> None:
        """The homeroom split's grade — Students reads it only as an intended-blank output."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentDemographicInformation.txt", "grade"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.PII_SCOPE, columns=("Grade",))

    def test_30_a_demographic_without_homeroom_fails_typed_not_keyerror(self, run_entity, raw_data) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Classes", _drop(raw_data, "StudentDemographicInformation.txt", "homeroom"))
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("Homeroom",))

    def test_30_twin_no_homeroom_grade_student_reads_no_homeroom_column(
        self, run_entity, raw_data, global_config
    ) -> None:
        """The guard sits WHERE the column is read: a district with no homeroom-grade
        pupil never reads it, so its absence is not a fault."""
        raw = _drop(raw_data, "StudentDemographicInformation.txt", "homeroom")
        classes = run_entity("Classes", raw, gc={**global_config, "homeroom_grades": []})
        assert not classes.empty and not classes["Class ID"].astype(str).str.contains("_A1_").any()


class TestHomeroomTeacherNameFailsOpen:
    """§5 #35 — class (d): an OPTIONAL display value is blanked, warned once, never fatal."""

    def test_a_missing_teacher_name_blanks_the_segment_with_one_warning(self, run_entity, raw_data, caplog) -> None:
        raw = _drop(raw_data, "StudentDemographicInformation.txt", "teacher name")
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.classes"):
            classes = run_entity("Classes", raw)
        homerooms = classes[classes["Class ID"].astype(str).str.contains("_A1_|_B2_")]
        assert not homerooms.empty
        assert all(" - " not in name for name in homerooms["Name"]), "no teacher segment"
        warnings = [r for r in caplog.records if "homeroom class name(s) omit" in r.getMessage()]
        assert len(warnings) == 1, "ONE aggregated line, not one per homeroom"
        assert "Harper" not in caplog.text

    def test_twin_present_names_the_teacher_and_warns_nothing(self, run_entity, raw_data, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.classes"):
            classes = run_entity("Classes", raw_data)
        assert any(" - Ms. Harper" in name for name in classes["Name"])
        assert not [r for r in caplog.records if "homeroom class name(s) omit" in r.getMessage()]


# --------------------------------------------------------------------------- #
# Enrollments                                                                   #
# --------------------------------------------------------------------------- #
class TestEnrollmentsSites:
    def test_twin_the_standard_frames_build_student_and_teacher_rows(self, run_entity, raw_data) -> None:
        enrollments = run_entity("Enrollments", raw_data)
        assert {"student", "teacher"} <= set(enrollments["Role"])

    def test_28_a_schedule_without_its_student_id_fails_closed(self, run_entity, raw_data) -> None:
        """It used to ship every timetable class with teachers only — unenrolling its students."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Enrollments", _drop(raw_data, "StudentSchedule.txt", "student id"))
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Student ID",))

    @pytest.mark.parametrize(
        ("column", "guard", "label"),
        [
            ("school number", GuardKind.JOIN_KEY, "school number"),  # §5 #36 — a raw KeyError before S10
            ("teacher id", GuardKind.JOIN_KEY, "Teacher ID"),  # §5 #28 — non-blended teacher rows left out
            ("grade", GuardKind.PII_SCOPE, "Grade"),  # §5 #5
            ("master timetable id", GuardKind.JOIN_KEY, "Master Timetable ID"),  # §5 #29 — blank Class IDs
        ],
    )
    def test_a_schedule_column_classes_also_reads_fails_enrollments_closed(
        self, run_entity, raw_data, column, guard, label
    ) -> None:
        """Classes (which reads these too) is fed the intact file, so the raise is Enrollments' own.
        ``label`` is the config's spelling (a structural join names its lower-case constant)."""
        raw = _drop(raw_data, "StudentSchedule.txt", column)
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Enrollments", raw, prior_raw=raw_data)
        _assert_schema_error(exc, entity="Enrollments", guard=guard, columns=(label,))

    def test_one_error_names_every_schedule_linking_column_at_once(self, run_entity, raw_data) -> None:
        """The student id and the Class ID column used to be checked in two calls (one
        failed night each); they are one call now."""
        raw = _drop(raw_data, "StudentSchedule.txt", "student id", "master timetable id")
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Enrollments", raw, prior_raw=raw_data)
        _assert_schema_error(
            exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Student ID", "Master Timetable ID")
        )

    @pytest.mark.parametrize(("column", "label"), [("student number", "Student Number"), ("teacher id", "Teacher ID")])
    def test_10_28_homeroom_rows_need_every_merge_column_no_partial_frame(
        self, run_entity, raw_data, column, label
    ) -> None:
        """The deleted `except (KeyError, MergeError)` shipped whatever homeroom rows had been
        built before the error (and a demographic without its teacher id shipped every
        homeroom with no teacher — SD83, 2026-09-14). Each linking column is now required,
        named, and no homeroom row is returned at all."""
        raw = _drop(raw_data, "StudentDemographicInformation.txt", column)
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Enrollments", raw, prior_raw=raw_data)
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=(label,))

    def test_10_the_homeroom_lookup_without_the_teacher_id_fails_closed(
        self, raw_data, base_mapping, global_config
    ) -> None:
        """A mapping pointing Classes and Enrollments at different demographic files can hand
        Enrollments a homeroom lookup without the teacher id; its teacher rows must not vanish."""
        ctx = TransformContext(school_year=2025, entity_mappings=base_mapping["mappings"])
        lookup = pd.DataFrame({"school number": ["100"], "homeroom": ["A1"], "Class ID": ["100_A1_2025"]})
        artifacts = ClassArtifacts(
            homeroom_classes_df=lookup,
            class_info_df=pd.DataFrame(),
            blended_class_map={},
            blended_class_metadata={},
            blended_teacher_map={},
        )
        demographic = raw_data["StudentDemographicInformation.txt"]
        assert "teacher id" in demographic.columns, "the demographic side carries it"
        with pytest.raises(SourceSchemaError) as exc:
            EnrollmentTransformer()._homeroom_enrollments(
                demographic, global_config["homeroom_grades"], "teacher id", "Teacher ID", artifacts, ctx
            )
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Teacher ID",))

    def test_twin_homeroom_rows_include_the_homeroom_teacher(self, run_entity, raw_data) -> None:
        enrollments = run_entity("Enrollments", raw_data)
        homeroom = enrollments[enrollments["Class ID"].astype(str).str.contains("_A1_")]
        assert set(homeroom["Role"]) == {"student", "teacher"}


# --------------------------------------------------------------------------- #
# Config spelling survives for a RENAMED column (outcomes.safe_label, D4)       #
# --------------------------------------------------------------------------- #
_ENROLLMENT_FILES = ("StudentSchedule.txt", "StudentDemographicInformation.txt", "ClassInformationEnh.txt")


def _rename(raw: dict, old: str, new: str, *files: str) -> dict:
    out = dict(raw)
    for filename in files:
        out[filename] = raw[filename].rename(columns={old: new})
    return out


def _set_id_role(mappings: dict, key: str, value: str) -> None:
    for field in ("User ID", "Role"):
        mappings["Enrollments"]["field_map"][field][key] = value


class TestRenamedColumnsKeepTheirConfigSpelling:
    """A district that renames a column gets it named EXACTLY as its mapping spells it — a
    case-folded label would be dropped by ``outcomes.safe_label`` (exact-string membership)
    and Home / Run History would stop naming the column. Each case renames the source
    column in the data AND the mapping, then removes it from the one file under test."""

    def test_28_the_schedule_student_id(self, run_entity, raw_data, base_mapping) -> None:
        mappings = copy.deepcopy(base_mapping["mappings"])
        _set_id_role(mappings, "student_id_col", "Pupil Ref")
        renamed = _rename(raw_data, "student id", "pupil ref", "StudentSchedule.txt")
        with pytest.raises(SourceSchemaError) as exc:
            run_entity(
                "Enrollments",
                _drop(renamed, "StudentSchedule.txt", "pupil ref"),
                mappings=mappings,
                prior_raw=renamed,
            )
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Pupil Ref",))

    def test_10_28_the_homeroom_teacher_id(self, run_entity, raw_data, base_mapping) -> None:
        mappings = copy.deepcopy(base_mapping["mappings"])
        _set_id_role(mappings, "staff_id_col", "Staff Code")
        renamed = _rename(raw_data, "teacher id", "staff code", *_ENROLLMENT_FILES, "StaffInformationEnhanced.txt")
        with pytest.raises(SourceSchemaError) as exc:
            run_entity(
                "Enrollments",
                _drop(renamed, "StudentDemographicInformation.txt", "staff code"),
                mappings=mappings,
                prior_raw=renamed,
            )
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Staff Code",))

    def test_10_28_the_homeroom_student_number(self, run_entity, raw_data, base_mapping) -> None:
        mappings = copy.deepcopy(base_mapping["mappings"])
        mappings["Students"]["field_map"]["User ID"] = "Pupil No"
        renamed = _rename(raw_data, "student number", "pupil no", "StudentDemographicInformation.txt")
        with pytest.raises(SourceSchemaError) as exc:
            run_entity(
                "Enrollments",
                _drop(renamed, "StudentDemographicInformation.txt", "pupil no"),
                mappings=mappings,
                prior_raw=renamed,
            )
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("Pupil No",))

    @pytest.mark.parametrize("entity", ["Classes", "Enrollments"])
    def test_5_the_schedule_grade(self, run_entity, raw_data, base_mapping, entity) -> None:
        mappings = copy.deepcopy(base_mapping["mappings"])
        mappings["Classes"]["field_map"]["Grade"] = "Grade Level"
        renamed = _rename(raw_data, "grade", "grade level", "StudentSchedule.txt")
        with pytest.raises(SourceSchemaError) as exc:
            run_entity(
                entity, _drop(renamed, "StudentSchedule.txt", "grade level"), mappings=mappings, prior_raw=renamed
            )
        _assert_schema_error(exc, entity=entity, guard=GuardKind.PII_SCOPE, columns=("Grade Level",))

    def test_22a_the_staff_rescue_evidence(self, run_entity, raw_data, base_mapping) -> None:
        mappings = copy.deepcopy(base_mapping["mappings"])
        _set_id_role(mappings, "staff_id_col", "Staff Code")
        renamed = _rename(raw_data, "teacher id", "staff code", *_ENROLLMENT_FILES)
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Staff", _drop(renamed, "StudentSchedule.txt", "staff code"), mappings=mappings)
        _assert_schema_error(exc, entity="Staff", guard=GuardKind.JOIN_KEY, columns=("Staff Code",))

    def test_the_label_reaches_the_outcome_only_in_config_spelling(self) -> None:
        """Why exactness matters: the ledger keeps a label only when it is in the config's own
        vocabulary — ``Student ID`` survives, ``student id`` is dropped."""
        from src.etl.outcomes import safe_label

        vocabulary = frozenset({"Student ID"})
        assert safe_label("Student ID", vocabulary=vocabulary) == "Student ID"
        assert safe_label("student id", vocabulary=vocabulary) is None


# --------------------------------------------------------------------------- #
# Enrollments — ClassInformation co-teachers (§5 #15, owner ruling 2026-09-25)  #
# --------------------------------------------------------------------------- #
def _coteacher_context(*, class_info: pd.DataFrame, homerooms: bool, blended: bool) -> TransformContext:
    hr = (
        pd.DataFrame(
            {"school number": ["100"], "homeroom": ["A1"], "Class ID": ["100_A1_2025"], "teacher id": ["T001"]}
        )
        if homerooms
        else pd.DataFrame()
    )
    ctx = TransformContext(school_year=2025)
    ctx.class_artifacts = ClassArtifacts(
        homeroom_classes_df=hr,
        class_info_df=class_info,
        blended_class_map={"MT900": "BLENDED_X_2025"} if blended else {},
        blended_class_metadata={},
        blended_teacher_map={},
    )
    return ctx


def _class_info(**drop: bool) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "school number": ["100", "100"],
            "teacher id": ["T777", "T778"],
            "master timetable id": ["MT900", "MT901"],
            "primary teacher": ["Y", "Y"],
            "section letter": ["A1", "B9"],
        }
    )
    return frame.drop(columns=[c for c, gone in drop.items() if gone])


def _coteacher(ctx: TransformContext):
    return EnrollmentTransformer()._classinfo_coteacher_enrollments(
        "teacher id", "Teacher ID", {}, ctx.class_artifacts, ctx
    )


class TestCoteacherSites:
    """§5 #15 — owner ruling 2026-09-25 (reversing Gate A answer 2 for these columns only).

    The co-teacher columns of a present, non-empty ClassInformation are OPTIONAL: missing, the
    co-teacher rows they would link are left out, ONE aggregated WARNING names the columns in
    config spelling, and ``COTEACHER_SOURCE_UNUSABLE`` is recorded for the Enrollments outcome
    (a standing WARNING on Home — ``tests/test_coteacher_standing_warning.py``). Every other
    linking column stays fail-CLOSED: ClassInformation's school, once co-teacher rows are built.
    """

    NOTE = ((OutcomeNote.COTEACHER_SOURCE_UNUSABLE, 2),)

    @staticmethod
    def _left_out_lines(caplog) -> list[str]:
        return [r.getMessage() for r in caplog.records if "CO-TEACHERS LEFT OUT" in r.getMessage()]

    @staticmethod
    def _assert_claims_only_the_links(line: str) -> None:
        """With ONE path left out the other still reads the same primary-teacher rows, so the
        line may claim only that the links needing the missing column were not made — never
        that the rows went unused (review findings S10-AR-1 / M1 / PRIV-2)."""
        assert "the co-teacher links that need them were not made" in line
        assert "(2 Class Information row(s) affected)" in line
        assert "not used" not in line

    def test_twin_both_paths_enrol_the_coteachers_and_note_nothing(self, caplog) -> None:
        ctx = _coteacher_context(class_info=_class_info(), homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            rows = _coteacher(ctx)
        assert set(zip(rows["Class ID"], rows["User ID"])) == {("100_A1_2025", "T777"), ("BLENDED_X_2025", "T777")}
        assert ctx.outcome_notes_for("Enrollments") == ()
        assert self._left_out_lines(caplog) == []

    @pytest.mark.parametrize(
        ("column", "label"),
        [("primary teacher", "primary teacher"), ("teacher id", "Teacher ID")],
    )
    def test_an_entry_column_missing_leaves_every_coteacher_out_with_one_note(self, column, label, caplog) -> None:
        """``label`` is the config spelling: the id-role pair's ``Teacher ID``; the MyEd BC
        default for an unconfigured role. Both paths had work to do, and neither ran."""
        ctx = _coteacher_context(class_info=_class_info(**{column: True}), homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == self.NOTE  # every ClassInformation row unused
        (line,) = self._left_out_lines(caplog)
        assert repr([label]) in line

    def test_the_school_column_stays_fail_closed(self) -> None:
        """The ruling covers the co-teacher columns only; the school every co-teacher row reads
        is still a linking column (D9 keeps it strict)."""
        ctx = _coteacher_context(class_info=_class_info(**{"school number": True}), homerooms=False, blended=False)
        with pytest.raises(SourceSchemaError) as exc:
            _coteacher(ctx)
        _assert_schema_error(exc, entity="Enrollments", guard=GuardKind.JOIN_KEY, columns=("school number",))
        assert ctx.outcome_notes_for("Enrollments") == ()

    def test_the_school_is_never_read_when_no_coteacher_row_can_be_built(self) -> None:
        """An entry column missing means no co-teacher row is built, so the school is never read."""
        class_info = _class_info(**{"school number": True, "primary teacher": True})
        ctx = _coteacher_context(class_info=class_info, homerooms=True, blended=True)
        assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == self.NOTE

    def test_twin_an_absent_class_info_contributes_nothing_and_notes_nothing(self) -> None:
        ctx = _coteacher_context(class_info=pd.DataFrame(), homerooms=True, blended=True)
        assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()

    def test_path_1_left_out_path_2_still_enrols(self, caplog) -> None:
        ctx = _coteacher_context(class_info=_class_info(**{"section letter": True}), homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            rows = _coteacher(ctx)
        assert set(zip(rows["Class ID"], rows["User ID"])) == {("BLENDED_X_2025", "T777")}
        assert ctx.outcome_notes_for("Enrollments") == self.NOTE  # the primary-teacher rows
        (line,) = self._left_out_lines(caplog)
        assert repr(["section letter"]) in line
        self._assert_claims_only_the_links(line)

    def test_path_2_left_out_path_1_still_enrols(self, caplog) -> None:
        """SD40's shape (2026-09-25): ClassInformation without a Master Timetable ID while
        blended classes exist — the blended co-teachers are left out, the homeroom ones kept."""
        ctx = _coteacher_context(class_info=_class_info(**{"master timetable id": True}), homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            rows = _coteacher(ctx)
        assert set(zip(rows["Class ID"], rows["User ID"])) == {("100_A1_2025", "T777")}
        assert ctx.outcome_notes_for("Enrollments") == self.NOTE
        (line,) = self._left_out_lines(caplog)
        assert repr(["master timetable id"]) in line
        self._assert_claims_only_the_links(line)

    def test_path_1_twin_no_homerooms_reads_no_section_column(self) -> None:
        ctx = _coteacher_context(class_info=_class_info(**{"section letter": True}), homerooms=False, blended=False)
        assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()

    def test_path_2_twin_no_blends_reads_no_master_timetable_id(self) -> None:
        ctx = _coteacher_context(
            class_info=_class_info(**{"master timetable id": True}), homerooms=False, blended=False
        )
        assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()

    def test_both_paths_missing_is_one_note_and_one_line_naming_both(self, caplog) -> None:
        class_info = _class_info(**{"section letter": True, "master timetable id": True})
        ctx = _coteacher_context(class_info=class_info, homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == self.NOTE
        (line,) = self._left_out_lines(caplog)
        assert repr(["section letter", "master timetable id"]) in line

    @staticmethod
    def _plant_sentinel(class_info: pd.DataFrame) -> pd.DataFrame:
        """The sentinel as an extra OBSERVED header AND as every cell's value, so a line that
        interpolated the frame's columns or any of its values would carry it (P9). The
        primary-teacher flag keeps its ``Y`` where present, so the path branch still runs."""
        planted = class_info.copy()
        for column in planted.columns:
            if column != "primary teacher":
                planted[column] = _SENTINEL_HEADER
        planted[_SENTINEL_HEADER] = _SENTINEL_HEADER
        return planted

    def test_the_line_carries_config_names_and_a_count_never_a_value(self, caplog) -> None:
        """The entry-missing branch: no primary-teacher flag, so the whole file is affected."""
        class_info = self._plant_sentinel(_class_info(**{"primary teacher": True}))
        ctx = _coteacher_context(class_info=class_info, homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            _coteacher(ctx)
        (line,) = self._left_out_lines(caplog)  # the positive twin: the line fired
        assert "(2 Class Information row(s) affected)" in line
        assert _SENTINEL_HEADER not in line.lower()
        assert _SENTINEL_HEADER not in caplog.text.lower()

    def test_the_path_branch_line_carries_config_names_and_a_count_never_a_value(self, caplog) -> None:
        """The path-missing branch, which DOES read cell values (the primary-teacher flag):
        no Master Timetable ID while homerooms and blends both exist."""
        class_info = self._plant_sentinel(_class_info(**{"master timetable id": True}))
        ctx = _coteacher_context(class_info=class_info, homerooms=True, blended=True)
        with caplog.at_level(logging.WARNING):
            _coteacher(ctx)
        (line,) = self._left_out_lines(caplog)  # the positive twin: the line fired
        assert repr(["master timetable id"]) in line
        assert _SENTINEL_HEADER not in line.lower()
        assert _SENTINEL_HEADER not in caplog.text.lower()

    def test_twin_no_primary_teacher_rows_reads_neither_path_column(self) -> None:
        """No ``Y`` row means neither path runs, so neither path's column is read — unchanged."""
        class_info = _class_info(**{"section letter": True, "master timetable id": True})
        class_info["primary teacher"] = "N"
        ctx = _coteacher_context(class_info=class_info, homerooms=True, blended=True)
        assert _coteacher(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()


# --------------------------------------------------------------------------- #
# Students                                                                      #
# --------------------------------------------------------------------------- #
class TestStudentsSites:
    def test_27ii_a_demographic_without_the_user_id_source_fails_closed(self, run_entity, raw_data) -> None:
        """The roster used to become {'<NA>'} and silently drop every student row downstream."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Students", _drop(raw_data, "StudentDemographicInformation.txt", "student number"))
        _assert_schema_error(exc, entity="Students", guard=GuardKind.JOIN_KEY, columns=("Student Number",))

    def test_27ii_twin_present_publishes_the_roster(self, run_entity, raw_data, published_transformer) -> None:
        students = run_entity("Students", raw_data)
        assert set(students["User ID"]) == published_transformer._context.active_student_ids != set()

    def test_27i_a_mapping_without_user_id_is_not_this_guard(self, run_entity, raw_data, base_mapping) -> None:
        """No `User ID` mapped at all is §5 #27(i)'s fail-open posture (S11), not S10's."""
        mappings = copy.deepcopy(base_mapping["mappings"])
        del mappings["Students"]["field_map"]["User ID"]
        raw = _drop(raw_data, "StudentDemographicInformation.txt", "student number")
        run_entity("Students", raw, mappings=mappings)  # no raise

    @pytest.mark.parametrize("entry", [{"value": "X"}, {"format": "{legal first}"}])
    def test_27ii_a_user_id_that_reads_no_column_requires_none(self, raw_data, entry) -> None:
        """A fixed ``value:`` or an email ``format:`` names no source column to require."""
        frame = raw_data["StudentDemographicInformation.txt"].drop(columns="student number")
        StudentTransformer._require_user_id_source(frame, {"User ID": entry})  # no raise

    def test_27ii_twin_a_column_user_id_is_required(self, raw_data) -> None:
        frame = raw_data["StudentDemographicInformation.txt"].drop(columns="student number")
        with pytest.raises(SourceSchemaError) as exc:
            StudentTransformer._require_user_id_source(frame, {"User ID": {"column": "Student Number"}})
        _assert_schema_error(exc, entity="Students", guard=GuardKind.JOIN_KEY, columns=("Student Number",))

    def test_2a_collapse_without_its_school_column_fails_closed(self, run_entity, raw_data, global_config) -> None:
        gc = {**global_config, "cross_enrollment": {"collapse": True, "home_school_column": "Previous school number"}}
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Students", _drop(raw_data, "StudentDemographicInformation.txt", "school number"), gc=gc)
        _assert_schema_error(exc, entity="Students", guard=GuardKind.JOIN_KEY, columns=("School Number",))

    def test_2a_twin_collapse_off_checks_nothing(self, run_entity, raw_data) -> None:
        # Students' own field map reads School Number too, but only as an intended blank —
        # with the collapse off nothing requires it.
        assert not run_entity("Students", _drop(raw_data, "StudentDemographicInformation.txt", "school number")).empty


# --------------------------------------------------------------------------- #
# Staff (§5 #13a, #22a)                                                         #
# --------------------------------------------------------------------------- #
class TestStaffSites:
    def _mapping(self, base_mapping) -> dict:
        return base_mapping["mappings"]["Staff"]

    def test_22a_a_present_schedule_without_teacher_id_fails_the_rescue_closed(self, run_entity, raw_data) -> None:
        """T005 is un-roled; the rescue used to skip the frame in silence and DROP every
        un-roled teacher."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Staff", _drop(raw_data, "StudentSchedule.txt", "teacher id"))
        _assert_schema_error(exc, entity="Staff", guard=GuardKind.JOIN_KEY, columns=("Teacher ID",))

    def test_22a_twin_an_absent_schedule_is_simply_no_evidence(self, run_entity, raw_data) -> None:
        staff = run_entity("Staff", {**raw_data, "StudentSchedule.txt": pd.DataFrame()})
        assert "T001" in set(staff["User ID"])

    def test_22a_twin_all_staff_roled_never_consults_the_evidence(self, run_entity, raw_data) -> None:
        raw = _drop(raw_data, "StudentSchedule.txt", "teacher id")
        staff_frame = raw["StaffInformationEnhanced.txt"].copy()
        staff_frame["teaching staff"] = "Y"
        staff = run_entity("Staff", {**raw, "StaffInformationEnhanced.txt": staff_frame})
        assert len(staff) == len(staff_frame)

    def test_22a_a_demographic_without_teacher_id_fails_when_homerooms_exist(self, run_entity, raw_data) -> None:
        """With homeroom grades the demographic's teacher id is the homeroom teacher — a link."""
        with pytest.raises(SourceSchemaError) as exc:
            run_entity("Staff", _drop(raw_data, "StudentDemographicInformation.txt", "teacher id"))
        _assert_schema_error(exc, entity="Staff", guard=GuardKind.JOIN_KEY, columns=("Teacher ID",))

    def test_22a_twin_no_homeroom_grades_the_demographic_is_simply_no_evidence(
        self, run_entity, raw_data, global_config
    ) -> None:
        """With no homeroom grade that column links nothing (Classes and Enrollments never read
        it), so its absence is not a detected fault: the run builds, and un-roled T005 (in no
        timetable) is dropped exactly as it would be with the column present."""
        raw = _drop(raw_data, "StudentDemographicInformation.txt", "teacher id")
        staff = run_entity("Staff", raw, gc={**global_config, "homeroom_grades": []})
        assert set(staff["User ID"]) == {"T001", "T002", "T003", "T004"}

    def test_13a_a_roster_without_the_teacher_id_fails_closed(self, raw_data, base_mapping) -> None:
        mapping = copy.deepcopy(base_mapping["mappings"]["Staff"])
        mapping["source_files"] = {"staff_info": "StaffInformationEnhanced.txt", "roster": "Roster.txt"}
        roster = pd.DataFrame({"staff sourceid": ["X1"], "employee": ["T001"]})
        raw = {**raw_data, "Roster.txt": roster}
        ctx = TransformContext(raw_data=raw)
        with pytest.raises(SourceSchemaError) as exc:
            StaffTransformer()._merge_roster(raw["StaffInformationEnhanced.txt"], mapping, ctx)
        _assert_schema_error(exc, entity="Staff", guard=GuardKind.JOIN_KEY, columns=("teacher id",))

    def test_13a_twin_the_roster_merges(self, raw_data, base_mapping) -> None:
        mapping = copy.deepcopy(base_mapping["mappings"]["Staff"])
        mapping["source_files"] = {"staff_info": "StaffInformationEnhanced.txt", "roster": "Roster.txt"}
        roster = pd.DataFrame({"staff sourceid": ["X1"], "teacher id": ["T001"]})
        ctx = TransformContext(raw_data={**raw_data, "Roster.txt": roster})
        merged = StaffTransformer()._merge_roster(raw_data["StaffInformationEnhanced.txt"], mapping, ctx)
        assert "staff sourceid" in merged.columns


# --------------------------------------------------------------------------- #
# Blended detection (§5 #39) and the field-map ID (§5 #29)                      #
# --------------------------------------------------------------------------- #
class TestBlendedSessionColumns:
    def _run(self, published_transformer, classes_mapping, raw, global_config) -> pd.DataFrame:
        # The fixture's blend is grades 1-3; with the base K-7 homeroom set it would be
        # suppressed as homeroom-only (plan 0043), so no homeroom grades here.
        published_transformer.set_school_year(2025, "08-25", "07-25")
        gc = {**global_config, "homeroom_grades": []}
        return published_transformer.transform(raw["StudentSchedule.txt"], classes_mapping, "Classes", raw, gc)

    def test_twin_the_blend_is_detected(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        classes = self._run(published_transformer, classes_mapping, raw_data_with_blended, global_config)
        assert classes["Class ID"].astype(str).str.startswith("BLENDED_").any()

    def test_a_configured_time_column_the_frame_lacks_fails_closed(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        mapping = {**classes_mapping, "source_columns": {"session_period": "Block"}}
        with pytest.raises(SourceSchemaError) as exc:
            self._run(published_transformer, mapping, raw_data_with_blended, global_config)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("Block",))

    def test_the_school_column_is_required(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        raw = _drop(raw_data_with_blended, "ClassInformationEnh.txt", "school number")
        with pytest.raises(SourceSchemaError) as exc:
            self._run(published_transformer, classes_mapping, raw, global_config)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("school number",))

    @pytest.mark.parametrize("column", ["term", "semester", "day", "period"])
    def test_a_DEFAULT_time_column_absent_fails_closed_too(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config, column
    ) -> None:
        """No `session_components` is declared, so all four components are in force, each its
        MyEd BC default — and absent, it used to be dropped from the key, merging distinct
        sections into one blended class. A district whose export has no such column DECLARES
        the ones it has (SD40, owner ruling 2026-09-25 — ``TestDeclaredSessionComponents``)."""
        raw = _drop(raw_data_with_blended, "ClassInformationEnh.txt", column)
        with pytest.raises(SourceSchemaError) as exc:
            self._run(published_transformer, classes_mapping, raw, global_config)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=(column,))


class TestDeclaredSessionComponents:
    """Owner ruling 2026-09-25 (plan 0053 S10): §5 #39 stays STRICT on the EFFECTIVE component set,
    and a district whose export lacks a component DECLARES the ones it has (Classes
    ``session_components`` — config format 1.14). ``sd40myedbc`` is the first to."""

    _WITHOUT_TERM = ["session_semester", "session_day", "session_period"]

    def _detect(self, published_transformer, mapping, raw, global_config) -> pd.DataFrame:
        return TestBlendedSessionColumns()._run(published_transformer, mapping, raw, global_config)

    def test_a_declared_component_set_builds_without_the_undeclared_column(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        raw = _drop(raw_data_with_blended, "ClassInformationEnh.txt", "term")
        mapping = {**classes_mapping, "session_components": self._WITHOUT_TERM}
        classes = self._detect(published_transformer, mapping, raw, global_config)
        assert classes["Class ID"].astype(str).str.startswith("BLENDED_").any()

    def test_twin_the_same_frame_without_the_declaration_fails_naming_term(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        raw = _drop(raw_data_with_blended, "ClassInformationEnh.txt", "term")
        with pytest.raises(SourceSchemaError) as exc:
            self._detect(published_transformer, classes_mapping, raw, global_config)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("term",))

    def test_a_declared_component_is_still_required(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        """Strict on the EFFECTIVE set (review F2): declaring fewer never narrows the guard to
        nothing — a declared component the frame lacks still fails."""
        raw = _drop(raw_data_with_blended, "ClassInformationEnh.txt", "term", "day")
        mapping = {**classes_mapping, "session_components": self._WITHOUT_TERM}
        with pytest.raises(SourceSchemaError) as exc:
            self._detect(published_transformer, mapping, raw, global_config)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("day",))

    def test_the_declared_key_equals_the_pre_s10_available_components_key(
        self, published_transformer, classes_mapping, raw_data_with_blended, global_config
    ) -> None:
        """Byte-identity, by construction. Before S10 a component ABSENT from the frame was left
        out of the session key (``_add_session_key`` still keys on the components a frame
        carries when a caller skips ``detect``). So on a term-less frame the DECLARED three give
        exactly the key all four gave then — and the blended Class ID, which embeds the key,
        has no empty segment. (A BLANK term column would not do: it adds an empty segment to
        the Class ID — ``BLENDED_…_T010__1…`` — which is why the declaration, not a blank
        column, is the byte-identical spelling.)"""
        from src.etl.transformers.blended import (
            BlendedClassDetector,
            session_time_components,
            session_time_roles,
        )

        frame = _drop(raw_data_with_blended, "ClassInformationEnh.txt", "term")["ClassInformationEnh.txt"]
        declared = session_time_components({}, roles=session_time_roles(self._WITHOUT_TERM))
        all_four = session_time_components({}, roles=session_time_roles(None))
        assert "term" in all_four and "term" not in declared
        keyed_declared = BlendedClassDetector._add_session_key(frame.copy(), "teacher id", components=declared)
        keyed_before = BlendedClassDetector._add_session_key(frame.copy(), "teacher id", components=all_four)
        assert keyed_declared["session_key"].tolist() == keyed_before["session_key"].tolist()

        classes = self._detect(
            published_transformer,
            {**classes_mapping, "session_components": self._WITHOUT_TERM},
            _drop(raw_data_with_blended, "ClassInformationEnh.txt", "term"),
            global_config,
        )
        blended = [cid for cid in classes["Class ID"].astype(str) if cid.startswith("BLENDED_")]
        assert blended and all("__" not in cid for cid in blended)

    @pytest.mark.integration
    def test_sd40_end_to_end_builds_declared_and_fails_undeclared(self, tmp_path) -> None:
        """The bundled ``sd40myedbc`` on its term-less contract fixture BUILDS; the same drop through
        an overlay that puts ``term`` back in force (all four roles) fails on Classes naming it —
        the guard is strict on the effective set, never narrowed to "configured only"."""
        from src.utils.paths import user_mappings_dir
        from tests.test_contract import _create_sd40_inputs

        inp = tmp_path / "in"
        inp.mkdir()
        _create_sd40_inputs(inp)
        result = run_pipeline("sd40myedbc", str(inp), str(tmp_path / "out_declared"), dry_run=True)
        assert result.entity_counts["Classes"] > 0

        (user_mappings_dir() / "sd40allfourmyedbc_mapping.yaml").write_text(
            "_base: sd40myedbc\n"
            "mappings:\n"
            "  Classes:\n"
            "    session_components: [session_term, session_semester, session_day, session_period]\n",
            encoding="utf-8",
        )
        with pytest.raises(SourceSchemaError) as exc:
            run_pipeline("sd40allfourmyedbc", str(inp), str(tmp_path / "out_all_four"), dry_run=True)
        _assert_schema_error(exc, entity="Classes", guard=GuardKind.JOIN_KEY, columns=("term",))

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            (None, ("session_term", "session_semester", "session_day", "session_period")),
            (["session_period", "session_semester"], ("session_semester", "session_period")),  # fixed order
        ],
    )
    def test_the_roles_keep_the_fixed_key_order(self, declared, expected) -> None:
        from src.etl.transformers.blended import session_time_roles

        assert session_time_roles(declared) == expected

    @pytest.mark.parametrize("declared", [[], ["session_week"], ["session_day", "session_day"]])
    def test_the_runtime_floor_refuses_what_validation_refuses(self, declared) -> None:
        from src.etl.transformers.blended import session_time_roles

        with pytest.raises(ValueError):
            session_time_roles(declared)


class TestSessionComponentsValidation:
    """``session_components`` is validated at config load: a known role list, non-empty, each once,
    on Classes only, and never contradicting a configured ``source_columns`` role."""

    @staticmethod
    def _load(classes_extra: dict | None = None, *, other: str | None = None):
        from src.config.loader import validate_overlay

        overlay: dict = {"_base": "myedbc", "mappings": {"Classes": dict(classes_extra or {})}}
        if other is not None:
            overlay["mappings"][other] = {"session_components": ["session_day"]}
        return validate_overlay(overlay)

    def test_the_bundled_sd40_config_declares_its_three(self) -> None:
        from src.config.loader import load_config

        config = load_config("sd40myedbc")
        expected = TestDeclaredSessionComponents._WITHOUT_TERM
        assert config.mappings["Classes"].session_components == expected
        assert config.to_raw_dict()["mappings"]["Classes"]["session_components"] == expected
        # The twin: an undeclared config carries no key at all (all four, byte-identical).
        assert "session_components" not in load_config("myedbc").to_raw_dict()["mappings"]["Classes"]

    @pytest.mark.parametrize(
        ("classes_extra", "message"),
        [
            ({"session_components": []}, "is empty"),
            ({"session_components": ["session_week"]}, "unknown role"),
            ({"session_components": ["session_day", "session_day"]}, "more than once"),
            (
                {"session_components": ["session_day"], "source_columns": {"session_period": "Block"}},
                "never be read",
            ),
        ],
    )
    def test_a_bad_declaration_is_refused(self, classes_extra, message) -> None:
        with pytest.raises(Exception, match=message):
            self._load(classes_extra)

    def test_a_declaration_on_another_entity_is_refused(self) -> None:
        with pytest.raises(Exception, match="only the Classes entity reads session_components"):
            self._load(other="Enrollments")

    def test_an_explicit_null_declaration_is_all_four(self) -> None:
        config = self._load({"session_components": None})
        assert config.mappings["Classes"].session_components is None
        assert "session_components" not in config.to_raw_dict()["mappings"]["Classes"]

    def test_twin_a_valid_declaration_loads(self) -> None:
        self._load(
            {"session_components": ["session_day", "session_period"], "source_columns": {"session_day": "Cycle"}}
        )


class TestAppendYearIdField:
    """§5 #29: `FieldAppendYear` is an ID — its absent column is not an intended blank."""

    def _apply(self, spec: FieldAppendYear, working: pd.DataFrame):
        return spec.apply(working, _Host(), "Class ID", "Students", TransformContext(school_year=2026))

    def test_an_absent_id_column_fails_closed(self) -> None:
        with pytest.raises(SourceSchemaError) as exc:
            self._apply(FieldAppendYear(column="Master Timetable ID"), pd.DataFrame({"x": ["1"]}))
        assert (exc.value.entity, exc.value.columns, exc.value.guard) == (
            "Students",
            ("Master Timetable ID",),
            GuardKind.JOIN_KEY,
        )

    def test_twin_present_appends_the_year(self) -> None:
        out = self._apply(FieldAppendYear(column="Master Timetable ID"), pd.DataFrame({"master timetable id": ["MT1"]}))
        assert list(out) == ["MT1_2026"]

    def test_twin_append_disabled_is_still_a_direct_read(self) -> None:
        out = self._apply(FieldAppendYear(column="X", append_year_to_id=False), pd.DataFrame({"y": ["1"]}))
        assert out is pd.NA


class _Host(BaseTransformer):
    def transform(self, df, mapping, context):  # pragma: no cover - never called
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# The partial ship is gone (AST pin, with its doctored twin)                    #
# --------------------------------------------------------------------------- #
_FORBIDDEN_CAUGHT = frozenset({"KeyError", "MergeError"})

#: The ONE sanctioned ``KeyError`` handler under ``src/etl/transformers``, with its reason.
#: It is CELL scope (one row whose email template names a key that row lacks gets a blank
#: address) and it is RECORDED — one ERROR line + a ``context.data_errors`` entry, surfaced
#: as "Completed with N data errors" — like the field-map engine's per-cell isolation; it
#: never ships part of an entity in place of the whole.
_ALLOWED: dict[tuple[str, str], str] = {
    ("students.py", "_generate_emails"): "per-row email-template isolation, recorded to data_errors",
}


def _handlers_catching_keyerror_or_mergeerror(source: str, filename: str) -> list[tuple[str, str, int]]:
    """Every ``except`` clause naming ``KeyError`` or pandas ``MergeError``: (file, function, line)."""
    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.ExceptHandler) and child.type is not None:
                caught = child.type.elts if isinstance(child.type, ast.Tuple) else [child.type]
                names = {c.attr if isinstance(c, ast.Attribute) else getattr(c, "id", "") for c in caught}
                if names & _FORBIDDEN_CAUGHT:
                    found.append((filename, function, child.lineno))
            visit(child, name)

    visit(ast.parse(source, filename=filename), "<module>")
    return found


class TestNoPartialShip:
    def _all_handlers(self) -> list[tuple[str, str, int]]:
        files = sorted(_TRANSFORMERS_DIR.glob("*.py"))
        assert len(files) >= 10, "non-vacuity: the transformer package was found"
        return [
            hit
            for path in files
            for hit in _handlers_catching_keyerror_or_mergeerror(path.read_text("utf-8"), path.name)
        ]

    def test_no_transformer_catches_keyerror_or_mergeerror_outside_the_allowlist(self) -> None:
        offenders = [hit for hit in self._all_handlers() if (hit[0], hit[1]) not in _ALLOWED]
        assert offenders == [], "a missing column must raise a typed error, never be caught to ship the rest"

    def test_non_vacuity_the_detector_finds_the_allowlisted_handler_in_the_real_tree(self) -> None:
        assert {(f, fn) for f, fn, _ in self._all_handlers()} == set(_ALLOWED), "allowlist entries must be live"

    def test_doctored_the_deleted_handler_is_red(self) -> None:
        """The shape plan 0053 S10 deleted from `enrollments._homeroom_enrollments`."""
        doctored = (
            "def _homeroom_enrollments():\n    try:\n        x = 1\n"
            "    except (KeyError, pd.errors.MergeError) as e:\n        pass\n"
        )
        hits = _handlers_catching_keyerror_or_mergeerror(doctored, "enrollments.py")
        assert hits == [("enrollments.py", "_homeroom_enrollments", 4)]
        assert ("enrollments.py", "_homeroom_enrollments") not in _ALLOWED

    def test_other_handlers_are_not_flagged(self) -> None:
        clean = "try:\n    x = 1\nexcept EtlError:\n    raise\nexcept Exception:\n    pass\n"
        assert _handlers_catching_keyerror_or_mergeerror(clean, "base.py") == []


# --------------------------------------------------------------------------- #
# End to end: a CRITICAL entity's guard fails the run, typed and named          #
# --------------------------------------------------------------------------- #
def _snapshot(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.csv"))}


@pytest.mark.integration
def test_a_critical_guard_fails_the_run_with_source_schema_and_the_entity_named(tmp_path: Path) -> None:
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    _create_myedbc_inputs(input_dir)

    # Night 1 — the twin: the intact drop builds Classes.
    clean = run_pipeline("myedbc", str(input_dir), str(output_dir))
    assert {o.entity: o.kind for o in clean.entity_outcomes}["Classes"] is OutcomeKind.BUILT
    before = _snapshot(output_dir)
    assert "Classes.csv" in before

    # Night 2 — the course export lost its title column (§5 #8).
    course = pd.read_csv(input_dir / "CourseInformation.txt", dtype=str)
    course.drop(columns="Title").to_csv(input_dir / "CourseInformation.txt", index=False)
    with pytest.raises(SourceSchemaError) as raised:
        run_pipeline("myedbc", str(input_dir), str(output_dir))
    assert (raised.value.entity, raised.value.guard, raised.value.columns) == (
        "Classes",
        GuardKind.JOIN_KEY,
        ("title",),
    )
    assert _snapshot(output_dir) == before, "the last good output is untouched"

    record = (read_run_records() or [])[0]
    assert (record["status"], record["error_category"]) == ("failed", "source_schema")
    outcomes = record["entity_outcomes"]
    assert (outcomes["Classes"]["kind"], outcomes["Classes"]["reason"]) == (
        "failed",
        OutcomeReason.MISSING_SOURCE_COLUMN.value,
    )
    assert outcomes["Enrollments"]["kind"] == "not_run"
