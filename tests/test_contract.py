"""SpacesEDU output schema contract tests.

Verifies that the pipeline output for every supported district config always
contains exactly the required SpacesEDU Advanced CSV columns — no missing
columns, no unexpected extras. A failure here means the importer will reject
the file.

Parametrized over: myedbc (base), sd48myedbc, sd74myedbc.
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


def _write_course_info(path: Path) -> None:
    pd.DataFrame(
        {
            "School Number": ["100", "200", "200"],
            "Course Code": ["HR-3", "MAT10", "ENG12"],
            "Title": ["Homeroom 3", "Math 10", "English 12"],
        }
    ).to_csv(path / "CourseInformation.txt", index=False)


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
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
            "Section Letter": ["A", "A", "A"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    ).to_csv(d / "StudentSchedule.txt", index=False)
    _write_course_info(d)
    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformation.txt", index=False)
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd48_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicEnhanced.txt")
    _write_staff(d, "StaffInformation.txt")
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
            "Section Letter": ["A", "A", "A"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    ).to_csv(d / "StudentSchedule.txt", index=False)
    _write_course_info(d)
    pd.DataFrame(
        {"Student Number": ["S001"], "First Name": ["John"], "Last Name": ["Smith"], "Email Address": ["john@mail.com"]}
    ).to_csv(d / "EmergencyContactInformation.txt", index=False)
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd74_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_staff(d, "StaffInformation.txt")
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
            "Section": ["A", "A", "A"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    ).to_csv(d / "studentcourseselection.txt", index=False)
    _write_course_info(d)
    pd.DataFrame(
        {"Student Number": ["S001"], "First Name": ["John"], "Surname": ["Smith"], "Email Address": ["john@mail.com"]}
    ).to_csv(d / "ParentInformation.txt", index=False)
    _write_class_info_empty(d, "ClassInfoEnhanced.txt")


_DISTRICT_SETUP = {
    "myedbc": _create_myedbc_inputs,
    "sd48myedbc": _create_sd48_inputs,
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
