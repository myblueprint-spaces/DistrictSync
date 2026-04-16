import logging
import logging.config

from src.utils.paths import bundle_config_dir, user_log_file


def get_logger(name: str = __name__) -> logging.Logger:
    """Configure and return a logger instance with standard formatting.

    Log output goes to a persistent, absolute path
    (``~/.gde2acsv/etl_tool.log``) so logs survive PyInstaller
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
        # Fallback basic configuration — still absolute, not relative.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(logfile, encoding="utf-8"), logging.StreamHandler()],
        )

    return logging.getLogger(name)
