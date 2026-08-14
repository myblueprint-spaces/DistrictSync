"""`global_config.class_rostering_grades` — the opt-in CLASS-rostering scope.

Plan 0042, slice 1a. Three layers:

1. **Unit** — the scope resolver and the scoped side of
   :func:`~src.etl.transformers.grades.split_by_homeroom_grades`, including the
   ``keep="homeroom"`` guard that must RAISE rather than ignore the argument.
2. **The three district shapes end to end**, over ONE synthetic corpus run under
   three configs — the semantics ARE the feature, so each shape is exercised
   rather than described. Shape 3 includes the mode-masking fork (a section whose
   MODE grade is out of scope while it carries minority in-scope students).
3. **A differential over the frozen SD74 corpus** — the positive twin for
   "the other 11 configs are byte-identical". It runs the same inputs twice, once
   with the sentinel injected, and asserts exact set deltas. Differential by
   construction, so it survives plan 0043's regeneration of the golden.
"""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.etl.transformer import DataTransformer
from src.etl.transformers.grades import resolve_timetable_scope, split_by_homeroom_grades


# ---------------------------------------------------------------------------
# 1. Unit — the scope resolver
# ---------------------------------------------------------------------------
class TestResolveTimetableScope:
    def test_absent_key_yields_None(self):
        """None is 'no scope in force' — today's unbounded complement."""
        assert resolve_timetable_scope({}, ["KG", "01"]) is None

    def test_explicit_null_yields_None(self):
        assert resolve_timetable_scope({"class_rostering_grades": None}, ["KG"]) is None

    def test_sentinel_yields_an_EMPTY_scope(self):
        """Empty is meaningful (roster no timetable classes) and must never be
        confused with None — hence the `is None` branch everywhere."""
        scope = resolve_timetable_scope({"class_rostering_grades": "homeroom"}, ["KG", "01"])
        assert scope == set()
        assert scope is not None

    def test_list_yields_the_rostered_set_minus_homeroom(self):
        scope = resolve_timetable_scope(
            {"class_rostering_grades": ["07", "08", "09", "10", "11", "12"]},
            ["07", "08", "09"],
        )
        assert scope == {"10", "11", "12"}

    def test_list_with_no_homeroom_grades_yields_the_whole_list(self):
        scope = resolve_timetable_scope({"class_rostering_grades": ["10", "11", "12"]}, [])
        assert scope == {"10", "11", "12"}

    def test_unrecognised_string_raises_rather_than_being_read_as_a_grade_list(self):
        """`set("09")` would silently scope to {'0', '9'} — fail loud instead.

        The config boundary already rejects this; the resolver also serves
        raw-dict callers (the UI adapter, tests), so it validates too.
        """
        with pytest.raises(ValueError, match="class_rostering_grades"):
            resolve_timetable_scope({"class_rostering_grades": "09"}, ["KG"])


# ---------------------------------------------------------------------------
# 1b. Unit — split_by_homeroom_grades under a scope
# ---------------------------------------------------------------------------
def _grade_frame(grades: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"grade": grades, "row": list(range(len(grades)))})


