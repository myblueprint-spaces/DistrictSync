"""The ONE source-column resolver (plan 0053 S9 — ``failure-policy.md`` §9, P10).

Pins, in order:

1. the shape policy of :func:`columns.resolve_source_column` over every field-map shape;
2. its two once-per-run log lines — the default-fallback DEBUG line and the
   TRANSITIONAL rename WARNING — each with a twin proving it fires;
3. :func:`columns.source_column_label` and the grade-scope error it feeds, which S7's
   ``safe_label`` now accepts;
4. the ``TransformContext`` accessors reading ``entity_mappings`` (the production path);
5. end to end: a renamed Students ``User ID`` drives the homeroom active-roster filter in
   Classes AND Enrollments (before S9 it silently fell back to ``student number``), and
   the other columns S9 made configurable (the Students ``Grade``/``Homeroom`` on the
   homeroom path incl. the co-teacher lookup, the schedule grade in both subject splits
   AND blended detection, the blended time slot, the co-teacher ClassInformation
   columns) — each twinned with the default spelling producing the identical output;
6. the keyword-only, undefaulted parameters that decide which column is read.
"""

import copy
import inspect
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.loader import load_config
from src.config.models import FieldTransform
from src.etl.errors import SourceSchemaError
from src.etl.outcomes import OutcomeLedger, safe_label
from src.etl.pipeline import configured_entity_order, run_transform
from src.etl.preflight import label_vocabulary_by_entity
from src.etl.transformer import DataTransformer
from src.etl.transformers import columns
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import BlendedClassDetector, session_time_components, session_time_labels
from src.etl.transformers.columns import Previously, require_columns, resolve_source_column, source_column_label
from src.etl.transformers.context import TransformContext

BUNDLED = Path("config/mappings")
COLUMNS_LOGGER = "src.etl.transformers.columns"


@pytest.fixture(autouse=True)
def _fresh_run_window():
    """Every test opens its own run window, so a notice logged elsewhere cannot mute it."""
    columns.reset_run_notices()
    yield
    columns.reset_run_notices()


def _resolve(value, *, default="grade", previously=Previously.UNCHANGED):
    field_map = {} if value is _ABSENT else {"Grade": value}
    return resolve_source_column(field_map, "Grade", default=default, previously=previously)


_ABSENT = object()


def _warnings(caplog, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and text in r.getMessage()]


# ---------------------------------------------------------------------------
# 1. The shape policy
# ---------------------------------------------------------------------------


class TestShapePolicy:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param({"column": "Grade Level", "transform": "grade_to_ceds"}, "grade level", id="transform-dict"),
            pytest.param({"column": "Pupil Grade"}, "pupil grade", id="column-only-dict"),
            pytest.param({"column": "MT Code", "append_year_to_id": True}, "mt code", id="append-year-dict"),
            pytest.param({"column": "   "}, "grade", id="blank-column"),
            pytest.param("Grade Level", "grade level", id="bare-string"),
            pytest.param("  Grade Level  ", "grade level", id="bare-string-trimmed"),
            pytest.param("", "grade", id="blank-string"),
            pytest.param("   ", "grade", id="whitespace-string"),
            pytest.param(None, "grade", id="null-sentinel"),
            pytest.param(_ABSENT, "grade", id="absent-key"),
            pytest.param(12, "12", id="non-string-is-its-text-per-classify_field"),
            pytest.param({"value": ""}, "grade", id="fixed-value"),
            pytest.param({"student_id_col": "Student ID", "staff_id_col": "Teacher ID"}, "grade", id="id-role"),
            pytest.param({"format": "{student number}"}, "grade", id="email-format"),
            pytest.param({"use_academic_year": True}, "grade", id="academic-year"),
            pytest.param({"teacher last name": "Teacher Name"}, "grade", id="name-block"),
        ],
    )
    def test_every_shape_resolves_by_the_one_policy(self, value, expected):
        assert _resolve(value) == expected

    def test_a_dict_with_no_mapping_shape_is_refused_not_read_as_the_default(self):
        """Plan 0053 S12: ``classify_field`` refuses a typo'd dict (it used to be read as the
        default column, silently). Twin: the correctly spelled key resolves."""
        with pytest.raises(ValueError, match="unknown key 'colum' .*did you mean 'column'"):
            _resolve({"colum": "Typo"})
        assert _resolve({"column": "Typo"}) == "typo"

    def test_an_already_typed_value_resolves_exactly_as_its_raw_dict(self):
        """`ensure_field_mapping` is the one boundary: a validated MappingConfig's typed
        value and the raw YAML dict it came from must agree."""
        typed = FieldTransform(column="Grade Level", transform="grade_to_ceds")
        assert _resolve(typed) == _resolve({"column": "Grade Level", "transform": "grade_to_ceds"}) == "grade level"

    def test_the_answer_and_the_default_are_both_normalised(self):
        assert _resolve(_ABSENT, default="  Student Number ") == "student number"
        assert _resolve("  PUPIL No ") == "pupil no"

    def test_a_non_mapping_container_is_refused_not_read_as_empty(self):
        with pytest.raises(TypeError, match="needs a mapping"):
            resolve_source_column(
                "Student ID", "student_id_col", default="student number", previously=Previously.UNCHANGED
            )

    def test_the_same_resolution_serves_a_carrier_block_and_source_columns(self):
        name_block = {"teacher last name": "Teacher Name", "section letter": ""}
        assert (
            resolve_source_column(name_block, "teacher last name", default="last name", previously=Previously.UNCHANGED)
            == "teacher name"
        )
        assert (
            resolve_source_column(
                name_block, "section letter", default="section letter", previously=Previously.UNCHANGED
            )
            == "section letter"
        )
        assert (
            resolve_source_column(
                {"staff_status": "Employment"}, "staff_status", default="staff status", previously=Previously.UNCHANGED
            )
            == "employment"
        )


