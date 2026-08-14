"""`global_config.student_rostering_grades` — the opt-in STUDENT-rostering scope.

Plan 0042, slice 1b. **No shipped config sets this key**, so the 12-config
sweep is structurally blind to it and the "every config is byte-identical"
negative is vacuous by construction — the POSITIVE layer below carries the whole
proof (CLAUDE.md, no vacuous greens). Four layers:

1. **Unit** — :func:`~src.etl.transformers.grades.resolve_student_scope`, the
   INHERITED class bound it feeds
   (:func:`~src.etl.transformers.grades.resolve_timetable_scope` with
   ``class_rostering_grades`` absent), and
   :func:`~src.etl.transformers.grades.filter_to_grade_scope`'s
   derive-mask-drop contract including both of its fail-loud paths.
2. **The `StudentTransformer` boundary** — the double-conversion trap (a
   Kindergarten student must ship ``Grade == "KG"``, never ``"UG"``), the
   composition with the active-status filter, the ordering against the
   cross-enrollment collapse, and the RAISE on an unresolvable grade column.
3. **The cascade** — one key narrows Family and StudentCourses too, through the
   existing zero-orphan roster rather than any per-entity wiring.
4. **A differential over the frozen SD74 corpus** (shared runner:
   ``sd74_frozen_corpus`` in ``tests/conftest.py``, also used by slice 1a) — the
   same inputs run twice, once with a K-8 scope injected, asserting exact set
   deltas, ``Staff.csv`` identical, zero orphans everywhere, the KG pin on real
   data (that corpus has 9 raw ``"K"`` students), and the shape-4 BLEND pin: the
   suppression must fire from the RESOLVED scope even though
   ``class_rostering_grades`` is absent on this path.
"""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.etl.transformer import DataTransformer
from src.etl.transformers.grades import (
    filter_to_grade_scope,
    resolve_student_scope,
    resolve_timetable_scope,
)

#: A K-8 licensing district's scope, in CEDS output codes — shape 4 of the plan.
#: A SUPERSET of base `myedbc`'s twelve homeroom codes, which is what keeps it
#: expressible without restating `homeroom_grades` (see the forcing-function
#: cases in tests/test_config.py).
K8_SCOPE = ["IT", "PR", "PK", "TK", "KG", "01", "02", "03", "04", "05", "06", "07", "08"]


# ---------------------------------------------------------------------------
# 1. Unit — the student scope and the class bound it induces
# ---------------------------------------------------------------------------
class TestResolveStudentScope:
    def test_absent_key_yields_None(self):
        """None is 'no scope in force' — every grade, today's behaviour."""
        assert resolve_student_scope({}) is None

    def test_explicit_null_yields_None(self):
        assert resolve_student_scope({"student_rostering_grades": None}) is None

    def test_list_yields_the_set(self):
        assert resolve_student_scope({"student_rostering_grades": ["KG", "01"]}) == {"KG", "01"}

    def test_bare_string_raises_rather_than_being_read_as_a_grade_list(self):
        """`set("09")` would silently scope to {'0', '9'}; the sentinel spelling
        would silently scope to {'h','o','m','e','r'}. Fail loud on both."""
        with pytest.raises(ValueError, match="student_rostering_grades"):
            resolve_student_scope({"student_rostering_grades": "09"})

    def test_the_class_sentinel_spelling_is_rejected_here(self):
        with pytest.raises(ValueError, match="no 'homeroom' shortcut"):
            resolve_student_scope({"student_rostering_grades": "homeroom"})


