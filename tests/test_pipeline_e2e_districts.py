"""District-specific end-to-end pipeline tests.

Verifies that each district config (sd48, sd74) loads correctly and
produces all 5 expected output CSVs from synthetic GDE files with the
file names that each district uses.
"""

import pandas as pd
import pytest

from src.main import main

# ---------------------------------------------------------------------------
# Shared GDE data builders
# ---------------------------------------------------------------------------

def _write_student_demographic(path, filename):
    pd.DataFrame({
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
    }).to_csv(path / filename, index=False)


def _write_staff(path, filename):
    pd.DataFrame({
        "Teacher ID": ["T001", "T003", "T004"],
        "First Name": ["Jane", "Linda", "Raj"],
        "Last Name": ["Harper", "Liu", "Singh"],
        "Email Address": ["harper@school.ca", "liu@school.ca", "singh@school.ca"],
        "Teaching Staff": ["Y", "Y", "Y"],
        "School Number": ["100", "200", "200"],
    }).to_csv(path / filename, index=False)


def _write_schedule(path):
    pd.DataFrame({
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
    }).to_csv(path / "StudentSchedule.txt", index=False)


def _write_course_info(path):
    pd.DataFrame({
        "School Number": ["100", "200", "200"],
        "Course Code": ["HR-3", "MAT10", "ENG12"],
        "Title": ["Homeroom 3", "Math 10", "English 12"],
    }).to_csv(path / "CourseInformation.txt", index=False)


def _write_emergency_contacts(path):
    pd.DataFrame({
        "Student Number": ["S001"],
        "First Name": ["John"],
        "Last Name": ["Smith"],
        "Email Address": ["john@mail.com"],
    }).to_csv(path / "EmergencyContactInformation.txt", index=False)


def _write_class_info_enh(path):
    pd.DataFrame(columns=[
        "School Number", "Teacher ID", "Master Timetable ID",
        "Term", "Semester", "Day", "Period",
    ]).to_csv(path / "ClassInformationEnh.txt", index=False)


EXPECTED_OUTPUTS = ["Students.csv", "Staff.csv", "Family.csv", "Classes.csv", "Enrollments.csv"]


# ---------------------------------------------------------------------------
# SD48 tests (uses StudentDemographicEnhanced.txt + StaffInformation.txt)
# ---------------------------------------------------------------------------

class TestSD48Pipeline:

    @pytest.fixture
    def sd48_files(self, tmp_path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        _write_student_demographic(input_dir, "StudentDemographicEnhanced.txt")
        _write_staff(input_dir, "StaffInformation.txt")
        _write_schedule(input_dir)
        _write_course_info(input_dir)
        _write_emergency_contacts(input_dir)
        _write_class_info_enh(input_dir)

        return input_dir, output_dir

    def test_sd48_full_pipeline_produces_all_outputs(self, sd48_files):
        input_dir, output_dir = sd48_files
        main("sd48myedbc", str(input_dir), str(output_dir))
        for filename in EXPECTED_OUTPUTS:
            assert (output_dir / filename).exists(), f"SD48: Missing {filename}"

    def test_sd48_students_output_populated(self, sd48_files):
        input_dir, output_dir = sd48_files
        main("sd48myedbc", str(input_dir), str(output_dir))
        students = pd.read_csv(output_dir / "Students.csv")
        assert len(students) == 3

    def test_sd48_staff_output_populated(self, sd48_files):
        input_dir, output_dir = sd48_files
        main("sd48myedbc", str(input_dir), str(output_dir))
        staff = pd.read_csv(output_dir / "Staff.csv")
        assert len(staff) == 3

    def test_sd48_no_tmp_dirs_left(self, sd48_files):
        input_dir, output_dir = sd48_files
        main("sd48myedbc", str(input_dir), str(output_dir))
        tmp_dirs = list(output_dir.glob(".tmp_*"))
        assert tmp_dirs == []


# ---------------------------------------------------------------------------
# SD74 tests — uses same file names as base myedbc (no overrides in sd74)
# ---------------------------------------------------------------------------

class TestSD74Pipeline:
    """SD74 uses distinct source file names: studentcourseselection.txt,
    StaffInformation.txt, ParentInformation.txt, ClassInfoEnhanced.txt."""

    @pytest.fixture
    def sd74_files(self, tmp_path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        # StudentDemographicInformation.txt (same as base)
        _write_student_demographic(input_dir, "StudentDemographicInformation.txt")

        # Staff uses StaffInformation.txt (not Enhanced)
        _write_staff(input_dir, "StaffInformation.txt")

        # Schedule uses studentcourseselection.txt (lowercase)
        pd.DataFrame({
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
        }).to_csv(input_dir / "studentcourseselection.txt", index=False)

        _write_course_info(input_dir)

        # Family uses ParentInformation.txt with "Surname" column
        pd.DataFrame({
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Surname": ["Smith"],
            "Email Address": ["john@mail.com"],
        }).to_csv(input_dir / "ParentInformation.txt", index=False)

        # ClassInfoEnhanced.txt (different name from base ClassInformationEnh.txt)
        pd.DataFrame(columns=[
            "School Number", "Teacher ID", "Master Timetable ID",
            "Term", "Semester", "Day", "Period",
        ]).to_csv(input_dir / "ClassInfoEnhanced.txt", index=False)

        return input_dir, output_dir

    def test_sd74_full_pipeline_produces_all_outputs(self, sd74_files):
        input_dir, output_dir = sd74_files
        main("sd74myedbc", str(input_dir), str(output_dir))
        for filename in EXPECTED_OUTPUTS:
            assert (output_dir / filename).exists(), f"SD74: Missing {filename}"

    def test_sd74_students_output_populated(self, sd74_files):
        input_dir, output_dir = sd74_files
        main("sd74myedbc", str(input_dir), str(output_dir))
        students = pd.read_csv(output_dir / "Students.csv")
        assert len(students) == 3

    def test_sd74_no_tmp_dirs_left(self, sd74_files):
        input_dir, output_dir = sd74_files
        main("sd74myedbc", str(input_dir), str(output_dir))
        tmp_dirs = list(output_dir.glob(".tmp_*"))
        assert tmp_dirs == []
