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

from src.scheduler.messages import ABSENT_TASK_MARKERS as _ABSENT_DELETE_MARKERS
from src.scheduler.task_com import RESULT_BATCH_LOGON_PROBLEM, RESULT_HAS_NOT_RUN
from src.scheduler.windows import ScheduleReadback

#: The single-source sentence for "the nightly's run records are in ANOTHER account's profile"
#: (plan 0046 C / A5). TRUE BY CONSTRUCTION: ``src/history/store.py`` writes ``history.db`` into
#: ``paths.user_data_dir()`` of the account the task RUNS as, so once the task's principal is a
#: service account its records are written there and never reach this profile. The sentence makes
#: no claim about whether the run SUCCEEDED — that claim comes from Windows' own ``LastTaskResult``
#: via :func:`run_result_verdict`, which is unaffected by where the records land.
#:
#: Rendered by the Setup readout (through ``ScheduleStatus.detail``), by Home's foreign-principal
#: branch and by Run History's empty-state + stale arms — all from HERE, never re-spelled. It names
#: the account ON SCREEN only: no account name reaches a log, a run record or a message.
FOREIGN_RECORDS_NOTE = (
    "Your nightly sync runs as {account}, so its run records are saved under that account "
    "and don't appear in Run History here."
)

#: The SHARED-RECORDS sibling of :data:`FOREIGN_RECORDS_NOTE` (plan 0049 S-2a.2). On a
#: machine-scoped install the nightly and this app read and write ONE profile, so "they don't
#: appear in Run History here" is false — but only **from provisioning onward**. Provisioning
#: migrates the admin's own ``history.db``; the service account's pre-provisioning profile is
#: never touched and cannot be, so an install that ran for months under a foreign principal has
#: a real, permanent gap the district can see in its own ledger. The sentence therefore says
#: "appear here from now on" and never a flat "its records are here" — and Run History keeps its
#: gap arm alive for the older dates.
FOREIGN_RECORDS_SHARED_NOTE = (
    "Your nightly sync runs as {account}. This computer's DistrictSync settings and run history "
    "are shared, so its run records appear here from now on."
)

# Path components that mean the running exe lives in a transient location — pinning a task
# to it risks the "task fires, exe is gone, nothing recorded" blind spot (the Downloads case).
_TRANSIENT_DIR_PARTS: frozenset[str] = frozenset({"downloads", "temp", "tmp"})

# "The task doesn't exist" phrasings across BOTH platform delete paths — an absent task on
# Unregister is the desired end state (idempotent success-shaped), not a failure. The list is
# IMPORTED from src/scheduler/messages.py (plan 0047), not re-spelled here: `task_com` has to
# guard what it can RETURN against exactly these markers and cannot import `ui_flet` to learn
# them. Today's producers are `task_com._HRESULT_CANONICAL[HR_NOT_FOUND]` plus the guarded
# description pass-through on Windows, and `linux.py`'s crontab wording ("no crontab for
# <user>") on Unix — so a Linux Unregister of a missing entry classifies the same way.
# NOT closed on Unix: `linux._read_crontab_lines` interpolates RAW `crontab` output into its
# failure message, so a crontab that prints one of these phrases still reaches here unguarded —
# the cron half of the same defect the Windows guard closes (ROADMAP).
# See the marker-guard entry in `docs/claugentic-INVARIANTS.md` before adding a phrase here.


class RunResult(Enum):
    """What Windows reported for the task's LAST run (plan 0046 C / A4).

    A channel deliberately distinct from ``setup_errors``' registration-exception classifier:
    that one classifies what a register/remove call RAISED, this one classifies the task's
    run-time ``LastTaskResult``. The two never share a table (see the CHANNEL RULE beside
    ``task_com.RESULT_BATCH_LOGON_PROBLEM``).

    ``UNREADABLE`` (no value came back) and ``UNRECOGNISED`` (a value we will not guess at) are
    deliberately SEPARATE members: "we could not read a result" and "Windows reported a result we
    cannot name" are different facts, and collapsing them would let a failed probe present as a
    reported problem — or a reported problem present as a failed probe.
    """

    OK = "ok"
    NEVER_RUN = "never_run"
    FAILED = "failed"
    DELIVERY_FAILED = "delivery_failed"
    BATCH_LOGON_SUSPECTED = "batch_logon_suspected"
    UNRECOGNISED = "unrecognised"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class RunResultVerdict:
    """A classified ``LastTaskResult`` — the member plus the ONE sentence that may be shown."""

    result: RunResult
    note: str | None = None

    @property
    def reported_a_problem(self) -> bool:
        """The last run did NOT report success — true by DEFINITION of ``LastTaskResult`` for
        every non-zero code, INDEPENDENT of whether we can name the cause.

        ``NEVER_RUN`` is not a problem (a freshly-registered task has simply not fired yet) and
        neither ``UNREADABLE`` nor ``OK`` is. Only these rows may raise ``attention``, and only
        on a foreign principal — see :func:`_live_status`.
        """
        return self.result in (
            RunResult.FAILED,
            RunResult.DELIVERY_FAILED,
            RunResult.BATCH_LOGON_SUSPECTED,
            RunResult.UNRECOGNISED,
        )


