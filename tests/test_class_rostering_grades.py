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
from src.etl.transformers.grades import (
    CEDS_GRADE_CODES,
    resolve_timetable_scope,
    split_by_homeroom_grades,
    timetable_rostered_grades,
)


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
# 1a. Unit — the DERIVED rostered set (plan 0043, slice 1)
# ---------------------------------------------------------------------------
class TestTimetableRosteredGrades:
    """What EFFECTIVELY receives subject rostering, as opposed to what was
    CONFIGURED. The pair sits here rather than in its own module because the
    two functions answer adjacent questions with near-identical shapes, and
    reading them side by side is how a caller picks the right one."""

    HOMEROOM = ["KG", "01", "02", "03"]

    def test_no_configured_scope_yields_the_CEDS_complement(self):
        """The unbounded complement, spelled once and bounded by the CEDS output
        vocabulary — this is what `split_by_homeroom_grades` used to express as
        `~isin(homeroom_grades)`."""
        rostered = timetable_rostered_grades(self.HOMEROOM, timetable_scope=None)
        assert rostered == set(CEDS_GRADE_CODES) - set(self.HOMEROOM)
        assert "04" in rostered and "12" in rostered and "UG" in rostered
        assert not (rostered & set(self.HOMEROOM))

    def test_an_EMPTY_scope_is_not_the_complement(self):
        """The `is None` / falsiness trap, from the consuming side: an empty
        configured scope rosters NOTHING, while no scope at all rosters the
        whole complement. A truthiness check here would silently re-roster
        every timetable grade for the `"homeroom"` sentinel's districts."""
        assert timetable_rostered_grades(self.HOMEROOM, timetable_scope=set()) == set()

    def test_a_configured_scope_is_returned_as_a_FRESH_set(self):
        """Both branches return a new set: the caller's configured scope must
        never be aliased into a mask a later caller could mutate."""
        configured = {"10", "11"}
        rostered = timetable_rostered_grades(self.HOMEROOM, timetable_scope=configured)
        assert rostered == configured
        assert rostered is not configured
        rostered.add("12")
        assert configured == {"10", "11"}

    def test_the_scope_cannot_be_passed_POSITIONALLY(self):
        """Both arguments are collections of CEDS codes and `global_config.get`
        returns `Any`, so a swapped pair would type-check silently. Keyword-only
        with no default makes the unsafe call unrepresentable."""
        with pytest.raises(TypeError):
            timetable_rostered_grades(self.HOMEROOM, {"10"})  # type: ignore[misc]


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
    """The gate itself: with the key absent, the SCOPE machinery changes nothing.

    Still true after plan 0043 ungated blend suppression, and for a reason worth
    stating: every blend in THIS corpus carries at least one pupil on the
    timetable side, so the now-unconditional rule has nothing to take. The
    default path is no longer "nothing is ever suppressed" — a blend all of
    whose SCHEDULE ROWS are homeroom grades goes for every district
    (`TestRowSetIdentityUnderBlankGrades` below, and the SD74 golden).
    """

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

    def test_the_MODE_masked_blend_survives_on_its_ONE_timetable_side_pupil(
        self, corpus_raw_data, classes_mapping, enrollments_mapping, global_config
    ):
        """Blend "D" is named (05/06) — both MODE grades are base homeroom
        grades — but MTD1 also carries one grade-10 pupil, who IS timetable-side.
        The suppression rule reads schedule ROWS, not section modes, so the
        blend survives and that pupil rides it.

        This is precisely the case a mode-gated rule would have got wrong: it
        would have suppressed a blend that has a student, re-keyed them to
        `MTD1_2026` and GROWN Classes.csv. Pinned here as the default-path twin
        of `TestRowSetIdentityUnderBlankGrades`.
        """
        classes, enrollments = _run_shape(corpus_raw_data, classes_mapping, enrollments_mapping, global_config)
        blends = _blended_ids(_class_ids(classes))
        survivor = {cid for cid in blends if "T013" in cid}
        assert survivor, f"the 05/06 blend with a grade-10 pupil vanished: {sorted(blends)}"
        assert "MTD1_2026" not in _class_ids(classes), "the pupil was re-keyed to a per-section class"
        assert set(enrollments[enrollments["User ID"] == _MODE_MASKED_STUDENT]["Class ID"]) == survivor


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
        # MTB2 / MTC1 / MTC2 ride the two 10-11 blends, and MTD1 rides blend D —
        # which survives on its grade-10 pupil since plan 0043 gates on schedule
        # ROWS rather than section modes. So the standalone grade-12 section is
        # the ONE plain subject class.
        assert _subject_ids(ids) == {"MTE_2026"}, sorted(ids)
        # Nothing at all for the wholly unrostered sections.
        assert not {"MTA1_2026", "MTA2_2026", "MTD1_2026", "MTD2_2026", "MTF_2026"} & ids

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
        # MTB2 (10), MTC1 (10), MTC2 (11) and MTD1 (its grade-10 minority pupil)
        # are all absorbed into surviving blends, so MTE (12) is the only plain
        # subject class — inside 10-12, none below.
        assert set(subject["Class ID"]) == {"MTE_2026"}
        assert set(subject["Grade"]) == {"12"}
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

    def test_the_wholly_unrostered_blend_is_suppressed(self, shape3):
        """The 07/08 blend has no pupil on the timetable side at all (both
        grades are homeroom grades here), so it goes.

        Blend D used to be asserted here too — it no longer belongs: under
        per-row gating it carries a rostered pupil and survives, which is the
        subject of the row below.
        """
        classes, _ = shape3
        blends = _blended_ids(_class_ids(classes))
        assert not any("T010" in cid for cid in blends), "the 07/08 blend (both homeroom) survived"
        assert blends, "the positive twin: some blend must survive, or this passes by emptiness"

    def test_mode_masked_section_RIDES_its_blend_with_no_orphan(self, shape3):
        """The R3(i) fork, re-pointed by plan 0043: MTD1's MODE grade is 06 (out
        of scope), but its minority grade-10 pupil IS in scope — and the
        suppression rule reads schedule ROWS. So the blend survives, the pupil
        rides it, and there is no per-section fallback class.

        **Documented incoherence, accepted deliberately.** `get_grade_range`
        still reads the MODE map, so this blend ships named "(05/06)" while its
        ONE enrolled pupil is grade 10 — and `Classes.csv`'s `Grade` cell is
        empty for blends, making the name the only grade signal. Naming was left
        on modes so no district's blend NAMES change with this release; the
        residual is tracked on the roadmap.
        """
        classes, enrollments = shape3
        survivor = {cid for cid in _blended_ids(_class_ids(classes)) if "T013" in cid}
        assert survivor, "the mode-masked blend was suppressed despite carrying a rostered pupil"
        assert "MTD1_2026" not in _class_ids(classes), "the pupil was re-keyed to a per-section class"
        minority = enrollments[enrollments["User ID"] == _MODE_MASKED_STUDENT]
        assert set(minority["Class ID"]) == survivor
        blend_row = classes[classes["Class ID"] == survivor.copy().pop()]
        assert "(05/06)" in blend_row["Name"].iloc[0], "the accepted MODE-named / per-row-gated wrinkle"
        assert _class_ids(enrollments) <= _class_ids(classes), "orphan Class IDs in Enrollments"


