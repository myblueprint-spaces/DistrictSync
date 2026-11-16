"""DSYNC-62 (plan 0054 S3): DistrictSync's seat for the shared updater package.

Two kinds of test. The first group uses a stand-in module and runs everywhere. The second group needs the REAL
private package and uses ``pytest.importorskip``, so a fork or Dependabot pull request (which never receives the
read key) stays green without it. On a push to main CI sets ``DISTRICTSYNC_REQUIRE_UPDATER=1``, and
``test_the_package_is_present_when_it_is_required`` then FAILS if the package is absent: a skipped proof can never
count as a passing one.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

from src.update import package
from tests.test_installed_flavour import _stamp, _unstamped

_ROOT = Path(__file__).resolve().parent.parent


class _FakeUpdater(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("myb_desktop_updater")
        self.identities: list[tuple[str, str, str]] = []
        outer = self

        def app_identity(name: str, prefix: str, folder: str) -> tuple[str, str, str]:
            outer.identities.append((name, prefix, folder))
            return (name, prefix, folder)

        self.AppIdentity = app_identity


class TestLoadUpdater:
    def test_portable_build_never_imports_the_package(self, monkeypatch):
        _unstamped(monkeypatch)
        imported: list[str] = []
        monkeypatch.setattr(package.importlib, "import_module", lambda name: imported.append(name))
        assert package.load_updater() is None
        assert package.app_identity() is None
        assert imported == []

    def test_installed_build_imports_it(self, monkeypatch):
        """Positive twin of the portable case."""
        _stamp(monkeypatch, "installed")
        fake = _FakeUpdater()
        monkeypatch.setitem(sys.modules, "myb_desktop_updater", fake)
        assert package.load_updater() is fake

    def test_installed_build_without_the_package_raises(self, monkeypatch):
        _stamp(monkeypatch, "installed")
        monkeypatch.setitem(sys.modules, "myb_desktop_updater", None)
        with pytest.raises(ImportError):
            package.load_updater()

    def test_identity_is_built_from_the_wiring_constants(self):
        fake = _FakeUpdater()
        assert package.app_identity(fake) == ("DistrictSync", "DISTRICTSYNC", "DistrictSync")

    def test_pack_id_is_never_the_profile_folder(self):
        """Uninstall deletes the install folder; the profile (and its history) must never live there."""
        assert package.PACK_ID == "myBlueprint.DistrictSync"
        assert package.PACK_ID != package.STATE_FOLDER

    def test_the_module_names_the_package_through_a_variable(self):
        import re

        code = (_ROOT / "src" / "update" / "package.py").read_text(encoding="utf8")
        body = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith(("#", '"')))
        assert not re.search(r"^\s*(import|from)\s+myb_desktop_updater\b", body, re.M)
        assert not re.search(r"import_module\(\s*['\"]myb_desktop_updater", body)


def test_the_package_is_present_when_it_is_required():
    """On a push to main (``DISTRICTSYNC_REQUIRE_UPDATER=1``) the real package must import; a skip is a failure."""
    if not os.environ.get("DISTRICTSYNC_REQUIRE_UPDATER"):
        pytest.skip("not required here: a fork or local run without the private package")
    importlib.import_module("myb_desktop_updater")