class TestSplitByHomeroomGradesScope:
    HOMEROOM = ["KG", "01", "02", "03"]

    def test_subject_side_without_a_scope_is_the_complement(self):
        """The positive twin for every scoped row below: unchanged behaviour."""
        out = split_by_homeroom_grades(_grade_frame(["K", "3", "10", "12"]), "grade", self.HOMEROOM, keep="subject")
        assert list(out["grade_ceds"]) == ["10", "12"]

    def test_subject_side_with_a_scope_keeps_only_the_scope(self):
        out = split_by_homeroom_grades(
            _grade_frame(["K", "3", "10", "12"]),
            "grade",
            self.HOMEROOM,
            keep="subject",
            timetable_scope={"10"},
        )
        assert list(out["grade_ceds"]) == ["10"]

    def test_empty_scope_keeps_nothing(self):
        out = split_by_homeroom_grades(
            _grade_frame(["K", "3", "10", "12"]),
            "grade",
            self.HOMEROOM,
            keep="subject",
            timetable_scope=set(),
        )
        assert out.empty

    def test_subject_side_preserves_the_raw_grade_column(self):
        """The Classes Grade output re-derives from the raw column, so the
        scoped path must not rewrite it (grade_to_ceds is NOT idempotent)."""
        out = split_by_homeroom_grades(
            _grade_frame(["K", "10"]), "grade", self.HOMEROOM, keep="subject", timetable_scope={"10"}
        )
        assert list(out["grade"]) == ["10"]

    def test_homeroom_side_with_a_scope_RAISES(self):
        """Validator 5 — a filter argument accepted and silently dropped is
        exactly the permissive-default the engineering rules ban."""
        with pytest.raises(ValueError, match="does not accept a timetable_scope"):
            split_by_homeroom_grades(
                _grade_frame(["K", "10"]), "grade", self.HOMEROOM, keep="homeroom", timetable_scope={"10"}
            )

    def test_homeroom_side_without_a_scope_still_converts_and_keeps(self):
        """The positive twin of the guard AND the CEDS-idempotency pin: the
        raw K/1/3 cohort must all land in the homeroom half, converted once."""
        out = split_by_homeroom_grades(_grade_frame(["K", "1", "3", "10"]), "grade", self.HOMEROOM, keep="homeroom")
        assert list(out["grade"]) == ["KG", "01", "03"]


# ---------------------------------------------------------------------------
# 2. The three district shapes, end to end over one corpus
# ---------------------------------------------------------------------------
#
# School 300, one term/semester. Sections pair off into same-teacher/same-slot
# blends so every row of the plan's blend-rule table is exercised:
#
#   MTA1/MTA2  T010 period 1   grades 07 / 08   -> blend "A"
#   MTB1/MTB2  T011 period 2   grades 09 / 10   -> blend "B" (straddles)
#   MTC1/MTC2  T012 period 3   grades 10 / 11   -> blend "C"
#   MTD1/MTD2  T013 period 4   grades 06(mode)+10 / 05  -> blend "D" (mode-masked)
#   MTE        T014 period 5   grade 12         -> standalone
#   MTF        T015 period 6   grade 03         -> standalone
_SESSIONS = [
    ("MTA1", "T010", "1", "07", "ENG07"),
    ("MTA2", "T010", "1", "08", "ENG08"),
    ("MTB1", "T011", "2", "09", "SCI09"),
    ("MTB2", "T011", "2", "10", "SCI10"),
    ("MTC1", "T012", "3", "10", "MAT10"),
    ("MTC2", "T012", "3", "11", "MAT11"),
    ("MTD1", "T013", "4", "06", "ART06"),
    ("MTD2", "T013", "4", "05", "ART05"),
    ("MTE", "T014", "5", "12", "ENG12"),
    ("MTF", "T015", "6", "03", "HR-3"),
]

#: The minority in-scope pupil inside MTD1, whose MODE grade is 06. Named so the
#: mode-masking assertions read as the fork they are.
_MODE_MASKED_STUDENT = "S_MTD1_MINORITY"


def _corpus_schedule() -> pd.DataFrame:
    rows = []
    for mt_id, teacher, period, grade, course in _SESSIONS:
        # MTD1 carries THREE grade-06 pupils (so 06 is the mode) plus ONE grade-10.
        pupils = [(f"S_{mt_id}_{n}", grade) for n in range(3 if mt_id == "MTD1" else 1)]
        if mt_id == "MTD1":
            pupils.append((_MODE_MASKED_STUDENT, "10"))
        for student, pupil_grade in pupils:
            rows.append(
                {
                    "student number": student,
                    "student id": student,
                    "school number": "300",
                    "school year": "2025/2026",
                    "grade": pupil_grade,
                    "master timetable id": mt_id,
                    "teacher id": teacher,
                    "section letter": "A",
                    "district course code": course,
                    "primary teacher": "Y",
                    "teacher name": f"Teach{teacher[-1]}",
                    "period": period,
                }
            )
    return pd.DataFrame(rows)


def _corpus_class_info() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "school number": "300",
                "teacher id": teacher,
                "master timetable id": mt_id,
                "course code": course,
                "term": "1",
                "semester": "1",
                "day": "1",
                "period": period,
            }
            for mt_id, teacher, period, _grade, course in _SESSIONS
        ]
    )


