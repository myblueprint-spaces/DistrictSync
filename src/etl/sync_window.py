"""Pure seasonal sync-window predicate — is TODAY inside the school-year window?

The seasonal window (owner decision 2026-07-21) lets a partner run the setup
wizard ONCE and have the nightly sync run during the school year and pause over
summer, recurring every year with zero yearly touch. The chosen design puts the
window inside the APP, not the Windows schedule: the scheduled task stays a plain
daily trigger that fires every night all year, and this module answers, for a
given ``today``, whether the app should run (inside the window) or pause (outside,
over summer). Because "is today between Aug 11 and Jul 6" is true every school
year automatically, there is nothing to re-arm.

The window recurs annually, so a boundary is a MONTH-DAY (``"MM-DD"``), never a
full date. ``today`` is always an EXPLICIT parameter — mirroring the school-year
date-helper seam in ``dates.py`` (never ``date.today()`` inside pure code) — so
the caller supplies the real today and tests inject a fixed one.

Cohesion note: this is a distinct concern from ``dates.py`` (GDE date
parsing/formatting) — it is a runtime window-gating predicate — so it lives in
its own module rather than bloating the date-parsing helpers.
"""

from __future__ import annotations

from datetime import date

from src.utils.validators import validate_month_day


def _month_day_tuple(md: str) -> tuple[int, int]:
    """Parse a validated ``"MM-DD"`` boundary into a comparable ``(month, day)`` tuple.

    Reuses :func:`validate_month_day` so "is this a real calendar day?" lives in ONE
    place (the boundary validator). Raises ``ValueError`` on a malformed / nonexistent
    value, so a caller that has NOT pre-validated the window fails loud at the boundary.
    """
    normalized = validate_month_day(md)
    return int(normalized[:2]), int(normalized[3:])


def _resolve_start_date(year: int, month: int, day: int) -> date:
    """A concrete start date for ``year``, clamping ``02-29`` to Mar 1 in a non-leap year.

    Only ``02-29`` in a non-leap year can raise here (``month`` / ``day`` are already
    validated). Clamping FORWARD to Mar 1 matches :func:`in_sync_window`'s
    start-boundary rule — when Feb 29 does not exist, the window opens Mar 1.
    """
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, 3, 1)


def in_sync_window(today: date, start_md: str, end_md: str) -> bool:
    """True when ``today`` falls inside the recurring ``[start_md, end_md]`` window.

    Compared on MONTH-DAY only (year-independent), so the window recurs every year;
    both boundary days are INCLUSIVE.

    * **Wrap-around** (``start_md > end_md`` — e.g. Aug 11 -> Jul 6, spanning New
      Year): inside iff ``today >= start`` OR ``today <= end``.
    * **Non-wrapping** (``start_md < end_md`` — e.g. Feb 01 -> Jun 30): inside iff
      ``start <= today <= end``.
    * **One-day** (``start_md == end_md``): inside only on that single day (falls out
      of the non-wrapping branch's ``<=`` naturally).

    Leap day: the comparison is on ``(month, day)`` TUPLES — a ``date`` is NEVER built
    for a boundary here — so ``02-29`` can never raise. A ``02-29`` END boundary is
    effectively Feb 28 in a non-leap year (Feb 28 <= (2, 29); Mar 1 falls outside); a
    ``02-29`` START boundary is effectively Mar 1 (Feb 28 < (2, 29) falls outside; Mar 1
    lands inside). Both clamp toward the window interior — a defensible, tested rule.

    Raises ``ValueError`` (via :func:`_month_day_tuple`) if a boundary is malformed —
    the caller (the scheduled-run gate) catches it and fails loud rather than pausing.
    """
    start = _month_day_tuple(start_md)
    end = _month_day_tuple(end_md)
    today_md = (today.month, today.day)
    if start <= end:
        # Non-wrapping, including the one-day window (start == end).
        return start <= today_md <= end
    # Wrap-around window (spans the New Year).
    return today_md >= start or today_md <= end


def next_resume_date(today: date, start_md: str) -> date:
    """The next calendar date on which the window re-opens (for the "resumes <date>" copy).

    This year's ``start_md`` when ``today`` is before it; otherwise next year's. A
    ``02-29`` start clamps to Mar 1 in a non-leap resume year (matching
    :func:`in_sync_window`'s start-boundary rule).
    """
    month, day = _month_day_tuple(start_md)
    today_md = (today.month, today.day)
    year = today.year if today_md < (month, day) else today.year + 1
    return _resolve_start_date(year, month, day)
