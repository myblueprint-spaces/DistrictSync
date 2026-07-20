"""Flexible GDE date parsing/formatting + school-year determination math.

Districts export dates in several shapes, so INPUT parsing is deliberately
flexible (one shared format grid, :data:`INPUT_DATE_FORMATS` — also the
withdraw-date grid, previously a duplicated tuple); OUTPUT shape is always
chosen by the caller. Friendly date-format tokens (``yyyy-MM-dd``) translate to
strftime here. The school-year helpers implement MyEd BC's end-year convention
("2026" = the 2025-2026 academic year) with a rollover-aware calendar fallback.

``today``/now resolution intentionally stays with the callers
(``BaseTransformer``) so the established test seam — patching
``src.etl.transformers.base.datetime`` — keeps working; every function here
takes ``today`` as an explicit parameter.
"""

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from src.etl.transformers.ids import normalize_id_series

logger = logging.getLogger(__name__)

# Recognized GDE input date formats, tried in order. The SINGLE shared grid —
# used for general date fields AND withdraw dates (previously two identical
# tuples in BaseTransformer).
INPUT_DATE_FORMATS: tuple[str, ...] = ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")

# Friendly date-format tokens -> strftime directives. Zero-padded only (Windows
# strftime has no portable non-padded directive). Longest token per letter
# family first so the alternation regex matches `yyyy` before `yy`, etc.
DATE_FORMAT_TOKENS: tuple[tuple[str, str], ...] = (
    ("yyyy", "%Y"),
    ("yy", "%y"),
    ("MMMM", "%B"),
    ("MMM", "%b"),
    ("MM", "%m"),
    ("dd", "%d"),
)
_DATE_FORMAT_TOKEN_RE = re.compile("|".join(tok for tok, _ in DATE_FORMAT_TOKENS))
_DATE_FORMAT_TOKEN_MAP: dict[str, str] = dict(DATE_FORMAT_TOKENS)