# ---------------------------------------------------------------------------
# 2. The once-per-run log lines
# ---------------------------------------------------------------------------


class TestDefaultFallbackIsLoggedOncePerRun:
    def test_a_fallback_logs_one_debug_line_per_run(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=COLUMNS_LOGGER):
            _resolve(_ABSENT)
            _resolve(None)
            debug = [
                r for r in caplog.records if r.levelno == logging.DEBUG and "reading the default" in r.getMessage()
            ]
            assert len(debug) == 1
            assert "'Grade'" in debug[0].getMessage() and "'grade'" in debug[0].getMessage()

            columns.reset_run_notices()  # the next run
            _resolve(_ABSENT)
            debug = [
                r for r in caplog.records if r.levelno == logging.DEBUG and "reading the default" in r.getMessage()
            ]
            assert len(debug) == 2

    def test_twin_a_configured_column_logs_no_fallback(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=COLUMNS_LOGGER):
            _resolve("Grade Level")
        assert not [r for r in caplog.records if "reading the default" in r.getMessage()]


class TestUnexpectedShapeWarnsOncePerRun:
    def test_a_shape_outside_the_allowlist_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _resolve({"use_academic_year": True})
            _resolve({"use_academic_year": True})
        warned = _warnings(caplog, "names no source column")
        assert len(warned) == 1
        assert "'Grade'" in warned[0] and "FieldAcademicYear" in warned[0]

    @pytest.mark.parametrize(
        "value",
        [{"value": ""}, {"student_id_col": "A", "staff_id_col": "B"}, {"format": "{x}"}, None],
        ids=["fixed-value", "id-role", "email-format", "null"],
    )
    def test_twin_an_allowlisted_shape_is_silent(self, caplog, value):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _resolve(value)
        assert not _warnings(caplog, "names no source column")


