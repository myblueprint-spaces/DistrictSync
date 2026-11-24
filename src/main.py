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
from datetime import date
from pathlib import Path
from typing import IO, Callable

# The START GUARD (plan 0054 S5 follow-up). As a frozen script this module runs as ``__main__``, and this block is the FIRST
# thing it does, before the heavy imports below: the start is counted before pandas, the ETL or Flet are imported, so an
# update that breaks those imports is still counted and recovered (see src/update/entry.py). Applies to the installed flavour
# under a Velopack install only; everywhere else ``run_entry`` returns None and the module carries on exactly as before. The
# guarded run imports ``src.main`` and calls ``cli`` itself, so nothing below runs a second time.
# Plan 0055 M1: Velopack's hooks (``--veloapp-*``) are dispatched FIRST, before the start guard below, because the guard
# resolves the profile and an MSI may run a hook as SYSTEM (a Group Policy or Intune install). The hook path imports only
# ``src.update.startup`` and touches no profile, log, keyring or registry (pinned by tests/test_install_kind.py).
if __name__ == "__main__":
    from src.update.startup import is_velopack_hook, run_velopack_hook

    if is_velopack_hook(sys.argv[1:]):
        sys.exit(run_velopack_hook())

if __name__ == "__main__":
    from src.update.entry import run_entry

    _guarded_exit = run_entry()
    if _guarded_exit is not None:
        sys.exit(_guarded_exit)


from src.config.app_config import AppConfig
from src.etl.pipeline import (
    ANOMALY_THRESHOLD,
    PipelineResult,
    _check_anomalies,
    _emit_run_log,
    _print_diff,
    _resolve_source,
    _sftp_upload,
    extract_required_files,
    run_pipeline,
)
from src.etl.sync_window import in_sync_window, next_resume_date
from src.sftp.uploader import SFTPUploader
from src.update import policy
from src.utils.logger import get_logger
from src.utils.paths import MachineScopeRefused, handshake_dir, migrate_legacy_data_dir, pin_data_dir
from src.utils.validators import validate_sftp_host, validate_sis_type
from src.utils.version import app_version, startup_banner

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


def _configure_cli_logging(log_file: Path | None = None) -> logging.Logger:
    """Configure the shared file-log sink for a CLI run and return the app logger.

    Deferred out of import time (D3): importing ``src.main`` — e.g. to reach a
    re-exported symbol or an SFTP subcommand helper from a test — must never attach
    a handler to the real user log (the source of the "dummy" Run History records).
    Every CLI entry path calls this exactly once so the run and its exit-code-3
    summary are written to ``etl_tool.log``.

    ``log_file`` is passed by exactly one caller — :func:`_report_machine_scope_refusal`,
    where the profile resolver itself has refused and the normal sink cannot be resolved.
    """
    return get_logger(__name__, log_file=log_file)


def _report_machine_scope_refusal(exc: MachineScopeRefused) -> int:
    """Report a refused machine-scoped profile and return the documented exit code 1.

    The failure has no other catcher: ``cli()`` handles only ``SystemExit``, so without
    this the scheduled nightly would die with a bare traceback on a stderr the Task
    Scheduler discards — a silent nightly, which is the exact failure mode plan 0049
    exists to remove.

    The sink is the per-user :func:`~src.utils.paths.handshake_dir` (always resolvable —
    it never consults the switch), and the line carries a stable anchor plus the bounded
    reason and the path, so a district's log has one thing to grep for. Configuring that
    sink is itself best-effort: if even that fails, the error still goes out through
    logging's last resort rather than being swallowed.
    """
    logger = logging.getLogger(__name__)
    try:
        directory = handshake_dir()
        directory.mkdir(parents=True, exist_ok=True)
        logger = _configure_cli_logging(directory / "etl_tool.log")
    except OSError as sink_exc:
        logger.error("Could not open a log file to report the failure below (%s).", sink_exc)
    logger.error("DistrictSync cannot start [machine-scope refused: %s] — %s", exc.reason.value, exc)
    return 1


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


