"""Tests for src/utils/version.py — app_version()."""

from __future__ import annotations

import importlib.metadata
from unittest.mock import patch

from src.utils.version import app_version


class TestAppVersion:
    def test_returns_installed_version_string(self):
        """When the package metadata is present, return its version string."""
        with patch("importlib.metadata.version", return_value="3.3.1") as mock_ver:
            assert app_version() == "3.3.1"
        mock_ver.assert_called_once_with("districtsync")

    def test_dev_fallback_when_not_packaged(self):
        """A missing package (source checkout, never pip-installed) reports 'dev'."""
        with patch(
            "importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError(),
        ):
            assert app_version() == "dev"

    def test_returns_a_nonempty_string_in_this_environment(self):
        """In the test env districtsync is installed; the value is a real string."""
        value = app_version()
        assert isinstance(value, str)
        assert value
