"""Zero-orphan invariant: no enrollment/class references a non-rostered student.

Slice 2 of plan 0003. After StudentTransformer publishes the active roster
(``context.active_student_ids``), ClassTransformer (homeroom) and
EnrollmentTransformer (homeroom + subject) filter their *student* rows against
it. The invariant under test:

    Every student-role Enrollments row's ``User ID`` is in ``set(Students.User ID)``
    — across BOTH the homeroom (demographic) and subject (schedule) paths.

Teacher and co-teacher rows are derived from UNfiltered frames and must stay
byte-identical to the pre-filter output (only *student* rows are filtered).

The active set is published by running the real Students transform first (the
``DataTransformer`` facade shares one context across entity transforms), so the
test exercises the production publish→filter wiring end to end.
"""

import pandas as pd

from src.etl.transformer import DataTransformer

# Homeroom grades K + 01-07 (per base myedbc config). Grade 10/12 are subject.
HOMEROOM_GC = {
    "homeroom_grades": ["IT", "PR", "PK", "TK", "KG", "01", "02", "03", "04", "05", "06", "07"],
}


def _global_config(base_mapping, **overrides):
    gc = {
        **base_mapping.get("global_config", {}),
        "mappings": base_mapping.get("mappings", {}),
        **HOMEROOM_GC,
    }
    gc.update(overrides)
    return gc


# ---------------------------------------------------------------------------
# Fixtures: active + withdrawn students across homeroom and subject paths
# ---------------------------------------------------------------------------
#
# Active   homeroom: S001 (K, A1), S002 (3, A1), S005 (4, B2)
# Inactive homeroom: S010 (Inactive, A1), S011 (past withdraw, B2)
# Active   subject : S003 (10), S004 (12)
# Inactive subject : S012 (10, withdrawn)  -> in schedule but NOT in roster
#
# Every homeroom that contains an inactive student (A1, B2) ALSO contains an
# active one, so the homeroom class set is identical whether or not the active
# filter runs. That holds the class set constant between the mixed and the
# all-active control, isolating the student-only enrollment filter — teacher
# rows must then be byte-identical across the two.

_DEMO_COLUMNS = [
    "student number",
    "legal first name",
    "legal surname",
    "grade",
    "school number",
    "homeroom",
    "enrolment status",
    "withdraw date",
    "teacher name",
    "teacher id",
]


def _demographic(all_active: bool = False) -> pd.DataFrame:
    """Demographic frame.

    With ``all_active=True`` every status is Active and every withdraw date
    blank — the "control" used to prove teacher rows are identical with vs
    without the active filter.
    """
    rows = [
        # student, first, last, grade, school, homeroom, status,      withdraw,       tname,        tid
        ("S001", "Alice", "A", "K", "100", "A1", "Active", "", "Harper", "T001"),
        ("S002", "Bob", "B", "3", "100", "A1", "Active", "", "Harper", "T001"),
        ("S010", "Cara", "C", "1", "100", "A1", "Inactive", "", "Harper", "T001"),
        ("S005", "Hank", "H", "4", "100", "B2", "Active", "", "Reed", "T002"),
        ("S011", "Dan", "D", "2", "100", "B2", "Active", "15-Jan-2020", "Reed", "T002"),
        ("S003", "Eve", "E", "10", "200", "C3", "Active", "", "Liu", "T003"),
        ("S004", "Fay", "F", "12", "200", "C4", "Active", "", "Singh", "T004"),
        ("S012", "Gus", "G", "10", "200", "C3", "Withdrawn", "", "Liu", "T003"),
    ]
    if all_active:
        rows = [
            (num, fn, ln, gr, sc, hr, "Active", "", tn, tid) for (num, fn, ln, gr, sc, hr, _st, _wd, tn, tid) in rows
        ]
    return pd.DataFrame(rows, columns=_DEMO_COLUMNS)


def _schedule() -> pd.DataFrame:
    """Schedule with active subject students (S003/S004) AND a withdrawn one (S012).

    Homeroom-grade rows (S001/S002 etc.) are not required here — homeroom
    enrollments are built from the demographic file, not the schedule.
    """
    return pd.DataFrame(
        {
            "student number": ["S003", "S004", "S012"],
            "student id": ["S003", "S004", "S012"],
            "school number": ["200", "200", "200"],
            "school year": ["2025/2026", "2025/2026", "2025/2026"],
            "grade": ["10", "12", "10"],
            "master timetable id": ["MT004", "MT005", "MT004"],
            "teacher id": ["T003", "T004", "T003"],
            "section letter": ["A", "A", "A"],
            "district course code": ["MAT10", "ENG12", "MAT10"],
            "primary teacher": ["Y", "Y", "Y"],
            "teacher name": ["Liu", "Singh", "Liu"],
        }
    )


