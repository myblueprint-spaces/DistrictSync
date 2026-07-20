"""Class-name construction helpers (shared text shaping).

Builds the "<Teacher> <Course Title> (<Section>) <Year>" display names used by
subject classes, with word-boundary truncation to the 100-char Advanced CSV
limit. Shared by ``ClassTransformer`` (via the ``BaseTransformer`` wrapper) and
``BlendedClassDetector`` (a plain service class — it imports from here rather
than inheriting the transformer base).
"""

from typing import Any, Protocol

import pandas as pd


class _HasSchoolYear(Protocol):
    """The slice of ``TransformContext`` the naming helpers read."""

    school_year: int


def truncate_name(name: str, max_len: int = 100) -> str:
    """Gracefully truncate a string, breaking at word boundaries."""
    if len(name) <= max_len:
        return name
    trunc_len = max_len - 3
    last_space = name.rfind(" ", 0, trunc_len)
    if last_space != -1:
        return name[:last_space] + "..."
    return name[:trunc_len] + "..."


def generate_class_name(
    row: pd.Series,
    teacher_flag_col: str,
    teacher_last_col: str,
    course_title_col: str,
    section_letter_col: str,
    context: _HasSchoolYear,
) -> str:
    """Build a subject-class display name from the configured source columns.

    "<Teacher last> <Course title> (<Section>) <Year>" — the teacher part is
    included only when the primary-teacher flag column (if configured and
    present) is 'y'. Truncated at a word boundary to the 100-char limit.
    """
    course_title = str(row.get(course_title_col, row.get("title", "Unknown Course"))).strip()
    teacher_last = ""

    if teacher_flag_col and teacher_flag_col in row:
        if str(row.get(teacher_flag_col, "")).strip().lower() == "y":
            teacher_last = str(row.get(teacher_last_col, "")).strip()
    else:
        teacher_last = str(row.get(teacher_last_col, "")).strip()

    if pd.isna(teacher_last) or teacher_last.lower() == "nan":
        teacher_last = ""

    section = str(row.get(section_letter_col, "")).strip()
    year = context.school_year

    parts: list[Any] = []
    if teacher_last:
        parts.append(teacher_last)
    parts.append(course_title)
    if section:
        if parts:
            parts[-1] = f"{parts[-1]} ({section})"
        else:
            parts.append(f"({section})")
    parts.append(str(year))

    return truncate_name(" ".join(parts).strip())
