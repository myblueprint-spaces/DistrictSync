"""DistrictSync CLI entry point.

Thin wrapper that parses command-line arguments and dispatches to the
core pipeline (src/etl/pipeline.py) or the SFTP setup subcommands
defined below. Keeping the heavy logic out of this file is deliberate:
this module is the PyInstaller entry point, so in a frozen one-file
build it runs as ``__main__`` rather than as ``src.main``. Callers
that need ``extract_required_files`` or ``run_pipeline`` should import
them from ``src.etl.pipeline`` — this module re-exports them for
backward compatibility only.

``cli(argv) -> int`` is THE entry point: the ``__main__`` block, the
``[project.scripts]`` console script, and the test suite all go through
it. It RETURNS the process exit code rather than calling ``sys.exit``,
so the documented contract (0/1/2/3) is directly assertable — see
``tests/test_cli_entry.py``.
"""

import argparse
import logging
import os
import sys
from typing import IO, Callable

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
from src.utils.paths import migrate_legacy_data_dir
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
    "cli",
    "extract_required_files",
    "main",
    "run_pipeline",
]

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


def _configure_cli_logging() -> logging.Logger:
    """Configure the shared file-log sink for a CLI run and return the app logger.

    Deferred out of import time (D3): importing ``src.main`` — e.g. to reach a
    re-exported symbol or an SFTP subcommand helper from a test — must never attach
    a handler to the real user log (the source of the "dummy" Run History records).
    Every CLI entry path calls this exactly once so the run and its exit-code-3
    summary are written to ``etl_tool.log``.
    """
    return get_logger(__name__)


def main(sis_type: str, input_path: str, output_path: str) -> None:
    """Legacy 3-argument pipeline helper — NOT the command-line entry point.

    Kept for in-repo callers that want a one-line "run the pipeline with
    defaults" (see ``tests/test_contract.py``). The CLI entry point — the one
    the console script and the frozen exe run — is :func:`cli`.
    """
    run_pipeline(sis_type, input_path, output_path)


# ----------------------------------------------------------------------
# Console attach — make CLI output visible from a GUI-subsystem exe
# ----------------------------------------------------------------------

# (sys attribute, console device, open mode) for each std stream. CONOUT$ and
# CONIN$ are the console's own devices; opening them after AttachConsole binds
# the process to the terminal that launched it.
_CONSOLE_STREAMS: tuple[tuple[str, str, str], ...] = (
    ("stdout", "CONOUT$", "w"),
    ("stderr", "CONOUT$", "w"),
    ("stdin", "CONIN$", "r"),
)

_ATTACH_PARENT_PROCESS = -1  # DWORD 0xFFFFFFFF — "the console of my parent process"


