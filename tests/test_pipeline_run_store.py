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
from collections.abc import Collection
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


# --------------------------------------------------------------------------- #
# deliver_job → deliver from disk (0034 Slice 2)                                #
# --------------------------------------------------------------------------- #
def _fake_uploader(calls: list[tuple[Path, str | None, set[str]]], *, fail: bool = False) -> type:
    """A test double for ``SFTPUploader`` recording ``upload_csvs`` calls (no network).

    Records the delivery MANIFEST alongside the folder + district: deliver-from-disk must
    nominate the files it ships, never let the uploader glob the folder.
    """

    class _Fake:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def upload_csvs(
            self,
            output_dir: Path,
            zip_name: str | None = None,
            sis_type: str | None = None,
            *,
            manifest: Collection[str],
        ) -> list:
            calls.append((output_dir, sis_type, set(manifest)))
            if fail:
                raise RuntimeError("connection refused by 203.0.113.9")
            return ["Students.csv"]

    return _Fake


# The 5 rostering entity CSVs the base ``myedbc`` config produces — the authoritative
# deliver-from-disk manifest for these fixtures.
_MYEDBC_CSVS = {"Students.csv", "Staff.csv", "Family.csv", "Classes.csv", "Enrollments.csv"}


class TestDeliverFromDisk:
    """``deliver_job`` uploads the COMMITTED CSVs from disk — never a re-transform — and
    records ONE ``delivery_only`` manual record per attempt that never double-counts a build."""

    def _configure(self, gde_input: Path, gde_output: Path) -> None:
        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()

    def test_success_records_a_delivery_only_run_with_no_build_counts(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens import convert as convert_screen

        self._configure(gde_input, gde_output)
        convert_screen.convert_job("myedbc", str(gde_input))  # a committed build on disk first
        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls))

        result = convert_screen.deliver_job("myedbc")
        assert result.status is ConvertStatus.DELIVERED_FROM_DISK
        assert result.sftp_attempted is True and result.sftp_ok is True
        # Shipped from the OUTPUT dir, zip named per district, and the payload NOMINATED:
        # exactly the active config's entity CSVs found on disk — never the folder's glob.
        assert calls == [(gde_output, "myedbc", _MYEDBC_CSVS)]

        records = read_run_records()
        assert records is not None and len(records) == 2  # the build record + the delivery record
        delivery, build = records[0], records[1]
        assert delivery["source"] == "manual" and delivery["status"] == "success"
        assert delivery["delivery_only"] is True
        assert delivery["sftp_attempted"] is True and delivery["sftp_ok"] is True
        # Never double-counts the build: the delivery record carries NO entity counts…
        assert delivery["Students"] == 0 and delivery["Enrollments"] == 0
        # …while the build record keeps its own (the counts belong to the build alone).
        assert build["Students"] == 2 and "delivery_only" not in build

    def test_deliver_never_retransforms_or_reads_the_input_folder(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The acceptance lock: a between-build-and-deliver input change CANNOT alter what ships,
        because delivery never touches the build seams — poison them all and deliver anyway."""
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens import convert as convert_screen

        self._configure(gde_input, gde_output)
        convert_screen.convert_job("myedbc", str(gde_input))

        def _boom(*_a: object, **_k: object) -> None:
            raise AssertionError("deliver_job must not re-transform or read the input folder")

        monkeypatch.setattr(convert_screen, "run_transform", _boom)
        monkeypatch.setattr(convert_screen, "_read_gde_bytes", _boom)
        monkeypatch.setattr(convert_screen.DataExtractor, "load_from_bytes", _boom)
        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls))

        result = convert_screen.deliver_job("myedbc")
        assert result.status is ConvertStatus.DELIVERED_FROM_DISK
        assert calls and calls[0][0] == gde_output

    def test_failed_upload_folds_into_built_not_delivered_and_records_honestly(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        import json as _json

        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens import convert as convert_screen

        self._configure(gde_input, gde_output)
        convert_screen.convert_job("myedbc", str(gde_input))
        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls, fail=True))

        result = convert_screen.deliver_job("myedbc")
        assert result.status is ConvertStatus.BUILT_NOT_DELIVERED
        assert result.sftp_attempted is True and result.sftp_ok is False

        records = read_run_records()
        assert records is not None
        latest = records[0]
        assert latest["delivery_only"] is True
        assert latest["status"] == "success"  # the ETL axis is untouched; only delivery failed
        assert latest["sftp_attempted"] is True and latest["sftp_ok"] is False
        # Privacy split: the raw upload error never enters the stored record.
        assert "connection refused" not in _json.dumps(latest)

    def test_unset_output_dir_fails_loud_and_records_nothing(self, gde_input: Path) -> None:
        from src.ui_flet.screens.convert import deliver_job

        AppConfig(input_dir=str(gde_input), output_dir="", sis_type="myedbc").save()
        with pytest.raises(ValueError, match="output folder"):
            deliver_job("myedbc")
        assert read_run_records() == []

    def test_unset_district_fails_loud_and_records_nothing(self, gde_input: Path, gde_output: Path) -> None:
        """No district ⇒ no authoritative set. Fail loud rather than fall back to
        "ship whatever is in the folder" — that fallback IS the defect."""
        from src.ui_flet.screens.convert import deliver_job

        self._configure(gde_input, gde_output)
        with pytest.raises(ValueError, match="district"):
            deliver_job("")
        assert read_run_records() == []

    def test_foreign_csv_in_the_output_folder_is_not_nominated(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The deliver-from-disk defect: an admin's stray CSV must not ship to SpacesEDU."""
        from src.ui_flet.screens import convert as convert_screen

        self._configure(gde_input, gde_output)
        convert_screen.convert_job("myedbc", str(gde_input))
        (gde_output / "old_roster.csv").write_text("id,name\n9,Ex Student\n", encoding="utf-8")
        (gde_output / "students_backup.csv").write_text("id,name\n8,Backup\n", encoding="utf-8")

        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls))
        convert_screen.deliver_job("myedbc")

        assert calls and calls[0][2] == _MYEDBC_CSVS
        assert "old_roster.csv" not in calls[0][2]
        assert "students_backup.csv" not in calls[0][2]
        # Nothing is deleted — the files simply stay home.
        assert (gde_output / "old_roster.csv").exists()

    def test_delivery_record_drives_home_and_history_sensibly(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The stored delivery record renders honestly: Home falls back to the build's counts;
        the Run History row reads 'Delivered saved files' with no 0-count cells."""
        from src.ui_flet.screens import convert as convert_screen

        self._configure(gde_input, gde_output)
        convert_screen.convert_job("myedbc", str(gde_input))
        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls))
        convert_screen.deliver_job("myedbc")

        records = read_run_records()
        assert records is not None and len(records) == 2
        cfg = AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc")
        status = derive_home_status(records, cfg)
        assert status.verdict.name == "HEALTHY"
        assert status.metrics is not None and status.metrics.entity_counts["Students"] == 2

        rows = to_run_rows(records)
        assert rows[0].status_label == "Delivered saved files"
        assert rows[0].entity_counts == {}  # never a "0 Students" cell
        assert rows[1].entity_counts["Students"] == 2


# --------------------------------------------------------------------------- #
# early-exit recording (0034 Slice 4 — kill the false silence)                  #
# --------------------------------------------------------------------------- #
class TestEarlyExitRecording:
    """The SystemExit paths inside ``run_pipeline`` (input dir missing, config load failure)
    record a failed run to BOTH sinks before exiting — Task Scheduler's exit 1 and Run History
    can no longer disagree. Exit codes stay byte-identical; the store never carries the free text.
    """

    def test_missing_input_dir_records_failed_run_and_exits_1(
        self, tmp_path: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "nope"
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(SystemExit) as exc_info:
            run_pipeline("myedbc", str(missing), str(gde_output), source="scheduled")
        assert exc_info.value.code == 1

        records = read_run_records()
        assert records is not None and records
        stored = records[0]
        assert stored["status"] == "failed"
        assert stored["error_category"] == "no_input"
        assert stored["source"] == "scheduled"
        assert "error" not in stored  # privacy split: the free text goes to the log line only

        lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert lines, "expected a structured run-log line"
        payload = json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["error_category"] == "no_input"
        assert payload["error"]  # the rich free-text detail lives in the log

    def test_unknown_config_records_config_category_and_exits_1(self, gde_input: Path, gde_output: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run_pipeline("not-a-district", str(gde_input), str(gde_output))
        assert exc_info.value.code == 1
        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "config"

    def test_invalid_config_records_config_category_and_exits_1(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        def _bad(_sis: str) -> None:
            raise ValueError("mapping validation failed")

        monkeypatch.setattr(pipeline, "load_config", _bad)
        with pytest.raises(SystemExit) as exc_info:
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert exc_info.value.code == 1
        records = read_run_records()
        assert records is not None and records
        assert records[0]["error_category"] == "config"

    def test_store_failure_never_blocks_the_early_exit(self, tmp_path: Path, gde_output: Path, monkeypatch) -> None:
        # Recording is best-effort (D2b): a store explosion must not change the exit code.
        def _boom(*_a: object, **_k: object) -> bool:
            raise RuntimeError("store exploded")

        monkeypatch.setattr(pipeline, "write_run_record", _boom)
        missing = tmp_path / "nope"
        with pytest.raises(SystemExit) as exc_info:
            run_pipeline("myedbc", str(missing), str(gde_output))
        assert exc_info.value.code == 1

    def test_early_failure_shows_failed_on_home(self, tmp_path: Path, gde_output: Path) -> None:
        # The acceptance shape: breaking the input dir surfaces as a FAILED Home verdict.
        missing = tmp_path / "nope"
        with pytest.raises(SystemExit):
            run_pipeline("myedbc", str(missing), str(gde_output))
        records = read_run_records()
        cfg = AppConfig(input_dir=str(missing), output_dir=str(gde_output), sis_type="myedbc")
        status = derive_home_status(records, cfg)
        assert status.verdict.name == "FAILED"
