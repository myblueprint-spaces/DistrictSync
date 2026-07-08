"""Tests for the COUNTED pure Setup helper (``filepicker.setup_state``).

Pins the structural Save gate (RC3): both-valid → can_save → persists →
``is_complete()``; one-invalid → cannot save (an invalid path can never reach
``AppConfig.save()``). The view (``screens/setup.py``) is coverage-omitted and
exercised via DISTRICTSYNC_UI=flet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.ui_flet.filepicker import setup_state


@pytest.fixture
def tmp_app_config(isolated_user_profile: Path) -> Path:
    """The isolated app-data dir (AppConfig ``save()`` is sandboxed suite-wide).

    Isolation is provided by the conftest autouse ``isolated_user_profile`` fixture
    (``paths.user_data_dir`` → per-test tmp); this just surfaces that dir.
    """
    return isolated_user_profile


class TestSetupStateGate:
    def test_both_valid_can_save(self, tmp_path: Path):
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        out_dir = tmp_path / "output"  # need not exist; parent (tmp_path) does
        state = setup_state(str(in_dir), str(out_dir), "myedbc")
        assert state.input_result.ok is True
        assert state.output_result.ok is True
        assert state.can_save is True

    def test_invalid_input_blocks_save(self, tmp_path: Path):
        missing_in = tmp_path / "nope"
        out_dir = tmp_path / "output"
        state = setup_state(str(missing_in), str(out_dir), "myedbc")
        assert state.input_result.ok is False
        assert state.can_save is False  # block-not-flag: cannot reach save()

    def test_invalid_output_blocks_save(self, tmp_path: Path):
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        a_file = tmp_path / "out.csv"
        a_file.write_text("x", encoding="utf-8")
        # parent of `<file>/sub` is a file → invalid output
        state = setup_state(str(in_dir), str(a_file / "sub"), "myedbc")
        assert state.output_result.ok is False
        assert state.can_save is False

    def test_blank_sis_blocks_save(self, tmp_path: Path):
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        out_dir = tmp_path / "output"
        assert setup_state(str(in_dir), str(out_dir), "").can_save is False
        assert setup_state(str(in_dir), str(out_dir), "   ").can_save is False


class TestSetupPersistence:
    def test_valid_state_persists_and_flips_is_complete(self, tmp_path: Path, tmp_app_config: Path):
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        out_dir = tmp_path / "output"

        state = setup_state(str(in_dir), str(out_dir), "myedbc")
        assert state.can_save is True

        # Simulate the surface's save path (only reached when can_save holds).
        cfg = AppConfig.load()
        assert cfg.is_complete() is False  # nothing configured yet
        cfg.input_dir = str(in_dir)
        cfg.output_dir = str(out_dir)
        cfg.sis_type = "myedbc"
        cfg.save()

        assert (tmp_app_config / "config.json").exists()
        reloaded = AppConfig.load()
        assert reloaded.input_dir == str(in_dir)
        assert reloaded.output_dir == str(out_dir)
        assert reloaded.sis_type == "myedbc"
        assert reloaded.is_complete() is True
