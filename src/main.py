import argparse
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.transformer import DataTransformer
from src.quality.report import DataQualityReport
from src.sftp.uploader import SFTPUploader
from src.utils.logger import get_logger
from src.utils.validators import validate_sftp_host, validate_sis_type

logger = get_logger(__name__)

ANOMALY_THRESHOLD = 0.20  # Warn if any entity drops >20% vs previous output


def extract_required_files(config) -> list[str]:
    """Extract all unique source filenames from a validated MappingConfig."""
    files = set()
    for entity_cfg in config.mappings.values():
        files.update(entity_cfg.source_files.values())
    files.update(config.global_config.school_year_sources.values())
    return list(files)


def _check_anomalies(outputs: dict[str, pd.DataFrame], output_dir: Path) -> list[str]:
    """Compare output row counts against previous run; return warning strings."""
    warnings: list[str] = []
    for entity, df in outputs.items():
        prev_path = output_dir / f"{entity}.csv"
        if not prev_path.exists():
            continue
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev_count = sum(1 for _ in f) - 1
        except Exception:  # nosec B112 - skip unreadable previous output files
            continue
        if prev_count > 0 and len(df) < prev_count * (1 - ANOMALY_THRESHOLD):
            pct = ((prev_count - len(df)) / prev_count) * 100
            msg = f"ANOMALY: {entity} dropped from {prev_count} to {len(df)} rows ({pct:.0f}% decrease)"
            logger.warning(msg)
            warnings.append(msg)
    return warnings


