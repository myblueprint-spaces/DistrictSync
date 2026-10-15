"""SD45 (West Vancouver) over the files the district sends, under their real header names.

History. The 2026-09-30 migration kept the district's old ``StudentScheduleDrops.csv`` as the
schedule. Its first real run (DistrictSync 3.27.0, 2026-10-09) stopped at Enrollments: that file
has ``Student Number`` and no ``Student ID``, and v3.27.1 pointed the schedule's student there.
On 2026-10-14 the MyEd BC GDE spec showed that Student Schedule Drops is "Drops only"
(v1.17 §2.42 / v1.8 §2.49: ``Change Date`` = the date the section was DROPPED). Every subject
enrollment built from it was a dropped section. ``sd45myedbc`` now reads the standard Student
Schedule GDE (§2.41 / §2.48), exported as ``StudentSchedule.csv``. That is the base's own shape,
so every schedule column is inherited and the Drops-only overrides are gone.

These tests feed ``sd45myedbc`` the drop it must now send: six files, each under its spec or
district header, and NO ``EmergencyContactInfoEnhanced.csv`` (the district sends no family data).
``ClassInformationEnhanced.csv`` (sent since 2026-10-13) feeds co-teachers; blended detection
stays off. A drop that still carries only the Drops extract stops the night, and that is pinned
too. Every row is synthetic (fake ids and names).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config.loader import load_config
from src.etl.errors import IncompleteInputError
from src.etl.outcomes import CLASS_INFORMATION_ROLE, OutcomeKind, OutcomeReason
from src.etl.pipeline import run_pipeline
from src.history.store import read_run_records
from tests.test_contract import (
    _SUPPORT_STAFF_ID,
    _create_sd45_inputs,
    _write_course_info,
    _write_staff,
    _write_student_demographic,
)

SD45 = "sd45myedbc"
SCHEDULE_FILE = "StudentSchedule.csv"
DROPS_FILE = "StudentScheduleDrops.csv"
CLASS_INFORMATION_FILE = "ClassInformationEnhanced.csv"
EMERGENCY_CONTACTS_FILE = "EmergencyContactInfoEnhanced.csv"

#: MyEd BC's standard Student Schedule GDE header, in order (spec v1.17 §2.41 / v1.8 §2.48).
STANDARD_SCHEDULE_HEADERS = [
    "School Year",
    "School Number",
    "Student ID",
    "PEN",
    "Grade",
    "Homeroom",
    "Student Home School ID",
    "District Course Code",
    "School Course Code",
    "Course Title",
    "Short Name",
    "Period",
    "Day",
    "Semester / Term",
    "Section Letter",
    "Master Timetable ID",
    "Teacher ID",
    "Teacher Name",
    "Primary Teacher",
    "Status",
]

#: MyEd BC's fixed Student Schedule DROPS header, in order (spec §2.42 / §2.49). The mapping
#: must NOT read this file.
DROPS_SCHEDULE_HEADERS = [
    "School Year",
    "School Number",
    "Student Number",
    "Personal Education Number",
    "Student Legal Last Name",
    "Student Legal First Name",
    "Grade",
    "Teacher Name",
    "Semester",
    "Course Code",
    "Alternate Course Code",
    "Section",
    "Type",
    "Change Date",
    "Master Timetable ID",
    "DL Start Date",
    "DL Course Active Date",
    "DL Completion Date",
    "Teacher ID",
]

#: The district's own ``ClassInformationEnhanced.csv`` header, in order.
REAL_CLASS_INFORMATION_HEADERS = [
    "School Number",
    "Course Code",
    "Teacher Id",
    "Primary Teacher",
    "Section Letter",
    "Semester",
    "Term",
    "Day",
    "Period",
    "Master Timetable ID",
    "Class Identifier",
    "Schedule Expression",
    "Max",
    "Unrotated Schedule",
]

#: The co-teacher the Class Information export names for homeroom ``A1`` at school 100 (the
#: grade-3 pupil S001's homeroom). This is the fixture's support-staff member: their staff record
#: states no teaching role and they hold no timetable section, so this file is their only evidence.
_HOMEROOM_COTEACHER = _SUPPORT_STAFF_ID

#: A section only the Drops extract names: S002 dropped it. It must never be enrolled.
_DROPPED_MTID = "MT900"


def _header_line(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def _write_rows(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    """A comma-delimited UTF-8 CSV under EXACTLY ``headers``."""
    assert all(len(row) == len(headers) for row in rows)
    lines = [",".join(headers), *(",".join(row) for row in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_standard_schedule(path: Path) -> None:
    """Three synthetic timetable rows under the spec's Student Schedule header."""
    # Year, School, Student ID, PEN, Grade, Homeroom, Home School, District Course, School Course,
    # Title, Short, Period, Day, Semester / Term, Section Letter, MTID, Teacher ID, Teacher Name,
    # Primary Teacher, Status
    rows = [
        ["2025/2026", "100", "S001", "900000001", "3", "A1", "100", "HR-3", "HR-3", "Homeroom 3", "HR3"]
        + ["1", "1", "S1", "A1", "MT001", "T001", "Harper", "Y", "Active"],
        ["2025/2026", "200", "S002", "900000002", "10", "C3", "200", "MAT10", "MAT10", "Math 10", "MA10"]
        + ["2", "1", "S1", "A", "MT002", "T003", "Liu", "Y", "Active"],
        ["2025/2026", "200", "S003", "900000003", "12", "C4", "200", "ENG12", "ENG12", "English 12", "EN12"]
        + ["3", "1", "S2", "B", "MT003", "T004", "Singh", "Y", "Active"],
    ]
    _write_rows(path / SCHEDULE_FILE, STANDARD_SCHEDULE_HEADERS, rows)


