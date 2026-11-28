"""DSYNC-109: headless ``--configure --sis <district> --input <folder> --output <folder>``.

On a machine-wide install ``--schedule-register`` refuses until the district and both folders are saved, and the only
writer of those three was the window's Setup, which Server Core does not have. ``--configure`` is another CALLER of the
rules the window's folders card applies when it saves all three (``src.config.setup_rules``): the input folder must exist
and be a folder, the output folder's parent must exist, and a district set up on this computer must have passed a test
conversion as it now reads before it may become the one this install converts. Every "nothing was written" assertion
has a positive twin in the same class.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from src.config import district_setup
from src.config.app_config import AppConfig, config_file_path
from src.config.authoring import OverlaySpec, current_digest, write_overlay
from src.scheduler import headless, machine_setup, provision_session
from src.scheduler.register_gate import RegisterBlock
from src.utils.install_kind import InstallKind

SHIPPED_ID = "sd48myedbc"
OTHER_SHIPPED_ID = "sd74myedbc"
CUSTOM_ID = "sd93custom"
#: The command's syntax as the gate copy and the guides spell it.
SYNTAX = "DistrictSync.exe --configure --sis <district> --input <folder> --output <folder>"


def _cli(argv: list[str]) -> int:
    from src.main import cli

    return cli(argv)


@pytest.fixture()
def folders(tmp_path: Path) -> dict[str, Path]:
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    return {"input": tmp_path / "in", "output": tmp_path / "out"}


def _argv(sis: str, folders: dict[str, Path], **override: str) -> list[str]:
    values = {"--sis": sis, "--input": str(folders["input"]), "--output": str(folders["output"])}
    values.update({f"--{k}": v for k, v in override.items()})
    argv = ["--configure"]
    for flag, value in values.items():
        argv += [flag, value]
    return argv


def _saved() -> tuple[str, str, str]:
    cfg = AppConfig.load()
    return cfg.sis_type, cfg.input_dir, cfg.output_dir


def _disk_bytes() -> bytes | None:
    path = config_file_path()
    return path.read_bytes() if path.exists() else None


@pytest.fixture()
def overlay(monkeypatch: pytest.MonkeyPatch) -> str:
    """A REAL ``sd93custom`` overlay in the isolated user mappings dir, so its origin is ``"user"``."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    write_overlay(
        OverlaySpec(sd_number=93, district_name="SD93 - Configure Test", district_domains=(), base="myedbc"),
        overwrite=True,
    )
    return CUSTOM_ID


class TestItSavesTheThreeSettings:
    def test_a_shipped_district_and_two_folders_are_saved(self, folders, capsys):
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert _saved() == (SHIPPED_ID, str(folders["input"]), str(folders["output"]))
        assert AppConfig.load().is_complete()  # what --schedule-register's gate reads
        out = capsys.readouterr().out
        assert out.startswith("Saved:") and out.count("\n") == 1  # one confirmation line

    def test_the_district_is_saved_in_lower_case_as_a_conversion_reads_it(self, folders):
        assert _cli(_argv(SHIPPED_ID.upper(), folders)) == 0
        assert AppConfig.load().sis_type == SHIPPED_ID

    def test_relative_folders_are_saved_absolute(self, folders, monkeypatch):
        """A nightly runs from another working folder, so a relative path would point somewhere else."""
        monkeypatch.chdir(folders["input"].parent)
        assert _cli(["--configure", "--sis", SHIPPED_ID, "--input", "in", "--output", "out"]) == 0
        assert _saved()[1:] == (str(folders["input"]), str(folders["output"]))

    def test_other_settings_are_kept(self, folders):
        AppConfig(sftp_host="sftp.example.org", schedule_time="02:00").save()
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        cfg = AppConfig.load()
        assert (cfg.sftp_host, cfg.schedule_time) == ("sftp.example.org", "02:00")

    def test_the_output_folder_may_not_exist_yet_and_is_not_created(self, folders):
        """The window's rule: the output folder's PARENT must exist; the conversion creates the folder itself."""
        new_out = folders["output"] / "acsv"
        assert _cli(_argv(SHIPPED_ID, folders, output=str(new_out))) == 0
        assert AppConfig.load().output_dir == str(new_out)
        assert not new_out.exists()


