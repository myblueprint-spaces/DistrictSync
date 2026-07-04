"""Humanization helpers for the Flet UI — turn machine ids into the words an admin speaks.

PURE + COUNTED (no flet import): trust-surface copy must never show a raw config id
(``sd48myedbc``) or a raw ISO timestamp to a non-technical admin. IA-2 needs only
``friendly_district_name``; IA-3 adds ``friendly_timestamp`` (the run-log verdict copy);
IA-9 grows the broader sweep (no raw paths/filenames/stack traces). Kept minimal by
design (YAGNI) — total functions, not a framework.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def friendly_district_name(sis_type: str, *, config_dir: Path | None = None) -> str:
    """Map a SIS id to its human ``district_name``; TOTAL — never raises, never crashes a view.

    - empty/whitespace ``sis_type`` → ``""`` (no district chosen → generic hero copy).
    - else load the config and return its ``district_name`` stripped IFF non-empty,
      otherwise fall back to the raw ``sis_type``.
    - any load failure (FileNotFoundError / ValueError / unexpected) → fall back to the
      raw ``sis_type`` (an admin sees ``sd48myedbc`` at worst, never a blank or a crash),
      logged at ``warning`` (mirrors ``screens/setup._district_options``).

    ``config_dir`` is a test seam passed straight through to ``loader.load_config``
    (whose own ``config_dir`` arg overrides the ``~/.districtsync`` search dirs), so
    this is unit-testable against a fixture mappings dir without a home dependency.
    """
    sis = sis_type.strip()
    if not sis:
        return ""
    try:
        from src.config.loader import load_config

        cfg = load_config(sis, config_dir)
        name = (cfg.district_name or "").strip()
        return name if name else sis
    except Exception as exc:  # noqa: BLE001 - total: any load failure falls back to the raw id
        logger.warning("friendly_district_name(%r) fell back to the raw id: %s", sis, exc)
        return sis


def friendly_timestamp(iso: str, *, now: datetime | None = None) -> str:
    """Turn a run-log ISO timestamp into a plain relative phrase an admin reads.

    TOTAL — never raises, never surfaces the raw ISO. An empty or unparseable input
    falls back to a safe generic ("recently"), so a partial/old record can never leak
    a raw string or crash a trust surface.

    - empty / whitespace / unparseable ``iso`` → ``"recently"``.
    - < 1 minute → ``"just now"``.
    - < 1 hour → ``"{n} minutes ago"`` (``"a minute ago"`` at 1).
    - < 24 hours → ``"{n} hours ago"`` (``"an hour ago"`` at 1).
    - < 2 days → ``"yesterday at H:MM AM/PM"``.
    - < 7 days → ``"{n} days ago"``.
    - else → ``"{n} weeks ago"``.

    ``now`` is the test seam (defaults to ``datetime.now()`` in the parsed value's
    naive/aware kind so the delta is well-defined).
    """
    text = (iso or "").strip()
    if not text:
        return "recently"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return "recently"

    reference = now if now is not None else datetime.now(tz=parsed.tzinfo)
    try:
        delta_seconds = (reference - parsed).total_seconds()
    except TypeError:
        # naive/aware mismatch between a caller-supplied `now` and `parsed` — total.
        return "recently"

    if delta_seconds < 0:
        # A future/clock-skewed timestamp — don't invent "-3 hours"; treat as recent.
        return "just now"

    minutes = delta_seconds / 60
    hours = delta_seconds / 3600
    days = delta_seconds / 86400

    if minutes < 1:
        return "just now"
    if hours < 1:
        n = int(minutes)
        return "a minute ago" if n == 1 else f"{n} minutes ago"
    if days < 1:
        n = int(hours)
        return "an hour ago" if n == 1 else f"{n} hours ago"
    if days < 2:
        return f"yesterday at {parsed.strftime('%-I:%M %p')}" if _supports_dash_strftime() else _plain_time(parsed)
    if days < 7:
        return f"{int(days)} days ago"
    weeks = int(days // 7)
    return "a week ago" if weeks == 1 else f"{weeks} weeks ago"


def _supports_dash_strftime() -> bool:
    """Whether ``%-I`` (no-pad hour) is honoured — glibc yes, Windows/msvcrt no."""
    try:
        return datetime(2020, 1, 3, 9, 5).strftime("%-I") == "9"
    except ValueError:
        return False


def _plain_time(dt: datetime) -> str:
    """Cross-platform "yesterday at 3:00 AM" without ``%-I`` (strips the leading zero)."""
    return f"yesterday at {dt.strftime('%I:%M %p').lstrip('0')}"