def run_result_verdict(last_result: int | None) -> RunResultVerdict:
    """Classify Windows' own ``LastTaskResult`` for the nightly task (pure, TOTAL, no raise path).

    ``ScheduleReadback.last_result`` has been read since inception and consumed by NOTHING; this
    is its first consumer, and for a district on a service account it is the PRIMARY "did the
    nightly actually run?" signal — the only one unaffected by which profile the run records land
    in (A5 silences the record-gap inference exactly there).

    Honesty rules baked into the table:

    * ``2`` is deliberately NOT mapped, against an earlier draft's "bad arguments" row. Our exit 2
      is *stdin empty or mutually-exclusive flags* — structurally unreachable for a task whose args
      DistrictSync itself baked — while ``ERROR_FILE_NOT_FOUND`` is also 2. Naming it would be more
      likely wrong than right, so it falls to ``UNRECOGNISED``.
    * ``1`` and ``3`` are worded to be TRUE under both readings (our own exit code, or a bare Win32
      code). ``1`` says only "ended with an error"; ``3`` names the result as the one *DistrictSync
      uses* for a delivery failure and never claims files were built.
    * ``RESULT_BATCH_LOGON_PROBLEM`` is COMMUNITY-SOURCED. The copy states what Windows reported
      and what the code means in general, offers no in-app remedy, and routes to IT — it never
      claims a district's policy-blocked sync is fixable here.
    * An unmapped code still yields a TRUE statement without a guessed cause.
    """
    if last_result is None:
        return RunResultVerdict(result=RunResult.UNREADABLE)
    if last_result == 0:
        return RunResultVerdict(result=RunResult.OK)
    if last_result == RESULT_HAS_NOT_RUN:
        return RunResultVerdict(
            result=RunResult.NEVER_RUN,
            note="Windows reports this task hasn't run yet.",
        )
    if last_result == 1:
        return RunResultVerdict(
            result=RunResult.FAILED,
            note="Windows recorded that the last nightly run ended with an error.",
        )
    if last_result == 3:
        return RunResultVerdict(
            result=RunResult.DELIVERY_FAILED,
            note=(
                "The last nightly run ended with the result DistrictSync uses for a delivery "
                "failure — if delivery is turned on, check that this account has its own saved "
                "SFTP credential."
            ),
        )
    if last_result == RESULT_BATCH_LOGON_PROBLEM:
        return RunResultVerdict(
            result=RunResult.BATCH_LOGON_SUSPECTED,
            note=(
                "Windows reported a logon-type failure for this task. That is what Windows "
                "returns when an account has not been granted the 'Log on as a batch job' "
                "right — your IT team can confirm and grant it."
            ),
        )
    return RunResultVerdict(
        result=RunResult.UNRECOGNISED,
        note="Windows recorded a problem with the last nightly run.",
    )


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
            fired-but-no-record contradiction) OR (LIVE on a FOREIGN principal whose Windows
            ``LastTaskResult`` reported a problem — the swapped-in signal, plan 0046 C). A clean
            LIVE, an unexpected MISSING, and every UNKNOWN are NOT attention (never nag, never
            assert).
        foreign_account: see the field docstring below.
    """

    state: ScheduleState
    headline: str
    detail: str
    expected: bool = False
    contradiction: bool = False
    next_run_display: str | None = None
    attention: bool = False
    foreign_account: str = ""
    """The RECORDED principal when it is not the signed-in account; ``""`` otherwise (plan 0046 C).

    Non-blank means, and ONLY means: at a confirmed registration this app WROTE that account name
    to ``AppConfig.schedule_run_as_user``, and ``setup_gates.principal_key`` reduces it to a
    different identity than the account now running. It is the sole authority for "a missing run
    record is EXPECTED here" — never inferred from an empty store, never from the config hint flag,
    and never read back off the live task (``task_com.TaskFacts`` carries no principal at all).

    ONE fact, ONE carrier: ``_is_contradiction`` reads it directly and ``home_status._is_missed_run``
    reads it off the ``ScheduleStatus`` it already receives, so the two predicates can never
    disagree and exactly one call path — the seam that already does the I/O — can get it wrong.

    Named for the fact that is CHECKED, not for the inference drawn from it: "records land
    elsewhere" is a consequence a machine-wide ``DISTRICTSYNC_DATA_DIR`` override could falsify.

    The dataclass default is ``""`` (do NOT suppress) because that is the conservative value; the
    INPUT on :func:`derive_schedule_status` is REQUIRED keyword-only, so no call site may omit it
    by accident.
    """

    shared_records: bool = False
    """Whether this install reads and writes the SHARED machine-scoped profile (plan 0049 S-2a.1).

    ``paths.is_machine_scope()``, resolved at the view seam that already does the I/O and carried
    here for exactly the reason ``foreign_account`` is: ONE fact, ONE carrier. Every predicate that
    suppresses an alarm under a foreign principal reads BOTH off the same ``ScheduleStatus``
    (``_is_contradiction`` directly, ``home_status._is_missed_run`` /
    ``home_status._foreign_records_elsewhere`` / the two ``sync_window_paused`` derivations off the
    status they already receive), so they can never disagree about one install.

    **The rule everywhere: suppress only when ``foreign_account and not shared_records``.** Slice C
    (plan 0046) made "the records land in another profile" true by suppressing alarms; machine
    scope makes it FALSE, and this is the one fact those predicates were missing.

    The dataclass default is ``False`` — the value that keeps today's behaviour byte-identical —
    while the INPUT on :func:`derive_schedule_status` is REQUIRED keyword-only: a defaulted
    ``False`` at the seam would silently keep the now-wrong claims on exactly the installs this
    plan exists to fix.
    """


def derive_schedule_status(
    readback: ScheduleReadback,
    *,
    hint_registered: bool,
    latest_record_ts: str | None,
    foreign_account: str,
    shared_records: bool,
    surface: str = "home",
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

    ``surface`` de-circularizes the MISSING copy (finding #3): rendered ON the Setup surface
    (``"setup"``) it reads "add/re-register it **below**"; everywhere else (``"home"``,
    Run History, badge) it keeps "**in Setup**" — the fix lives on a different screen there.

    ``foreign_account`` (plan 0046 C / A5) is REQUIRED keyword-only — the conservative value is
    ``""`` but it must be SUPPLIED, never defaulted, because omitting it is exactly the mistake
    that would silently disable the suppression (or, if the default went the other way, silently
    disable the app's only "did it run?" signal). Resolved by the one impure
    ``schedule_probe.foreign_task_account``. It is threaded to ALL THREE builders — Home and Run
    History read the field regardless of state, so a MISSING/UNKNOWN status must carry it too.

    ``shared_records`` (plan 0049 S-2a.1) is REQUIRED keyword-only for the same reason and is
    threaded to all three builders too — the pure consumers read it off the status. See the field
    docstring for the one rule it serves: suppress only when ``foreign_account and not
    shared_records``.
    """
    if readback.found is True:
        return _live_status(
            readback,
            latest_record_ts=latest_record_ts,
            expected=hint_registered,
            foreign_account=foreign_account,
            shared_records=shared_records,
        )
    if readback.found is False:
        return _missing_status(
            expected=hint_registered,
            surface=surface,
            foreign_account=foreign_account,
            shared_records=shared_records,
        )
    return _unknown_status(expected=hint_registered, foreign_account=foreign_account, shared_records=shared_records)