class TestTheDistrictRules:
    def test_an_unknown_district_is_refused_and_nothing_is_written(self, folders, capsys):
        before = _disk_bytes()
        assert _cli(_argv("sd999nosuch", folders)) == 1
        out = capsys.readouterr().out
        assert out.startswith(district_setup.NOTHING_SAVED) and out.count("\n") == 1
        assert "sd999nosuch" in out
        assert _disk_bytes() == before

    def test_positive_twin_a_known_district_is_written(self, folders):
        before = _disk_bytes()
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert _disk_bytes() != before

    @pytest.mark.parametrize("bad", ["sd 48", "sd48;rm", "../sd48myedbc"])
    def test_an_invalid_district_id_is_refused(self, folders, bad):
        assert _cli(_argv(bad, folders)) == 1
        assert AppConfig.load().sis_type == ""

    def test_a_district_set_up_here_and_not_tested_is_refused(self, folders, overlay, capsys):
        assert _cli(_argv(overlay, folders)) == 1
        out = capsys.readouterr().out
        assert district_setup.NEEDS_TEST_LINE in out
        assert AppConfig.load().sis_type == ""
        assert AppConfig.load().input_dir == ""  # nothing at all, not the folders without the district

    def test_positive_twin_a_district_set_up_here_and_tested_is_saved(self, folders, overlay):
        assert AppConfig.load().creator_save(creator_verified={overlay: current_digest(overlay)})
        assert _cli(_argv(overlay, folders)) == 0
        assert AppConfig.load().sis_type == overlay

    def test_a_stale_test_is_refused_too(self, folders, overlay):
        assert AppConfig.load().creator_save(creator_verified={overlay: "b" * 64})
        assert _cli(_argv(overlay, folders)) == 1
        assert AppConfig.load().sis_type == ""

    def test_folders_only_on_the_district_already_converted_are_not_gated(self, folders, overlay, tmp_path):
        """The folders card's rule: the check guards a district CHANGE. Fixing a folder on the district this install
        already converts activates nothing, and refusing it would block the repair."""
        AppConfig(sis_type=overlay, input_dir="", output_dir="").save()
        assert _cli(_argv(overlay, folders)) == 0
        assert _saved() == (overlay, str(folders["input"]), str(folders["output"]))

    def test_the_rule_is_the_windows_one(self):
        """One comparison, many callers: the command reaches the SAME ``activation_allowed`` the window does."""
        from src.config import setup_rules
        from src.ui_flet import config_editor, filepicker

        assert config_editor.activation_allowed is setup_rules.activation_allowed
        assert filepicker.validate_input_dir is setup_rules.validate_input_dir
        assert filepicker.validate_output_dir is setup_rules.validate_output_dir
        assert district_setup.activation_allowed is setup_rules.activation_allowed


class TestTheFolderRules:
    def test_a_missing_input_folder_is_refused_and_nothing_is_written(self, folders, capsys):
        before = _disk_bytes()
        assert _cli(_argv(SHIPPED_ID, folders, input=str(folders["input"] / "missing"))) == 1
        out = capsys.readouterr().out
        assert out.startswith(district_setup.NOTHING_SAVED) and "--input" in out and out.count("\n") == 1
        assert _disk_bytes() == before

    def test_an_input_that_is_a_file_is_refused(self, folders):
        a_file = folders["input"] / "students.csv"
        a_file.write_text("x", encoding="utf-8")
        assert _cli(_argv(SHIPPED_ID, folders, input=str(a_file))) == 1
        assert AppConfig.load().sis_type == ""

    def test_an_output_whose_parent_is_missing_is_refused(self, folders, capsys):
        assert _cli(_argv(SHIPPED_ID, folders, output=str(folders["output"] / "no" / "such"))) == 1
        assert "--output" in capsys.readouterr().out
        assert AppConfig.load().output_dir == ""

    def test_an_output_that_is_a_file_is_refused(self, folders):
        a_file = folders["output"] / "file.txt"
        a_file.write_text("x", encoding="utf-8")
        assert _cli(_argv(SHIPPED_ID, folders, output=str(a_file))) == 1
        assert AppConfig.load().output_dir == ""

    def test_positive_twin_both_existing_folders_are_saved(self, folders):
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert _saved()[1:] == (str(folders["input"]), str(folders["output"]))


class TestASaveThatFails:
    def test_an_unwritable_settings_file_is_a_one_line_refusal(self, folders, monkeypatch, capsys):
        def denied(self):  # noqa: ANN001, ANN202
            raise PermissionError("denied")

        monkeypatch.setattr(AppConfig, "save", denied)
        assert _cli(_argv(SHIPPED_ID, folders)) == 1
        out = capsys.readouterr().out
        assert out == district_setup.SAVE_DENIED_LINE + "\n"

    def test_a_refused_overwrite_is_reported_not_raised(self, folders, monkeypatch, capsys):
        from src.config.app_config import SettingsOverwriteRefused

        def refused(self):  # noqa: ANN001, ANN202
            raise SettingsOverwriteRefused("moved")

        monkeypatch.setattr(AppConfig, "save", refused)
        assert _cli(_argv(SHIPPED_ID, folders)) == 1
        assert capsys.readouterr().out.startswith(district_setup.NOTHING_SAVED)


class TestARegisteredNightly:
    def test_a_change_under_a_registered_nightly_says_to_schedule_again(self, folders, capsys):
        AppConfig(sis_type=OTHER_SHIPPED_ID, schedule_registered=True, schedule_time="02:00").save()
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert district_setup.RESCHEDULE_NOTE in capsys.readouterr().out

    def test_positive_twin_no_nightly_no_note(self, folders, capsys):
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert district_setup.RESCHEDULE_NOTE not in capsys.readouterr().out


