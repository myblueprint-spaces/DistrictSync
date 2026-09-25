"""The ONE source-column resolver (plan 0053 S9 — ``failure-policy.md`` §9, P10).

Every "which SOURCE column does this key read?" question in the transformers is
answered here, by :func:`resolve_source_column`, with ONE shape policy built on the
typed field-map layer that already exists (:func:`src.config.models.ensure_field_mapping`
over :func:`~src.config.models.classify_field`) — never a second raw-dict rule:

=====================================================  ======================================
The value configured for ``key``                        Resolves to
=====================================================  ======================================
a bare string (``"Pupil No"``)                          that string
a column-carrying dict (``{column: …}``, also with      its ``column``
``transform:`` or ``append_year_to_id:``)
a blank string, a blank ``column``, ``null``, or the    the documented ``default``
key absent
a shape that names NO source column for this key —      the documented ``default``
:data:`DEFAULT_COLUMN_SHAPES` (``{value: …}``, an
id/role pair, an email ``format:``)
any other shape (an academic-year date, a Name block,   the documented ``default``, with ONE
an EnrollStatus block, an unrecognised dict)            WARNING per run naming the key
=====================================================  ======================================

The answer is always passed through :func:`~src.etl.column_names.normalize_column_name`
(strip + lower-case — exactly what the extractor applies to every frame's headers), so
it can be compared with a loaded frame's columns directly.

The same resolution serves a field_map entry, a sub-key of a config-carrier entry (a
``Name`` block's ``"teacher last name"``, an id/role pair's ``staff_id_col``) and an
entity's ``source_columns`` block: each is a mapping from a key to a column spelling.

**Two log lines, each at most once per run** (:func:`reset_run_notices` opens a run —
``pipeline.run_transform`` calls it, and it serves both entry points):

* a fallback to the default is logged at DEBUG — a permissive default must say so;
* **TRANSITIONAL (one release; ROADMAP — "remove the S9 transitional column WARNING"):**
  when the answer differs from what the build before S9 read at that call site
  (:class:`Previously`), one WARNING names the key and both column names. Before S9,
  three resolvers disagreed on a bare string and the Classes/Enrollments homeroom
  path never read the Students mapping at all, so a deployed user-folder mapping on a
  district server — where no local scan reaches — may have a rename that starts taking
  effect with this build. The district's log then says so.

Every name in either line is CONFIG vocabulary (a key, a configured spelling or a
default) — never an observed header or a cell value (§8).

Pandas-free: a composed module beside ``grades.py``/``sources.py`` (P10 — new shared
behaviour goes in composed modules, not new ``BaseTransformer`` methods).
"""

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final

from src.config.models import (
    FieldAppendYear,
    FieldEmailFormat,
    FieldFixedValue,
    FieldIdRolePair,
    FieldTransform,
    ensure_field_mapping,
)
from src.etl.column_names import normalize_column_name

logger = logging.getLogger(__name__)

#: The typed shapes that name NO source column for the key they sit on, so the documented
#: default applies without a warning — an EXPLICIT allowlist, not a catch-all: a fixed
#: ``{value: …}`` (SD83 withholds Date of Birth this way; the StudentCourses placeholders),
#: an id/role pair and an email ``format:`` template. Any other shape that carries no
#: ``column`` still resolves to the default, but WARNS (see the module table).
DEFAULT_COLUMN_SHAPES: Final[tuple[type, ...]] = (FieldFixedValue, FieldIdRolePair, FieldEmailFormat)

_ABSENT: Final = object()


class Previously(StrEnum):
    """What the build BEFORE plan 0053 S9 read at a call site — TRANSITIONAL.

    Required at every :func:`resolve_source_column` call so each site states its own
    history; removed, with the WARNING it drives, the release after S9 ships (ROADMAP).

    * ``UNCHANGED`` — the site already resolved exactly as S9 does; never warns.
    * ``DEFAULT`` — the site ignored the config and read the default: a hardcoded
      literal, or the dead ``global_config["mappings"]`` path the Classes/Enrollments
      homeroom reads went through (``TransformContext.get_students_config``).
    * ``COLUMN_KEY_ONLY`` — the retired ``BaseTransformer.resolve_column``: a dict's
      ``column`` (the default when the key was missing), anything else the default —
      so a bare-string rename was IGNORED.
    * ``AS_CONFIGURED`` — the value as configured (a dict's ``column`` or a bare string),
      a blank or null one kept as NO column rather than the default.
    """

    UNCHANGED = "unchanged"
    DEFAULT = "default"
    COLUMN_KEY_ONLY = "column_key_only"
    AS_CONFIGURED = "as_configured"


# Notice keys already logged in the current run (see ``reset_run_notices``).
_run_notices: set[tuple[str, ...]] = set()


