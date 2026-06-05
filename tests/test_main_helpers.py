"""Tests for helper functions in src/main.py.

Covers _check_anomalies, _emit_run_log, extract_required_files,
_sftp_upload, and _print_diff.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pandas as pd

from src.main import (
    _check_anomalies,
    _emit_run_log,
    _print_diff,
    _sftp_upload,
    extract_required_files,
)

# -----------------------------------------------------------------------
# extract_required_files
# -----------------------------------------------------------------------


class TestExtractRequiredFiles:
    def test_collects_all_source_files_when_no_enabled_filter(self):
        config = MagicMock()
        entity1 = MagicMock()
        entity1.source_files = {"primary": "StudentDemo.txt", "schedule": "StudentSchedule.txt"}
        entity2 = MagicMock()
        entity2.source_files = {"primary": "StaffInfo.txt"}
        config.mappings = {"Students": entity1, "Staff": entity2}
        config.global_config.enabled_entities = []
        config.global_config.school_year_sources = {"primary": "StudentSchedule.txt"}

        files = extract_required_files(config)
        assert set(files) == {"StudentDemo.txt", "StudentSchedule.txt", "StaffInfo.txt"}

    def test_deduplicates_files(self):
        config = MagicMock()
        entity1 = MagicMock()
        entity1.source_files = {"primary": "Same.txt"}
        entity2 = MagicMock()
        entity2.source_files = {"primary": "Same.txt"}
        config.mappings = {"A": entity1, "B": entity2}
        config.global_config.enabled_entities = []
        config.global_config.school_year_sources = {"primary": "Same.txt"}

        files = extract_required_files(config)
        assert len(files) == 1

    def test_filters_by_enabled_entities(self):
        """Disabled entities' source files must not appear — and a
        school_year_source not used by any enabled entity is dropped
        (determine_school_year falls back to the calendar-date heuristic)."""
        config = MagicMock()
        students = MagicMock()
        students.source_files = {"primary": "StudentDemo.txt"}
        staff = MagicMock()
        staff.source_files = {"primary": "StaffInfo.txt"}
        classes = MagicMock()
        classes.source_files = {"primary": "StudentSchedule.txt", "info": "ClassInfo.txt"}
        config.mappings = {"Students": students, "Staff": staff, "Classes": classes}
        config.global_config.enabled_entities = ["Students"]
        config.global_config.school_year_sources = {"primary": "StudentSchedule.txt"}

        files = extract_required_files(config)
        assert set(files) == {"StudentDemo.txt"}

    def test_real_mbp_core_config_only_requires_three_files(self):
        """End-to-end check against the actual mbp_core_mapping.yaml."""
        from src.config.loader import load_config

        cfg = load_config("mbp_core")
        files = set(extract_required_files(cfg))
        # mbp_core enables Students + CourseInfo + StudentCourses
        assert files == {
            "StudentDemographicInformation.txt",
            "CourseInformation.txt",
            "StudentCourseHistory.txt",
            "StudentCourseSelection.txt",
        }

    def test_real_mbponly_config_requires_only_course_files(self):
        """mbponly emits only the two course CSVs, so no demographic GDE is needed."""
        from src.config.loader import load_config

        cfg = load_config("mbponly")
        files = set(extract_required_files(cfg))
        # CourseInfo + StudentCourses only — no StudentDemographicInformation.txt
        assert files == {
            "CourseInformation.txt",
            "StudentCourseHistory.txt",
            "StudentCourseSelection.txt",
        }


# -----------------------------------------------------------------------
# _check_anomalies
# -----------------------------------------------------------------------


class TestCheckAnomalies:
    def test_no_anomaly_when_no_previous_file(self, tmp_path):
        outputs = {"Students": pd.DataFrame({"id": range(100)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_no_anomaly_within_threshold(self, tmp_path):
        # Previous: 100 rows. New: 85 rows (15% drop, below 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(85)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_anomaly_when_large_drop(self, tmp_path):
        # Previous: 100 rows. New: 50 rows (50% drop, above 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(50)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert len(warnings) == 1
        assert "ANOMALY" in warnings[0]
        assert "Students" in warnings[0]

    def test_no_anomaly_when_previous_empty(self, tmp_path):
        # Previous: 0 rows (header only). Should not trigger.
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_handles_unreadable_previous(self, tmp_path):
        # Create a directory instead of file — triggers the except branch
        (tmp_path / "Students.csv").mkdir()

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []


# -----------------------------------------------------------------------
# _emit_run_log
# -----------------------------------------------------------------------


class TestEmitRunLog:
    def test_emits_structured_log(self, caplog):
        outputs = {
            "Students": pd.DataFrame({"id": range(10)}),
            "Staff": pd.DataFrame({"id": range(5)}),
        }
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.5, outputs)

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert len(log_lines) == 1
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "success"
        assert payload["duration_s"] == 1.5
        assert payload["Students"] == 10
        assert payload["Staff"] == 5
        assert payload["sftp_attempted"] is False

    def test_emits_error_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("failed", 0.3, {}, error="boom")

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "failed"
        assert payload["error"] == "boom"

    def test_emits_sftp_status(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 2.0, {}, sftp_attempted=True, sftp_ok=True)

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["sftp_attempted"] is True
        assert payload["sftp_ok"] is True

    def test_emits_anomalies(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.0, {}, anomalies=["Students dropped 50%"])

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["anomalies"] == ["Students dropped 50%"]


# -----------------------------------------------------------------------
# _sftp_upload
# -----------------------------------------------------------------------


class TestSftpUpload:
    def test_sftp_not_configured(self, tmp_path):
        """When AppConfig says SFTP is not configured, _sftp_upload returns False."""
        mock_cfg = MagicMock()
        mock_cfg.sftp_is_configured.return_value = False
        mock_app_config_cls = MagicMock()
        mock_app_config_cls.load.return_value = mock_cfg

        with patch("src.config.app_config.AppConfig.load", return_value=mock_cfg):
            result = _sftp_upload(str(tmp_path))
            assert result is False

    def test_sftp_missing_dependency(self):
        # If paramiko/keyring not installed, should return False gracefully
        with patch.dict("sys.modules", {"src.sftp.uploader": None, "src.sftp": None}):
            result = _sftp_upload("/output")
            assert result is False


# -----------------------------------------------------------------------
# _print_diff
# -----------------------------------------------------------------------


class TestPrintDiff:
    def test_diff_new_file(self, tmp_path, capsys):
        outputs = {"Students": pd.DataFrame({"id": [1, 2, 3]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "NEW" in captured.out
        assert "Students" in captured.out

    def test_diff_existing_file(self, tmp_path, capsys):
        # Write existing file
        pd.DataFrame({"id": [1, 2]}).to_csv(tmp_path / "Students.csv", index=False)

        outputs = {"Students": pd.DataFrame({"id": [1, 2, 3, 4]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "Students" in captured.out
        assert "2 -> 4" in captured.out
        assert "+2" in captured.out

    def test_diff_with_column_changes(self, tmp_path, capsys):
        pd.DataFrame({"id": [1], "old_col": ["x"]}).to_csv(tmp_path / "Staff.csv", index=False)

        outputs = {"Staff": pd.DataFrame({"id": [1], "new_col": ["y"]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "Staff" in captured.out
