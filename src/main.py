"""DistrictSync CLI entry point.

Thin wrapper that parses command-line arguments and dispatches to the
core pipeline (src/etl/pipeline.py) or the SFTP setup subcommands
defined below. Keeping the heavy logic out of this file is deliberate:
this module is the PyInstaller entry point, so in a frozen one-file
build it runs as ``__main__`` rather than as ``src.main``. Callers
that need ``extract_required_files`` or ``run_pipeline`` should import
them from ``src.etl.pipeline`` — this module re-exports them for
backward compatibility only.
"""

import argparse
import os
import sys
from typing import Callable

from src.config.app_config import AppConfig
from src.etl.pipeline import (
    ANOMALY_THRESHOLD,
    PipelineResult,
    _check_anomalies,
    _emit_run_log,
    _print_diff,
    _sftp_upload,
    extract_required_files,
    run_pipeline,
)
from src.sftp.uploader import SFTPUploader
from src.utils.logger import get_logger
from src.utils.validators import validate_sftp_host, validate_sis_type
from src.utils.version import app_version

__all__ = [
    "ANOMALY_THRESHOLD",
    "PipelineResult",
    "SFTP_PASSWORD_ENV_VAR",
    "_check_anomalies",
    "_emit_run_log",
    "_print_diff",
    "_sftp_upload",
    "extract_required_files",
    "main",
    "run_pipeline",
]

logger = get_logger(__name__)
# Keep the re-export references alive (used via __all__).
_ = (
    ANOMALY_THRESHOLD,
    PipelineResult,
    extract_required_files,
    run_pipeline,
    _check_anomalies,
    _emit_run_log,
    _print_diff,
    _sftp_upload,
)


def main(sis_type: str, input_path: str, output_path: str) -> None:
    """Legacy entry point — calls run_pipeline with defaults."""
    run_pipeline(sis_type, input_path, output_path)


# ----------------------------------------------------------------------
# SFTP CLI subcommands — headless / Docker / scripted setup
# ----------------------------------------------------------------------

SFTP_PASSWORD_ENV_VAR = "DISTRICTSYNC_SFTP_PASSWORD"  # nosec B105


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
         reads password from DISTRICTSYNC_SFTP_PASSWORD env var, stdin, or prompt.
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
    print("Run 'DistrictSync --sftp-test' to verify the connection.")
    return 0


def _sftp_test(args: argparse.Namespace) -> int:
    """Test the SFTP connection using the stored configuration."""
    cfg = AppConfig.load()
    if not cfg.sftp_is_configured():
        print("Error: SFTP is not configured. Run 'DistrictSync --sftp-configure' first.")
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
        print("SFTP is not configured. Run 'DistrictSync --sftp-configure' to set it up.")
        return 0
    print("SFTP configuration:")
    print(f"  host:         {cfg.sftp_host}")
    print(f"  port:         {cfg.sftp_port}")
    print(f"  username:     {cfg.sftp_username}")
    print(f"  remote path:  {cfg.sftp_remote_path}")
    print("  configured:   yes (password stored in OS keyring)")
    return 0


def _default_ui_launcher() -> Callable[[], None]:
    """Return the no-argv UI launcher — the native Flet shell.

    Flet is the only UI. This one-line seam keeps the no-argv dispatch
    testable by identity (a monkeypatched sentinel) without launching a
    window; it is the ONLY place the default UI entry point is named.
    """
    from src.ui_flet.launcher import main as _launch_ui

    return _launch_ui


if __name__ == "__main__":
    # No arguments → launch the UI (e.g. double-clicked from Explorer).
    if len(sys.argv) == 1:
        _default_ui_launcher()()
        sys.exit(0)

    # Single source (src/utils/version.py): build-stamped tag → package
    # metadata → "dev". A frozen exe reports the real release via the
    # tag-stamped src/_version.py; importlib alone would always say "dev".
    version = app_version()

    parser = argparse.ArgumentParser(
        description="SIS Data ETL Tool for myBlueprint - SpacesEDU",
        epilog=(
            "SFTP setup (headless / Docker):\n"
            "  DistrictSync --sftp-configure --sftp-host HOST --sftp-user USER --sftp-remote PATH\n"
            f"  (password read from ${SFTP_PASSWORD_ENV_VAR} env var, --sftp-password-stdin, or prompt)\n"
            "  DistrictSync --sftp-test       # verify stored credentials\n"
            "  DistrictSync --sftp-show       # print current SFTP configuration\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"DistrictSync {version}")

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
        result = run_pipeline(
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

    # Exit code 3: SFTP was requested and attempted but delivery failed.
    # The ETL output (CSV files) is already written and intact — only the
    # upload to SpacesEDU failed.  Non-zero exit lets Task Scheduler flag
    # the run as failed so operators are not left with a false green.
    #
    # Exit code inventory:
    #   0 — success (ETL complete; SFTP succeeded or not requested)
    #   1 — ETL / argument / validation error (run did not complete)
    #   2 — stdin empty / mutual-exclusion flag error
    #   3 — SFTP delivery failure (ETL succeeded; upload did not)
    if result.sftp_attempted and not result.sftp_ok:
        # _sftp_upload already logged at ERROR level with the host; re-emit
        # a brief summary here so the Task Scheduler "Last Run Result" note
        # is clearly non-zero.
        logger.error(
            "Run exiting with code 3: SFTP upload was attempted but failed. "
            "Output CSVs are present on disk. Check logs for details."
        )
        sys.exit(3)
