import argparse
import importlib.metadata
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.config.loader import load_config
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.transformer import DataTransformer
from src.quality.report import DataQualityReport
from src.utils.logger import get_logger

logger = get_logger(__name__)


def extract_required_files(config) -> list[str]:
    """Extract all unique source filenames from a validated MappingConfig."""
    files = set()
    for entity_cfg in config.mappings.values():
        files.update(entity_cfg.source_files.values())
    files.update(config.global_config.school_year_sources.values())
    return list(files)


def run_pipeline(
    sis_type: str,
    input_path: str,
    output_path: str,
    dry_run: bool = False,
    diff: bool = False,
    quality: bool = False,
    sftp: bool = False,
) -> None:
    """Core ETL pipeline with optional dry-run, diff, quality, and SFTP modes."""
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
    transformer = DataTransformer()
    loader = DataLoader(output_path)

    required_files = extract_required_files(config)
    logger.info(f"Required files: {required_files}")

    raw_data = extractor.load_data(required_files)

    # Determine school year
    sy_sources_config = global_config.get("school_year_sources", {})
    sy = transformer.determine_school_year(raw_data, sy_sources_config)
    start_md = global_config.get("academic_start_month_day", "08-25")
    end_md = global_config.get("academic_end_month_day", "07-25")
    transformer.set_school_year(sy, start_md, end_md)
    logger.info(f"Using school year {sy}, academic start={transformer.academic_start}, end={transformer.academic_end}")

    outputs: dict[str, pd.DataFrame] = {}
    field_orders: dict[str, list[str]] = {}

    entity_order = global_config.get("entity_order") or list(mappings.keys())
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

        primary_source = source_files[0]
        primary_df = raw_data.get(primary_source, pd.DataFrame())

        if primary_df.empty:
            logger.warning(f"Primary source file '{primary_source}' is empty for '{entity_name}'; skipping.")
            continue

        transformed = transformer.transform(primary_df, entity_cfg, entity_name, raw_data, global_config)

        if transformed.empty:
            logger.warning(f"No data transformed for entity '{entity_name}'; skipping.")
            continue

        outputs[entity_name] = transformed
        field_orders[entity_name] = list(entity_cfg.get("field_map", {}).keys())

    # Write all outputs transactionally (all-or-nothing commit)
    if not dry_run and outputs:
        loader.save_all(outputs, field_orders)

        # SFTP upload (only on a successful, non-dry-run write)
        if sftp:
            _sftp_upload(output_path)

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


def _sftp_upload(output_path: str) -> None:
    """Upload generated CSV files via SFTP using stored app config + keyring credentials."""
    try:
        from src.config.app_config import AppConfig
        from src.sftp.uploader import SFTPUploader

        cfg = AppConfig.load()
        if not cfg.sftp_is_configured():
            logger.warning("SFTP upload requested but SFTP is not configured. "
                           "Run the setup wizard to configure SFTP settings.")
            return

        uploader = SFTPUploader(
            host=cfg.sftp_host,
            port=cfg.sftp_port,
            username=cfg.sftp_username,
            remote_path=cfg.sftp_remote_path,
        )
        uploaded = uploader.upload_csvs(Path(output_path))
        logger.info(f"SFTP upload complete: {len(uploaded)} file(s) — {uploaded}")
    except ImportError as e:
        logger.warning(f"SFTP upload skipped — missing dependency: {e}")
    except Exception as e:
        logger.error(f"SFTP upload failed: {e}")
        # SFTP failure does not abort the pipeline — CSVs are already written


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


def main(sis_type: str, input_path: str, output_path: str) -> None:
    """Legacy entry point — calls run_pipeline with defaults."""
    run_pipeline(sis_type, input_path, output_path)


if __name__ == "__main__":
    try:
        version = importlib.metadata.version("gde2acsv")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"

    parser = argparse.ArgumentParser(
        description="SIS Data ETL Tool for myBlueprint - SpacesEDU"
    )
    parser.add_argument("--version", action="version", version=f"GDE2Acsv {version}")
    parser.add_argument("--sis", required=True, help="SIS type (e.g., myedbc)")
    parser.add_argument("--input", required=True, help="Path to input GDE files")
    parser.add_argument("--output", default="data/output", help="Output path for CSV files")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing output files")
    parser.add_argument("--diff", action="store_true", help="Show diff against existing output files")
    parser.add_argument("--quality", action="store_true", help="Generate a data quality report")
    parser.add_argument("--sftp", action="store_true",
                        help="Upload output CSVs via SFTP after a successful run")
    args = parser.parse_args()
    run_pipeline(
        args.sis.lower(),
        args.input,
        args.output,
        dry_run=args.dry_run,
        diff=args.diff,
        quality=args.quality,
        sftp=args.sftp,
    )