def _emit_run_log(
    status: str,
    elapsed: float,
    outputs: dict[str, pd.DataFrame],
    sftp_attempted: bool = False,
    sftp_ok: bool = False,
    error: str = "",
    anomalies: Optional[list[str]] = None,
) -> None:
    """Write a structured __GDE2ACSV_RUN__ log line for the Run History page."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "duration_s": round(elapsed, 1),
        "Students": len(outputs.get("Students", [])),
        "Staff": len(outputs.get("Staff", [])),
        "Family": len(outputs.get("Family", [])),
        "Classes": len(outputs.get("Classes", [])),
        "Enrollments": len(outputs.get("Enrollments", [])),
        "sftp_attempted": sftp_attempted,
        "sftp_ok": sftp_ok,
        "error": error,
        "anomalies": anomalies or [],
    }
    logger.info(f"__GDE2ACSV_RUN__ {json.dumps(entry)}")


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
        transformer = DataTransformer()
        loader = DataLoader(output_path)

        required_files = extract_required_files(config)
        logger.info(f"Required files: {required_files}")

        # Collect explicit headers for headerless files (keyed by filename)
        file_headers: dict[str, list[str]] = {}
        for entity_cfg in mappings.values():
            for filename, header_list in entity_cfg.get("headers", {}).items():
                file_headers[filename] = header_list

        raw_data = extractor.load_data(required_files, file_headers=file_headers)

        # Determine school year
        sy_sources_config = global_config.get("school_year_sources", {})
        sy = transformer.determine_school_year(raw_data, sy_sources_config)
        start_md = global_config.get("academic_start_month_day", "08-25")
        end_md = global_config.get("academic_end_month_day", "07-25")
        transformer.set_school_year(sy, start_md, end_md)
        logger.info(
            f"Using school year {sy}, academic start={transformer.academic_start}, end={transformer.academic_end}"
        )

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

        # Check for anomalies before writing
        if outputs and not dry_run:
            anomalies = _check_anomalies(outputs, Path(output_path))

        # Write all outputs transactionally (all-or-nothing commit)
        if not dry_run and outputs:
            loader.save_all(outputs, field_orders)

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
        _emit_run_log("success", elapsed, outputs, sftp_attempted=sftp_attempted, sftp_ok=sftp_ok, anomalies=anomalies)

    except SystemExit:
        raise
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"Pipeline failed: {e}")
        _emit_run_log("failed", elapsed, outputs, error=str(e), sftp_attempted=sftp_attempted, sftp_ok=sftp_ok)
        raise


def _sftp_upload(output_path: str, sis_type: Optional[str] = None) -> bool:
    """Upload generated CSV files via SFTP. Returns True on success."""
    try:
        cfg = AppConfig.load()
        if not cfg.sftp_is_configured():
            logger.warning(
                "SFTP upload requested but SFTP is not configured. "
                "Run 'GDE2Acsv --sftp-configure' or use the setup wizard."
            )
            return False

        uploader = SFTPUploader(
            host=cfg.sftp_host,
            port=cfg.sftp_port,
            username=cfg.sftp_username,
            remote_path=cfg.sftp_remote_path,
        )
        uploaded = uploader.upload_csvs(Path(output_path), sis_type=sis_type)
        logger.info(f"SFTP upload complete: {len(uploaded)} file(s) — {uploaded}")
        return len(uploaded) > 0
    except Exception as e:
        logger.error(f"SFTP upload failed: {e}")
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


def main(sis_type: str, input_path: str, output_path: str) -> None:
    """Legacy entry point — calls run_pipeline with defaults."""
    run_pipeline(sis_type, input_path, output_path)


# ----------------------------------------------------------------------
# SFTP CLI subcommands — headless / Docker / scripted setup
# ----------------------------------------------------------------------

SFTP_PASSWORD_ENV_VAR = "GDE2ACSV_SFTP_PASSWORD"


def _read_sftp_password(args: argparse.Namespace) -> str:
    """Resolve the SFTP password from (in order): env var, stdin, interactive prompt.

    Raises SystemExit if stdin mode is requested but no password is piped.
    """
    env_pw = os.environ.get(SFTP_PASSWORD_ENV_VAR)
    if env_pw:
        return env_pw
    if args.sftp_password_stdin:
        pw = sys.stdin.read().rstrip("\n")
        if not pw:
            print("Error: --sftp-password-stdin was set but no password was received on stdin.")
            sys.exit(2)
        return pw
    import getpass

    return getpass.getpass("SFTP password: ")


def _sftp_configure(args: argparse.Namespace) -> int:
    """Write SFTP settings to AppConfig and store the password in the OS keyring.

    Two modes:
      1. Headless (all of --sftp-host/--sftp-user/--sftp-remote provided):
         reads password from GDE2ACSV_SFTP_PASSWORD env var, stdin, or prompt.
      2. Interactive (no flags): prompts for every field.

    Host is validated against ALLOWED_SFTP_HOSTS. Returns exit code.
    """
    cfg = AppConfig.load()

    headless = bool(args.sftp_host and args.sftp_user and args.sftp_remote)
    if headless:
        host = args.sftp_host
        port = args.sftp_port
        username = args.sftp_user
        remote_path = args.sftp_remote
    else:
        from src.utils.validators import ALLOWED_SFTP_HOSTS

        print("SpacesEDU SFTP setup — press Ctrl+C to cancel.")
        print(f"Allowed hosts: {', '.join(sorted(ALLOWED_SFTP_HOSTS))}")
        default_host = cfg.sftp_host or "sftp.ca.spacesedu.com"
        host = input(f"Host [{default_host}]: ").strip() or default_host
        port_raw = input(f"Port [{cfg.sftp_port or 22}]: ").strip()
        port = int(port_raw) if port_raw else (cfg.sftp_port or 22)
        username = input(f"Username [{cfg.sftp_username}]: ").strip() or cfg.sftp_username
        remote_path = input(f"Remote path [{cfg.sftp_remote_path}]: ").strip() or cfg.sftp_remote_path

    try:
        host = validate_sftp_host(host)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    if not username:
        print("Error: username is required.")
        return 1
    if not remote_path:
        print("Error: remote path is required.")
        return 1

    password = _read_sftp_password(args)
    if not password:
        print("Error: password is required.")
        return 1

    uploader = SFTPUploader(host=host, port=port, username=username, remote_path=remote_path)
    uploader.store_password(password)

    cfg.sftp_enabled = True
    cfg.sftp_host = host
    cfg.sftp_port = port
    cfg.sftp_username = username
    cfg.sftp_remote_path = remote_path
    cfg.save()

    print(f"SFTP configured: {username}@{host}:{port}{remote_path}")
    print("Password saved to the OS credential store.")
    print("Run 'GDE2Acsv --sftp-test' to verify the connection.")
    return 0


def _sftp_test(args: argparse.Namespace) -> int:
    """Test the SFTP connection using the stored configuration."""
    cfg = AppConfig.load()
    if not cfg.sftp_is_configured():
        print("Error: SFTP is not configured. Run 'GDE2Acsv --sftp-configure' first.")
        return 1

    uploader = SFTPUploader(
        host=cfg.sftp_host,
        port=cfg.sftp_port,
        username=cfg.sftp_username,
        remote_path=cfg.sftp_remote_path,
    )
    ok, msg = uploader.test_connection()
    print(msg)
    return 0 if ok else 1


def _sftp_show(args: argparse.Namespace) -> int:
    """Print the saved SFTP configuration (never the password)."""
    cfg = AppConfig.load()
    if not cfg.sftp_enabled:
        print("SFTP is not configured. Run 'GDE2Acsv --sftp-configure' to set it up.")
        return 0
    print("SFTP configuration:")
    print(f"  host:         {cfg.sftp_host}")
    print(f"  port:         {cfg.sftp_port}")
    print(f"  username:     {cfg.sftp_username}")
    print(f"  remote path:  {cfg.sftp_remote_path}")
    print("  configured:   yes (password stored in OS keyring)")
    return 0


if __name__ == "__main__":
    # No arguments → launch the web UI (e.g. double-clicked from Explorer)
    if len(sys.argv) == 1:
        from src.ui.launcher import main as _launch_ui

        _launch_ui()
        sys.exit(0)

    try:
        version = importlib.metadata.version("gde2acsv")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"

    parser = argparse.ArgumentParser(
        description="SIS Data ETL Tool for myBlueprint - SpacesEDU",
        epilog=(
            "SFTP setup (headless / Docker):\n"
            "  GDE2Acsv --sftp-configure --sftp-host HOST --sftp-user USER --sftp-remote PATH\n"
            f"  (password read from ${SFTP_PASSWORD_ENV_VAR} env var, --sftp-password-stdin, or prompt)\n"
            "  GDE2Acsv --sftp-test       # verify stored credentials\n"
            "  GDE2Acsv --sftp-show       # print current SFTP configuration\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"GDE2Acsv {version}")

    # ETL pipeline flags (required only when running the pipeline)
    parser.add_argument("--sis", help="SIS type (e.g., myedbc, sd40myedbc)")
    parser.add_argument("--input", help="Path to input GDE files")
    parser.add_argument("--output", default="data/output", help="Output path for CSV files")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing output files")
    parser.add_argument("--diff", action="store_true", help="Show diff against existing output files")
    parser.add_argument("--quality", action="store_true", help="Generate a data quality report")
    parser.add_argument("--sftp", action="store_true", help="Upload output CSVs via SFTP after a successful run")

    # SFTP setup subcommands (headless / scripted / Docker-friendly)
    sftp_group = parser.add_argument_group("SFTP setup (choose one)")
    sftp_group.add_argument(
        "--sftp-configure", action="store_true", help="Configure SFTP settings and store password in OS keyring"
    )
    sftp_group.add_argument("--sftp-test", action="store_true", help="Test the stored SFTP connection")
    sftp_group.add_argument(
        "--sftp-show", action="store_true", help="Print the current SFTP configuration (no password)"
    )
    sftp_group.add_argument("--sftp-host", help="SFTP host (headless --sftp-configure)")
    sftp_group.add_argument("--sftp-port", type=int, default=22, help="SFTP port (default: 22)")
    sftp_group.add_argument("--sftp-user", help="SFTP username (headless --sftp-configure)")
    sftp_group.add_argument("--sftp-remote", help="Remote upload path (headless --sftp-configure)")
    sftp_group.add_argument("--sftp-password-stdin", action="store_true", help="Read the SFTP password from stdin")

    args = parser.parse_args()

    # Route to SFTP subcommands (mutually exclusive with the ETL pipeline)
    sftp_actions = [args.sftp_configure, args.sftp_test, args.sftp_show]
    if sum(sftp_actions) > 1:
        print("Error: choose only one of --sftp-configure, --sftp-test, --sftp-show.")
        sys.exit(2)
    if args.sftp_configure:
        sys.exit(_sftp_configure(args))
    if args.sftp_test:
        sys.exit(_sftp_test(args))
    if args.sftp_show:
        sys.exit(_sftp_show(args))

    # ETL pipeline — require --sis and --input
    if not args.sis or not args.input:
        parser.error("--sis and --input are required to run the ETL pipeline (omit to use an SFTP subcommand)")

    try:
        sis = validate_sis_type(args.sis.lower())
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    try:
        run_pipeline(
            sis,
            args.input,
            args.output,
            dry_run=args.dry_run,
            diff=args.diff,
            quality=args.quality,
            sftp=args.sftp,
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"\nError: {e}")
        print("Check etl_tool.log for details. Contact support@myBlueprint.ca for help.")
        sys.exit(1)
