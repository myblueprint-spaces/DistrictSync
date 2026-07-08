"""Tests for the pure helpers in src/ui_flet/launcher.py.

The launcher module itself is coverage-omitted (it's view glue), but its pure
helpers are trust-critical (where the early-failure traceback lands, the
frozen-cwd resolution, the plain-language message) and are tested here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.ui_flet import launcher


class TestResolveFrozenCwd:
    def test_not_frozen_returns_none(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert launcher.resolve_frozen_cwd() is None

    def test_frozen_returns_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert launcher.resolve_frozen_cwd() == Path(str(tmp_path))


class TestResolveLogPath:
    def test_uses_user_log_file_sink(self):
        path = launcher.resolve_log_path()
        assert isinstance(path, Path)
        assert path.name == "etl_tool.log"

    def test_fallback_when_helper_unavailable(self, monkeypatch):
        def _boom() -> Path:
            raise RuntimeError("no sink")

        monkeypatch.setattr(launcher, "user_log_file", _boom)
        path = launcher.resolve_log_path()
        # If the paths seam itself is broken, the launcher falls back to a bare
        # filename (cwd) — it deliberately does NOT re-derive the app-data location,
        # which would duplicate the single paths.py source of truth.
        assert path.name == "etl_tool.log"
        assert path == Path("etl_tool.log")


class TestFormatUserError:
    def test_is_plain_language_without_traceback(self):
        try:
            raise RuntimeError("ImportError: cannot import flet internals at 0xdeadbeef")
        except RuntimeError as exc:
            message = launcher.format_user_error(exc)
        # Plain language, reassuring, points at the log — and NEVER leaks the traceback.
        assert "DistrictSync couldn't open its window" in message
        assert "scheduled nightly sync is not affected" in message
        assert "0xdeadbeef" not in message
        assert "Traceback" not in message
        assert "etl_tool.log" in message


class TestWriteTraceback:
    def test_writes_traceback_to_log(self, monkeypatch, tmp_path):
        log = tmp_path / "etl_tool.log"
        monkeypatch.setattr(launcher, "resolve_log_path", lambda: log)
        try:
            raise ValueError("kaboom-marker")
        except ValueError as exc:
            launcher._write_traceback(exc)
        text = log.read_text(encoding="utf-8")
        assert "failed to launch" in text
        assert "kaboom-marker" in text

    def test_never_raises_on_unwritable_sink(self, monkeypatch):
        monkeypatch.setattr(launcher, "resolve_log_path", lambda: Path("/this/does/not/exist/x.log"))
        # Must swallow its own failure — logging the error can't mask the original.
        launcher._write_traceback(RuntimeError("x"))
