"""Tests for src/config/app_config.py — runtime config load/save."""

import json

import pytest

from src.config.app_config import AppConfig


@pytest.fixture
def config_dir(isolated_user_profile):
    """The isolated app-data dir + config path.

    AppConfig now resolves its path through ``paths.user_data_dir()`` at call time,
    which the conftest autouse ``isolated_user_profile`` fixture redirects into a
    per-test tmp dir — so this just surfaces that isolated location for assertions.
    """
    return isolated_user_profile, isolated_user_profile / "config.json"


class TestAppConfigDefaults:
    def test_default_values(self):
        cfg = AppConfig()
        assert cfg.input_dir == ""
        assert cfg.output_dir == ""
        assert cfg.sis_type == "myedbc"
        assert cfg.schedule_time == "03:00"
        assert cfg.sftp_enabled is False
        assert cfg.sftp_port == 22


class TestAppConfigLoad:
    def test_load_returns_defaults_when_no_file(self, config_dir):
        cfg = AppConfig.load()
        assert cfg.sis_type == "myedbc"
        assert cfg.input_dir == ""

    def test_load_reads_saved_config(self, config_dir):
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps(
                {
                    "input_dir": "/data/input",
                    "output_dir": "/data/output",
                    "sis_type": "sd48myedbc",
                    "schedule_time": "04:00",
                }
            ),
            encoding="utf-8",
        )

        cfg = AppConfig.load()
        assert cfg.input_dir == "/data/input"
        assert cfg.output_dir == "/data/output"
        assert cfg.sis_type == "sd48myedbc"
        assert cfg.schedule_time == "04:00"

    def test_load_ignores_unknown_fields(self, config_dir):
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps(
                {
                    "input_dir": "/data/input",
                    "future_field": "ignored",
                }
            ),
            encoding="utf-8",
        )

        cfg = AppConfig.load()
        assert cfg.input_dir == "/data/input"
        assert not hasattr(cfg, "future_field")

    def test_load_returns_defaults_on_corrupt_file(self, config_dir):
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("not valid json{{{", encoding="utf-8")

        cfg = AppConfig.load()
        assert cfg.sis_type == "myedbc"  # defaults


class TestAppConfigSave:
    def test_save_creates_file(self, config_dir):
        cfg_dir, cfg_file = config_dir
        cfg = AppConfig(input_dir="/in", output_dir="/out")
        cfg.save()

        assert cfg_file.exists()
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert data["input_dir"] == "/in"
        assert data["output_dir"] == "/out"

    def test_save_roundtrip(self, config_dir):
        cfg = AppConfig(
            input_dir="/data/input",
            output_dir="/data/output",
            sis_type="sd74myedbc",
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
            sftp_username="district",
        )
        cfg.save()

        loaded = AppConfig.load()
        assert loaded.input_dir == "/data/input"
        assert loaded.sis_type == "sd74myedbc"
        assert loaded.sftp_enabled is True
        assert loaded.sftp_host == "sftp.ca.spacesedu.com"


class TestIsComplete:
    def test_incomplete_without_paths(self):
        cfg = AppConfig(sis_type="myedbc")
        assert cfg.is_complete() is False

    def test_incomplete_without_sis_type(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="")
        assert cfg.is_complete() is False

    def test_incomplete_with_invalid_sis_type(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="bad;type")
        assert cfg.is_complete() is False

    def test_complete(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc")
        assert cfg.is_complete() is True


class TestSftpIsConfigured:
    def test_not_configured_when_disabled(self):
        cfg = AppConfig(sftp_enabled=False)
        assert cfg.sftp_is_configured() is False

    def test_not_configured_without_host(self):
        cfg = AppConfig(sftp_enabled=True, sftp_host="", sftp_username="user")
        assert cfg.sftp_is_configured() is False

    def test_not_configured_without_username(self):
        cfg = AppConfig(
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
            sftp_username="",
        )
        assert cfg.sftp_is_configured() is False

    def test_not_configured_with_disallowed_host(self):
        cfg = AppConfig(
            sftp_enabled=True,
            sftp_host="evil.example.com",
            sftp_username="user",
            sftp_remote_path="/upload",
        )
        assert cfg.sftp_is_configured() is False

    def test_configured(self):
        cfg = AppConfig(
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
            sftp_username="district_user",
            sftp_remote_path="/upload",
        )
        assert cfg.sftp_is_configured() is True