class TestInheritedTimetableBound:
    """The class scope inherits the student list when only the student key is set."""

    HOMEROOM = ["KG", "01", "02", "03", "04", "05", "06", "07"]

    def test_student_set_and_class_absent_yields_student_minus_homeroom(self):
        scope = resolve_timetable_scope({"student_rostering_grades": K8_SCOPE}, self.HOMEROOM)
        assert scope == {"IT", "PR", "PK", "TK", "08"}

    def test_neither_key_still_yields_None(self):
        """The 1a path must be untouched — this is the byte-identical guarantee
        for every config that sets neither key."""
        assert resolve_timetable_scope({}, self.HOMEROOM) is None
        assert resolve_timetable_scope({"student_rostering_grades": None}, self.HOMEROOM) is None

    def test_class_key_WINS_when_both_are_set(self):
        """The chain guarantees class ⊆ student, so the narrower class list is
        the operative bound; the student list must not widen it back."""
        scope = resolve_timetable_scope(
            {"class_rostering_grades": ["07", "08"], "student_rostering_grades": K8_SCOPE},
            self.HOMEROOM,
        )
        assert scope == {"08"}

    def test_the_class_sentinel_still_wins_over_a_student_list(self):
        scope = resolve_timetable_scope(
            {"class_rostering_grades": "homeroom", "student_rostering_grades": K8_SCOPE},
            self.HOMEROOM,
        )
        assert scope == set()

    def test_student_equal_to_homeroom_yields_an_EMPTY_scope_not_None(self):
        """Legitimate (shape 1 expressed via the student key) — and it must be a
        SET, since an empty scope means 'no timetable classes' while None means
        'no scope at all'."""
        scope = resolve_timetable_scope({"student_rostering_grades": self.HOMEROOM}, self.HOMEROOM)
        assert scope == set()
        assert scope is not None


# ---------------------------------------------------------------------------
# 2. Unit — filter_to_grade_scope's derive-mask-drop contract
# ---------------------------------------------------------------------------
def _student_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "student number": ["S1", "S2", "S3", "S4"],
            "grade": ["K", "3", "10", ""],
            "name": ["A", "B", "C", "D"],
        }
    )


class TestFilterToGradeScope:
    def test_keeps_only_the_scope_in_CEDS_space(self):
        """Raw "K"/"3" are matched via their CEDS codes, not literally."""
        out = filter_to_grade_scope(_student_frame(), "grade", {"KG", "03"}, caller="Students")
        assert list(out["student number"]) == ["S1", "S2"]

    def test_a_blank_grade_is_UG_and_needs_UG_listed(self):
        """The documented residual: a blank/unrecognised grade leaves the
        delivery entirely unless the district lists "UG"."""
        assert "S4" not in list(
            filter_to_grade_scope(_student_frame(), "grade", {"KG"}, caller="Students")["student number"]
        )
        kept = filter_to_grade_scope(_student_frame(), "grade", {"UG"}, caller="Students")
        assert list(kept["student number"]) == ["S4"]

    def test_the_column_set_is_unchanged_and_the_temp_column_is_dropped(self):
        source = _student_frame()
        out = filter_to_grade_scope(source, "grade", {"KG"}, caller="Students")
        assert list(out.columns) == list(source.columns)

    def test_the_raw_grade_column_is_NEVER_rewritten(self):
        """THE highest-severity trap in this slice: the Students field_map
        converts this same column again, and `grade_to_ceds` is not idempotent —
        an in-place rewrite would ship Kindergarten as `Grade = "UG"`."""
        out = filter_to_grade_scope(_student_frame(), "grade", {"KG", "03"}, caller="Students")
        assert list(out["grade"]) == ["K", "3"]

    def test_the_caller_frame_is_untouched(self):
        source = _student_frame()
        before = source.copy()
        filter_to_grade_scope(source, "grade", {"KG"}, caller="Students")
        assert_frame_equal(source, before)

    def test_an_unresolvable_grade_column_RAISES_naming_the_available_columns(self):
        """Fail-open is the dangerous direction: this is an EXCLUSION key, so a
        renamed/absent column must never mean "keep everyone"."""
        frame = _student_frame().drop(columns="grade")
        with pytest.raises(KeyError) as exc:
            filter_to_grade_scope(frame, "grade", {"KG"}, caller="Students")
        message = str(exc.value)
        assert "grade" in message
        assert "student number" in message, "the message must name the available columns"
        assert "Students" in message, "the message must name the consumer"

    def test_a_frame_already_carrying_the_temp_column_RAISES(self):
        """Collision-proofing, pinned: the drop would otherwise delete a real
        source column. AC7's "column set unchanged" cannot catch this alone."""
        from src.etl.transformers.grades import _SCOPE_CEDS_COLUMN

        frame = _student_frame()
        frame[_SCOPE_CEDS_COLUMN] = "mine"
        with pytest.raises(ValueError, match="reserved column"):
            filter_to_grade_scope(frame, "grade", {"KG"}, caller="Students")

    def test_the_temp_column_name_cannot_collide_with_the_split_helpers(self):
        """`grade_ceds` is a real KEPT column of split_by_homeroom_grades — this
        filter must not borrow the name (it drops what it derives)."""
        from src.etl.transformers.grades import _SCOPE_CEDS_COLUMN

        assert _SCOPE_CEDS_COLUMN != "grade_ceds"