def _staff() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "teacher id": ["T001", "T002", "T003", "T004"],
            "first name": ["Jane", "Mark", "Linda", "Raj"],
            "last name": ["Harper", "Reed", "Liu", "Singh"],
            "email address": ["h@s.ca", "r@s.ca", "l@s.ca", "s@s.ca"],
            "teaching staff": ["Y", "Y", "Y", "Y"],
            "school number": ["100", "100", "200", "200"],
        }
    )


def _course_info() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "school number": ["200", "200"],
            "course code": ["MAT10", "ENG12"],
            "title": ["Math 10", "English 12"],
        }
    )


def _raw_data(demographic: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "StudentDemographicInformation.txt": demographic,
        "StudentSchedule.txt": _schedule(),
        "StaffInformationEnhanced.txt": _staff(),
        "CourseInformation.txt": _course_info(),
        "EmergencyContactInformation.txt": pd.DataFrame(
            {"student number": ["S001"], "first name": ["P"], "last name": ["A"], "email address": ["p@a.ca"]}
        ),
        "ClassInformationEnh.txt": pd.DataFrame(),
    }


def _run_full(base_mapping, demographic):
    """Run Students -> Classes -> Enrollments on one shared transformer/context.

    Returns (students_df, classes_df, enrollments_df). Students runs first so
    its published active set is visible to Classes/Enrollments — matching the
    pipeline's entity order.
    """
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    gc = _global_config(base_mapping)
    raw = _raw_data(demographic)

    students = t.transform(demographic, base_mapping["mappings"]["Students"], "Students", raw, gc)
    classes = t.transform(_schedule(), base_mapping["mappings"]["Classes"], "Classes", raw, gc)
    enrollments = t.transform(_schedule(), base_mapping["mappings"]["Enrollments"], "Enrollments", raw, gc)
    return students, classes, enrollments


def _orphan_counts(students: pd.DataFrame, enrollments: pd.DataFrame) -> tuple[int, int]:
    """(homeroom_orphans, subject_orphans): student rows whose User ID ∉ roster.

    Homeroom Class IDs are ``{school}_{homeroom}_{year}``; subject Class IDs are
    Master-Timetable-derived (``MT...``), giving a clean split for counting.
    """
    roster = set(students["User ID"].astype(str))
    student_rows = enrollments[enrollments["Role"] == "student"].copy()
    student_rows["User ID"] = student_rows["User ID"].astype(str)
    orphans = student_rows[~student_rows["User ID"].isin(roster)]
    is_homeroom = orphans["Class ID"].astype(str).str.contains("_A1_|_B2_|_C3_|_C4_")
    return int(is_homeroom.sum()), int((~is_homeroom).sum())


class TestZeroOrphanInvariant:
    def test_no_orphaned_student_enrollments(self, base_mapping):
        """Headline: zero student-role Enrollments rows reference a non-rostered
        student — across both the homeroom and subject paths.
        """
        students, _classes, enrollments = _run_full(base_mapping, _demographic())

        roster = set(students["User ID"].astype(str))
        # Inactive students are off the roster...
        assert {"S010", "S011", "S012"}.isdisjoint(roster)
        # ...and active ones are on it.
        assert {"S001", "S002", "S003", "S004", "S005"}.issubset(roster)

        student_rows = enrollments[enrollments["Role"] == "student"]
        orphans = student_rows[~student_rows["User ID"].astype(str).isin(roster)]
        assert orphans.empty, f"Orphaned student enrollments: {orphans.to_dict('records')}"

    def test_homeroom_and_subject_both_clean(self, base_mapping):
        """Both paths contribute zero orphans (the regression's before/after)."""
        students, _classes, enrollments = _run_full(base_mapping, _demographic())
        homeroom_orphans, subject_orphans = _orphan_counts(students, enrollments)
        assert homeroom_orphans == 0
        assert subject_orphans == 0

    def test_active_students_still_enrolled(self, base_mapping):
        """The filter removes only inactive rows — active students still enroll."""
        _students, _classes, enrollments = _run_full(base_mapping, _demographic())
        student_ids = set(enrollments[enrollments["Role"] == "student"]["User ID"].astype(str))
        # S001/S002/S005 homeroom (demographic), S003/S004 subject (schedule).
        assert {"S001", "S002", "S005"}.issubset(student_ids)
        assert {"S003", "S004"}.issubset(student_ids)

    def test_set_identity_after_students_transform(self, base_mapping):
        """``active_student_ids`` equals ``set(Students.User ID)`` by construction."""
        t = DataTransformer()
        t.set_school_year(2025, "08-25", "07-25")
        gc = _global_config(base_mapping)
        demographic = _demographic()
        raw = _raw_data(demographic)

        result = t.transform(demographic, base_mapping["mappings"]["Students"], "Students", raw, gc)
        assert t._context.active_student_ids == set(result["User ID"].astype(str).str.strip())


