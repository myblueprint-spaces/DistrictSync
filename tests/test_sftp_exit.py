"""Tests for Slice 2 — Fail-loud SFTP exit-code behaviour.

Verifies that:
- A requested SFTP upload that fails causes run_pipeline to return
  sftp_attempted=True, sftp_ok=False, and the CLI entry point to exit 3.
- The 5 output CSV files are NOT rolled back when the upload fails
  (the ETL conversion succeeded; only delivery failed).
- A successful upload exits 0.
- Runs without --sftp exit 0 and never touch the uploader.
- --dry-run --sftp exits 0 and never touches the uploader.
- An ERROR-level log line is emitted on SFTP failure.

Every exit-code assertion here drives the real ``src.main.cli`` entry point.
Assertions that used to re-state main.py's guard inline (``not (attempted and
not ok)``) were tautologies and have been replaced; the full exit-code contract
lives in ``tests/test_cli_entry.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.etl.pipeline import PipelineResult, run_pipeline

# ---------------------------------------------------------------------------
# Shared GDE fixture (mirrors the minimal set in test_cli.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
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

    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(d / "ClassInformationEnh.txt", index=False)

    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_ENTITIES = ["Students", "Staff", "Family", "Classes", "Enrollments"]


def _csv_files_present(output_dir: Path) -> list[str]:
    """Return the entity names for which <Entity>.csv exists in output_dir."""
    return [e for e in _EXPECTED_ENTITIES if (output_dir / f"{e}.csv").exists()]


def _make_mock_app_config(host: str = "sftp.ca.spacesedu.com") -> MagicMock:
    """Build a MagicMock AppConfig that reports SFTP as configured."""
    cfg = MagicMock()
    cfg.sftp_is_configured.return_value = True
    cfg.sftp_host = host
    cfg.sftp_port = 22
    cfg.sftp_username = "partner"
    cfg.sftp_remote_path = "/files"
    return cfg


# ---------------------------------------------------------------------------
# run_pipeline return type
# ---------------------------------------------------------------------------


class TestPipelineResultType:
    """Sanity-check the PipelineResult dataclass shape."""

    def test_pipeline_result_has_required_fields(self, gde_input: Path, gde_output: Path) -> None:
        with patch("src.etl.pipeline._sftp_upload", return_value=True):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        assert isinstance(result, PipelineResult)
        assert hasattr(result, "sftp_attempted")
        assert hasattr(result, "sftp_ok")
        assert hasattr(result, "entity_counts")
        assert hasattr(result, "anomalies")

    def test_no_sftp_flag_returns_not_attempted(self, gde_input: Path, gde_output: Path) -> None:
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.sftp_attempted is False
        assert result.sftp_ok is False

    def test_sftp_success_reflected_in_result(self, gde_input: Path, gde_output: Path) -> None:
        with patch("src.etl.pipeline._sftp_upload", return_value=True):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)
        assert result.sftp_attempted is True
        assert result.sftp_ok is True

    def test_sftp_failure_reflected_in_result(self, gde_input: Path, gde_output: Path) -> None:
        with patch("src.etl.pipeline._sftp_upload", return_value=False):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)
        assert result.sftp_attempted is True
        assert result.sftp_ok is False


# ---------------------------------------------------------------------------
# SFTP failure: exit code 3 + CSVs intact
# ---------------------------------------------------------------------------


class TestSftpFailureExitCode:
    """The PipelineResult flags an exit-3 decision is made from.

    The exit code ITSELF is asserted against the real entry point in
    ``tests/test_cli_entry.py::TestExitCodeThree`` — this class covers the
    pipeline-side inputs to that decision (flags, CSV survival, log lines).
    """

    def test_sftp_fail_exits_3_and_csvs_exist(self, gde_input: Path, gde_output: Path) -> None:
        """Failed upload → exit 3; all 5 CSVs are written and present on disk."""
        mock_cfg = _make_mock_app_config()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.side_effect = ConnectionError("Network unreachable")
            mock_uploader_cls.return_value = mock_uploader

            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        # The pipeline result must carry the failure flags
        assert result.sftp_attempted is True
        assert result.sftp_ok is False

        # All 5 output CSVs must still be present (not rolled back)
        present = _csv_files_present(gde_output)
        assert set(present) == set(_EXPECTED_ENTITIES), f"Expected all 5 CSVs; found: {present}"

    def test_sftp_fail_produces_exit_3_via_the_real_entry_point(self, gde_input: Path, gde_output: Path) -> None:
        """An uploader that raises mid-transfer reaches the process as exit 3.

        Replaces a tautology: the old version reproduced main.py's condition inline,
        raised its own ``sys.exit(3)`` and asserted it exited 3 — it could not have
        failed even if main.py exited 0. This drives ``src.main.cli`` end-to-end
        from a REAL uploader fault (``OSError`` out of ``upload_csvs``), so the
        whole chain — uploader → ``_sftp_upload`` → ``PipelineResult`` → the entry
        point's exit-code branch — is what is under test.
        """
        from src.main import cli

        mock_cfg = _make_mock_app_config()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.side_effect = OSError("Upload failed")
            mock_uploader_cls.return_value = mock_uploader

            code = cli(["--sis", "myedbc", "--input", str(gde_input), "--output", str(gde_output), "--sftp"])

        assert code == 3

    def test_sftp_fail_logs_error(self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture) -> None:
        """An ERROR-level log line must be emitted when the upload fails."""
        mock_cfg = _make_mock_app_config("sftp.ca.spacesedu.com")
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
            caplog.at_level(logging.ERROR, logger="src.etl.pipeline"),
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.side_effect = RuntimeError("auth failed")
            mock_uploader_cls.return_value = mock_uploader

            run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("SFTP upload FAILED" in msg for msg in error_messages), (
            f"Expected an ERROR log containing 'SFTP upload FAILED'; got: {error_messages}"
        )
        assert any("sftp.ca.spacesedu.com" in msg for msg in error_messages), "Expected the host name in the error log"

    def test_sftp_fail_logs_error_includes_host(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The ERROR log must include the configured host."""
        mock_cfg = _make_mock_app_config("sftp.app.spacesedu.com")
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
            caplog.at_level(logging.ERROR, logger="src.etl.pipeline"),
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.side_effect = TimeoutError("timeout")
            mock_uploader_cls.return_value = mock_uploader

            run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("sftp.app.spacesedu.com" in msg for msg in error_messages)

    def test_sftp_fail_run_log_status_success_sftp_flags(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The run-log of an ETL-OK-but-SFTP-failed run carries
        status="success", sftp_attempted=True, sftp_ok=False — the exact
        boolean source the Run History "ETL OK · SFTP FAILED" Status cell reads.
        (ETL completed + wrote the CSVs; only delivery failed — a separate axis.)
        """
        mock_cfg = _make_mock_app_config()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
            caplog.at_level(logging.INFO, logger="src.etl.pipeline"),
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.side_effect = ConnectionError("Network unreachable")
            mock_uploader_cls.return_value = mock_uploader

            run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        run_logs = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert run_logs, "expected a structured run-log line"
        payload = json.loads(run_logs[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "success"
        assert payload["sftp_attempted"] is True
        assert payload["sftp_ok"] is False


# ---------------------------------------------------------------------------
# SFTP success: exit 0
# ---------------------------------------------------------------------------


class TestSftpSuccessExitCode:
    def test_sftp_success_result_is_ok(self, gde_input: Path, gde_output: Path) -> None:
        mock_cfg = _make_mock_app_config()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.return_value = ["Students.csv", "Staff.csv"]
            mock_uploader_cls.return_value = mock_uploader

            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        assert result.sftp_attempted is True
        assert result.sftp_ok is True

    def test_sftp_success_returns_exit_0(self, gde_input: Path, gde_output: Path) -> None:
        """A successful upload exits 0 — asserted against the REAL entry point
        rather than by re-stating main.py's guard inline (the old form asserted
        ``not (attempted and not ok)``, which is main.py's condition copied, not
        main.py's behaviour observed)."""
        from src.main import cli

        mock_cfg = _make_mock_app_config()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg),
            patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls,
        ):
            mock_uploader = MagicMock()
            mock_uploader.upload_csvs.return_value = ["Students.csv"]
            mock_uploader_cls.return_value = mock_uploader

            code = cli(["--sis", "myedbc", "--input", str(gde_input), "--output", str(gde_output), "--sftp"])

        assert code == 0