def _corpus_demographic() -> pd.DataFrame:
    """One demographic row per scheduled pupil, homeroom keyed to their grade."""
    schedule = _corpus_schedule().drop_duplicates(subset=["student number"])
    return pd.DataFrame(
        {
            "student number": schedule["student number"].tolist(),
            "legal first name": ["First"] * len(schedule),
            "legal surname": ["Last"] * len(schedule),
            "date of birth": ["2010-01-15"] * len(schedule),
            "grade": schedule["grade"].tolist(),
            "school number": ["300"] * len(schedule),
            "homeroom": [f"HR{g}" for g in schedule["grade"]],
            "previous school number": [""] * len(schedule),
            "usual first name": [""] * len(schedule),
            "usual surname": [""] * len(schedule),
            "student email address": [""] * len(schedule),
            "enrolment status": ["Active"] * len(schedule),
            "teacher name": schedule["teacher name"].tolist(),
            "teacher id": schedule["teacher id"].tolist(),
        }
    )


def _corpus_course_info() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "school number": ["300"] * len(_SESSIONS),
            "course code": [course for *_rest, course in _SESSIONS],
            "title": [f"Course {course}" for *_rest, course in _SESSIONS],
        }
    )


def _corpus_staff() -> pd.DataFrame:
    teachers = sorted({teacher for _mt, teacher, *_rest in _SESSIONS})
    return pd.DataFrame(
        {
            "teacher id": teachers,
            "first name": ["T"] * len(teachers),
            "last name": [f"Teach{t[-1]}" for t in teachers],
            "email address": [f"{t.lower()}@school.ca" for t in teachers],
            "teaching staff": ["Y"] * len(teachers),
            "school number": ["300"] * len(teachers),
        }
    )


@pytest.fixture
def corpus_raw_data() -> dict[str, pd.DataFrame]:
    return {
        "StudentDemographicInformation.txt": _corpus_demographic(),
        "StudentSchedule.txt": _corpus_schedule(),
        "StaffInformationEnhanced.txt": _corpus_staff(),
        "CourseInformation.txt": _corpus_course_info(),
        "EmergencyContactInformation.txt": pd.DataFrame(),
        "ClassInformationEnh.txt": _corpus_class_info(),
    }


def _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **overrides):
    """Run Classes then Enrollments over the corpus with `overrides` applied."""
    transformer = DataTransformer()
    transformer.set_school_year(2026, "08-25", "07-25")
    gc = {**global_config, **overrides}
    classes = transformer.transform(
        corpus_raw_data["StudentSchedule.txt"], classes_mapping, "Classes", corpus_raw_data, gc
    )
    enrollments = transformer.transform(
        corpus_raw_data["StudentSchedule.txt"], enrollments_mapping, "Enrollments", corpus_raw_data, gc
    )
    return classes, enrollments


def _class_ids(frame: pd.DataFrame) -> set[str]:
    return set() if frame.empty else set(frame["Class ID"].astype(str))


def _blended_ids(ids: set[str]) -> set[str]:
    return {cid for cid in ids if cid.startswith("BLENDED_")}


def _subject_ids(ids: set[str]) -> set[str]:
    return {cid for cid in ids if cid.startswith("MT")}


class TestShapeDefaultUnchanged:
    """The gate itself: with the key absent NOTHING changes, blends included."""

    def test_every_blend_and_every_subject_class_is_present(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        classes, enrollments = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config)
        ids = _class_ids(classes)
        assert len(_blended_ids(ids)) == 4, "all four same-slot blends must survive with no scope in force"
        # Base homeroom_grades is KG-07, so MTA1 (07) and MTF (03) are homeroom-side
        # and every other section is absorbed into one of the four blends —
        # leaving the standalone grade-12 section as the one plain subject class.
        assert _subject_ids(ids) == {"MTE_2026"}, sorted(ids)
        assert _class_ids(enrollments) <= ids, "zero-orphan"

    def test_an_ALL_HOMEROOM_blend_is_still_registered(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        """Blend "D" (05/06) is entirely inside the base homeroom grades, so it
        is exactly the studentless blend plan 0043 will remove. Until then it
        MUST still be produced — this is the only proof the suppression rule
        does not fire on the six live districts, and 0043 deletes the golden
        that currently implies it.
        """
        classes, _ = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config)
        blends = _blended_ids(_class_ids(classes))
        assert any("T013" in cid for cid in blends), f"the all-homeroom 05/06 blend vanished: {sorted(blends)}"


