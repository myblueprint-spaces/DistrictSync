"""Pure tri-state schedule-status derivation — the single owner of schedule truth (D4).

NO ``flet`` import, NO I/O: given an injected :class:`~src.scheduler.windows.ScheduleReadback`
(the boundary layer performs the actual PowerShell read-back off-thread and feeds the result
in) plus the config hint, derive ONE typed :class:`ScheduleStatus` — a ``ScheduleState``
(LIVE / MISSING / UNKNOWN) + plain-language headline/detail + the derived next-run display +
the fired-but-no-record contradiction flag. Every schedule consumer (Home verdict, Setup
readout, Run History empty-state copy, the nav badge) reads this ONE derivation, so they can
never drift.

**The load-bearing honesty invariant (D4):** only a *definitively-queried-absent* task
(``found=False``) may claim "not scheduled" (MISSING). A query that itself failed
(``found=None`` — PowerShell missing, timeout, access denied, a non-Windows host, an
elevated-registered task unreadable by a filtered token) renders UNKNOWN — "we couldn't
confirm the schedule right now" — and NEVER falls back to asserting "scheduled" from the
config hint. This is the direct fix for the live Event-141 case (a config flag saying
"scheduled 15:36" while the task was externally deleted).

**All schedule copy lives here.** A displayed next-run time comes ONLY from the OS-reported
``NextRunTime`` in the read-back — the config ``schedule_time`` is never rendered as a verified
next-run (the hint-as-truth pattern this slice bans), so the module takes no ``hint_time`` input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.scheduler.windows import ScheduleReadback

# Path components that mean the running exe lives in a transient location — pinning a task
# to it risks the "task fires, exe is gone, nothing recorded" blind spot (the Downloads case).
_TRANSIENT_DIR_PARTS: frozenset[str] = frozenset({"downloads", "temp", "tmp"})

# schtasks/delete "the task doesn't exist" phrasings — an absent task on Unregister is the
# desired end state (idempotent success-shaped), not a failure.
_ABSENT_DELETE_MARKERS: tuple[str, ...] = ("cannot find", "does not exist", "no such")


class ScheduleState(Enum):
    """The honest tri-state of the nightly schedule (D4)."""

    LIVE = "live"  # the OS task exists (found=True) — next run known
    MISSING = "missing"  # definitively queried and absent (found=False) — "not scheduled"
    UNKNOWN = "unknown"  # the query itself failed (found=None) — "couldn't confirm right now"


@dataclass(frozen=True)
class ScheduleStatus:
    """The derived schedule truth every consumer renders (single source of copy + state).

    Attributes:
        state: LIVE / MISSING / UNKNOWN.
        headline: a short plain-language line (reads in a Home banner AND a Setup readout).
        detail: the supporting sentence — honest, category-only, never a raw path/error.
        expected: the config hint said a schedule was registered (``hint_registered``).
        contradiction: LIVE but the task fired more recently than the newest recorded run
            (fired-but-no-record — the store has no row for that run).
        next_run_display: a friendly clock time ("3:00 AM") when LIVE **and** the OS reported a
            real NextRunTime, else ``None`` — the config schedule_time is NEVER presented as a
            verified next-run (honesty invariant); MISSING/UNKNOWN never carry a time either.
        attention: this warrants a fix nudge → a Home WARNING routed to Setup + the nav
            badge. True iff (MISSING while the config expected a schedule) OR (LIVE with a
            fired-but-no-record contradiction). A clean LIVE, an unexpected MISSING, and
            every UNKNOWN are NOT attention (never nag, never assert).
    """

    state: ScheduleState
    headline: str
    detail: str
    expected: bool = False
    contradiction: bool = False
    next_run_display: str | None = None
    attention: bool = False


def derive_schedule_status(
    readback: ScheduleReadback,
    *,
    hint_registered: bool,
    latest_record_ts: str | None,
) -> ScheduleStatus:
    """Derive the tri-state ``ScheduleStatus`` from a read-back + the config hint (pure, TOTAL).

    Precedence (owned here, single-source): ``found=True`` → LIVE (with the next-run copy +
    contradiction detection); ``found=False`` → MISSING (fix routes to Setup); ``found=None``
    → UNKNOWN (never asserts "scheduled" from the hint). ``hint_registered`` shapes MISSING
    copy + the attention/badge signal (the config-vs-reality contradiction) but NEVER upgrades
    UNKNOWN to a positive claim, and the config ``schedule_time`` is deliberately NOT an input
    — a displayed next-run time comes ONLY from the OS-reported ``NextRunTime`` (never the
    hint presented as verified). ``latest_record_ts`` (the newest run record's timestamp)
    enables the "fired more recently than the newest record" contradiction branch.
    """
    if readback.found is True:
        return _live_status(readback, latest_record_ts=latest_record_ts, expected=hint_registered)
    if readback.found is False:
        return _missing_status(expected=hint_registered)
    return _unknown_status(expected=hint_registered)


def _live_status(
    readback: ScheduleReadback,
    *,
    latest_record_ts: str | None,
    expected: bool,
) -> ScheduleStatus:
    """Build the LIVE status — a registered task, with next-run copy + contradiction detection."""
    contradiction = _is_contradiction(readback, latest_record_ts)
    if contradiction:
        # HEDGED copy (honesty): the evidence is only that a run fired without a store record —
        # it does NOT establish the run failed, or that the app was moved. Name what we can see
        # (no success reported) + the actionable IF, never a flat "didn't complete"/"was moved".
        return ScheduleStatus(
            state=ScheduleState.LIVE,
            headline="Your last scheduled run reported a problem",
            detail=(
                "The last nightly run didn't report success — open Run History for details. "
                "If DistrictSync was moved or deleted from its scheduled location, re-register "
                "the schedule."
            ),
            expected=expected,
            contradiction=True,
            next_run_display=None,
            attention=True,
        )

    # The next-run time comes ONLY from the OS-reported NextRunTime — never the config hint
    # (the hint-as-truth pattern this slice bans). A found task with no NextRunTime → timeless copy.
    next_display = _time_of_day(readback.next_run) if readback.next_run else None
    detail = (
        f"Your nightly schedule is registered — next run at {next_display}."
        if next_display
        else "Your nightly schedule is registered with Windows."
    )
    return ScheduleStatus(
        state=ScheduleState.LIVE,
        headline="Nightly sync is scheduled",
        detail=detail,
        expected=expected,
        contradiction=False,
        next_run_display=next_display,
        attention=False,
    )


def _missing_status(*, expected: bool) -> ScheduleStatus:
    """Build the MISSING status — a definitively-absent task; copy varies on whether it was expected."""
    if expected:
        return ScheduleStatus(
            state=ScheduleState.MISSING,
            headline="Your schedule isn't registered anymore",
            detail=(
                "Your saved nightly schedule is no longer registered with Windows — "
                "re-register it in Setup so the roster keeps flowing."
            ),
            expected=True,
            attention=True,
        )
    return ScheduleStatus(
        state=ScheduleState.MISSING,
        headline="No nightly schedule is registered",
        detail="You haven't set up a nightly schedule yet — add one in Setup whenever you're ready.",
        expected=False,
        attention=False,
    )


def _unknown_status(*, expected: bool) -> ScheduleStatus:
    """Build the UNKNOWN status — the query failed; NEVER assert a schedule from the hint."""
    return ScheduleStatus(
        state=ScheduleState.UNKNOWN,
        headline="We couldn't confirm the schedule",
        detail="We couldn't confirm the nightly schedule right now — it may still be registered.",
        expected=expected,
        attention=False,
    )


def _is_contradiction(readback: ScheduleReadback, latest_record_ts: str | None) -> bool:
    """Whether the task fired but the store has no row for that run (the record-gap blind spot).

    The SOLE trigger is the record gap: a real prior run (``last_run`` present, so the never-run
    sentinel is excluded) whose time is strictly NEWER than the newest recorded run — the store
    captured nothing for it. This deliberately does NOT fire on a non-benign ``LastTaskResult``
    alone: an exit-3 run (roster built, SFTP failed) writes a record and is a completed
    "Built, not delivered" row in Run History — flagging it here would contradict that surface.
    A non-benign ``last_result`` is only ever supporting evidence WITHIN this record-gap case,
    never a standalone trigger. With no records to compare against, no gap can be established,
    so no contradiction is raised (a pre-store run must not false-alarm).
    """
    if not readback.last_run or not latest_record_ts:
        return False
    last = _parse_dt(readback.last_run)
    newest = _parse_dt(latest_record_ts)
    return last is not None and newest is not None and last > newest


def needs_setup_badge(status: ScheduleStatus | None) -> bool:
    """Whether the Setup nav destination should show a "needs attention" badge (pure).

    Driven by the single ``attention`` signal — an expected-but-missing schedule (the
    Event-141 case) or a fired-but-no-record contradiction, both of which route to Setup.
    A ``None`` status (not yet probed / not applicable) never badges.
    """
    return status is not None and status.attention


@dataclass(frozen=True)
class UnregisterOutcome:
    """The presentation of an Unregister attempt (idempotent — an absent task is success-shaped)."""

    success_shaped: bool
    headline: str
    detail: str


def interpret_unregister(ok: bool, message: str) -> UnregisterOutcome:
    """Map a ``delete_task`` result to a plain-language outcome (pure).

    Idempotent: a real delete OR an already-absent task ("cannot find …") both present as
    success-shaped — the desired end state (no schedule) holds either way. Only a genuine
    failure (e.g. access denied) is presented as an error, with FIXED category copy (the raw
    schtasks message is never echoed).
    """
    if ok:
        return UnregisterOutcome(
            success_shaped=True,
            headline="Schedule removed",
            detail="The nightly schedule is no longer registered with Windows.",
        )
    if any(marker in (message or "").lower() for marker in _ABSENT_DELETE_MARKERS):
        return UnregisterOutcome(
            success_shaped=True,
            headline="No schedule was registered",
            detail="There was no nightly schedule to remove — nothing changed.",
        )
    return UnregisterOutcome(
        success_shaped=False,
        headline="Couldn't remove the schedule",
        detail="We couldn't remove the nightly schedule. Try again from Setup.",
    )


def is_transient_location(exe_path: str) -> bool:
    """Whether the running exe lives in a transient dir (Downloads/Temp) — warn before pinning.

    A path-COMPONENT match (not substring) so a folder like ``temperature`` is never mistaken
    for a transient location. Total — blank input → ``False``.
    """
    text = (exe_path or "").strip()
    if not text:
        return False
    parts = [p for p in re.split(r"[\\/]+", text.lower()) if p]
    return any(part in _TRANSIENT_DIR_PARTS for part in parts)


def _time_of_day(iso: str) -> str | None:
    """The friendly clock time ("3:00 AM") from an ISO datetime; ``None`` if unparseable."""
    parsed = _parse_dt(iso)
    if parsed is None:
        return None
    return parsed.strftime("%I:%M %p").lstrip("0")


def _parse_dt(text: str | None) -> datetime | None:
    """Parse the wall-clock ``YYYY-MM-DDTHH:MM:SS`` head of an ISO string; total (``None`` on failure).

    Robust to PowerShell's ``'o'`` round-trip (7 fractional digits + optional offset) and to
    the store's naive-local ISO — both compared on wall-clock seconds, which is all the
    fired-but-no-record "newer than" check needs.
    """
    if not text:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
