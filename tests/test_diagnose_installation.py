"""Plan 0055 D2 (DSYNC-102): ``--diagnose``'s Installation block. Read-only, total, and honest about each fact."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.utils import diagnostics, installation


def _report() -> str:
    out = io.StringIO()
    assert diagnostics.run_diagnose(out) == 0
    return out.getvalue()


class TestTheMsiVersion:
    def test_it_reads_the_districtsync_msi_entry(self, monkeypatch):
        monkeypatch.setattr(
            installation,
            "_uninstall_entries",
            lambda: [
                {"DisplayName": "Other", "DisplayVersion": "9.9", "WindowsInstaller": 1},
                {"DisplayName": "DistrictSync", "DisplayVersion": "3.31.0", "WindowsInstaller": 0},  # not an MSI
                {"DisplayName": "DistrictSync", "DisplayVersion": "3.32.0", "WindowsInstaller": 1},
            ],
        )
        assert installation.msi_product_version() == "3.32.0"

    def test_none_without_an_msi_or_when_unreadable(self, monkeypatch):
        monkeypatch.setattr(installation, "_uninstall_entries", lambda: [])
        assert installation.msi_product_version() is None

        def boom():
            raise OSError("denied")

        monkeypatch.setattr(installation, "_uninstall_entries", boom)
        assert installation.msi_product_version() is None


class TestThePerUserCopy:
    def test_it_is_found_by_its_update_exe(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert installation.per_user_copy_present() is False
        (tmp_path / "myBlueprint.DistrictSync").mkdir()
        (tmp_path / "myBlueprint.DistrictSync" / "Update.exe").write_bytes(b"")
        assert installation.per_user_copy_present() is True


class TestTheUpdaterLog:
    def test_the_last_line_only(self, tmp_path):
        log = tmp_path / "updater.log"
        log.write_text("a\nb\n\nlast line\n", encoding="utf-8")
        assert installation.updater_last_line(log) == "last line"

    def test_missing_is_none(self, tmp_path):
        assert installation.updater_last_line(tmp_path / "nope.log") is None

    def test_a_huge_log_is_read_from_its_tail(self, tmp_path):
        log = tmp_path / "updater.log"
        log.write_text("x" * 100_000 + "\nthe end\n", encoding="utf-8")
        assert installation.updater_last_line(log) == "the end"


class TestTheBlock:
    def test_it_is_in_the_report_with_every_line(self, monkeypatch):
        monkeypatch.setattr(installation, "_uninstall_entries", lambda: [])
        report = _report()
        assert "Installation:" in report
        for label in (
            "install folder",
            "running version",
            "MSI product version",
            "copy for one account too",
            "updater folder",
            "updater last run",
            "last update check",
            "newer version available",
        ):
            assert f"{label}:" in report, label
        assert diagnostics.NO_MSI in report and diagnostics.NOT_INSTALLED in report

    @pytest.mark.parametrize(
        ("exists", "trusted", "expected"),
        [
            (False, False, diagnostics.UPDATER_FOLDER_ABSENT),
            (True, True, diagnostics.UPDATER_FOLDER_TRUSTED),
            (True, False, diagnostics.UPDATER_FOLDER_UNTRUSTED),
        ],
    )
    def test_the_updater_folder_line(self, monkeypatch, tmp_path, exists, trusted, expected):
        from src.update import service

        folder = tmp_path / "DistrictSync-Updater"
        if exists:
            folder.mkdir()
        monkeypatch.setattr(service, "state_dir", lambda: folder)
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: trusted)
        assert diagnostics._updater_folder() == expected

    def test_a_raising_reader_never_stops_the_report(self, monkeypatch):
        def boom():
            raise RuntimeError("registry")

        monkeypatch.setattr(installation, "msi_product_version", boom)
        import re

        assert re.search(r"MSI product version:\s+unavailable \(RuntimeError\)", _report())

    def test_before_machine_setup_no_profile_is_touched(self, monkeypatch):
        from src.scheduler import machine_setup
        from src.utils import paths

        monkeypatch.setattr(machine_setup, "needs_machine_setup", lambda: True)
        monkeypatch.setattr(paths, "user_data_dir", lambda: pytest.fail("pinned a per-user profile"))
        monkeypatch.setattr(installation, "_uninstall_entries", lambda: [])
        assert "Installation:" in _report()


def test_the_module_writes_nothing():
    """Read-only by construction: no open-for-write, no mkdir, no registry write in the module."""
    source = (Path(__file__).resolve().parent.parent / "src" / "utils" / "installation.py").read_text(encoding="utf-8")
    for writer in ('"w"', '"a"', '"wb"', ".mkdir(", ".write_", "SetValueEx", "CreateKey"):
        assert writer not in source, writer


class TestUntrustedTextNeverReachesTheReport:
    """Review B1: the nightly's notice and an untrusted updater folder are written by others."""

    def test_the_notice_is_reduced_to_codes_and_a_parsed_version(self, monkeypatch):
        from src.update import check

        monkeypatch.setattr(
            check,
            "last_check_summary",
            lambda state_dir=None: {
                "at": "2026-11-27T02:00:00+00:00\nfake: row",
                "status": "available\nfake: row",
                "reason": "newer_available",
                "version": "9.9.9\nfake: row",
            },
        )
        line = diagnostics._notice_lines()[0]
        assert "fake" not in line and "\n" not in line and "9.9.9" in line and "newer_available" in line

    def test_an_untrusted_folder_s_log_is_not_shown(self, monkeypatch, tmp_path):
        from src.update import service

        folder = tmp_path / "DistrictSync-Updater"
        folder.mkdir()
        (folder / service.LOG_FILE).write_text("planted text\n", encoding="utf-8")
        monkeypatch.setattr(service, "state_dir", lambda: folder)
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: False)
        assert "planted" not in diagnostics._updater_last_run()
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: True)
        assert diagnostics._updater_last_run() == "planted text"  # positive twin

    def test_a_log_line_is_one_printable_line(self):
        assert diagnostics._clean("a\x1b[31mb\tc") == "a[31mbc"


def test_both_msi_entry_names_are_recognised(monkeypatch):
    for name in installation.MSI_ENTRY_NAMES:
        monkeypatch.setattr(
            installation,
            "_uninstall_entries",
            lambda n=name: [{"DisplayName": n, "DisplayVersion": "3.4.0", "WindowsInstaller": 1}],
        )
        assert installation.msi_product_version() == "3.4.0"