# ---------------------------------------------------------------------------
# 3. The StudentTransformer boundary
# ---------------------------------------------------------------------------
def _demographic(grades: list[str], statuses: list[str] | None = None) -> pd.DataFrame:
    size = len(grades)
    return pd.DataFrame(
        {
            "student number": [f"S{n:03d}" for n in range(1, size + 1)],
            "legal first name": ["First"] * size,
            "legal surname": ["Last"] * size,
            "date of birth": ["2010-01-15"] * size,
            "grade": grades,
            "school number": ["100"] * size,
            "homeroom": ["A1"] * size,
            "previous school number": [""] * size,
            "usual first name": [""] * size,
            "usual surname": [""] * size,
            "student email address": [""] * size,
            "enrolment status": statuses if statuses is not None else ["Active"] * size,
        }
    )


@pytest.fixture
def students_transformer():
    transformer = DataTransformer()
    transformer.set_school_year(2026, "08-25", "07-25")
    return transformer


def _run_students(transformer, mapping, global_config, demographic, **overrides):
    gc = {**global_config, **overrides}
    raw = {"StudentDemographicInformation.txt": demographic}
    return transformer.transform(demographic, mapping, "Students", raw, gc)


class TestStudentsUnderAScope:
    def test_only_the_listed_grades_survive(self, students_transformer, students_mapping, global_config):
        result = _run_students(
            students_transformer,
            students_mapping,
            global_config,
            _demographic(["K", "3", "9", "12"]),
            student_rostering_grades=["KG", "03"],
        )
        assert set(result["User ID"]) == {"S001", "S002"}

    def test_the_key_absent_keeps_every_grade(self, students_transformer, students_mapping, global_config):
        """The negative twin — byte-identical behaviour for every district that
        does not set the key."""
        result = _run_students(
            students_transformer, students_mapping, global_config, _demographic(["K", "3", "9", "12"])
        )
        assert set(result["User ID"]) == {"S001", "S002", "S003", "S004"}

    def test_a_KINDERGARTEN_student_ships_as_KG_not_UG(self, students_transformer, students_mapping, global_config):
        """The double-conversion pin, half one: with the key SET, an in-place
        CEDS rewrite would feed "KG" back through `grade_to_ceds` in the
        field_map and deliver `Grade = "UG"` for the whole cohort."""
        result = _run_students(
            students_transformer,
            students_mapping,
            global_config,
            _demographic(["K", "K", "3"]),
            student_rostering_grades=["KG", "03"],
        )
        assert sorted(result["Grade"]) == ["03", "KG", "KG"]

    def test_the_same_grades_with_the_key_ABSENT_are_unchanged(
        self, students_transformer, students_mapping, global_config
    ):
        """Half two, and it guards a DIFFERENT bug from half one: an
        unconditional rewrite that fires even when no scope is configured."""
        result = _run_students(students_transformer, students_mapping, global_config, _demographic(["K", "K", "3"]))
        assert sorted(result["Grade"]) == ["03", "KG", "KG"]

    def test_the_status_filter_and_the_grade_filter_COMPOSE(
        self, students_transformer, students_mapping, global_config
    ):
        """Neither replaces the other: an inactive in-scope student and an
        active out-of-scope student are BOTH dropped."""
        result = _run_students(
            students_transformer,
            students_mapping,
            global_config,
            _demographic(["K", "K", "12"], statuses=["Active", "Inactive", "Active"]),
            student_rostering_grades=["KG"],
        )
        assert set(result["User ID"]) == {"S001"}

    def test_an_unresolvable_grade_column_RAISES_instead_of_keeping_everyone(
        self, students_transformer, students_mapping, global_config
    ):
        """`{value: ""}` is legal config (SD83 does it for Date of Birth) and
        `resolve_column` does not honour a bare string, so "column absent" is
        reachable in ordinary config — and fail-open here delivers the PII of
        students the district is not licensed to send."""
        mapping = {**students_mapping, "field_map": {**students_mapping["field_map"], "Grade": {"value": ""}}}
        demographic = _demographic(["K", "12"]).drop(columns="grade")
        with pytest.raises(KeyError, match="grade"):
            _run_students(
                students_transformer,
                mapping,
                global_config,
                demographic,
                student_rostering_grades=["KG"],
            )

    def test_the_grade_column_is_resolved_from_the_field_map_not_hardcoded(
        self, students_transformer, students_mapping, global_config
    ):
        """Configurable Columns: a district that renames the source column keeps
        working, through the SAME `resolve_column` seam Classes/Enrollments use."""
        mapping = {
            **students_mapping,
            "field_map": {
                **students_mapping["field_map"],
                "Grade": {"column": "grade level", "transform": "grade_to_ceds"},
            },
        }
        demographic = _demographic(["K", "12"]).rename(columns={"grade": "grade level"})
        result = _run_students(
            students_transformer,
            mapping,
            global_config,
            demographic,
            student_rostering_grades=["KG"],
        )
        assert set(result["User ID"]) == {"S001"}
        assert list(result["Grade"]) == ["KG"]

    @pytest.mark.parametrize(
        ("home_grade", "other_grade", "expected"),
        [
            ("12", "K", set()),  # the HOME school says grade 12 → out of scope → dropped
            ("K", "12", {"S001"}),  # the HOME school says K → in scope → kept
        ],
    )
    def test_the_HOME_school_row_decides_inclusion(
        self, students_transformer, students_mapping, global_config, home_grade, other_grade, expected
    ):
        """Ordering pin: the filter runs AFTER the cross-enrollment collapse.

        Collapse leaves ONE row per ``User ID`` — the home-school one — so
        exactly one grade decision is made per student. The first row below is
        what discriminates: filtering FIRST would drop the home-school grade-12
        row, leave the other school's grade-K row for the collapse to keep, and
        deliver a student the district excluded.
        """
        demographic = _demographic([home_grade, other_grade])
        demographic["student number"] = ["S001", "S001"]
        demographic["school number"] = ["100", "200"]
        demographic["home school"] = ["100", "100"]
        result = _run_students(
            students_transformer,
            students_mapping,
            global_config,
            demographic,
            student_rostering_grades=["KG"],
            cross_enrollment={"collapse": True, "home_school_column": "home school"},
        )
        assert set(result["User ID"]) == expected

    def test_the_log_reports_counts_only(self, students_transformer, students_mapping, global_config, caplog):
        """Privacy: grade is student data, so the message names the CONFIGURED
        scope and the counts — never a student, never a per-student grade."""
        import logging

        with caplog.at_level(logging.INFO, logger="src.etl.transformers.students"):
            _run_students(
                students_transformer,
                students_mapping,
                global_config,
                _demographic(["K", "3", "12"]),
                student_rostering_grades=["KG"],
            )
        lines = [r.message for r in caplog.records if "student_rostering_grades" in r.message]
        assert lines and "kept 1/3" in lines[-1]
        assert "S001" not in lines[-1] and "S003" not in lines[-1]


