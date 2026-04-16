"""Path resolution helpers — single source of truth.

Separates read-only bundle paths (built-in mappings, logging config,
shipped docs) from user-writable data paths (logs, custom mappings,
runtime config). Works identically when running from source or from
a PyInstaller one-file bundle.

Why this exists: relative paths like ``Path("config/mappings")`` break
in the frozen exe because the launcher chdirs to ``sys._MEIPASS`` (a
temp directory that's deleted on exit) and the scheduled-task runtime
has cwd set to ``%SystemRoot%\\System32``. Both scenarios need
absolute paths resolved against the right anchor.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundle_root() -> Path:
    """Return the root of the PyInstaller bundle (or the project root in dev)."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file builds extract to sys._MEIPASS.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev layout: src/utils/paths.py -> ../../.. = project root.
    return Path(__file__).resolve().parent.parent.parent


def bundle_config_dir() -> Path:
    """Directory containing bundled read-only config (logging.conf, base mappings)."""
    return bundle_root() / "config"


def bundle_mappings_dir() -> Path:
    """Directory containing built-in mapping YAMLs shipped with the binary."""
    return bundle_config_dir() / "mappings"


def user_data_dir() -> Path:
    """Persistent per-user data directory (logs, custom mappings, app config).

    Created on first access if missing. Same dir used by AppConfig
    (`~/.districtsync/config.json`).
    """
    path = Path.home() / ".districtsync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_mappings_dir() -> Path:
    """Per-user directory for district mapping overrides and custom configs."""
    path = user_data_dir() / "mappings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_log_file() -> Path:
    """Canonical log-file path, shared by CLI, wizard, and scheduled runs."""
    return user_data_dir() / "etl_tool.log"
