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
* **Two fixed shapes, one selector** (plan 0044 S3). ``FlowMode`` is either the shipped
  five-step ``"standard"`` walk or the six-step ``"creator"`` walk an admin takes when their
  district ships no mapping and they build one in-app (District → Folders → **Your files** →
  Delivery → Schedule → Finish). There is still NO data-driven step engine: two hand-written
  tuples and ONE selector (``step_order``). Every mode-aware function defaults to
  ``"standard"``, and the four creator ``FlowInputs`` facts each default to the SAFE value and
  are consulted ONLY in creator mode, so standard-mode behaviour is byte-identical.
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
from typing import Literal

from src.scheduler.task_com import PrincipalKind, principal_kind_from_record
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.setup_gates import principal_key
from src.utils.validators import validate_month_day, validate_run_time


class SetupStep(Enum):
    """The concrete, named wizard steps (no data-driven step engine — YAGNI, D8).

    ``FILES`` is the CREATOR-only "Your files" step (plan 0044 S3): it belongs to
    ``CREATOR_STEP_ORDER`` alone, so the standard walk is unchanged and ``step_number(FILES)``
    without ``mode="creator"`` RAISES rather than inventing a position in a walk that has none.
    """

    FOLDERS = "folders"
    DISTRICT = "district"
    FILES = "files"
    SCHEDULE = "schedule"
    DELIVERY = "delivery"
    FINISH = "finish"


# The two wizard SHAPES (plan 0044 S3). A ``Literal`` rather than an Enum deliberately: the mode is
# a plain string the view already holds (derived from a pending-creator token), it needs no
# behaviour of its own, and a string default keeps every existing call site byte-identical.
FlowMode = Literal["standard", "creator"]


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
# The CREATOR walk (plan 0044 S3): six steps, the standard five plus FILES. FILES sits AFTER
# FOLDERS because its test conversion needs the input folder to read, and DELIVERY still precedes
# SCHEDULE for exactly the F1 reason above (``--sftp`` is baked at registration).
CREATOR_STEP_ORDER: tuple[SetupStep, ...] = (
    SetupStep.DISTRICT,
    SetupStep.FOLDERS,
    SetupStep.FILES,
    SetupStep.DELIVERY,
    SetupStep.SCHEDULE,
    SetupStep.FINISH,
)
# ``TOTAL_STEPS`` stays the STANDARD denominator constant (== 5): the view reads it directly and its
# pin is load-bearing. ``total_steps(mode)`` is the mode-aware companion for the "Step N of M" line.
TOTAL_STEPS: int = len(STEP_ORDER)

# The two steps the user may defer ("set up later") — advancing them is always allowed. FILES is
# deliberately ABSENT: it IS the creator activation gate, so it can never be skipped past.
_SKIPPABLE_STEPS: frozenset[SetupStep] = frozenset({SetupStep.SCHEDULE, SetupStep.DELIVERY})


def step_order(mode: FlowMode) -> tuple[SetupStep, ...]:
    """The fixed step tuple for ``mode`` — the ONE place either walk is selected (pure, TOTAL).

    Every mode-aware function routes through here, so the two shapes can never disagree about
    order, length or membership. Anything other than ``"creator"`` reads as the standard walk (the
    safe shape: it has no activation gate to skip and no extra step to hide).
    """
    return CREATOR_STEP_ORDER if mode == "creator" else STEP_ORDER


def total_steps(mode: FlowMode) -> int:
    """The "Step N of M" denominator for ``mode`` (5 standard, 6 creator) — derived, never typed."""
    return len(step_order(mode))


