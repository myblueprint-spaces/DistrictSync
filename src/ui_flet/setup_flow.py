"""Pure wizard state machine for first-run Setup (COUNTED — no flet import, no I/O).

Slice 8 (D8): first-run Setup is a five-step guided path — **District → Folders →
Delivery → Schedule → Finish** — that graduates into a flat Settings surface once the
finish line is reached. This module owns the trust-critical *decisions* of that flow so
they are unit-tested and single-sourced; the view (``screens/setup.py``) performs all
I/O (path validation, the schedule read-back, the keyring check) and feeds the results
in as **injected facts**, then renders whatever step this machine says to.

Load-bearing invariants (the honesty + no-double-register spine):

* **Resume derives from REAL injected state — there is NO stored cursor.** ``derive_flow``
  returns the first step not truthfully satisfied, so a mid-wizard abandonment reopens
  exactly where the real state (validated folders, a live task read-back, a stored
  credential) says the work actually stopped — never a persisted "you were on step 3".
* **No step flips ``setup_completed``.** This module never marks the install set-up; only
  the view's explicit finish confirmation does (``can_finish`` merely reports the finish
  step is *reachable*). So abandoning after Schedule can never read as "set up".
* **Skippable Schedule + Delivery.** The aha moment is not gated on a Windows password + a
  live SFTP credential being at hand — those two steps advance freely (skip = "set up
  later"), and skipping marks them satisfied for resume without asserting anything false.
* **Finish copy is honest and adaptive** (``finish_copy``): it names WHAT was checked and
  WHEN, never a future guarantee. Three variants — schedule skipped / delivery deferred /
  delivery tested-just-now — each phrased in the present-perfect trust register. The finish
  line also exposes a **checked configured-vs-deferred summary** (``finish_summary_rows``)
  derived from the SAME injected facts, so the calm "here's what you set up" card can never
  contradict the finish copy (no celebration — a trust instrument, not a confetti moment).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from enum import Enum

from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.utils.validators import validate_month_day, validate_run_time


class SetupStep(Enum):
    """The five concrete, named wizard steps (no data-driven step engine — YAGNI, D8)."""

    FOLDERS = "folders"
    DISTRICT = "district"
    SCHEDULE = "schedule"
    DELIVERY = "delivery"
    FINISH = "finish"


# The fixed step order (user decision 2026-07-15): DISTRICT now LEADS — "pick who you are first,
# then where your files live" — so FOLDERS follows DISTRICT. DELIVERY still precedes SCHEDULE by
# design (F1): the nightly task's ``--sftp`` flag is baked at registration from ``cfg.sftp_enabled``,
# so delivery must be committed BEFORE the Schedule step registers the task, or the natural in-order
# walk ships a task with no delivery. "Set up where it goes, then when it runs." No re-registration
# machinery / second UAC.
STEP_ORDER: tuple[SetupStep, ...] = (
    SetupStep.DISTRICT,
    SetupStep.FOLDERS,
    SetupStep.DELIVERY,
    SetupStep.SCHEDULE,
    SetupStep.FINISH,
)
# The steps that must be satisfied before the finish line is reachable (FINISH itself is the
# terminal confirmation, never "satisfied" by derivation — only by the explicit confirm).
_PRE_FINISH_STEPS: tuple[SetupStep, ...] = (
    SetupStep.DISTRICT,
    SetupStep.FOLDERS,
    SetupStep.DELIVERY,
    SetupStep.SCHEDULE,
)
TOTAL_STEPS: int = len(STEP_ORDER)

# The two steps the user may defer ("set up later") — advancing them is always allowed.
_SKIPPABLE_STEPS: frozenset[SetupStep] = frozenset({SetupStep.SCHEDULE, SetupStep.DELIVERY})


class DeliveryFact(Enum):
    """The injected outcome of the Delivery (SFTP) step (D8).

    ``TESTED_OK`` / ``TESTED_FAILED`` are a live-tested-just-now result; ``STORED_CRED_PRESENT``
    is the reconcile fact (a credential is already in the keyring from a prior session);
    ``SKIPPED`` is an explicit defer. ``NONE`` is the genuine "not addressed yet" initial state
    (beyond the spec's four injected facts — the step is simply unsatisfied until acted on).
    """

    TESTED_OK = "tested_ok"
    TESTED_FAILED = "tested_failed"
    STORED_CRED_PRESENT = "stored_cred_present"
    SKIPPED = "skipped"
    NONE = "none"


# A delivery is "satisfied" (safe to advance past / resume beyond) when it worked just now,
# a credential is already stored, or it was explicitly deferred. A failed test or an
# untouched step is NOT satisfied (the user has unfinished business there).
_DELIVERY_SATISFIED: frozenset[DeliveryFact] = frozenset(
    {DeliveryFact.TESTED_OK, DeliveryFact.STORED_CRED_PRESENT, DeliveryFact.SKIPPED}
)

# A delivery counts as CONFIGURED when a credential was tested-ok or is already stored —
# deliberately NARROWER than ``_DELIVERY_SATISFIED`` (which also counts ``SKIPPED``): a skipped
# delivery is "safe to advance" for the flow, but it configures nothing. Shared by the finish
# summary (deferred-vs-configured rows) AND the desync downgrade (``finish_needs_attention``):
# a failed/absent test is never configured.
_DELIVERY_CONFIGURED: frozenset[DeliveryFact] = frozenset({DeliveryFact.TESTED_OK, DeliveryFact.STORED_CRED_PRESENT})


@dataclass(frozen=True)
class FlowInputs:
    """The injected real-state facts ``derive_flow`` consumes (the view does all the I/O).

    Attributes:
        folders_valid: both the input and output folders validate (the boundary check).
        district_chosen: a non-blank, valid district is selected.
        schedule: the OS schedule read-back for this session (``None`` = not yet probed),
            the SAME tri-state ``ScheduleStatus`` every other surface consumes — so the
            wizard never trusts the config flag for live-ness.
        schedule_skipped: the admin chose "set up a schedule later".
        delivery: the injected ``DeliveryFact`` for the Delivery step.
        window_valid: the seasonal-window fields are safe to advance past (the view computes it
            via ``setup_gates.window_settings_valid``). ``True`` by default (the window is opt-in,
            and disabled / valid → always ``True``); an ENABLED-but-invalid window closes the
            Schedule step's Continue gate — the "Enter can't bypass a disabled button" guarantee,
            extended to the window. It does NOT affect resume/satisfaction (an invalid window is
            transient — never persisted, since the section only saves a valid one).
    """

    folders_valid: bool
    district_chosen: bool
    schedule: ScheduleStatus | None = None
    schedule_skipped: bool = False
    delivery: DeliveryFact = DeliveryFact.NONE
    window_valid: bool = True


@dataclass(frozen=True)
class FlowState:
    """The derived wizard state a view renders (single source of resume + satisfaction).

    Attributes:
        resume_step: the first step not truthfully satisfied — where a reopen lands (no cursor).
        satisfied: the frozenset of derivation-satisfied steps (FINISH is never in here).
        can_finish: every pre-finish step is satisfied → the finish line is reachable. This
            is NOT "the install is set up" — only the explicit finish confirmation sets that.
    """

    resume_step: SetupStep
    satisfied: frozenset[SetupStep]
    can_finish: bool


def _schedule_satisfied(inputs: FlowInputs) -> bool:
    """A schedule step is satisfied when explicitly skipped OR the read-back is LIVE.

    UNKNOWN / MISSING never count as satisfied (the admin still has work to do there); and
    ``None`` (not yet probed) is never satisfied — resume lands on Schedule so the step's own
    read-back can reconcile ("already scheduled — daily at HH:MM") instead of double-registering.
    """
    return inputs.schedule_skipped or (inputs.schedule is not None and inputs.schedule.state is ScheduleState.LIVE)


def _satisfied_steps(inputs: FlowInputs) -> frozenset[SetupStep]:
    """The set of derivation-satisfied pre-finish steps (FINISH is only ever confirmed)."""
    done: set[SetupStep] = set()
    if inputs.folders_valid:
        done.add(SetupStep.FOLDERS)
    if inputs.district_chosen:
        done.add(SetupStep.DISTRICT)
    if _schedule_satisfied(inputs):
        done.add(SetupStep.SCHEDULE)
    if inputs.delivery in _DELIVERY_SATISFIED:
        done.add(SetupStep.DELIVERY)
    return frozenset(done)


def derive_flow(inputs: FlowInputs) -> FlowState:
    """Derive the wizard ``FlowState`` from injected real-state facts (pure, TOTAL).

    ``resume_step`` is the first pre-finish step not satisfied; if all four are satisfied the
    resume target is FINISH (the reachable confirmation). ``can_finish`` mirrors that: the
    finish line is reachable only when Folders + District + Schedule + Delivery are all
    satisfied (skipped counts as satisfied for the two deferrable steps).
    """
    satisfied = _satisfied_steps(inputs)
    resume = SetupStep.FINISH
    for step in _PRE_FINISH_STEPS:
        if step not in satisfied:
            resume = step
            break
    can_finish = all(step in satisfied for step in _PRE_FINISH_STEPS)
    return FlowState(resume_step=resume, satisfied=satisfied, can_finish=can_finish)


def can_advance(step: SetupStep, inputs: FlowInputs) -> bool:
    """Whether the given step's Next/Enter gate is satisfied (pure — the Enter-advance gate).

    FOLDERS / DISTRICT advance only when their own value is valid (Enter can never bypass the
    gate a disabled Next button enforces — same guarantee as ``setup_gates``). SCHEDULE is
    skippable BUT additionally gated on ``window_valid`` — an enabled-but-invalid seasonal window
    blocks Continue (the window lives on the Schedule step). DELIVERY is skippable, so advancing is
    always allowed. FINISH advances (confirms) only when the finish line is reachable
    (``derive_flow(...).can_finish``).
    """
    if step is SetupStep.FOLDERS:
        return inputs.folders_valid
    if step is SetupStep.DISTRICT:
        return inputs.district_chosen
    if step is SetupStep.SCHEDULE:
        # Skippable, but a visibly-enabled invalid window can't be advanced past (the window is
        # not a task arg — this only blocks Continue, never the register flow).
        return inputs.window_valid
    if step in _SKIPPABLE_STEPS:
        return True
    return derive_flow(inputs).can_finish


def is_skippable(step: SetupStep) -> bool:
    """Whether the step offers a "set up later" defer (Schedule + Delivery only)."""
    return step in _SKIPPABLE_STEPS


def step_number(step: SetupStep) -> int:
    """The 1-based position of ``step`` (for the "Step N of 5" indicator)."""
    return STEP_ORDER.index(step) + 1


def next_step(step: SetupStep) -> SetupStep | None:
    """The step after ``step`` in the fixed order, or ``None`` at the end."""
    index = STEP_ORDER.index(step)
    return STEP_ORDER[index + 1] if index + 1 < len(STEP_ORDER) else None


def prev_step(step: SetupStep) -> SetupStep | None:
    """The step before ``step`` in the fixed order, or ``None`` at the start."""
    index = STEP_ORDER.index(step)
    return STEP_ORDER[index - 1] if index > 0 else None


def auto_selected_district(available: Sequence[str]) -> str:
    """The district to pre-select in the District step: the sole config, else nothing (D9).

    Auto-select ONLY when exactly one config exists (there is no meaningful choice to make);
    with zero or multiple configs, return ``""`` so the "Choose your district" placeholder
    shows and the admin picks explicitly — no silent alphabetical default.
    """
    return available[0] if len(available) == 1 else ""


# --------------------------------------------------------------------------- #
# Seasonal-window pre-fill defaults (B) — derived from the district calendar.  #
# --------------------------------------------------------------------------- #
# "~2 weeks before school starts" — the season opens early enough that the first nightly syncs
# land before day one. Applied to ``global_config.academic_start_month_day``.
_WINDOW_START_LEAD_DAYS = 14
# The summer gap: the season END sits ~5 weeks BEFORE it re-opens, giving a real summer pause. Keyed
# off the derived START (not ``academic_end_month_day`` — that is the DATA-year boundary "07-25",
# NOT school-end, and using it naively would overlap the start). Calendar-relative, so ANY district
# calendar yields a sensible gap. For the base 08-25 academic start this reproduces the owner's
# canonical example EXACTLY: start 08-11, end 07-06 (08-11 − 36d = 07-06).
_WINDOW_SUMMER_GAP_DAYS = 36
# Plain fallbacks when no district is chosen yet / the config is unreadable / has no academic dates.
_WINDOW_FALLBACK_START = "08-11"
_WINDOW_FALLBACK_END = "07-06"
# A NON-leap probe year so the MM-DD arithmetic is deterministic and never emits 02-29 (a 02-29
# input clamps out via the try/except below — a school year does not start on Feb 29).
_WINDOW_PROBE_YEAR = 2001


def default_window_bounds(academic_start_md: str | None, academic_end_md: str | None = None) -> tuple[str, str]:
    """Pre-fill ``(sync_window_start, sync_window_end)`` from a district's academic calendar (pure).

    ``sync_window_start`` = ``academic_start_month_day`` − 14 days ("~2 weeks before school
    starts"). ``sync_window_end`` = that start − 36 days (a ~5-week summer pause just before the
    season re-opens). ``academic_end_md`` is accepted (the view reads it alongside the start) but
    DELIBERATELY UNUSED for the end: it is the academic-DATA-year boundary ("07-25"), not
    school-end, so using it as the season end would overlap the start. The vendor tunes both per
    district — do not overclaim the pre-fill's precision.

    TOTAL: a ``None`` / blank / malformed / leap-day (02-29) start → the plain
    (``"08-11"``, ``"07-06"``) fallback, never a raise.
    """
    start_md = _shift_month_day(academic_start_md, -_WINDOW_START_LEAD_DAYS)
    if start_md is None:
        return (_WINDOW_FALLBACK_START, _WINDOW_FALLBACK_END)
    end_md = _shift_month_day(start_md, -_WINDOW_SUMMER_GAP_DAYS)
    if end_md is None:
        return (_WINDOW_FALLBACK_START, _WINDOW_FALLBACK_END)
    return (start_md, end_md)


def _shift_month_day(md: str | None, days: int) -> str | None:
    """Shift a validated ``"MM-DD"`` by ``days`` in the non-leap probe year → ``"MM-DD"`` / ``None``.

    Reuses ``validate_month_day`` (rejects garbage, accepts 02-29). ``None`` when the input is
    unusable or lands on a date the non-leap probe year can't build (02-29) — the caller degrades
    to the plain fallback rather than crash.
    """
    if not isinstance(md, str) or not md.strip():
        return None
    try:
        normalized = validate_month_day(md)
        shifted = date(_WINDOW_PROBE_YEAR, int(normalized[:2]), int(normalized[3:])) + timedelta(days=days)
    except (ValueError, TypeError):
        return None
    return f"{shifted.month:02d}-{shifted.day:02d}"


# --------------------------------------------------------------------------- #
# Settings-mode reconcile — the task-arg-change predicate (D8).                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TaskArgs:
    """The exact fields baked into the registered scheduled-task action (D8 reconcile).

    A change to ANY of these means the live task's action is stale and must be re-registered
    so tonight's run uses the new settings. The SFTP host/user/remote/port are deliberately
    NOT here — the pipeline reads those from the config at run time, so they don't change the
    task's command line and never need a re-register.
    """

    input_dir: str
    output_dir: str
    sis_type: str
    sftp_enabled: bool
    run_time: str

    @staticmethod
    def of(*, input_dir: str, output_dir: str, sis_type: str, sftp_enabled: bool, run_time: str) -> TaskArgs:
        """Build normalized ``TaskArgs`` (strings stripped) so cosmetic whitespace isn't a change."""
        return TaskArgs(
            input_dir=(input_dir or "").strip(),
            output_dir=(output_dir or "").strip(),
            sis_type=(sis_type or "").strip(),
            sftp_enabled=bool(sftp_enabled),
            run_time=(run_time or "").strip(),
        )


def task_args_changed(saved: TaskArgs, pending: TaskArgs) -> bool:
    """Whether any task-baked field changed → the live schedule needs re-registration (pure).

    Compares the five fields (input_dir, output_dir, sis_type, sftp flag, run_time). The
    Settings-mode Save uses this to drive re-registration ONLY when a change would otherwise
    leave the nightly task pointing at stale arguments.
    """
    return saved != pending


# The exact key set a persisted task-args record must carry (mirrors ``TaskArgs``' fields).
_TASK_ARGS_STR_FIELDS: tuple[str, ...] = ("input_dir", "output_dir", "sis_type", "run_time")
_TASK_ARGS_FIELDS: tuple[str, ...] = (*_TASK_ARGS_STR_FIELDS, "sftp_enabled")


def task_args_to_persisted(args: TaskArgs) -> dict[str, object]:
    """Serialize just-registered ``TaskArgs`` for the durable AppConfig record (0034 S3-d).

    Written at every CONFIRMED successful register so the Settings reconcile can compare a
    pending Save against what the live task ACTUALLY carries — surviving app restarts and
    Mapping district switches (a mount-time snapshot forgets both).
    """
    return asdict(args)


def task_args_from_persisted(raw: object) -> TaskArgs | None:
    """Rebuild the last-REGISTERED ``TaskArgs`` from its persisted form (total; 0034 S3-d).

    ``None`` means **"no usable record" — the honest UNKNOWN**, not a licence to substitute a
    guess (W3-C). DEFENSIVE rather than fail-loud by design: the record lives in the user-profile
    ``config.json`` (hand-editable, and absent on installs that registered before the field
    existed in v3.7.0), so a missing/garbled record must degrade, never crash Settings. Values are
    normalized through ``TaskArgs.of`` so a persisted record and a live snapshot compare on equal
    footing.
    """
    if not isinstance(raw, dict):
        return None
    if not all(field in raw for field in _TASK_ARGS_FIELDS):
        return None
    if not all(isinstance(raw[field], str) for field in _TASK_ARGS_STR_FIELDS):
        return None
    if not isinstance(raw["sftp_enabled"], bool):
        return None
    return TaskArgs.of(
        input_dir=raw["input_dir"],
        output_dir=raw["output_dir"],
        sis_type=raw["sis_type"],
        sftp_enabled=raw["sftp_enabled"],
        run_time=raw["run_time"],
    )


# --------------------------------------------------------------------------- #
# The durable registered-schedule record + the reconcile decision (W3-C).       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegisteredSchedule:
    """What the app durably KNOWS about the live scheduled task — ``None`` means "we can't tell".

    The two facets are written together by every confirmed register and cleared together by every
    confirmed unregister, so the record is **atomic**: either both facts are evidenced or neither
    is. That is why an absent ``args`` also makes ``unattended`` unknown — the ``False`` default of
    ``AppConfig.schedule_unattended`` is a dataclass default, not an observation.

    Attributes:
        args: the task-baked args the live task actually carries, or ``None`` when there is no
            usable record (an install that registered before the record shipped in v3.7.0, or a
            hand-edited ``config.json``).
        unattended: whether the live task runs while signed out, or ``None`` when unknown. Always
            ``False`` where the platform has no stored-password logon at all (cron): there is no
            logon type an unproven re-register could downgrade, so "unknown" would buy nothing.
    """

    args: TaskArgs | None
    unattended: bool | None


def registered_schedule(
    *,
    raw_task_args: object,
    unattended_flag: bool,
    supports_unattended: bool = True,
) -> RegisteredSchedule:
    """Resolve the durable registered-schedule record from its persisted parts (pure, TOTAL).

    The SINGLE place ``AppConfig.schedule_task_args`` + ``schedule_unattended`` are turned into
    reconcile facts, so no caller can re-derive "what the task carries" from the *current* config
    (the unsound baseline W3-C removes: a surface that mutated config before Settings mounted —
    a Mapping district switch — makes any current-config baseline equal the pending args, so the
    reconcile reads "unchanged" and silently skips the re-register it just promised).

    ``supports_unattended`` gates only the INFERENCE, never a recorded fact: a persisted
    ``unattended_flag`` is evidence and is honored on any platform (a ``config.json`` can travel),
    while the *absence* of evidence is treated as unknown only where an unattended logon type
    exists to lose.
    """
    args = task_args_from_persisted(raw_task_args)
    if args is None:
        return RegisteredSchedule(args=None, unattended=None if supports_unattended else False)
    return RegisteredSchedule(args=args, unattended=bool(unattended_flag))


class ScheduleReconcile(Enum):
    """What a Settings Save must do with the live nightly task (the pure decision; W3-C).

    ``NO_TASK`` — nothing is registered, so there is nothing to reconcile (a run-time edit is
    still persisted as config — see ``run_time_save_decision``). ``UP_TO_DATE`` — the durable
    record PROVES the live task already carries the pending args. ``REREGISTER`` — the record
    differs, **or there is no record at all**: an unproven task can never be reported up to date,
    because the app would be asserting a state it never checked.
    """

    NO_TASK = "no_task"
    UP_TO_DATE = "up_to_date"
    REREGISTER = "reregister"


def schedule_reconcile(
    *,
    schedule_registered: bool,
    record: RegisteredSchedule,
    pending: TaskArgs,
) -> ScheduleReconcile:
    """Decide whether a Settings Save must re-register the live task (pure, TOTAL; W3-C).

    The baseline comes ONLY from the durable record. An unproven record resolves to
    ``REREGISTER`` — the safe action, because a stale task silently converts the wrong district
    every night while the UI reports the fix as applied. The cost is bounded: a confirmed
    re-register writes the record, so the very next Save is precisely change-gated again (at most
    one extra prompt per install, never one per Save).
    """
    if not schedule_registered:
        return ScheduleReconcile.NO_TASK
    if record.args is None:
        return ScheduleReconcile.REREGISTER
    return ScheduleReconcile.REREGISTER if task_args_changed(record.args, pending) else ScheduleReconcile.UP_TO_DATE


def schedule_delivery_desync(
    *,
    schedule_live: bool,
    registered: TaskArgs | None,
    sftp_enabled: bool,
) -> bool:
    """Whether the LIVE task was baked WITHOUT ``--sftp`` while delivery is now enabled (pure).

    The wizard backtrack gap (0029 close-out): register on the Schedule step, Back to Delivery,
    save a credential (``sftp_enabled`` flips on), then Finish — the live task's baked ``--sftp``
    is stale, so tonight builds but never delivers while the finish line would otherwise claim
    delivery. The finish body reads this to downgrade its copy honestly (the Settings Save
    self-heals via the same persisted record later).

    Keyed off the durable last-REGISTERED record (``cfg.schedule_task_args``) rather than
    session-local state, so a resumed wizard whose task was registered in an EARLIER session is
    guarded too. Defensive-total: no live task, or no usable record (pre-record installs), →
    ``False`` — never assert a desync without evidence. Deliberately one-directional: a task
    baked WITH ``--sftp`` while delivery is now off is unreachable from the wizard (it has no
    disable affordance) and the finish copy claims nothing for it, so only the over-claiming
    direction (enabled now, not baked) flags.
    """
    if not schedule_live or registered is None:
        return False
    return bool(sftp_enabled) and not registered.sftp_enabled


# --------------------------------------------------------------------------- #
# Settings Save — run-time persistence decision (0034 S3-b).                    #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunTimeSaveDecision:
    """What a Settings Save must do with the run-time field when NO schedule is registered.

    Attributes:
        persist: the normalized (stripped) run time to write to ``cfg.schedule_time``, or
            ``None`` when there is nothing to persist (unchanged, or invalid).
        invalid: ``True`` when the field holds an EDITED value that failed ``validate_run_time``
            — the view paints the existing inline run-time error and persists nothing.
    """

    persist: str | None
    invalid: bool


def run_time_save_decision(*, saved_run_time: str, field_run_time: str) -> RunTimeSaveDecision:
    """Decide whether a Settings Save persists a run-time edit with no registered task (pure).

    The run time is CONFIG, not only a register side-effect (0034 S3-b): with a registered
    schedule an edit re-registers (and persists on success), but with no task the edit used to
    evaporate on Save. This decision closes that: a changed, valid ``HH:MM`` persists; an
    unchanged field is a no-op; a changed-but-invalid value persists NOTHING and flags
    ``invalid`` so the view surfaces the same inline error the register flow shows.
    """
    pending = (field_run_time or "").strip()
    if pending == (saved_run_time or "").strip():
        return RunTimeSaveDecision(persist=None, invalid=False)
    try:
        validate_run_time(pending)
    except ValueError:
        return RunTimeSaveDecision(persist=None, invalid=True)
    return RunTimeSaveDecision(persist=pending, invalid=False)


# --------------------------------------------------------------------------- #
# Reconcile re-register — the no-silent-downgrade interrupt (0034 S3-a).        #
# --------------------------------------------------------------------------- #
# Owner-approved copy (2026-07-15): the two explicit choices, verbatim. Calm, no default that
# downgrades silently; Cancel = no change, task untouched.
_DOWNGRADE_HEADLINE = "Keep the nightly sync running when you're signed out?"
_DOWNGRADE_DETAIL = (
    "Your nightly schedule currently runs whether or not anyone is signed in. "
    "Updating it without your Windows password would change it to run only while you're signed in."
)
# The UNKNOWN-logon-type variant (W3-C): on an install whose task was registered before the
# durable record shipped, ``schedule_unattended`` is a dataclass default, not an observation — so
# the copy must NOT reuse the assertive "your schedule currently runs whether or not anyone is
# signed in" (the trust bar: never assert a state you didn't check). Same three choices, honest
# premise. Only the premise differs; the consequence of continuing is identical.
_DOWNGRADE_UNKNOWN_HEADLINE = "Should the nightly sync keep running when you're signed out?"
_DOWNGRADE_UNKNOWN_DETAIL = (
    "We can't tell whether your nightly schedule runs while you're signed out — DistrictSync has "
    "no record of how it was set up. Updating it without your Windows password would set it to "
    "run only while you're signed in."
)
_DOWNGRADE_KEEP_LABEL = "Keep running when signed out — re-enter the Windows password"
_DOWNGRADE_SIGNED_IN_ONLY_LABEL = "Continue — the sync will only run while signed in"
_DOWNGRADE_CANCEL_LABEL = "Cancel"
_DOWNGRADE_KEEP_NEXT_HEADLINE = "Enter your Windows password to update the schedule"
_DOWNGRADE_KEEP_NEXT_DETAIL = (
    "Type your Windows account password below, then choose Schedule nightly sync — your new "
    "settings will apply and the sync will keep running when you're signed out."
)
_DOWNGRADE_CANCELLED_HEADLINE = "Schedule not updated"
_DOWNGRADE_CANCELLED_DETAIL = (
    "Your settings are saved, but the nightly schedule still runs with your previous settings. "
    "Save again whenever you're ready to update it."
)


@dataclass(frozen=True)
class DowngradeInterrupt:
    """The explicit-choice dialog a reconcile re-register must show before a logon downgrade.

    Produced by ``downgrade_interrupt`` when re-registering would (or MIGHT — the unknown-record
    variant, W3-C) silently turn an unattended task (registered WITH a Windows password — runs
    while signed out) into a logged-on-only one. ``headline``/``detail`` carry the known-unattended
    premise by default and are overridden with the honest can't-tell premise for an unproven
    record; every choice label is shared, so the view is variant-agnostic (it renders whatever
    copy it is handed). The view renders exactly this copy: two equal-weight choices (neither is a default
    that downgrades silently) plus Cancel (no change — the task is untouched).
    ``keep_next_*`` is the guidance painted after choosing to stay unattended — the password
    is collected ONLY through the existing schedule-section field flow (I1/I3: handler-local,
    never a dialog stash, never persisted). ``cancelled_*`` is the honest post-Cancel record.
    """

    headline: str = _DOWNGRADE_HEADLINE
    detail: str = _DOWNGRADE_DETAIL
    keep_unattended_label: str = _DOWNGRADE_KEEP_LABEL
    signed_in_only_label: str = _DOWNGRADE_SIGNED_IN_ONLY_LABEL
    cancel_label: str = _DOWNGRADE_CANCEL_LABEL
    keep_next_headline: str = _DOWNGRADE_KEEP_NEXT_HEADLINE
    keep_next_detail: str = _DOWNGRADE_KEEP_NEXT_DETAIL
    cancelled_headline: str = _DOWNGRADE_CANCELLED_HEADLINE
    cancelled_detail: str = _DOWNGRADE_CANCELLED_DETAIL


def downgrade_interrupt(*, registered_unattended: bool | None, password_supplied: bool) -> DowngradeInterrupt | None:
    """Whether a reconcile-triggered re-register must pause for the explicit downgrade choice.

    ``None`` → proceed (re-registering cannot downgrade the logon type: the task was never
    unattended, or a password is supplied so it stays unattended). A ``DowngradeInterrupt`` →
    the view MUST show the choice dialog before registering — a task registered to run while
    signed out would otherwise be silently replaced by a logged-on-only one when the Settings
    password field is blank.

    ``registered_unattended`` is the durable ``RegisteredSchedule.unattended`` fact, and
    ``None`` means **unknown** (W3-C — an install with no record of how its task was set up).
    Unknown interrupts too, with its own honest copy: the same silent-downgrade hazard applies
    (on a district server nobody is signed in, so a downgrade stops the nightly sync entirely),
    and guessing "not unattended" would be exactly the unchecked assertion the trust bar forbids.

    Applies ONLY to the reconcile path (Settings Save): a blank-password Register via the
    button is a legitimate explicit user choice (the wizard offers it) and never interrupts.
    """
    if password_supplied:
        return None
    if registered_unattended is None:
        return DowngradeInterrupt(headline=_DOWNGRADE_UNKNOWN_HEADLINE, detail=_DOWNGRADE_UNKNOWN_DETAIL)
    return DowngradeInterrupt() if registered_unattended else None


# --------------------------------------------------------------------------- #
# Settings reconcile outcome — honest Save-note copy (0034 S3 correctness fix). #
# --------------------------------------------------------------------------- #
# The shared Settings reconcile either DISPATCHES a re-register, merely shows the downgrade
# INTERRUPT (nothing registered — the admin must still choose), is BLOCKED by the register flow's
# own gate (nothing registered — e.g. an invalid run time, whose inline error the schedule section
# paints), or does neither (NONE — no live task, or no task-baked field changed). Both Save sites
# paint their schedule note from THIS outcome, so a Save can never claim the nightly schedule is
# "updating" when the reconcile only opened the choice dialog / hit a gate and returned without
# registering (the bug this fix closes: after Cancel an optimistic "updating…" Save note
# contradicted the schedule card's "Schedule not updated"; the same optimistic note also painted
# beside a run-time ErrorCard). "Saved" itself stays truthful — the config fields DID persist;
# only the schedule clause is gated on what actually happened.
_FOLDERS_SAVED = "Saved."
_FOLDERS_SAVED_DISPATCHED = "Saved — updating the nightly schedule to match…"
_FOLDERS_SAVED_INTERRUPTED = "Saved — confirm the schedule choice above."
_FOLDERS_SAVED_BLOCKED = (
    "Saved — the nightly schedule wasn't updated. Fix the run time in the Daily schedule section, then save again."
)
_SFTP_RECONCILE_DISPATCHED = " Updating the nightly schedule to deliver too…"
_SFTP_RECONCILE_INTERRUPTED = " Confirm the schedule choice above to update the nightly sync."
_SFTP_RECONCILE_BLOCKED = (
    " The nightly schedule wasn't updated — fix the run time in the Daily schedule section, then save again."
)


class ReconcileOutcome(Enum):
    """What the shared Settings reconcile actually did with the live nightly task (S3 fix).

    ``DISPATCHED`` — a re-register was genuinely started (the schedule section is now applying the
    new settings). ``INTERRUPTED`` — the downgrade-choice dialog was shown INSTEAD and nothing was
    registered (the admin must confirm first). ``BLOCKED`` — a re-register was needed but the
    register flow early-returned WITHOUT dispatching (its own gate refused — e.g. a malformed run
    time, whose inline error the schedule section paints). ``NONE`` — no reconcile action (no
    registered task, or no task-baked field changed). The two Settings Save sites paint their
    schedule note from this, so an optimistic "updating…" note is never shown when the reconcile
    merely opened a dialog / hit a gate and returned.
    """

    DISPATCHED = "dispatched"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    NONE = "none"


def folders_save_note(outcome: ReconcileOutcome) -> str:
    """The folders/district Save note, honest to what the reconcile actually did (pure, TOTAL).

    ``DISPATCHED`` → the "updating the nightly schedule to match" note; ``INTERRUPTED`` → an honest
    "confirm the schedule choice above" (the dialog is open, nothing is updating yet); ``BLOCKED``
    → an honest "the schedule wasn't updated — fix the run time" (the register flow refused to
    dispatch and painted its inline error); ``NONE`` (or any unexpected value) → a plain "Saved."
    The "Saved" prefix is always truthful — the folders + district persisted regardless of the
    schedule reconcile.
    """
    if outcome is ReconcileOutcome.DISPATCHED:
        return _FOLDERS_SAVED_DISPATCHED
    if outcome is ReconcileOutcome.INTERRUPTED:
        return _FOLDERS_SAVED_INTERRUPTED
    if outcome is ReconcileOutcome.BLOCKED:
        return _FOLDERS_SAVED_BLOCKED
    return _FOLDERS_SAVED


def sftp_reconcile_suffix(outcome: ReconcileOutcome) -> str:
    """The clause appended to the "Delivery settings saved" note, honest to the outcome (pure).

    ``DISPATCHED`` → the "updating the nightly schedule to deliver too" clause; ``INTERRUPTED`` →
    an honest "confirm the schedule choice above" prompt (the dialog is open, nothing dispatched);
    ``BLOCKED`` → an honest "the schedule wasn't updated — fix the run time" (the register flow
    refused to dispatch); ``NONE`` (or any unexpected value) → empty (no live task to update, so
    the base stored-note stands alone). Leading space so it appends cleanly to the base sentence.
    """
    if outcome is ReconcileOutcome.DISPATCHED:
        return _SFTP_RECONCILE_DISPATCHED
    if outcome is ReconcileOutcome.INTERRUPTED:
        return _SFTP_RECONCILE_INTERRUPTED
    if outcome is ReconcileOutcome.BLOCKED:
        return _SFTP_RECONCILE_BLOCKED
    return ""


# --------------------------------------------------------------------------- #
# Adaptive finish-line copy — honesty register (D8).                           #
# --------------------------------------------------------------------------- #
# The finish headline adapts to honesty (finding #1a): a scheduled install peaks on "You're all
# set."; a schedule-skipped install must NOT over-signal at the peak moment — it names the one
# thing still open (no nightly schedule) right in the headline. The delivery-desync variant
# (backtrack guard) names its one open item the same way.
_FINISH_HEADLINE_SCHEDULED = "You're all set"
_FINISH_HEADLINE_UNSCHEDULED = "You're set up — nightly sync not scheduled yet"
_FINISH_HEADLINE_DESYNC = "You're set up — delivery needs one more save"

# Appended to the LIVE schedule summary row when the delivery-desync guard fires, so the checked
# summary can never contradict the downgraded finish copy (the task is live but carries no --sftp).
_SUMMARY_SCHEDULE_DESYNC_SUFFIX = " — delivery not included yet"

# One-time cue shown after the finish confirmation, when the surface graduates to Settings.
TRANSITION_CUE = "You're all set — this is now your Settings page; edit anything here anytime."


def finish_needs_attention(*, delivery: DeliveryFact, delivery_desync: bool) -> bool:
    """Whether the finish banner needs the amber (attention) tone (pure, the single source).

    TRUE exactly when ``finish_copy`` downgrades to the desync headline: a CONFIGURED
    delivery (tested-ok or stored — either way ``sftp_enabled`` was flipped by a real Save,
    so a saved credential backs the claim) whose live task was baked without ``--sftp``.
    The view derives the banner ``Verdict`` from THIS predicate — never from the raw desync
    fact alone — so the amber tone can never sit under a confident "You're all set"
    headline (W4a nit: on the Save-then-Test path the post-save Test flipped the session's
    delivery fact from ``STORED_CRED_PRESENT`` to ``TESTED_OK``, the copy stayed confident,
    and the verdict went amber on the raw desync — tone and words disagreed).
    """
    return delivery_desync and delivery in _DELIVERY_CONFIGURED


def _tonight_prefix(schedule_time_display: str | None) -> str:
    """ "Tonight at 3:00 AM" when a real next-run time is known, else a timeless "Tonight".

    The time comes ONLY from the OS-reported next run (the read-back's ``next_run_display``),
    never the config hint presented as verified — a found task with no reported next-run reads
    the timeless form rather than asserting a schedule_time it never confirmed.
    """
    return f"Tonight at {schedule_time_display}" if schedule_time_display else "Tonight"


def finish_copy(
    *,
    schedule_live: bool,
    delivery: DeliveryFact,
    district: str,
    schedule_time_display: str | None,
    host: str,
    username: str,
    delivery_desync: bool = False,
) -> tuple[str, str]:
    """The adaptive (headline, detail) for the finish line — honest, never a future guarantee.

    The delivery claim keys off PERSISTED delivery, never a transient test (F1 honesty fix): only a
    saved credential (``STORED_CRED_PRESENT`` — ``sftp_enabled`` written + the keyring holds it)
    lets the copy promise the nightly will *try to deliver*; a merely-tested-but-unsaved connection
    (``TESTED_OK``) says the connection worked and prompts Save, WITHOUT claiming the nightly will
    deliver (the nightly reads saved config, so an unsaved test changes nothing tonight).

    ``delivery_desync`` (the backtrack guard — ``schedule_delivery_desync``) downgrades the
    delivery promise for a CONFIGURED delivery (``finish_needs_attention`` — the single source
    the view's banner verdict shares, so tone and words always agree): the credential IS saved
    (``sftp_enabled`` only flips on a real Save, so the ``TESTED_OK`` session fact after a
    Save-then-Test is backed by a stored credential too), but the live task was baked without
    ``--sftp``, so the copy must NOT claim tonight delivers — it names the one Save in
    Settings that will pick the change up. An unconfigured delivery (skipped/absent/failed)
    claims nothing about delivering, so the desync flag changes nothing there.

    Four cases (plus the desync downgrade of the two configured-delivery ones):

    * **schedule skipped** (not live): the Convert-tab path + "add a schedule whenever you're
      ready" — no "tonight" claim, because nothing is scheduled.
    * **schedule live + delivery persisted** (``STORED_CRED_PRESENT``): the district built + a
      real "will try to deliver to SpacesEDU" (the saved credential backs the claim) — unless
      ``delivery_desync``, which swaps in the honest "the schedule hasn't picked up the delivery
      change yet" variant.
    * **schedule live + delivery tested-but-unsaved** (``TESTED_OK``): the connection to <host>
      as <user> worked, but it isn't saved — click Save; NO nightly-delivery claim.
    * **schedule live + delivery deferred/absent/failed**: built into the output folder + the
      "set up delivery whenever you're ready" defer.
    """
    if not schedule_live:
        return _FINISH_HEADLINE_UNSCHEDULED, (
            f"DistrictSync will build {district} when you run a conversion. "
            "Run conversions from the Convert tab; add a nightly schedule whenever you're ready."
        )
    prefix = _tonight_prefix(schedule_time_display)
    if finish_needs_attention(delivery=delivery, delivery_desync=delivery_desync):
        return _FINISH_HEADLINE_DESYNC, (
            f"{prefix} DistrictSync will build {district} into your output folder. Your delivery "
            "password is saved, but the nightly schedule hasn't picked up the delivery change yet — "
            "finish setup, then click Save in Settings to have the nightly sync deliver it too."
        )
    if delivery is DeliveryFact.STORED_CRED_PRESENT:
        detail = (
            f"{prefix} DistrictSync will build {district} and try to deliver it to SpacesEDU — "
            "your delivery password is saved on this computer."
        )
    elif delivery is DeliveryFact.TESTED_OK:
        detail = (
            f"{prefix} DistrictSync will build {district} into your output folder. Your delivery "
            f"connection to {host} as {username} worked — click Save on the delivery step to have "
            "the nightly sync deliver it too."
        )
    else:
        detail = (
            f"{prefix} DistrictSync will build {district} into your output folder. "
            "Set up delivery whenever you're ready."
        )
    return _FINISH_HEADLINE_SCHEDULED, detail


# --------------------------------------------------------------------------- #
# Finish-line checked summary — the honest "here's what you set up" card (D8).  #
# --------------------------------------------------------------------------- #
# The shared deferral phrase for a skippable step the admin left for later (Delivery / Schedule).
_SUMMARY_DEFERRED_DETAIL = "Set up later in Setup"


@dataclass(frozen=True)
class FinishSummaryRow:
    """One row of the finish-line checked summary — a configured-vs-deferred fact (D8, honesty).

    Attributes:
        label: the input step's name (Folders / District / Delivery / Schedule).
        done: the step is configured/ready (the view paints a ✓); ``False`` for a deferred
            skippable step (the view paints a subdued "set up later" cue — never a fake ✓).
        detail: the concrete value (the friendly district, the nightly time, the delivery
            target) or the honest deferral phrase. The view renders icon + label + detail and
            never re-derives the state.
    """

    label: str
    done: bool
    detail: str


def finish_summary_rows(
    *,
    schedule_live: bool,
    delivery: DeliveryFact,
    district: str,
    schedule_time_display: str | None,
    delivery_desync: bool = False,
) -> list[FinishSummaryRow]:
    """The ordered configured-vs-deferred checklist the finish card renders (pure, TOTAL).

    Rows follow the WIZARD input order — District, Folders, Delivery, Schedule (District leads per
    the 2026-07-15 reorder) — and derive from the SAME injected facts ``finish_copy`` consumes
    (``schedule_live``, ``delivery``, ``district``, ``schedule_time_display``,
    ``delivery_desync``), so the card can NEVER contradict the honest finish copy. The caller
    passes ``district`` already resolved to its friendly name (as it does for ``finish_copy``), so
    a raw config id never reaches the card.

    Honesty rules (mirroring the finish copy):

    * **Folders + District are required** — reaching Finish means both are done (always ``done``).
    * **Delivery is done only when a credential is configured** (tested-ok / stored); a *skipped*
      delivery, a failed test, and an untouched step all read as deferred — "a credential is
      configured" never means "data was delivered".
    * **Schedule is done only when the read-back is LIVE**; a skipped / unconfirmed schedule is
      deferred, and the LIVE detail names the OS-reported time when known (never a config hint —
      timeless "Nightly sync scheduled" when the read-back reported no next-run time). With
      ``delivery_desync`` (the backtrack guard) the LIVE detail also carries the honest
      "delivery not included yet" — the live task was baked without ``--sftp``, so the row must
      not read as if tonight delivers.
    """
    delivery_done = delivery in _DELIVERY_CONFIGURED
    if schedule_live:
        schedule_detail = f"Nightly at {schedule_time_display}" if schedule_time_display else "Nightly sync scheduled"
        if delivery_desync:
            schedule_detail += _SUMMARY_SCHEDULE_DESYNC_SUFFIX
    else:
        schedule_detail = _SUMMARY_DEFERRED_DETAIL
    return [
        FinishSummaryRow(label="District", done=True, detail=district),
        FinishSummaryRow(label="Folders", done=True, detail="Ready"),
        FinishSummaryRow(
            label="Delivery",
            done=delivery_done,
            detail="SpacesEDU" if delivery_done else _SUMMARY_DEFERRED_DETAIL,
        ),
        FinishSummaryRow(label="Schedule", done=schedule_live, detail=schedule_detail),
    ]