# ---------------------------------------------------------------------------
# 4. The cascade — one key, the existing zero-orphan roster
# ---------------------------------------------------------------------------
class TestStudentCoursesFollowsTheRoster:
    """`StudentCourses` is the cascade consumer the SD74 corpus cannot reach
    (SD74 emits the 5 rostering entities only), so it gets its own run — a
    district adopting this key while running myBlueprint+ loses the excluded
    grades' transcripts BY DESIGN, and that has to be proven, not assumed."""

    SC_MAPPING = {
        "source_files": {
            "course_info": "CourseInformation.txt",
            "course_history": "StudentCourseHistory.txt",
            "course_selection": "StudentCourseSelection.txt",
        },
        "field_map": {
            key: {"value": ""}
            for key in (
                "Student ID",
                "Course Code",
                "IntegrationId",
                "Course Name",
                "Completion Date",
                "Final Mark",
                "Credits Earned",
                "Alternate Course Code",
                "Potential Credits Earned",
                "Term Grade",
            )
        },
    }

    @staticmethod
    def _course_frames():
        info = pd.DataFrame(
            {
                "course code": ["MAT10", "ENG12"],
                "school number": ["100", "100"],
                "title": ["Math 10", "English 12"],
                "credit value": [4, 4],
            }
        )
        history = pd.DataFrame(
            [
                {
                    "student number": student,
                    "school number": "100",
                    "course code": course,
                    "full course code": course,
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2025",
                    "dl completion date": "30-Jan-2026",
                }
                for student, course in (("S001", "MAT10"), ("S002", "ENG12"))
            ]
        )
        return info, history

    def _run_both(self, students_mapping, global_config, **overrides):
        transformer = DataTransformer()
        transformer.set_school_year(2026, "08-25", "07-25")
        info, history = self._course_frames()
        demographic = _demographic(["K", "12"])
        raw = {
            "StudentDemographicInformation.txt": demographic,
            "CourseInformation.txt": info,
            "StudentCourseHistory.txt": history,
            "StudentCourseSelection.txt": pd.DataFrame(),
        }
        gc = {**global_config, **overrides}
        students = transformer.transform(demographic, students_mapping, "Students", raw, gc)
        courses = transformer.transform(info, self.SC_MAPPING, "StudentCourses", raw, gc)
        return students, courses

    def test_without_the_key_both_students_keep_their_transcripts(self, students_mapping, global_config):
        """The positive twin — without it the row below could pass by emptiness."""
        students, courses = self._run_both(students_mapping, global_config)
        assert set(students["User ID"]) == {"S001", "S002"}
        assert set(courses["Student ID"]) == {"S001", "S002"}

    def test_the_excluded_grade_loses_its_transcript_rows(self, students_mapping, global_config):
        students, courses = self._run_both(students_mapping, global_config, student_rostering_grades=["KG"])
        assert set(students["User ID"]) == {"S001"}
        assert set(courses["Student ID"]) == {"S001"}


