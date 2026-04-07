"""Runtime application configuration (non-sensitive settings only).

Stores the partner's setup wizard choices to disk at
``~/.gde2acsv/config.json``.  SFTP passwords are NOT stored here —
they are stored in the OS credential store via the ``keyring`` library.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

APP_CONFIG_DIR = Path.home() / ".gde2acsv"
APP_CONFIG_FILE = APP_CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    """Partner-configured runtime settings."""

    # ETL paths
    input_dir: str = ""
    output_dir: str = ""
    sis_type: str = "myedbc"

    # Scheduling
    schedule_time: str = "03:00"  # HH:MM (24-hour)
    schedule_task_name: str = "GDE2Acsv_Daily"
    schedule_registered: bool = False

    # SFTP (non-sensitive only)
    sftp_enabled: bool = False
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_remote_path: str = "/upload"

    @classmethod
    def load(cls) -> AppConfig:
        """Load config from disk, returning defaults if the file doesn't exist."""
        if not APP_CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
            # Only pass known fields to avoid errors on old config files
            known = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except Exception as exc:
            logger.warning(f"Could not read app config ({exc}); using defaults")
            return cls()

    def save(self) -> None:
        """Persist config to disk (creates parent directory if needed)."""
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        APP_CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2),
            encoding="utf-8",
        )
        # Restrict permissions on Unix (config contains SFTP host/username)
        if sys.platform != "win32":
            try:
                os.chmod(APP_CONFIG_DIR, 0o700)
                os.chmod(APP_CONFIG_FILE, 0o600)
            except OSError:
                pass
        logger.info(f"App config saved to {APP_CONFIG_FILE}")

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
