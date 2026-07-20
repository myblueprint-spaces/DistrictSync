"""Tests for src/utils/helpers.py — utility functions.

The zip-naming helpers (``district_slug`` / ``build_zip_name``) live with their
SFTP consumer now — see ``tests/test_sftp_uploader.py``.
"""

import types
from unittest.mock import patch

import pandas as pd

from src.utils import helpers
from src.utils.helpers import (
    ensure_directory,
    normalize_columns,
    subprocess_no_window_flags,
)


class TestEnsureDirectory:
    def test_creates_directory(self, tmp_path):
        new_dir = tmp_path / "a" / "b" / "c"
        result = ensure_directory(new_dir)
        assert result == new_dir
        assert new_dir.is_dir()

    def test_existing_directory(self, tmp_path):
        result = ensure_directory(tmp_path)
        assert result == tmp_path


class TestNormalizeColumns:
    def test_strips_and_lowercases(self):
        df = pd.DataFrame(columns=["  Name  ", "AGE", "  School Number  "])
        result = normalize_columns(df)
        assert list(result.columns) == ["name", "age", "school number"]

    def test_does_not_mutate_original(self):
        df = pd.DataFrame(columns=["Name", "Age"])
        normalize_columns(df)
        assert list(df.columns) == ["Name", "Age"]


class TestSubprocessNoWindowFlags:
    """The single-source no-console flag every Windows-facing subprocess.run must pass.

    On Windows the windowed exe would otherwise flash a console for every PowerShell/
    schtasks/icacls child (e.g. the schedule read-back on a nav click); the helper returns
    ``CREATE_NO_WINDOW`` there and a harmless 0 on POSIX (where the flag is a no-op).
    """

    def test_win32_returns_create_no_window(self):
        # Simulate a Windows host: platform win32 + a subprocess module exposing the flag.
        fake_subprocess = types.SimpleNamespace(CREATE_NO_WINDOW=0x08000000)
        with (
            patch.object(helpers.sys, "platform", "win32"),
            patch.object(helpers, "subprocess", fake_subprocess),
        ):
            assert subprocess_no_window_flags() == 0x08000000

    def test_non_windows_returns_zero(self):
        with patch.object(helpers.sys, "platform", "linux"):
            assert subprocess_no_window_flags() == 0
