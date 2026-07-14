"""End-to-end smoke test for the `mbponly` myBlueprint+ tier.

`mbponly` emits ONLY the two course CSVs (CourseInfo + StudentCourses) and
nothing else, so its only required GDEs are CourseInformation.txt,
StudentCourseHistory.txt, and StudentCourseSelection.txt. The detailed
transformation rules are covered exhaustively by the in-memory unit tests
(test_transform_course_info.py / test_transform_student_courses.py); this
test guards the *wiring* the unit tests can't see:

  - `extract_required_files` asks for only the 3 course files,
  - the extractor parses real-GDE-shaped .txt files (dates, quoted commas)
    into the columns the transformers expect,
  - the pipeline produces exactly CourseInfo.csv + StudentCourses.csv and
    none of the 5 rostering CSVs,
  - the loader commits them with no leftover staging dir.

Fixtures live in tests/snapshots/mbp_input/ — small, hand-authored, fully
synthetic rows (no real student data) shaped like a MyEd BC course export.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.main import main

FIXTURE_DIR = Path(__file__).parent / "snapshots" / "mbp_input"

ROSTERING_CSVS = ["Students.csv", "Staff.csv", "Family.csv", "Classes.csv", "Enrollments.csv"]

COURSE_INFO_COLUMNS = [
    "Course Code",
    "Alternate Course Code",
    "School ID",
    "Course Name",
    "Course Description",
    "Discipline",
    "Department",
    "Type",
    "Grade",
    "MaxGrade",
    "Credit Value",
    "IntegrationId",
    "Year Offered",
]

STUDENT_COURSES_COLUMNS = [
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
]


@pytest.fixture
def mbponly_run(tmp_path):
    """Run the mbponly pipeline against the committed fixtures into a tmp output dir."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main("mbponly", str(FIXTURE_DIR), str(output_dir))
    return output_dir


def _read(output_dir, name):
    """Read an output CSV as all-strings so blanks are '' and no dtype inference."""
    return pd.read_csv(output_dir / name, dtype=str, keep_default_na=False)


class TestMBPOnlyPipeline:
    def test_produces_only_the_two_course_csvs(self, mbponly_run):
        assert (mbponly_run / "CourseInfo.csv").exists()
        assert (mbponly_run / "StudentCourses.csv").exists()
        for rostering in ROSTERING_CSVS:
            assert not (mbponly_run / rostering).exists(), f"mbponly must not emit {rostering}"

    def test_no_staging_dir_left_behind(self, mbponly_run):
        assert list(mbponly_run.glob(".tmp_*")) == []

    def test_course_info_schema_and_content(self, mbponly_run):
        course_info = _read(mbponly_run, "CourseInfo.csv")
        assert list(course_info.columns) == COURSE_INFO_COLUMNS
        codes = set(course_info["Course Code"])
        # Grade 10+ rows survive; the X-prefix row is pattern-excluded end-to-end.
        assert {"MEN--10", "MMA--10"} <= codes
        assert "XGEN-12" not in codes
        mma = course_info[course_info["Course Code"] == "MMA--10"].iloc[0]
        assert mma["School ID"] == "7479001"
        assert mma["Course Name"] == "Math 10"

    def test_student_courses_schema_and_content(self, mbponly_run):
        student_courses = _read(mbponly_run, "StudentCourses.csv")
        assert list(student_courses.columns) == STUDENT_COURSES_COLUMNS

        # History pass row for student A, plus the no-history selection row for
        # student B. The X-prefix history row is pattern-excluded.
        assert set(student_courses["Course Code"]) == {"MMA--10", "MEN--10"}
        assert "XGEN-12" not in set(student_courses["Course Code"])

        passed = student_courses[student_courses["Student ID"] == "4000001"].iloc[0]
        assert passed["Course Code"] == "MMA--10"
        assert passed["Final Mark"] == "75"
        assert passed["Completion Date"] == "2025-01-30"  # dd-Mon-yyyy parsed to ISO
        assert passed["Credits Earned"] == "4"
        assert passed["Course Name"] == "Math 10"

        selected = student_courses[student_courses["Student ID"] == "4000002"].iloc[0]
        assert selected["Course Code"] == "MEN--10"
        assert selected["Final Mark"] == ""  # selection rows carry no mark/completion
        assert selected["Completion Date"] == ""


class TestMBPOnlyRequiredFiles:
    def test_extract_required_files_is_courses_only(self):
        from src.config.loader import load_config
        from src.etl.pipeline import extract_required_files

        required = set(extract_required_files(load_config("mbponly")))
        assert required == {
            "CourseInformation.txt",
            "StudentCourseHistory.txt",
            "StudentCourseSelection.txt",
        }
