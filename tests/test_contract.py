"""SpacesEDU output schema contract tests.

Verifies that the pipeline output for every supported district config always
contains exactly the required SpacesEDU Advanced CSV columns — no missing
columns, no unexpected extras. A failure here means the importer will reject
the file.

Parametrized over ALL 7 SpacesEDU-relevant configs: myedbc (base), sd40myedbc
(CSV files + headerless schedule + ATT--* exclusions), sd48myedbc, sd51myedbc
(plain inheritance + generated emails), sd54myedbc (renamed source files,
withdraw-date-only active detection, surname.firstname emails), sd60myedbc
(Family row_filters, cross-enrollment collapse, sanitized learn60 emails with
derived admission-year, Home-school rostering), sd74myedbc.

Each district's input builder mirrors that district's REAL GDE header shape
(column names verified against the district extracts) with fully synthetic
rows, so the per-district mapping quirks are exercised end-to-end.
``TestDistrictQuirks`` pins the quirk behaviours per district via indirect
parametrization (reusing the module-scoped pipeline run for that district).
"""

from pathlib import Path

import pandas as pd
import pytest

from src.main import main

# ---------------------------------------------------------------------------
# SpacesEDU required column schema
# Derived from the SD74 golden output — these are the exact columns SpacesEDU
# Advanced CSV expects for each entity type.
# ---------------------------------------------------------------------------

SPACESEDU_SCHEMA: dict[str, list[str]] = {
    "Students": [
        "User ID",
        "Student Number",
        "First Name",
        "Last Name",
        "Date of Birth",
        "Grade",
        "EnrollStatus",
        "SchoolCode",
        "Homeroom",
        "PreRegSchoolCode",
        "Preferred First Name",
        "Preferred Last Name",
        "Community Hours",
        "Literacy Test Completed",
        "Email Address",
    ],
    "Staff": ["User ID", "First Name", "Last Name", "Email", "Role", "School ID"],
    "Family": ["First Name", "Last Name", "Email", "Student User ID"],
    "Classes": ["Class ID", "Name", "Grade", "School ID", "Start Date", "End Date"],
    "Enrollments": ["Class ID", "User ID", "Role", "School ID"],
}

VALID_STAFF_ROLES = {"teacher", "administrator"}
VALID_ENROLLMENT_ROLES = {"student", "teacher"}

# ---------------------------------------------------------------------------
# GDE data builders (shared across all district fixtures)
# ---------------------------------------------------------------------------


