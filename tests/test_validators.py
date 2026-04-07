"""Tests for src/utils/validators.py — input validation for security-sensitive ops."""

import pytest

from src.utils.validators import (
    ALLOWED_SFTP_HOSTS,
    quote_for_shell,
    validate_run_time,
    validate_sftp_host,
    validate_sis_type,
    validate_task_name,
)


class TestValidateSisType:
    def test_valid_alphanumeric(self):
        assert validate_sis_type("myedbc") == "myedbc"

    def test_valid_with_underscore(self):
        assert validate_sis_type("sd40_myedbc") == "sd40_myedbc"

    def test_valid_with_digits(self):
        assert validate_sis_type("sd48myedbc") == "sd48myedbc"

    def test_strips_whitespace(self):
        assert validate_sis_type("  myedbc  ") == "myedbc"

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("my;edbc")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("my edbc")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("../etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("")


class TestValidateTaskName:
    def test_valid_simple(self):
        assert validate_task_name("GDE2Acsv_Daily") == "GDE2Acsv_Daily"

    def test_valid_with_spaces_and_hyphens(self):
        assert validate_task_name("GDE2Acsv Daily-Run") == "GDE2Acsv Daily-Run"

    def test_strips_whitespace(self):
        assert validate_task_name("  MyTask  ") == "MyTask"

    def test_rejects_semicolons(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("task;rm -rf /")

    def test_rejects_slashes(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("task/name")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("")


class TestValidateRunTime:
    def test_valid_time(self):
        assert validate_run_time("03:00") == ("03", "00")

    def test_valid_midnight(self):
        assert validate_run_time("00:00") == ("00", "00")

    def test_valid_end_of_day(self):
        assert validate_run_time("23:59") == ("23", "59")

    def test_strips_whitespace(self):
        assert validate_run_time("  14:30  ") == ("14", "30")

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError, match="Expected HH:MM"):
            validate_run_time("3:00")

    def test_rejects_hour_out_of_range(self):
        with pytest.raises(ValueError, match="Hour must be"):
            validate_run_time("25:00")

    def test_rejects_minute_out_of_range(self):
        with pytest.raises(ValueError, match="Minute must be"):
            validate_run_time("12:60")

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="Expected HH:MM"):
            validate_run_time("ab:cd")


class TestValidateSftpHost:
    def test_valid_hosts(self):
        for host in ALLOWED_SFTP_HOSTS:
            assert validate_sftp_host(host) == host

    def test_case_insensitive(self):
        assert validate_sftp_host("SFTP.CA.SPACESEDU.COM") == "sftp.ca.spacesedu.com"

    def test_strips_whitespace(self):
        assert validate_sftp_host("  sftp.ca.spacesedu.com  ") == "sftp.ca.spacesedu.com"

    def test_rejects_unknown_host(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_sftp_host("evil.example.com")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_sftp_host("")


class TestQuoteForShell:
    def test_simple_value(self):
        result = quote_for_shell("myedbc")
        assert "myedbc" in result

    def test_value_with_spaces(self):
        result = quote_for_shell("path with spaces")
        # shlex.quote wraps in single quotes
        assert "'" in result or '"' in result

    def test_value_with_special_chars(self):
        result = quote_for_shell("value;rm -rf /")
        # Must be quoted/escaped safely
        assert result != "value;rm -rf /"
