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
from src.config.authoring import OverlaySpec, overlay_path, write_overlay
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
        from src.etl.errors import OutputFolderUnsetError
        from src.ui_flet.screens.convert import convert_job

        AppConfig(input_dir=str(gde_input), output_dir="", sis_type="myedbc").save()
        before = sorted(p.name for p in gde_input.iterdir())
        with pytest.raises(ValueError, match="output folder") as excinfo:
            convert_job("myedbc", str(gde_input))
        # Typed (plan 0053 S3) so the on_error card words it as the OUTPUT category, not DATA.
        assert excinfo.type is OutputFolderUnsetError
        assert read_run_records() == []
        # The anti-regression is airtight: nothing was written into the INPUT folder
        # (the old fallback's exact failure mode), not merely "the call raised".
        assert sorted(p.name for p in gde_input.iterdir()) == before


# --------------------------------------------------------------------------- #
# output-folder pre-flight (plan 0050) - an output fault is never an input fault #
# --------------------------------------------------------------------------- #
class TestOutputFolderPreflight:
    """Plan 0050: an unusable OUTPUT folder is refused BEFORE any ETL work, named as an
    OUTPUT fault, and recorded per each surface's own rule.

    The bug this closes: Convert ran the entire ETL (80k+ rows, seconds of work) and then
    failed at ``DataLoader`` construction / ``save_all`` with copy blaming the *input*
    folder, while the scheduled path recorded an unreachable drive as a ``config``
    problem. Three reproduced causes (unmapped drive, over-long path, unwritable folder)
    plus a fourth already documented in this repo (an output CSV held open in Excel).
    """

    @staticmethod
    def _unusable(tmp_path: Path) -> str:
        """An output folder that cannot be created - a FILE squatting on the leaf name.

        Chosen over an unmapped drive letter because it reproduces on every platform the
        suite runs on, and it is the same ``mkdir`` failure the real causes produce.
        """
        leaf = tmp_path / "unusable_output"
        leaf.write_text("a file, not a folder", encoding="utf-8")
        return str(leaf)

    # -- Convert (manual, watched) ------------------------------------------------- #

    def test_convert_refuses_before_any_etl_work(self, gde_input: Path, tmp_path: Path, monkeypatch) -> None:
        """THE goal-1 pin: the refusal happens before ``run_transform`` is ever called.

        A pre-check placed at ``convert_job``'s ``DataLoader`` line instead of the top
        would satisfy a naive reading of "before the loader" and leave every other
        assertion here green - this is the one that catches it.
        """
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens import convert as convert_mod

        calls: list = []
        monkeypatch.setattr(convert_mod, "run_transform", lambda *a, **kw: calls.append(a))

        AppConfig(input_dir=str(gde_input), output_dir=self._unusable(tmp_path), sis_type="myedbc").save()
        before = sorted(p.name for p in gde_input.iterdir())

        result = convert_mod.convert_job("myedbc", str(gde_input))

        assert result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE
        assert calls == [], "the ETL must not run for a folder we already know we cannot write to"
        assert read_run_records() == [], "a refusal the admin is watching is not a night in the ledger"
        assert sorted(p.name for p in gde_input.iterdir()) == before, "the input folder is never touched"

    def test_the_same_fixture_converts_when_the_folder_is_good(self, gde_input: Path, gde_output: Path) -> None:
        """The positive twin: "nothing ran" above is only meaningful beside a run that does."""
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens.convert import convert_job

        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()
        result = convert_job("myedbc", str(gde_input))
        assert result.status is ConvertStatus.DELIVERED
        assert result.entity_counts.get("Students", 0) == 2
        records = read_run_records()
        assert records and records[0]["status"] == "success"

    def test_a_blank_output_folder_still_fails_loud(self, gde_input: Path) -> None:
        """D10 is untouched: an UNSET folder is a gate bug and must keep raising, not
        become a calm card. The pre-flight is consulted only for a folder that IS set."""
        from src.ui_flet.screens.convert import convert_job

        AppConfig(input_dir=str(gde_input), output_dir="", sis_type="myedbc").save()
        with pytest.raises(ValueError, match="output folder"):
            convert_job("myedbc", str(gde_input))

    # -- the write-time window a pre-check structurally cannot see ----------------- #

    def test_a_write_time_oserror_becomes_the_output_status(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The Excel-lock / drive-drops-mid-run shape: the folder passed the pre-flight and
        then failed at the write. It must land on the SAME honest copy, not the generic
        input-folder card the ``on_error`` floor renders."""
        from src.etl.loader import DataLoader
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens import convert as convert_mod

        def _locked(*_a: object, **_kw: object) -> None:
            raise PermissionError(13, "The process cannot access the file because it is being used")

        monkeypatch.setattr(DataLoader, "save_all", _locked)
        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()

        result = convert_mod.convert_job("myedbc", str(gde_input))

        assert result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE
        assert read_run_records() == [], "nothing was produced, so nothing goes in the ledger"
        assert convert_mod.is_write_in_flight() is False, "the C6 flag must clear on the failure path too"

    def test_a_write_time_valueerror_still_propagates(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """The twin that stops the ``OSError`` catch widening: ``save_all`` raises
        ``ValueError`` for a missing field-map column (``loader.select_ordered``), which is
        a DATA fault and must keep reaching ``_on_error`` untouched."""
        from src.etl.loader import DataLoader
        from src.ui_flet.screens import convert as convert_mod

        def _missing_column(*_a: object, **_kw: object) -> None:
            raise ValueError("Cannot write Students.csv - columns missing from output: ['Grade']")

        monkeypatch.setattr(DataLoader, "save_all", _missing_column)
        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()

        with pytest.raises(ValueError, match="columns missing from output"):
            convert_mod.convert_job("myedbc", str(gde_input))
        assert convert_mod.is_write_in_flight() is False

    # -- the pipeline (scheduled / CLI) -------------------------------------------- #

    def test_run_pipeline_exits_1_and_stores_the_output_category(
        self, gde_input: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The SD51 shape: a mapped drive is per-logon-session, so the scheduled task's
        session is where the output folder is most likely to be missing. It used to record
        ``config``; it must now record ``output`` - and never the free text."""
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(SystemExit) as exc_info:
            run_pipeline("myedbc", str(gde_input), self._unusable(tmp_path), source="scheduled")
        assert exc_info.value.code == 1

        records = read_run_records()
        assert records is not None and records
        # Exactly ONE row: two identical rows (a double store write) would satisfy every
        # other assertion here. The autouse isolated profile makes the count meaningful.
        assert len(records) == 1
        stored = records[0]
        assert stored["status"] == "failed"
        assert stored["error_category"] == "output"
        assert "error" not in stored, "privacy split: the free-text reason goes to the log line only"

        lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert lines, "expected a structured run-log line"
        payload = json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["error_category"] == "output"
        assert payload["error"], "the rich free-text detail lives in the log, where support can read it"

    def test_run_pipeline_write_time_oserror_stores_the_output_category(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """A fault raised at the WRITE carries its own bounded category to the one failure
        sink - the ``DeliveryIntegrityError`` pattern, not a third store sink."""
        from src.etl.loader import DataLoader

        def _locked(*_a: object, **_kw: object) -> None:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(DataLoader, "save_all", _locked)
        with pytest.raises(pipeline.OutputWriteError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        records = read_run_records()
        assert records is not None and records
        assert records[0]["error_category"] == "output"
        assert "error" not in records[0]

    def test_a_missing_field_map_column_still_records_data(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The pipeline-side twin of the widening guard: ``ValueError`` out of ``save_all``
        keeps its ``data`` category - the new catch is ``OSError`` only."""
        from src.etl.loader import DataLoader

        def _missing_column(*_a: object, **_kw: object) -> None:
            raise ValueError("Cannot write Students.csv - columns missing from output: ['Grade']")

        monkeypatch.setattr(DataLoader, "save_all", _missing_column)
        with pytest.raises(ValueError, match="columns missing from output"):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        records = read_run_records()
        assert records is not None and records
        assert records[0]["error_category"] == "data"

    def test_a_loader_construct_failure_after_a_clean_preflight_records_output(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """The pre-flight→construct window, closed (F2). The folder passed the probe and
        broke a few lines later at ``DataLoader``'s own ``mkdir``; without the wrap that
        ``FileNotFoundError``/``PermissionError`` falls to the generic classifier and
        records ``config`` — the exact misclassification this slice exists to remove.
        Convert's ``except OSError`` has always covered its loader construct; this is the
        pipeline's symmetry."""

        class _Boom:
            def __init__(self, *_a: object, **_kw: object) -> None:
                raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(pipeline, "output_target_problem", lambda _p: None)  # probe passes
        monkeypatch.setattr(pipeline, "DataLoader", _Boom)

        with pytest.raises(pipeline.OutputWriteError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        records = read_run_records()
        assert records is not None and len(records) == 1
        assert records[0]["error_category"] == "output"

    def test_a_dry_run_loader_failure_is_byte_identical(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """The twin that keeps acceptance criterion 5 true: a PREVIEW never probed, so it
        has no output claim to make and its failure must stay exactly what it was — the
        raw ``OSError``, not an ``OutputWriteError``. ``creator_gate_job`` rides this path."""

        class _Boom:
            def __init__(self, *_a: object, **_kw: object) -> None:
                raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(pipeline, "DataLoader", _Boom)

        with pytest.raises(PermissionError) as exc_info:
            run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        assert not isinstance(exc_info.value, pipeline.OutputWriteError)
        assert read_run_records() == []

    def test_a_dry_run_neither_records_nor_probes(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """``creator_gate_job`` (the mapping creator's test conversion) passes
        ``dry_run=True``; the gate is what keeps that path byte-identical. A preview never
        calls ``save_all``, so requiring writability would be a new gate on a path that
        needs none - and a failed probe cleanup would leave a directory on a run
        advertised as writing nothing."""
        probes: list = []
        monkeypatch.setattr(pipeline, "output_target_problem", lambda path: probes.append(path))

        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)

        assert probes == [], "a preview must not probe the output folder"
        assert read_run_records() == []

    def test_a_real_run_does_probe(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """The positive twin: "did not probe" above only means something beside a run that does."""
        probes: list = []

        def _spy(path: str) -> None:
            probes.append(path)
            return None

        monkeypatch.setattr(pipeline, "output_target_problem", _spy)
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert probes == [str(gde_output)]


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
        monkeypatch.setattr(convert_screen, "extract_required_files", _boom)
        monkeypatch.setattr(convert_screen.DataExtractor, "load_data", _boom)
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
# dry run — never enters the ledger (plan 0038, flag 7)                         #
# --------------------------------------------------------------------------- #
class TestDryRunWritesNoRecord:
    """``--dry-run`` writes no files, so it must write no run record either.

    Before this, a support-requested dry run wrote a full ``success`` record: Home
    said "your roster synced just now" and Run History grew a phantom row for a run
    that delivered nothing. The diagnostic ``__DISTRICTSYNC_RUN__`` log line stays —
    ops keeps the trail, the ledger keeps the truth.
    """

    def test_dry_run_writes_no_store_record(self, gde_input: Path, gde_output: Path) -> None:
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        assert read_run_records() == []

    def test_dry_run_leaves_the_history_db_untouched(self, gde_input: Path, gde_output: Path) -> None:
        """The acceptance lock: the store FILE is not created (nor touched) by a preview."""
        from src.utils import paths

        db = paths.user_history_db()
        assert not db.exists()
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        assert not db.exists(), "a dry run must not create history.db"

    def test_dry_run_still_emits_the_diagnostic_log_line(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert lines, "the dry run's diagnostic log line is the ops trail and must survive"
        payload = json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "success" and payload["source"] == "cli"

    def test_a_real_run_after_a_dry_run_is_the_only_recorded_one(self, gde_input: Path, gde_output: Path) -> None:
        """The skip is scoped to the preview — the very next real run records normally."""
        run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert records[0]["status"] == "success"

    def test_dry_run_failure_records_nothing_either(self, tmp_path: Path, gde_output: Path) -> None:
        """A failed PREVIEW is not a failed sync — the gate sits at the store sink, so the
        failure and early-exit paths inherit it and cannot paint a red verdict for a run
        that never touched the roster. The log line still carries the failure detail."""
        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output), dry_run=True)
        assert read_run_records() == []

    def test_dry_run_early_exit_records_nothing_either(self, tmp_path: Path, gde_output: Path) -> None:
        missing = tmp_path / "nope"
        with pytest.raises(SystemExit) as exc_info:
            run_pipeline("myedbc", str(missing), str(gde_output), dry_run=True)
        assert exc_info.value.code == 1  # the exit-code contract is untouched
        assert read_run_records() == []

    def test_dry_run_config_load_failure_records_nothing_either(self, gde_input: Path, gde_output: Path) -> None:
        """The CONFIG-load early exits are their own ``_record_early_failure`` call sites.

        ``run_pipeline`` has three ``sys.exit(1)`` sinks — the missing input dir and the
        two ``load_config`` failures — and each has to forward ``dry_run`` itself. This
        covers the two the missing-input test does not reach, which is why ``dry_run`` is
        a REQUIRED keyword-only argument on both sink helpers: no future sink can inherit
        a quiet default.
        """
        with pytest.raises(SystemExit) as exc_info:
            run_pipeline("no_such_sis", str(gde_input), str(gde_output), dry_run=True)
        assert exc_info.value.code == 1
        assert read_run_records() == []


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


# --------------------------------------------------------------------------- #
# a CORRUPT USER-DIR overlay (plan 0044 S7 §7.2 — the breaking-base floor)      #
# --------------------------------------------------------------------------- #
class TestCorruptUserOverlayRecordsAFailedRun:
    """The floor under plan 0044's sharpest cost: **no CI gate ever validates a
    user-authored overlay**, so a vendor base change (or a hand edit) that breaks one
    surfaces for the first time on a district's own machine, at 2 a.m., unattended.

    What must hold that night: exit 1, a FAILED run in the ledger with the bounded
    ``config`` category and no free text, and NEVER a crash before the record — the
    admin has to be able to open Run History the next morning and be told it was their
    mapping. The existing ``config``-category coverage above is an unknown id and a
    monkeypatched ``load_config`` raise; neither is a real file, and a real file is
    exactly what this claim is about.

    Two corruption shapes, because they take different code paths: YAML that does not
    PARSE (``yaml.YAMLError`` — a hand edit or a half-written file) and YAML that parses
    but fails validation (``ValueError`` — the shape a base change produces, e.g. a grade
    chain that no longer resolves). The SUCCESS twin writes the same id PROPERLY through
    ``authoring.write_overlay``, so a red half is the corruption and not the harness.

    Nothing here asserts repair: the app never rewrites a district's own file.

    The spec built locally on purpose — importing ``tests/test_config_authoring.py``
    would invert the test dependency (the S2b lesson).
    """

    SIS = "sd93custom"

    def _spec(self) -> OverlaySpec:
        """A minimal valid SD93 overlay spec — no renames, so the standard-named
        ``gde_input`` fixture files are exactly what it expects."""
        return OverlaySpec(
            sd_number=93,
            district_name="SD93 - Run store pin",
            district_domains=("sd93.bc.ca",),
            base="myedbc",
        )

    def _plant(self, text: str) -> Path:
        """Write real bytes into the isolated ``user_mappings_dir()`` as the overlay."""
        target = overlay_path(self.SIS)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def _only_record(self) -> dict:
        records = read_run_records()
        assert records is not None and records, "the nightly run left NO record at all"
        return records[0]

    def test_unparseable_overlay_exits_1_and_records_the_config_category(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        # A truncated flow sequence — what a half-written file or a bad hand edit looks
        # like. `yaml.safe_load` raises, so this never reaches Pydantic at all.
        planted = self._plant("_base: myedbc\ndistrict_name: [SD93 - torn\n")

        with pytest.raises(SystemExit) as exc_info:
            run_pipeline(self.SIS, str(gde_input), str(gde_output), source="scheduled")

        assert exc_info.value.code == 1
        stored = self._only_record()
        assert stored["status"] == "failed"
        assert stored["error_category"] == "config"
        assert stored["sis_type"] == self.SIS
        assert stored["source"] == "scheduled"
        assert "error" not in stored  # privacy split: free text stays in the log line
        # The app never repairs or removes the district's own file.
        assert planted.read_text(encoding="utf-8").startswith("_base: myedbc")

    def test_schema_invalid_overlay_exits_1_and_records_the_config_category(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        # Parses fine; fails validation. `student_rostering_grades: []` is the honest
        # stand-in for a base change that leaves an overlay's grade chain unresolvable.
        self._plant(
            "_base: myedbc\n"
            "district_name: SD93 - Invalid\n"
            "district_domains: []\n"
            "global_config:\n"
            "  student_rostering_grades: []\n"
        )

        with pytest.raises(SystemExit) as exc_info:
            run_pipeline(self.SIS, str(gde_input), str(gde_output), source="scheduled")

        assert exc_info.value.code == 1
        stored = self._only_record()
        assert stored["status"] == "failed"
        assert stored["error_category"] == "config"
        assert stored["sis_type"] == self.SIS
        assert "error" not in stored

    def test_a_properly_written_overlay_for_the_same_id_records_success(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        """The twin: the SAME id, authored properly, converts and records success."""
        write_overlay(self._spec(), overwrite=False)

        result = run_pipeline(self.SIS, str(gde_input), str(gde_output), source="scheduled")

        assert result.entity_counts["Students"] > 0
        stored = self._only_record()
        assert stored["status"] == "success"
        assert stored["error_category"] == "none"
        assert stored["sis_type"] == self.SIS

    def test_the_torn_file_is_read_as_this_district_not_as_a_missing_config(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The overlay really SHADOWED a load — the failure is a parse, not a not-found.

        Without this, a run that never found the file at all would satisfy every
        assertion above (an unknown id records `config` + exit 1 too).
        """
        self._plant("_base: myedbc\ndistrict_name: [SD93 - torn\n")

        with caplog.at_level(logging.ERROR, logger="src.etl.pipeline"), pytest.raises(SystemExit):
            run_pipeline(self.SIS, str(gde_input), str(gde_output), source="scheduled")

        errors = "\n".join(r.getMessage() for r in caplog.records)
        assert f"{self.SIS}_mapping.yaml" in errors, errors
        assert "not found" not in errors.lower(), errors


# --------------------------------------------------------------------------- #
# run_as — an additive RECORD KEY, never a column (plan 0049 S-1a-ii.3)         #
# --------------------------------------------------------------------------- #
class TestRunAsRidesTheRecord:
    """Which OS account a night ran as, carried in the record dict both sinks share.

    Deliberately NOT a ``runs`` column: the only reader (``read_run_records``) returns the
    parsed JSON blob, so a column would be invisible to it, and the ``ALTER TABLE`` +
    ``PRAGMA user_version`` pair has a measured brick state (two autocommit commits; a
    crash between them re-ALTERs forever). See the plan's S-1a-ii.3.
    """

    def test_a_pipeline_run_carries_the_process_account(self, gde_input: Path, gde_output: Path) -> None:
        from src.utils.accounts import process_account

        run_pipeline("myedbc", str(gde_input), str(gde_output), source="scheduled")

        records = read_run_records()
        assert records is not None and records
        assert records[0]["run_as"] == process_account()
        assert records[0]["run_as"]  # total by contract — never blank

    def test_a_manual_run_carries_it_too(self, gde_input: Path, gde_output: Path) -> None:
        from src.ui_flet.screens.convert import convert_job
        from src.utils.accounts import process_account

        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()
        convert_job("myedbc", str(gde_input))

        records = read_run_records()
        assert records is not None and records
        assert records[0]["source"] == "manual"
        assert records[0]["run_as"] == process_account()

    def test_the_log_line_and_the_store_row_carry_the_same_account(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One dict, two sinks — the diagnostic line must not describe a different run."""
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            run_pipeline("myedbc", str(gde_input), str(gde_output), source="cli")

        line = next(m for m in (r.getMessage() for r in caplog.records) if m.startswith("__DISTRICTSYNC_RUN__"))
        logged = json.loads(line.split(" ", 1)[1])
        records = read_run_records()
        assert records is not None
        assert logged["run_as"] == records[0]["run_as"]

    def test_the_schema_version_is_untouched_on_a_fresh_db(self, gde_input: Path, gde_output: Path) -> None:
        """No ``user_version`` bump, with the positive twin that the value round-trips anyway."""
        import sqlite3

        from src.utils.paths import user_history_db

        run_pipeline("myedbc", str(gde_input), str(gde_output), source="cli")

        with sqlite3.connect(user_history_db()) as conn:
            assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1
            assert {row[1] for row in conn.execute("PRAGMA table_info(runs)")} == {
                "id",
                "timestamp",
                "sis_type",
                "source",
                "status",
                "error_category",
                "schema_version",
                "record",
            }

        records = read_run_records()
        assert records is not None and records[0]["run_as"]

    def test_an_older_record_without_the_key_still_reads(self) -> None:
        """A DB written by <= v3.21.0 has no ``run_as`` — the reader must not care.

        Such a record also predates ``entity_outcomes`` (plan 0053 S2), so both are removed
        to reproduce the real older shape.
        """
        from src.history.store import write_run_record

        legacy = pipeline.build_run_record(
            status="success",
            elapsed=1.0,
            entity_counts={"Students": 1},
            source="cli",
            sis_type="myedbc",
            error_category="none",
            entity_outcomes=None,
        )
        legacy.pop("run_as")
        legacy.pop("entity_outcomes")
        assert write_run_record(legacy, source="cli") is True

        records = read_run_records()
        assert records is not None and len(records) == 1
        assert "run_as" not in records[0]
        assert to_run_rows(records)  # the Run History reader still renders it


# --------------------------------------------------------------------------- #
# typed categories reach the record (plan 0053 S1)                             #
# --------------------------------------------------------------------------- #
class TestTypedCategoriesReachTheRecord:
    """Each typed fault records its OWN category, by type — and each has the twin that
    proves the path is not answering the same thing for everything."""

    #: Unity's own config: Family on, filtered to guardians (``row_filters``).
    _UNITY = "unitychristianmyedbc"

    @staticmethod
    def _plain_emergency_report(d: Path, *, first_header: str = "Student Number", guardian: bool = False) -> None:
        """The 2026-09-22 shape: the PLAIN report under the Enhanced report's filename."""
        columns: dict[str, list[str]] = {
            first_header: ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
        if first_header != "Student Number":
            columns["Student Number"] = ["S001"]
        if guardian:
            columns["Parent Auth / Guardian"] = ["Y"]
        pd.DataFrame(columns).to_csv(d / "EmergencyContactInformation.txt", index=False)

    def test_a_missing_row_filter_column_records_source_schema(self, gde_input: Path, gde_output: Path) -> None:
        """Was ``data`` (an untyped ``ValueError``) before S1."""
        from src.etl.errors import GuardKind, SourceSchemaError

        self._plain_emergency_report(gde_input)
        with pytest.raises(SourceSchemaError) as exc_info:
            run_pipeline(self._UNITY, str(gde_input), str(gde_output))
        assert exc_info.value.guard is GuardKind.PII_SCOPE
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "source_schema"

    def test_the_twin_the_enhanced_report_succeeds_with_the_same_config(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        self._plain_emergency_report(gde_input, guardian=True)
        run_pipeline(self._UNITY, str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["status"] == "success"
        assert records[0]["error_category"] == "none"
        assert records[0]["Family"] == 1

    def test_no_observed_header_reaches_the_exception_or_the_log(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """§8 end to end: a first header standing in for a pupil (row 1 of a headerless
        file) never reaches the raised message, the ``Pipeline failed`` line or the
        ``__DISTRICTSYNC_RUN__`` line."""
        from src.etl.errors import SourceSchemaError
        from tests.test_etl_errors import SENTINEL_PII

        self._plain_emergency_report(gde_input, first_header=SENTINEL_PII)
        with caplog.at_level(logging.DEBUG), pytest.raises(SourceSchemaError) as exc_info:
            run_pipeline(self._UNITY, str(gde_input), str(gde_output))
        assert SENTINEL_PII.lower() not in str(exc_info.value).lower()
        assert SENTINEL_PII.lower() not in caplog.text.lower()
        # Non-vacuity: the failure line WAS logged, carrying the typed message.
        assert "Pipeline failed" in caplog.text and "Parent Auth / Guardian" in caplog.text

    def test_an_unparseable_required_file_records_input_unreadable(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """Was ``unknown`` before S1 (``ExtractionError`` had no category)."""
        from src.etl.extractor import DataExtractor, ExtractionError

        monkeypatch.setattr(DataExtractor, "_read_with_fallback", staticmethod(lambda *a, **k: None))
        with pytest.raises(ExtractionError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert records[0]["error_category"] == "input_unreadable"

    def test_the_twin_an_untyped_transform_raise_still_records_unknown(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("an untyped transformer fault")

        monkeypatch.setattr(pipeline, "run_transform", _boom)
        with pytest.raises(RuntimeError, match="untyped transformer fault"):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["error_category"] == "unknown"

    def test_no_usable_input_is_typed_and_its_message_is_unchanged(self, tmp_path: Path, gde_output: Path) -> None:
        from src.etl.errors import NoUsableInputError

        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        with pytest.raises(NoUsableInputError) as exc_info:
            run_pipeline("myedbc", str(empty_input), str(gde_output))
        assert isinstance(exc_info.value, RuntimeError)
        assert str(exc_info.value).startswith(
            "No usable required input was loaded — every required file is missing or empty: "
        )
        assert str(exc_info.value).endswith(
            ". Check the input folder, the export job, and that the files are not locked."
        )
        records = read_run_records()
        assert records is not None and records[0]["error_category"] == "no_input"


# --------------------------------------------------------------------------- #
# per-entity outcomes reach the record (plan 0053 S2)                          #
# --------------------------------------------------------------------------- #
_MYEDBC_ORDER = ["Students", "Staff", "Family", "Classes", "Enrollments"]


def _log_payload(caplog: pytest.LogCaptureFixture) -> dict:
    lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
    assert lines, "expected a structured run-log line"
    return json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])


def _kinds(stored: dict) -> dict[str, tuple[str, str]]:
    return {entity: (entry["kind"], entry["reason"]) for entity, entry in stored["entity_outcomes"].items()}


class TestEntityOutcomesReachTheRecord:
    """``entity_outcomes`` rides EVERY record both entry points write: one outcome per
    configured entity, in configured order, the log line and the store sharing one dict —
    and ``None`` exactly where no ledger existed. Recording only: every failure below still
    fails the run exactly as before S2."""

    _UNITY = TestTypedCategoriesReachTheRecord._UNITY

    def test_a_success_record_carries_one_built_outcome_per_configured_entity(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        stored = records[0]
        assert list(stored["entity_outcomes"]) == _MYEDBC_ORDER, "keys == configured_entity_order, in order"
        assert _log_payload(caplog)["entity_outcomes"] == stored["entity_outcomes"], "one dict, two sinks"
        for entity, entry in stored["entity_outcomes"].items():
            assert entry["kind"] == "built" and entry["reason"] == "none"
            # For a BUILT entity on a successful run the two row numbers agree.
            assert entry["rows"] == stored[entity] > 0
        # The run's own return value carries the same outcomes.
        assert [o.entity for o in result.entity_outcomes] == _MYEDBC_ORDER
        assert {o.entity: o.rows for o in result.entity_outcomes} == result.entity_counts

    def test_the_unity_plain_report_records_family_failed_and_the_rest_not_run(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from src.etl.errors import SourceSchemaError

        TestTypedCategoriesReachTheRecord._plain_emergency_report(gde_input)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(SourceSchemaError):
            run_pipeline(self._UNITY, str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        stored = records[0]
        assert stored["status"] == "failed" and stored["error_category"] == "source_schema"
        assert _kinds(stored) == {
            "Students": ("built", "none"),
            "Staff": ("built", "none"),
            "Family": ("failed", "missing_source_column"),
            "Classes": ("not_run", "run_aborted"),
            "Enrollments": ("not_run", "run_aborted"),
        }
        assert _log_payload(caplog)["entity_outcomes"] == stored["entity_outcomes"]
        # The flat count keys keep their meaning: nothing reached `outputs` on this run.
        assert stored["Students"] == 0 and stored["entity_outcomes"]["Students"]["rows"] == 2
        # Nothing was written — S2 changes no behaviour.
        assert not list(gde_output.glob("*.csv"))

    def test_a_family_with_no_usable_contact_is_empty_no_rows_after_transform(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        """The SD51 shape: contacts arrive, none carries an email, Family builds nothing."""
        pd.DataFrame(
            {"Student Number": ["S001"], "First Name": ["John"], "Last Name": ["Smith"], "Email Address": [""]}
        ).to_csv(gde_input / "EmergencyContactInformation.txt", index=False)
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and records[0]["status"] == "success"
        assert _kinds(records[0])["Family"] == ("empty", "no_rows_after_transform")
        assert records[0]["Family"] == 0

    def test_the_twin_a_family_with_no_file_is_empty_source_files_empty(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        (gde_input / "EmergencyContactInformation.txt").unlink()
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None
        assert _kinds(records[0])["Family"] == ("empty", "source_files_empty")
        assert _kinds(records[0])["Students"] == ("built", "none")

    def test_a_raise_before_the_entity_loop_records_every_entity_not_run(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        from src.etl.extractor import DataExtractor, ExtractionError

        monkeypatch.setattr(DataExtractor, "_read_with_fallback", staticmethod(lambda *a, **k: None))
        with pytest.raises(ExtractionError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert _kinds(records[0]) == dict.fromkeys(_MYEDBC_ORDER, ("not_run", "run_aborted"))

    def test_no_usable_input_records_every_entity_not_run(self, tmp_path: Path, gde_output: Path) -> None:
        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))
        records = read_run_records()
        assert records is not None
        assert _kinds(records[0]) == dict.fromkeys(_MYEDBC_ORDER, ("not_run", "run_aborted"))

    def test_a_raise_after_the_transform_keeps_the_complete_ledger(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        from src.etl.loader import DataLoader

        def _locked(*_a: object, **_kw: object) -> None:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(DataLoader, "save_all", _locked)
        with pytest.raises(pipeline.OutputWriteError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None
        assert records[0]["error_category"] == "output"
        assert _kinds(records[0]) == dict.fromkeys(_MYEDBC_ORDER, ("built", "none"))

    @pytest.mark.parametrize("fault", ["missing_input_dir", "unknown_config", "unusable_output"])
    def test_an_attempt_that_ended_before_the_ledger_records_none(
        self, fault: str, gde_input: Path, gde_output: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sis, input_dir, output_dir = "myedbc", str(gde_input), str(gde_output)
        if fault == "missing_input_dir":
            input_dir = str(tmp_path / "nope")
        elif fault == "unknown_config":
            sis = "not-a-district"
        else:
            output_dir = TestOutputFolderPreflight._unusable(tmp_path)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(SystemExit):
            run_pipeline(sis, input_dir, output_dir)
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert "entity_outcomes" in records[0] and records[0]["entity_outcomes"] is None
        assert _log_payload(caplog)["entity_outcomes"] is None

    def test_a_generic_raise_before_the_ledger_still_records_none(
        self, gde_input: Path, gde_output: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The generic failure sink's ``None`` arm: a non-``SystemExit`` raise before the ledger
        exists must still write exactly one record (a dropped guard would swallow it silently)."""

        def _probe(*_a: object, **_kw: object) -> None:
            raise RuntimeError("probe")

        monkeypatch.setattr(pipeline, "output_target_problem", _probe)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError, match="probe"):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        records = read_run_records()
        assert records is not None and len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "unknown"
        assert "entity_outcomes" in records[0] and records[0]["entity_outcomes"] is None
        assert _log_payload(caplog)["entity_outcomes"] is None

    def test_a_dry_run_log_line_carries_the_outcomes_but_nothing_is_stored(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        assert list(_log_payload(caplog)["entity_outcomes"]) == _MYEDBC_ORDER
        assert read_run_records() == []


class TestConvertRecordsTheSameOutcomes:
    """Convert builds its ledger at the CLI's point, so a Convert result and record say per
    entity exactly what the CLI would; a delivery-only record and the pre-flight refusal say
    ``None`` (no ledger existed)."""

    def test_a_manual_record_carries_the_outcomes_the_cli_records(
        self, gde_input: Path, gde_output: Path, tmp_path: Path
    ) -> None:
        from src.ui_flet.screens.convert import convert_job

        cli_output = tmp_path / "cli_output"
        cli_output.mkdir()
        run_pipeline("myedbc", str(gde_input), str(cli_output))
        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()
        result = convert_job("myedbc", str(gde_input))

        records = read_run_records()
        assert records is not None and len(records) == 2
        manual, cli = records[0], records[1]
        assert manual["source"] == "manual" and cli["source"] == "cli"
        assert manual["entity_outcomes"] == cli["entity_outcomes"]
        assert list(manual["entity_outcomes"]) == _MYEDBC_ORDER
        assert result.entity_outcomes is not None
        assert [o.entity for o in result.entity_outcomes] == _MYEDBC_ORDER

    def test_convert_no_input_carries_every_entity_not_run(self, gde_output: Path, tmp_path: Path) -> None:
        from src.etl.outcomes import OutcomeKind
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens.convert import convert_job

        empty_input = tmp_path / "empty"
        empty_input.mkdir()
        AppConfig(input_dir=str(empty_input), output_dir=str(gde_output), sis_type="myedbc").save()
        result = convert_job("myedbc", str(empty_input))
        assert result.status is ConvertStatus.NO_INPUT
        assert result.entity_outcomes is not None
        assert [o.entity for o in result.entity_outcomes] == _MYEDBC_ORDER
        assert {o.kind for o in result.entity_outcomes} == {OutcomeKind.NOT_RUN}
        assert read_run_records() == [], "NO_INPUT still writes no record (unchanged)"

    def test_the_convert_preflight_refusal_carries_none(self, gde_input: Path, tmp_path: Path) -> None:
        from src.ui_flet.convert_result import ConvertStatus
        from src.ui_flet.screens.convert import convert_job

        unusable = TestOutputFolderPreflight._unusable(tmp_path)
        AppConfig(input_dir=str(gde_input), output_dir=unusable, sis_type="myedbc").save()
        result = convert_job("myedbc", str(gde_input))
        assert result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE
        assert result.entity_outcomes is None

    def test_a_delivery_only_record_carries_none_beside_a_build_record_that_carries_them(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        from src.ui_flet.screens import convert as convert_screen

        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()
        convert_screen.convert_job("myedbc", str(gde_input))
        calls: list[tuple[Path, str | None, set[str]]] = []
        monkeypatch.setattr(convert_screen, "SFTPUploader", _fake_uploader(calls))
        result = convert_screen.deliver_job("myedbc")

        assert result.entity_outcomes is None
        records = read_run_records()
        assert records is not None and len(records) == 2
        delivery, build = records[0], records[1]
        assert delivery["delivery_only"] is True and delivery["entity_outcomes"] is None
        assert list(build["entity_outcomes"]) == _MYEDBC_ORDER