def _write_drops_schedule(path: Path) -> None:
    """One synthetic DROPPED section (Change Date populated, as on every real Drops row)."""
    rows = [
        ["2025/2026", "200", "S002", "900000002", "Jones", "Bob", "10", "Singh", "S1", "ENG12", "", "C"]
        + ["Drop", "2026-09-15", _DROPPED_MTID, "", "", "", "T004"],
    ]
    _write_rows(path / DROPS_FILE, DROPS_SCHEDULE_HEADERS, rows)


def _write_real_class_information(path: Path) -> None:
    """Synthetic ClassInformationEnhanced.csv rows under the district's own header names.

    * ``MT001``'s primary teacher, plus a SECOND primary-teacher row for the same homeroom section
      (``A1``, school 100). That co-teacher appears only in this file.
    * ``MT002`` with a non-primary row (flag ``N``) that must link nothing.
    * ``MT003``, whose section letter names no homeroom. Nothing links while blended detection
      is off.
    """
    # School, Course, Teacher Id, Primary, Section Letter, Semester, Term, Day, Period, MTID,
    # Class Identifier, Schedule Expression, Max, Unrotated Schedule
    rows = [
        ["100", "HR-3", "T001", "Y", "A1", "S1", "T1", "1", "1", "MT001", "HR-3-A1", "1(A)", "30", "1(A)"],
        ["100", "HR-3", _HOMEROOM_COTEACHER, "Y", "A1", "S1", "T1", "1", "1", "MT001", "HR-3-A1", "1(A)"]
        + ["30", "1(A)"],
        ["200", "MAT10", "T003", "Y", "A", "S1", "T1", "1", "2", "MT002", "MAT10-A", "2(A)", "30", "2(A)"],
        ["200", "MAT10", "T004", "N", "A", "S1", "T1", "1", "2", "MT002", "MAT10-A", "2(A)", "30", "2(A)"],
        ["200", "ENG12", "T004", "Y", "B", "S2", "T1", "1", "3", "MT003", "ENG12-B", "3(A)", "30", "3(A)"],
    ]
    _write_rows(path / CLASS_INFORMATION_FILE, REAL_CLASS_INFORMATION_HEADERS, rows)


