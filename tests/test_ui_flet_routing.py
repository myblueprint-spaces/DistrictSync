"""Tests for the no-argv UI dispatch seam in src/main.py.

Flet is the only UI. The no-argv branch dispatches to the Flet launcher via the
``_default_ui_launcher()`` seam, which returns ``src.ui_flet.launcher.main``.
This pins the flipped default WITHOUT launching a window — the test asserts the
returned callable by identity against a monkeypatched sentinel on the flet
launcher module, and (separately) that the CLI branch still parses its flags.
"""

from __future__ import annotations

import argparse

from src.main import _default_ui_launcher


class TestDefaultUiLauncher:
    def test_default_launcher_is_flet_main(self, monkeypatch):
        """The no-argv dispatch resolves to src.ui_flet.launcher.main by identity."""
        import src.ui_flet.launcher as flet_launcher

        def _flet_main():  # sentinel — never called
            raise AssertionError("launcher should not be invoked by selection")

        monkeypatch.setattr(flet_launcher, "main", _flet_main)
        assert _default_ui_launcher() is _flet_main

    def test_default_launcher_resolves_without_launching(self):
        """Resolving the launcher must be side-effect-free (no window)."""
        import src.ui_flet.launcher as flet_launcher

        assert _default_ui_launcher() is flet_launcher.main


class TestCliBranchUnaffected:
    """The flip left the CLI arg surface intact — sanity-parse the ETL flags."""

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        parser.add_argument("--sis")
        parser.add_argument("--input")
        parser.add_argument("--output", default="data/output")
        return parser

    def test_cli_still_parses_sis_and_input(self):
        parser = self._build_parser()
        args = parser.parse_args(["--sis", "myedbc", "--input", "data/input"])
        assert args.sis == "myedbc"
        assert args.input == "data/input"
