"""Shared ID/join-key normalization helpers.

The GDE files carry pupil numbers, teacher ids, school numbers, and section
letters whose values may differ only in incidental whitespace or dtype
(``1001`` vs ``"1001 "``). Every cross-frame join/filter in the transformers
normalizes both sides with the same ``astype(str).str.strip()`` treatment —
defined ONCE here (DRY) so the join semantics cannot drift between call sites.
"""

import pandas as pd


def normalize_id_series(series: pd.Series) -> "pd.Series[str]":
    """Normalize an ID/join-key column: stringify and trim whitespace.

    The single shared spelling of ``astype(str).str.strip()``. NaN becomes the
    literal string ``"nan"`` (pandas ``astype(str)`` semantics) — callers that
    must drop those chain ``.str.lower()`` and compare (see
    :func:`clean_invalid_ids`). Returns a NEW series; the input is untouched.
    """
    return series.astype(str).str.strip()


def is_blank_series(series: pd.Series) -> "pd.Series[bool]":
    """Mask of BLANK values: NaN, empty/whitespace-only, or the literal ``"nan"``.

    The ONE spelling of "this cell says nothing" in this codebase. The literal
    case matters because :func:`normalize_id_series` stringifies NaN to ``"nan"``
    (pandas ``astype(str)`` semantics), so a caller comparing against ``""``
    alone would silently treat a missing cell as a real value — the trap that a
    ``row_filter`` listing ``""`` would otherwise walk into.

    Returns a NEW series; the input is untouched.
    """
    normalized = normalize_id_series(series).str.lower()
    return series.isna() | (normalized == "") | (normalized == "nan")


def clean_invalid_ids(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """Remove rows where ``id_col`` is NaN, empty, or the literal string 'nan'."""
    return df[~is_blank_series(df[id_col])]  # type: ignore[return-value]
