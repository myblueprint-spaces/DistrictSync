"""Tests for src/utils/version.py — app_version().

app_version() resolves in order: the build-stamped ``src/_version.py`` (written
from the git tag in flet-pack.yml), then installed package metadata, then the
``"dev"`` fallback. The metadata/dev tests force ``src._version`` absent so they
exercise those branches regardless of any on-disk build artifact.
"""

from __future__ import annotations

import importlib.metadata
import sys
import types
from unittest.mock import patch

from src.utils.version import app_version


class TestAppVersion:
    def test_prefers_stamped_version_file(self):
        """A build-stamped src/_version.py wins over package metadata."""
        fake = types.ModuleType("src._version")
        fake.version = "9.9.9"  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"src._version": fake}):
            assert app_version() == "9.9.9"

    def test_returns_installed_version_string(self):
        """No stamped file → return the installed package version string."""
        with (
            patch.dict(sys.modules, {"src._version": None}),
            patch("importlib.metadata.version", return_value="3.3.1") as mock_ver,
        ):
            assert app_version() == "3.3.1"
        mock_ver.assert_called_once_with("districtsync")

    def test_dev_fallback_when_not_packaged(self):
        """No stamped file + missing package (source checkout) reports 'dev'."""
        with (
            patch.dict(sys.modules, {"src._version": None}),
            patch(
                "importlib.metadata.version",
                side_effect=importlib.metadata.PackageNotFoundError(),
            ),
        ):
            assert app_version() == "dev"

    def test_returns_a_nonempty_string_in_this_environment(self):
        """In the test env districtsync is installed; the value is a real string."""
        value = app_version()
        assert isinstance(value, str)
        assert value