class TestShape1SentinelHomeroomOnly:
    """SD83's shape: roster exactly the homeroom grades."""

    OVERRIDES = {"class_rostering_grades": "homeroom"}

    def test_no_subject_and_no_blended_classes_remain(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        classes, _ = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES)
        ids = _class_ids(classes)
        assert _blended_ids(ids) == set()
        assert _subject_ids(ids) == set()
        assert ids, "the homeroom classes themselves must survive"

    def test_homeroom_classes_for_every_homeroom_grade_survive(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        classes, _ = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES)
        # Base homeroom_grades = IT..07; the corpus holds grades 03, 05, 06, 07.
        assert set(classes["Grade"]) == {"03", "05", "06", "07"}

    def test_suppressed_blends_are_absent_from_Classes_AND_Enrollments_together(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        """Two-sided: `enrolled ⊆ classes` alone is satisfiable by emptiness, so
        the pairing is asserted with a non-empty Enrollments frame."""
        classes, enrollments = _run_shape(
            corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES
        )
        assert not enrollments.empty, "homeroom enrollments must still exist"
        assert _blended_ids(_class_ids(classes)) == set()
        assert _blended_ids(_class_ids(enrollments)) == set()
        assert _class_ids(enrollments) <= _class_ids(classes), "orphan Class IDs in Enrollments"


class TestShape2NoHomeroomsSeniorTimetableOnly:
    """`homeroom_grades: []` + `class_rostering_grades: ["10","11","12"]`."""

    OVERRIDES = {"homeroom_grades": [], "class_rostering_grades": ["10", "11", "12"]}

    def test_no_homeroom_classes_and_only_senior_subject_classes(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        classes, _ = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES)
        ids = _class_ids(classes)
        assert not any(cid.startswith("300_HR") for cid in ids), "a homeroom class was created for an empty list"
        # MTB2 / MTC1 / MTC2 are absorbed into the two surviving blends, so the
        # PLAIN subject classes are the suppressed-blend fallback (MTD1, kept for
        # its grade-10 pupil) plus the standalone grade-12 section.
        assert _subject_ids(ids) == {"MTD1_2026", "MTE_2026"}, sorted(ids)
        # Nothing at all for the wholly unrostered sections.
        assert not {"MTA1_2026", "MTA2_2026", "MTD2_2026", "MTF_2026"} & ids

    def test_a_ten_eleven_blend_survives(self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config):
        classes, _ = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES)
        blends = _blended_ids(_class_ids(classes))
        assert any("T012" in cid for cid in blends), f"the 10/11 blend was suppressed: {sorted(blends)}"
        assert not any("T010" in cid for cid in blends), "the 07/08 blend survived an unrostered range"


