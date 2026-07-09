"""Integration tests for the run-store wiring in ``run_pipeline`` + ``convert_job`` (Plan 0029, 4b).

Covers the D2/D2a/D2b/D2c contract end-to-end:
- ``source`` propagation (``DSYNC_SOURCE`` env → scheduled; default cli; explicit arg wins;
  ``convert_job`` → manual);
- enriched ``__DISTRICTSYNC_RUN__`` log-line parity with the stored record (one dict, two sinks);
- strictly-non-fatal store writes — a forced failure changes neither the ``PipelineResult`` nor
  the written CSVs, and the FAILURE path never masks the original ETL exception (identity check);
- record-shape equivalence: the stored record drives ``home_status`` / ``run_history`` unchanged.

Runs under the autouse isolation fixture, so the store lands in a per-test tmp profile.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl import pipeline
from src.etl.pipeline import PipelineResult, run_pipeline
from src.history.store import read_run_records
from src.ui_flet.home_status import derive_home_status
from src.ui_flet.run_history import to_run_rows


def _write_myedbc_input(d: Path) -> None:
    """Minimal-but-complete myedbc rostering input (mirrors test_pipeline_required_input)."""
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


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_myedbc_input(d)
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


# --------------------------------------------------------------------------- #
# source propagation (D2c)                                                      #
# --------------------------------------------------------------------------- #
class TestSourcePropagation:
    def test_env_marker_labels_scheduled(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        monkeypatch.setenv("DSYNC_SOURCE", "scheduled")
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["source"] == "scheduled"

    def test_default_is_cli(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        monkeypatch.delenv("DSYNC_SOURCE", raising=False)
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["source"] == "cli"

    def test_explicit_source_wins_over_env(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        monkeypatch.setenv("DSYNC_SOURCE", "scheduled")
        run_pipeline("myedbc", str(gde_input), str(gde_output), source="manual")
        records = read_run_records()
        assert records is not None and records[0]["source"] == "manual"

    def test_bogus_source_coerces_to_unknown(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        monkeypatch.setenv("DSYNC_SOURCE", "nonsense-value")
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["source"] == "unknown"


# --------------------------------------------------------------------------- #
# enriched log-line parity (D2a — one dict, two sinks)                          #
# --------------------------------------------------------------------------- #
class TestLogLineParity:
    def test_log_line_matches_stored_record_enrichment(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture, monkeypatch
    ) -> None:
        monkeypatch.setenv("DSYNC_SOURCE", "scheduled")
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert lines, "expected a structured run-log line"
        log_payload = json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])

        records = read_run_records()
        assert records is not None
        stored = records[0]

        # The enrichment is identical across both sinks (built from one dict) and correct.
        expected = {"source": "scheduled", "sis_type": "myedbc", "error_category": "none"}
        for key, value in expected.items():
            assert log_payload[key] == stored[key] == value
        # Privacy split: the log line carries the free-text ``error`` field; the store never does.
        assert "error" in log_payload
        assert "error" not in stored


# --------------------------------------------------------------------------- #
# strictly non-fatal writes (D2b)                                              #
# --------------------------------------------------------------------------- #
class TestNonFatal:
    def test_store_write_failure_does_not_change_result_or_csvs(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> bool:
            raise RuntimeError("store exploded")

        monkeypatch.setattr(pipeline, "write_run_record", _boom)
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        # The result + exit-code inputs are unchanged; the CSVs are written.
        assert isinstance(result, PipelineResult)
        assert result.sftp_attempted is False and result.sftp_ok is False
        assert (gde_output / "Students.csv").exists()
        assert result.entity_counts.get("Students", 0) == 2

    def test_failure_path_store_error_preserves_original_exception(
        self, tmp_path: Path, gde_output: Path, monkeypatch
    ) -> None:
        # A store failure DURING the failed-run recording must not mask the ETL error.
        def _boom(*_a: object, **_k: object) -> bool:
            raise RuntimeError("store exploded during failure recording")

        monkeypatch.setattr(pipeline, "write_run_record", _boom)
        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

    def test_failure_path_record_build_error_preserves_original_exception(
        self, tmp_path: Path, gde_output: Path, monkeypatch
    ) -> None:
        # Even if record-BUILDING blows up while recording a failure, the outer guard
        # re-raises the ORIGINAL ETL exception (identity), never the recording error.
        def _boom(*_a: object, **_k: object) -> dict:
            raise ValueError("record build exploded")

        monkeypatch.setattr(pipeline, "build_run_record", _boom)
        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))


# --------------------------------------------------------------------------- #
# record-shape equivalence — the stored record drives the derivation modules    #
# --------------------------------------------------------------------------- #
class TestShapeEquivalence:
    def test_stored_record_drives_home_status_and_run_rows(self, gde_input: Path, gde_output: Path) -> None:
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records

        cfg = AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc")
        # home_status consumes the stored record unchanged → a recent clean run is HEALTHY.
        status = derive_home_status(records, cfg)
        assert status.verdict.name == "HEALTHY"
        assert status.metrics is not None and status.metrics.entity_counts["Students"] == 2
        # run_history maps the same record to a PII-free row with matching counts.
        rows = to_run_rows(records)
        assert rows and rows[0].entity_counts["Students"] == 2


# --------------------------------------------------------------------------- #
# convert_job → manual (the manual path finally appears in Run History)          #
# --------------------------------------------------------------------------- #
class TestConvertManual:
    def test_convert_job_records_a_manual_run(self, gde_input: Path, gde_output: Path) -> None:
        from src.ui_flet.screens.convert import convert_job

        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()
        result = convert_job("myedbc", str(gde_input))
        assert result.entity_counts.get("Students", 0) == 2

        records = read_run_records()
        assert records is not None and records
        assert records[0]["source"] == "manual"
        assert records[0]["status"] == "success"
        assert records[0]["Students"] == 2

    def test_no_input_run_records_nothing(self, gde_output: Path, tmp_path: Path) -> None:
        """Slice 9 regression: only COMMITTED manual runs are recorded — a NO_INPUT run writes nothing.

        Guards that the Slice-9 output-gate rework did not change ``_record_manual_run``'s
        committed-only asymmetry (an empty input folder → ``NO_INPUT`` before any write).
        """
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens.convert import convert_job

        empty_input = tmp_path / "empty_in"
        empty_input.mkdir()
        AppConfig(input_dir=str(empty_input), output_dir=str(gde_output), sis_type="myedbc").save()
        result = convert_job("myedbc", str(empty_input))
        assert result.status is ConvertStatus.NO_INPUT
        assert read_run_records() == []  # committed-only: nothing recorded

    def test_empty_output_dir_fails_loud_and_records_nothing(self, gde_input: Path) -> None:
        """D10: an unset output folder makes ``convert_job`` FAIL LOUD — never a silent input-dir write.

        The old ``AppConfig.load().output_dir or input_dir`` fallback would have quietly written the
        roster into the *input* folder; now it raises, and (fail-fast) records nothing.
        """
        from src.ui_flet.screens.convert import convert_job

        AppConfig(input_dir=str(gde_input), output_dir="", sis_type="myedbc").save()
        before = sorted(p.name for p in gde_input.iterdir())
        with pytest.raises(ValueError, match="output folder"):
            convert_job("myedbc", str(gde_input))
        assert read_run_records() == []
        # The anti-regression is airtight: nothing was written into the INPUT folder
        # (the old fallback's exact failure mode), not merely "the call raised".
        assert sorted(p.name for p in gde_input.iterdir()) == before