def _write_student_demographic(path: Path, filename: str) -> None:
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Legal First Name": ["Alice", "Bob", "Charlie"],
            "Legal Surname": ["Smith", "Jones", "Brown"],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10"],
            "Grade": ["3", "10", "12"],
            "School Number": ["100", "200", "200"],
            "Homeroom": ["A1", "C3", "C4"],
            "Previous school number": ["", "", ""],
            "Usual First Name": ["", "", ""],
            "Usual surname": ["", "", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca", "charlie@test.ca"],
            "Enrolment Status": ["Active", "Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    ).to_csv(path / filename, index=False)


def _write_staff(path: Path, filename: str) -> None:
    pd.DataFrame(
        {
            "Teacher ID": ["T001", "T003", "T004"],
            "First Name": ["Jane", "Linda", "Raj"],
            "Last Name": ["Harper", "Liu", "Singh"],
            "Email Address": ["harper@school.ca", "liu@school.ca", "singh@school.ca"],
            "Teaching Staff": ["Y", "Y", "Y"],
            "School Number": ["100", "200", "200"],
        }
    ).to_csv(path / filename, index=False)


def _write_course_info(path: Path, filename: str = "CourseInformation.txt") -> None:
    pd.DataFrame(
        {
            "School Number": ["100", "200", "200"],
            "Course Code": ["HR-3", "MAT10", "ENG12"],
            "Title": ["Homeroom 3", "Math 10", "English 12"],
        }
    ).to_csv(path / filename, index=False)


def _write_base_schedule(path: Path, filename: str, section_col: str = "Section Letter") -> None:
    """The canonical MyEd BC schedule shape shared by the base-like districts."""
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
            section_col: ["A", "A", "A"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    ).to_csv(path / filename, index=False)


def _write_family(path: Path, filename: str, last_name_col: str = "Last Name") -> None:
    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            last_name_col: ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(path / filename, index=False)


def _write_class_info_empty(path: Path, filename: str) -> None:
    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(path / filename, index=False)


# ---------------------------------------------------------------------------
# Per-district input file creation
# ---------------------------------------------------------------------------


def _create_myedbc_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_staff(d, "StaffInformationEnhanced.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd48_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicEnhanced.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd74_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "studentcourseselection.txt", section_col="Section")
    _write_course_info(d)
    _write_family(d, "ParentInformation.txt", last_name_col="Surname")
    _write_class_info_empty(d, "ClassInfoEnhanced.txt")


def _create_sd40_inputs(d: Path) -> None:
    """SD40 (New Westminster): CSV extracts, HEADERLESS schedule, ATT--* exclusions.

    The schedule CSV is written WITHOUT a header row — column names are injected
    at extract time from the ``headers:`` block in sd40myedbc_mapping.yaml, so
    this fixture proves that injection end-to-end. An ATT--AM bookkeeping row
    (MT900) exercises ``excluded_course_codes``.
    """
    # Real SD-40_StudentDemographic.csv header subset (two-L "Enrollment status").
    pd.DataFrame(
        {
            "School number": ["100", "200", "200"],
            "Student number": ["S001", "S002", "S003"],
            "Homeroom": ["A1", "C3", "C4"],
            "Teacher name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
            "Legal surname": ["Smith", "Jones", "Brown"],
            "Legal first name": ["Alice", "Bob", "Charlie"],
            "Usual surname": ["", "", ""],
            "Usual first name": ["", "", ""],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10"],
            "Grade": ["3", "10", "12"],
            "Enrollment status": ["Active", "Active", "Active"],
            "Next school code": ["", "", ""],
            "Student email address": ["", "", ""],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    ).to_csv(d / "SD-40_StudentDemographic.csv", index=False)
    _write_staff(d, "SD-40_StaffInformation.csv")
    # HEADERLESS schedule — the 20 columns of the YAML `headers` block, in file
    # order (dict keys document the positions; header=False omits them on disk).
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 4,
            "School Number": ["100", "200", "200", "200"],
            "Student Number": ["S001", "S002", "S003", "S002"],
            "PEN": ["P001", "P002", "P003", "P002"],
            "Grade": ["3", "10", "12", "10"],
            "Homeroom": ["A1", "C3", "C4", "C3"],
            "Course School Number": ["100", "200", "200", "200"],
            "Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "District Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "Course Title": ["Homeroom 3", "Math 10", "English 12", "AM Attendance"],
            "Short Name": ["HR3", "MA10", "EN12", "ATTAM"],
            "Period": ["1", "2", "3", "4"],
            "Day": ["1", "1", "1", "1"],
            "Semester": ["S1", "S1", "S1", "S1"],
            "Section Letter": ["A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T003"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Liu"],
            "Primary Teacher": ["Y", "Y", "Y", "Y"],
            "Enrolment Status": ["Active", "Active", "Active", "Active"],
        }
    ).to_csv(d / "SD-40_StudentSchedule.csv", index=False, header=False)
    _write_course_info(d, "SD-40_CourseInformation.csv")
    _write_family(d, "SD-40_StudentEmergencyContact.csv")
    # Real SD-40_ClassInformation.csv has NO Master Timetable ID column, so
    # blended detection must fall back to the deduplicated schedule.
    pd.DataFrame(
        {
            "School Number": ["200"],
            "Course Code": ["MAT10"],
            "Teacher Id": ["T003"],
            "Primary Teacher": ["Y"],
            "Section Letter": ["A"],
            "Semester": ["S1"],
            "Term": ["T1"],
            "Day": ["1"],
            "Period": ["2"],
        }
    ).to_csv(d / "SD-40_ClassInformation.csv", index=False)


def _create_sd51_inputs(d: Path) -> None:
    """SD51 (Boundary): plain base inheritance + generated {student number} emails.

    StudentDailyAbsences.txt / StudentPeriodAbsences.txt are intentionally
    absent: the enabled StudentAttendance entity skips on all-empty sources
    (attendance has its own dedicated test module) while the 5 rostering CSVs
    still emit — this pins that a missing attendance drop never blocks rostering.
    """
    _write_student_demographic(d, "StudentDemographicEnhanced.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd54_inputs(d: Path) -> None:
    """SD54 (Bulkley Valley): renamed lowercase files, no enrollment-status column.

    The real SD54 demographic has NO "Enrollment status" column, so active
    detection falls back to the withdraw date (S004 has a past date → dropped).
    Emails are generated as {legal surname}.{usual first name}@sd54.bc.ca. An
    ATT--AM row (MT900) exercises ``excluded_course_codes`` via the schedule's
    "District Course Code" column (SD54's schedule has no plain "Course Code").
    """
    pd.DataFrame(
        {
            "School number": ["100", "200", "200", "200"],
            "Student number": ["S001", "S002", "S003", "S004"],
            "Homeroom": ["A1", "C3", "C4", "C5"],
            "Teacher name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh", "Mr. Singh"],
            "Legal surname": ["Smith", "Jones", "Brown", "White"],
            "Legal first name": ["Alice", "Bob", "Charlie", "Wendy"],
            "Usual surname": ["", "", "", ""],
            "Usual first name": ["Ali", "Rob", "Chuck", "Wen"],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10", "2009-11-30"],
            "Grade": ["3", "10", "12", "11"],
            "Withdraw date": ["", "", "", "2024-09-15"],
            "Teacher ID": ["T001", "T003", "T004", "T004"],
        }
    ).to_csv(d / "StudentDemographicInformation.txt", index=False)
    _write_staff(d, "staffinformation.txt")
    # Real studentschedule.txt shape: "Student ID", District Course Code only,
    # "Semester/Term" — plus the ATT--AM bookkeeping row.
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 4,
            "School Number": ["100", "200", "200", "200"],
            "Student ID": ["S001", "S002", "S003", "S002"],
            "Grade": ["3", "10", "12", "10"],
            "Homeroom": ["A1", "C3", "C4", "C3"],
            "District Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "Course Title": ["Homeroom 3", "Math 10", "English 12", "AM Attendance"],
            "Period": ["1", "2", "3", "4"],
            "Day": ["1", "1", "1", "1"],
            "Semester/Term": ["S1", "S1", "S1", "S1"],
            "Section Letter": ["A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T003"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Liu"],
            "Primary Teacher": ["Y", "Y", "Y", "Y"],
        }
    ).to_csv(d / "studentschedule.txt", index=False)
    _write_course_info(d, "courseinformation.txt")
    # S004's contact must be dropped with the student (active-roster filter).
    pd.DataFrame(
        {
            "Student Number": ["S001", "S004"],
            "First Name": ["John", "Wanda"],
            "Last Name": ["Smith", "White"],
            "Email Address": ["john@mail.com", "wanda@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformationEnhanced.txt", index=False)
    _write_class_info_empty(d, "classinformationenhanced.txt")


def _create_sd60_inputs(d: Path) -> None:
    """SD60 (Peace River North): the most-overridden district config.

    Exercises: Family ``row_filters`` (Parent Auth / Guardian = Y), generated
    learn60 emails (sanitize + derived admission-year yy), rostering under
    "Home school number", cross-enrollment collapse (S002 Active at schools
    200 AND 300 → one Students row, enrollments preserved at both), the base
    active_values dropping "Active No Primary" (S005), and ATT--AM exclusion.
    """
    pd.DataFrame(
        {
            "School number": ["200", "300", "100", "210", "200"],
            "Student number": ["S002", "S002", "S001", "S003", "S005"],
            "Homeroom": ["C3", "C3", "A1", "C4", "C5"],
            "Teacher name": ["Mrs. Liu", "Mrs. Liu", "Ms. Harper", "Mr. Singh", "Mr. Singh"],
            "Legal surname": ["Jones", "Jones", "Smith", "O'Brien", "Turner"],
            "Legal first name": ["Bob", "Bob", "Alice", "Mary-Jane", "Eve"],
            "Usual surname": ["", "", "", "", ""],
            "Usual first name": ["", "", "", "", ""],
            "Date of birth": ["2009-06-20", "2009-06-20", "2010-01-15", "2011-03-10", "2010-08-01"],
            "Grade": ["10", "10", "3", "12", "11"],
            "Enrollment status": ["Active", "Active", "Active", "Active", "Active No Primary"],
            "Admission date": ["2014-05-01", "2014-05-01", "2015-09-08", "2016-01-15", "2017-09-05"],
            "Home school number": ["200", "200", "100", "200", "200"],
            "Next school code": ["", "", "", "", ""],
            "Student email address": ["", "", "", "", ""],
            "Teacher ID": ["T003", "T003", "T001", "T004", "T004"],
        }
    ).to_csv(d / "Student_demo_enh.txt", index=False)
    _write_staff(d, "StaffInformation.txt")
    # Real StudentCourseSelection.txt shape: "Course Code" + "Section" (no
    # Section Letter, no Primary Teacher flag). S002 has classes at BOTH its
    # schools; the ATT--AM row (MT900) must be excluded.
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 5,
            "School Number": ["100", "200", "300", "200", "200"],
            "Student Number": ["S001", "S002", "S002", "S003", "S002"],
            "Grade": ["3", "10", "10", "12", "10"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Singh", "Liu"],
            "Semester": ["S1", "S1", "S1", "S1", "S1"],
            "Course Code": ["HR-3", "MAT10", "SCI10", "ENG12", "ATT--AM"],
            "Section": ["A", "A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT202", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T004", "T003"],
        }
    ).to_csv(d / "StudentCourseSelection.txt", index=False)
    pd.DataFrame(
        {
            "School Number": ["100", "200", "300", "200"],
            "Course Code": ["HR-3", "MAT10", "SCI10", "ENG12"],
            "Title": ["Homeroom 3", "Math 10", "Science 10", "English 12"],
        }
    ).to_csv(d / "CourseInformation.txt", index=False)
    # row_filters keep only Parent Auth / Guardian = Y (the N contact drops).
    pd.DataFrame(
        {
            "Student Number": ["S001", "S001"],
            "First Name": ["John", "Nana"],
            "Last Name": ["Smith", "Elder"],
            "Email Address": ["john@mail.com", "nana@mail.com"],
            "Parent Auth / Guardian": ["Y", "N"],
        }
    ).to_csv(d / "EmergencyEnhanced.txt", index=False)
    _write_class_info_empty(d, "ClassInformation.txt")


_DISTRICT_SETUP = {
    "myedbc": _create_myedbc_inputs,
    "sd40myedbc": _create_sd40_inputs,
    "sd48myedbc": _create_sd48_inputs,
    "sd51myedbc": _create_sd51_inputs,
    "sd54myedbc": _create_sd54_inputs,
    "sd60myedbc": _create_sd60_inputs,
    "sd74myedbc": _create_sd74_inputs,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=list(_DISTRICT_SETUP.keys()),
    ids=list(_DISTRICT_SETUP.keys()),
    scope="module",
)
def district_output(request, tmp_path_factory):
    """Run the pipeline for one district and return (sis_type, output_dir).

    This is a MODULE-scoped fixture, so it runs during setup BEFORE the function-scoped
    ``isolated_user_profile`` autouse fixture is active. ``run_pipeline`` now writes a
    run record to the store via ``paths.user_data_dir()``, so redirect that seam into
    this fixture's own tmp dir here too — otherwise a module-scoped run would write the
    REAL ``history.db`` (the isolation canary would catch it). The CSV output goes to the
    explicit ``output_dir``, so the SpacesEDU schema / SD74 snapshot is unaffected.
    """
    sis = request.param
    d = tmp_path_factory.mktemp(f"contract_{sis}")
    input_dir = d / "input"
    output_dir = d / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    _DISTRICT_SETUP[sis](input_dir)
    mp = pytest.MonkeyPatch()
    mp.setattr("src.utils.paths.user_data_dir", lambda: d / ".districtsync")
    try:
        main(sis, str(input_dir), str(output_dir))
    finally:
        mp.undo()
    return sis, output_dir


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOutputSchemaContract:
    def test_all_five_output_files_exist(self, district_output):
        _, out = district_output
        for entity in SPACESEDU_SCHEMA:
            assert (out / f"{entity}.csv").exists(), f"Missing {entity}.csv"

    @pytest.mark.parametrize("entity", list(SPACESEDU_SCHEMA.keys()))
    def test_required_columns_present(self, district_output, entity):
        sis, out = district_output
        df = pd.read_csv(out / f"{entity}.csv", encoding="utf-8-sig")
        required = SPACESEDU_SCHEMA[entity]
        missing = [c for c in required if c not in df.columns]
        assert not missing, f"[{sis}] {entity}.csv is missing required columns: {missing}"

    @pytest.mark.parametrize("entity", list(SPACESEDU_SCHEMA.keys()))
    def test_no_extra_columns(self, district_output, entity):
        sis, out = district_output
        df = pd.read_csv(out / f"{entity}.csv", encoding="utf-8-sig")
        required = SPACESEDU_SCHEMA[entity]
        extras = [c for c in df.columns if c not in required]
        assert not extras, f"[{sis}] {entity}.csv has unexpected extra columns: {extras}"

    def test_staff_role_values(self, district_output):
        sis, out = district_output
        df = pd.read_csv(out / "Staff.csv", encoding="utf-8-sig")
        bad = set(df["Role"].dropna().unique()) - VALID_STAFF_ROLES
        assert not bad, f"[{sis}] Staff.csv has invalid Role values: {bad}"

    def test_enrollment_role_values(self, district_output):
        sis, out = district_output
        df = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig")
        bad = set(df["Role"].dropna().unique()) - VALID_ENROLLMENT_ROLES
        assert not bad, f"[{sis}] Enrollments.csv has invalid Role values: {bad}"

    def test_class_ids_contain_school_year(self, district_output):
        sis, out = district_output
        classes = pd.read_csv(out / "Classes.csv", encoding="utf-8-sig")
        ids = classes["Class ID"].dropna().astype(str)
        assert any("_20" in cid for cid in ids), f"[{sis}] No Class ID contains a school year suffix"

    def test_students_grade_is_ceds_format(self, district_output):
        sis, out = district_output
        students = pd.read_csv(out / "Students.csv", encoding="utf-8-sig", dtype=str)
        grades = students["Grade"].dropna()
        # CEDS grades are 2-char strings (e.g. "03", "KG") or special values
        invalid = [g for g in grades if len(str(g)) > 5]
        assert not invalid, f"[{sis}] Students.csv has unexpectedly long Grade values: {invalid}"

    def test_every_enrollment_class_exists_in_classes(self, district_output):
        """Every Class ID referenced in Enrollments.csv must exist in Classes.csv.

        Regression guard for the blended-class orphan bug: detected blended
        classes must always be written to Classes.csv before Enrollments
        references them.
        """
        sis, out = district_output
        classes = pd.read_csv(out / "Classes.csv", encoding="utf-8-sig", dtype=str)
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        class_ids = set(classes["Class ID"].dropna().astype(str))
        enrolled_ids = set(enrollments["Class ID"].dropna().astype(str))
        orphans = enrolled_ids - class_ids
        assert not orphans, (
            f"[{sis}] {len(orphans)} Class IDs in Enrollments.csv are not defined in Classes.csv: {sorted(orphans)[:5]}"
        )

    def test_no_empty_user_ids_in_enrollments(self, district_output):
        """Every Enrollments row must have a non-empty User ID.

        Regression guard for SD40 FY2026: blended detection was grouping
        teacherless sections into fake blends and emitting teacher rows
        with empty User ID, which the partner's pre-upload validator
        rejects with 'Missing required Field:userId'.
        """
        sis, out = district_output
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        user_ids = enrollments["User ID"].fillna("").astype(str).str.strip().str.lower()
        blank = enrollments[(user_ids == "") | (user_ids == "nan")]
        assert blank.empty, (
            f"[{sis}] {len(blank)} Enrollments rows have empty/nan User ID. Sample: {blank.head(3).to_dict('records')}"
        )

    def test_no_empty_class_ids_in_enrollments(self, district_output):
        """Every Enrollments row must have a non-empty Class ID."""
        sis, out = district_output
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        class_ids = enrollments["Class ID"].fillna("").astype(str).str.strip().str.lower()
        blank = enrollments[(class_ids == "") | (class_ids == "nan")]
        assert blank.empty, f"[{sis}] {len(blank)} Enrollments rows have empty/nan Class ID"


# ---------------------------------------------------------------------------
# Per-district quirk pins
#
# Each test indirect-parametrizes `district_output` to ONE district, so pytest
# reuses that district's module-scoped pipeline run from the schema tests above
# (same fixture param → same cached instance; no extra pipeline executions).
# ---------------------------------------------------------------------------


def _read_output(out: Path, entity: str) -> pd.DataFrame:
    return pd.read_csv(out / f"{entity}.csv", encoding="utf-8-sig", dtype=str)


def _assert_mt_excluded(out: Path, mt_id: str) -> None:
    """No Classes/Enrollments row may reference the excluded section's MT ID."""
    for entity in ("Classes", "Enrollments"):
        ids = _read_output(out, entity)["Class ID"].dropna().astype(str)
        offenders = [cid for cid in ids if cid.startswith(f"{mt_id}_")]
        assert not offenders, f"{entity}.csv contains excluded ATT section {mt_id}: {offenders}"


@pytest.mark.integration
class TestDistrictQuirks:
    # ---- SD40: headerless schedule + ATT exclusions + generated emails ----

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_headerless_schedule_columns_injected(self, district_output):
        """The header-free schedule CSV must load via the YAML `headers` block —
        proven by subject classes keyed on its Master Timetable ID column."""
        _, out = district_output
        ids = set(_read_output(out, "Classes")["Class ID"].dropna())
        assert any(cid.startswith("MT002_") for cid in ids), f"Expected MT002_<year> class, got {sorted(ids)}"
        assert any(cid.startswith("MT003_") for cid in ids)

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_att_bookkeeping_sections_excluded(self, district_output):
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_generated_newwestschools_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "s001@newwestschools.ca",
            "s002@newwestschools.ca",
            "s003@newwestschools.ca",
        }

    # ---- SD51: plain inheritance + generated emails ----

    @pytest.mark.parametrize("district_output", ["sd51myedbc"], indirect=True)
    def test_sd51_generated_sd51_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "s001@sd51.bc.ca",
            "s002@sd51.bc.ca",
            "s003@sd51.bc.ca",
        }

    # ---- SD54: withdraw-date-only active detection + surname.firstname emails ----

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_generated_surname_dot_usual_first_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "smith.ali@sd54.bc.ca",
            "jones.rob@sd54.bc.ca",
            "brown.chuck@sd54.bc.ca",
        }

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_withdraw_date_fallback_drops_student(self, district_output):
        """SD54's demographic has no status column — a past withdraw date must
        drop the student via the date-only fallback."""
        _, out = district_output
        user_ids = set(_read_output(out, "Students")["User ID"])
        assert "S004" not in user_ids
        assert {"S001", "S002", "S003"} == user_ids

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_family_filtered_to_active_roster(self, district_output):
        """The withdrawn student's contact must not ship (zero-orphan invariant)."""
        _, out = district_output
        family = _read_output(out, "Family")
        assert set(family["Student User ID"]) == {"S001"}

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_att_bookkeeping_sections_excluded(self, district_output):
        """SD54's schedule has no plain 'Course Code' column — exclusion must
        work via 'District Course Code'."""
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    # ---- SD60: row_filters + learn60 emails + home-school rostering + collapse ----

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_family_row_filter_keeps_guardians_only(self, district_output):
        _, out = district_output
        family = _read_output(out, "Family")
        assert set(family["Email"]) == {"john@mail.com"}, "non-guardian (N) contact must be dropped"
        assert len(family) == 1

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_generated_learn60_emails_sanitized_with_admission_yy(self, district_output):
        """firstlast + 2-digit admission year @learn60.ca; sanitize strips the
        apostrophe/hyphen from Mary-Jane O'Brien."""
        _, out = district_output
        students = _read_output(out, "Students")
        emails = dict(zip(students["User ID"], students["Email Address"]))
        assert emails == {
            "S001": "alicesmith15@learn60.ca",
            "S002": "bobjones14@learn60.ca",
            "S003": "maryjaneobrien16@learn60.ca",
        }

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_cross_enrollment_collapses_to_one_row_keeping_both_schools(self, district_output):
        """S002 is Active at schools 200 AND 300: ONE Students row (home school),
        but enrollments preserved at BOTH schools."""
        _, out = district_output
        students = _read_output(out, "Students")
        s002 = students[students["User ID"] == "S002"]
        assert len(s002) == 1
        assert s002["SchoolCode"].iloc[0] == "200"
        enrollments = _read_output(out, "Enrollments")
        s002_classes = set(enrollments[enrollments["User ID"] == "S002"]["Class ID"])
        assert any(cid.startswith("MT002_") for cid in s002_classes), "home-school class lost"
        assert any(cid.startswith("MT202_") for cid in s002_classes), "cross-school class lost"

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_rosters_under_home_school_number(self, district_output):
        """S003 attends school 210 but the Home school number is 200 —
        SchoolCode must be the home school."""
        _, out = district_output
        students = _read_output(out, "Students")
        assert students[students["User ID"] == "S003"]["SchoolCode"].iloc[0] == "200"

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_active_no_primary_dropped(self, district_output):
        _, out = district_output
        assert "S005" not in set(_read_output(out, "Students")["User ID"])

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_att_bookkeeping_sections_excluded(self, district_output):
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_class_name_without_primary_teacher_flag(self, district_output):
        """SD60's schedule has no Primary-Teacher flag; class names still carry
        the teacher's last name and the course-info Title.

        Deliberately does NOT assert the section letter: ``to_raw_dict`` emits
        the Name config with spaced keys ("section letter") while
        ``ClassTransformer._assign_class_names`` looks up underscore keys
        ("section_letter"), so the configured section column is currently
        ignored and SD60 names omit "(A)". The prefix/suffix assertion holds
        both today and after that key mismatch is fixed.
        """
        _, out = district_output
        classes = _read_output(out, "Classes")
        name = classes[classes["Class ID"].astype(str).str.startswith("MT002_")]["Name"].iloc[0]
        assert name.startswith("Liu Math 10"), name
        assert name.endswith("2026"), name
