"""SQLite run-history store — the durable, district-scoped run ledger (Plan 0029, D2).

UI-neutral + platform-neutral (NO ``ui_flet`` import): the ETL pipeline and the
manual Convert path WRITE run records here; the Flet UI READS them. Replaces the
rotating-log parser (``ui_flet/run_log.py``, retired) so run history survives log
rotation and is immune to the multi-process log-write race between the UI and the
scheduled CLI.

Design (see ``docs/claugentic-INVARIANTS.md`` for the load-bearing contract):

- **Open-use-close per operation.** No shared/module-level connection — the UI reads
  on the UI thread while Convert writes on its worker thread, so each call opens its
  own connection. The DB path is resolved through ``paths.user_history_db()`` at CALL
  time (never a module constant) so the test-isolation seam redirects it — a store
  keyed off an import-time path would write the real ``history.db`` from every test.

- **The WRITE path is the sole creator/migrator.** The first write creates the schema,
  stamps ``PRAGMA user_version = 1``, enables WAL + ``busy_timeout`` (falling back to a
  rollback journal when WAL is unavailable, e.g. a network filesystem), and sets
  owner-only permissions on Unix. A writer that sees a HIGHER ``user_version`` than it
  knows writes with NAMED columns only and never migrates/downgrades (two exe versions
  share this DB: the pinned scheduled exe + an updated UI). Additive-only schema rule.

- **Writes are strictly non-fatal.** Any ``sqlite3.Error`` / ``OSError`` is logged at
  WARNING and returns ``False`` — a store failure never propagates (the enriched
  ``__DISTRICTSYNC_RUN__`` log line is the durable fallback). A "database disk image is
  malformed" quarantines the corrupt file (``history.corrupt-<ts>.db``) and recreates,
  so one torn write can't brick the ledger forever.

- **The READ path never creates the DB.** Missing DB → ``[]`` (a calm "no runs yet"),
  0 rows → ``[]``, any ``sqlite3.Error`` / ``OSError`` → ``None`` (the graceful-degradation
  "status unavailable" sentinel — the exact ``[]``-vs-``None`` split ``home_status`` /
  ``run_history`` consume). Records come back NEWEST-FIRST as the exact flat record dicts
  the derivation modules already read (from the ``record`` JSON column) — zero shape
  change to those modules, exactly one reader.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils import paths

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
"""The additive-only schema version stamped into ``PRAGMA user_version`` and each row's
``schema_version`` column. Bumped only when a future *additive* column lands (never a
destructive change); a writer at a lower version still writes safely via named columns."""

VALID_SOURCES: tuple[str, ...] = ("manual", "scheduled", "cli", "unknown")
"""The closed set of run ``source`` tags (the ``runs.source`` CHECK constraint). The single
source of truth reused by ``pipeline._resolve_source`` — an out-of-set value coerces to
``"unknown"`` rather than aborting the write on a CHECK violation."""

_VALID_STATUSES: tuple[str, ...] = ("success", "failed")

# The schema is CREATE ... IF NOT EXISTS throughout, so re-running it on an existing
# (or higher-version) DB is a harmless no-op — the writer never ALTERs or drops.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    sis_type TEXT,
    source TEXT NOT NULL CHECK(source IN ('manual','scheduled','cli','unknown')),
    status TEXT NOT NULL CHECK(status IN ('success','failed')),
    error_category TEXT,
    schema_version INTEGER NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_sis_ts ON runs (sis_type, timestamp);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_INSERT_RUN = (
    "INSERT INTO runs "
    "(timestamp, sis_type, source, status, error_category, schema_version, record) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# --------------------------------------------------------------------------- #
# Write path — the sole creator/migrator. Strictly non-fatal by construction.  #
# --------------------------------------------------------------------------- #
def write_run_record(record: dict[str, Any], *, source: str) -> bool:
    """Append one run record to the store; return ``True`` iff it was written.

    STRICTLY NON-FATAL: never raises. Any ``sqlite3.Error`` / ``OSError`` is logged at
    WARNING and returns ``False`` (the caller keeps its ``PipelineResult`` / exit code /
    CSVs unchanged — the enriched run-log line is the durable fallback). A corrupt DB is
    quarantined and recreated once, then the write retried on the fresh DB.

    Args:
        record: the flat run-record dict (the exact shape the derivation modules read);
            stored verbatim as JSON plus a few promoted typed columns for filtering.
        source: the run origin — coerced to ``"unknown"`` if not in :data:`VALID_SOURCES`
            (so an unexpected value can't abort the write on the CHECK constraint).
    """
    db_path = paths.user_history_db()
    src = source if source in VALID_SOURCES else "unknown"
    try:
        return _write(db_path, record, src)
    except sqlite3.Error as exc:
        if _is_corrupt(exc):
            return _quarantine_and_recreate(db_path, record, src, exc)
        logger.warning("Run-history store write failed (%s); the run is recorded in the diagnostic log only", exc)
        return False
    except OSError as exc:
        logger.warning("Run-history store write failed (%s); the run is recorded in the diagnostic log only", exc)
        return False


def _write(db_path: Path, record: dict[str, Any], source: str) -> bool:
    """Open-use-close a write: ensure schema, insert the row + stamp ``created_at`` once."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open(db_path)
    try:
        _ensure_schema(conn)
        with conn:  # one transaction — commit on success, rollback on any error
            conn.execute(
                _INSERT_RUN,
                (
                    str(record.get("timestamp", "")),
                    _opt_str(record.get("sis_type")),
                    source,
                    _coerce_status(record.get("status")),
                    _opt_str(record.get("error_category")),
                    SCHEMA_VERSION,
                    json.dumps(record),
                ),
            )
            # ``created_at`` is the store's own birth stamp — set once, on the first ever
            # write, and never overwritten (drives the "history starts fresh" empty state).
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', ?)",
                (str(record.get("timestamp", "")) or _now_iso(),),
            )
        _harden_permissions(db_path)
        return True
    finally:
        conn.close()


