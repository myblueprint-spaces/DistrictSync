import csv
import logging
from pathlib import Path

import pandas as pd

from src.utils.helpers import normalize_columns

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when a file exists on disk but cannot be parsed by any encoding/delimiter."""


class DataExtractor:
    """
    Responsible for loading each GDE file (CSV/TXT) into a pandas DataFrame.
    Normalizes column names (strip + lowercase) immediately after loading.
    """

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)

    def load_data(self, required_files: list[str]) -> dict[str, pd.DataFrame]:
        """
        Try to load each file with multiple encodings and delimiters.
        Returns a dict: { filename → DataFrame }.
        If loading fails, returns an empty DataFrame for that key.
        """
        data: dict[str, pd.DataFrame] = {}

        for filename in required_files:
            file_path = self.input_path / filename
            logger.info(f"Attempting to load: {file_path}")

            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                data[filename] = pd.DataFrame()
                continue

            loaded_df = None
            for encoding in ("utf-8", "latin1", "cp1252"):
                for sep in (",", "\t", None):
                    try:
                        if sep is None:
                            # Let pandas sniff the delimiter with python engine
                            df = pd.read_csv(file_path, sep=None, engine="python", encoding=encoding, on_bad_lines="warn")
                        else:
                            df = pd.read_csv(file_path, sep=sep, encoding=encoding, low_memory=False, on_bad_lines="warn")

                        logger.info(f"Loaded {filename} with encoding={encoding}, sep={'auto' if sep is None else repr(sep)}")
                        loaded_df = df
                        break
                    except (pd.errors.ParserError, UnicodeDecodeError, ValueError, csv.Error) as e:
                        # Try the next encoding/delimiter combination
                        logger.debug(f"Failed loading {filename} with encoding={encoding}, sep={repr(sep)}: {e}")
                        continue

                if loaded_df is not None:
                    break

            if loaded_df is None:
                raise ExtractionError(
                    f"File exists but could not be parsed with any encoding/delimiter: {file_path}"
                )
            else:
                # Normalize column names here
                data[filename] = normalize_columns(loaded_df)
                logger.info(f"Successfully loaded {filename}: {len(data[filename])} rows")

        return data
