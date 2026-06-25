"""Tests for src.main._resolve_version() — the version-resolution chain.

The released exe reports a version stamped into ``src/_version.py`` at build
time; these cover that file winning, the package-metadata fallback, and the
final ``"dev"`` fallback for an unbuilt source checkout.
"""

from __future__ import annotations

import importlib.metadata
import sys
import types

from src.main import _resolve_version


def test_prefers_stamped_version_file(monkeypatch):
    fake = types.ModuleType("src._version")
    fake.version = "9.9.9"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src._version", fake)
    assert _resolve_version() == "9.9.9"


def test_falls_back_to_package_metadata(monkeypatch):
    # Force `from src._version import version` to raise regardless of any
    # on-disk generated file (None in sys.modules → ImportError), so the
    # package-metadata branch is exercised.
    monkeypatch.setitem(sys.modules, "src._version", None)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")
    assert _resolve_version() == "1.2.3"


def test_falls_back_to_dev_when_unbuilt(monkeypatch):
    monkeypatch.setitem(sys.modules, "src._version", None)

    def _raise(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)
    assert _resolve_version() == "dev"