# ---------------------------------------------------------------------------
# No --sftp flag: exit 0, uploader never called
# ---------------------------------------------------------------------------


class TestNoSftpFlag:
    def test_no_sftp_uploader_not_called(self, gde_input: Path, gde_output: Path) -> None:
        """Without --sftp, SFTPUploader must never be instantiated."""
        with patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls:
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=False)

        mock_uploader_cls.assert_not_called()
        assert result.sftp_attempted is False
        assert result.sftp_ok is False

    def test_no_sftp_returns_exit_0(self, gde_input: Path, gde_output: Path) -> None:
        """A run without --sftp can never reach the exit-3 branch — observed at the
        real entry point, not inferred from a copy of its guard."""
        from src.main import cli

        assert cli(["--sis", "myedbc", "--input", str(gde_input), "--output", str(gde_output)]) == 0


# ---------------------------------------------------------------------------
# --dry-run --sftp: exit 0, no upload attempted
# ---------------------------------------------------------------------------


class TestSftpEmptyOutputDirExit3:
    """An empty output dir at delivery time is fail-loud (no silent []-as-delivered).

    Unreachable from Convert (it always builds CSVs first), but reachable from a
    CLI/scheduled misconfig pointing --output at a dir with no CSVs.
    """

    def test_empty_output_dir_upload_raises(self, tmp_path: Path) -> None:
        """``upload_csvs`` on an empty dir raises RuntimeError instead of returning []."""
        from src.sftp.uploader import SFTPUploader

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/files")
        with pytest.raises(RuntimeError, match="No CSV files found to upload"):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"})

    def test_sftp_upload_seam_empty_dir_returns_false(self, tmp_path: Path) -> None:
        """The pipeline seam catches the fail-loud raise and reports False.

        That False is what becomes ``sftp_ok=False`` → exit 3 ("built but not
        delivered"); the exit code itself is asserted end-to-end in
        ``tests/test_cli_entry.py::TestExitCodeThree``.
        """
        from src.etl.pipeline import _sftp_upload

        mock_cfg = _make_mock_app_config()
        with patch("src.etl.pipeline.AppConfig.load", return_value=mock_cfg):
            ok = _sftp_upload(str(tmp_path), manifest={"Students.csv"})

        assert ok is False


