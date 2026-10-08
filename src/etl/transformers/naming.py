"""Class-name construction helpers (shared text shaping).

Builds the "<Teacher> <Course Title> (<Section>) <Year>" display names used by
subject classes, with word-boundary truncation to the 100-char Advanced CSV
limit. Shared by ``ClassTransformer`` (via the ``BaseTransformer`` wrapper) and
``BlendedClassDetector`` (a plain service class — it imports from here rather
than inheriting the transformer base).
"""

from typing import Any, Protocol

import pandas as pd

from src.etl.column_names import COURSE_TITLE


class _HasSchoolYear(Protocol):
    """The slice of ``TransformContext`` the naming helpers read."""

    school_year: int


#: The cap every emitted class name is held to (``Classes.csv`` → ``Name``,
#: stated in ``docs/developer/output-contract.md``). Named here because
#: :func:`truncate_name` is the one place the rule is enforced, and a caller
#: that needs to BUDGET against the cap (see
#: ``BlendedClassDetector.create_name``) must read the same number rather than
#: respell it.
MAX_CLASS_NAME_LENGTH = 100


def truncate_name(name: str, max_len: int = MAX_CLASS_NAME_LENGTH) -> str:
    """Gracefully truncate a string, breaking at word boundaries.

    The ``len(result) <= max_len`` guarantee holds for ``max_len >= 10``; below
    that ``trunc_len`` goes negative and the ellipsis can make the result
    LONGER than the cap. Callers passing a computed budget must clamp — see
    ``blended._MIN_COURSE_SEGMENT_BUDGET``.
    """
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

    When the row lacks ``course_title_col`` the title falls back to
    :data:`~src.etl.column_names.COURSE_TITLE` — the CourseInformation title that
    ``ClassTransformer._merge_course_and_staff`` joins onto every subject row
    under that same structural name (plan 0053 S9 replaced the literal here).
    """
    course_title = str(row.get(course_title_col, row.get(COURSE_TITLE, "Unknown Course"))).strip()
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
