"""Gate test — the Flet stack is exact-pinned and matches the conventions doc.

Flet 1.0 is a beta API; an unnoticed version bump would silently break the
documented forms. This converts the API-drift safeguard from doc-only to
gate-enforced: the installed packages, requirements.txt, and
docs/FLET_1.0_CONVENTIONS.md must all name the SAME exact version.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

# Single source of truth for the expected pin — change here AND in the files below.
EXPECTED_FLET_VERSION = "0.85.3"

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestFletPin:
    @pytest.mark.parametrize("package", ["flet", "flet-desktop", "flet-web"])
    def test_installed_package_is_exact_pin(self, package):
        assert importlib.metadata.version(package) == EXPECTED_FLET_VERSION

    def test_requirements_names_exact_version(self):
        text = (_REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert f"flet=={EXPECTED_FLET_VERSION}" in text
        assert f"flet-desktop=={EXPECTED_FLET_VERSION}" in text

    def test_requirements_dev_names_exact_version(self):
        text = (_REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        assert f"flet-web=={EXPECTED_FLET_VERSION}" in text
        assert f"flet-cli=={EXPECTED_FLET_VERSION}" in text

    def test_conventions_doc_names_exact_version(self):
        text = (_REPO_ROOT / "docs" / "FLET_1.0_CONVENTIONS.md").read_text(encoding="utf-8")
        assert EXPECTED_FLET_VERSION in text
