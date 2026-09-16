"""Message vocabulary shared by the scheduler engine and the UI classifier (plan 0047).

**Import-free on purpose.** ``task_com`` must guard what it RETURNS against the markers other
consumers key on, and it may not import ``ui_flet`` to learn them; equally, the pure
``ui_flet.schedule_status`` must key on the SAME list without owning it. ONE home for the
lists — ``task_com``, ``windows``, ``src/scheduler/__init__.py`` and
``ui_flet.schedule_status`` and ``ui_flet.setup_errors`` (its defensive access-denied
fallback, since plan 0047 A2) all import from here today. ONE live consumer still hand-spells
a marker rather than importing it: ``src/scheduler/linux.py`` (its own ``"no crontab"``
check) — tracked on ``docs/claugentic-ROADMAP.md`` beside the cron path's unguarded
marker interpolation, so the "never re-spelled on one side only" property holds for the five
importers above, not universally.

Three vocabularies, three owners:

* :data:`ABSENT_TASK_MARKERS` — ``schedule_status.interpret_unregister`` turns any of these
  into the success-shaped "No schedule was registered", and Setup then persists
  ``schedule_registered = False``. Only ``task_com.HR_NOT_FOUND``'s canonical may carry one.
* :data:`ACCESS_DENIED_MARKERS` — ``src/scheduler/__init__.py``'s delete adapter retries once
  behind a UAC prompt when a failure message carries one. Only ``HR_ACCESS_DENIED``'s
  canonical may carry one.
* :data:`SECRET_SENTINEL_PREFIX` — ``windows._sanitize_child_message`` collapses any message
  carrying it, so a canonical carrying it would collapse on the elevated path ONLY.

:func:`carries_foreign_marker` is the boundary guard ``task_com._canonical_message`` applies to
both of its uncontrolled escapes (Windows' own ``excepinfo`` description and ``str(exc)``).
There is deliberately no ``owned=`` knob: the HRESULT table returns BEFORE the guard, so the
two legitimate owners never reach it. See ``docs/claugentic-INVARIANTS.md``.
"""

from __future__ import annotations

# "The task doesn't exist" phrasings across BOTH platform delete paths — an absent task on
# Unregister is the desired end state (idempotent success-shaped), not a failure. Covers the
# Windows canonical ("cannot find") and the phrasings Windows' own FormatMessage text uses
# ("does not exist" / "no such"), AND crontab's own wording ("no crontab for <user>").
ABSENT_TASK_MARKERS: tuple[str, ...] = ("cannot find", "does not exist", "no such", "no crontab")

# The delete adapter's elevated-retry predicate (src/scheduler/__init__.py).
ACCESS_DENIED_MARKERS: tuple[str, ...] = ("access is denied", "access denied")

# The internal token no admin-facing message may carry. (B105 is a false positive: the NAME
# carries the word, the value is a log/IPC token prefix and no credential is involved — the
# same false positive already annotated on windows._MSG_ACCOUNT_NEEDS_PASSWORD.)
SECRET_SENTINEL_PREFIX = "DSYNC_"  # nosec B105

_ALL_MARKERS: tuple[str, ...] = ABSENT_TASK_MARKERS + ACCESS_DENIED_MARKERS + (SECRET_SENTINEL_PREFIX,)


def carries_foreign_marker(text: str) -> bool:
    """True when ``text`` (case-insensitively) carries ANY marker a consumer keys on."""
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in _ALL_MARKERS)