#: Plan 0055 M2: what ``--machine-setup`` prints for each bounded outcome. Plain words for a district IT person; no
#: account, no path from this computer except the shared folder's fixed location.
MACHINE_SETUP_DONE = "This computer now keeps one shared DistrictSync profile in C:\\ProgramData\\DistrictSync."
MACHINE_SETUP_COPY: dict[str, str] = {
    "declined": "The Windows permission prompt was declined, so nothing was changed.",
    "launch_failed": "Windows did not show the permission prompt, so nothing was changed.",
    "unconfirmed": "The elevated step did not report back in time; see the line below for what is now true.",
    "different_account": (
        "The permission prompt was answered by a different administrator, so nothing was changed. "
        "Run this as the administrator who will look after DistrictSync."
    ),
    "unavailable": "The machine setup could not be started on this computer (see the log), so nothing was changed.",
    "failed": "The machine setup failed (see the log).",
}


#: What a committed but unusable shared folder means (the child committed against a folder the app refuses).
MACHINE_SETUP_REFUSED_FOLDER = (
    "The shared profile is switched on, but DistrictSync cannot use the folder C:\\ProgramData\\DistrictSync "
    "(reason: {reason}). Run DistrictSync.exe --diagnose and send the report to your support contact."
)
#: Outcomes in which the elevated step never ran, so nothing was changed.
_MACHINE_SETUP_NOT_RUN = frozenset({"declined", "launch_failed", "different_account", "unavailable"})
#: Flags that belong to a conversion or to SFTP setup; none of them may ride along with --machine-setup.
_NOT_WITH_MACHINE_SETUP = frozenset(
    {
        "--sis",
        "--input",
        "--output",
        "--dry-run",
        "--diff",
        "--quality",
        "--sftp",
        "--source",
        "--sftp-host",
        "--sftp-port",
        "--sftp-user",
        "--sftp-remote",
        "--sftp-password-stdin",
    }
)


def _is_windows() -> bool:
    """A seam: the platform check without patching the global ``sys.platform`` the preamble also reads."""
    return sys.platform == "win32"


def _machine_setup(args: argparse.Namespace) -> int:
    """``--machine-setup [--updates off|notify|on]`` (plan 0055 M2). Exit 0 only when the shared profile is in use.

    Unelevated, like the window: the elevated half is the existing provisioning child behind ONE UAC prompt, and the
    parent half (``complete_handover``: re-pin, fence, ``MOVED.txt``) runs here exactly as it does after a Schedule
    press. No nightly is registered; schedule it afterwards. A per-user installed copy is refused: a shared profile is
    for accounts that cannot read one person's profile, and that copy lives in one.
    """
    if not _is_windows():
        print("Error: --machine-setup is for Windows.")
        return 1
    from src.scheduler import provision_session
    from src.scheduler.task_com import runs_from_per_user_install

    if runs_from_per_user_install(sys.executable):
        print(
            "Error: this copy is installed for one Windows account only, so it cannot be set up for the whole "
            "computer. Install the machine-wide DistrictSync, then run --machine-setup from it."
        )
        return 1
    mode = args.updates or policy.DEFAULT_UPDATE_MODE
    before = provision_session.switch_state()  # True, False, or None when it cannot be read
    cfg = AppConfig.load()
    attempt = provision_session.request_machine_setup(
        update_mode=mode,
        sftp=bool(cfg.sftp_enabled),
        sftp_host=cfg.sftp_host or "",
        sftp_username=cfg.sftp_username or "",
    )
    outcome = attempt.outcome.value
    if outcome == "refused":
        step = attempt.step.value if attempt.step is not None else "unknown"
        code = f", Windows permissions code {attempt.icacls_exit}" if attempt.icacls_exit is not None else ""
        print(f"The machine setup stopped at step '{step}'{code}; see the log. Nothing after that step was changed.")
    elif outcome != "provisioned":
        print(MACHINE_SETUP_COPY.get(outcome, MACHINE_SETUP_COPY["failed"]))
    handover = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)
    if handover.refused is not None:
        print(MACHINE_SETUP_REFUSED_FOLDER.format(reason=handover.refused.value))
        return 1
    if not handover.handed_over:
        print("This computer still keeps DistrictSync settings per Windows account.")
        return 1
    # The switch is on. Whether THIS run did anything is the child's answer, never the switch's: on a computer that was
    # already set up (a re-run to change the update mode) a declined prompt still leaves the switch on.
    if outcome == "provisioned":
        print(MACHINE_SETUP_DONE)
        print(f"Automatic updates: {mode}.")
        return 0
    if outcome == "unconfirmed" and before is False:
        # The switch was provably off before and is on now, and the mode is written before the switch.
        print(MACHINE_SETUP_DONE)
        print(f"Automatic updates: {mode}.")
        return 0
    if outcome in _MACHINE_SETUP_NOT_RUN:
        print("This computer already keeps one shared DistrictSync profile; the update setting was not changed.")
    else:
        print(
            "This computer keeps one shared DistrictSync profile, but this run may not have finished; "
            "run DistrictSync.exe --diagnose to see the update setting."
        )
    return 1


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


