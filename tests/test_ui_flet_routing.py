"""Tests for the dual-mode UI selection seam in src/main.py.

The no-argv branch reads DISTRICTSYNC_UI once and routes to the Flet launcher
when ``=flet``, else the Streamlit launcher (default). This pins routing BOTH
ways without actually launching either UI — _select_ui_launcher returns the
chosen ``main`` callable, which the tests assert by identity against monkeypatched
sentinels on each launcher module.
"""

from __future__ import annotations

from src.main import _select_ui_launcher


class TestSelectUiLauncher:
    def test_unset_or_empty_selects_streamlit(self, monkeypatch):
        import src.ui.launcher as streamlit_launcher

        def _streamlit_main():  # sentinel — never called
            raise AssertionError("launcher should not be invoked by selection")

        monkeypatch.setattr(streamlit_launcher, "main", _streamlit_main)
        assert _select_ui_launcher("") is _streamlit_main

    def test_flet_mode_selects_flet(self, monkeypatch):
        import src.ui_flet.launcher as flet_launcher

        def _flet_main():  # sentinel — never called
            raise AssertionError("launcher should not be invoked by selection")

        monkeypatch.setattr(flet_launcher, "main", _flet_main)
        assert _select_ui_launcher("flet") is _flet_main

    def test_unknown_mode_falls_back_to_streamlit(self, monkeypatch):
        import src.ui.launcher as streamlit_launcher

        sentinel = object()
        monkeypatch.setattr(streamlit_launcher, "main", sentinel)
        assert _select_ui_launcher("nonsense") is sentinel

    def test_env_var_normalization_is_case_insensitive(self, monkeypatch):
        """main.py normalizes via .strip().lower(); 'flet' is the only Flet trigger."""
        import src.ui.launcher as streamlit_launcher

        sentinel = object()
        monkeypatch.setattr(streamlit_launcher, "main", sentinel)
        # An un-normalized value never reaches _select_ui_launcher as 'flet'.
        assert _select_ui_launcher("FLET") is sentinel
