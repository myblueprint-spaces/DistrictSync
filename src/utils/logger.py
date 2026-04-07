import logging
import logging.config
from pathlib import Path


def get_logger(name: str = __name__) -> logging.Logger:
    """
    Configure and return a logger instance with standard formatting
    """
    config_path = Path(__file__).parent.parent / "config" / "logging.conf"

    if config_path.exists():
        logging.config.fileConfig(config_path)
    else:
        # Fallback basic configuration
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("etl_tool.log"),
                logging.StreamHandler()
            ]
        )

    return logging.getLogger(name)