def coerce_date(value: Any) -> tuple[str, Optional[datetime]]:
    """Parse *value* against the recognized input formats.

    Returns ``(trimmed_string, parsed_datetime_or_None)``. Blank/NaN/"nan"
    inputs yield ``("", None)``; an unparseable non-blank value yields
    ``(s, None)`` so callers can pass the original through (fail-visible).
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "", None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return "", None
    for fmt in INPUT_DATE_FORMATS:
        try:
            return s, datetime.strptime(s, fmt)
        except ValueError:
            continue
    return s, None


def normalize_iso_date(value: Any) -> str:
    """Convert various date formats to ISO 8601 (yyyy-mm-dd).

    Accepts dd-MMM-yyyy (e.g. '15-Sep-2024'), already-ISO yyyy-mm-dd,
    and m/d/yyyy / d/m/yyyy. Returns the original trimmed string if
    no format matches, or '' for NaN/None/empty inputs.
    """
    s, parsed = coerce_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed is not None else s


def format_date(value: Any, strftime_format: str) -> str:
    """Reformat a flexible GDE date to *strftime_format* (a strftime string).

    Same parse grid as :func:`normalize_iso_date`; the original trimmed
    string passes through unchanged when no input format matches
    (fail-visible), and blank/NaN inputs return ''. ``strftime_format`` is
    the already-translated strftime string (see
    :func:`friendly_date_format_to_strftime`).
    """
    s, parsed = coerce_date(value)
    return parsed.strftime(strftime_format) if parsed is not None else s


def friendly_date_format_to_strftime(fmt: str) -> str:
    """Translate a friendly date format (e.g. ``yyyy-MM-dd``) to a strftime string.

    Supported tokens: ``yyyy`` ``yy`` ``MMMM`` ``MMM`` ``MM`` ``dd`` plus
    literal separators. Fails loud (``ValueError``) on any unrecognized
    alphabetic token (e.g. a lowercase ``mm`` typo) so a misconfigured
    district date format is caught at the boundary instead of silently
    producing a constant/garbled date.
    """
    residue = _DATE_FORMAT_TOKEN_RE.sub("", fmt)
    if any(c.isalpha() for c in residue):
        raise ValueError(
            f"Unsupported date_format {fmt!r}. Use tokens yyyy, yy, MMMM, MMM, MM, dd "
            "with separators — e.g. 'yyyy-MM-dd' or 'dd-MMM-yyyy'."
        )
    return _DATE_FORMAT_TOKEN_RE.sub(lambda m: _DATE_FORMAT_TOKEN_MAP[m.group(0)], fmt)


def derive_date_part(value: Any, strftime_fmt: str) -> str:
    """Format a flexible GDE date to *strftime_fmt*, empty on blank OR unparseable.

    Reuses :func:`coerce_date`. Unlike :func:`format_date` (which passes
    the original string through when no input format matches), this returns
    ``""`` for a blank OR unparseable value — so a derived email suffix is
    never a garbage passthrough (e.g. an unparseable admission date yields
    ``firstlast`` with NO suffix rather than ``firstlastunknown`` under
    ``sanitize``). ``strftime_fmt`` is the already-translated strftime string
    (see :func:`friendly_date_format_to_strftime`).
    """
    _s, parsed = coerce_date(value)
    return parsed.strftime(strftime_fmt) if parsed is not None else ""


def classify_withdraw(value: Any, today: date) -> tuple[bool, bool]:
    """Classify a withdraw-date cell as ``(is_withdrawn, was_unparseable)``.

    - Blank / NaN → ``(False, False)`` (no withdrawal).
    - Parses to a date on/before ``today`` → ``(True, False)``.
    - Parses to a future date → ``(False, False)`` (still enrolled).
    - Non-blank but unparseable → ``(True, True)`` (fail-safe to Inactive;
      the caller aggregates these into one warning).
    """
    if pd.isna(value) or str(value).strip() == "":
        return False, False
    date_str = str(value).strip()
    for fmt in INPUT_DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date() <= today, False
        except ValueError:
            continue
    return True, True


def past_withdraw_date(value: Any, today: date) -> bool:
    """True when ``value`` is a past/unparseable withdraw date.

    Thin per-value predicate over :func:`classify_withdraw` (a blank or
    future date is not a withdrawal).
    """
    return classify_withdraw(value, today)[0]


def parse_school_year_to_end(raw: str, naming: str = "end") -> Optional[int]:
    """Parse a 'school year' cell value to the academic-period END year.

    - ``YYYY/YYYY`` or ``YYYY-YYYY`` → second year (range is unambiguous;
      ``naming`` is ignored)
    - ``YYYY`` with ``naming="end"`` → year as-is
    - ``YYYY`` with ``naming="start"`` → ``year + 1``
    - anything else → None
    """
    raw = raw.strip()
    parts = re.split(r"[/-]", raw)
    if len(parts) == 2 and all(p.isdigit() and len(p) == 4 for p in parts):
        return int(parts[1])
    if raw.isdigit() and len(raw) == 4:
        year = int(raw)
        return year + 1 if naming == "start" else year
    return None


def fallback_school_year(today: date, rollover_month_day: str) -> int:
    """End-year fallback when no 'school year' source column is found.

    Returns ``today.year`` before the rollover (still in current academic
    year ending this calendar year) and ``today.year + 1`` from the
    rollover onwards (next academic year about to start, ending next
    calendar year).
    """
    try:
        month, day = map(int, rollover_month_day.split("-"))
        rollover = date(today.year, month, day)
    except (ValueError, TypeError):
        logger.warning(f"Invalid academic_year_rollover_month_day '{rollover_month_day}'; using 08-01 cutoff.")
        rollover = date(today.year, 8, 1)
    return today.year if today < rollover else today.year + 1


def determine_school_year(
    all_data: dict[str, pd.DataFrame],
    normalized_sources: dict[str, str],
    rollover_month_day: str,
    today: date,
    school_year_naming: str = "end",
) -> int:
    """Return the academic year's END year (MyEd BC "School Year" convention).

    Pure determination over ALREADY-normalized ``{role: filename}`` sources
    and a concrete ``today`` (callers resolve now(); see the module docstring).
    All configured sources are scanned; the FIRST parsed value is used
    (behavior-preserving), but when the sources disagree — a mixed-vintage
    input set that would silently produce wrong academic dates and Class
    IDs — one loud WARNING names every end year found and which was chosen.
    Falls back to the rollover-aware calendar heuristic when no source has a
    recognised value.
    """
    found_years: list[int] = []
    for _role, filename in normalized_sources.items():
        df = all_data.get(filename)
        if df is not None and "school year" in df.columns:
            for raw in normalize_id_series(df["school year"].dropna()).unique():
                parsed = parse_school_year_to_end(str(raw), school_year_naming)
                if parsed is not None and parsed not in found_years:
                    found_years.append(parsed)

    if found_years:
        chosen = found_years[0]
        if len(found_years) > 1:
            logger.warning(
                f"School-year sources disagree: found end years {found_years}; using {chosen}. "
                "Academic dates and Class IDs derive from this value — check that every "
                "GDE file comes from the same export period."
            )
        return chosen

    return fallback_school_year(today, rollover_month_day)