def _open(db_path: Path) -> sqlite3.Connection:
    """Connect + apply ``busy_timeout`` and the journal mode (WAL, DELETE fallback)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    _set_journal_mode(conn)
    return conn


def _set_journal_mode(conn: sqlite3.Connection) -> None:
    """Enable WAL (concurrent readers + a serialized writer); fall back to a rollback journal.

    On a filesystem that can't support WAL (some network/roaming shares), the WAL request
    silently doesn't stick — detect that and fall back to the classic DELETE journal so the
    store still works (just without reader/writer concurrency).
    """
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if row and str(row[0]).lower() == "wal":
            return
    except sqlite3.Error:
        pass
    logger.info("Run-history store: WAL journal unavailable, using the DELETE rollback journal")
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA journal_mode=DELETE")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the schema if absent + stamp ``user_version`` on a brand-new DB only.

    A DB already at a KNOWN or HIGHER ``user_version`` is left untouched — the writer never
    migrates or downgrades (two exe versions share the file). The ``CREATE ... IF NOT EXISTS``
    statements are harmless no-ops on any existing/higher-version DB.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    user_version = int(row[0]) if row else 0
    conn.executescript(_SCHEMA)
    if user_version == 0:
        # Brand-new (or pre-versioning) DB — stamp the current schema version. PRAGMA
        # takes no bound parameter; the value is a controlled int constant, never input.
        conn.execute(f"PRAGMA user_version={int(SCHEMA_VERSION)}")  # nosec B608


def _quarantine_and_recreate(db_path: Path, record: dict[str, Any], source: str, exc: sqlite3.Error) -> bool:
    """Move a corrupt DB (+ WAL/SHM sidecars) aside and recreate — a torn write can't brick it forever."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrupt = db_path.with_name(f"history.corrupt-{stamp}.db")
    try:
        for suffix in ("", "-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists():
                side.replace(Path(str(corrupt) + suffix))
        logger.warning("Run-history store was corrupt (%s); quarantined to %s and recreated", exc, corrupt.name)
        return _write(db_path, record, source)
    except (sqlite3.Error, OSError) as exc2:
        logger.warning(
            "Run-history store quarantine/recreate failed (%s); the run is recorded in the diagnostic log only",
            exc2,
        )
        return False


# --------------------------------------------------------------------------- #
# Read path — NEVER creates the DB. Graceful-degradation [] vs None split.      #
# --------------------------------------------------------------------------- #
def read_run_records(limit: int | None = None) -> list[dict[str, Any]] | None:
    """Return the run records NEWEST-FIRST, or a graceful-degradation sentinel.

    Returns:
        - ``[]`` when the DB is missing (a calm "no runs yet" — the read path NEVER
          creates the DB) or present with 0 rows.
        - a ``list[dict]`` NEWEST-FIRST (the exact flat record dicts stored in the
          ``record`` JSON column; a malformed/non-dict payload is skipped, never raised).
        - ``None`` on any ``sqlite3.Error`` / ``OSError`` (corrupt, locked past the
          busy_timeout, permission denied) — the "status unavailable" sentinel.

    Args:
        limit: cap the newest-N rows returned (``None`` = all).
    """
    db_path = paths.user_history_db()
    if not db_path.exists():
        return []  # missing → no runs yet; a READ must never create the store
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            sql = "SELECT record FROM runs ORDER BY timestamp DESC, id DESC"
            if limit is not None:
                rows = conn.execute(sql + " LIMIT ?", (int(limit),)).fetchall()
            else:
                rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None

    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            entry = json.loads(row[0])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue  # a corrupt record payload — skip it, mirror the old parser's totality
        if isinstance(entry, dict):
            records.append(entry)
    return records


def store_meta() -> dict[str, str] | None:
    """Return the store's ``meta`` key/value map (at least ``created_at``), or ``None``.

    Missing DB → ``None`` (the store was never created); any read error → ``None``. Used by
    the fresh-start empty-state branch to distinguish an upgraded install's blank ledger
    from a genuine first run.
    """
    db_path = paths.user_history_db()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None
    return {str(key): str(value) for key, value in rows}


# --------------------------------------------------------------------------- #
# Small helpers.                                                                #
# --------------------------------------------------------------------------- #
def _harden_permissions(db_path: Path) -> None:
    """Owner-only (0o600) on the DB + sidecars on Unix (best-effort; no-op on Windows)."""
    if sys.platform == "win32":
        return
    with contextlib.suppress(OSError):
        os.chmod(db_path.parent, 0o700)
    for suffix in ("", "-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            with contextlib.suppress(OSError):
                os.chmod(side, 0o600)


def _is_corrupt(exc: sqlite3.Error) -> bool:
    """Whether a sqlite error means a torn/non-database file (→ quarantine-and-recreate)."""
    message = str(exc).lower()
    return "malformed" in message or "not a database" in message


def _coerce_status(value: object) -> str:
    """Map a record status to the CHECK-constrained set; anything unexpected → ``"failed"``."""
    text = str(value) if value is not None else ""
    return text if text in _VALID_STATUSES else "failed"


def _opt_str(value: object) -> str | None:
    """A nullable text column value: ``None`` stays ``None``; everything else stringifies."""
    return None if value is None else str(value)


def _now_iso() -> str:
    """Naive-local ISO timestamp (matches the record ``timestamp`` shape) — created_at fallback."""
    return datetime.now().isoformat(timespec="seconds")
