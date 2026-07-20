"""Core ETL pipeline — separated from src/main.py so it can be imported.

src/main.py is the PyInstaller entry point; in a frozen one-file exe the
entry script runs as ``__main__``, not as a proper module, so
``from src.main import run_pipeline`` fails at runtime even though it
works in dev. Callers (the Flet UI, tests, CLI) import from this
module instead and stay decoupled from the CLI argparse layer.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.config.models import filter_enabled_entities
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.transformer import DataTransformer
from src.history.store import VALID_SOURCES, write_run_record
from src.quality.report import DataQualityReport
from src.sftp.uploader import SFTPUploader

logger = logging.getLogger(__name__)

ANOMALY_THRESHOLD = 0.20  # Warn if any entity drops >20% vs previous output

# The entity-count keys carried FLAT on every run record (the shape the Home / Run-History
# derivation modules read via ``record["Students"]`` etc.). Single-sourced so the log line
# and the store record can never drift on which entities they count.
_RECORD_ENTITY_KEYS: tuple[str, ...] = (
    "Students",
    "Staff",
    "Family",
    "Classes",
    "Enrollments",
    "CourseInfo",
    "StudentCourses",
    "StudentAttendance",
)

_SOURCE_ENV_VAR = "DSYNC_SOURCE"  # scheduled/cron/Docker set this; the registered task passes --source


class RunErrorCategory(str, Enum):
    """The bounded, PII-free failure taxonomy stamped on every run record (mirrors the
    ``LatestReason`` closed-set style). The store carries ONLY this category — never the
    free-text ``str(e)`` (which can leak a path / column / sis_type); the diagnostic log
    keeps the rich message for ops. A closed set so a future filter UI can rely on it.
    """

    NONE = "none"  # a completed run (delivery/anomaly/data-warning axes live in their own fields)
    NO_INPUT = "no_input"  # no usable input (input folder missing, or every required file missing/empty)
    CONFIG = "config"  # a config/validation problem surfaced as the failure
    DATA = "data"  # a build/write problem (missing field-map column, transform, loader)
    UNKNOWN = "unknown"  # an unclassified failure


@dataclass
class PipelineResult:
    """Structured return value from run_pipeline."""

    entity_counts: dict[str, int] = field(default_factory=dict)
    sftp_attempted: bool = False
    sftp_ok: bool = False
    anomalies: list[str] = field(default_factory=list)


def extract_required_files(config) -> list[str]:
    """Extract all unique source filenames from a validated MappingConfig.

    Respects ``enabled_entities``: source files for disabled entities are
    excluded. ``school_year_sources`` are only included when also referenced
    by an enabled entity — when they aren't, ``determine_school_year``
    falls back to the calendar-date heuristic in BaseTransformer.
    """
    active = config.active_entities()

    files: set[str] = set()
    for entity_name, entity_cfg in config.mappings.items():
        if entity_name not in active:
            continue
        files.update(entity_cfg.source_files.values())
    return list(files)


def configured_entity_order(mappings: dict, global_config: dict) -> list[str]:
    """The ordered entity list this run is configured to produce.

    ``entity_order`` (ordering — defaults to the mapping keys) filtered by
    ``enabled_entities`` (inclusion) — exactly the list :func:`run_transform`
    iterates. Exposed so callers reasoning about what a run SHOULD have produced
    (the vanished-entity anomaly leg) derive from enabled entities, never raw
    ``mappings.keys()``: ``_base`` inheritance leaves inherited-but-disabled
    entities in the mapping (see CLAUDE.md "Output Targeting"), and treating
    those as expected would fire false anomalies against a DIFFERENT config's
    legitimate CSV sharing the output dir.
    """
    entity_order = global_config.get("entity_order") or list(mappings.keys())
    # `enabled_entities` (when non-empty) filters which mappings actually run.
    # This lets the base config define more entity templates than it
    # activates by default — districts opt in by listing them. The selection
    # rule itself is single-sourced in `filter_enabled_entities` (the same
    # kernel behind `MappingConfig.active_entities`).
    return filter_enabled_entities(entity_order, global_config.get("enabled_entities"))


class TransformOutputs(NamedTuple):
    """Result of :func:`run_transform` — transformed frames plus their CSV column order.

    ``data_errors`` is the run's fail-loud field-transform ledger (a separate
    axis from ETL success): non-fatal per-row / column-level transform problems
    recorded by ``BaseTransformer.apply_field_map`` instead of being silently
    swallowed. Empty on a clean run.
    """

    outputs: dict[str, pd.DataFrame]
    field_orders: dict[str, list[str]]
    data_errors: list[dict]


def run_transform(
    raw_data: dict[str, pd.DataFrame],
    mappings: dict,
    global_config: dict,
) -> TransformOutputs:
    """Shared transform-orchestration: school-year determination + the per-entity loop.

    Behaviour-preserving extraction of the canonical orchestration block from
    ``run_pipeline``. Constructs a fresh :class:`DataTransformer` (both callers
    already build a new transformer per run, so the per-run ``TransformContext``
    state stays isolated). Honors ``enabled_entities`` (inclusion) and
    ``entity_order`` (ordering); skips an entity only when ALL of its source
    frames are empty (a role-resolved entity may have an empty positional-first
    source yet a populated secondary band); collects each emitted entity's field
    order from its ``field_map`` keys.
    The returned :class:`TransformOutputs` also carries ``data_errors`` — the
    shared context's fail-loud field-transform ledger accumulated across every
    entity (empty on a clean run).
    """
    transformer = DataTransformer()

    outputs: dict[str, pd.DataFrame] = {}
    field_orders: dict[str, list[str]] = {}

    # Determine school year — NO in-code defaults. Validation guarantees
    # academic_start_month_day and academic_end_month_day are set when
    # Classes is enabled; rollover falls back to academic_end_month_day
    # when not separately configured.
    sy_sources_config = global_config.get("school_year_sources", {})
    start_md = global_config["academic_start_month_day"]
    end_md = global_config["academic_end_month_day"]
    rollover_md = global_config.get("academic_year_rollover_month_day") or end_md
    naming = global_config.get("school_year_naming") or "end"
    sy = transformer.determine_school_year(
        raw_data,
        sy_sources_config,
        rollover_month_day=rollover_md,
        school_year_naming=naming,
    )
    transformer.set_school_year(sy, start_md, end_md)
    logger.info(f"Using school year {sy}, academic start={transformer.academic_start}, end={transformer.academic_end}")

    for entity_name in configured_entity_order(mappings, global_config):
        entity_cfg = mappings.get(entity_name, {})
        source_config = entity_cfg.get("source_files", {})

        if not source_config:
            logger.warning(f"No source_files for entity '{entity_name}' in the mapping; skipping.")
            continue

        source_files = list(source_config.values()) if isinstance(source_config, dict) else source_config
        if not source_files:
            logger.warning(f"No valid source files for entity '{entity_name}'; skipping.")
            continue

        # Skip only when EVERY source frame is empty. The positional-first frame
        # is still passed as the primary, but an entity that resolves its bands
        # BY ROLE (e.g. StudentAttendance — daily/period order-independent) must
        # not be dropped just because its listed-first source is empty: a
        # period-only district lists `daily_absences` first, so an empty daily
        # frame would otherwise skip a fully-populated period band.
        source_frames = [raw_data.get(sf, pd.DataFrame()) for sf in source_files]
        if all(df.empty for df in source_frames):
            logger.warning(f"All source files {source_files} are empty for '{entity_name}'; skipping.")
            continue
        primary_df = source_frames[0]  # may be empty for a role-resolved entity (period-only attendance)

        transformed = transformer.transform(primary_df, entity_cfg, entity_name, raw_data, global_config)

        if transformed.empty:
            logger.warning(f"No data transformed for entity '{entity_name}'; skipping.")
            continue

        outputs[entity_name] = transformed
        field_orders[entity_name] = list(entity_cfg.get("field_map", {}).keys())

    # The shared per-run TransformContext accumulates fail-loud field-transform
    # errors across every entity; surface them on the same axis as the outputs.
    return TransformOutputs(outputs, field_orders, transformer.data_errors)


def _previous_row_count(prev_path: Path) -> int | None:
    """Data-row count of a previous output CSV, or ``None`` when unreadable.

    ``None`` (locked file, corrupt bytes, a directory squatting on the name) is
    NOT equivalent to "no baseline": callers surface it as an anomaly-check
    degradation warning instead of silently skipping the drop check.
    """
    try:
        with open(prev_path, encoding="utf-8") as f:
            return sum(1 for _ in f) - 1
    except Exception:  # noqa: BLE001 - any read failure means "unreadable baseline"; the caller warns loudly
        return None


def _unreadable_baseline_msg(entity: str) -> str:
    """The anomaly-check degradation warning for an unreadable previous CSV."""
    return f"{entity}: the previous output file could not be read, so the drop check was skipped for this entity"


def compute_anomalies(
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
    expected_entities: Iterable[str] | None = None,
) -> list[str]:
    """Pure anomaly compute shared by the CLI and the Convert page.

    Two checks, one plain warning-string list. Each base message carries NO
    surface-specific presentation (no ``ANOMALY:`` prefix, no logging/printing);
    each caller adds its own (CLI logs, UI renders).

    * **Row-count drop** — each output entity's row count vs its previous run's
      CSV in ``output_dir``: a drop over :data:`ANOMALY_THRESHOLD` warns
      (``"{entity} dropped from {prev} to {new} rows ({pct}% decrease)"``).
    * **Vanished entity** (present→absent / N→0) — each entity in
      ``expected_entities`` (the enabled-entities-derived set this run was
      CONFIGURED to produce — :func:`configured_entity_order`, never raw
      ``mappings.keys()``) that produced nothing while a non-empty previous CSV
      sits on disk. An entity that transforms to zero rows never enters
      ``outputs`` (``run_transform`` drops it), so without this leg a vanishing
      roster file was invisible to the anomaly check.

    A missing or empty previous file is fine — a first run / new entity is not
    an anomaly. An UNREADABLE previous file warns as an anomaly-check
    degradation for that entity — never silently skipped, and never a hard
    failure (a corrupt baseline must not fail the run).
    """
    warnings: list[str] = []
    for entity, df in outputs.items():
        prev_path = output_dir / f"{entity}.csv"
        if not prev_path.exists():
            continue
        prev_count = _previous_row_count(prev_path)
        if prev_count is None:
            warnings.append(_unreadable_baseline_msg(entity))
            continue
        if prev_count > 0 and len(df) < prev_count * (1 - ANOMALY_THRESHOLD):
            pct = ((prev_count - len(df)) / prev_count) * 100
            warnings.append(f"{entity} dropped from {prev_count} to {len(df)} rows ({pct:.0f}% decrease)")

    # Vanished entities: configured to be produced, produced nothing this run,
    # and a previous CSV is on disk (sorted for a deterministic warning order).
    for entity in sorted(set(expected_entities or ()) - set(outputs)):
        prev_path = output_dir / f"{entity}.csv"
        if not prev_path.exists():
            continue  # never produced before — a missing baseline is fine
        prev_count = _previous_row_count(prev_path)
        if prev_count is None:
            warnings.append(_unreadable_baseline_msg(entity))
            continue
        if prev_count > 0:
            warnings.append(f"{entity} produced no output this run (previous run had {prev_count} rows)")
    return warnings


def _check_anomalies(
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
    expected_entities: Iterable[str] | None = None,
) -> list[str]:
    """CLI renderer over :func:`compute_anomalies`: prefix ``ANOMALY:`` and log.

    Thin wrapper that adds the CLI's existing presentation to each shared base
    warning — the ``ANOMALY:``-prefixed ``logger.warning`` lines — and returns
    the prefixed strings (consumed by the structured run-log's ``anomalies``).
    """
    warnings: list[str] = []
    for msg in compute_anomalies(outputs, output_dir, expected_entities):
        prefixed = f"ANOMALY: {msg}"
        logger.warning(prefixed)
        warnings.append(prefixed)
    return warnings


def build_run_record(
    *,
    status: str,
    elapsed: float,
    entity_counts: dict[str, int],
    sftp_attempted: bool = False,
    sftp_ok: bool = False,
    anomalies: list[str] | None = None,
    data_errors: dict[str, Any] | None = None,
    source: str,
    sis_type: str,
    error_category: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the ONE flat run-record dict written to BOTH sinks (D2a — one dict, two sinks).

    This is the exact flat shape ``home_status`` / ``run_history`` consume (entity counts as
    FLAT top-level keys, ``sftp_*`` / ``anomalies`` / ``data_errors`` axes) plus the new
    enrichment (``source`` / ``sis_type`` / ``error_category``). It carries NO free-text
    ``error`` — that rich detail is added ONLY to the diagnostic-log line (see
    :func:`_log_run_record`); the store never persists it (privacy split).

    ``data_errors`` is the compact ``{"total": N, "by_field": {...}}`` summary — a separate
    axis from ``status`` (which stays ``success``/``failed`` for the ETL run itself).
    """
    record: dict[str, Any] = {
        "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "source": source,
        "sis_type": sis_type,
        "error_category": error_category,
        "duration_s": round(elapsed, 1),
        "sftp_attempted": sftp_attempted,
        "sftp_ok": sftp_ok,
        "anomalies": anomalies or [],
        "data_errors": data_errors or {},
    }
    for key in _RECORD_ENTITY_KEYS:
        record[key] = int(entity_counts.get(key, 0))
    return record


def _counts_from_outputs(outputs: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Entity row counts from the produced frames (missing entity → 0 via ``build_run_record``)."""
    return {name: len(df) for name, df in outputs.items()}


def _log_run_record(record: dict[str, Any], *, error: str = "") -> None:
    """Write the structured ``__DISTRICTSYNC_RUN__`` diagnostic-log line for this record.

    The log keeps the RICH free-text ``error`` (ops needs "KeyError: final mark" at 2am);
    it is merged in here and NOWHERE else — the stored record never carries it.
    """
    logger.info(f"__DISTRICTSYNC_RUN__ {json.dumps({**record, 'error': error})}")


def _emit_run_log(
    status: str,
    elapsed: float,
    outputs: dict[str, pd.DataFrame],
    sftp_attempted: bool = False,
    sftp_ok: bool = False,
    error: str = "",
    anomalies: list[str] | None = None,
    data_errors: dict[str, Any] | None = None,
    *,
    source: str = "cli",
    sis_type: str = "",
    error_category: str = RunErrorCategory.NONE.value,
) -> None:
    """Build a run record from ``outputs`` and write the diagnostic-log line (log sink only).

    Retained as the small "count the outputs and log it" convenience the unit tests pin;
    ``run_pipeline`` uses the finer-grained :func:`build_run_record` + :func:`_log_run_record`
    + :func:`_store_run_record` split so the SAME dict reaches both sinks (build once).
    """
    record = build_run_record(
        status=status,
        elapsed=elapsed,
        entity_counts=_counts_from_outputs(outputs),
        sftp_attempted=sftp_attempted,
        sftp_ok=sftp_ok,
        anomalies=anomalies,
        data_errors=data_errors,
        source=source,
        sis_type=sis_type,
        error_category=error_category,
    )
    _log_run_record(record, error=error)


def _store_run_record(record: dict[str, Any], *, source: str) -> bool:
    """Best-effort store write — STRICTLY NON-FATAL (D2b): never raises, never masks a caller error.

    ``write_run_record`` already swallows ``sqlite3.Error`` / ``OSError``; this extra guard
    also swallows anything unexpected (a bug, an import problem) so a store write can NEVER
    change ``run_pipeline``'s result, exit code, CSVs, or — critically at the failure site —
    mask the original ETL exception. Returns whether the row was written.
    """
    try:
        return write_run_record(record, source=source)
    except Exception as exc:  # noqa: BLE001 - the store is a best-effort sink; it must never propagate
        logger.warning("Run-history store write raised unexpectedly (%s); the run is in the diagnostic log", exc)
        return False


def _record_early_failure(t0: float, *, source: str, sis_type: str, error: str, category: str) -> None:
    """Record a pre-ETL failure (bad input dir / config) to BOTH sinks before an early ``sys.exit``.

    The ``sys.exit(1)`` paths inside ``run_pipeline`` re-raise through the ``except SystemExit``
    guard and never reach the generic failure sink — without this, a scheduled run that lost its
    input folder or its config exits 1 with NO run record at all (a false silence: Task Scheduler
    sees the failure, Run History shows nothing). Same guard shape as the failure sink (D2b):
    recording can never raise, never delays or changes the exit code, and the free-text ``error``
    goes to the diagnostic-log line ONLY — the store carries the bounded ``category`` (privacy split).
    """
    try:
        record = build_run_record(
            status="failed",
            elapsed=time.monotonic() - t0,
            entity_counts={},
            source=source,
            sis_type=sis_type,
            error_category=category,
        )
        _log_run_record(record, error=error)  # rich free-text error → LOG only
        _store_run_record(record, source=source)  # store carries error_category only
    except Exception as record_exc:  # noqa: BLE001 - recording must never block the early exit
        logger.error(f"Failed to record the failed run ({record_exc}); exiting anyway")


def _resolve_source(source: str | None) -> str:
    """Resolve the run ``source`` tag: explicit arg → ``DSYNC_SOURCE`` env → ``"cli"``.

    The registered scheduled task passes ``--source scheduled`` (see ``scheduler/windows.py``);
    ``convert_job`` passes ``"manual"``; a container/cron deployment may ``export DSYNC_SOURCE``.
    An out-of-set value coerces to ``"unknown"`` (the store's CHECK constraint's escape hatch).
    """
    resolved = source or os.environ.get(_SOURCE_ENV_VAR) or "cli"
    return resolved if resolved in VALID_SOURCES else "unknown"


def _classify_error_category(exc: BaseException) -> str:
    """Map a failure to the bounded, PII-free :class:`RunErrorCategory` (never the message).

    Classified by exception type/marker, not text interpolation — the store never sees the
    free-text error. ``SystemExit`` never reaches here (it is re-raised before the failure sink).
    """
    if isinstance(exc, RuntimeError) and "No usable required input" in str(exc):
        return RunErrorCategory.NO_INPUT.value
    if isinstance(exc, FileNotFoundError):
        return RunErrorCategory.CONFIG.value
    if isinstance(exc, ValueError):
        # A missing field-map column (loader) / validation surfaces as ValueError.
        return RunErrorCategory.DATA.value
    return RunErrorCategory.UNKNOWN.value


def _summarize_data_errors(data_errors: list[dict]) -> dict[str, Any]:
    """Collapse the per-(entity, field) error ledger into a compact run-log summary.

    ``{"total": <sum of failed_rows>, "by_field": {"<Entity>.<Field>": <rows>}}``.
    Returns an empty dict for a clean run (nothing to surface).
    """
    if not data_errors:
        return {}
    by_field: dict[str, int] = {}
    total = 0
    for rec in data_errors:
        key = f"{rec.get('entity', '?')}.{rec.get('field', '?')}"
        rows = int(rec.get("failed_rows", 0))
        by_field[key] = by_field.get(key, 0) + rows
        total += rows
    return {"total": total, "by_field": by_field}


def run_pipeline(
    sis_type: str,
    input_path: str,
    output_path: str,
    dry_run: bool = False,
    diff: bool = False,
    quality: bool = False,
    sftp: bool = False,
    source: str | None = None,
) -> PipelineResult:
    """Core ETL pipeline with optional dry-run, diff, quality, and SFTP modes.

    Returns a :class:`PipelineResult` whose ``sftp_attempted`` / ``sftp_ok``
    fields let callers (e.g. ``src/main.py``) decide whether to exit non-zero
    when delivery failed.

    ``source`` tags where the run came from for the durable run store
    (manual / scheduled / cli / unknown); it resolves via :func:`_resolve_source`
    (explicit arg → ``DSYNC_SOURCE`` env → ``"cli"``). The store write is
    best-effort and strictly non-fatal — it never changes the result, the CSVs,
    or the exit-code contract (0/1/2/3).
    """
    t0 = time.monotonic()
    resolved_source = _resolve_source(source)
    outputs: dict[str, pd.DataFrame] = {}
    sftp_attempted = False
    sftp_ok = False
    anomalies: list[str] = []

    try:
        input_dir = Path(input_path)
        if not input_dir.exists() or not input_dir.is_dir():
            error = f"Input path is not a directory: {input_dir}"
            logger.error(error)
            _record_early_failure(
                t0,
                source=resolved_source,
                sis_type=sis_type,
                error=error,
                category=RunErrorCategory.NO_INPUT.value,
            )
            sys.exit(1)
        logger.info(f"Input directory: {input_dir.resolve()}")

        # Load and validate config
        try:
            config = load_config(sis_type)
        except FileNotFoundError as e:
            logger.error(str(e))
            _record_early_failure(
                t0,
                source=resolved_source,
                sis_type=sis_type,
                error=str(e),
                category=RunErrorCategory.CONFIG.value,
            )
            sys.exit(1)
        except ValueError as e:
            logger.error(str(e))
            _record_early_failure(
                t0,
                source=resolved_source,
                sis_type=sis_type,
                error=str(e),
                category=RunErrorCategory.CONFIG.value,
            )
            sys.exit(1)

        logger.info(f"Loaded config: sis={config.sis}, version={config.version}")

        # Reconstruct the raw dicts that the transformer pipeline expects,
        # derived from the already-validated MappingConfig (no re-read needed).
        raw = config.to_raw_dict()
        mappings: dict[str, dict] = raw["mappings"]
        global_config: dict[str, Any] = raw["global_config"]

        extractor = DataExtractor(input_path)
        loader = DataLoader(output_path)

        required_files = extract_required_files(config)
        logger.info(f"Required files: {required_files}")

        # Collect explicit headers for headerless files (keyed by filename)
        file_headers: dict[str, list[str]] = {}
        for entity_cfg in mappings.values():
            for filename, header_list in entity_cfg.get("headers", {}).items():
                file_headers[filename] = header_list

        raw_data = extractor.load_data(required_files, file_headers=file_headers)

        # Fail loud on NO USABLE INPUT. A scheduled, unattended run that received
        # no usable required input (wrong folder, truncated export, locked file)
        # must not masquerade as a clean run. The guard keys off INPUT presence
        # (`raw_data`), independent of `run_transform`'s per-entity skip-on-empty
        # — so a partial run (some files present) proceeds, and a period-only
        # attendance run (period file non-empty, daily absent) does NOT fire it.
        if not raw_data or all(df.empty for df in raw_data.values()):
            empty_or_missing = [name for name, df in raw_data.items() if df.empty] or list(required_files)
            raise RuntimeError(
                "No usable required input was loaded — every required file is "
                f"missing or empty: {empty_or_missing}. Check the input folder, "
                "the export job, and that the files are not locked."
            )

        # Shared transform-orchestration (school-year + per-entity loop +
        # enabled_entities filter + field-order collection).
        outputs, field_orders, data_errors = run_transform(raw_data, mappings, global_config)

        # Surface fail-loud field-transform errors (a separate axis from ETL
        # status): consolidated ERROR + a compact run-log summary. The run still
        # completed + delivered, so `status` stays `success`.
        data_errors_summary = _summarize_data_errors(data_errors)
        if data_errors_summary:
            logger.error(
                f"Completed with {data_errors_summary['total']} data error(s) across "
                f"{len(data_errors_summary['by_field'])} field(s): {data_errors_summary['by_field']}"
            )

        # Check for anomalies before writing — including entities this run was
        # configured to produce (enabled-entities-derived, NEVER mappings.keys())
        # that produced nothing while a previous non-empty CSV sits on disk
        # (present→absent / N→0). A partial run with some empty sources stays
        # exit 0 by design — these are warnings, not failures.
        if not dry_run:
            anomalies = _check_anomalies(outputs, Path(output_path), configured_entity_order(mappings, global_config))

        # Write all outputs transactionally (all-or-nothing commit)
        if not dry_run and outputs:
            loader.save_all(outputs, field_orders)

            # Archive (non-destructive) entity CSVs left in the output dir that
            # this run did NOT produce — they were not refreshed and would ship
            # stale in the next SFTP zip. Moving them into archive_<ts>/ (a
            # SUBfolder) excludes them from SFTP's top-level *.csv glob without
            # deleting anything — auto-delete is unsafe under _base inheritance
            # (see DataLoader.archive_stale_outputs). No exit/run-log change.
            archived = loader.archive_stale_outputs(set(outputs))
            if archived:
                logger.info(
                    f"Archived {len(archived)} stale entity CSV(s) not produced by this run into "
                    f"archive_<ts>/ (excluded from SFTP): {archived}"
                )

            # SFTP upload (only on a successful, non-dry-run write)
            if sftp:
                sftp_attempted = True
                sftp_ok = _sftp_upload(output_path, sis_type)

        # Dry-run summary
        if dry_run:
            print("\n=== DRY RUN (no files written) ===")
            for name, df in outputs.items():
                print(f"  {name}: {len(df)} rows, columns: {list(df.columns)}")
            print()

        # Diff against existing output
        if diff:
            _print_diff(outputs, output_path)

        # Quality report
        if quality:
            report = DataQualityReport().analyze(outputs)
            print(report.to_text())

        logger.info("ETL process completed successfully.")

        # Build the run record ONCE (D2a) → the diagnostic-log line AND the durable store.
        # The store write is best-effort/non-fatal and positioned AFTER the output commit,
        # so it can never touch the CSVs or the exit-code contract.
        elapsed = time.monotonic() - t0
        record = build_run_record(
            status="success",
            elapsed=elapsed,
            entity_counts=_counts_from_outputs(outputs),
            sftp_attempted=sftp_attempted,
            sftp_ok=sftp_ok,
            anomalies=anomalies,
            data_errors=data_errors_summary,
            source=resolved_source,
            sis_type=sis_type,
            error_category=RunErrorCategory.NONE.value,
        )
        _log_run_record(record)
        _store_run_record(record, source=resolved_source)

        return PipelineResult(
            entity_counts={name: len(df) for name, df in outputs.items()},
            sftp_attempted=sftp_attempted,
            sftp_ok=sftp_ok,
            anomalies=anomalies,
        )

    except SystemExit:
        raise
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"Pipeline failed: {e}")
        # Record the failure to BOTH sinks — but recording must NEVER raise and mask the
        # original ETL exception (D2b). The whole record/log/store block is guarded; the
        # bare ``raise`` below always re-raises the ORIGINAL ``e`` (exception identity
        # preserved), whatever happens while recording.
        try:
            record = build_run_record(
                status="failed",
                elapsed=elapsed,
                entity_counts=_counts_from_outputs(outputs),
                sftp_attempted=sftp_attempted,
                sftp_ok=sftp_ok,
                source=resolved_source,
                sis_type=sis_type,
                error_category=_classify_error_category(e),
            )
            _log_run_record(record, error=str(e))  # rich free-text error → LOG only
            _store_run_record(record, source=resolved_source)  # store carries error_category only
        except Exception as record_exc:  # noqa: BLE001 - recording must never mask the ETL failure
            logger.error(f"Failed to record the failed run ({record_exc}); re-raising the original error")
        raise


def _sftp_upload(output_path: str, sis_type: str | None = None) -> bool:
    """Upload generated CSV files via SFTP. Returns True on success.

    Never raises — exceptions are caught and logged at ERROR level so the
    caller (``run_pipeline``) can propagate ``sftp_ok=False`` to ``main.py``
    which then decides to exit non-zero.  The already-written CSVs are NOT
    touched on failure.
    """
    try:
        cfg = AppConfig.load()
        if not cfg.sftp_is_configured():
            logger.warning(
                "SFTP upload requested but SFTP is not configured. "
                "Run 'DistrictSync --sftp-configure' or use the setup wizard."
            )
            return False

        host = cfg.sftp_host or "<unknown host>"
        uploader = SFTPUploader(
            host=host,
            port=cfg.sftp_port,
            username=cfg.sftp_username,
            remote_path=cfg.sftp_remote_path,
        )
        uploaded = uploader.upload_csvs(Path(output_path), sis_type=sis_type)
        if uploaded:
            logger.info(f"SFTP upload complete: {len(uploaded)} file(s) — {uploaded}")
            return True
        # upload_csvs returned an empty list (e.g. no CSVs found) — treat as failure
        logger.error(f"SFTP upload FAILED — output files were NOT delivered to {host} (no files were transferred)")
        return False
    except Exception as e:
        try:
            host = AppConfig.load().sftp_host or "<unknown host>"
        except Exception:
            host = "<unknown host>"
        logger.error(f"SFTP upload FAILED — output files were NOT delivered to {host}: {e}")
        return False


def _print_diff(outputs: dict[str, pd.DataFrame], output_path: str) -> None:
    """Compare new outputs against existing CSV files and print changes."""
    output_dir = Path(output_path)
    print("\n=== DIFF vs existing output ===")

    for name, new_df in outputs.items():
        existing_path = output_dir / f"{name}.csv"
        if not existing_path.exists():
            print(f"  {name}: NEW (no existing file)")
            continue

        try:
            old_df = pd.read_csv(existing_path)
        except Exception:
            print(f"  {name}: could not read existing file")
            continue

        old_rows = len(old_df)
        new_rows = len(new_df)
        row_delta = new_rows - old_rows

        old_cols = set(old_df.columns)
        new_cols = set(new_df.columns)
        added_cols = new_cols - old_cols
        removed_cols = old_cols - new_cols

        parts = [f"{name}: {old_rows} -> {new_rows} rows"]
        if row_delta:
            sign = "+" if row_delta > 0 else ""
            parts[0] += f" ({sign}{row_delta})"
        if added_cols:
            parts.append(f"    + columns: {added_cols}")
        if removed_cols:
            parts.append(f"    - columns: {removed_cols}")

        for line in parts:
            print(f"  {line}")

    print()
