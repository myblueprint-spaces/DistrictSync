"""Tests for CLI flags: --dry-run, --diff, --quality, --version.

These tests call run_pipeline() directly with flag=True to avoid the
argparse layer, which allows pytest to capture stdout cleanly.
"""

import pandas as pd
import pytest

from src.etl import output_layout
from src.main import run_pipeline
from tests.test_pipeline_required_input import _write_class_info_rows


@pytest.fixture
def gde_input(tmp_path):
    """Write minimal GDE files to a temp directory."""
    d = tmp_path / "input"
    d.mkdir()

    pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Legal First Name": ["Alice", "Bob"],
            "Legal Surname": ["Smith", "Jones"],
            "Date of birth": ["2010-01-15", "2009-06-20"],
            "Grade": ["10", "12"],
            "School Number": ["100", "100"],
            "Homeroom": ["A1", "A1"],
            "Previous school number": ["", ""],
            "Usual First Name": ["", ""],
            "Usual surname": ["", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca"],
            "Enrolment Status": ["Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Ms. Harper"],
            "Teacher ID": ["T001", "T001"],
        }
    ).to_csv(d / "StudentDemographicInformation.txt", index=False)

    pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Student ID": ["S001", "S002"],
            "School Number": ["100", "100"],
            "School Year": ["2025/2026", "2025/2026"],
            "Grade": ["10", "12"],
            "Master Timetable ID": ["MT001", "MT002"],
            "Teacher ID": ["T001", "T001"],
            "Section Letter": ["A", "A"],
            "District Course Code": ["MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y"],
            "Teacher Name": ["Harper", "Harper"],
        }
    ).to_csv(d / "StudentSchedule.txt", index=False)

    pd.DataFrame(
        {
            "Teacher ID": ["T001"],
            "First Name": ["Jane"],
            "Last Name": ["Harper"],
            "Email Address": ["harper@school.ca"],
            "Teaching Staff": ["Y"],
            "School Number": ["100"],
        }
    ).to_csv(d / "StaffInformationEnhanced.txt", index=False)

    pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Course Code": ["MAT10", "ENG12"],
            "Title": ["Math 10", "English 12"],
        }
    ).to_csv(d / "CourseInformation.txt", index=False)

    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformation.txt", index=False)

    _write_class_info_rows(d)

    return d


@pytest.fixture
def gde_output(tmp_path):
    return tmp_path / "output"


class TestDryRunFlag:
    def test_dry_run_writes_no_files(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        written = [p for p in gde_output.rglob("*") if p.is_file()]
        assert written == [], f"Dry run wrote files: {written}"

    def test_dry_run_prints_summary(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "rows" in captured.out

    def test_dry_run_shows_entity_counts(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        captured = capsys.readouterr()
        # At least Students entity should appear
        assert "Students" in captured.out


class TestDiffFlag:
    def test_diff_with_no_existing_output_reports_new(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        # First run to produce output, then diff against it
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        # Remove one file to simulate "new" entity
        students_csv = output_layout.csv_path(gde_output, "Students.csv")
        students_csv.unlink()
        # No pre-DSYNC-57 top-level copy either, or --diff would fall back to it.
        assert not output_layout.legacy_csv_path(gde_output, "Students.csv").exists()
        run_pipeline("myedbc", str(gde_input), str(gde_output), diff=True, dry_run=True)
        captured = capsys.readouterr()
        assert "DIFF" in captured.out
        assert "NEW" in captured.out

    def test_diff_with_existing_output_shows_counts(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        # First run
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        # Second run with diff
        run_pipeline("myedbc", str(gde_input), str(gde_output), diff=True, dry_run=True)
        captured = capsys.readouterr()
        assert "DIFF" in captured.out
        assert "->" in captured.out or "rows" in captured.out


class TestQualityFlag:
    def test_quality_flag_prints_report(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output), quality=True)
        captured = capsys.readouterr()
        assert "DATA QUALITY REPORT" in captured.out

    def test_quality_report_mentions_entities(self, gde_input, gde_output, capsys):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output), quality=True)
        captured = capsys.readouterr()
        assert "Students" in captured.out


class TestTransactionalWrite:
    """Verify that save_all() commits all-or-nothing."""

    def test_normal_run_produces_all_outputs(self, gde_input, gde_output):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        # All 5 entities should be present, in csv/ and inside the one Advanced CSV zip
        entities = ["Students", "Staff", "Family", "Classes", "Enrollments"]
        for entity in entities:
            assert output_layout.csv_path(gde_output, f"{entity}.csv").exists(), f"Missing {entity}.csv"
        (zip_path,) = output_layout.acsv_zips(gde_output)
        assert zip_path.name == output_layout.acsv_zip_name("myedbc")
        assert output_layout.zip_member_names(zip_path) == {f"{e}.csv" for e in entities}

    def test_no_tmp_dir_left_after_success(self, gde_input, gde_output):
        gde_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        tmp_dirs = list(gde_output.glob(".tmp_*"))
        assert tmp_dirs == [], f"Temp directories not cleaned up: {tmp_dirs}"
