"""Tests for the no-usable-input boundary guard in ``run_pipeline``.

A scheduled, unattended run that received no usable required input (wrong
folder, truncated export, locked file) must fail loudly — not masquerade as a
clean run. The guard keys off INPUT presence (``raw_data`` right after
``load_data``), independent of ``run_transform``'s per-entity skip-on-empty, so:

  (a) every required file missing/empty  → raise + ``_emit_run_log("failed")`` →
      main wiring exits 1;
  (b) a period-only ``sd51attendance`` run (daily absent/empty, period present)
      → the period file is non-empty in ``raw_data`` → guard does NOT fire;
  (c) a partial multi-entity run (one entity has data) → completes + writes that
      entity, exit 0.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.etl.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Required-input GDE columns (minimal, mirrors test_sftp_exit.py)
# ---------------------------------------------------------------------------


def _write_full_rostering_input(d: Path) -> None:
    """Write a minimal-but-complete myedbc rostering input set to ``d``."""
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

    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(d / "ClassInformationEnh.txt", index=False)


# 17 columns in file order — StudentPeriodAbsences.txt is HEADERLESS (headers
# injected from config). Two data rows, no header row. Only School Number /
# Student Number / Absence Date / Absence Category are functionally used.
_PERIOD_ROWS = [
    "100,P1,Last,First,10,A1,Teacher,2024-09-18,MAT10,A,,,MT001,A,T001,SCC,FL",
    "100,P2,Last,First,11,A1,Teacher,19-Sep-2024,ENG11,L,,,MT002,B,T002,SCC,FL",
]


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# (a) No usable input at all → raise + failed run-log + exit 1
# ---------------------------------------------------------------------------


class TestNoUsableInput:
    def test_all_required_files_missing_raises(self, tmp_path: Path, gde_output: Path) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()  # exists, but contains none of the required files

        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

    def test_failed_run_log_emitted(self, tmp_path: Path, gde_output: Path, caplog: pytest.LogCaptureFixture) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

        run_logs = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert run_logs, "expected a structured run-log line"
        payload = json.loads(run_logs[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "failed"

    def test_main_wiring_exits_1(self, tmp_path: Path, gde_output: Path) -> None:
        """Reproduce main.py's __main__ except-block: a run_pipeline exception
        that is NOT a SystemExit is caught and converted to sys.exit(1)."""
        import sys

        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with pytest.raises(SystemExit) as exc:
            try:
                run_pipeline("myedbc", str(empty_input), str(gde_output))
            except SystemExit:
                raise
            except Exception:
                # Mirrors src/main.py lines 272-277.
                sys.exit(1)
        assert exc.value.code == 1

    def test_no_output_files_written(self, tmp_path: Path, gde_output: Path) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

        assert list(gde_output.glob("*.csv")) == []


# ---------------------------------------------------------------------------
# (b) Period-only sd51attendance → guard does NOT fire (period file present)
# ---------------------------------------------------------------------------


class TestPeriodOnlyAttendanceDoesNotFire:
    def test_period_only_run_does_not_raise_from_guard(self, tmp_path: Path, gde_output: Path) -> None:
        """daily absent, period present → the period file is NON-empty in
        ``raw_data`` → the no-usable-input guard does NOT fire (no false
        failure). The run completes normally (exit 0).

        NOTE: this run produces no StudentAttendance output TODAY because
        ``run_transform``'s positional-primary skip drops the entity when the
        daily file (its listed-first source) is empty — a separate latent bug
        the input guard neither masks nor fixes (it keys off INPUT presence,
        not produced output, per the plan Resolution). The assertion here is
        only that the guard does NOT turn this supported scenario into a crash.
        """
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "StudentPeriodAbsences.txt").write_text("\n".join(_PERIOD_ROWS), encoding="utf-8")
        # NOTE: StudentDailyAbsences.txt deliberately absent.

        # Must NOT raise the new "No usable required input" guard — the period
        # file is non-empty, so input WAS loaded.
        result = run_pipeline("sd51attendance", str(input_dir), str(gde_output))

        # Sanity: it returned a result rather than raising (exit 0 path).
        assert result is not None


# ---------------------------------------------------------------------------
# (c) Partial multi-entity input → completes + writes the entity, exit 0
# ---------------------------------------------------------------------------


class TestPartialInputStillRuns:
    def test_partial_input_completes_and_writes(self, tmp_path: Path, gde_output: Path) -> None:
        """A full myedbc input set (all primaries present) completes normally and
        writes the rostering CSVs — the guard only fires when NOTHING loaded."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert (gde_output / "Students.csv").exists()
        assert result.entity_counts.get("Students", 0) > 0

    def test_partial_input_with_some_empty_sources_still_runs(self, tmp_path: Path, gde_output: Path) -> None:
        """Some required files present (Students), others absent — the guard does
        NOT fire because at least one required frame is non-empty; the run
        completes (per-entity skip-on-empty handles the absent ones)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        # Only the demographic file → Students has data; schedule/staff absent.
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
        ).to_csv(input_dir / "StudentDemographicInformation.txt", index=False)

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        # Did not raise; Students written from the one present file.
        assert (gde_output / "Students.csv").exists()
        assert result.entity_counts.get("Students", 0) > 0
