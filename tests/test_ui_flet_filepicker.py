"""Tests for src/ui_flet/filepicker.py — the COUNTED boundary logic.

Covers the trust-critical pieces (the async dialog glue is ``# pragma: no cover``
— it needs a live Flet loop / native window, exercised via DISTRICTSYNC_UI=flet):
  * ``validate_input_dir`` — exists+is_dir, missing, file-as-path
  * ``validate_output_dir`` — ok, parent-is-file
  * ``check_writable`` — tmp-writable vs unwritable (effectful, not "pure")
  * ``_ensure_picker`` — idempotent ``page.services`` append (mock page)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.ui_flet.filepicker import (
    ValidationResult,
    _ensure_picker,
    check_writable,
    validate_input_dir,
    validate_output_dir,
)


class TestValidateInputDir:
    def test_existing_directory_is_ok(self, tmp_path: Path):
        result = validate_input_dir(str(tmp_path))
        assert isinstance(result, ValidationResult)
        assert result.ok is True

    def test_missing_path_is_rejected(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist"
        result = validate_input_dir(str(missing))
        assert result.ok is False
        assert result.message  # actionable message, never blank

    def test_file_as_path_is_rejected(self, tmp_path: Path):
        a_file = tmp_path / "extract.csv"
        a_file.write_text("data", encoding="utf-8")
        result = validate_input_dir(str(a_file))
        assert result.ok is False  # a file is not a directory

    def test_empty_string_is_rejected(self):
        assert validate_input_dir("").ok is False
        assert validate_input_dir("   ").ok is False


class TestValidateOutputDir:
    def test_existing_directory_is_ok(self, tmp_path: Path):
        assert validate_output_dir(str(tmp_path)).ok is True

    def test_nonexistent_dir_with_real_parent_is_ok(self, tmp_path: Path):
        # The output dir itself need not exist yet — the loader creates it.
        target = tmp_path / "output_csvs"
        result = validate_output_dir(str(target))
        assert result.ok is True

    def test_parent_is_a_file_is_rejected(self, tmp_path: Path):
        a_file = tmp_path / "not_a_dir"
        a_file.write_text("x", encoding="utf-8")
        # Parent of `<file>/sub` is the file itself — an impossible location.
        result = validate_output_dir(str(a_file / "sub"))
        assert result.ok is False

    def test_path_that_is_a_file_is_rejected(self, tmp_path: Path):
        a_file = tmp_path / "out.csv"
        a_file.write_text("x", encoding="utf-8")
        assert validate_output_dir(str(a_file)).ok is False

    def test_empty_string_is_rejected(self):
        assert validate_output_dir("").ok is False


class TestCheckWritable:
    def test_writable_tmp_dir(self, tmp_path: Path):
        assert check_writable(str(tmp_path)) is True

    def test_nonexistent_dir_falls_back_to_writable_parent(self, tmp_path: Path):
        target = tmp_path / "new_subdir"
        assert check_writable(str(target)) is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't gate os.access on Windows")
    def test_unwritable_dir_is_rejected(self, tmp_path: Path):
        locked = tmp_path / "locked"
        locked.mkdir()
        os.chmod(locked, 0o500)  # r-x: not writable
        try:
            assert check_writable(str(locked)) is False
        finally:
            os.chmod(locked, 0o700)  # restore so tmp cleanup can remove it


class _FakeFilePicker:
    """Stand-in matching ``isinstance(service, ft.FilePicker)`` via monkeypatch."""


class _FakePage:
    """Mock page whose ``.services`` is a plain list (the registration contract)."""

    def __init__(self) -> None:
        self.services: list[object] = []


class TestEnsurePickerIdempotent:
    def test_appends_exactly_once_across_two_calls(self, monkeypatch):
        # Make ``ft.FilePicker`` the cheap fake and ``isinstance`` recognise it,
        # so we test the idempotent-append contract without a live Flet loop.
        import src.ui_flet.filepicker as fp_mod

        monkeypatch.setattr(fp_mod.ft, "FilePicker", _FakeFilePicker)
        page = _FakePage()

        first = _ensure_picker(page)
        assert len(page.services) == 1
        assert isinstance(first, _FakeFilePicker)

        second = _ensure_picker(page)
        assert len(page.services) == 1  # NOT re-appended
        assert second is first  # same registered service reused
