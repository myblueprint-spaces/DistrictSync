"""Forward-migration ladder for ``history.db`` (DSYNC-11, ``src/history/store.py``).

Production ships NO migration step (version 1 is the baseline), so every scenario below
registers a synthetic step on a monkeypatched ladder. All DBs live under ``tmp_path`` or the
autouse isolated profile — the real ``history.db`` is never touched.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from src.history import store
from src.utils import paths


def _add_note_column(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE runs ADD COLUMN note TEXT")


def _failing_step(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE runs ADD COLUMN half_done TEXT")  # applied, then must roll back
    conn.execute("ALTER TABLE no_such_table ADD COLUMN x TEXT")


def _version(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _columns(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()


def _make_v1_db(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(store._SCHEMA)
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    finally:
        conn.close()


def _ensure(db: Path) -> None:
    conn = store._open(db)
    try:
        store._ensure_schema(conn)
    finally:
        conn.close()


@pytest.fixture
def ladder_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the code is at schema version 2 with one additive step."""
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(store, "_MIGRATIONS", {2: _add_note_column})


def test_production_ladder_is_contiguous_and_empty_at_baseline() -> None:
    assert store.SCHEMA_VERSION == 1
    assert set(store._MIGRATIONS) == set(range(2, store.SCHEMA_VERSION + 1))


def test_fresh_db_gets_current_version(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    _ensure(db)
    assert _version(db) == store.SCHEMA_VERSION
    assert "record" in _columns(db)  # positive twin: the schema really exists


def test_fresh_db_climbs_the_ladder_to_current(tmp_path: Path, ladder_v2: None) -> None:
    db = tmp_path / "history.db"
    _ensure(db)
    assert _version(db) == 2
    assert "note" in _columns(db)


def test_v1_db_migrates_forward_and_new_column_insert_succeeds(tmp_path: Path, ladder_v2: None) -> None:
    db = tmp_path / "history.db"
    _make_v1_db(db)
    assert "note" not in _columns(db)
    _ensure(db)
    assert _version(db) == 2
    assert "note" in _columns(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO runs (timestamp, source, status, schema_version, record, note) "
            "VALUES ('t', 'cli', 'success', 2, '{}', 'hello')"
        )
        assert conn.execute("SELECT note FROM runs").fetchone()[0] == "hello"
    finally:
        conn.close()


def test_migration_is_idempotent_on_reopen(tmp_path: Path, ladder_v2: None, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "history.db"
    _make_v1_db(db)
    _ensure(db)
    calls: list[int] = []
    monkeypatch.setattr(store, "_MIGRATIONS", {2: lambda c: calls.append(1)})
    _ensure(db)  # would raise "duplicate column" if the step ran again
    assert calls == []
    assert _version(db) == 2
    assert "note" in _columns(db)


def test_failing_step_raises_and_leaves_db_usable_at_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(store, "_MIGRATIONS", {2: _failing_step})
    db = tmp_path / "history.db"
    _make_v1_db(db)
    with pytest.raises(store.MigrationError) as info:
        _ensure(db)
    assert info.value.step == 2
    assert "version 2" in str(info.value)
    assert str(tmp_path) not in str(info.value)  # no paths in the message
    assert _version(db) == 1
    assert "half_done" not in _columns(db)  # the partial ALTER rolled back
    # Positive twin: the DB still accepts a plain v1 insert.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(store._INSERT_RUN, ("t", None, "cli", "success", None, 1, "{}"))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        conn.close()


def test_missing_step_registration_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(store, "_MIGRATIONS", {})
    db = tmp_path / "history.db"
    _make_v1_db(db)
    with pytest.raises(store.MigrationError):
        _ensure(db)
    assert _version(db) == 1


def test_write_run_record_logs_error_with_step_and_stays_non_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = paths.user_history_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    _make_v1_db(db)
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(store, "_MIGRATIONS", {2: _failing_step})
    with caplog.at_level(logging.WARNING, logger="src.history.store"):
        ok = store.write_run_record({"timestamp": "2026-01-01T00:00:00", "status": "success"}, source="cli")
    assert ok is False
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and "step 2" in errors[0].getMessage()
    assert list(db.parent.glob("history.corrupt-*")) == []  # a failed migration is not "corruption"
    assert _version(db) == 1


def test_write_run_record_succeeds_after_successful_migration(ladder_v2: None) -> None:
    db = paths.user_history_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    _make_v1_db(db)
    assert store.write_run_record({"timestamp": "2026-01-01T00:00:00", "status": "success"}, source="cli") is True
    assert _version(db) == 2


def test_higher_version_db_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_MIGRATIONS", {2: _failing_step})  # must never run
    db = tmp_path / "history.db"
    _make_v1_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version=7")
    conn.commit()
    conn.close()
    before = _columns(db)
    _ensure(db)
    assert _version(db) == 7  # never lowered, never migrated
    assert _columns(db) == before
    # Positive twin: the same ladder DOES run (and fail) for a lower-version DB.
    low = tmp_path / "low.db"
    _make_v1_db(low)
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    with pytest.raises(store.MigrationError):
        _ensure(low)
