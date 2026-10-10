"""SD45's schedule extract has `Student Number` and NO `Student ID` — Enrollments must build.

The district's first real run (DistrictSync 3.27.0, 2026-10-09) stopped at Enrollments with
``column(s) ['Student ID'] not found in the source`` (``source_schema``): the base mapping
reads the schedule's student from ``Student ID`` and ``sd45myedbc`` inherited that, although
``StudentScheduleDrops.csv`` names the column ``Student Number`` (the demographic file's name
too). The shared contract fixture (``tests.test_contract._write_base_schedule``) writes BOTH
columns, which is why the whole suite stayed green over a config that could not run on the
district's real export.

These tests feed ``sd45myedbc`` a schedule with the headers the district's own export has —
the four named in the failing run (``School Year``, ``Student Number``, ``Section``,
``Type``) plus the columns the mapping reads — and NO ``Student ID`` / ``Section Letter`` /
``Primary Teacher`` / ``Teacher Name``. Every row is synthetic (fake ids).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config.loader import load_config
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.outcomes import OutcomeKind
from src.etl.pipeline import run_pipeline
from src.history.store import read_run_records
from tests.test_contract import (
    _create_sd45_inputs,
    _write_course_info,
    _write_family,
    _write_staff,
    _write_student_demographic,
)

SD45 = "sd45myedbc"

#: The schedule columns the failing run proved exist (``School Year``, ``Student Number``,
#: ``Section``, ``Type``) and the ones the mapping reads that the preflight did not flag.
#: Deliberately absent: ``Student ID``, ``Section Letter``, ``Primary Teacher``.
REAL_SCHEDULE_HEADERS = [
    "School Year",
    "School Number",
    "Student Number",
    "Grade",
    "Course Code",
    "Section",
    "Type",
    "Master Timetable ID",
    "Teacher ID",
]


def _write_real_schedule(path: Path, *, student_column: str = "Student Number") -> None:
    """Three synthetic timetable rows under the district's own header names."""
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 3,
            "School Number": ["100", "200", "200"],
            student_column: ["S001", "S002", "S003"],
            "Grade": ["3", "10", "12"],
            "Course Code": ["HR-3", "MAT10", "ENG12"],
            "Section": ["1", "1", "2"],
            "Type": ["Regular", "Regular", "Regular"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    ).to_csv(path / "StudentScheduleDrops.csv", index=False)


def _inputs(tmp_path: Path, *, student_column: str = "Student Number") -> tuple[Path, Path]:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    _write_student_demographic(inp, "StudentDemographicEnhanced.csv")
    _write_staff(inp, "StaffInformationEnhanced.csv")
    _write_course_info(inp, "CourseInformationEnhanced.csv")
    _write_family(inp, "EmergencyContactInfoEnhanced.csv")
    _write_real_schedule(inp, student_column=student_column)
    return inp, out


def test_the_fixture_is_the_district_shape_not_the_shared_one(tmp_path: Path) -> None:
    """The guard against the fixture drifting back to one that carries `Student ID`."""
    inp, _out = _inputs(tmp_path)
    headers = list(pd.read_csv(inp / "StudentScheduleDrops.csv", nrows=0).columns)
    assert headers == REAL_SCHEDULE_HEADERS
    for absent in ("Student ID", "Section Letter", "Primary Teacher"):
        assert absent not in headers


@pytest.mark.integration
def test_enrollments_build_on_the_schedules_student_number(tmp_path: Path) -> None:
    inp, out = _inputs(tmp_path)
    result = run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success" and record["error_category"] == "none"
    kinds = {o.entity: o.kind for o in result.entity_outcomes}
    assert kinds["Enrollments"] is OutcomeKind.BUILT
    assert kinds["Classes"] is OutcomeKind.BUILT

    enrollments = pd.read_csv(out / "Enrollments.csv", dtype=str, keep_default_na=False)
    subject = enrollments[enrollments["Class ID"].str.startswith(("MT002_", "MT003_"))]
    # Positive twin of the join: BOTH timetable students (grades 10 and 12) are enrolled, each
    # in their own section, as students — and their teachers beside them.
    assert set(zip(subject["User ID"], subject["Role"], strict=True)) == {
        ("S002", "student"),
        ("S003", "student"),
        ("T003", "teacher"),
        ("T004", "teacher"),
    }
    # S001 is a homeroom-grade pupil: no subject enrollment, but never dropped from the roster.
    students = pd.read_csv(out / "Students.csv", dtype=str, keep_default_na=False)
    assert {"S001", "S002", "S003"} <= set(students["User ID"])


@pytest.mark.integration
def test_the_missing_section_and_primary_teacher_columns_degrade_names_not_enrollments(tmp_path: Path) -> None:
    """No `Section Letter` / `Primary Teacher` / `Teacher Name`: class NAMES lose those parts
    (one standing note on Classes), but every class and enrollment still ships."""
    inp, out = _inputs(tmp_path)
    run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success"
    assert record["entity_outcomes"]["Classes"]["notes"] == {"class_name_column_absent": 2}
    # SD45 sends no ClassInformationEnh.txt either (blended detection is off): that is the one
    # standing amber — co-teachers left out — and never a failure.
    assert record["entity_outcomes"]["Enrollments"]["notes"] == {"coteacher_source_unusable": 1}
    classes = pd.read_csv(out / "Classes.csv", dtype=str, keep_default_na=False)
    shipped = {cid.split("_")[0] for cid in classes["Class ID"]}
    assert {"MT002", "MT003"} <= shipped


@pytest.mark.integration
def test_a_schedule_without_student_number_still_stops_the_night(tmp_path: Path) -> None:
    """The negative control: the overlay asks for `Student Number`, so a schedule that names the
    column anything else (here the base's `Student ID`) fails typed and names `Student Number` —
    the join is not silently dropped, and the test above is not vacuous."""
    inp, out = _inputs(tmp_path, student_column="Student ID")
    with pytest.raises(SourceSchemaError) as raised:
        run_pipeline(SD45, str(inp), str(out))
    assert raised.value.entity == "Enrollments"
    assert raised.value.guard is GuardKind.JOIN_KEY
    assert "Student Number" in str(raised.value)
    assert "Student ID" not in str(raised.value)


class TestTheOverlayIsScopedToSd45:
    def test_sd45_reads_the_schedules_student_from_student_number(self) -> None:
        field_map = load_config(SD45).to_raw_dict()["mappings"]["Enrollments"]["field_map"]
        for field in ("User ID", "Role"):
            assert field_map[field] == {"student_id_col": "Student Number", "staff_id_col": "Teacher ID"}

    def test_the_base_still_reads_student_id(self) -> None:
        field_map = load_config("myedbc").to_raw_dict()["mappings"]["Enrollments"]["field_map"]
        assert field_map["User ID"] == {"student_id_col": "Student ID", "staff_id_col": "Teacher ID"}

    def test_the_shared_contract_fixture_carries_no_student_id_for_sd45(self, tmp_path: Path) -> None:
        """`_create_sd45_inputs` feeds the whole contract sweep: it must stay the district's shape
        (no `Student ID`), or the sweep would again pass over a config that cannot run."""
        _create_sd45_inputs(tmp_path)
        headers = list(pd.read_csv(tmp_path / "StudentScheduleDrops.csv", nrows=0).columns)
        assert "Student Number" in headers and "Student ID" not in headers
