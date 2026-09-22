import codecs
import csv
import io
import logging
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd

from src.etl.column_names import normalize_column_name
from src.utils.helpers import normalize_columns

logger = logging.getLogger(__name__)

# A file the config gives a `headers:` block to is declared HEADERLESS: the names are
# injected positionally and `read_csv` runs with `header=None`. When such an export
# starts emitting a header row anyway, that row lands in the frame as DATA (see
# `DataExtractor._drop_echoed_header_row`). This is the fraction of DECLARED columns
# whose name the first row must echo, AT ITS OWN POSITION, before the row is read as a
# header rather than as a record. A majority is deliberately far above what any real
# record could reach by coincidence and far below a demand for an exact match — the
# export that forced this carried `Absence Code AM` where the config declares
# `Absent Code AM`, so an all-or-nothing rule would have detected nothing.
_HEADER_ECHO_MIN_RATIO = 0.5


def _compare_token(value: object) -> str:
    """Normalise ONE cell or declared name for header comparison only.

    Wraps the canonical :func:`~src.etl.column_names.normalize_column_name`
    (strip + lower) with the two tolerances a COMPARISON needs and a column NAME
    must not have: a non-``str`` cell is coerced instead of raising (a data row
    legitimately holds floats and ``NaN``), and internal whitespace is collapsed so
    a header spelled ``Absent  Code AM`` still matches. Nothing here changes what a
    column name IS — the frame's labels keep going through `normalize_columns`.
    """
    return " ".join(normalize_column_name(str(value)).split())


class ExtractionError(Exception):
    """Raised when a file exists on disk but cannot be parsed by any encoding/delimiter."""


