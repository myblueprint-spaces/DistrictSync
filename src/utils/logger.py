import logging
import logging.config
import logging.handlers

from src.utils.paths import bundle_config_dir, user_log_file

# Fallback rotation limits — keep in sync with config/logging.conf's
# handler_fileHandler args (5 MB per file, 3 backups). The fallback must match
# the normal path so a missing logging.conf never silently disables rotation
# (an unbounded log on a district server that runs nightly for years).
_LOG_MAX_BYTES = 5_242_880
_LOG_BACKUP_COUNT = 3


def get_logger(name: str = __name__) -> logging.Logger:
    """Configure and return a logger instance with standard formatting.

    Log output goes to a persistent, absolute path
    (``~/.districtsync/etl_tool.log``) so logs survive PyInstaller
    one-file exe restarts and are visible to Run History regardless
    of which working directory the ETL was launched from.
    """
    config_path = bundle_config_dir() / "logging.conf"
    # Use forward slashes: logging.conf substitutes this into a Python
    # literal that gets eval()'d, and Windows backslashes trigger
    # "unicodeescape" SyntaxError (e.g. "\U" in user names). Windows
    # APIs accept forward slashes, so this is safe on all platforms.
    logfile = user_log_file().as_posix()

    if config_path.exists():
        # logging.conf references %(logfile)s which we inject here so
        # the handler writes to an absolute user-data path rather than
        # a relative one (which would land in the exe's temp dir).
        logging.config.fileConfig(config_path, defaults={"logfile": logfile}, disable_existing_loggers=False)
    else:
        # Fallback basic configuration — still absolute, not relative, and still
        # ROTATING (same 5 MB × 3 limits as logging.conf's normal path).
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.handlers.RotatingFileHandler(
                    logfile,
                    maxBytes=_LOG_MAX_BYTES,
                    backupCount=_LOG_BACKUP_COUNT,
                    encoding="utf-8",
                ),
                logging.StreamHandler(),
            ],
        )

    return logging.getLogger(name)