class TestTransitionalRenameWarning:
    """TRANSITIONAL (one release; ROADMAP): the district log names a rename that starts
    taking effect with this build — and stays quiet where nothing changed."""

    @pytest.mark.parametrize(
        ("value", "previously", "was"),
        [
            pytest.param("Grade Level", Previously.COLUMN_KEY_ONLY, "grade", id="bare-string-was-ignored"),
            pytest.param({"column": "Pupil Grade"}, Previously.DEFAULT, "grade", id="dead-path-read-the-default"),
            pytest.param("", Previously.AS_CONFIGURED, "(no column)", id="a-blank-was-no-column"),
            pytest.param(None, Previously.AS_CONFIGURED, "(no column)", id="a-null-was-no-column"),
            pytest.param({"column": ""}, Previously.COLUMN_KEY_ONLY, "(no column)", id="blank-column-was-read-blank"),
            pytest.param({"column": ""}, Previously.AS_CONFIGURED, "(no column)", id="blank-column-dict-was-no-column"),
        ],
    )
    def test_a_changed_answer_warns_with_config_vocabulary_only(self, caplog, value, previously, was):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            answer = _resolve(value, previously=previously)
        (message,) = _warnings(caplog, "now reads source column")
        assert message == (
            f"[columns] 'Grade' now reads source column '{answer}'; versions before this one read "
            f"'{was}' here. The district mapping now takes effect at this step — check the output if "
            f"that is not intended. (A temporary notice.)"
        )

    @pytest.mark.parametrize(
        ("value", "previously"),
        [
            pytest.param({"column": "Grade Level"}, Previously.COLUMN_KEY_ONLY, id="dict-was-already-read"),
            pytest.param("Grade", Previously.DEFAULT, id="spelling-of-the-default"),
            pytest.param("Grade Level", Previously.AS_CONFIGURED, id="bare-string-was-already-read"),
            pytest.param({"column": "Grade Level"}, Previously.AS_CONFIGURED, id="column-dict-was-already-read"),
            pytest.param("Grade Level", Previously.UNCHANGED, id="unchanged-site-never-warns"),
            pytest.param(_ABSENT, Previously.DEFAULT, id="nothing-configured"),
        ],
    )
    def test_twin_an_unchanged_answer_is_silent(self, caplog, value, previously):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _resolve(value, previously=previously)
        assert not _warnings(caplog, "now reads source column")

    def test_it_is_logged_once_per_run_and_again_on_the_next_run(self, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            for _ in range(3):
                _resolve("Grade Level", previously=Previously.COLUMN_KEY_ONLY)
            assert len(_warnings(caplog, "now reads source column")) == 1
            columns.reset_run_notices()
            _resolve("Grade Level", previously=Previously.COLUMN_KEY_ONLY)
            assert len(_warnings(caplog, "now reads source column")) == 2


# ---------------------------------------------------------------------------
# 3. The config-spelling label and the grade-scope error (S1 site #4 + S7)
# ---------------------------------------------------------------------------


class TestSourceColumnLabel:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ({"column": " Grade Level ", "transform": "grade_to_ceds"}, "Grade Level"),
            ("Grade", "Grade"),
            ({"value": ""}, "grade"),
            (_ABSENT, "grade"),
        ],
    )
    def test_the_config_spelling_is_kept_else_the_default(self, value, expected):
        field_map = {} if value is _ABSENT else {"Grade": value}
        assert source_column_label(field_map, "Grade", default="grade") == expected

    def test_a_non_mapping_container_is_refused_not_read_as_empty(self):
        with pytest.raises(TypeError, match="needs a mapping"):
            source_column_label("Student ID", "student_id_col", default="student number")

    def test_it_agrees_with_the_resolver_on_which_column(self):
        field_map = {"Grade": {"column": " Grade Level ", "transform": "grade_to_ceds"}}
        label = source_column_label(field_map, "Grade", default="grade")
        assert label.strip().lower() == resolve_source_column(
            field_map, "Grade", default="grade", previously=Previously.UNCHANGED
        )


class TestGradeScopeErrorNamesTheConfigSpelling:
    """S1 left site #4 carrying the RESOLVED lower-cased column "until S9's resolver
    lands" — so S7's label check could never name it. Now it carries the config's own
    spelling and `safe_label` accepts it; the default (not config-declared) still is not."""

    def _run(self, grade_value):
        cfg = load_config("sd27myedbc", config_dir=BUNDLED)
        raw = cfg.to_raw_dict()
        students = copy.deepcopy(raw["mappings"]["Students"])
        if grade_value is not _ABSENT:
            students["field_map"]["Grade"] = grade_value
        demographic = pd.DataFrame(
            {
                "student number": ["S1"],
                "legal first name": ["A"],
                "legal surname": ["B"],
                "enrolment status": ["Active"],
            }
        )
        t = DataTransformer()
        t.set_school_year(2025, "08-25", "07-25")
        t.set_entity_mappings(raw["mappings"])
        with pytest.raises(SourceSchemaError) as exc:
            t.transform(
                demographic,
                students,
                "Students",
                {"StudentDemographicInformation.txt": demographic},
                raw["global_config"],
            )
        vocabulary = label_vocabulary_by_entity(cfg)["Students"].columns
        return exc.value, vocabulary

    def test_the_configured_column_is_named_and_safe_label_accepts_it(self):
        err, vocabulary = self._run(_ABSENT)  # sd27 inherits `Grade: {column: Grade, ...}`
        assert err.columns == ("Grade",)
        assert safe_label(err.columns[0], vocabulary=vocabulary) == "Grade"

    def test_twin_an_undeclared_default_is_named_but_never_labelled(self):
        err, vocabulary = self._run({"value": ""})
        assert err.columns == ("grade",)
        assert safe_label(err.columns[0], vocabulary=vocabulary) is None


# ---------------------------------------------------------------------------
# 4. The context accessors read `entity_mappings` (the production path)
# ---------------------------------------------------------------------------


def _mappings(*, students=None, classes=None, enrollments=None) -> dict:
    return {
        "Students": {"field_map": students or {}},
        "Classes": {"field_map": classes or {}},
        "Enrollments": {"field_map": enrollments or {}},
    }