def _live_status(
    readback: ScheduleReadback,
    *,
    latest_record_ts: str | None,
    expected: bool,
    foreign_account: str,
    shared_records: bool,
) -> ScheduleStatus:
    """Build the LIVE status — a registered task, with next-run copy + contradiction detection.

    Composition order is load-bearing (plan 0046 C):

    1. the record-gap contradiction, which a foreign principal SUPPRESSES (A5);
    2. otherwise today's detail, BYTE FOR BYTE;
    3. then, and only when non-empty, ``FOREIGN_RECORDS_NOTE`` (foreign only) followed by the
       run-result note — the foreign note first, because it explains why the ledger is silent
       before the OS result speaks about the run itself;
    4. ``attention`` iff the contradiction fired (today's rule) OR the principal is foreign AND
       Windows reported a problem. The second arm carries a DIFFERENT headline, because it rests
       on a different — and better — class of evidence than an inference from an empty ledger.

    For a same-account install NOTHING escalates that did not escalate before: the run store and
    Run History already own the "a run failed" narrative (exactly why ``_is_contradiction`` was
    written not to fire on a non-benign ``last_result`` alone), so their copy stays byte-identical
    apart from the one appended run-result sentence.

    ``shared_records`` (plan 0049 S-2a) lifts step 1's suppression and swaps step 3's sentence for
    :data:`FOREIGN_RECORDS_SHARED_NOTE`. Step 4's second arm is deliberately UNCHANGED: Windows'
    own ``LastTaskResult`` reporting a problem is evidence about the RUN, not a claim about where
    the record went, so narrowing an alarm there would be the one direction this plan must never
    move.
    """
    contradiction = _is_contradiction(
        readback, latest_record_ts, foreign_account=foreign_account, shared_records=shared_records
    )
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
            foreign_account=foreign_account,
            shared_records=shared_records,
        )

    # The next-run time comes ONLY from the OS-reported NextRunTime — never the config hint
    # (the hint-as-truth pattern this slice bans). A found task with no NextRunTime → timeless copy.
    next_display = _time_of_day(readback.next_run) if readback.next_run else None
    detail = (
        f"Your nightly schedule is registered — next run at {next_display}."
        if next_display
        else "Your nightly schedule is registered with Windows."
    )
    # A5 + A4: the swapped signal. On a foreign principal the record-based alarm is off (the gap is
    # where the record WENT), so say plainly where it went — a permanently empty Run History with
    # no explanation is itself read as evidence the sync never ran — and let Windows' own result
    # take over the alarm. Sentences are appended in fixed order and only when non-empty.
    verdict = run_result_verdict(readback.last_result)
    if foreign_account:
        note = FOREIGN_RECORDS_SHARED_NOTE if shared_records else FOREIGN_RECORDS_NOTE
        detail = f"{detail} {note.format(account=foreign_account)}"
    if verdict.note:
        detail = f"{detail} {verdict.note}"
    reported_problem = bool(foreign_account) and verdict.reported_a_problem
    return ScheduleStatus(
        state=ScheduleState.LIVE,
        headline=("Your last nightly run reported a problem" if reported_problem else "Nightly sync is scheduled"),
        detail=detail,
        expected=expected,
        contradiction=False,
        next_run_display=next_display,
        attention=reported_problem,
        foreign_account=foreign_account,
        shared_records=shared_records,
    )


