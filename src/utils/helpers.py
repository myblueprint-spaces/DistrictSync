"""Small cross-cutting utilities shared across layers.

Deliberately tiny: subprocess window suppression (Windows exe polish),
directory creation, the shared column-name normalization, and the log-safety
seam (:func:`describe_value_for_log` / :func:`describe_exception_for_log`).
SFTP zip-naming lives with its consumer in ``src.sftp.uploader``; ID/join-key
normalization in ``src.etl.transformers.ids``.
"""

from __future__ import annotations

import numbers
import subprocess  # nosec B404
import sys
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# Log safety — the SINGLE seam between a source cell and a log line.
#
# GDE cells are student PII (names, dates of birth, student numbers, marks) and
# the log file is the artifact partners are told to email support
# (docs/partner/troubleshooting.md), so no diagnostic may reproduce a cell's
# CONTENT. Every fail-loud diagnostic that wants to talk about an offending
# value routes through ``describe_value_for_log`` — which yields type + length
# + character-class shape, enough to diagnose a district's bad data ("10 chars,
# digits+slashes" → they export d/m/Y) with nothing identifying in it.
# ---------------------------------------------------------------------------

# Character-shape scanning is bounded: a pathological cell (a whole file slurped
# into one field) still costs O(1). The reported LENGTH is always the true one.
_MAX_SHAPE_SCAN = 512

# Separators worth naming individually — they are what distinguishes one date /
# id / name convention from another, and they carry no information about the
# value itself. Everything else non-alphanumeric collapses to "symbols".
_PUNCTUATION_CLASSES: dict[str, str] = {
    "-": "dashes",
    "/": "slashes",
    ".": "dots",
    ":": "colons",
    ",": "commas",
    "@": "at-signs",
    "_": "underscores",
    "'": "quotes",
    '"': "quotes",
}

# Fixed emission order, so a descriptor is deterministic (and assertable) rather
# than dependent on where a character happened to appear in the value.
_SHAPE_ORDER: tuple[str, ...] = (
    "letters",
    "digits",
    "spaces",
    "dashes",
    "slashes",
    "dots",
    "colons",
    "commas",
    "at-signs",
    "underscores",
    "quotes",
    "symbols",
    "non-ascii",
)

# Types whose ``str()`` is a safe thing to MEASURE (never to emit). Anything
# else — a Series, a dict, a district's row object — is described by type name
# only, so a whole record can never be stringified into a log line.
_MEASURABLE_TYPES = (str, numbers.Number, date, time, timedelta)


def describe_value_for_log(value: Any) -> str:
    """A non-identifying descriptor of *value*, safe to write to a log file.

    Emits the type, the length, and the character-class shape — e.g.
    ``str(10 chars, digits+slashes)``, ``float(missing)``, ``bytes(6 bytes)`` —
    and NEVER the content. Use this (never ``{value!r}``) whenever a fail-loud
    diagnostic needs to describe an offending source cell.

    TOTAL by contract: any input (``None`` / ``NaN`` / ``NaT`` / bytes / a
    multi-megabyte string / an object whose ``__str__`` raises) yields a string;
    it never raises, because a diagnostic must not become the failure.
    """
    try:
        return _describe_value(value)
    except Exception:  # noqa: BLE001 — total by contract: a descriptor never raises
        return "undescribable value"


def describe_exception_for_log(exc: BaseException) -> str:
    """The exception TYPE name — the message is deliberately dropped.

    Exception messages built from a source cell routinely echo it verbatim
    (``ValueError: time data '2011-04-23' does not match format '%Y-%m-%d'``),
    so pairing a message with a cell would re-open exactly the leak
    :func:`describe_value_for_log` closes. Type + the caller's own static wording
    is what crosses into the log.
    """
    return type(exc).__name__


def _describe_value(value: Any) -> str:
    """Core of :func:`describe_value_for_log` (see its contract)."""
    if value is None:
        return "None"

    type_name = type(value).__name__

    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{type_name}({len(value)} bytes)"

    if _is_missing(value):
        return f"{type_name}(missing)"

    if not isinstance(value, _MEASURABLE_TYPES):
        # Never stringify an arbitrary object — its repr can contain the whole
        # record (a pandas Series prints its values).
        return f"{type_name}(not described)"

    text = value if isinstance(value, str) else str(value)
    if not text:
        return f"{type_name}(empty)"

    unit = "char" if len(text) == 1 else "chars"
    return f"{type_name}({len(text)} {unit}, {_shape_of(text)})"


def _is_missing(value: Any) -> bool:
    """True for a scalar null (``NaN`` / ``NaT`` / ``pd.NA``); never raises.

    ``pd.isna`` returns an ARRAY for array-likes, so anything sized is treated
    as not-missing (it is described by type name instead).
    """
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if hasattr(result, "__len__"):
        return False
    return bool(result)


def _shape_of(text: str) -> str:
    """Character-class fingerprint of *text* — e.g. ``digits+slashes``.

    Scans at most :data:`_MAX_SHAPE_SCAN` characters (bounded cost); classes are
    emitted in :data:`_SHAPE_ORDER`, so the result is deterministic.
    """
    found: set[str] = set()
    for char in text[:_MAX_SHAPE_SCAN]:
        if char.isdigit():
            found.add("digits")
        elif char.isalpha():
            found.add("letters")
        elif char.isspace():
            found.add("spaces")
        else:
            found.add(_PUNCTUATION_CLASSES.get(char, "symbols"))
        if ord(char) > 127:
            found.add("non-ascii")
    return "+".join(name for name in _SHAPE_ORDER if name in found)