def _win32_attach_parent_console() -> bool:
    """Call ``AttachConsole(ATTACH_PARENT_PROCESS)``. The ONLY Windows syscall here.

    Isolated as a seam so the surrounding policy (below) is testable on POSIX CI.
    ``AttachConsole`` *borrows* an EXISTING console; it never allocates one — we
    deliberately do not call ``AllocConsole``, because that would pop a black box
    on the double-click path the GUI-subsystem packaging exists to avoid.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # win32-only, guarded by caller
    return bool(kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))


def _open_console_stream(device: str, mode: str) -> IO[str]:
    """Open a console device as a line-buffered text stream. Injected seam (see above)."""
    # SIM115: deliberately un-closed — this becomes a process-lifetime std stream.
    return open(device, mode, buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115


def _attach_parent_console() -> bool:
    """Bind this process to the console it was launched from, if there is one (Windows).

    The released Windows exe is packed GUI-subsystem (``Subsystem == 2``, gated by
    ``scripts/ci_flet_pack_smoke.py``) so a double-click never flashes a console.
    The cost is that Python starts with ``sys.stdout``/``stderr``/``stdin`` set to
    ``None``: every ``print`` in a CLI run silently no-ops and ``input()`` raises —
    so a partner running the exe in a terminal, exactly as the partner docs say to,
    saw nothing at all.

    Policy, in order:

    1. **POSIX → no-op.** Nothing to fix; CI runs Ubuntu.
    2. **All streams already usable → no-op.** A source run, or a redirected
       ``DistrictSync.exe --sis ... > out.txt``, already has real streams. We do
       not attach and we do not rebind — rebinding would silently break the
       redirect the operator asked for.
    3. **No parent console → no-op.** A scheduled task, a service, or a
       double-click from Explorer has no console to borrow; ``AttachConsole``
       fails and the streams stay ``None`` (quiet, exactly as today).
    4. **Otherwise** rebind ONLY the dead (``None``) streams, per stream — so a
       partially-redirected invocation keeps its redirect.

    The ``sys.__stdout__``/``__stderr__``/``__stdin__`` originals are rebound too:
    ``getpass`` compares ``sys.stdin is sys.__stdin__`` to decide whether it may use
    the no-echo console reader, and falling back would echo an SFTP password in
    plaintext.

    Returns True only if a console was attached AND at least one stream was rebound.
    Never raises: "there is no console" is a normal environment state, not a
    configuration error, and the caller records the outcome to ``etl_tool.log``.
    """
    if sys.platform != "win32":
        return False
    if all(getattr(sys, name, None) is not None for name, _device, _mode in _CONSOLE_STREAMS):
        return False

    try:
        if not _win32_attach_parent_console():
            return False
    except (AttributeError, OSError):
        # No kernel32 (non-Windows host masquerading, hardened runtime): treat as
        # "no console" — the CLI stays quiet rather than dying before it starts.
        return False

    rebound = False
    for name, device, mode in _CONSOLE_STREAMS:
        if getattr(sys, name, None) is not None:
            continue
        try:
            stream = _open_console_stream(device, mode)
        except OSError:
            continue
        setattr(sys, name, stream)
        setattr(sys, f"__{name}__", stream)
        rebound = True
    return rebound


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


def _exit_code_from(exc: SystemExit) -> int:
    """Translate a ``SystemExit`` into the exit code CPython would give the process.

    ``cli`` returns an int so the exit-code contract is assertable, but argparse
    (usage errors, ``--version``, ``--help``), ``_read_sftp_password``'s empty-stdin
    guard, and ``run_pipeline``'s early bad-input/bad-config exits all signal via
    ``SystemExit``. Translating here — rather than letting them escape — means the
    caller does exactly one thing (``sys.exit(cli())``) and there is nothing left
    for a test to re-implement.
    """
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    # CPython prints a non-int SystemExit payload to stderr and exits 1.
    print(code, file=sys.stderr)
    return 1


def cli(argv: list[str] | None = None) -> int:
    """Run the DistrictSync command line and RETURN the process exit code.

    ``argv`` is the argument list WITHOUT the program name (defaults to
    ``sys.argv[1:]``). An empty list is the no-argument case → launch the desktop
    UI. This is the single entry point: the ``__main__`` block below, the
    ``districtsync`` console script (``pyproject.toml`` ``[project.scripts]``),
    and the frozen exe all call it.

    Exit codes (the contract — see the inventory at the exit-3 branch below):
    ``0`` success · ``1`` ETL/validation error · ``2`` argument misuse ·
    ``3`` SFTP delivery failed with ETL output present.
    """
    try:
        return _cli(argv)
    except SystemExit as exc:
        return _exit_code_from(exc)


def _cli(argv: list[str] | None) -> int:
    args_list = sys.argv[1:] if argv is None else list(argv)

    # No arguments → launch the UI (e.g. double-clicked from Explorer).
    # The launcher configures its own logging sink (launcher.boot_logging).
    # NOTE: the console attach below is deliberately AFTER this branch — the
    # double-click path must never touch a console.
    if not args_list:
        _default_ui_launcher()()
        return 0

    # Relocate a legacy ~/.districtsync profile to the platform data dir BEFORE
    # configuring the log sink, so the log opens in the post-migration location.
    # Idempotent + failure-safe (falls back to the legacy dir on any error, never
    # raises), so this is a cheap exists()-check no-op on every already-migrated run.
    migrate_legacy_data_dir()

    # Borrow the launching terminal's console (Windows GUI-subsystem exe) BEFORE
    # logging is configured, so logging.conf's WARNING+ consoleHandler binds to a
    # live sys.stdout instead of None. No-op on POSIX and wherever there is no
    # parent console. See _attach_parent_console.
    console_attached = _attach_parent_console()

    # CLI entry path: configure the shared file-log sink now (deferred from import
    # time so importing src.main in tests never touches the real user profile).
    logger = _configure_cli_logging()
    logger.debug(
        "parent-console attach: %s",
        "attached (CLI output visible in the launching terminal)"
        if console_attached
        else "not attached (POSIX, no parent console, or streams already usable)",
    )

    # Best-effort sweep of any orphaned elevation-handshake files (D5) — never fatal.
    from src.scheduler.elevation import sweep_orphans

    sweep_orphans()

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
    parser.add_argument(
        "--acknowledge-shrink",
        action="store_true",
        help="Deliver anyway when the student roster is much smaller than the previous run. "
        "By default an unattended run REFUSES to deliver a roster that shrank past the anomaly "
        "threshold (a broken export would deactivate the missing students in SpacesEDU) and exits "
        "non-zero, leaving the last-good output untouched. Set this for THIS run only to accept a "
        "genuine drop (e.g. a year-end collapse); it is never saved.",
    )
    parser.add_argument(
        "--source",
        choices=["manual", "scheduled", "cli"],
        default=None,
        help="Run origin tag for the run-history store (the registered scheduled task passes 'scheduled'; "
        "defaults to the DSYNC_SOURCE env var, else 'cli')",
    )

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

    args = parser.parse_args(args_list)

    # Route to SFTP subcommands (mutually exclusive with the ETL pipeline)
    sftp_actions = [args.sftp_configure, args.sftp_test, args.sftp_show]
    if sum(sftp_actions) > 1:
        print("Error: choose only one of --sftp-configure, --sftp-test, --sftp-show.")
        return 2
    if args.sftp_configure:
        return _sftp_configure(args)
    if args.sftp_test:
        return _sftp_test(args)
    if args.sftp_show:
        return _sftp_show(args)

    # ETL pipeline — require --sis and --input
    if not args.sis or not args.input:
        parser.error("--sis and --input are required to run the ETL pipeline (omit to use an SFTP subcommand)")

    try:
        sis = validate_sis_type(args.sis.lower())
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    try:
        result = run_pipeline(
            sis,
            args.input,
            args.output,
            dry_run=args.dry_run,
            diff=args.diff,
            quality=args.quality,
            sftp=args.sftp,
            source=args.source,
            acknowledge_shrink=args.acknowledge_shrink,
        )
    except SystemExit:
        # run_pipeline's own early exits (bad input dir / bad config) already
        # recorded a failed run; let cli() translate the code verbatim.
        raise
    except Exception as e:
        print(f"\nError: {e}")
        print("Check etl_tool.log for details. Contact support@myBlueprint.ca for help.")
        return 1

    # Exit code 3: SFTP was requested and attempted but delivery failed.
    # The ETL output (CSV files) is already written and intact — only the
    # upload to SpacesEDU failed.  Non-zero exit lets Task Scheduler flag
    # the run as failed so operators are not left with a false green.
    #
    # Exit code inventory (asserted end-to-end in tests/test_cli_entry.py):
    #   0 — success (ETL complete; SFTP succeeded or not requested)
    #   1 — ETL / validation error (run did not complete)
    #   2 — argument misuse: an argparse usage error (missing/unknown flags),
    #       more than one SFTP subcommand, or --sftp-password-stdin with an
    #       empty stdin
    #   3 — SFTP delivery failure (ETL succeeded; upload did not)
    if result.sftp_attempted and not result.sftp_ok:
        # _sftp_upload already logged at ERROR level with the host; re-emit
        # a brief summary here so the Task Scheduler "Last Run Result" note
        # is clearly non-zero.
        logger.error(
            "Run exiting with code 3: SFTP upload was attempted but failed. "
            "Output CSVs are present on disk. Check logs for details."
        )
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(cli())
