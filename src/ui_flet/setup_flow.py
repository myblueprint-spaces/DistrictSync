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
from dataclasses import dataclass
from enum import Enum

from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus


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
    """

    folders_valid: bool
    district_chosen: bool
    schedule: ScheduleStatus | None = None
    schedule_skipped: bool = False
    delivery: DeliveryFact = DeliveryFact.NONE


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
    gate a disabled Next button enforces — same guarantee as ``setup_gates``). SCHEDULE /
    DELIVERY are skippable, so advancing is always allowed. FINISH advances (confirms) only
    when the finish line is reachable (``derive_flow(...).can_finish``).
    """
    if step is SetupStep.FOLDERS:
        return inputs.folders_valid
    if step is SetupStep.DISTRICT:
        return inputs.district_chosen
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


# --------------------------------------------------------------------------- #
# Adaptive finish-line copy — honesty register (D8).                           #
# --------------------------------------------------------------------------- #
# The finish headline adapts to honesty (finding #1a): a scheduled install peaks on "You're all
# set."; a schedule-skipped install must NOT over-signal at the peak moment — it names the one
# thing still open (no nightly schedule) right in the headline.
_FINISH_HEADLINE_SCHEDULED = "You're all set"
_FINISH_HEADLINE_UNSCHEDULED = "You're set up — nightly sync not scheduled yet"

# One-time cue shown after the finish confirmation, when the surface graduates to Settings.
TRANSITION_CUE = "You're all set — this is now your Settings page; edit anything here anytime."


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
) -> tuple[str, str]:
    """The adaptive (headline, detail) for the finish line — honest, never a future guarantee.

    The delivery claim keys off PERSISTED delivery, never a transient test (F1 honesty fix): only a
    saved credential (``STORED_CRED_PRESENT`` — ``sftp_enabled`` written + the keyring holds it)
    lets the copy promise the nightly will *try to deliver*; a merely-tested-but-unsaved connection
    (``TESTED_OK``) says the connection worked and prompts Save, WITHOUT claiming the nightly will
    deliver (the nightly reads saved config, so an unsaved test changes nothing tonight).

    Four cases:

    * **schedule skipped** (not live): the Convert-tab path + "add a schedule whenever you're
      ready" — no "tonight" claim, because nothing is scheduled.
    * **schedule live + delivery persisted** (``STORED_CRED_PRESENT``): the district built + a
      real "will try to deliver to SpacesEDU" (the saved credential backs the claim).
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
# A delivery counts as CONFIGURED for the summary when a credential was tested-ok or is already
# stored — deliberately NARROWER than ``_DELIVERY_SATISFIED`` (which also counts ``SKIPPED``): a
# skipped delivery is "safe to advance" for the flow, but the summary must show it as a *deferred*
# step, matching the honest finish copy. A failed/absent test is never configured.
_DELIVERY_CONFIGURED: frozenset[DeliveryFact] = frozenset({DeliveryFact.TESTED_OK, DeliveryFact.STORED_CRED_PRESENT})

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
) -> list[FinishSummaryRow]:
    """The ordered configured-vs-deferred checklist the finish card renders (pure, TOTAL).

    Rows follow the WIZARD input order — District, Folders, Delivery, Schedule (District leads per
    the 2026-07-15 reorder) — and derive from the SAME injected facts ``finish_copy`` consumes
    (``schedule_live``, ``delivery``, ``district``,
    ``schedule_time_display``), so the card can NEVER contradict the honest finish copy. The caller
    passes ``district`` already resolved to its friendly name (as it does for ``finish_copy``), so
    a raw config id never reaches the card.

    Honesty rules (mirroring the finish copy):

    * **Folders + District are required** — reaching Finish means both are done (always ``done``).
    * **Delivery is done only when a credential is configured** (tested-ok / stored); a *skipped*
      delivery, a failed test, and an untouched step all read as deferred — "a credential is
      configured" never means "data was delivered".
    * **Schedule is done only when the read-back is LIVE**; a skipped / unconfirmed schedule is
      deferred, and the LIVE detail names the OS-reported time when known (never a config hint —
      timeless "Nightly sync scheduled" when the read-back reported no next-run time).
    """
    delivery_done = delivery in _DELIVERY_CONFIGURED
    if schedule_live:
        schedule_detail = f"Nightly at {schedule_time_display}" if schedule_time_display else "Nightly sync scheduled"
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
