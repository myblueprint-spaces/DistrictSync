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
