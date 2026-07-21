"""Tests for the no-argv UI dispatch seam in src/main.py.

Flet is the only UI. The no-argv branch dispatches to the Flet launcher via the
``_default_ui_launcher()`` seam, which returns ``src.ui_flet.launcher.main``.
This pins the flipped default WITHOUT launching a window — the test asserts the
returned callable by identity against a monkeypatched sentinel on the flet
launcher module, and (separately) that the CLI branch still parses its flags.
"""

from __future__ import annotations

import src.main as main_mod
from src.etl.pipeline import PipelineResult
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
    """The flip left the CLI arg surface intact — proven on the REAL parser.

    This used to build a throwaway ``argparse.ArgumentParser`` in the test and
    assert that argparse parsed it: a test of the standard library, not of
    DistrictSync. Since ``src.main.cli`` became importable the actual parser is
    driven instead, so a flag renamed or dropped in ``main.py`` turns this red.
    """

    def test_cli_passes_the_parsed_flags_through_to_the_pipeline(self, tmp_path, monkeypatch):
        from src.main import cli

        seen: dict[str, object] = {}

        def _spy(sis_type, input_path, output_path, **kwargs):
            seen.update({"sis": sis_type, "input": input_path, "output": output_path, **kwargs})
            return PipelineResult(entity_counts={}, sftp_attempted=False, sftp_ok=False, anomalies=[])

        monkeypatch.setattr(main_mod, "run_pipeline", _spy)

        assert cli(["--sis", "myedbc", "--input", str(tmp_path), "--output", str(tmp_path / "out"), "--quality"]) == 0
        assert seen["sis"] == "myedbc"
        assert seen["input"] == str(tmp_path)
        assert seen["output"] == str(tmp_path / "out")
        assert seen["quality"] is True
        assert seen["dry_run"] is False
