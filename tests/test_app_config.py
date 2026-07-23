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
        assert cfg.sis_type == ""  # D9: no district pre-selected on a fresh install
        assert cfg.schedule_time == "03:00"
        assert cfg.sftp_enabled is False
        assert cfg.sftp_port == 22
        # 0034 S3: the "what was actually registered" facts default to "no record".
        assert cfg.schedule_unattended is False
        assert cfg.schedule_task_args is None


class TestAppConfigLoad:
    def test_load_returns_defaults_when_no_file(self, config_dir):
        cfg = AppConfig.load()
        assert cfg.sis_type == ""  # D9: no district pre-selected
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
        assert cfg.sis_type == ""  # defaults (D9: no district pre-selected)


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


class TestHasCompletedSetup:
    """D4a: the durable finish-line fact — distinct from the schedule's live-ness."""

    def test_fresh_install_has_not_completed_setup(self):
        assert AppConfig().has_completed_setup() is False

    def test_explicit_flag_is_honored(self):
        # The wizard (Slice 8) sets this even before a schedule is registered.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        assert cfg.has_completed_setup() is True

    def test_inferred_from_old_finish_line_condition(self):
        # An install predating the flag: complete config + registered schedule → completed.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)
        assert cfg.has_completed_setup() is True

    def test_complete_but_unscheduled_without_flag_is_not_completed(self):
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        assert cfg.has_completed_setup() is False


class TestSetupCompletedBackCompatInference:
    """D4a: an existing deployed machine never regresses into onboarding after this update."""

    def test_load_bakes_inferred_setup_completed(self, config_dir):
        # An OLD config.json (no setup_completed key) with the old finish-line state → inferred True.
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps(
                {
                    "input_dir": "/in",
                    "output_dir": "/out",
                    "sis_type": "myedbc",
                    "schedule_registered": True,
                }
            ),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.setup_completed is True
        assert cfg.has_completed_setup() is True

    def test_load_does_not_infer_for_unscheduled_install(self, config_dir):
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc"}),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.setup_completed is False

    def test_load_honors_persisted_true_even_if_unscheduled(self, config_dir):
        # A completed-setup manual-only upgrader (wizard wrote the flag) stays completed.
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc", "setup_completed": True}),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.setup_completed is True

    def test_setup_completed_survives_save_roundtrip(self, config_dir):
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True).save()
        assert AppConfig.load().setup_completed is True


class TestScheduleRegistrationFacts:
    """0034 S3: the durable last-register facts (unattended flag + task-args record)."""

    _ARGS = {
        "input_dir": "/in",
        "output_dir": "/out",
        "sis_type": "myedbc",
        "sftp_enabled": True,
        "run_time": "03:00",
    }

    def test_facts_survive_save_roundtrip(self, config_dir):
        AppConfig(schedule_unattended=True, schedule_task_args=dict(self._ARGS)).save()
        loaded = AppConfig.load()
        assert loaded.schedule_unattended is True
        assert loaded.schedule_task_args == self._ARGS

    def test_old_config_file_without_the_fields_loads_defaults(self, config_dir):
        # Back-compat: a pre-S3 config.json has neither key → additive defaults apply and the
        # reconcile falls back to its mount snapshot (the pre-record behaviour).
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc"}),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.schedule_unattended is False
        assert cfg.schedule_task_args is None


class TestWindowGeometryFields:
    """0032 T2 #8: the additive window-geometry fields (persisted on exit, restored clamped)."""

    def test_defaults_are_never_saved(self):
        cfg = AppConfig()
        assert cfg.window_width is None
        assert cfg.window_height is None
        assert cfg.window_left is None
        assert cfg.window_top is None
        assert cfg.window_maximized is False

    def test_geometry_survives_save_roundtrip(self, config_dir):
        AppConfig(
            window_width=1100.0,
            window_height=750.5,
            window_left=-10.0,
            window_top=40.0,
            window_maximized=True,
        ).save()
        loaded = AppConfig.load()
        assert loaded.window_width == 1100.0
        assert loaded.window_height == 750.5
        assert loaded.window_left == -10.0
        assert loaded.window_top == 40.0
        assert loaded.window_maximized is True

    def test_old_config_file_without_the_fields_loads_defaults(self, config_dir):
        # Back-compat: a pre-geometry config.json has none of the keys → additive defaults.
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc"}),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.window_width is None
        assert cfg.window_maximized is False


class TestSyncWindowFields:
    """Seasonal sync window: the additive, opt-in ``sync_window_*`` admin choices."""

    def test_defaults_are_off_and_blank(self):
        cfg = AppConfig()
        assert cfg.sync_window_enabled is False
        assert cfg.sync_window_start == ""
        assert cfg.sync_window_end == ""

    def test_survives_save_roundtrip(self, config_dir):
        AppConfig(
            sync_window_enabled=True,
            sync_window_start="08-11",
            sync_window_end="07-06",
        ).save()
        loaded = AppConfig.load()
        assert loaded.sync_window_enabled is True
        assert loaded.sync_window_start == "08-11"
        assert loaded.sync_window_end == "07-06"

    def test_old_config_file_without_the_fields_loads_defaults(self, config_dir):
        # Back-compat: a pre-window config.json has none of the keys → additive defaults,
        # so an existing install keeps running year-round unchanged.
        cfg_dir, cfg_file = config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc"}),
            encoding="utf-8",
        )
        cfg = AppConfig.load()
        assert cfg.sync_window_enabled is False
        assert cfg.sync_window_start == ""
        assert cfg.sync_window_end == ""


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
