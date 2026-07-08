"""Pure run-log parser for the Flet UI — the reusable ``__DISTRICTSYNC_RUN__`` reader.

NO ``flet`` import — this is trust-critical, cheaply-tested I/O-boundary logic that
reads the structured run-log lines the ETL pipeline emits (``pipeline._emit_run_log``)
and yields them as plain ``list[dict]`` records, NEWEST-FIRST. Home reads ``records[0]``;
IA-6 (Run History) renders the whole list — so this lives in a neutral module (NOT
``home_*``), with no Home coupling.

Graceful-degradation contract (the load-bearing ``[]``-vs-``None`` split, both TESTED):

- **Missing file → ``[]``** — a readable "no runs yet" signal (drives derivation rule
  "empty"). An admin whose nightly sync simply hasn't run yet is NOT an error.
- **Present file → parse each line containing ``TAG``**, taking the JSON after the tag;
  **malformed / non-JSON lines are SKIPPED** (never raised), mirroring the Streamlit
  page's parser (``03_Run_History.py``). Records are returned NEWEST-FIRST.
- **Unreadable file (OSError on open/read) → ``None``** — the graceful-degradation
  sentinel (drives derivation rule "status unavailable"). Distinct from ``[]``: missing
  = no runs yet vs. present-but-unreadable = can't tell.

The legacy relative ``etl_tool.log`` fallback the old CLI once used is NOT reproduced
here (dead for the Flet era) — the canonical path is ``paths.user_log_file()`` only.
"""

from __future__ import annotations

import json
from pathlib import Path

TAG = "__DISTRICTSYNC_RUN__"
"""The run-log line marker emitted by ``pipeline._emit_run_log`` (the unchanged emitter)."""


def read_run_records(log_path: Path | None = None) -> list[dict] | None:
    """Parse the ``__DISTRICTSYNC_RUN__`` records from the run log, newest-first.

    Args:
        log_path: the log file to read; ``None`` resolves the canonical
            ``paths.user_log_file()`` (``~/.districtsync/etl_tool.log``). The arg is
            the test seam — point it at a fixture file.

    Returns:
        - ``[]`` if the file is missing (readable, no runs yet).
        - a ``list[dict]`` NEWEST-FIRST if the file is present (malformed / non-tag
          lines skipped, never raising).
        - ``None`` if the file exists but cannot be read (``OSError``) — the
          "status unavailable" graceful-degradation sentinel.
    """
    if log_path is None:
        from src.utils.paths import user_log_file

        log_path = user_log_file()

    if not log_path.exists():
        return []

    records: list[dict] = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if TAG not in line:
                    continue
                try:
                    json_part = line.split(TAG, 1)[1].strip()
                    entry = json.loads(json_part)
                except (json.JSONDecodeError, IndexError, ValueError):
                    continue  # malformed / non-JSON tag line — skip, never raise
                if isinstance(entry, dict):
                    records.append(entry)
    except OSError:
        return None  # present-but-unreadable → graceful-degradation sentinel

    records.reverse()  # file appends chronologically → newest-first
    return records
