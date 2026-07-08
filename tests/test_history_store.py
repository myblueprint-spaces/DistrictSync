"""Unit tests for the SQLite run-history store (``src/history/store.py``, Plan 0029 D2).

Migrates + extends the retired ``run_log`` reader's discipline: the load-bearing
``[]``-vs-``None`` graceful-degradation split (missing DB → ``[]`` a calm "no runs yet";
unreadable/corrupt/locked → ``None`` the "status unavailable" sentinel), NEWEST-FIRST
ordering, and malformed-payload totality. Adds the store-only contract: no-create-on-read,
quarantine-and-recreate, strictly-non-fatal writes, ``created_at`` meta, higher-``user_version``
never-migrate/downgrade, and context-managed connections (no WAL sidecar handle blocks
tmp cleanup on Windows).

All tests run under the autouse ``isolated_user_profile`` fixture, so ``paths.user_history_db()``
resolves into a per-test tmp profile — the real ``history.db`` is never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.history import store
from src.utils import paths

_ENTITY_KEYS = (
    "Students",
    "Staff",
    "Family",
    "Classes",
    "Enrollments",
    "CourseInfo",
    "StudentCourses",
    "StudentAttendance",
)


def _record(*, timestamp: str, status: str = "success", source: str = "cli", **extra: object) -> dict:
    """A minimal flat run record matching the derivation-module shape."""
    rec: dict = {
        "timestamp": timestamp,
        "status": status,
        "source": source,
        "sis_type": "myedbc",
        "error_category": "none",
        "duration_s": 1.0,
        "sftp_attempted": False,
        "sftp_ok": False,
        "anomalies": [],
        "data_errors": {},
    }
    for key in _ENTITY_KEYS:
        rec[key] = 0
    rec.update(extra)
    return rec


# --------------------------------------------------------------------------- #
# Round-trip + ordering                                                         #
# --------------------------------------------------------------------------- #
class TestRoundTrip:
    def test_write_then_read_returns_the_record(self) -> None:
        assert store.write_run_record(_record(timestamp="2026-07-01T03:00:00", Students=42), source="cli") is True
        records = store.read_run_records()
        assert records is not None
        assert len(records) == 1
        assert records[0]["Students"] == 42
        assert records[0]["status"] == "success"

    def test_records_are_newest_first(self) -> None:
        for ts in ("2026-07-01T03:00:00", "2026-07-02T03:00:00", "2026-07-03T03:00:00"):
            store.write_run_record(_record(timestamp=ts, marker=ts[8:10]), source="cli")
        records = store.read_run_records()
        assert records is not None
        assert [r["marker"] for r in records] == ["03", "02", "01"]

    def test_same_timestamp_breaks_ties_by_insertion_order(self) -> None:
        # Two runs in the same second → the LATER-written (higher id) is newest-first.
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00", marker="first"), source="cli")
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00", marker="second"), source="cli")
        records = store.read_run_records()
        assert records is not None
        assert [r["marker"] for r in records] == ["second", "first"]

    def test_limit_caps_the_newest_n(self) -> None:
        for i in range(5):
            store.write_run_record(_record(timestamp=f"2026-07-0{i + 1}T03:00:00", marker=str(i)), source="cli")
        records = store.read_run_records(limit=2)
        assert records is not None
        assert [r["marker"] for r in records] == ["4", "3"]


# --------------------------------------------------------------------------- #
# Reader contract table — the []-vs-None graceful-degradation split             #
# --------------------------------------------------------------------------- #
class TestReaderContract:
    def test_missing_db_returns_empty_list_and_does_not_create_it(self) -> None:
        db = paths.user_history_db()
        assert not db.exists()
        result = store.read_run_records()
        assert result == []
        assert result is not None
        assert not db.exists(), "a READ must never create the store"

    def test_empty_db_returns_empty_list(self) -> None:
        # A store with schema but zero rows (delete the row we wrote) → [].
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")
        db = paths.user_history_db()
        with sqlite3.connect(str(db)) as conn:
            conn.execute("DELETE FROM runs")
        assert store.read_run_records() == []

    def test_corrupt_db_returns_none(self) -> None:
        db = paths.user_history_db()
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"this is not a sqlite database at all")
        assert store.read_run_records() is None

    def test_locked_db_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # OUR degradation under a lock we can't wait out: connect raises "database is locked".
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")

        def _locked(*_a: object, **_k: object) -> object:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store.sqlite3, "connect", _locked)
        assert store.read_run_records() is None

    def test_malformed_record_payload_is_skipped_not_raised(self) -> None:
        store.write_run_record(_record(timestamp="2026-07-02T03:00:00", marker="good"), source="cli")
        db = paths.user_history_db()
        # Inject a row whose record JSON is broken — the reader skips it, never raises.
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                store._INSERT_RUN,
                ("2026-07-01T03:00:00", "myedbc", "cli", "success", "none", store.SCHEMA_VERSION, "{not json"),
            )
        records = store.read_run_records()
        assert records is not None
        assert [r["marker"] for r in records] == ["good"]


# --------------------------------------------------------------------------- #
# Write path — non-fatal, quarantine, versioning                               #
# --------------------------------------------------------------------------- #
class TestWritePath:
    def test_write_failure_is_non_fatal_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_a: object, **_k: object) -> object:
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(store.sqlite3, "connect", _boom)
        # Never raises; returns False (the run stays in the diagnostic log).
        assert store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli") is False

    def test_corrupt_db_is_quarantined_and_recreated_on_write(self) -> None:
        db = paths.user_history_db()
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"torn write \x00\x01 not a database")
        ok = store.write_run_record(_record(timestamp="2026-07-05T03:00:00", marker="fresh"), source="cli")
        assert ok is True
        quarantined = list(db.parent.glob("history.corrupt-*.db"))
        assert quarantined, "the corrupt DB must be quarantined, not deleted"
        records = store.read_run_records()
        assert records is not None
        assert [r["marker"] for r in records] == ["fresh"]

    def test_invalid_source_coerces_to_unknown(self) -> None:
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="totally-bogus")
        db = paths.user_history_db()
        with sqlite3.connect(str(db)) as conn:
            (stored_source,) = conn.execute("SELECT source FROM runs").fetchone()
        assert stored_source == "unknown"

    def test_higher_user_version_is_not_migrated_or_downgraded(self) -> None:
        # A future exe wrote at v99; our v1 writer must insert (named columns) and NOT downgrade.
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00", marker="v1"), source="cli")
        db = paths.user_history_db()
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA user_version=99")
        assert store.write_run_record(_record(timestamp="2026-07-02T03:00:00", marker="v1-again"), source="cli") is True
        with sqlite3.connect(str(db)) as conn:
            (uv,) = conn.execute("PRAGMA user_version").fetchone()
        assert uv == 99, "the writer must never downgrade a higher user_version"
        records = store.read_run_records()
        assert records is not None
        assert {r["marker"] for r in records} == {"v1", "v1-again"}

    def test_fresh_db_is_stamped_at_schema_version(self) -> None:
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")
        db = paths.user_history_db()
        with sqlite3.connect(str(db)) as conn:
            (uv,) = conn.execute("PRAGMA user_version").fetchone()
        assert uv == store.SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# store_meta + connection hygiene                                              #
# --------------------------------------------------------------------------- #
class TestMetaAndHygiene:
    def test_store_meta_missing_db_returns_none(self) -> None:
        assert store.store_meta() is None

    def test_store_meta_reports_created_at(self) -> None:
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")
        meta = store.store_meta()
        assert meta is not None
        assert meta.get("created_at") == "2026-07-01T03:00:00"

    def test_created_at_is_set_once_and_not_overwritten(self) -> None:
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")
        store.write_run_record(_record(timestamp="2026-07-09T03:00:00"), source="cli")
        meta = store.store_meta()
        assert meta is not None
        assert meta["created_at"] == "2026-07-01T03:00:00", "created_at is the store's birth stamp, set once"

    def test_connections_are_closed_so_the_db_file_can_be_removed(self) -> None:
        # A leaked WAL sidecar handle would block this on Windows — proves open-use-close.
        store.write_run_record(_record(timestamp="2026-07-01T03:00:00"), source="cli")
        assert store.read_run_records() is not None
        assert store.store_meta() is not None
        db = paths.user_history_db()
        db.unlink()  # raises PermissionError on Windows if any handle is still open
        for suffix in ("-wal", "-shm"):
            side = Path(str(db) + suffix)
            if side.exists():
                side.unlink()
        assert not db.exists()