def _inputs(
    tmp_path: Path, *, schedule: bool = True, drops: bool = False, class_information: bool = True
) -> tuple[Path, Path]:
    """The drop SD45 must send: NO emergency-contact file (the district sends no family data)."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    _write_student_demographic(inp, "StudentDemographicEnhanced.csv")
    _write_staff(inp, "StaffInformationEnhanced.csv")
    _write_course_info(inp, "CourseInformationEnhanced.csv")
    if schedule:
        _write_standard_schedule(inp)
    if drops:
        _write_drops_schedule(inp)
    if class_information:
        _write_real_class_information(inp)
    return inp, out


def _enrollments(out: Path) -> pd.DataFrame:
    return pd.read_csv(out / "Enrollments.csv", dtype=str, keep_default_na=False)


def test_the_fixture_is_the_spec_shape(tmp_path: Path) -> None:
    """The guard against the fixture drifting from the spec and district headers."""
    inp, _out = _inputs(tmp_path, drops=True)
    assert _header_line(inp / SCHEDULE_FILE) == STANDARD_SCHEDULE_HEADERS
    assert _header_line(inp / DROPS_FILE) == DROPS_SCHEDULE_HEADERS
    assert _header_line(inp / CLASS_INFORMATION_FILE) == REAL_CLASS_INFORMATION_HEADERS
    assert not (inp / EMERGENCY_CONTACTS_FILE).exists()


@pytest.mark.integration
def test_enrollments_build_on_the_standard_schedules_student_id(tmp_path: Path) -> None:
    inp, out = _inputs(tmp_path)
    result = run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success" and record["error_category"] == "none"
    kinds = {o.entity: o.kind for o in result.entity_outcomes}
    assert kinds["Enrollments"] is OutcomeKind.BUILT
    assert kinds["Classes"] is OutcomeKind.BUILT

    enrollments = _enrollments(out)
    subject = enrollments[enrollments["Class ID"].str.startswith(("MT002_", "MT003_"))]
    # Both timetable students (grades 10 and 12) are enrolled as students, each in their own
    # section, with their teachers beside them.
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
def test_class_names_carry_section_letter_and_primary_teacher(tmp_path: Path) -> None:
    """The standard schedule carries every name part the base reads (Section Letter, Teacher Name,
    Primary Teacher), so names are complete and Classes and Enrollments record no notes."""
    inp, out = _inputs(tmp_path)
    run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success"
    assert all("notes" not in record["entity_outcomes"][e] for e in ("Classes", "Enrollments"))
    classes = pd.read_csv(out / "Classes.csv", dtype=str, keep_default_na=False)
    names = dict(zip(classes["Class ID"].str.split("_").str[0], classes["Name"], strict=True))
    assert names["MT002"].startswith("Liu Math 10 (A) ")
    assert names["MT003"].startswith("Singh English 12 (B) ")


@pytest.mark.integration
def test_a_drop_with_only_the_drops_extract_stops_the_night_naming_the_schedule(tmp_path: Path) -> None:
    """The drop SD45 sent on 2026-10-13 has the Drops extract and no standard schedule. The night
    stops at the input gate and names ``StudentSchedule.csv``, so dropped sections are never
    rostered."""
    inp, out = _inputs(tmp_path, schedule=False, drops=True)
    with pytest.raises(IncompleteInputError) as raised:
        run_pipeline(SD45, str(inp), str(out))
    assert raised.value.missing == (SCHEDULE_FILE,)
    assert read_run_records()[0]["error_category"] == "incomplete_input"
    assert not (out / "Enrollments.csv").exists()


@pytest.mark.integration
def test_the_drops_extract_is_ignored_when_sent_beside_the_schedule(tmp_path: Path) -> None:
    """A section only the Drops extract names (S002 dropped it) is never enrolled or classed. The
    positive twin is the standard schedule's own section for S002, which still is."""
    inp, out = _inputs(tmp_path, drops=True)
    run_pipeline(SD45, str(inp), str(out))

    enrollments = _enrollments(out)
    assert not enrollments["Class ID"].str.startswith(f"{_DROPPED_MTID}_").any()
    mt002 = enrollments[enrollments["Class ID"].str.startswith("MT002_")]
    assert ("S002", "student") in set(zip(mt002["User ID"], mt002["Role"], strict=True))
    classes = pd.read_csv(out / "Classes.csv", dtype=str, keep_default_na=False)
    assert not classes["Class ID"].str.startswith(f"{_DROPPED_MTID}_").any()


@pytest.mark.integration
def test_the_missing_emergency_contact_file_only_leaves_family_out(tmp_path: Path) -> None:
    """SD45 sends no family data. Family is EMPTY (no Family.csv, a standing warning) and the night
    succeeds with every other file delivered. Family is the isolatable entity, so this never stops
    the night."""
    inp, out = _inputs(tmp_path)
    result = run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success" and record["error_category"] == "none"
    outcomes = {o.entity: o for o in result.entity_outcomes}
    assert (outcomes["Family"].kind, outcomes["Family"].reason) == (
        OutcomeKind.EMPTY,
        OutcomeReason.SOURCE_FILES_EMPTY,
    )
    for entity in ("Students", "Staff", "Classes", "Enrollments"):
        assert outcomes[entity].kind is OutcomeKind.BUILT
    assert {p.name for p in out.glob("*.csv")} == {"Students.csv", "Staff.csv", "Classes.csv", "Enrollments.csv"}


