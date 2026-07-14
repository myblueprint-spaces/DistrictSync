import csv
import io
import logging
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd

from src.utils.helpers import normalize_columns

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when a file exists on disk but cannot be parsed by any encoding/delimiter."""


class DataExtractor:
    """
    Responsible for loading each GDE file (CSV/TXT) into a pandas DataFrame.
    Normalizes column names (strip + lowercase) immediately after loading.

    Two public entrypoints share one bytes-based parsing core (`_load_bytes`):
    `load_data` (disk) reads each present file's bytes and dispatches to the core;
    `load_from_bytes` (in-memory, e.g. browser uploads) dispatches directly. Both
    inherit the same encoding-detection, delimiter-detection, and malformed-row
    repair behaviour.
    """

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)

    def load_data(
        self,
        required_files: list[str],
        file_headers: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Try to load each file with multiple encodings and delimiters.
        Returns a dict: { filename → DataFrame }.
        A file that is not present on disk yields an empty DataFrame for that key.
        A file that exists but cannot be parsed raises `ExtractionError`.
        """
        file_headers = file_headers or {}
        data: dict[str, pd.DataFrame] = {}

        for filename in required_files:
            file_path = self.input_path / filename
            logger.info(f"Attempting to load: {file_path}")

            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                data[filename] = pd.DataFrame()
                continue

            # Read the bytes once and dispatch to the shared bytes-core: disk and
            # in-memory uploads parse through exactly the same encoding/delimiter/
            # repair logic.
            raw = file_path.read_bytes()
            data[filename] = self._load_bytes(filename, raw, file_headers.get(filename))

        return data

    def load_from_bytes(
        self,
        sources: dict[str, bytes],
        file_headers: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Parse already-in-memory GDE files (e.g. browser uploads) through the same
        core as `load_data`. Returns a dict: { name → DataFrame }.

        Only the supplied keys are parsed — a referenced-but-not-supplied source is
        simply absent from the result (downstream code uses
        `.get(name, pd.DataFrame())`, so the absence is treated as "skip this
        entity"; this method must NOT back-fill empty frames for missing keys).
        Content that cannot be parsed raises `ExtractionError`, matching disk.
        """
        file_headers = file_headers or {}
        data: dict[str, pd.DataFrame] = {}
        for name, raw in sources.items():
            logger.info(f"Attempting to load (in-memory): {name}")
            data[name] = self._load_bytes(name, raw, file_headers.get(name))
        return data

    def _load_bytes(
        self,
        name: str,
        raw: bytes,
        explicit_names: Optional[list[str]],
    ) -> pd.DataFrame:
        """Parse one GDE file's raw bytes into a normalized DataFrame.

        Shared by `load_data` (disk) and `load_from_bytes` (uploads). Raises
        `ExtractionError` when the bytes cannot be parsed by any encoding/delimiter.
        """
        if explicit_names:
            logger.info(f"Using explicit headers for {name} ({len(explicit_names)} columns)")

        # Pick the delimiter from the (clean) header line rather than relying on
        # "first parse that doesn't raise". A free-text field containing stray
        # delimiter characters can otherwise trick the loader into accepting the
        # wrong delimiter and silently loading the whole file as one garbage column.
        sep = self._detect_delimiter(raw)
        logger.info(f"Detected delimiter for {name}: {'auto' if sep is None else repr(sep)}")

        # Pick the text encoding by inspecting the bytes rather than "first that
        # doesn't raise" — latin1 never raises, so a UTF-8 file with a few stray
        # bytes would otherwise fall through to latin1 and mojibake every accented
        # character in the file.
        encoding, encoding_errors = self._detect_encoding(raw)
        logger.info(f"Detected encoding for {name}: {encoding} (errors={encoding_errors})")

        loaded_df = self._read_with_fallback(name, raw, sep, encoding, encoding_errors, explicit_names)

        if loaded_df is None:
            raise ExtractionError(f"File exists but could not be parsed with any encoding/delimiter: {name}")

        # Normalize column names here
        normalized = normalize_columns(loaded_df)
        logger.info(f"Successfully loaded {name}: {len(normalized)} rows")
        return normalized

    @staticmethod
    def _detect_delimiter(raw: bytes) -> Optional[str]:
        """
        Choose a delimiter by counting candidates in the first physical line.

        Decoded as latin1 (which never fails and leaves ASCII delimiters intact),
        so the choice is independent of the file's real text encoding. Returns the
        most frequent of comma/tab, or None (let pandas auto-detect) when neither
        appears — e.g. a single-column file.
        """
        first_line = raw.split(b"\n", 1)[0]
        header = first_line.decode("latin1", errors="replace")
        counts = {",": header.count(","), "\t": header.count("\t")}
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] > 0 else None

    @staticmethod
    def _detect_encoding(raw: bytes) -> tuple[str, str]:
        """
        Pick a text encoding by inspecting the bytes, returning (encoding, errors).

        - Clean UTF-8 → ("utf-8", "strict").
        - UTF-8 with only a few invalid bytes (e.g. Word smart-quotes pasted into a
          free-text field) → ("utf-8", "replace"): valid accented text survives and
          only the stray bytes become the replacement character.
        - A genuinely legacy-encoded file (a large share of bytes break UTF-8) →
          cp1252 if it decodes cleanly, otherwise latin1 (which always decodes).
        """
        try:
            raw.decode("utf-8")
            return ("utf-8", "strict")
        except UnicodeDecodeError:
            pass

        # How much of the non-ASCII content actually breaks UTF-8? A tiny fraction
        # means "UTF-8 file with junk"; a large fraction means a real legacy encoding.
        bad = raw.decode("utf-8", errors="replace").count("�")
        non_ascii = sum(1 for b in raw if b >= 0x80)
        if non_ascii and bad / non_ascii < 0.5:
            return ("utf-8", "replace")

        try:
            raw.decode("cp1252")
            return ("cp1252", "strict")
        except UnicodeDecodeError:
            return ("latin1", "strict")

    @staticmethod
    def _read_with_fallback(
        name: str,
        raw: bytes,
        sep: Optional[str],
        encoding: str,
        encoding_errors: str,
        explicit_names: Optional[list[str]],
    ) -> Optional[pd.DataFrame]:
        """
        Read the file's bytes with the detected delimiter and encoding.

        The fast C engine is tried first. If it reports malformed rows (too many
        fields), the file is re-read with the python engine and a repair hook that
        merges the overflow back into the last column — MyEd BC emits the trailing
        ``Section`` column unquoted, so a comma inside it (e.g. ``6B,R-B O3``) splits
        the row into extra fields. Recovering keeps those rows instead of dropping
        them, and silences the ParserWarnings. When sep is None only the python
        engine can auto-detect.

        Every column is read as ``str`` so code-like values (school codes, phone
        numbers) keep their exact text and are never coerced to float — which would
        append a spurious ``.0`` to any column that contains blanks.
        """
        base_kwargs: dict = {
            "encoding": encoding,
            "encoding_errors": encoding_errors,
            "sep": sep,
            "dtype": str,
            # Never let pandas treat a data column as the index: a row with one extra
            # field would otherwise be silently absorbed as an index rather than
            # flagged, hiding malformed rows and shifting every column.
            "index_col": False,
        }
        if explicit_names:
            base_kwargs["header"] = None
            base_kwargs["names"] = explicit_names

        # Fast path: C engine. Capture (and thereby silence) bad-line warnings so we
        # can decide whether a recovery pass is needed rather than just dropping rows.
        if sep is not None:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                df = None
                try:
                    df = pd.read_csv(io.BytesIO(raw), engine="c", on_bad_lines="warn", low_memory=False, **base_kwargs)
                except (pd.errors.ParserError, UnicodeDecodeError, ValueError, csv.Error) as e:
                    logger.debug(f"C-engine read failed for {name}: {e}")

            if df is not None:
                bad_lines = any(issubclass(w.category, pd.errors.ParserWarning) for w in caught)
                if not bad_lines:
                    logger.info(f"Loaded {name} with encoding={encoding}, sep={repr(sep)}, engine=c")
                    return df
                expected_cols = df.shape[1]
                del df
                logger.info(
                    f"{name}: malformed rows detected (extra delimiters in an unquoted "
                    f"field); recovering by merging the overflow into the last column."
                )
                return DataExtractor._read_repaired(
                    name, raw, sep, encoding, encoding_errors, explicit_names, expected_cols
                )

        # No usable C-engine result (sep is None, or the C engine raised). Fall back
        # to the tolerant python engine, which can also auto-detect the delimiter.
        try:
            df = pd.read_csv(io.BytesIO(raw), engine="python", on_bad_lines="warn", **base_kwargs)
            logger.info(
                f"Loaded {name} with encoding={encoding}, sep={'auto' if sep is None else repr(sep)}, engine=python"
            )
            return df
        except (pd.errors.ParserError, UnicodeDecodeError, ValueError, csv.Error) as e:
            logger.debug(f"Python-engine read failed for {name}: {e}")
            return None

    @staticmethod
    def _read_repaired(
        name: str,
        raw: bytes,
        sep: str,
        encoding: str,
        encoding_errors: str,
        explicit_names: Optional[list[str]],
        expected_cols: int,
    ) -> Optional[pd.DataFrame]:
        """Re-read a file whose rows have extra unescaped delimiters in the last column.

        pandas (both engines) silently mishandles a row with one extra field — it
        either absorbs it as an index or truncates it — so neither the C warning nor
        the python ``on_bad_lines`` hook can repair it directly. Instead we re-quote
        each row at the CSV level: any row with more than ``expected_cols`` fields has
        its overflow merged back into the last column (MyEd BC emits the trailing
        ``Section`` column unquoted, e.g. ``6B,R-B O3``). The cleaned rows are written
        to an in-memory buffer that pandas then reads normally, preserving its usual
        blank→NaN handling and ``dtype=str``.

        The bytes are decoded with the detected ``(encoding, encoding_errors)`` and
        fed to ``csv.reader`` via a ``StringIO`` — equivalent to the disk path that
        opened the file with ``newline=""`` (a decoded in-memory string already
        presents universal-newline-free physical rows to ``csv.reader``).
        """
        try:
            text = raw.decode(encoding, errors=encoding_errors)
        except (LookupError, UnicodeDecodeError) as e:
            logger.debug(f"Repair-pass decode failed for {name}: {e}")
            return None

        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=sep)
        repaired = 0
        try:
            for fields in csv.reader(io.StringIO(text, newline=""), delimiter=sep):
                if len(fields) > expected_cols:
                    fields = fields[: expected_cols - 1] + [sep.join(fields[expected_cols - 1 :])]
                    repaired += 1
                writer.writerow(fields)
        except csv.Error as e:
            logger.debug(f"Repair pass failed for {name}: {e}")
            return None

        buffer.seek(0)
        read_kwargs: dict = {"sep": sep, "dtype": str, "on_bad_lines": "warn", "index_col": False}
        if explicit_names:
            read_kwargs["header"] = None
            read_kwargs["names"] = explicit_names
        try:
            df = pd.read_csv(buffer, engine="c", **read_kwargs)
        except (pd.errors.ParserError, ValueError, csv.Error) as e:
            logger.debug(f"Repaired-buffer read failed for {name}: {e}")
            return None

        logger.info(
            f"Loaded {name} with encoding={encoding}, sep={repr(sep)}, engine=c (repaired {repaired} malformed row(s))"
        )
        return df
