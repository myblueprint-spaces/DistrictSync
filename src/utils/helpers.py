"""Small cross-cutting utilities shared across layers.

Deliberately tiny: subprocess window suppression (Windows exe polish),
directory creation, and the shared column-name normalization. SFTP zip-naming
lives with its consumer in ``src.sftp.uploader``; ID/join-key normalization in
``src.etl.transformers.ids``.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pandas as pd


def subprocess_no_window_flags() -> int:
    """``creationflags`` that suppress the child console window on Windows (0 elsewhere).

    The windowed (no-console) PyInstaller exe otherwise flashes a console window for EVERY
    PowerShell / schtasks / icacls child — e.g. every schedule read-back probe fired on a
    nav click, which reads as unprofessional flicker. SINGLE SOURCE: every Windows-facing
    ``subprocess.run`` in this repo must pass ``creationflags=subprocess_no_window_flags()``.

    ``subprocess.CREATE_NO_WINDOW`` exists only on Windows Python, so it is read via
    ``getattr`` (returns 0 on POSIX, where the flag is a harmless no-op). The ``sys.platform``
    guard keeps the intent explicit and type-checks cleanly cross-platform.
    """
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def ensure_directory(path: Path) -> Path:
    """
    Create directory if it doesn't exist
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace and lowercase all column names. Returns a copy."""
    return df.rename(columns=lambda c: c.strip().lower())