def _missing_status(
    *, expected: bool, foreign_account: str, shared_records: bool, surface: str = "home"
) -> ScheduleStatus:
    """Build the MISSING status — a definitively-absent task; copy varies on expectation + surface.

    ``surface="setup"`` swaps the circular "in Setup" pointer for "below" (the fix is on THIS
    screen); every other surface keeps "in Setup" (the fix is one hop away). Finding #3.
    """
    where = "below" if surface == "setup" else "in Setup"
    if expected:
        return ScheduleStatus(
            state=ScheduleState.MISSING,
            headline="Your schedule isn't registered anymore",
            detail=(
                "Your saved nightly schedule is no longer registered with Windows — "
                f"re-register it {where} so the roster keeps flowing."
            ),
            expected=True,
            attention=True,
            foreign_account=foreign_account,
            shared_records=shared_records,
        )
    return ScheduleStatus(
        state=ScheduleState.MISSING,
        headline="No nightly schedule is registered",
        detail=f"You haven't set up a nightly schedule yet — add one {where} whenever you're ready.",
        expected=False,
        attention=False,
        foreign_account=foreign_account,
        shared_records=shared_records,
    )


def _unknown_status(*, expected: bool, foreign_account: str, shared_records: bool) -> ScheduleStatus:
    """Build the UNKNOWN status — the query failed; NEVER assert a schedule from the hint.

    Carries ``foreign_account`` (the RECORD is readable even when the OS query failed) but the
    copy is byte-identical: this state asserts nothing about a schedule it could not see, so it
    must not assert where that schedule's records go either.
    """
    return ScheduleStatus(
        state=ScheduleState.UNKNOWN,
        headline="We couldn't confirm the schedule",
        detail="We couldn't confirm the nightly schedule right now — it may still be registered.",
        expected=expected,
        attention=False,
        foreign_account=foreign_account,
        shared_records=shared_records,
    )