def _pre_finish_steps(mode: FlowMode) -> tuple[SetupStep, ...]:
    """The steps that must be satisfied before ``mode``'s finish line is reachable.

    DERIVED from ``step_order`` rather than hand-written a second time (a parallel tuple would
    drift the moment a step moves). FINISH itself is the terminal confirmation and is never
    "satisfied" by derivation — only by the explicit confirm.
    """
    return tuple(step for step in step_order(mode) if step is not SetupStep.FINISH)


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
        schedule_busy: a register/unregister is IN FLIGHT on the Schedule step (the view sets it
            around the off-thread call, which on Windows blocks on the UAC prompt). Like
            ``window_valid`` it closes the Schedule step's Continue gate and NOTHING else —
            advancing mid-flight abandoned a registration the admin had just authorised and
            latched ``schedule_skipped`` against a task that then went live (QA, 2026-08-18).
            Transient by construction: it is never persisted, and a crashed worker clears it in
            the same ``finally`` that clears the spinner.
        mode: which wizard shape this walk is (plan 0044 S3). ``"standard"`` by default, so an
            existing caller keeps the five-step walk byte-identical.
        creator_district_chosen: the creator's district IS chosen — a pending overlay/token exists
            for it. Read INSTEAD of ``district_chosen`` in creator mode (a creator has no bundled
            district to pick, so the standard fact could never satisfy the step for them).
        files_step_satisfied: the "Your files" gate has been passed and is still current (the
            overlay exists AND the recorded verified digest matches the resolved config's). The
            view computes it; this module never touches a file.
        creator_activated: the new district is genuinely the one this install converts
            (``sis_type`` is the creator id and the pending token is cleared).

    The last four are consulted **only when ``mode == "creator"``** and each defaults to the SAFE
    value (standard walk / not chosen / gate closed / not activated), so no default can loosen the
    standard walk and a standard-mode ``FlowInputs`` ignores them entirely. Like every other field
    here they are INJECTED — the view does the I/O (``folders_valid`` is the precedent), which is
    what keeps this module free of ``flet``, ``pathlib`` and any config/authoring import.
    """

    folders_valid: bool
    district_chosen: bool
    schedule: ScheduleStatus | None = None
    schedule_skipped: bool = False
    delivery: DeliveryFact = DeliveryFact.NONE
    window_valid: bool = True
    schedule_busy: bool = False
    mode: FlowMode = "standard"
    creator_district_chosen: bool = False
    files_step_satisfied: bool = False
    creator_activated: bool = False


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
    """The set of derivation-satisfied pre-finish steps (FINISH is only ever confirmed).

    Creator mode changes exactly two rows: DISTRICT is satisfied by ``creator_district_chosen``
    INSTEAD of ``district_chosen`` (a creator picks no bundled district, so the standard fact would
    never fire), and FILES is satisfied by ``files_step_satisfied`` ALONE — the injected "gate
    passed and still current" fact, never inferred from anything else.
    """
    creator = inputs.mode == "creator"
    done: set[SetupStep] = set()
    if inputs.folders_valid:
        done.add(SetupStep.FOLDERS)
    district_chosen = inputs.creator_district_chosen if creator else inputs.district_chosen
    if district_chosen:
        done.add(SetupStep.DISTRICT)
    if creator and inputs.files_step_satisfied:
        done.add(SetupStep.FILES)
    if _schedule_satisfied(inputs):
        done.add(SetupStep.SCHEDULE)
    if inputs.delivery in _DELIVERY_SATISFIED:
        done.add(SetupStep.DELIVERY)
    return frozenset(done)


def derive_flow(inputs: FlowInputs) -> FlowState:
    """Derive the wizard ``FlowState`` from injected real-state facts (pure, TOTAL).

    ``resume_step`` is the first pre-finish step of ``inputs.mode``'s walk that is not satisfied;
    if they all are, the resume target is FINISH (the reachable confirmation). ``can_finish``
    mirrors that: the finish line is reachable only when every pre-finish step of that walk is
    satisfied (skipped counts as satisfied for the two deferrable steps).

    **Creator mode ANDs ``creator_activated`` into ``can_finish``** (plan 0044 S3): a creator who
    never pressed the activation confirm must not reach the confirmation that flips
    ``setup_completed``, or the install would read as set up while ``sis_type`` still pointed at
    nothing (``has_completed_setup()`` True / ``is_complete()`` False). Belt-and-braces by
    construction — the recorded verified digest that opens the FILES gate is written BY the
    activation — and deliberately so: it is the one guard that does not depend on that ordering.
    """
    satisfied = _satisfied_steps(inputs)
    pre_finish = _pre_finish_steps(inputs.mode)
    resume = SetupStep.FINISH
    for step in pre_finish:
        if step not in satisfied:
            resume = step
            break
    can_finish = all(step in satisfied for step in pre_finish)
    if inputs.mode == "creator":
        can_finish = can_finish and inputs.creator_activated
    return FlowState(resume_step=resume, satisfied=satisfied, can_finish=can_finish)


def can_advance(step: SetupStep, inputs: FlowInputs) -> bool:
    """Whether the given step's Next/Enter gate is satisfied (pure — the Enter-advance gate).

    FOLDERS / DISTRICT advance only when their own value is valid (Enter can never bypass the
    gate a disabled Next button enforces — same guarantee as ``setup_gates``); in creator mode
    DISTRICT reads ``creator_district_chosen``, mirroring ``_satisfied_steps``. FILES (creator only)
    advances only once ``files_step_satisfied`` — it IS the activation gate, so it is NOT skippable
    and Enter must not walk past an untested district. SCHEDULE is
    skippable BUT additionally gated on TWO transient view facts — ``window_valid`` (an
    enabled-but-invalid seasonal window blocks Continue; the window lives on the Schedule step)
    and ``schedule_busy`` (a register/unregister is in flight). DELIVERY is skippable, so
    advancing is always allowed. FINISH advances (confirms) only when the finish line is
    reachable (``derive_flow(...).can_finish``).

    Both Schedule gates are ADVANCE-only: neither reaches ``derive_flow``, so neither can change
    where a reopened wizard resumes or which steps count as satisfied.
    """
    if step is SetupStep.FOLDERS:
        return inputs.folders_valid
    if step is SetupStep.DISTRICT:
        return inputs.creator_district_chosen if inputs.mode == "creator" else inputs.district_chosen
    if step is SetupStep.FILES:
        return inputs.files_step_satisfied
    if step is SetupStep.SCHEDULE:
        # Skippable, but a visibly-enabled invalid window can't be advanced past (the window is
        # not a task arg — this only blocks Continue, never the register flow), and neither can
        # an in-flight register/unregister (whose UAC prompt is still on screen).
        return inputs.window_valid and not inputs.schedule_busy
    if step in _SKIPPABLE_STEPS:
        return True
    return derive_flow(inputs).can_finish


def is_skippable(step: SetupStep) -> bool:
    """Whether the step offers a "set up later" defer (Schedule + Delivery only)."""
    return step in _SKIPPABLE_STEPS


def step_number(step: SetupStep, *, mode: FlowMode = "standard") -> int:
    """The 1-based position of ``step`` in ``mode``'s walk (the "Step N of M" numerator).

    Keyword-only ``mode`` with the standard default, so every existing call is unchanged. A step
    absent from the selected walk RAISES (``ValueError``) rather than inventing a position — asking
    for FILES' number in the standard walk is a bug, not a display edge case.
    """
    return step_order(mode).index(step) + 1


def next_step(step: SetupStep, *, mode: FlowMode = "standard") -> SetupStep | None:
    """The step after ``step`` in ``mode``'s fixed order, or ``None`` at the end."""
    order = step_order(mode)
    index = order.index(step)
    return order[index + 1] if index + 1 < len(order) else None


def prev_step(step: SetupStep, *, mode: FlowMode = "standard") -> SetupStep | None:
    """The step before ``step`` in ``mode``'s fixed order, or ``None`` at the start."""
    order = step_order(mode)
    index = order.index(step)
    return order[index - 1] if index > 0 else None


def auto_selected_district(available: Sequence[str]) -> str:
    """The district to pre-select in the District step: the sole VISIBLE option, else nothing.

    Auto-select ONLY when there is exactly one option (there is no meaningful choice to
    make); with zero or several, return ``""`` so the "Choose your district" placeholder shows
    and the admin picks explicitly — no silent alphabetical default.

    **``available`` is the VISIBLE list, not the whole catalog (D9 re-scoped, plan 0038 S5 —
    see the dated DECISIONS entry).** The caller passes
    ``mapping_catalog.filtered_catalog(...)``'s ids, so a matched admin whose domain resolves
    to exactly one district gets it pre-selected. Two things that does NOT change:

    * it stays a PRE-SELECTION on a step the admin still lands on — the caller applies it
      AFTER ``derive_flow``, deliberately, or a satisfied District step would be resumed past
      and never confirmed;
    * Convert does not auto-select at all. A per-run conversion is not a setup step; it
      prefills only from a valid saved ``sis_type`` (the other half of D9, untouched).

    This function is unchanged by the re-scoping — the rule was always "one option", and the
    caller decides what is on offer.
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
#: The run-as field's label — single-sourced (plan 0046 B). The view renders it; the
#: ``setup_errors`` branches for a bad account NAME and a missing service-account password both
#: quote it, so an admin is pointed at a control that is spelled the same way on screen.
SCHEDULE_ACCOUNT_FIELD_LABEL = "Windows account for the nightly task"

#: The gMSA disclosure's tick-box label (plan 0049 S-4) — single-sourced for exactly the reason
#: the field label above is: the Settings view renders it, the downgrade interrupt's
#: follow-through copy tells the admin to leave it ticked, and ``setup_errors``' managed-service
#: -account arm names it. Three surfaces, one spelling.
SCHEDULE_GMSA_TOGGLE_LABEL = "This is a managed service account (gMSA)"

#: What a district's IT team must do BEFORE a managed service account can run the nightly task,
#: as a checklist (plan 0049 S-4). Read by the Settings disclosure AND by ``setup_errors``'
#: managed-service-account failure arm, because those are the two moments an admin needs them
#: and a second copy would let the two drift. The hand-to-IT document states them verbatim too,
#: tied back by ``tests/test_partner_doc_schedule_copy_parity.py``.
#:
#: None of the three is knowable from here: ``validators.validate_gmsa_account`` is a SHAPE
#: check by design, and Windows answers the real questions only at registration time. So this is
#: a list to hand to somebody, never a state this app can verify — which is also why the
#: failure arm offers no "try again" and the disclosure renders them as read-only glyphs rather
#: than tick boxes.
GMSA_PREREQUISITES: tuple[str, ...] = (
    "This computer is listed in the account's PrincipalsAllowedToRetrieveManagedPassword.",
    "Install-ADServiceAccount has been run for the account on this computer.",
    "The account is granted the 'Log on as a batch job' right on this computer.",
)

#: The hand-to-IT document's TITLE, spelled once (plan 0049 S-4). The Settings disclosure names
#: it so an admin can ask for it by name, and ``docs/partner/managed-service-accounts.md`` opens
#: with it — a parity test ties the two together, because a document nobody can find by the name
#: the app gave them is worse than no reference at all. It is a NAME, not a link: the MkDocs site
#: was removed, so a partner doc has no URL to click.
GMSA_IT_DOC_TITLE = "Managed service accounts (gMSA) — what your IT team needs to do"


@dataclass(frozen=True)
class RegisteredSchedule:
    """What the app durably KNOWS about the live scheduled task — ``None`` means "we can't tell".

    The four facets are written together by every confirmed register and cleared together by every
    confirmed unregister, so the record is **atomic**: either the facts are evidenced or none of
    them is. That is why an absent ``args`` also makes ``unattended`` unknown — the ``False``
    default of ``AppConfig.schedule_unattended`` is a dataclass default, not an observation.

    Attributes:
        args: the task-baked args the live task actually carries, or ``None`` when there is no
            usable record (an install that registered before the record shipped in v3.7.0, or a
            hand-edited ``config.json``).
        unattended: whether the live task runs while signed out, or ``None`` when unknown. Always
            ``False`` where the platform has no stored-password logon at all (cron): there is no
            logon type an unproven re-register could downgrade, so "unknown" would buy nothing.
        run_as_user: the PRINCIPAL the live task was registered to (plan 0046 B). ``None`` exactly
            when ``args`` is ``None`` — the facets are written and cleared together, so an absent
            record makes all three unknown. ``""`` (the signed-in account) is not a guess: no build
            before this one could register anything else (``screens/setup.py`` passed
            ``run_as_user=None`` unconditionally), so a pre-0046 record's ``""`` is EVIDENCED. A
            hand-edited ``config.json`` can still lie; every consequence of being wrong falls back
            to refusing a switch, never to performing one.
        run_as_kind: the principal KIND the live task was registered with (plan 0049 S-4), or
            ``None`` exactly when ``args`` is ``None`` — the fourth facet of the same atomic
            record, so an absent record makes all four unknown. It is never ``None`` beside a
            usable record: ``task_com.principal_kind_from_record`` resolves a blank stored value
            from the recorded USER, and both of its answers are evidenced by what earlier builds
            could register. Recorded rather than derived because a managed service account is
            unattended WITHOUT a password, so ``unattended`` no longer implies which credential
            Windows wants — and the ``$`` on the name is a spelling, not a security kind.
    """

    args: TaskArgs | None
    unattended: bool | None
    run_as_user: str | None
    run_as_kind: PrincipalKind | None


def registered_schedule(
    *,
    raw_task_args: object,
    unattended_flag: bool,
    raw_run_as_user: object,
    raw_run_as_kind: object,
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

    ``raw_run_as_kind`` (plan 0049 S-4) is REQUIRED and undefaulted, for the same reason
    ``raw_run_as_user`` is: a defaulted facet is one a new call site can quietly stop supplying,
    and this one selects which credential story the admin is told about. Its blank/absent value
    is resolved by ``task_com.principal_kind_from_record`` — the ONE spelling of that evidenced
    rule, shared with the elevated prune.
    """
    args = task_args_from_persisted(raw_task_args)
    if args is None:
        return RegisteredSchedule(
            args=None,
            unattended=None if supports_unattended else False,
            run_as_user=None,
            run_as_kind=None,
        )
    principal = raw_run_as_user.strip() if isinstance(raw_run_as_user, str) else ""
    return RegisteredSchedule(
        args=args,
        unattended=bool(unattended_flag),
        run_as_user=principal,
        run_as_kind=principal_kind_from_record(raw_run_as_kind, user=principal),
    )


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
    pending_run_as_user: str,
    current_account: str,
) -> ScheduleReconcile:
    """Decide whether a Settings Save must re-register the live task (pure, TOTAL; W3-C).

    The baseline comes ONLY from the durable record. An unproven record resolves to
    ``REREGISTER`` — the safe action, because a stale task silently converts the wrong district
    every night while the UI reports the fix as applied. The cost is bounded: a confirmed
    re-register writes the record, so the very next Save is precisely change-gated again (at most
    one extra prompt per install, never one per Save).

    The PRINCIPAL is a third axis (plan 0046 B): a task whose args are unchanged but whose
    run-as account moved is NOT up to date, and reporting it so would leave the nightly running
    as the wrong identity while Settings claimed otherwise. ``TaskArgs`` is deliberately untouched
    (A2 — it is documented as "fields baked into the ACTION", and adding a field would invalidate
    every persisted ``schedule_task_args`` record on 20 installs), so the comparison is inlined
    here, through the ONE ``setup_gates.principal_key`` reduction. It is reachable only when
    ``record.args is not None`` — the facets move together.
    """
    if not schedule_registered:
        return ScheduleReconcile.NO_TASK
    if record.args is None:
        return ScheduleReconcile.REREGISTER
    if task_args_changed(record.args, pending):
        return ScheduleReconcile.REREGISTER
    if principal_key(record.run_as_user, current_account) != principal_key(pending_run_as_user, current_account):
        return ScheduleReconcile.REREGISTER
    return ScheduleReconcile.UP_TO_DATE


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
# The SERVICE-ACCOUNT variant (plan 0046 B). Every pre-0046 string here coaches the admin's OWN
# Windows password, which is the wrong credential when the task runs as someone else — so the
# follow-through copy (``keep_next_*``) is overridden too, not just the premise. The account name
# is interpolated by ``downgrade_interrupt``: the pure function owns the copy and the view renders
# whatever it is handed, unchanged from today.
_DOWNGRADE_SERVICE_ACCOUNT_HEADLINE = "Re-enter the service account password to update the nightly sync"
_DOWNGRADE_SERVICE_ACCOUNT_DETAIL = (
    "Your nightly schedule runs as {account}. Windows needs that account's password again to apply "
    "your new settings — DistrictSync never stores it. To run the sync as a different account "
    "instead, choose Remove nightly sync first, then schedule it again."
)
_DOWNGRADE_SERVICE_ACCOUNT_KEEP_NEXT_HEADLINE = "Enter the password for {account}"
_DOWNGRADE_SERVICE_ACCOUNT_KEEP_NEXT_DETAIL = (
    "Type that account's Windows password in the Daily schedule section, then choose Schedule "
    "nightly sync — your new settings will apply and the sync will keep running when you're signed out."
)
# The MANAGED-SERVICE-ACCOUNT variant (plan 0049 S-4). Every other variant here asks for a
# credential; this one must not, and deliberately does not contain the word at all. The
# directory holds a gMSA's credential, so there is nothing an admin could type, and the
# service-account variant's "Windows needs that account's password again" would send them
# hunting for something that does not exist — the same class of wrong coaching plan 0046's A7
# found aimed at a service account, one kind further on.
#
# It still INTERRUPTS rather than proceeding silently, for a reason the other variants do not
# have: applying the settings DELETES and re-creates the task, and the account it is re-created
# for is one this app can neither verify nor re-authorise — so an admin is entitled to decide
# when that happens and to be told to check the result.
_DOWNGRADE_MSA_HEADLINE = "Update the nightly sync that runs as a managed service account?"
_DOWNGRADE_MSA_DETAIL = (
    "Your nightly schedule runs as {account}, a managed service account — Windows gets that "
    "account's credential from your directory, so there is nothing for you to type. Applying "
    "your new settings re-creates the task as the same account, and Windows asks for permission "
    "once. Check the schedule shown on this page afterwards, before changing anything else. To "
    "run the sync as a different account instead, choose Remove nightly sync first, then "
    "schedule it again."
)
_DOWNGRADE_MSA_KEEP_LABEL = "Update the schedule for {account}"
_DOWNGRADE_MSA_KEEP_NEXT_HEADLINE = "Choose Schedule nightly sync to update it"
_DOWNGRADE_MSA_KEEP_NEXT_DETAIL = (
    f"In the Daily schedule section, leave '{SCHEDULE_GMSA_TOGGLE_LABEL}' ticked and choose "
    "Schedule nightly sync — your new settings will apply and the sync will keep running when "
    "no one is signed in. There is nothing for you to type."
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
    ``offers_signed_in_only`` is ``False`` for the service-account variant (plan 0046 B): the
    Register gate REFUSES a principal change on a live task, so that button would be a dead
    control — and its label goes blank with it, so nothing can render it by accident. The
    managed-service-account variant (plan 0049 S-4) withdraws it for the same reason, and is
    also the first variant to override ``keep_unattended_label``: every other one offers to
    "re-enter the Windows password", which is not a thing a gMSA has.
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
    offers_signed_in_only: bool = True


def downgrade_interrupt(
    *,
    registered_unattended: bool | None,
    password_supplied: bool,
    registered_foreign_account: str,
    registered_kind: PrincipalKind | None,
) -> DowngradeInterrupt | None:
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

    ``registered_foreign_account`` is the RECORDED principal when it is not the signed-in account
    (blank otherwise — the view reduces it through ``setup_gates.principal_key`` first). A
    non-blank value selects the service-account variant REGARDLESS of ``registered_unattended``:
    a record claiming a foreign principal is not unattended is inconsistent, so interrupting is
    the honest move. It is also the only place the admin is told WHICH account's password Windows
    wants — every pre-0046 string here says "your Windows account password", which is the wrong
    credential.

    ``registered_kind`` is the RECORDED :class:`~src.scheduler.task_com.PrincipalKind` (plan 0049
    S-4), ``None`` when there is no usable record. It is REQUIRED and undefaulted because it
    selects between two mutually exclusive credential stories, and the safe-looking default
    (PASSWORD) is the wrong one for a gMSA. It is a RECORD, never a character in a name: sniffing
    the ``$`` off ``registered_foreign_account`` would re-introduce one layer up exactly the
    inference S-3 deleted from ``task_com.apply_definition``.

    It also repairs this function's pre-S-4 premise, which asserted that a foreign principal
    *implies* a stored password "the engine refuses to register one without it". That became
    false the moment a managed service account was registrable, so the MSA arm is checked FIRST
    and the service-account arm keeps only the claim it can still make.

    Applies ONLY to the reconcile path (Settings Save): a blank-password Register via the
    button is a legitimate explicit user choice (the wizard offers it) and never interrupts.
    """
    if password_supplied:
        # Unchanged, and deliberately still first: a supplied password means the re-register
        # stays unattended, so there is no downgrade to interrupt for. It is unreachable beside
        # a recorded MSA from the UI (the disclosure hides and clears that field), and returning
        # ``None`` is the right answer for the inconsistent state anyway — the re-register
        # proceeds and the engine's own refusal is the honest report.
        return None
    account = (registered_foreign_account or "").strip()
    if registered_kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
        # Checked BEFORE the foreign-account arm because an MSA is always foreign, so that arm
        # would otherwise swallow it and coach a password the account does not have. ``account``
        # can only be blank here on a hand-edited record (a blank principal is the signed-in
        # account, which is never an MSA); the copy degrades to naming the kind rather than
        # interpolating an empty string into the middle of a sentence.
        named = account or "a managed service account"
        return DowngradeInterrupt(
            headline=_DOWNGRADE_MSA_HEADLINE,
            detail=_DOWNGRADE_MSA_DETAIL.format(account=named),
            keep_unattended_label=_DOWNGRADE_MSA_KEEP_LABEL.format(account=named),
            signed_in_only_label="",
            keep_next_headline=_DOWNGRADE_MSA_KEEP_NEXT_HEADLINE,
            keep_next_detail=_DOWNGRADE_MSA_KEEP_NEXT_DETAIL,
            offers_signed_in_only=False,
        )
    if account:
        return DowngradeInterrupt(
            headline=_DOWNGRADE_SERVICE_ACCOUNT_HEADLINE,
            detail=_DOWNGRADE_SERVICE_ACCOUNT_DETAIL.format(account=account),
            signed_in_only_label="",
            keep_next_headline=_DOWNGRADE_SERVICE_ACCOUNT_KEEP_NEXT_HEADLINE.format(account=account),
            keep_next_detail=_DOWNGRADE_SERVICE_ACCOUNT_KEEP_NEXT_DETAIL,
            offers_signed_in_only=False,
        )
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
_FOLDERS_SAVED_IN_FLIGHT = (
    "Saved — the nightly schedule is still applying an earlier change and doesn't include this yet. "
    "Save again once it finishes."
)
_SFTP_RECONCILE_DISPATCHED = " Updating the nightly schedule to deliver too…"
_SFTP_RECONCILE_INTERRUPTED = " Confirm the schedule choice above to update the nightly sync."
_SFTP_RECONCILE_BLOCKED = (
    " The nightly schedule wasn't updated — fix the run time in the Daily schedule section, then save again."
)
_SFTP_RECONCILE_IN_FLIGHT = (
    " The nightly schedule is still applying an earlier change and doesn't include delivery yet — "
    "save again once it finishes."
)
# Plan 0046 B — the two causes the pre-0046 BLOCKED strings could not name. Those strings
# hardcode "fix the run time" as the ONLY reason a reconcile can be blocked; a missing
# service-account password is a second, and a refused in-place principal switch is a third, and
# neither is fixed by touching the run time. BLOCKED's own copy is untouched (byte-identical).
_FOLDERS_SAVED_BLOCKED_ACCOUNT = (
    "Saved — the nightly schedule wasn't updated. Check the Windows account and its password in "
    "the Daily schedule section, then save again."
)
_FOLDERS_SAVED_BLOCKED_ACCOUNT_SWITCH = (
    "Saved — the nightly schedule wasn't updated. To run it as a different Windows account, choose "
    "Remove nightly sync in the Daily schedule section, then schedule it again."
)
_SFTP_RECONCILE_BLOCKED_ACCOUNT = (
    " The nightly schedule wasn't updated — check the Windows account and its password in the Daily "
    "schedule section, then save again."
)
# Plan 0049 S-2b.1. A THIRD cause neither of the two above can name, and the reason it needs its
# own string is the whole point of this family: ``BLOCKED_ACCOUNT`` says "check the Windows account
# and its password", which would send an admin to their SERVICE-ACCOUNT credentials over a fault in
# the SpacesEDU DELIVERY password — the same misdirect ``BLOCKED``'s "fix the run time" made, one
# field over. Names the card that owns the remedy, and offers the honest alternative, because the
# password may have been saved by a different Windows account whose store this one cannot read.
_FOLDERS_SAVED_BLOCKED_DELIVERY_SECRET = (  # nosec B105 - the value is a banner, not a credential
    "Saved — the nightly schedule wasn't updated. DistrictSync can't read your saved delivery "
    "password, so it has nothing to give the other account. Re-enter it in the Delivery section "
    "and save again, or turn delivery off to schedule the sync without it."
)
_SFTP_RECONCILE_BLOCKED_DELIVERY_SECRET = (  # nosec B105 - the value is a banner, not a credential
    " The nightly schedule wasn't updated — DistrictSync can't read the saved delivery password "
    "back, so it has nothing to give the other account. Enter it again above and save, or turn "
    "delivery off to schedule the sync without it."
)
_SFTP_RECONCILE_BLOCKED_ACCOUNT_SWITCH = (
    " The nightly schedule wasn't updated — to run it as a different Windows account, choose Remove "
    "nightly sync in the Daily schedule section, then schedule it again."
)


class ReconcileOutcome(Enum):
    """What the shared Settings reconcile actually did with the live nightly task (S3 fix).

    ``DISPATCHED`` — a re-register was genuinely started (the schedule section is now applying the
    new settings). ``INTERRUPTED`` — the downgrade-choice dialog was shown INSTEAD and nothing was
    registered (the admin must confirm first). ``BLOCKED`` — a re-register was needed but the
    register flow early-returned WITHOUT dispatching (its own gate refused — e.g. a malformed run
    time, whose inline error the schedule section paints). ``IN_FLIGHT`` — a register/unregister
    dispatched EARLIER is still applying, so the reconcile must not decide anything from the
    current config (2026-08-31, live install: a delivery Save landed during a registration's UAC
    window, read ``schedule_registered=False`` → "no task", and the in-flight registration then
    confirmed with its pre-delivery args — the nightly ran without ``--sftp`` while the UI said
    delivery was on). ``NONE`` — no reconcile action (no registered task, or no task-baked field
    changed). The two Settings Save sites paint their schedule note from this, so an optimistic
    "updating…" note is never shown when the reconcile merely opened a dialog / hit a gate /
    found an apply already in progress and returned.

    ``BLOCKED_ACCOUNT`` / ``BLOCKED_ACCOUNT_SWITCH`` (plan 0046 B) are the two principal causes
    ``BLOCKED``'s run-time copy could not name: the Windows account or its password is wrong or
    missing, and a live task is on a different principal (which the app REFUSES to re-point in
    place — the admin removes the schedule and creates it again, with the delete and the create
    both in front of them). Added rather than folded into ``BLOCKED`` so today's two strings stay
    byte-identical by construction.

    ``BLOCKED_DELIVERY_SECRET`` (plan 0049 S-2b.1) is the third, for the same reason again:
    delivery is configured but its password cannot be read back, so provisioning has nothing to
    seed the shared store with. It may NOT reuse ``BLOCKED_ACCOUNT`` — that copy says "check the
    Windows account and its password", which points at the service account's credentials rather
    than the delivery password, and a misdirect one field over is still a misdirect.
    """

    DISPATCHED = "dispatched"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    IN_FLIGHT = "in_flight"
    NONE = "none"
    BLOCKED_ACCOUNT = "blocked_account"
    BLOCKED_ACCOUNT_SWITCH = "blocked_account_switch"
    BLOCKED_DELIVERY_SECRET = "blocked_delivery_secret"  # nosec B105 - an enum tag, not a credential


def folders_save_note(outcome: ReconcileOutcome) -> str:
    """The folders/district Save note, honest to what the reconcile actually did (pure, TOTAL).

    ``DISPATCHED`` → the "updating the nightly schedule to match" note; ``INTERRUPTED`` → an honest
    "confirm the schedule choice above" (the dialog is open, nothing is updating yet); ``BLOCKED``
    → an honest "the schedule wasn't updated — fix the run time" (the register flow refused to
    dispatch and painted its inline error); ``IN_FLIGHT`` → an honest "still applying an earlier
    change — save again once it finishes" (the just-saved fields are NOT in the task being
    applied); ``NONE`` (or any unexpected value) → a plain "Saved." The "Saved" prefix is always
    truthful — the folders + district persisted regardless of the schedule reconcile.
    """
    if outcome is ReconcileOutcome.DISPATCHED:
        return _FOLDERS_SAVED_DISPATCHED
    if outcome is ReconcileOutcome.INTERRUPTED:
        return _FOLDERS_SAVED_INTERRUPTED
    if outcome is ReconcileOutcome.BLOCKED:
        return _FOLDERS_SAVED_BLOCKED
    if outcome is ReconcileOutcome.BLOCKED_ACCOUNT:
        return _FOLDERS_SAVED_BLOCKED_ACCOUNT
    if outcome is ReconcileOutcome.BLOCKED_ACCOUNT_SWITCH:
        return _FOLDERS_SAVED_BLOCKED_ACCOUNT_SWITCH
    if outcome is ReconcileOutcome.BLOCKED_DELIVERY_SECRET:
        return _FOLDERS_SAVED_BLOCKED_DELIVERY_SECRET
    if outcome is ReconcileOutcome.IN_FLIGHT:
        return _FOLDERS_SAVED_IN_FLIGHT
    return _FOLDERS_SAVED


def sftp_reconcile_suffix(outcome: ReconcileOutcome) -> str:
    """The clause appended to the "Delivery settings saved" note, honest to the outcome (pure).

    ``DISPATCHED`` → the "updating the nightly schedule to deliver too" clause; ``INTERRUPTED`` →
    an honest "confirm the schedule choice above" prompt (the dialog is open, nothing dispatched);
    ``BLOCKED`` → an honest "the schedule wasn't updated — fix the run time" (the register flow
    refused to dispatch); ``IN_FLIGHT`` → an honest "still applying an earlier change — save again
    once it finishes" (the task being applied was dispatched BEFORE this save and carries no
    ``--sftp``); ``NONE`` (or any unexpected value) → empty (no live task to update, so the base
    stored-note stands alone). Leading space so it appends cleanly to the base sentence.
    """
    if outcome is ReconcileOutcome.DISPATCHED:
        return _SFTP_RECONCILE_DISPATCHED
    if outcome is ReconcileOutcome.INTERRUPTED:
        return _SFTP_RECONCILE_INTERRUPTED
    if outcome is ReconcileOutcome.BLOCKED:
        return _SFTP_RECONCILE_BLOCKED
    if outcome is ReconcileOutcome.BLOCKED_ACCOUNT:
        return _SFTP_RECONCILE_BLOCKED_ACCOUNT
    if outcome is ReconcileOutcome.BLOCKED_ACCOUNT_SWITCH:
        return _SFTP_RECONCILE_BLOCKED_ACCOUNT_SWITCH
    if outcome is ReconcileOutcome.BLOCKED_DELIVERY_SECRET:
        return _SFTP_RECONCILE_BLOCKED_DELIVERY_SECRET
    if outcome is ReconcileOutcome.IN_FLIGHT:
        return _SFTP_RECONCILE_IN_FLIGHT
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