@pytest.mark.integration
def test_class_information_enhanced_links_the_homeroom_coteacher(tmp_path: Path) -> None:
    """The co-teacher who appears only in ClassInformationEnhanced.csv (Primary Teacher = Y on
    homeroom `A1`, school 100) is enrolled as a teacher of that homeroom class and published on
    Staff. The non-primary row links nothing, and blended detection stays off (no BLENDED class)."""
    inp, out = _inputs(tmp_path)
    run_pipeline(SD45, str(inp), str(out))

    enrollments = _enrollments(out)
    homeroom = enrollments[enrollments["Class ID"].str.startswith("100_A1_")]
    pairs = set(zip(homeroom["User ID"], homeroom["Role"], strict=True))
    assert {(_HOMEROOM_COTEACHER, "teacher"), ("S001", "student")} <= pairs
    # The `N` row (T004 on MT002) links nothing: MT002's teachers come from the schedule alone.
    mt002 = enrollments[enrollments["Class ID"].str.startswith("MT002_")]
    assert set(mt002.loc[mt002["Role"] == "teacher", "User ID"]) == {"T003"}
    classes = pd.read_csv(out / "Classes.csv", dtype=str, keep_default_na=False)
    assert not classes["Class ID"].str.startswith("BLENDED_").any()
    staff = pd.read_csv(out / "Staff.csv", dtype=str, keep_default_na=False)
    assert _HOMEROOM_COTEACHER in set(staff["User ID"])


@pytest.mark.integration
def test_twin_without_class_information_the_coteacher_is_left_out_and_the_night_still_runs(
    tmp_path: Path,
) -> None:
    """The twin of the test above. The same drop WITHOUT ClassInformationEnhanced.csv still runs
    (the file stays optional while blended detection is off). It records the standing co-teacher
    note and neither enrolls nor publishes the co-teacher."""
    inp, out = _inputs(tmp_path, class_information=False)
    run_pipeline(SD45, str(inp), str(out))

    record = read_run_records()[0]
    assert record["status"] == "success" and record["error_category"] == "none"
    assert record["entity_outcomes"]["Enrollments"]["notes"] == {"coteacher_source_unusable": 1}
    assert _HOMEROOM_COTEACHER not in set(_enrollments(out)["User ID"])
    staff = pd.read_csv(out / "Staff.csv", dtype=str, keep_default_na=False)
    assert _HOMEROOM_COTEACHER not in set(staff["User ID"])


class TestTheOverlay:
    def test_sd45_reads_the_standard_schedule_and_never_the_drops_extract(self) -> None:
        mappings = load_config(SD45).to_raw_dict()["mappings"]
        for entity in ("Classes", "Enrollments"):
            assert mappings[entity]["source_files"]["student_schedule"] == SCHEDULE_FILE
        listed = {f for cfg in mappings.values() for f in (cfg.get("source_files") or {}).values()}
        assert DROPS_FILE not in listed

    def test_every_schedule_column_is_inherited_from_the_base(self) -> None:
        """No Drops-only override survives: the schedule's student is `Student ID` and class
        names read `Section Letter` + `Primary Teacher`, exactly as the base does."""
        sd45 = load_config(SD45).to_raw_dict()["mappings"]
        base = load_config("myedbc").to_raw_dict()["mappings"]
        assert sd45["Enrollments"]["field_map"] == base["Enrollments"]["field_map"]
        assert sd45["Classes"]["field_map"] == base["Classes"]["field_map"]
        assert sd45["Enrollments"]["field_map"]["User ID"]["student_id_col"] == "Student ID"

    def test_sd45_lists_class_information_enhanced_with_blended_detection_off(self) -> None:
        raw = load_config(SD45).to_raw_dict()
        assert raw["mappings"]["Classes"]["source_files"][CLASS_INFORMATION_ROLE] == CLASS_INFORMATION_FILE
        assert raw["global_config"]["blended_classes"] is False
        # Family keeps its listing: a drop without the file only leaves Family out.
        assert raw["mappings"]["Family"]["source_files"]["emergency_contacts"] == EMERGENCY_CONTACTS_FILE

    def test_the_base_still_lists_its_own_files(self) -> None:
        sources = load_config("myedbc").to_raw_dict()["mappings"]["Classes"]["source_files"]
        assert (sources["student_schedule"], sources[CLASS_INFORMATION_ROLE]) == (
            "StudentSchedule.txt",
            "ClassInformationEnh.txt",
        )

    def test_the_shared_contract_fixture_is_the_standard_schedule(self, tmp_path: Path) -> None:
        """`_create_sd45_inputs` feeds the whole contract sweep, so it must write the file the
        mapping reads and never the Drops extract."""
        _create_sd45_inputs(tmp_path)
        assert (tmp_path / SCHEDULE_FILE).exists() and not (tmp_path / DROPS_FILE).exists()