def _today() -> date:
    """Return the real calendar date — the ONE injectable 'now' seam for the sync-window gate.

    The pure window predicate (``src/etl/sync_window.py``) takes ``today`` as a
    parameter; this seam supplies the real one to the gate below while letting a test
    inject a fixed date (patch ``src.main._today``), mirroring the school-year
    helpers' established test seam. ``date.today()`` is never called inside pure code.
    """
    return date.today()


def _paused_by_sync_window(cfg: AppConfig, today: date, logger: logging.Logger) -> bool:
    """True when an ENABLED sync window says the automatic nightly run must pause tonight.

    Pure decision over an already-loaded ``cfg`` + an injected ``today`` (the wiring
    stays thin; the window math lives in ``src/etl/sync_window.py``). Returns False
    when the window is disabled — the default — so a normal install is byte-identical.

    A MALFORMED window (hand-edited ``config.json``) must NEVER silently stop the
    nightly sync — a silent data stoppage is the worst outcome. So a bad boundary
    fails LOUD (error log) and returns False (run normally, ignore the window),
    rather than pausing forever or crashing the scheduled task red every night.
    """
    if not cfg.sync_window_enabled:
        return False
    try:
        inside = in_sync_window(today, cfg.sync_window_start, cfg.sync_window_end)
    except ValueError as exc:
        logger.error(
            "Sync window is enabled but its bounds are invalid (%s); ignoring the window and "
            "running normally. Fix the window in Setup.",
            exc,
        )
        return False
    if inside:
        return False
    resume = next_resume_date(today, cfg.sync_window_start)
    logger.info(
        "Sync paused — outside the active window (%s–%s); resumes %s",
        cfg.sync_window_start,
        cfg.sync_window_end,
        resume.isoformat(),
    )
    return True


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


def _check_for_update_after_run(args: argparse.Namespace, logger: logging.Logger) -> None:
    """The after-run trigger, for every way a run can end. Only the app's OWN scheduled, non-dry run counts."""
    if not args.dry_run:
        _check_for_update_after_scheduled_run(_resolve_source(args.source), logger)


def _check_for_update_after_scheduled_run(source: str, logger: logging.Logger) -> None:
    """Run the installed build's update check once, after a SCHEDULED run. Never raises, never changes the run.

    The broad ``except`` is deliberate and is the one place this layer swallows: the conversion has already
    finished and its exit code is decided from its own result, so an update problem is a separate axis. It is
    logged with its traceback, not hidden.
    """
    if source != "scheduled":
        return
    try:
        from src.update.check import TRIGGER_AFTER_RUN, run_update_check

        outcome = run_update_check(TRIGGER_AFTER_RUN)
        # Counts and codes only; a skip is routine (portable build, nothing configured) so it stays at debug.
        level = logging.DEBUG if outcome.status == "skipped" else logging.INFO
        logger.log(level, "update check after run: %s (%s)", outcome.status, outcome.reason)
    except Exception:
        logger.warning("update check failed; the run's result is unaffected", exc_info=True)


