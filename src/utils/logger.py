import logging
import logging.config
import logging.handlers
import sys
from pathlib import Path

from src.utils import paths
from src.utils.paths import bundle_config_dir, user_log_file
from src.utils.redact import RedactingFormatter

# Fallback rotation limits — keep in sync with config/logging.conf's
# handler_fileHandler args (5 MB per file, 3 backups). The fallback must match
# the normal path so a missing logging.conf never silently disables rotation
# (an unbounded log on a district server that runs nightly for years).
_LOG_MAX_BYTES = 5_242_880
_LOG_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _build_file_handler(path: Path) -> logging.handlers.RotatingFileHandler:
    """The rotating INFO file sink, built in code (never from eval'd config text)."""
    handler = logging.handlers.RotatingFileHandler(
        path,
        mode="a",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    # Plan 0055 N1: no account name or profile path reaches a log file, tracebacks included.
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    return handler


#: The one line a fallback writes into its own sink (DSYNC-117). It names the error TYPE and number
#: only: ``str(exc)`` quotes the path, and a per-user path carries the account name.
FALLBACK_NOTICE = (
    "The log file could not be opened (%s, errno %s); this session is logging to %s instead. DistrictSync carries on."
)
_FALLBACK_HANDSHAKE = "the per-user DistrictSync folder"
_FALLBACK_STDERR = "standard error"


def _fallback_handler() -> tuple[logging.Handler, str]:
    """The sink used when the real log file cannot be opened, and a bounded name for it.

    The per-user :func:`~src.utils.paths.handshake_dir` is the ONE declared per-user location on a
    machine-scoped install (it already holds the UAC handshake files and the refusal log), so it is
    the only place a file is tried. It is resolved through ``paths`` at call time and never through
    ``user_data_dir()``: a denied shared log must never fall through to the per-user PROFILE.
    When even that cannot be opened the line goes to stderr; a windowed exe has no stderr, and then
    nothing can carry it, which is said here rather than hidden.
    """
    try:
        folder = paths.handshake_dir()
        folder.mkdir(parents=True, exist_ok=True)
        return _build_file_handler(folder / "etl_tool.log"), _FALLBACK_HANDSHAKE
    except (OSError, RuntimeError, ValueError):
        # RuntimeError/ValueError: the profile resolver's own refusals of a bad override value.
        if sys.stderr is None:
            return logging.NullHandler(), _FALLBACK_STDERR
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.INFO)
        stream.setFormatter(RedactingFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        return stream, _FALLBACK_STDERR


def _open_file_sink(logfile: Path) -> tuple[logging.Handler, OSError | None, str]:
    """The rotating file sink for ``logfile``, or the fallback when it cannot be opened (DSYNC-117).

    Returns ``(handler, the open error or None, where the fallback writes)``. Never raises for an
    ``OSError`` (``PermissionError`` included): a log that cannot be opened must not kill the app.
    In the lab a second administrator whose grant missed ``runs/`` got PyInstaller's traceback
    dialog from exactly this open.
    """
    try:
        return _build_file_handler(logfile), None, ""
    except OSError as exc:
        handler, where = _fallback_handler()
        return handler, exc, where


def get_logger(name: str = __name__, *, log_file: Path | None = None) -> logging.Logger:
    """Configure and return a logger instance with standard formatting.

    Log output goes to a persistent, absolute path (``paths.user_log_file()``) so logs
    survive PyInstaller one-file exe restarts and are visible to Run History regardless
    of which working directory the ETL was launched from.

    ``log_file`` overrides that sink for the ONE caller that cannot use it: when
    ``paths.user_data_dir()`` REFUSES a machine-scoped profile (plan 0049 S-1a-i.4),
    ``user_log_file()`` re-raises the same refusal, so ``main._cli`` reports the failure
    into the always-resolvable per-user ``paths.handshake_dir()`` instead. The rotation
    limits stay single-sourced here either way.
    """
    config_path = bundle_config_dir() / "logging.conf"
    # The log path is NEVER handed to fileConfig: it eval()s handler ``args`` after
    # configparser interpolation, so an apostrophe or ``%`` in a profile path
    # (C:\Users\O'Brien) crashed startup and a crafted name was code execution
    # (DSYNC-10). The file handler is built in code from the Path object instead.
    logfile = log_file if log_file is not None else user_log_file()

    file_handler, open_error, fallback_where = _open_file_sink(logfile)

    if config_path.exists():
        # logging.conf supplies levels, format and the console handler only.
        logging.config.fileConfig(config_path, disable_existing_loggers=False)
        # Same wiring the conf used to declare: root + the non-propagating "etl" logger.
        logging.getLogger().addHandler(file_handler)
        logging.getLogger("etl").addHandler(file_handler)
    else:
        # Fallback basic configuration — still absolute, not relative, and still
        # ROTATING (same 5 MB × 3 limits as the normal path).
        logging.basicConfig(
            level=logging.INFO,
            format=_LOG_FORMAT,
            handlers=[file_handler, logging.StreamHandler()],
        )

    logger = logging.getLogger(name)
    if open_error is not None:
        logger.warning(FALLBACK_NOTICE, type(open_error).__name__, open_error.errno, fallback_where)
    return logger