class TestShape3SplitHomeroomAndTimetable:
    """homeroom 07-09, class rostering 07-12 — K-6 excluded entirely."""

    OVERRIDES = {
        "homeroom_grades": ["07", "08", "09"],
        "class_rostering_grades": ["07", "08", "09", "10", "11", "12"],
    }

    @pytest.fixture
    def shape3(self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config):
        return _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config, **self.OVERRIDES)

    def test_homerooms_only_for_seven_to_nine(self, shape3):
        classes, _ = shape3
        homeroom_grades = set(classes[classes["Class ID"].str.startswith("300_HR")]["Grade"])
        assert homeroom_grades == {"07", "08", "09"}

    def test_subject_classes_only_for_ten_to_twelve(self, shape3):
        classes, _ = shape3
        subject = classes[classes["Class ID"].str.startswith("MT")]
        # MTB2 (10), MTC1 (10) and MTC2 (11) are absorbed into the surviving
        # blends, so the plain subject classes are MTD1 (the suppressed-blend
        # fallback, grade 10) and MTE (12) — all inside 10-12, none below.
        assert set(subject["Class ID"]) == {"MTD1_2026", "MTE_2026"}
        assert set(subject["Grade"]) == {"10", "12"}
        assert set(subject["Grade"]) <= {"10", "11", "12"}

    def test_the_grade_eleven_section_is_rostered_inside_its_blend(self, shape3):
        """Grade 11 is in scope but has no plain class — it rides blend C, whose
        grade range names it. Pinned so the row above cannot be misread as
        "grade 11 was dropped"."""
        classes, enrollments = shape3
        blend_c = classes[classes["Class ID"].str.contains("T012")]
        assert len(blend_c) == 1
        blend_id = blend_c["Class ID"].iloc[0]
        assert set(enrollments[enrollments["User ID"] == "S_MTC2_0"]["Class ID"]) == {blend_id}

    def test_grades_below_seven_appear_in_neither_half(self, shape3):
        classes, _ = shape3
        assert not set(classes["Grade"]) & {"03", "05", "06"}
        assert "MTF_2026" not in _class_ids(classes)

    def test_the_straddling_nine_ten_blend_survives_carrying_its_grade_ten_students(self, shape3):
        classes, enrollments = shape3
        blends = _blended_ids(_class_ids(classes))
        survivor = {cid for cid in blends if "T011" in cid}
        assert survivor, f"the 09/10 blend was suppressed: {sorted(blends)}"
        blend_id = survivor.pop()
        enrolled = set(enrollments[enrollments["Class ID"] == blend_id]["User ID"])
        assert "S_MTB2_0" in enrolled, "the grade-10 pupil lost their blended enrollment"

    def test_the_wholly_unrostered_blends_are_suppressed(self, shape3):
        classes, _ = shape3
        blends = _blended_ids(_class_ids(classes))
        assert not any("T010" in cid for cid in blends), "the 07/08 blend (both homeroom) survived"
        assert not any("T013" in cid for cid in blends), "the 05/06 blend (unrostered) survived"

    def test_mode_masked_section_falls_back_to_its_plain_class_with_no_orphan(self, shape3):
        """The R3(i) fork, named and pinned: MTD1's MODE grade is 06 (out of
        scope) so its blend is suppressed — but its minority grade-10 pupil is
        NOT orphaned. They land in the plain per-section class instead, which
        both Classes and Enrollments resolve identically because suppression
        happens BEFORE the `class_map` write.
        """
        classes, enrollments = shape3
        assert "MTD1_2026" in _class_ids(classes), "the mode-masked section lost its plain class"
        minority = enrollments[enrollments["User ID"] == _MODE_MASKED_STUDENT]
        assert set(minority["Class ID"]) == {"MTD1_2026"}
        assert _class_ids(enrollments) <= _class_ids(classes), "orphan Class IDs in Enrollments"