class TestTeacherRowsUnchanged:
    """Teacher / co-teacher rows must be byte-identical with vs without the
    active filter — the filter touches *student* rows only.
    """

    @staticmethod
    def _teacher_rows(df: pd.DataFrame) -> pd.DataFrame:
        rows = df[df["Role"] == "teacher"].copy()
        return rows.sort_values(["Class ID", "User ID"]).reset_index(drop=True)

    def test_teacher_enrollments_identical_with_and_without_filter(self, base_mapping):
        """Compare teacher rows for a mixed roster vs an all-active roster.

        With all students active the filter is a no-op, so any difference in
        teacher rows would mean the filter leaked into a teacher derivation.
        The fixture keeps an active student in every homeroom that has an
        inactive one, so the homeroom *class* set is identical across the two
        runs — isolating the student-only enrollment filter. (A homeroom with
        *only* inactive students is correctly dropped along with its teacher
        row; that is the no-empty-homeroom goal, covered separately in
        ``TestHomeroomClassFiltering``.)
        """
        _s_mixed, _c_mixed, enr_mixed = _run_full(base_mapping, _demographic(all_active=False))
        _s_all, _c_all, enr_all = _run_full(base_mapping, _demographic(all_active=True))

        pd.testing.assert_frame_equal(self._teacher_rows(enr_mixed), self._teacher_rows(enr_all))

    def test_teacher_rows_present_for_mixed_homeroom(self, base_mapping):
        """A homeroom with a mix of active + inactive students still emits its
        teacher row (the inactive student rows are dropped, the teacher is not).
        """
        _students, _classes, enrollments = _run_full(base_mapping, _demographic())
        teacher_rows = enrollments[enrollments["Role"] == "teacher"]
        # T001 teaches homeroom A1 (S001 active, S010 inactive) — must survive.
        a1_teacher = teacher_rows[teacher_rows["Class ID"].astype(str).str.contains("_A1_")]
        assert not a1_teacher.empty


class TestHomeroomClassFiltering:
    """Homeroom classes are built only where active students exist."""

    def test_all_inactive_homeroom_produces_no_class(self, base_mapping):
        """A homeroom whose only students are inactive yields no homeroom class."""
        demographic = pd.DataFrame(
            [
                # Two inactive students sharing homeroom Z9 — no active student there.
                ("S020", "X", "X", "1", "300", "Z9", "Inactive", "", "Reed", "T002"),
                ("S021", "Y", "Y", "2", "300", "Z9", "Withdrawn", "", "Reed", "T002"),
                # An active student in a different homeroom keeps the roster non-empty
                # (so the guard does not short-circuit the filter).
                ("S001", "Alice", "A", "K", "100", "A1", "Active", "", "Harper", "T001"),
            ],
            columns=_DEMO_COLUMNS,
        )
        t = DataTransformer()
        t.set_school_year(2025, "08-25", "07-25")
        gc = _global_config(base_mapping)
        raw = _raw_data(demographic)

        t.transform(demographic, base_mapping["mappings"]["Students"], "Students", raw, gc)
        classes = t.transform(_schedule(), base_mapping["mappings"]["Classes"], "Classes", raw, gc)

        class_ids = classes["Class ID"].astype(str)
        assert not class_ids.str.contains("_Z9_").any(), "Homeroom Z9 (all-inactive) must not be created"

    def test_mixed_homeroom_still_created(self, base_mapping):
        """A homeroom with at least one active student is still created."""
        _students, classes, _enrollments = _run_full(base_mapping, _demographic())
        class_ids = classes["Class ID"].astype(str)
        # A1 has S001 (active) + S010 (inactive) — class survives.
        assert class_ids.str.contains("_A1_").any()


class TestEmptyRosterGuard:
    """When Students has not published a roster, filtering is skipped (never
    filter-to-empty) and a warning is logged.
    """

    def test_guard_leaves_rows_intact_and_warns(self, base_mapping, caplog):
        """Running Classes/Enrollments WITHOUT Students first → empty roster →
        all student rows survive (back-compat) + a warning is emitted.
        """
        t = DataTransformer()
        t.set_school_year(2025, "08-25", "07-25")
        gc = _global_config(base_mapping)
        demographic = _demographic()
        raw = _raw_data(demographic)

        # Deliberately skip the Students transform → active_student_ids stays empty.
        assert t._context.active_student_ids == set()

        t.transform(_schedule(), base_mapping["mappings"]["Classes"], "Classes", raw, gc)
        with caplog.at_level("WARNING"):
            enrollments = t.transform(_schedule(), base_mapping["mappings"]["Enrollments"], "Enrollments", raw, gc)

        # Without the roster the inactive subject student S012 is NOT dropped.
        student_ids = set(enrollments[enrollments["Role"] == "student"]["User ID"].astype(str))
        assert "S012" in student_ids
        assert any("active_student_ids empty" in r.message for r in caplog.records)
