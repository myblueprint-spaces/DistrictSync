"""End-to-end pipeline test.

Creates synthetic GDE files on disk, runs the full ETL pipeline,
and verifies the output CSVs have the expected structure and data.
"""

import pandas as pd
import pytest

from src.main import main


class TestEndToEndPipeline:
    """Full pipeline: files on disk → ETL → output CSVs."""

    @pytest.fixture
    def setup_gde_files(self, tmp_path):
        """Create synthetic GDE input files on disk."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        # StudentDemographicInformation.txt
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
                "Usual First Name": ["Ali", "", "Chuck"],
                "Usual surname": ["", "", ""],
                "Student email address": ["alice@test.ca", "bob@test.ca", "charlie@test.ca"],
                "Enrolment Status": ["Active", "Active", "Active"],
                "Teacher Name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
                "Teacher ID": ["T001", "T003", "T004"],
            }
        ).to_csv(input_dir / "StudentDemographicInformation.txt", index=False)

        # StudentSchedule.txt
        pd.DataFrame(
            {
                "Student Number": ["S001", "S002", "S003"],
                "Student ID": ["S001", "S002", "S003"],
                "School Number": ["100", "200", "200"],
                "School Year": ["2025/2026", "2025/2026", "2025/2026"],
                "Grade": ["3", "10", "12"],
                "Master Timetable ID": ["MT001", "MT002", "MT003"],
                "District Course Code": ["HR-3", "MAT10", "ENG12"],
                "Teacher ID": ["T001", "T003", "T004"],
                "Section Letter": ["A", "A", "B"],
                "Primary Teacher": ["Y", "Y", "Y"],
                "Teacher Name": ["Harper", "Liu", "Singh"],
            }
        ).to_csv(input_dir / "StudentSchedule.txt", index=False)

        # StaffInformationEnhanced.txt
        pd.DataFrame(
            {
                "Teacher Id": ["T001", "T003", "T004"],
                "First Name": ["Jane", "Linda", "Raj"],
                "Last Name": ["Harper", "Liu", "Singh"],
                "Email Address": ["harper@school.ca", "liu@school.ca", "singh@school.ca"],
                "Teaching Staff": ["Y", "Y", "Y"],
                "School Number": ["100", "200", "200"],
            }
        ).to_csv(input_dir / "StaffInformationEnhanced.txt", index=False)

        # EmergencyContactInformation.txt
        pd.DataFrame(
            {
                "Student Number": ["S001", "S002"],
                "First Name": ["John", "Robert"],
                "Last Name": ["Smith", "Jones"],
                "Email Address": ["john@mail.com", "robert@mail.com"],
            }
        ).to_csv(input_dir / "EmergencyContactInformation.txt", index=False)

        # CourseInformation.txt
        pd.DataFrame(
            {
                "School Number": ["200", "200"],
                "Course Code": ["MAT10", "ENG12"],
                "Title": ["Math 10", "English 12"],
            }
        ).to_csv(input_dir / "CourseInformation.txt", index=False)

        # ClassInformationEnh.txt (empty — no blended classes)
        pd.DataFrame(
            {
                "School Number": [],
                "Teacher ID": [],
                "Master Timetable ID": [],
                "Term": [],
                "Semester": [],
                "Day": [],
                "Period": [],
            }
        ).to_csv(input_dir / "ClassInformationEnh.txt", index=False)

        return input_dir, output_dir

    def test_full_pipeline_produces_all_outputs(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        expected_files = ["Students.csv", "Staff.csv", "Family.csv", "Classes.csv", "Enrollments.csv"]
        for filename in expected_files:
            path = output_dir / filename
            assert path.exists(), f"Missing output file: {filename}"

    def test_students_output_only_active(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        students = pd.read_csv(output_dir / "Students.csv")
        assert len(students) == 3  # All 3 are Active
        assert "EnrollStatus" in students.columns

    def test_students_grades_are_ceds_mapped(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        students = pd.read_csv(output_dir / "Students.csv", dtype=str)
        grades = set(students["Grade"].dropna())
        # "3" → "03", "10" → "10", "12" → "12" (all CEDS-format)
        assert "03" in grades
        assert "3" not in grades

    def test_classes_id_includes_school_year(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        classes = pd.read_csv(output_dir / "Classes.csv")
        # Test data has 'School Year' = "2025/2026" → end-year = 2026
        # so class IDs are suffixed with "_2026".
        subject_ids = classes["Class ID"].dropna().astype(str)
        assert any("_2026" in cid for cid in subject_ids)

    def test_enrollments_have_both_roles(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        enrollments = pd.read_csv(output_dir / "Enrollments.csv")
        roles = set(enrollments["Role"].dropna())
        assert "student" in roles
        assert "teacher" in roles

    def test_output_field_order_matches_config(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        from src.config.loader import load_config

        config = load_config("myedbc")
        for entity_name in ("Students", "Staff", "Family"):
            output_file = output_dir / f"{entity_name}.csv"
            if output_file.exists():
                df = pd.read_csv(output_file)
                expected_order = list(config.mappings[entity_name].field_map.keys())
                # Filter to columns that exist in both
                expected_in_output = [c for c in expected_order if c in df.columns]
                actual = list(df.columns[: len(expected_in_output)])
                assert actual == expected_in_output, f"{entity_name} column order mismatch"

    def test_output_files_have_utf8_bom(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        for name in ("Students", "Staff", "Family", "Classes", "Enrollments"):
            raw = (output_dir / f"{name}.csv").read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf"), f"{name}.csv missing UTF-8 BOM"

    def test_staff_output_has_correct_count(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        staff = pd.read_csv(output_dir / "Staff.csv")
        assert len(staff) == 3

    def test_family_output(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        family = pd.read_csv(output_dir / "Family.csv")
        assert len(family) == 2

    def test_classes_output_not_empty(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        classes = pd.read_csv(output_dir / "Classes.csv")
        assert len(classes) > 0
        assert "Class ID" in classes.columns

    def test_enrollments_output_not_empty(self, setup_gde_files):
        input_dir, output_dir = setup_gde_files
        main("myedbc", str(input_dir), str(output_dir))

        enrollments = pd.read_csv(output_dir / "Enrollments.csv")
        assert len(enrollments) > 0
        assert "Role" in enrollments.columns
        # Should have both student and teacher roles
        assert "student" in enrollments["Role"].values
