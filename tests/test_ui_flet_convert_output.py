"""Unit tests for the COUNTED Convert output-folder + run-gate logic (Plan 0029, Slice 9).

Covers the path-bearing trust logic that keeps the silent fallbacks out (D9/D10):
- ``can_run_convert`` gating table (no district / empty output dir / invalid input / all set);
- ``output_dir_is_set`` blank/whitespace/None handling;
- ``resolved_output_caption`` derivation (set → names the folder; unset → routed message);
- ``open_folder`` per-OS dispatch (Windows/macOS/Linux) + blank-path and failure handling —
  all mocked, so no real file browser opens under test.

The path lives HERE, never in ``ConvertResult`` — ``test_ui_flet_convert_result`` pins the
result model stays path-free; this module is its deliberate counterpart.
"""

from __future__ import annotations

import pytest

from src.ui_flet import convert_output
from src.ui_flet.convert_output import (
    can_run_convert,
    open_folder,
    output_dir_is_set,
    resolved_output_caption,
)


class TestCanRunConvert:
    """The run-gate is True ONLY when all three input gates are satisfied (no silent fallback)."""

    def test_all_gates_present_runs(self) -> None:
        assert can_run_convert(district_chosen=True, output_dir_set=True, input_valid=True) is True

    def test_no_district_blocks(self) -> None:
        # D9: no explicit district → refuse (no alphabetical configs[0] guess).
        assert can_run_convert(district_chosen=False, output_dir_set=True, input_valid=True) is False

    def test_empty_output_dir_blocks(self) -> None:
        # D10: no output folder → refuse (no silent write into the input folder).
        assert can_run_convert(district_chosen=True, output_dir_set=False, input_valid=True) is False

    def test_invalid_input_blocks(self) -> None:
        assert can_run_convert(district_chosen=True, output_dir_set=True, input_valid=False) is False

    def test_all_gates_missing_blocks(self) -> None:
        assert can_run_convert(district_chosen=False, output_dir_set=False, input_valid=False) is False


class TestOutputDirIsSet:
    @pytest.mark.parametrize("value", ["", "   ", "\t", None])
    def test_blank_is_not_set(self, value: str | None) -> None:
        assert output_dir_is_set(value) is False

    @pytest.mark.parametrize("value", ["/out", r"C:\Users\admin\output", "  /out  "])
    def test_real_path_is_set(self, value: str) -> None:
        assert output_dir_is_set(value) is True


class TestResolvedOutputCaption:
    def test_set_names_the_folder_and_routes_to_settings(self) -> None:
        caption = resolved_output_caption(r"C:\Users\admin\output")
        assert r"C:\Users\admin\output" in caption
        assert "Files will be written to" in caption
        assert "change it in Settings" in caption

    def test_set_trims_surrounding_whitespace(self) -> None:
        assert "Files will be written to /out —" in resolved_output_caption("  /out  ")

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_unset_shows_the_routed_blocked_message(self, value: str | None) -> None:
        caption = resolved_output_caption(value)
        assert "Set your output folder in Settings first" in caption


class TestOpenFolder:
    """Per-OS dispatch, fully mocked — no real file browser opens under test."""

    def test_blank_path_does_not_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list = []
        monkeypatch.setattr(convert_output.subprocess, "run", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(convert_output.os, "startfile", lambda *a, **k: calls.append(a), raising=False)
        assert open_folder("") is False
        assert open_folder("   ") is False
        assert calls == []

    def test_windows_uses_startfile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "win32")
        monkeypatch.setattr(convert_output.os, "startfile", lambda p: recorded.append(p), raising=False)
        # subprocess must NOT be used on Windows.
        monkeypatch.setattr(convert_output.subprocess, "run", lambda *a, **k: pytest.fail("subprocess used on Windows"))
        assert open_folder(r"C:\out") is True
        assert recorded == [r"C:\out"]

    def test_macos_uses_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "darwin")
        monkeypatch.setattr(convert_output.subprocess, "run", lambda args, **k: recorded.append(args))
        assert open_folder("/out") is True
        assert recorded == [["open", "/out"]]

    def test_linux_uses_xdg_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "linux")
        monkeypatch.setattr(convert_output.subprocess, "run", lambda args, **k: recorded.append(args))
        assert open_folder("/out") is True
        assert recorded == [["xdg-open", "/out"]]

    def test_dispatch_failure_returns_false_and_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_a, **_k):
            raise OSError("no display / no file browser")

        monkeypatch.setattr(convert_output.sys, "platform", "linux")
        monkeypatch.setattr(convert_output.subprocess, "run", _boom)
        assert open_folder("/out") is False  # calm degradation, not a crash