def _cli(argv: list[str] | None) -> int:
    args_list = sys.argv[1:] if argv is None else list(argv)

    # --elevated-apply: the ELEVATED schedule child (plan 0041 S1b) — dispatched FIRST,
    # above every preamble side effect below, and that position is a SECURITY property,
    # not tidiness: this branch runs under an elevated token, and the preamble performs
    # best-effort filesystem work in user-writable places (legacy-profile migration, log
    # sink creation, orphan sweeps) that an elevated process must not touch. The child
    # reads one DPAPI request, applies one task_com operation, writes one result — see
    # src/scheduler/elevated_apply.py for the full fail-closed ladder. Deliberately
    # ABSENT from --help: it is an IPC mode with no human caller.
    if args_list and args_list[0] == "--elevated-apply":
        from src.scheduler.elevated_apply import run_elevated_apply

        return run_elevated_apply(args_list[1:])

    # --schedule-check <token>: the schedule check (DSYNC-33) — run by a one-shot task the
    # elevated child registers under the NIGHTLY's own account, to prove Windows can start
    # DistrictSync as that account. Recognised HERE, above the preamble, for the same reasons
    # as the two modes around it: it must pin the data dir itself (the thing it is testing) and
    # report a refusal as an EXIT CODE rather than through a log sink, and it must not migrate a
    # profile, attach a console, open a log, sweep handshake files — or reach argparse, ETL,
    # delivery or the run store. It writes one marker and exits. Absent from --help: no human
    # caller.
    if args_list and args_list[0] == "--schedule-check":
        from src.scheduler.schedule_check import run_check_mode

        return run_check_mode(args_list[1:])

    # --diagnose: the read-only support report (plan 0049 D7). Recognised HERE, beside
    # --elevated-apply, and that position is the whole point: the preamble below PINS the
    # data dir and RETURNS the refusal before argparse is even built, so a normally-parsed
    # flag would be unreachable in the one state this command exists for — a machine-scoped
    # install whose shared folder the app will not use. Reaching it must not depend on the
    # profile resolving, so none of the preamble runs: no migration (a diagnostic must not
    # move an admin's data), no log sink, no orphan sweep. The console attach is the ONE
    # step it keeps, because the shipped exe is GUI-subsystem — without it every line of
    # this report would print into a `sys.stdout` of `None`. Documented in --help's epilog.
    if args_list and args_list[0] == "--diagnose":
        _attach_parent_console()
        from src.utils.diagnostics import run_diagnose

        return run_diagnose()

    # Velopack start-up (plan 0054 S2): the installed build's runtime must see the process
    # before any other work, because Velopack's own hooks (--veloapp-install/-updated/
    # -obsolete) arrive as argv and expect it to handle them and exit. Placed AFTER the three
    # dispatch-first modes on purpose (R17): the elevated child, the schedule check and
    # --diagnose never start it. A portable build (and the unzipped CI folder) skips it
    # entirely; see src/update/startup.py. Imported lazily so the portable build never
    # imports velopack.
    from src.update.startup import run_velopack_startup

    run_velopack_startup()

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

    # Resolve the data directory ONCE, here, BEFORE the log sink opens inside it
    # (plan 0049 D0): the sink's own path depends on the answer, and a machine-scope
    # refusal must be REPORTED rather than escape as a traceback. Inert with the switch
    # off — the resolved location is exactly the per-user ladder's.
    try:
        pin_data_dir()
    except MachineScopeRefused as exc:
        return _report_machine_scope_refusal(exc)

    # CLI entry path: configure the shared file-log sink now (deferred from import
    # time so importing src.main in tests never touches the real user profile).
    logger = _configure_cli_logging()
    logger.info(startup_banner())
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
            "\n"
            "Support:\n"
            "  DistrictSync --diagnose        # print a support report (no passwords)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # CI-PINNED STRING — do not reword without updating the smoke.
    # `scripts/ci_flet_pack_smoke.py` (smoke 1, run against the PACKED exe in
    # `.github/workflows/flet-pack.yml`) asserts stdout starts with the literal
    # "DistrictSync " prefix. The exit code is the primary signal and this prefix
    # the secondary one, so a reword degrades the release gate rather than
    # breaking it loudly — hence this note. Same discipline as the `=== DRY RUN`
    # banner in `src/etl/pipeline.py` (smoke 2).
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
    machine_group = parser.add_argument_group("Machine setup (Windows, machine-wide install)")
    machine_group.add_argument(
        "--machine-setup",
        action="store_true",
        help="Set this computer up for one shared DistrictSync profile (one permission prompt)",
    )
    machine_group.add_argument(
        "--updates",
        choices=list(policy.UPDATE_MODES),
        default=None,
        help=f"With --machine-setup: off, notify or on (default: {policy.DEFAULT_UPDATE_MODE})",
    )

    args = parser.parse_args(args_list)

    # Route to SFTP subcommands (mutually exclusive with the ETL pipeline)
    sftp_actions = [args.sftp_configure, args.sftp_test, args.sftp_show]
    if sum(sftp_actions) + int(args.machine_setup) > 1:
        print("Error: choose only one of --sftp-configure, --sftp-test, --sftp-show, --machine-setup.")
        return 2
    if args.updates is not None and not args.machine_setup:
        print("Error: --updates is used with --machine-setup.")
        return 2
    if args.machine_setup:
        if any(arg.split("=", 1)[0] in _NOT_WITH_MACHINE_SETUP for arg in args_list):
            print("Error: --machine-setup sets the computer up only; run a conversion or SFTP setup separately.")
            return 2
        return _machine_setup(args)
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

    # Seasonal sync-window gate (owner decision 2026-07-21). The window governs the
    # app's OWN automatic nightly run ONLY — the run that IDENTIFIES as scheduled
    # (resolved via _resolve_source, so governance follows the same source the run
    # store labels it with). A hand-run CLI, a headless district's own cron, and
    # manual Convert are explicit human/ops choices about WHEN to run and bypass the
    # window (no override flag needed). Placed here — before run_pipeline — so a
    # paused night never enters the pipeline: no ETL, no write, no delivery, and the
    # previous output dir is left untouched (no torn state). A paused night is a
    # healthy, intentional state, so it exits 0 (the scheduled task shows success)
    # and writes NO per-night run record (the paused state is derived by the UI from
    # the window config + today; the diagnostic log line is the trail).
    if _resolve_source(args.source) == "scheduled" and _paused_by_sync_window(AppConfig.load(), _today(), logger):
        return 0

    # Healthy = start-up COMPLETED, written BEFORE ETL begins. Never "the conversion succeeded": a run that
    # exits 1 over a missing GDE file is a data problem, not a bad build (plan 0054 section 6).
    from src.update.health import mark_healthy

    mark_healthy()

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
        )
    except SystemExit:
        # run_pipeline's own early exits (bad input dir / bad config) already
        # recorded a failed run; let cli() translate the code verbatim.
        _check_for_update_after_run(args, logger)
        raise
    except Exception as e:
        print(f"\nError: {e}")
        # Kept as a literal rather than importing help.SUPPORT_EMAIL: the CLI must not
        # depend on the Flet UI layer (it would pull flet into every headless run).
        # Second spelling of the same address — keep in step with that constant.
        print("Check etl_tool.log for details. Contact hello@spacesedu.com for help.")
        _check_for_update_after_run(args, logger)
        return 1

    # Update check after the app's OWN scheduled run (plan 0054 S4), here and on the two failure paths
    # above: a build whose every nightly fails (the bug the next release fixes) must still be able to take
    # that release. The run's result is decided before this and cannot be touched by it: the check never
    # raises, applies only after exit, and the code below is computed from ``result`` alone. A hand-run CLI,
    # a district's own cron and --dry-run never trigger it.
    _check_for_update_after_run(args, logger)

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