# ---------------------------------------------------------------------------
# 2a. ROW-SET IDENTITY — the acceptance test for plan 0043 slice 2
# ---------------------------------------------------------------------------
#
# One teacher (T020), two sections at one slot, ALL of whose declared grades are
# homeroom grades — except for a single pupil whose grade cell is BLANK. A blank
# converts to "UG", "UG" is not a homeroom grade, so that pupil IS timetable-side
# and the blend genuinely has a student.
_BLANK_PUPIL = "S_MTZ1_BLANK"
_BLANK_SESSIONS = [("MTZ1", "HR-3B"), ("MTZ2", "HR-4B")]


def _blank_grade_corpus(*, with_blank_pupil: bool) -> dict[str, pd.DataFrame]:
    """The two-section corpus, with and without the one blank-grade row."""
    pupils = [("S_MTZ1_A", "MTZ1", "03"), ("S_MTZ1_B", "MTZ1", "03"), ("S_MTZ2_A", "MTZ2", "04")]
    if with_blank_pupil:
        pupils.insert(2, (_BLANK_PUPIL, "MTZ1", None))

    schedule = pd.DataFrame(
        [
            {
                "student number": student,
                "student id": student,
                "school number": "300",
                "school year": "2025/2026",
                "grade": grade,
                "master timetable id": mt_id,
                "teacher id": "T020",
                "section letter": "A",
                "district course code": dict(_BLANK_SESSIONS)[mt_id],
                "primary teacher": "Y",
                "teacher name": "Zhang",
                "period": "9",
            }
            for student, mt_id, grade in pupils
        ]
    )
    demographic = pd.DataFrame(
        {
            "student number": [student for student, *_ in pupils],
            "legal first name": ["First"] * len(pupils),
            "legal surname": ["Last"] * len(pupils),
            "date of birth": ["2015-01-15"] * len(pupils),
            "grade": [grade for *_rest, grade in pupils],
            "school number": ["300"] * len(pupils),
            # The blank-grade pupil has no homeroom either — their ONLY possible
            # class is the blend, which is what makes the assertions sharp.
            "homeroom": [f"HR{grade}" if grade else "" for *_rest, grade in pupils],
            "previous school number": [""] * len(pupils),
            "usual first name": [""] * len(pupils),
            "usual surname": [""] * len(pupils),
            "student email address": [""] * len(pupils),
            "enrolment status": ["Active"] * len(pupils),
            "teacher name": ["Zhang"] * len(pupils),
            "teacher id": ["T020"] * len(pupils),
        }
    )
    class_info = pd.DataFrame(
        [
            {
                "school number": "300",
                "teacher id": "T020",
                "master timetable id": mt_id,
                "course code": course,
                "term": "1",
                "semester": "1",
                "day": "1",
                "period": "9",
            }
            for mt_id, course in _BLANK_SESSIONS
        ]
    )
    return {
        "StudentDemographicInformation.txt": demographic,
        "StudentSchedule.txt": schedule,
        "StaffInformationEnhanced.txt": pd.DataFrame(
            {
                "teacher id": ["T020"],
                "first name": ["Zoe"],
                "last name": ["Zhang"],
                "email address": ["zhang@school.ca"],
                "teaching staff": ["Y"],
                "school number": ["300"],
            }
        ),
        "CourseInformation.txt": pd.DataFrame(
            {
                "school number": ["300"] * len(_BLANK_SESSIONS),
                "course code": [course for _mt, course in _BLANK_SESSIONS],
                "title": [f"Course {course}" for _mt, course in _BLANK_SESSIONS],
            }
        ),
        "EmergencyContactInformation.txt": pd.DataFrame(),
        "ClassInformationEnh.txt": class_info,
    }


