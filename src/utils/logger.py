import logging
import logging.config
import logging.handlers
from pathlib import Path

from src.utils.paths import bundle_config_dir, user_log_file

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
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    return handler


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

    if config_path.exists():
        # logging.conf supplies levels, format and the console handler only.
        logging.config.fileConfig(config_path, disable_existing_loggers=False)
        file_handler = _build_file_handler(logfile)
        # Same wiring the conf used to declare: root + the non-propagating "etl" logger.
        logging.getLogger().addHandler(file_handler)
        logging.getLogger("etl").addHandler(file_handler)
    else:
        # Fallback basic configuration — still absolute, not relative, and still
        # ROTATING (same 5 MB × 3 limits as the normal path).
        logging.basicConfig(
            level=logging.INFO,
            format=_LOG_FORMAT,
            handlers=[_build_file_handler(logfile), logging.StreamHandler()],
        )

    return logging.getLogger(name)