class TestTheLogNamesNoFolder:
    def test_the_log_carries_the_outcome_but_no_path(self, folders, caplog):
        with caplog.at_level(logging.DEBUG):
            assert district_setup.configure(
                sis=SHIPPED_ID, input_dir=str(folders["input"]), output_dir=str(folders["output"])
            ) == (0, district_setup.saved_line(SHIPPED_ID, str(folders["input"]), str(folders["output"])))
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "Headless configure" in logged  # positive twin: the sweep sees the command's lines
        assert str(folders["input"]) not in logged and str(folders["output"]) not in logged


class TestMisuse:
    @pytest.mark.parametrize("missing", ["--sis", "--input", "--output"])
    def test_each_flag_is_required(self, folders, missing, capsys):
        argv = _argv(SHIPPED_ID, folders)
        index = argv.index(missing)
        del argv[index : index + 2]
        assert _cli(argv) == 2
        assert missing in capsys.readouterr().out
        assert AppConfig.load().sis_type == ""

    @pytest.mark.parametrize(
        "other",
        [["--schedule-register"], ["--schedule-remove"], ["--machine-setup"], ["--sftp-show"], ["--migrate"]],
    )
    def test_it_is_one_command_at_a_time(self, folders, other):
        assert _cli(_argv(SHIPPED_ID, folders) + other) == 2
        assert AppConfig.load().sis_type == ""

    @pytest.mark.parametrize(
        "extra",
        [["--dry-run"], ["--sftp"], ["--quality"], ["--sftp-host", "h"], ["--updates", "off"], ["--time", "02:00"]],
    )
    def test_conversion_and_other_options_do_not_ride_along(self, folders, extra):
        assert _cli(_argv(SHIPPED_ID, folders) + extra) == 2
        assert AppConfig.load().sis_type == ""

    def test_positive_twin_the_three_flags_alone_run(self, folders):
        assert _cli(_argv(SHIPPED_ID, folders)) == 0

    def test_a_conversion_still_defaults_its_output_folder(self, folders, monkeypatch):
        """``--output`` lost its parser default so ``--configure`` can tell "not given"; a conversion keeps it."""
        import src.main as main_mod

        seen: dict[str, object] = {}

        def fake_pipeline(sis, input_dir, output_dir, **kwargs):  # noqa: ANN001, ANN003, ANN202
            seen["output"] = output_dir
            raise SystemExit(0)

        monkeypatch.setattr(main_mod, "run_pipeline", fake_pipeline)
        monkeypatch.setattr("src.update.health.mark_healthy", lambda: None)
        monkeypatch.setattr(main_mod, "_check_for_update_after_run", lambda args, logger: None)
        assert _cli(["--sis", SHIPPED_ID, "--input", str(folders["input"])]) == 0
        assert seen["output"] == main_mod.DEFAULT_OUTPUT_DIR == "data/output"


class TestBeforeMachineSetup:
    @pytest.fixture()
    def machine_not_set_up(self, monkeypatch):
        import src.main as main_mod

        calls: list[str] = []
        monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: InstallKind.MACHINE)
        monkeypatch.setattr(provision_session, "switch_state", lambda: False)
        monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", lambda: calls.append("migrate"))
        monkeypatch.setattr(main_mod, "pin_data_dir", lambda: calls.append("pin"))
        return calls

    def test_it_is_refused_before_any_profile_is_touched(self, machine_not_set_up, folders, capsys):
        assert _cli(_argv(SHIPPED_ID, folders)) == 1
        assert machine_setup.NOT_SET_UP_CLI in capsys.readouterr().out
        assert machine_not_set_up == []

    def test_positive_twin_a_set_up_computer_configures(self, machine_not_set_up, folders, monkeypatch):
        monkeypatch.setattr(provision_session, "switch_state", lambda: True)
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert "pin" in machine_not_set_up
        assert AppConfig.load().sis_type == SHIPPED_ID


class TestTheScheduleGateNamesTheCommand:
    def test_the_incomplete_line_names_the_window_and_the_command(self):
        line = headless.GATE_COPY[RegisterBlock.INCOMPLETE]
        assert "Setup" in line
        assert SYNTAX in line

    def test_every_flag_in_the_line_is_one_the_parser_accepts(self):
        source = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
        import re

        shown = set(re.findall(r"(--[a-z][a-z-]+)", headless.GATE_COPY[RegisterBlock.INCOMPLETE]))
        assert shown == {"--configure", "--sis", "--input", "--output"}
        assert all(f'"{flag}"' in source for flag in shown)

    def test_schedule_register_prints_it_until_configure_has_run(self, folders, capsys):
        assert _cli(["--schedule-register", "--time", "02:00"]) == 1
        assert capsys.readouterr().out.strip() == headless.GATE_COPY[RegisterBlock.INCOMPLETE]
        # Positive twin: after --configure the gate no longer stops on this rung.
        assert _cli(_argv(SHIPPED_ID, folders)) == 0
        assert AppConfig.load().is_complete()


def test_the_settings_file_holds_exactly_what_was_given(folders):
    assert _cli(_argv(SHIPPED_ID, folders)) == 0
    data = json.loads(config_file_path().read_text(encoding="utf-8"))
    assert (data["sis_type"], data["input_dir"], data["output_dir"]) == _saved()