class DataExtractor:
    """
    Responsible for loading each GDE file (CSV/TXT) into a pandas DataFrame.
    Normalizes column names (strip + lowercase) immediately after loading.

    ONE public entrypoint, `load_data`, over a bytes-based parsing core (`_load_bytes`):
    it resolves each configured filename on disk (exactly, then case-insensitively),
    reads its bytes and dispatches to the core. A second public entrypoint,
    `load_from_bytes`, existed for the retired Streamlit UI's uploads and was itself the
    Convert/CLI divergence plan 0051 deleted — Convert read the folder's own contents
    through it, so an extract no config names could fail a run. Do not reintroduce one:
    a parsing path production does not run is a path free to drift.
    """

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)
        # Lazily-built {lowercased filename: [real paths]} for case-insensitive resolution.
        # None until first needed, so an extractor that never loads from disk never lists it.
        self._ci_index: Optional[dict[str, list[Path]]] = None

    def _resolve_case_insensitively(self, filename: str) -> Optional[Path]:
        """Find ``filename`` on disk ignoring case. ``None`` when genuinely absent.

        District extracts are hand-dropped by staff and by SIS export jobs whose casing is
        not stable (``Students.txt`` vs ``students.txt`` vs ``STUDENTS.TXT``), while a
        mapping can only spell it one way. Windows resolves that itself; a case-sensitive
        filesystem does not, and neither does any string comparison in our own code — which
        is where this actually bit (a district's file read as "missing" on a Windows box
        because a picker compared names in Python).

        **An EXACT match always wins** — this is only consulted when the configured name is
        not on disk verbatim (the caller checks ``exists()`` first). So where a district has
        both ``Students.txt`` and ``students.txt`` and the mapping names one of them, that
        one is loaded and nothing is ambiguous.

        **A case COLLISION fails loudly rather than guessing.** Ambiguity is real only when
        we are choosing purely on case — the mapping names neither spelling exactly and
        several variants exist. There is then no defensible way to pick one, and choosing
        wrong ships a wrong roster, the highest-consequence failure this product has.
        """
        if self._ci_index is None:
            index: dict[str, list[Path]] = {}
            try:
                for entry in self.input_path.iterdir():
                    if entry.is_file():
                        index.setdefault(entry.name.lower(), []).append(entry)
            except OSError:
                # An unreadable/absent input dir is the caller's existing "file not found"
                # path, not a new failure mode — resolve nothing and let it report that.
                index = {}
            self._ci_index = index

        matches = self._ci_index.get(filename.lower(), [])
        if len(matches) > 1:
            names = sorted(p.name for p in matches)
            raise ExtractionError(
                f"{len(matches)} files in {self.input_path} match '{filename}' when case is ignored "
                f"({', '.join(names)}). Rename or remove all but one — loading the wrong one would "
                f"convert the wrong data."
            )
        return matches[0] if matches else None

    def load_data(
        self,
        required_files: list[str],
        file_headers: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Try to load each file with multiple encodings and delimiters.
        Returns a dict: { filename → DataFrame }.
        A file that is not present on disk yields an empty DataFrame for that key, and
        so does one that is present but carries no record at all — "nothing here" is one
        fact and gets one answer (see `_carries_no_record`). A file whose CONTENT cannot
        be parsed by any encoding/delimiter still raises `ExtractionError`.
        """
        file_headers = file_headers or {}
        data: dict[str, pd.DataFrame] = {}

        for filename in required_files:
            file_path = self.input_path / filename
            logger.info(f"Attempting to load: {file_path}")

            if not file_path.exists():
                # Case-insensitive second look before declaring it absent (see
                # `_resolve_case_insensitively`). The returned dict stays keyed by the
                # CONFIGURED name — every downstream lookup uses the mapping's spelling.
                resolved = self._resolve_case_insensitively(filename)
                if resolved is None:
                    logger.error(f"File not found: {file_path}")
                    data[filename] = pd.DataFrame()
                    continue
                logger.info(f"Matched '{filename}' on disk as '{resolved.name}' (case-insensitive).")
                file_path = resolved

            # Read the bytes once and dispatch to the parsing core, which owns the
            # encoding detection, delimiter detection and malformed-row repair.
            raw = file_path.read_bytes()
            data[filename] = self._load_bytes(filename, raw, file_headers.get(filename))

        return data

    def _load_bytes(
        self,
        name: str,
        raw: bytes,
        explicit_names: Optional[list[str]],
    ) -> pd.DataFrame:
        """Parse one GDE file's raw bytes into a normalized DataFrame.

        The parsing core `load_data` dispatches to, per resolved file. Raises
        `ExtractionError` when the bytes cannot be parsed by any encoding/delimiter —
        but an export carrying NO RECORD AT ALL is not that (see `_carries_no_record`).
        """
        if explicit_names:
            logger.info(f"Using explicit headers for {name} ({len(explicit_names)} columns)")

        # "There is nothing here" is NOT a parse failure. An ABSENT source file already
        # yields an empty frame and skips the entity; a PRESENT-but-empty one used to
        # raise and fail the whole run — the same "no records" fact answered two ways.
        # A district legitimately exports nothing (no family contacts, an attendance band
        # with no absences on a holiday), and the genuinely catastrophic case — an empty
        # DEMOGRAPHIC export — is still caught downstream by `check_delivery_integrity`,
        # which refuses to deliver a roster-less output set. So this is not a swallowed
        # error: the way-out gate still holds, and the fail-loud path below is untouched
        # for bytes that carry content nothing can read.
        if self._carries_no_record(raw):
            logger.warning(
                f"{name} is present but contains no records (it is empty apart from any "
                f"byte-order mark or line endings); loading it as zero rows. Any entity that "
                f"reads only this file will be skipped — check the export if that is unexpected."
            )
            return pd.DataFrame()

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

        # An export that has grown a header row since its `headers:` block was written
        # would otherwise deliver that row as a record (see the method's docstring).
        loaded_df = self._drop_echoed_header_row(name, loaded_df, explicit_names)

        # Normalize column names here
        normalized = normalize_columns(loaded_df)
        logger.info(f"Successfully loaded {name}: {len(normalized)} rows")
        return normalized

    @staticmethod
    def _drop_echoed_header_row(name: str, df: pd.DataFrame, explicit_names: Optional[list[str]]) -> pd.DataFrame:
        """Skip a header row in a file this district's config declares HEADERLESS.

        A `headers:` block means "this export has no header line, use these names by
        position", so the read runs with ``header=None`` and any header line the export
        DOES carry becomes data row 0 — reaching the transformers as a record. SD51's
        2026-09-17 runs failed nine times on ``no category mapping for (Absent
        Code='Absence Code Am', Authorized='Authorized Am')``: a column HEADING parsed as
        an absence code, after the district added a header to a previously headerless
        daily-absence export.

        **Detection is POSITIONAL on purpose.** A positional echo is what proves the
        export's column ORDER still matches the declared one, and that is the only
        condition under which removing the row leaves every remaining row correctly
        sourced. A header whose names are present but MOVED is a different and worse
        fault — the records below it are mis-sourced too — so it is reported and
        deliberately NOT dropped, leaving the existing fail-loud paths to stop the run
        instead of silently delivering shifted data.

        Logs counts only, never a cell value: if this detection were ever wrong, the row
        it named would be a real pupil's absence record.
        """
        if not explicit_names or df.empty:
            return df

        declared = [_compare_token(n) for n in explicit_names]
        observed = [_compare_token(v) for v in df.iloc[0].tolist()]
        width = min(len(declared), len(observed))
        positional = sum(1 for i in range(width) if declared[i] and observed[i] == declared[i])

        if positional / len(declared) >= _HEADER_ECHO_MIN_RATIO:
            logger.info(
                f"{name}: the first row echoes {positional} of {len(declared)} declared column names "
                "at their own positions, so this export now carries a header row where the "
                "configuration declares none. Reading it as a header, not as a record."
            )
            return df.iloc[1:].reset_index(drop=True)

        declared_set = {token for token in declared if token}
        loose = sum(1 for token in observed if token in declared_set)
        if loose / len(declared) >= _HEADER_ECHO_MIN_RATIO:
            logger.warning(
                f"{name}: the first row looks like a header ({loose} of {len(declared)} declared column "
                f"names appear in it) but only {positional} sit where this configuration declares them, "
                "so the export's column ORDER no longer matches. The row was NOT skipped — every row "
                "below it would be mis-read the same way. Update this district's `headers:` block to the "
                "export's real column order."
            )
        return df

    @staticmethod
    def _carries_no_record(raw: bytes) -> bool:
        """True when these bytes hold no record at all — not even a header line.

        The predicate is deliberately NARROW, and the narrowness is the safety property:
        it matches EXACTLY the byte shapes that used to raise, and nothing else. Measured
        against the real reader rather than reasoned about:

        * **Matched** (raised before): zero bytes · newlines only · a byte-order mark ·
          a BOM followed by newlines.
        * **NOT matched** (parsed before, and still do): whitespace containing SPACES,
          which the python engine reads into a junk frame; ``,,,\\n`` and ``\\t\\t\\n``,
          which yield 0-row frames with columns; and a header row, which is a real
          declaration of shape.

        The BOM is the case that matters in practice rather than in theory: PowerShell's
        ``Out-File`` and ``Export-Csv -Encoding UTF8`` write one even when there is nothing
        to export, so a predicate keyed on ``bytes.strip()`` alone would miss the most
        likely real shape of an empty district export. UTF-16 marks are included because
        `_detect_encoding` can legitimately land there.

        Widening this to "anything unreadable" would make it the swallowed error the
        fail-loud rule exists to prevent — a column/config mismatch must still stop a run.
        Empty has no mismatch to hide.
        """
        for bom in (
            codecs.BOM_UTF8,
            codecs.BOM_UTF32_LE,
            codecs.BOM_UTF32_BE,
            codecs.BOM_UTF16_LE,
            codecs.BOM_UTF16_BE,
        ):
            if raw.startswith(bom):
                raw = raw[len(bom) :]
                break
        return raw.strip(b"\r\n\x00") == b""

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