# ---------------------------------------------------------------------------
# 5. The SD74 differential — the positive layer that carries the whole proof
# ---------------------------------------------------------------------------
def _class_ids(frame: pd.DataFrame) -> set[str]:
    return set() if frame.empty else set(frame["Class ID"].astype(str))


def _blended_ids(ids: set[str]) -> set[str]:
    return {cid for cid in ids if cid.startswith("BLENDED_")}


@pytest.fixture(scope="module")
def sd74_off_and_on(sd74_frozen_corpus):
    """The frozen SD74 corpus, as shipped and under a K-8 student scope.

    Shares the corpus runner with slice 1a's differential
    (``tests/conftest.py::sd74_frozen_corpus``) — one runner, two injections.
    ``class_rostering_grades`` is deliberately NOT set here: this run IS the
    inherited-bound path (shape 4).
    """
    global_config, run = sd74_frozen_corpus
    return global_config, run(), run(student_rostering_grades=K8_SCOPE)


class TestSD74StudentScopeDifferential:
    IN_SCOPE = set(K8_SCOPE)

    def test_the_shipped_run_really_carries_the_grades_this_scope_removes(self, sd74_off_and_on):
        """Asserted first, so no row below can pass by emptiness: the OFF run
        must actually have 9-12 students to lose."""
        _gc, off, _on = sd74_off_and_on
        off_grades = set(off.outputs["Students"]["Grade"])
        assert {"09", "10", "11", "12"} <= off_grades
        assert not off.outputs["Enrollments"].empty

    def test_students_are_EXACTLY_the_in_scope_grades(self, sd74_off_and_on):
        _gc, off, on = sd74_off_and_on
        expected = set(off.outputs["Students"][off.outputs["Students"]["Grade"].isin(self.IN_SCOPE)]["User ID"])
        assert expected
        assert set(on.outputs["Students"]["User ID"]) == expected
        assert set(on.outputs["Students"]["Grade"]) <= self.IN_SCOPE

    def test_the_KINDERGARTEN_cohort_survives_as_KG_not_UG(self, sd74_off_and_on):
        """The double-conversion pin on real data: this corpus's raw demographic
        grades are "K"/"1"…"12", so an in-place rewrite would corrupt a ninth of
        it — 9 students shipped as `Grade = "UG"`."""
        _gc, off, on = sd74_off_and_on
        kg_off = int((off.outputs["Students"]["Grade"] == "KG").sum())
        assert kg_off == 9, "the corpus's Kindergarten cohort is the oracle here"
        assert int((on.outputs["Students"]["Grade"] == "KG").sum()) == kg_off
        assert "UG" not in set(on.outputs["Students"]["Grade"])

    def test_STAFF_is_byte_identical(self, sd74_off_and_on):
        """The non-goal, proven positively: staff are never grade-filtered, so a
        teacher of an excluded grade still ships."""
        _gc, off, on = sd74_off_and_on
        assert_frame_equal(off.outputs["Staff"], on.outputs["Staff"])

    def test_FAMILY_shrinks_to_the_surviving_students_guardians(self, sd74_off_and_on):
        _gc, off, on = sd74_off_and_on
        roster = set(on.outputs["Students"]["User ID"])
        assert len(on.outputs["Family"]) < len(off.outputs["Family"])
        assert not on.outputs["Family"].empty
        assert set(on.outputs["Family"]["Student User ID"]) <= roster

    def test_classes_and_enrollments_shrink_and_stay_a_strict_subset(self, sd74_off_and_on):
        _gc, off, on = sd74_off_and_on
        assert _class_ids(on.outputs["Classes"]) < _class_ids(off.outputs["Classes"])
        assert _class_ids(on.outputs["Enrollments"]) <= _class_ids(on.outputs["Classes"]), "orphan Class IDs"
        assert not on.outputs["Enrollments"].empty

    def test_no_class_survives_for_an_excluded_grade(self, sd74_off_and_on):
        """The inherited bound end to end: with `class_rostering_grades` ABSENT,
        the timetable scope becomes `student − homeroom`, so grade 9-12 subject
        classes disappear while grade 08's remain."""
        _gc, off, on = sd74_off_and_on
        graded_off = set(off.outputs["Classes"]["Grade"].dropna())
        assert {"08", "09", "12"} <= graded_off
        assert "08" in set(on.outputs["Classes"]["Grade"].dropna())
        assert not {"09", "10", "11", "12"} & set(on.outputs["Classes"]["Grade"].dropna())

    def test_ZERO_orphans_across_every_entity(self, sd74_off_and_on):
        """The zero-orphan invariant under the narrowed roster: no surviving row
        anywhere may reference a student who is no longer delivered."""
        _gc, _off, on = sd74_off_and_on
        roster = set(on.outputs["Students"]["User ID"].astype(str))
        assert set(on.outputs["Family"]["Student User ID"].astype(str)) <= roster
        enrollments = on.outputs["Enrollments"]
        students_rows = enrollments[enrollments["Role"] == "student"]
        assert not students_rows.empty
        assert set(students_rows["User ID"].astype(str)) <= roster

    def test_a_blend_outside_the_INHERITED_scope_is_absent_from_BOTH_files(self, sd74_off_and_on):
        """The shape-4 blend pin (Stage-3 re-review, RC1). Nothing else pins
        that suppression fires when only the STUDENT key is set: the gate reads
        the resolved scope, and a gate re-keyed to `class_rostering_grades`'s
        presence would be silently dead here — leaving a `BLENDED_` class with a
        teacher and zero students for grades the district is not licensed for.
        Asserted on BOTH files together, since `enrolled ⊆ classes` alone is
        satisfiable by emptiness."""
        _gc, off, on = sd74_off_and_on
        removed = _blended_ids(_class_ids(off.outputs["Classes"])) - _blended_ids(_class_ids(on.outputs["Classes"]))
        assert removed, "the corpus must lose at least one blend, or this pins nothing"
        assert removed & _class_ids(on.outputs["Classes"]) == set()
        assert removed & _class_ids(on.outputs["Enrollments"]) == set()

    def test_an_IN_scope_blend_survives_and_is_not_studentless(self, sd74_off_and_on):
        """The other half of the pin: suppression must be selective, and a
        surviving blend must still carry students — a teacher-only blended class
        is the exact shape this plan exists to prevent."""
        _gc, _off, on = sd74_off_and_on
        surviving = _blended_ids(_class_ids(on.outputs["Classes"]))
        assert surviving, "no blend survived — the suppression is over-firing"
        enrollments = on.outputs["Enrollments"]
        for blend_id in surviving:
            rows = enrollments[enrollments["Class ID"] == blend_id]
            assert set(rows["Role"]) >= {"student", "teacher"}, blend_id