class TestDryRunWithSftp:
    def test_dry_run_sftp_not_attempted(self, gde_input: Path, gde_output: Path) -> None:
        """--dry-run skips the write step and therefore must never attempt upload."""
        with patch("src.etl.pipeline.SFTPUploader") as mock_uploader_cls:
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True, sftp=True)

        mock_uploader_cls.assert_not_called()
        assert result.sftp_attempted is False
        assert result.sftp_ok is False

    def test_dry_run_sftp_returns_exit_0(self, gde_input: Path, gde_output: Path) -> None:
        """--dry-run --sftp exits 0 — observed at the real entry point."""
        from src.main import cli

        with patch("src.etl.pipeline.SFTPUploader"):
            code = cli(
                [
                    "--sis",
                    "myedbc",
                    "--input",
                    str(gde_input),
                    "--output",
                    str(gde_output),
                    "--dry-run",
                    "--sftp",
                ]
            )
        assert code == 0


# ---------------------------------------------------------------------------
# What actually ships: THIS run's roster, never the output folder's contents
# ---------------------------------------------------------------------------


def _capturing_sftp() -> tuple[MagicMock, list[str], list[str]]:
    """A mock SFTPClient recording (zip member names, remote filenames) per ``put``."""
    import zipfile

    zipped: list[str] = []
    remote_names: list[str] = []
    mock_sftp = MagicMock()

    def _capture_put(local: str, remote: str) -> None:
        remote_names.append(Path(remote).name)
        if str(local).endswith(".zip"):
            with zipfile.ZipFile(local) as zf:
                zipped.extend(zf.namelist())

    mock_sftp.put.side_effect = _capture_put
    return mock_sftp, zipped, remote_names


class TestDeliveredSetIsThisRunsRoster:
    """The delivered payload is the roster THIS run produced — not the folder's *.csv glob.

    A district admin who leaves a spreadsheet export / backup CSV in the output folder
    must not have it uploaded to SpacesEDU (student PII egress the run never vouched for).
    """

    def test_foreign_csv_in_output_folder_is_not_delivered(self, gde_input: Path, gde_output: Path) -> None:
        (gde_output / "old_roster.csv").write_text("id,name\n1,Ex Student\n", encoding="utf-8")
        (gde_output / "students_backup.csv").write_text("id,name\n2,Backup\n", encoding="utf-8")

        mock_sftp, zipped, remote_names = _capturing_sftp()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=_make_mock_app_config()),
            patch("src.sftp.uploader.SFTPUploader._connect", return_value=(MagicMock(), mock_sftp)),
        ):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        assert result.sftp_ok is True
        # The foreign files stay on disk (nothing is deleted) but never leave the building.
        assert (gde_output / "old_roster.csv").exists()
        assert "old_roster.csv" not in zipped
        assert "students_backup.csv" not in zipped
        assert sorted(zipped) == sorted(f"{e}.csv" for e in _EXPECTED_ENTITIES)
        assert remote_names == [n for n in remote_names if n.endswith(".zip")]

    def test_prior_run_entity_csvs_still_all_delivered(self, gde_input: Path, gde_output: Path) -> None:
        """Back-compat: a folder holding the SAME config's earlier CSVs still ships all of them."""
        for entity in _EXPECTED_ENTITIES:
            (gde_output / f"{entity}.csv").write_text("stale\n", encoding="utf-8")

        mock_sftp, zipped, _ = _capturing_sftp()
        with (
            patch("src.etl.pipeline.AppConfig.load", return_value=_make_mock_app_config()),
            patch("src.sftp.uploader.SFTPUploader._connect", return_value=(MagicMock(), mock_sftp)),
        ):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)

        assert result.sftp_ok is True
        assert sorted(zipped) == sorted(f"{e}.csv" for e in _EXPECTED_ENTITIES)