class TestContextAccessors:
    def test_students_config_is_read_from_entity_mappings(self):
        ctx = TransformContext(entity_mappings=_mappings(students={"User ID": "Pupil No"}))
        assert ctx.get_students_config() == {"field_map": {"User ID": "Pupil No"}}

    def test_twin_no_mappings_anywhere_means_the_defaults(self):
        ctx = TransformContext()
        assert ctx.get_students_config() == {}
        assert ctx.get_demo_student_col() == "student number"
        assert ctx.get_teacher_id_col() == "teacher id"
        assert ctx.get_schedule_grade_col() == "grade"

    def test_entity_mappings_win_over_a_hand_built_global_config(self):
        ctx = TransformContext(
            entity_mappings=_mappings(students={"User ID": "Pupil No"}),
            global_config={"mappings": _mappings(students={"User ID": "Other Column"})},
        )
        assert ctx.get_demo_student_col() == "pupil no"

    def test_a_hand_built_context_still_reads_global_config_mappings(self):
        """The fallback the spec keeps for a context a test builds by hand (production
        never populates it) — proven with a NON-default value, so it is not vacuous."""
        ctx = TransformContext(global_config={"mappings": _mappings(students={"User ID": {"column": "Pupil No"}})})
        assert ctx.get_demo_student_col() == "pupil no"

    @pytest.mark.parametrize("user_id", ["Pupil No", {"column": "Pupil No"}], ids=["bare-string", "column-dict"])
    def test_demo_student_col_honours_both_shapes(self, user_id):
        ctx = TransformContext(entity_mappings=_mappings(students={"User ID": user_id}))
        assert ctx.get_demo_student_col() == "pupil no"

    def test_teacher_id_col_reads_the_enrollments_id_role_pair(self):
        ctx = TransformContext(
            entity_mappings=_mappings(
                enrollments={"User ID": {"student_id_col": "Student ID", "staff_id_col": "Staff Key"}}
            )
        )
        assert ctx.get_teacher_id_col() == "staff key"

    def test_teacher_id_col_defaults_when_user_id_is_not_a_pair(self):
        ctx = TransformContext(entity_mappings=_mappings(enrollments={"User ID": "Teacher ID"}))
        assert ctx.get_teacher_id_col() == "teacher id"

    def test_schedule_grade_col_is_the_classes_grade_key(self):
        ctx = TransformContext(entity_mappings=_mappings(classes={"Grade": "Year Level"}))
        assert ctx.get_schedule_grade_col() == "year level"


# ---------------------------------------------------------------------------
# 5. End to end through the transformers
# ---------------------------------------------------------------------------

_HOMEROOM_DEMO = [
    # student, grade, homeroom, status, teacher name, teacher id
    ("S001", "K", "A1", "Active", "Harper", "T001"),
    ("S002", "3", "A1", "Active", "Harper", "T001"),
    ("S005", "4", "B2", "Active", "Reed", "T002"),
    # Inactive and ALONE in Z9: with the active filter working, no Z9 class exists
    # and S010 has no enrollment.
    ("S010", "1", "Z9", "Inactive", "Moss", "T009"),
]


def _demographic(student_col: str, *, grade_col: str = "grade", homeroom_col: str = "homeroom") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                student_col: num,
                "legal first name": f"F{num}",
                "legal surname": f"L{num}",
                grade_col: grade,
                "school number": "100",
                homeroom_col: hr,
                "enrolment status": status,
                "teacher name": tname,
                "teacher id": tid,
            }
            for num, grade, hr, status, tname, tid in _HOMEROOM_DEMO
        ]
    )


def _run_homeroom(base_mapping, *, renamed: bool, renamed_grade_and_homeroom: bool = False):
    mappings = copy.deepcopy(base_mapping["mappings"])
    if renamed:
        mappings["Students"]["field_map"]["User ID"] = {"column": "Pupil No"}
        mappings["Students"]["field_map"]["Student Number"] = "Pupil No"
    if renamed_grade_and_homeroom:
        mappings["Students"]["field_map"]["Grade"] = {"column": "Grade Level", "transform": "grade_to_ceds"}
        mappings["Students"]["field_map"]["Homeroom"] = {"column": "HR Code"}
        demographic = _demographic("student number", grade_col="grade level", homeroom_col="hr code")
    else:
        demographic = _demographic("pupil no" if renamed else "student number")
    raw = {"StudentDemographicInformation.txt": demographic}
    gc = dict(base_mapping["global_config"])
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    t.set_entity_mappings(mappings)  # exactly what run_transform publishes
    students = t.transform(demographic, mappings["Students"], "Students", raw, gc)
    classes = t.transform(pd.DataFrame(), mappings["Classes"], "Classes", raw, gc)
    enrollments = t.transform(pd.DataFrame(), mappings["Enrollments"], "Enrollments", raw, gc)
    return students, classes, enrollments


