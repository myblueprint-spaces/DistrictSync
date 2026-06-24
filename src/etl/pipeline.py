"""Core ETL pipeline — separated from src/main.py so it can be imported.

src/main.py is the PyInstaller entry point; in a frozen one-file exe the
entry script runs as ``__main__``, not as a proper module, so
``from src.main import run_pipeline`` fails at runtime even though it
works in dev. Callers (the Streamlit UI, tests, CLI) import from this
module instead and stay decoupled from the CLI argparse layer.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.transformer import DataTransformer
from src.quality.report import DataQualityReport
from src.sftp.uploader import SFTPUploader

logger = logging.getLogger(__name__)

ANOMALY_THRESHOLD = 0.20  # Warn if any entity drops >20% vs previous output


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
    enabled_attr = getattr(config.global_config, "enabled_entities", None)
    enabled = set(enabled_attr) if isinstance(enabled_attr, list) and enabled_attr else None

    files: set[str] = set()
    for entity_name, entity_cfg in config.mappings.items():
        if enabled is not None and entity_name not in enabled:
            continue
        files.update(entity_cfg.source_files.values())
    return list(files)


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

    entity_order = global_config.get("entity_order") or list(mappings.keys())
    # `enabled_entities` (when non-empty) filters which mappings actually run.
    # This lets the base config define more entity templates than it
    # activates by default — districts opt in by listing them.
    enabled = global_config.get("enabled_entities") or []
    if enabled:
        enabled_set = set(enabled)
        entity_order = [e for e in entity_order if e in enabled_set]
    for entity_name in entity_order:
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


def compute_anomalies(outputs: dict[str, pd.DataFrame], output_dir: Path) -> list[str]:
    """Pure row-count-drop compute shared by the CLI and the Convert page.

    Compares each output entity's row count against its previous run's CSV in
    ``output_dir`` and returns a plain per-entity warning string for every
    entity that dropped by more than :data:`ANOMALY_THRESHOLD`. The base
    message — ``"{entity} dropped from {prev} to {new} rows ({pct}% decrease)"``
    — carries NO surface-specific presentation (no ``ANOMALY:`` prefix, no
    logging/printing); each caller adds its own (CLI logs, UI renders).

    Entities with no previous file, an unreadable previous file, or an empty
    previous file are skipped — a missing baseline is fine, not an anomaly.
    """
    warnings: list[str] = []
    for entity, df in outputs.items():
        prev_path = output_dir / f"{entity}.csv"
        if not prev_path.exists():
            continue
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev_count = sum(1 for _ in f) - 1
        # Skip unreadable previous output files — missing baseline is fine
        except Exception:  # nosec B112
            continue
        if prev_count > 0 and len(df) < prev_count * (1 - ANOMALY_THRESHOLD):
            pct = ((prev_count - len(df)) / prev_count) * 100
            warnings.append(f"{entity} dropped from {prev_count} to {len(df)} rows ({pct:.0f}% decrease)")
    return warnings


def _check_anomalies(outputs: dict[str, pd.DataFrame], output_dir: Path) -> list[str]:
    """CLI renderer over :func:`compute_anomalies`: prefix ``ANOMALY:`` and log.

    Thin wrapper that adds the CLI's existing presentation to each shared base
    warning — the ``ANOMALY:``-prefixed ``logger.warning`` lines — and returns
    the prefixed strings (consumed by the structured run-log's ``anomalies``).
    """
    warnings: list[str] = []
    for msg in compute_anomalies(outputs, output_dir):
        prefixed = f"ANOMALY: {msg}"
        logger.warning(prefixed)
        warnings.append(prefixed)
    return warnings


def _emit_run_log(
    status: str,
    elapsed: float,
    outputs: dict[str, pd.DataFrame],
    sftp_attempted: bool = False,
    sftp_ok: bool = False,
    error: str = "",
    anomalies: list[str] | None = None,
    data_errors: dict[str, Any] | None = None,
) -> None:
    """Write a structured __DISTRICTSYNC_RUN__ log line for the Run History page.

    ``data_errors`` is a compact summary (``{"total": N, "by_field": {...}}``)
    of non-fatal field-transform problems — a separate axis from ``status``
    (which stays ``success``/``failed`` for the ETL run itself). Run History
    renders it as "Completed with N data errors". Absent/empty on a clean run.
    """
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "duration_s": round(elapsed, 1),
        "Students": len(outputs.get("Students", [])),
        "Staff": len(outputs.get("Staff", [])),
        "Family": len(outputs.get("Family", [])),
        "Classes": len(outputs.get("Classes", [])),
        "Enrollments": len(outputs.get("Enrollments", [])),
        "CourseInfo": len(outputs.get("CourseInfo", [])),
        "StudentCourses": len(outputs.get("StudentCourses", [])),
        "StudentAttendance": len(outputs.get("StudentAttendance", [])),
        "sftp_attempted": sftp_attempted,
        "sftp_ok": sftp_ok,
        "error": error,
        "anomalies": anomalies or [],
        "data_errors": data_errors or {},
    }
    logger.info(f"__DISTRICTSYNC_RUN__ {json.dumps(entry)}")


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
) -> PipelineResult:
    """Core ETL pipeline with optional dry-run, diff, quality, and SFTP modes.

    Returns a :class:`PipelineResult` whose ``sftp_attempted`` / ``sftp_ok``
    fields let callers (e.g. ``src/main.py``) decide whether to exit non-zero
    when delivery failed.
    """
    t0 = time.monotonic()
    outputs: dict[str, pd.DataFrame] = {}
    sftp_attempted = False
    sftp_ok = False
    anomalies: list[str] = []

    try:
        input_dir = Path(input_path)
        if not input_dir.exists() or not input_dir.is_dir():
            logger.error(f"Input path is not a directory: {input_dir}")
            sys.exit(1)
        logger.info(f"Input directory: {input_dir.resolve()}")

        # Load and validate config
        try:
            config = load_config(sis_type)
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        except ValueError as e:
            logger.error(str(e))
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

        # Check for anomalies before writing
        if outputs and not dry_run:
            anomalies = _check_anomalies(outputs, Path(output_path))

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

        # Emit structured run log
        elapsed = time.monotonic() - t0
        _emit_run_log(
            "success",
            elapsed,
            outputs,
            sftp_attempted=sftp_attempted,
            sftp_ok=sftp_ok,
            anomalies=anomalies,
            data_errors=data_errors_summary,
        )

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
        _emit_run_log("failed", elapsed, outputs, error=str(e), sftp_attempted=sftp_attempted, sftp_ok=sftp_ok)
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
