"""DSYNC-112: a ``config.json`` saved WITH a UTF-8 byte-order mark is still settings.

Windows PowerShell 5.1's ``Set-Content -Encoding UTF8`` writes a BOM, so an IT admin who
hand-edits the settings file produced a document the app reported as "truncated or
corrupt" and ran on defaults (``ConfigLoadState.UNREADABLE``). Every reader of the file
now decodes through the ONE shared rule (``app_config.decode_settings_bytes``): a BOM is
accepted on read, and the app still WRITES plain UTF-8 with no BOM.

Every test here works in tmp paths — the autouse ``isolated_user_profile`` fixture, or a
monkeypatched ``paths.per_user_data_dir`` seam — never the real profile.
"""

from __future__ import annotations

import codecs
import json
from pathlib import Path

import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.scheduler import machine_setup, migration, provisioning
from src.utils import paths

_BOM = codecs.BOM_UTF8

# Several fields of several declared types, so "every field intact" means something.
_PAYLOAD: dict[str, object] = {
    "input_dir": "C:/DistrictSync/input",
    "output_dir": "C:/DistrictSync/output",
    "sis_type": "sd74myedbc",
    "schedule_time": "04:30",
    "sftp_enabled": True,
    "sftp_host": "sftp.ca.spacesedu.com",
    "sftp_username": "district",
    "sftp_port": 22,
}


def _document(*, bom: bool) -> bytes:
    body = json.dumps(_PAYLOAD, indent=2).encode("utf-8")
    return _BOM + body if bom else body


@pytest.fixture
def config_file(isolated_user_profile: Path) -> Path:
    isolated_user_profile.mkdir(parents=True, exist_ok=True)
    return isolated_user_profile / "config.json"


class TestLoad:
    def test_a_bom_file_loads_with_every_field_intact(self, config_file: Path) -> None:
        config_file.write_bytes(_document(bom=True))
        assert config_file.read_bytes().startswith(_BOM), "the fixture must really carry a BOM"

        cfg = AppConfig.load()

        assert cfg.load_state is ConfigLoadState.LOADED
        for key, value in _PAYLOAD.items():
            assert getattr(cfg, key) == value, key

    def test_positive_twin_a_file_without_a_bom_loads(self, config_file: Path) -> None:
        config_file.write_bytes(_document(bom=False))
        assert not config_file.read_bytes().startswith(_BOM)

        cfg = AppConfig.load()

        assert cfg.load_state is ConfigLoadState.LOADED
        for key, value in _PAYLOAD.items():
            assert getattr(cfg, key) == value, key

    @pytest.mark.parametrize("bom", [False, True], ids=["no-bom", "bom"])
    def test_truncated_json_is_still_unreadable(self, config_file: Path, bom: bool) -> None:
        config_file.write_bytes((_BOM if bom else b"") + b'{"sis_type": "sd74my')

        assert AppConfig.load().load_state is ConfigLoadState.UNREADABLE

    def test_a_bom_followed_by_nothing_is_still_unreadable(self, config_file: Path) -> None:
        config_file.write_bytes(_BOM)

        assert AppConfig.load().load_state is ConfigLoadState.UNREADABLE


class TestSave:
    def test_a_save_after_loading_a_bom_file_writes_no_bom(self, config_file: Path) -> None:
        config_file.write_bytes(_document(bom=True))
        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED

        cfg.schedule_time = "05:15"
        cfg.save()

        written = config_file.read_bytes()
        assert not written.startswith(_BOM)
        # Positive twin: the save really landed, and it is plain UTF-8 JSON.
        assert json.loads(written.decode("utf-8"))["schedule_time"] == "05:15"
        assert AppConfig.load().sis_type == "sd74myedbc"

    def test_a_bom_file_is_not_quarantined_as_corrupt_by_the_save(self, config_file: Path) -> None:
        config_file.write_bytes(_document(bom=True))
        AppConfig.load().save()

        assert list(config_file.parent.glob("config.corrupt-*.json")) == []

    def test_positive_twin_a_torn_bom_file_is_quarantined_byte_for_byte(self, config_file: Path) -> None:
        planted = _BOM + b'{"sis_type": "sd74my'
        config_file.write_bytes(planted)
        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE
        # A chosen setting, so the save is not refused by the "no defaults over an
        # unreadable file" guard (SettingsOverwriteRefused) and reaches the quarantine.
        cfg.sis_type = "sd74myedbc"
        cfg.save()

        quarantined = list(config_file.parent.glob("config.corrupt-*.json"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == planted


class TestTheOtherReaders:
    """The readers that parse the per-user file directly, without ``AppConfig.load()``."""

    def test_the_machine_setup_delivery_seed_accepts_a_bom(self, monkeypatch, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_bytes(_document(bom=True))
        monkeypatch.setattr(paths, "per_user_data_dir", lambda: tmp_path)

        assert machine_setup.delivery_seed() == (True, "sftp.ca.spacesedu.com", "district")

    def test_the_migration_report_settings_accept_a_bom(self, monkeypatch, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_bytes(_document(bom=True))
        monkeypatch.setattr(paths, "per_user_data_dir", lambda: tmp_path)
        monkeypatch.setattr(machine_setup, "needs_machine_setup", lambda: True)

        settings = migration._settings()

        assert isinstance(settings, AppConfig)
        assert settings.sis_type == "sd74myedbc"

    def test_the_profile_migration_accepts_a_bom_and_writes_none(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        (source / "config.json").write_bytes(_document(bom=True))

        provisioning.migrate_profile(source, destination)

        migrated = (destination / "config.json").read_bytes()
        assert not migrated.startswith(_BOM)
        assert json.loads(migrated.decode("utf-8")) == _PAYLOAD

    def test_the_profile_migration_still_refuses_a_torn_bom_file(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        (source / "config.json").write_bytes(_BOM + b'{"sis_type": "sd74my')

        with pytest.raises(provisioning.ProvisionRefused):
            provisioning.migrate_profile(source, destination)
        assert not (destination / "config.json").exists()


class TestOneRule:
    def test_the_shared_decoder_strips_a_bom_and_leaves_plain_utf8_alone(self) -> None:
        from src.config.app_config import decode_settings_bytes

        assert decode_settings_bytes(_BOM + b"{}") == "{}"
        assert decode_settings_bytes(b"{}") == "{}"

    def test_the_shared_decoder_still_raises_on_bytes_that_are_not_utf8(self) -> None:
        from src.config.app_config import decode_settings_bytes

        with pytest.raises(UnicodeDecodeError):
            decode_settings_bytes(b"\xff\xfe{}")