class TestCoTeacherPathsUnderTheSentinel:
    """ClassInformation co-teacher rows: path 1 survives, path 2 goes with the blend.

    The 12-config contract sweep is structurally blind here — SD83's fixture
    writes an EMPTY ClassInformation, so neither path runs at all in it.
    """

    #: The co-teacher who appears only in ClassInformation, never in the schedule.
    CO_TEACHER = "T099"

    @pytest.fixture
    def coteacher_raw_data(self, corpus_raw_data):
        class_info = corpus_raw_data["ClassInformationEnh.txt"].copy()
        # Only the grade-07/08 pair carries a primary-teacher flag. Its section
        # letter matches the grade-07 homeroom code, so path 1 can resolve it.
        class_info["primary teacher"] = [
            "Y" if mt in ("MTA1", "MTA2") else "" for mt in class_info["master timetable id"]
        ]
        class_info["section letter"] = ["HR07" if mt == "MTA1" else "" for mt in class_info["master timetable id"]]
        class_info.loc[class_info["master timetable id"].isin(["MTA1", "MTA2"]), "teacher id"] = self.CO_TEACHER
        corpus_raw_data["ClassInformationEnh.txt"] = class_info
        return corpus_raw_data

    def _co_teacher_rows(self, enrollments: pd.DataFrame) -> pd.DataFrame:
        return enrollments[enrollments["User ID"] == self.CO_TEACHER]

    def test_path_2_emits_a_blended_co_teacher_row_with_no_scope_in_force(
        self, coteacher_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        """The positive twin — without it the suppressed-path assertion below
        would pass against a corpus where path 2 never fired at all."""
        _classes, enrollments = _run_shape(coteacher_raw_data, classes_mapping, enrollments_mapping, global_config)
        rows = self._co_teacher_rows(enrollments)
        assert _blended_ids(set(rows["Class ID"])), sorted(set(rows["Class ID"]))

    def test_path_1_still_emits_the_homeroom_teacher_row_under_the_sentinel(
        self, coteacher_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        _classes, enrollments = _run_shape(
            coteacher_raw_data,
            classes_mapping,
            enrollments_mapping,
            global_config,
            class_rostering_grades="homeroom",
        )
        assert "300_HR07_2026" in set(self._co_teacher_rows(enrollments)["Class ID"])

    def test_path_2_emits_NOTHING_for_a_suppressed_blend(
        self, coteacher_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        classes, enrollments = _run_shape(
            coteacher_raw_data,
            classes_mapping,
            enrollments_mapping,
            global_config,
            class_rostering_grades="homeroom",
        )
        assert _blended_ids(_class_ids(enrollments)) == set()
        assert _class_ids(enrollments) <= _class_ids(classes), "orphan Class IDs in Enrollments"


# ---------------------------------------------------------------------------
# 3. The SD74 differential — the positive twin for "11 configs unchanged"
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sd74_off_and_on(sd74_frozen_corpus):
    """Run the frozen SD74 corpus twice: as shipped, and with the sentinel.

    The corpus runner itself is the shared ``sd74_frozen_corpus`` fixture
    (``tests/conftest.py``) — slice 1b differentials the same corpus against
    ``student_rostering_grades``, and one runner is what keeps the two honest.
    """
    global_config, run = sd74_frozen_corpus
    return global_config, run(), run(class_rostering_grades="homeroom")


class TestSD74SentinelDifferential:
    def test_the_shipped_run_really_produces_subject_and_blended_classes(self, sd74_off_and_on):
        """The differential is only meaningful if the OFF run has something to
        lose — asserted first so no later row can pass by emptiness."""
        _gc, off, _on = sd74_off_and_on
        ids = _class_ids(off.outputs["Classes"])
        assert _blended_ids(ids), "the frozen corpus produced no blends"
        assert len(ids - _blended_ids(ids)) > 1

    def test_surviving_classes_are_EXACTLY_the_homeroom_grade_classes(self, sd74_off_and_on):
        global_config, off, on = sd74_off_and_on
        homeroom_grades = set(global_config["homeroom_grades"])
        off_classes = off.outputs["Classes"]
        expected = {
            str(class_id)
            for class_id, grade in zip(off_classes["Class ID"], off_classes["Grade"])
            if not str(class_id).startswith("BLENDED_") and grade in homeroom_grades
        }
        assert expected, "the oracle itself is empty — the corpus has no homeroom classes"
        assert _class_ids(on.outputs["Classes"]) == expected

    def test_the_scoped_run_is_a_strict_subset_of_the_shipped_run(self, sd74_off_and_on):
        _gc, off, on = sd74_off_and_on
        assert _class_ids(on.outputs["Classes"]) < _class_ids(off.outputs["Classes"])

    def test_no_blended_class_survives(self, sd74_off_and_on):
        _gc, _off, on = sd74_off_and_on
        assert _blended_ids(_class_ids(on.outputs["Classes"])) == set()

    def test_every_removed_class_is_removed_from_Enrollments_too(self, sd74_off_and_on):
        _gc, off, on = sd74_off_and_on
        removed = _class_ids(off.outputs["Classes"]) - _class_ids(on.outputs["Classes"])
        assert removed
        on_enrollments = on.outputs["Enrollments"]
        assert not on_enrollments.empty
        assert _class_ids(on_enrollments) & removed == set()
        assert _class_ids(on_enrollments) <= _class_ids(on.outputs["Classes"]), "orphan Class IDs"

    @pytest.mark.parametrize("entity", ["Students", "Staff", "Family"])
    def test_the_non_class_entities_are_identical(self, sd74_off_and_on, entity):
        """The non-goal, proven positively: this key scopes CLASSES only."""
        _gc, off, on = sd74_off_and_on
        assert_frame_equal(off.outputs[entity], on.outputs[entity])
