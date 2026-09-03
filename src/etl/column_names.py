"""Canonical column-name knowledge for GDE source files.

Two things live here: the shared structural join-key constants (centralising
these string literals prevents subtle bugs from typos and makes
district-specific overrides easy to manage in one place) and
:func:`normalize_column_name` — the ONE per-name normalisation rule, shared by
the extractor's frame-level ``helpers.normalize_columns`` and the pure
pre-flight derivation in ``src.etl.preflight``.

Imports NOTHING (not even pandas), on purpose: that is what lets a pure,
frame-free module depend on the same rule the loader applies.
"""

# Student/Staff shared
SCHOOL_NUMBER = "school number"
TEACHER_NAME = "teacher name"

# Schedule / timetable
MASTER_TIMETABLE_ID = "master timetable id"

# Staff roster
STAFF_SOURCEID = "staff sourceid"

# Course
COURSE_CODE = "course code"
DISTRICT_COURSE_CODE = "district course code"
COURSE_TITLE = "title"

# Commonly-joined columns
LAST_NAME = "last name"


def normalize_column_name(name: str) -> str:
    """The ONE spelling of "what a source column name IS" in this codebase.

    Strip surrounding whitespace, lower-case: exactly what
    :func:`src.utils.helpers.normalize_columns` has always applied to every
    frame the extractor loads (``extractor._load_bytes``), promoted out of that
    DataFrame-shaped helper so a non-pandas caller can normalise a single name
    without importing pandas or holding a frame. ``normalize_columns`` now
    delegates here, so the observed-header side and the config-expectation side
    (``src.etl.preflight``) cannot drift on what one column name is.

    Semantics are deliberately byte-identical to the lambda it replaced —
    INCLUDING the ``AttributeError`` on a non-``str`` label (a frame whose
    column labels are not strings was never supported and must keep failing
    loudly rather than silently stringifying). A caller holding a possibly
    non-string value from a hand-edited YAML coerces at ITS OWN boundary
    (``normalize_column_name(str(value))``).
    """
    return name.strip().lower()
