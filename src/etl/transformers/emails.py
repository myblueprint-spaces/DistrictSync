"""Student email generation from config-driven templates.

The email ``format`` string (e.g. ``{student number}@sd40.bc.ca``) is
interpolated per row; the optional ``sanitize`` knob reduces substituted
string values to ``[a-z0-9]`` so punctuation in names never leaks into a
local part. The derived-dates machinery (injecting e.g. an admission-year
suffix) lives with its consumer, ``StudentTransformer._generate_emails``,
which composes this function with ``dates.derive_date_part``.
"""

import re
from typing import Any

import pandas as pd


def generate_student_email(row: pd.Series, format_str: str, sanitize: bool = False) -> str:
    """Interpolate row values into a lowercased email format string.

    StudentTransformer lowercases ``format_str`` before calling, so any
    template like ``{Legal Surname}.{Usual First Name}@sd54.bc.ca``
    becomes ``{legal surname}.{usual first name}@sd54.bc.ca`` — matching
    the lowercased column names. String row values are similarly
    normalised (lowercased, whitespace trimmed, internal spaces collapsed)
    so double-barrelled surnames like "Goodrick Hill" produce a
    deliverable local part ("goodrickhill"). NaN/None values become "".

    When ``sanitize`` is True (opt-in), each substituted STRING value is
    reduced to ``[a-z0-9]`` (lowercase) so apostrophes/hyphens/other
    punctuation in names (e.g. "O'Brien-Smith") never leak into the local
    part. The default path (``sanitize=False``) is unchanged and
    byte-identical for every existing district.

    Fail-loud: a template key absent from the row raises ``KeyError`` (a
    config/column mismatch must never silently blank every email).
    ``StudentTransformer._generate_emails`` is the resilient caller — it
    blanks only the failing cell and records the failure to
    ``context.data_errors``.
    """
    normalised: dict[str, Any] = {}
    for k, v in row.to_dict().items():
        key = str(k).lower()
        if pd.isna(v):
            normalised[key] = ""
        elif isinstance(v, str):
            if sanitize:
                normalised[key] = re.sub(r"[^a-z0-9]", "", v.strip().lower())
            else:
                normalised[key] = v.strip().lower().replace(" ", "")
        else:
            normalised[key] = v
    return format_str.format(**normalised)