def _run_students_classes_enrollments(raw_data, students_mapping, classes_mapping, enrollments_mapping, global_config):
    """Students FIRST, so `active_student_ids` is genuinely populated.

    `filter_to_active` fails SAFE on an empty roster (it keeps everyone), so a
    run that skipped Students would prove the blend carries a *row*, not a live
    STUDENT — and a live student is the whole claim.
    """
    transformer = DataTransformer()
    transformer.set_school_year(2026, "08-25", "07-25")
    students = transformer.transform(
        raw_data["StudentDemographicInformation.txt"], students_mapping, "Students", raw_data, global_config
    )
    classes = transformer.transform(
        raw_data["StudentSchedule.txt"], classes_mapping, "Classes", raw_data, global_config
    )
    enrollments = transformer.transform(
        raw_data["StudentSchedule.txt"], enrollments_mapping, "Enrollments", raw_data, global_config
    )
    return students, classes, enrollments


class TestRowSetIdentityUnderBlankGrades:
    """The blend-suppression gate must partition the SAME ROWS, by the SAME
    derivation, as `split_by_homeroom_grades(keep="subject")` (plan 0043).

    The natural implementation — building the per-row map beside
    `_build_grade_map`, inheriting its `.dropna()` — passes every other test in
    this file and breaks exactly here: the blank-grade pupil survives the
    subject filter but is invisible to the gate, so the blend is suppressed, the
    pupil falls back to `MTZ1_2026`, `Classes.csv` GROWS and a live Class ID is
    re-assigned. That is the opposite of what this change promises, so this pair
    is the acceptance criterion, not a nice-to-have.

    Default (unscoped) config throughout — the shape every shipped district runs.
    """

    def test_the_blend_SURVIVES_and_keeps_the_blank_grade_pupil(
        self, students_mapping, classes_mapping, enrollments_mapping, global_config
    ):
        students, classes, enrollments = _run_students_classes_enrollments(
            _blank_grade_corpus(with_blank_pupil=True),
            students_mapping,
            classes_mapping,
            enrollments_mapping,
            global_config,
        )
        assert _BLANK_PUPIL in set(students["User ID"]), "the blank-grade pupil must be an ACTIVE student"
        assert not enrollments.empty

        blends = _blended_ids(_class_ids(classes))
        survivor = {cid for cid in blends if "T020" in cid}
        assert survivor, f"the blend carrying a live student was suppressed: {sorted(_class_ids(classes))}"
        blend_id = survivor.pop()

        # No per-section fallback class, and no Class ID re-assignment: the
        # blank-grade pupil's ONLY enrollment is the blended one.
        assert not {"MTZ1_2026", "MTZ2_2026"} & _class_ids(classes), sorted(_class_ids(classes))
        assert set(enrollments[enrollments["User ID"] == _BLANK_PUPIL]["Class ID"]) == {blend_id}
        assert _class_ids(enrollments) <= _class_ids(classes), "orphan Class IDs in Enrollments"

    def test_WITHOUT_that_one_row_the_very_same_blend_is_suppressed(
        self, students_mapping, classes_mapping, enrollments_mapping, global_config
    ):
        """The differential twin. Identical corpus minus the blank-grade row:
        now every pupil really is homeroom-side, the blend really is studentless
        and it goes — together with its teacher row, and WITHOUT leaving a
        per-section class behind (nothing was on the timetable side to key one).
        """
        students, classes, enrollments = _run_students_classes_enrollments(
            _blank_grade_corpus(with_blank_pupil=False),
            students_mapping,
            classes_mapping,
            enrollments_mapping,
            global_config,
        )
        assert len(students) == 3
        assert not enrollments.empty, "the homeroom enrollments must still exist"
        assert _blended_ids(_class_ids(classes)) == set()
        assert _blended_ids(_class_ids(enrollments)) == set()
        assert not _subject_ids(_class_ids(classes)), sorted(_class_ids(classes))
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
