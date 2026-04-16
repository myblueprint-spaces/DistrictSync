from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd


def validate_csv(file_path: Path) -> bool:
    """
    Validate CSV file existence and basic structure
    """
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    try:
        # Quick read without loading full data
        pd.read_csv(file_path, nrows=1)
        return True
    except Exception as e:
        raise ValueError(f"Invalid CSV format in {file_path}: {str(e)}") from e


def ensure_directory(path: Path) -> Path:
    """
    Create directory if it doesn't exist
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_float_conversion(value, default=0.0):
    """
    Safely convert values to float with error handling
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def validate_path(path: Path) -> bool:
    """Validate path exists and is directory"""
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")
    return True


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace and lowercase all column names. Returns a copy."""
    df = df.copy()
    df.columns = [col.strip().lower() for col in df.columns]
    return df


def district_slug(sis_type: str) -> str:
    """Short user-facing identifier for a district, derived from its sis_type.

    - sd40myedbc  -> sd40
    - sd74myedbc  -> sd74
    - myedbc      -> myedbc   (base config, keep as-is)
    - myBlueprint+ -> myBlueprint  (sanitized for filenames)
    """
    stem = sis_type
    if stem != "myedbc" and stem.endswith("myedbc"):
        stem = stem[: -len("myedbc")]
    # Sanitize for filesystem + zip filename use
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "district"


def build_zip_name(sis_type: str | None = None, for_date: date | None = None) -> str:
    """Build the canonical output zip filename.

    Pattern: ``districtsync_<district>_<YYYY-MM-DD>.zip`` when sis_type is known,
    falling back to ``districtsync_<YYYY-MM-DD>.zip`` for legacy callers that
    don't pass a district (preserves backwards compatibility with existing
    SFTP uploads that use only the date).
    """
    when = (for_date or date.today()).isoformat()
    if sis_type:
        return f"districtsync_{district_slug(sis_type)}_{when}.zip"
    return f"districtsync_{when}.zip"