class TestRenamedStudentNumberDrivesTheHomeroomFilter:
    """The integration AC. Before S9 the Classes/Enrollments homeroom path read the
    Students mapping from a dead config path, so `"User ID": {column: "Pupil No"}` fell
    back to `student number`: Classes' `filter_to_active` skipped (the inactive pupil's
    homeroom class was built) and Enrollments' homeroom student rows were lost."""

    GOLDEN_CLASS_IDS = ["100_A1_2025", "100_B2_2025"]
    GOLDEN_STUDENT_ROWS = {("100_A1_2025", "S001"), ("100_A1_2025", "S002"), ("100_B2_2025", "S005")}

    def test_defaults_match_the_golden(self, base_mapping):
        students, classes, enrollments = _run_homeroom(base_mapping, renamed=False)
        assert set(students["User ID"]) == {"S001", "S002", "S005"}
        assert sorted(classes["Class ID"]) == self.GOLDEN_CLASS_IDS
        student_rows = enrollments[enrollments["Role"] == "student"]
        assert set(zip(student_rows["Class ID"], student_rows["User ID"])) == self.GOLDEN_STUDENT_ROWS

    def test_the_renamed_column_drives_the_filter_in_classes_and_enrollments(self, base_mapping, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _, classes, enrollments = _run_homeroom(base_mapping, renamed=True)
        assert sorted(classes["Class ID"]) == self.GOLDEN_CLASS_IDS  # no class for the inactive pupil
        student_rows = enrollments[enrollments["Role"] == "student"]
        assert set(zip(student_rows["Class ID"], student_rows["User ID"])) == self.GOLDEN_STUDENT_ROWS
        # ...and the district log names the rename that just started taking effect.
        assert _warnings(caplog, "'User ID' now reads source column 'pupil no'")

    def test_the_renamed_output_is_identical_to_the_default_output(self, base_mapping):
        _, classes_default, enrollments_default = _run_homeroom(base_mapping, renamed=False)
        columns.reset_run_notices()
        _, classes_renamed, enrollments_renamed = _run_homeroom(base_mapping, renamed=True)
        pd.testing.assert_frame_equal(classes_renamed, classes_default)
        pd.testing.assert_frame_equal(enrollments_renamed, enrollments_default)

    def test_twin_the_default_run_logs_no_rename(self, base_mapping, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _run_homeroom(base_mapping, renamed=False)
        assert not _warnings(caplog, "now reads source column")


class TestRenamedStudentsGradeAndHomeroomDriveTheHomeroomPath:
    """§5 #11 on the homeroom side: Classes' homeroom split + dedup and Enrollments'
    homeroom rows read the Students `Grade` and `Homeroom` keys. Before S9 both sites
    read the literals `"grade"`/`"homeroom"` (the Students mapping was on a dead path),
    so a demographic export spelling them differently built NO homeroom classes."""

    def test_the_renamed_columns_build_the_default_golden(self, base_mapping, caplog):
        _, classes_default, enrollments_default = _run_homeroom(base_mapping, renamed=False)
        columns.reset_run_notices()
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            students, classes, enrollments = _run_homeroom(base_mapping, renamed=False, renamed_grade_and_homeroom=True)
        assert set(students["User ID"]) == {"S001", "S002", "S005"}
        assert sorted(classes["Class ID"]) == TestRenamedStudentNumberDrivesTheHomeroomFilter.GOLDEN_CLASS_IDS
        pd.testing.assert_frame_equal(classes, classes_default)
        pd.testing.assert_frame_equal(enrollments, enrollments_default)
        # ...and the district log names each rename that just started taking effect.
        assert _warnings(caplog, "'Grade' now reads source column 'grade level'")
        assert _warnings(caplog, "'Homeroom' now reads source column 'hr code'")

    def test_twin_the_default_spelling_builds_the_golden_silently(self, base_mapping, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            _, classes, enrollments = _run_homeroom(base_mapping, renamed=False)
        assert sorted(classes["Class ID"]) == TestRenamedStudentNumberDrivesTheHomeroomFilter.GOLDEN_CLASS_IDS
        student_rows = enrollments[enrollments["Role"] == "student"]
        assert (
            set(zip(student_rows["Class ID"], student_rows["User ID"]))
            == TestRenamedStudentNumberDrivesTheHomeroomFilter.GOLDEN_STUDENT_ROWS
        )
        assert not _warnings(caplog, "now reads source column")


def _schedule(grade_col: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "student id": ["S003", "S004"],
            "school number": ["200", "200"],
            "school year": ["2025/2026", "2025/2026"],
            grade_col: ["10", "12"],
            "master timetable id": ["MT004", "MT005"],
            "teacher id": ["T003", "T004"],
            "section letter": ["A", "A"],
            "district course code": ["MAT10", "ENG12"],
            "primary teacher": ["Y", "Y"],
            "teacher name": ["Liu", "Singh"],
        }
    )


def _run_subject(base_mapping, *, grade_col: str):
    mappings = copy.deepcopy(base_mapping["mappings"])
    if grade_col != "grade":
        mappings["Classes"]["field_map"]["Grade"] = grade_col.title()
    demographic = pd.DataFrame(
        {
            "student number": ["S003", "S004"],
            "legal first name": ["E", "F"],
            "legal surname": ["E", "F"],
            "grade": ["10", "12"],
            "school number": ["200", "200"],
            "homeroom": ["C3", "C4"],
            "enrolment status": ["Active", "Active"],
        }
    )
    schedule = _schedule(grade_col)
    raw = {
        "StudentDemographicInformation.txt": demographic,
        "StudentSchedule.txt": schedule,
        "CourseInformation.txt": pd.DataFrame(
            {"school number": ["200", "200"], "course code": ["MAT10", "ENG12"], "title": ["Math 10", "English 12"]}
        ),
    }
    gc = dict(base_mapping["global_config"])
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    t.set_entity_mappings(mappings)
    t.transform(demographic, mappings["Students"], "Students", raw, gc)
    classes = t.transform(schedule, mappings["Classes"], "Classes", raw, gc)
    enrollments = t.transform(schedule, mappings["Enrollments"], "Enrollments", raw, gc)
    return classes, enrollments


class TestScheduleGradeIsTheClassesGradeKey:
    """§5 #11: the schedule split read a hardcoded `"grade"` in Classes, Enrollments and
    blended detection. It now reads the Classes mapping's `Grade` — the key that already
    named the column the subject class Grade came from — in all three."""

    def test_a_renamed_schedule_grade_builds_the_same_classes_and_enrollments(self, base_mapping):
        classes_default, enrollments_default = _run_subject(base_mapping, grade_col="grade")
        classes_renamed, enrollments_renamed = _run_subject(base_mapping, grade_col="year level")
        assert sorted(classes_default["Class ID"]) == ["MT004_2025", "MT005_2025"]
        assert list(classes_default["Grade"]) == ["10", "12"]
        pd.testing.assert_frame_equal(classes_renamed, classes_default)
        pd.testing.assert_frame_equal(enrollments_renamed, enrollments_default)

    def test_classes_and_enrollments_split_on_the_same_column(self, base_mapping):
        classes, enrollments = _run_subject(base_mapping, grade_col="year level")
        assert set(enrollments["Class ID"]) <= set(classes["Class ID"])  # zero orphans
        assert {"S003", "S004"} <= set(enrollments["User ID"])


def _blend_frames(day_col: str, *, grade_col: str = "grade"):
    class_info = pd.DataFrame(
        {
            "school number": ["300", "300", "300"],
            "teacher id": ["T010", "T010", "T010"],
            "master timetable id": ["MT100", "MT101", "MT102"],
            "course code": ["ENG01", "ENG02", "SCI03"],
            "term": ["1", "1", "1"],
            "semester": ["1", "1", "1"],
            day_col: ["1", "1", "2"],  # MT102 meets on another day — NOT the same slot
            "period": ["1", "1", "1"],
        }
    )
    schedule = pd.DataFrame(
        {
            "student id": ["S100", "S101", "S102"],
            "school number": ["300", "300", "300"],
            grade_col: ["1", "2", "3"],
            "master timetable id": ["MT100", "MT101", "MT102"],
            "teacher id": ["T010", "T010", "T010"],
            "teacher name": ["Adams", "Adams", "Adams"],
        }
    )
    course = pd.DataFrame(
        {"school number": ["300"] * 3, "course code": ["ENG01", "ENG02", "SCI03"], "title": ["E1", "E2", "S3"]}
    )
    return class_info, {"StudentSchedule.txt": schedule, "CourseInformation.txt": course}


def _detect(class_info, raw, *, source_columns=None, classes_grade=None):
    mapping = {
        "source_files": {"student_schedule": "StudentSchedule.txt", "course_info": "CourseInformation.txt"},
        "field_map": {"Name": {"teacher last name": "Teacher Name"}},
    }
    if classes_grade is not None:
        mapping["field_map"]["Grade"] = classes_grade
    if source_columns is not None:
        mapping["source_columns"] = source_columns
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    t._detect_blended_classes(class_info, mapping, raw, {"homeroom_grades": []})
    return dict(t.blended_class_map), dict(t.blended_class_metadata)


class TestBlendedTimeSlotIsConfigurable:
    """§5 #39: the time-slot components were the hardcoded `SESSION_TIME_COMPONENTS`; each
    is now a Classes `source_columns` role with the MyEd BC column as its default."""

    def test_default_columns_blend_only_the_shared_slot(self):
        class_map, metadata = _detect(*_blend_frames("day"))
        assert set(class_map) == {"MT100", "MT101"}
        (meta,) = metadata.values()
        assert "(Block 1 1 1 1)" in meta["Name"]

    def test_a_renamed_component_configured_as_a_role_blends_identically(self):
        class_map_default, metadata_default = _detect(*_blend_frames("day"))
        class_map, metadata = _detect(*_blend_frames("cycle day"), source_columns={"session_day": "Cycle Day"})
        assert class_map == class_map_default
        assert metadata == metadata_default

    def test_twin_an_unconfigured_rename_fails_closed_naming_the_default(self):
        """What the knob fixes: before S10 an absent component was dropped from the key, so
        MT102 (another day) joined the blend. S10 fails closed on it (§5 #39, the default
        named); S9's role makes it fixable."""
        with pytest.raises(SourceSchemaError) as exc:
            _detect(*_blend_frames("cycle day"))
        assert (exc.value.entity, exc.value.columns) == ("Classes", ("day",))


class TestBlendedDetectionReadsTheScheduleGradeKey:
    """§5 #11, the blended third of `schedule_grade_column`: detection's MODE map (which
    qualifies and NAMES a blend) and its enrollable map (the suppression gate, whose row
    set must equal the subject split's) both read the Classes `Grade` key — the column
    Classes' and Enrollments' subject splits read. Were it still the literal `"grade"`,
    a renamed schedule grade would empty the mode map and `validate` would silently
    reject every session: no blend at all."""

    def test_a_renamed_schedule_grade_configured_as_the_classes_grade_blends_identically(self):
        class_map_default, metadata_default = _detect(*_blend_frames("day"))
        assert set(class_map_default) == {"MT100", "MT101"}
        (meta,) = metadata_default.values()
        assert "(01/02)" in meta["Name"]  # the grade range comes from the MODE map
        class_map, metadata = _detect(*_blend_frames("day", grade_col="year level"), classes_grade="Year Level")
        assert class_map == class_map_default
        assert metadata == metadata_default

    def test_twin_an_unconfigured_rename_detects_no_blend(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.blended"):
            class_map, metadata = _detect(*_blend_frames("day", grade_col="year level"))
        assert class_map == {} and metadata == {}
        assert _warnings(caplog, "'grade' in student schedule")


def _coteacher_run(base_mapping, *, primary_col: str, source_columns=None, homeroom_col: str = "homeroom"):
    mappings = copy.deepcopy(base_mapping["mappings"])
    if source_columns is not None:
        mappings["Enrollments"]["source_columns"] = source_columns
    if homeroom_col != "homeroom":
        mappings["Students"]["field_map"]["Homeroom"] = {"column": homeroom_col.title()}
    demographic = pd.DataFrame(
        {
            "student number": ["S001"],
            "legal first name": ["A"],
            "legal surname": ["B"],
            "grade": ["3"],
            "school number": ["100"],
            homeroom_col: ["A1"],
            "enrolment status": ["Active"],
            "teacher name": ["Harper"],
            "teacher id": ["T001"],
        }
    )
    class_info = pd.DataFrame(
        {
            "school number": ["100"],
            "teacher id": ["T777"],  # a co-teacher the schedule never names
            "master timetable id": ["MT900"],
            primary_col: ["Y"],
            "section letter": ["A1"],  # matches homeroom A1
        }
    )
    raw = {"StudentDemographicInformation.txt": demographic, "ClassInformationEnh.txt": class_info}
    gc = {**base_mapping["global_config"], "blended_classes": False}
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    t.set_entity_mappings(mappings)
    t.transform(demographic, mappings["Students"], "Students", raw, gc)
    t.transform(pd.DataFrame(), mappings["Classes"], "Classes", raw, gc)
    return t.transform(pd.DataFrame(), mappings["Enrollments"], "Enrollments", raw, gc)


class TestCoteacherColumnsAreConfigurable:
    """§5 #11: the ClassInformation primary-teacher / section-letter columns were
    hardcoded; they are now Enrollments `source_columns` roles."""

    def test_default_columns_enrol_the_coteacher(self, base_mapping):
        enrollments = _coteacher_run(base_mapping, primary_col="primary teacher")
        assert ("100_A1_2025", "T777") in set(zip(enrollments["Class ID"], enrollments["User ID"]))

    def test_a_renamed_primary_flag_configured_as_a_role_enrols_identically(self, base_mapping):
        default = _coteacher_run(base_mapping, primary_col="primary teacher")
        renamed = _coteacher_run(
            base_mapping, primary_col="is primary", source_columns={"class_info_primary_teacher": "Is Primary"}
        )
        pd.testing.assert_frame_equal(renamed, default)

    def test_twin_an_unconfigured_rename_omits_the_coteacher_and_says_so(self, base_mapping, caplog):
        """The twin: a renamed flag the mapping does NOT name leaves the co-teacher row out —
        as before plan 0053 S10, but no longer in silence (§5 #15, owner ruling 2026-09-25):
        ONE warning names the default column it looked for (and the run records a note)."""
        with caplog.at_level(logging.WARNING):
            enrollments = _coteacher_run(base_mapping, primary_col="is primary")
        assert "T777" not in set(enrollments["User ID"])
        (line,) = _warnings(caplog, "CO-TEACHERS LEFT OUT")
        assert "['primary teacher']" in line

    def test_a_renamed_students_homeroom_attaches_the_coteacher_identically(self, base_mapping):
        """The co-teacher's homeroom lookup reads the Students `Homeroom` key too — the
        SAME column the homeroom classes were built on."""
        default = _coteacher_run(base_mapping, primary_col="primary teacher")
        renamed = _coteacher_run(base_mapping, primary_col="primary teacher", homeroom_col="hr code")
        assert ("100_A1_2025", "T777") in set(zip(renamed["Class ID"], renamed["User ID"]))
        pd.testing.assert_frame_equal(renamed, default)


class TestRunTransformOpensOneNoticeWindowPerRun:
    """`pipeline.run_transform` (both entry points) resets the window, so the transitional
    WARNING appears on EVERY run of a desktop session, not only the first."""

    def _run(self, base_mapping):
        mappings = copy.deepcopy(base_mapping["mappings"])
        mappings["Students"]["field_map"]["User ID"] = {"column": "Pupil No"}
        mappings["Students"]["field_map"]["Student Number"] = "Pupil No"
        gc = {**base_mapping["global_config"], "enabled_entities": ["Students", "Classes", "Enrollments"]}
        raw = {"StudentDemographicInformation.txt": _demographic("pupil no")}
        ledger = OutcomeLedger(configured_entity_order(mappings, gc))
        run_transform(raw, mappings, gc, ledger=ledger)

    def test_the_warning_is_logged_on_each_run(self, base_mapping, caplog):
        with caplog.at_level(logging.WARNING, logger=COLUMNS_LOGGER):
            self._run(base_mapping)
            assert len(_warnings(caplog, "'User ID' now reads source column 'pupil no'")) == 1
            self._run(base_mapping)
            assert len(_warnings(caplog, "'User ID' now reads source column 'pupil no'")) == 2


# ---------------------------------------------------------------------------
# 6. The undefaulted keywords stay undefaulted
# ---------------------------------------------------------------------------


def _kw_only_required(func, name: str) -> None:
    param = inspect.signature(func).parameters[name]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, f"{func.__qualname__}({name}) must be keyword-only"
    assert param.default is inspect.Parameter.empty, f"{func.__qualname__}({name}) must have no default"


class TestUndefaultedKeywords:
    """Each of these decides WHICH column is read or WHICH sections become one class, so
    a default would let a call site silently re-acquire the hardcoded spelling S9
    removed (CLAUDE.md: no permissive default on a safety-relevant parameter)."""

    @pytest.mark.parametrize(
        ("func", "name"),
        [
            (resolve_source_column, "previously"),
            (BlendedClassDetector._build_grade_map, "grade_col"),
            (BlendedClassDetector._build_enrollable_grade_map, "grade_col"),
            (BlendedClassDetector._add_session_key, "components"),
            (BlendedClassDetector._register_blends, "components"),
            (BlendedClassDetector.create_name, "session_components"),
            # plan 0053 S10 (owner ruling 2026-09-25) — which time-slot components are in force
            # decides which sections become ONE class; "all four" may never come by omission.
            (session_time_components, "roles"),
            (session_time_labels, "roles"),
            # plan 0053 S10 — which entity's scope a fail-closed guard is decided at, and
            # what it guards: a defaulted `entity` would mislabel a caller's failure (its
            # labels are kept only when `exc.entity` matches) and pass the §5 parity.
            (require_columns, "entity"),
            (require_columns, "guard"),
            (BaseTransformer.assign_class_ids, "entity"),
        ],
        ids=lambda v: v if isinstance(v, str) else v.__qualname__,
    )
    def test_keyword_only_with_no_default(self, func, name):
        _kw_only_required(func, name)

    def test_twin_the_check_rejects_a_default(self):
        def doctored(frame, *, grade_col="grade"):
            return frame

        with pytest.raises(AssertionError, match="must have no default"):
            _kw_only_required(doctored, "grade_col")