def reset_run_notices() -> None:
    """Open a new run's log window: each notice below is logged at most once per run.

    Called once per run by ``pipeline.run_transform`` (shared by the CLI and Convert),
    so a desktop session that converts twice logs each notice on both runs.
    """
    _run_notices.clear()


def _notice_once(key: tuple[str, ...]) -> bool:
    if key in _run_notices:
        return False
    _run_notices.add(key)
    return True


def _configured_spelling(raw: Any, key: str) -> str | None:
    """The column ``raw`` names for ``key`` in CONFIG spelling (trimmed), or ``None``.

    ``None`` means "no column configured here — use the documented default". The shape
    decision is ``ensure_field_mapping``'s (``classify_field``'s) — never re-derived.
    """
    # `ensure_field_mapping` answers None for a None input ONLY, so this one check covers
    # both "absent" and "null" and the `spec` below is never None.
    if raw is _ABSENT or raw is None:
        return None
    spec = ensure_field_mapping(raw)
    if isinstance(spec, str):
        return spec.strip() or None
    if isinstance(spec, (FieldTransform, FieldAppendYear)):  # the column-carrying shapes
        return spec.column.strip() or None
    if isinstance(spec, DEFAULT_COLUMN_SHAPES):
        return None
    if _notice_once(("shape", key, type(spec).__name__)):
        logger.warning(
            f"[columns] The mapping for '{key}' ({type(spec).__name__}) names no source column; "
            f"the default column is read instead. Check this key in the district mapping."
        )
    return None


def _previous_answer(raw: Any, default: str, previously: Previously) -> str | None:
    """What the pre-S9 build read here (``None`` = the site did not change). TRANSITIONAL."""
    if previously is Previously.UNCHANGED:
        return None
    if previously is Previously.DEFAULT:
        return normalize_column_name(default)
    if previously is Previously.COLUMN_KEY_ONLY:
        # The retired ``BaseTransformer.resolve_column``, verbatim.
        return str(raw.get("column", default)).lower() if isinstance(raw, dict) else default
    # AS_CONFIGURED
    if raw is _ABSENT:
        return normalize_column_name(default)
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return normalize_column_name(str(raw.get("column", default)))
    return normalize_column_name(str(raw))


def resolve_source_column(
    field_map: Mapping[str, Any],
    key: str,
    *,
    default: str,
    previously: Previously,
) -> str:
    """The normalised SOURCE column ``key`` reads, per the module's ONE shape policy.

    ``field_map`` is any key → column-spelling mapping: an entity ``field_map``, a
    config-carrier block (a ``Name`` block, an id/role pair) or a ``source_columns``
    block. ``default`` is the MyEd BC column the key reads when the config names none.
    ``previously`` is TRANSITIONAL (see :class:`Previously`).

    Raises ``TypeError`` when ``field_map`` is not a mapping (a mis-shaped config
    must not be read as "nothing configured").
    """
    if not isinstance(field_map, Mapping):
        raise TypeError(f"resolve_source_column needs a mapping for '{key}', got {type(field_map).__name__}")
    raw = field_map.get(key, _ABSENT)
    configured = _configured_spelling(raw, key)
    if configured is None:
        answer = normalize_column_name(default)
        if _notice_once(("default", key, answer)):
            logger.debug(f"[columns] No source column configured for '{key}'; reading the default '{answer}'.")
    else:
        answer = normalize_column_name(configured)

    previous = _previous_answer(raw, default, previously)
    if previous is not None and previous != answer and _notice_once(("changed", key, previous, answer)):
        logger.warning(
            f"[columns] '{key}' now reads source column '{answer}'; versions before this one read "
            f"'{previous or '(no column)'}' here. The district mapping now takes effect at this step — "
            f"check the output if that is not intended. (A temporary notice.)"
        )
    return answer


def source_column_label(field_map: Mapping[str, Any], key: str, *, default: str) -> str:
    """The same resolution in CONFIG spelling (trimmed, case kept) — for a typed error's ``columns``.

    A :class:`~src.etl.errors.SourceSchemaError` names its columns in config spelling so
    ``outcomes.safe_label`` can match them against the config's own vocabulary (S7);
    :func:`resolve_source_column`'s normalised answer could never match. Decides nothing
    about which column is read and adds no log line of its own (a shape WARNING it shares
    with :func:`resolve_source_column` is deduplicated per run) — call
    :func:`resolve_source_column` for the column at the same site.
    """
    if not isinstance(field_map, Mapping):
        raise TypeError(f"source_column_label needs a mapping for '{key}', got {type(field_map).__name__}")
    return _configured_spelling(field_map.get(key, _ABSENT), key) or default