def _is_contradiction(
    readback: ScheduleReadback,
    latest_record_ts: str | None,
    *,
    foreign_account: str,
    shared_records: bool,
) -> bool:
    """Whether the task fired but the store has no row for that run (the record-gap blind spot).

    The SOLE trigger is the record gap: a real prior run (``last_run`` present, so the never-run
    sentinel is excluded) whose time is strictly NEWER than the newest recorded run — the store
    captured nothing for it. This deliberately does NOT fire on a non-benign ``LastTaskResult``
    alone: an exit-3 run (roster built, SFTP failed) writes a record and is a completed
    "Built, not delivered" row in Run History — flagging it here would contradict that surface.
    This function does NOT read ``readback.last_result`` at all — it never has. A non-benign run
    result is classified independently by :func:`run_result_verdict` and surfaced in the LIVE
    detail sentence (plan 0046 C / A4); this function's only concern is the record-gap timing
    comparison. With no records to compare against, no gap can be established, so no contradiction
    is raised (a pre-store run must not false-alarm).

    ``foreign_account`` is REQUIRED keyword-only and is the A5 suppression — one-directional, and
    ONLY on positive confirmation. The asymmetry decides the direction: going quiet on an UNKNOWN
    record would silently disable the app's only "did it actually run?" signal for the districts
    NOT on a service account, invisibly and unboundedly; staying noisy on a torn record costs one
    visible amber that the next registration heals. So a missing record, a recorded ``""``, a
    case-insensitive match and a failed account resolution ALL keep alarming (see
    ``schedule_probe.foreign_task_account``, which fails to ``""`` on every one of them).

    ``shared_records`` (plan 0049 S-2a.1) is the OTHER half of that suppression and is REQUIRED
    keyword-only for the same reason: on a machine-scoped install the nightly writes its record
    into the SHARED store this reader reads, so the record gap A5 excuses is no longer expected and
    the alarm must come back on. **Suppress only when ``foreign_account and not shared_records``.**
    """
    if foreign_account and not shared_records:
        # A5: the nightly runs as another account, so its run record was written to THAT profile's
        # history.db (src/history/store.py writes under paths.user_data_dir() of the RUNNING
        # account). A gap here is the DOCUMENTED consequence of where the record was written, not
        # evidence of a fault — asserting one would walk the admin into re-registering a task that
        # is working perfectly, every night, forever. The replacement signal is run_result_verdict,
        # which reads Windows' own LastTaskResult and is unaffected by which profile records land in.
        return False
    if not readback.last_run or not latest_record_ts:
        return False
    last = _parse_dt(readback.last_run)
    newest = _parse_dt(latest_record_ts)
    return last is not None and newest is not None and last > newest


def needs_setup_badge(status: ScheduleStatus | None, *, paused: bool = False, setup_unfinished: bool = False) -> bool:
    """Whether the Setup nav destination should show a "needs attention" badge (pure).

    Driven by the single ``attention`` signal — an expected-but-missing schedule (the
    Event-141 case) or a fired-but-no-record contradiction, both of which route to Setup.
    A ``None`` status (not yet probed / not applicable) never badges.

    ``setup_unfinished`` (0038 S6 — ``nav.needs_setup``) suppresses the badge OUTRIGHT while
    the install has not reached the wizard's finish line. Home HOSTS the wizard in that
    state, so the rail's Setup item and the surface the admin is already working through are
    the same task: an attention dot on it names the work in progress as a fault. The one
    state that could otherwise raise it — a task left behind by an earlier install, firing
    with no record — is reconciled by the Schedule step a few keystrokes later ("already
    scheduled"), so nothing is lost by staying quiet until then. Defaults to ``False``, so
    every existing caller keeps its pre-S6 behaviour; the shell passes it explicitly.

    ``paused`` is the seasonal-window fact (an ENABLED window currently outside its season —
    see ``home_status.sync_window_paused``). During an intentional pause NO run is expected, so
    the fired-but-no-record **contradiction** is by design, not a fault: badging it would nag
    the admin all summer and contradict Home, which shows a calm "Paused for the summer" and
    suppresses the same contradiction. A genuinely **MISSING** task still badges even while
    paused — a gone schedule won't resume in the fall, so it is a real problem the vendor must
    fix. The two ``attention`` sources are mutually exclusive (a MISSING status is never a
    contradiction), so suppressing on ``status.contradiction`` targets exactly the summer-spurious
    case and never masks a MISSING one.
    """
    if status is None:
        return False
    if setup_unfinished:
        return False
    if paused and status.contradiction:
        return False
    return status.attention


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
