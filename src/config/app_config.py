"""Runtime application configuration (non-sensitive settings only).

Stores the partner's setup wizard choices to disk as ``config.json`` under the
per-user app-data directory (``paths.user_data_dir()`` — ``~/.districtsync`` today).
SFTP passwords are NOT stored here — they are stored in the OS credential store
via the ``keyring`` library.

The config path is resolved through ``paths.user_data_dir()`` at CALL time (not an
import-time constant) so it flows through the single app-data seam: the test
isolation fixture can redirect it, and Slice 11's relocation only touches
``paths.py`` (single source of truth for where app data lives).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from src.utils import paths

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"


def config_file_path() -> Path:
    """Resolve the ``config.json`` path at call time, through the single paths seam."""
    return paths.user_data_dir() / CONFIG_FILENAME


@dataclass
class AppConfig:
    """Partner-configured runtime settings."""

    # ETL paths
    input_dir: str = ""
    output_dir: str = ""
    sis_type: str = "myedbc"

    # Scheduling
    schedule_time: str = "03:00"  # HH:MM (24-hour)
    schedule_task_name: str = "DistrictSync_Daily"
    schedule_registered: bool = False

    # SFTP (non-sensitive only)
    sftp_enabled: bool = False
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_remote_path: str = "/files"

    @classmethod
    def load(cls) -> AppConfig:
        """Load config from disk, returning defaults if the file doesn't exist."""
        config_file = config_file_path()
        if not config_file.exists():
            return cls()
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            # Only pass known fields to avoid errors on old config files
            known = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except Exception as exc:
            logger.warning(f"Could not read app config ({exc}); using defaults")
            return cls()

    def save(self) -> None:
        """Persist config to disk (creates parent directory if needed)."""
        config_file = config_file_path()
        config_dir = config_file.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps(asdict(self), indent=2),
            encoding="utf-8",
        )
        # Restrict permissions on Unix (config contains SFTP host/username)
        if sys.platform != "win32":
            try:
                os.chmod(config_dir, 0o700)
                os.chmod(config_file, 0o600)
            except OSError:
                pass
        logger.info(f"App config saved to {config_file}")

    def is_complete(self) -> bool:
        """Return True if the minimum required settings are present."""
        if not (self.input_dir and self.output_dir and self.sis_type):
            return False
        from src.utils.validators import _SIS_TYPE_RE

        return bool(_SIS_TYPE_RE.match(self.sis_type))

    def sftp_is_configured(self) -> bool:
        """Return True if SFTP has been enabled and configured."""
        if not (self.sftp_enabled and self.sftp_host and self.sftp_username and self.sftp_remote_path):
            return False
        from src.utils.validators import ALLOWED_SFTP_HOSTS

        return self.sftp_host.strip().lower() in ALLOWED_SFTP_HOSTS
