"""Unit tests for the pure ``run_log.read_run_records`` parser (IA-3a, COUNTED).

Pins the load-bearing graceful-degradation contract: **missing file → ``[]``** (a calm
"no runs yet") vs. **unreadable file → ``None``** (the "status unavailable" sentinel) —
an admin whose sync merely hasn't run must never see a corrupt-log message. Also pins
NEWEST-FIRST ordering (Home reads ``records[0]``; IA-6 renders the list) and the
malformed-line-skip totality (never raises on garbage).

Uses the ``log_path`` seam over a temp file — hermetic, no ``~/.districtsync`` dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ui_flet.run_log import TAG, read_run_records


def _run_line(**fields: object) -> str:
    """A well-formed ``__DISTRICTSYNC_RUN__`` log line (prefixed like the real logger)."""
    return f"2026-07-04 03:00:01 INFO {TAG} {json.dumps(fields)}\n"


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


class TestValidParsing:
    def test_parses_every_tagged_line(self, tmp_path: Path) -> None:
        log = tmp_path / "etl_tool.log"
        _write(log, [_run_line(status="success", marker=i) for i in range(3)])
        records = read_run_records(log)
        assert records is not None
        assert len(records) == 3
        assert all(r.get("status") == "success" for r in records)

    def test_records_are_dicts_with_expected_keys(self, tmp_path: Path) -> None:
        log = tmp_path / "etl_tool.log"
        _write(log, [_run_line(status="success", Students=42, sftp_ok=True)])
        records = read_run_records(log)
        assert records is not None
        assert records[0]["Students"] == 42
        assert records[0]["sftp_ok"] is True


class TestNewestFirst:
    def test_last_written_run_is_first(self, tmp_path: Path) -> None:
        # File appends chronologically; the reader returns newest-first.
        log = tmp_path / "etl_tool.log"
        _write(log, [_run_line(marker="oldest"), _run_line(marker="middle"), _run_line(marker="newest")])
        records = read_run_records(log)
        assert records is not None
        assert [r["marker"] for r in records] == ["newest", "middle", "oldest"]


class TestMalformedSkipped:
    def test_malformed_and_untagged_lines_are_skipped_no_raise(self, tmp_path: Path) -> None:
        log = tmp_path / "etl_tool.log"
        _write(
            log,
            [
                "plain untagged noise line\n",
                _run_line(status="success", marker="valid1"),
                f"2026-07-04 03:00:02 INFO {TAG} {{not valid json\n",  # tagged but broken JSON
                f"2026-07-04 03:00:03 INFO {TAG} \n",  # tagged but empty payload
                _run_line(status="failed", marker="valid2"),
            ],
        )
        records = read_run_records(log)
        assert records is not None
        markers = [r.get("marker") for r in records]
        assert markers == ["valid2", "valid1"]  # newest-first, only the two valid ones

    def test_non_dict_json_payload_is_skipped(self, tmp_path: Path) -> None:
        # A tagged line whose JSON is a list/scalar (not a run record) is skipped.
        log = tmp_path / "etl_tool.log"
        _write(log, [f"INFO {TAG} [1, 2, 3]\n", _run_line(marker="ok")])
        records = read_run_records(log)
        assert records is not None
        assert [r["marker"] for r in records] == ["ok"]


class TestMissingFileEmpty:
    def test_missing_file_returns_empty_list_not_none(self, tmp_path: Path) -> None:
        # The "no runs yet" signal — readable, empty, NOT an error (drives derivation rule "empty").
        missing = tmp_path / "does_not_exist.log"
        result = read_run_records(missing)
        assert result == []
        assert result is not None


class TestUnreadableNone:
    def test_directory_path_returns_none(self, tmp_path: Path) -> None:
        # A directory exists() but can't be opened as a file → OSError → None sentinel.
        a_dir = tmp_path / "a_directory"
        a_dir.mkdir()
        assert read_run_records(a_dir) is None

    def test_open_oserror_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate a permission/OS error on open → the "status unavailable" sentinel.
        log = tmp_path / "etl_tool.log"
        _write(log, [_run_line(status="success")])

        def _boom(*_a: object, **_k: object) -> object:
            raise PermissionError("access denied")

        monkeypatch.setattr("builtins.open", _boom)
        assert read_run_records(log) is None


class TestEmptyVsNoneSplit:
    def test_missing_is_empty_and_unreadable_is_none(self, tmp_path: Path) -> None:
        # The load-bearing split asserted explicitly, side by side.
        missing = tmp_path / "missing.log"
        a_dir = tmp_path / "dir"
        a_dir.mkdir()
        assert read_run_records(missing) == []  # missing = no runs yet
        assert read_run_records(a_dir) is None  # present-but-unreadable = can't tell


class TestDefaultPathSeam:
    def test_none_arg_resolves_canonical_user_log_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # log_path=None resolves paths.user_log_file() (single source of truth).
        fixture = tmp_path / "etl_tool.log"
        _write(fixture, [_run_line(status="success", marker="canonical")])
        monkeypatch.setattr("src.utils.paths.user_log_file", lambda: fixture)
        records = read_run_records()
        assert records is not None
        assert records[0]["marker"] == "canonical"
