"""Setup surface — a first-run WIZARD that graduates into a flat SETTINGS page (D8).

VIEW glue (coverage-omitted): the trust-critical *decisions* live in the COUNTED pure
modules — ``setup_flow`` (the wizard state machine: resume, per-step gates, finish copy,
the ``task_args_changed`` reconcile predicate, district auto-select), ``filepicker``
(``setup_state``/path validation), ``setup_gates`` (submit gates), ``schedule_status``
(the tri-state schedule truth), ``sftp_copy`` (Test provenance copy). This file only wires
them to controls.

**The creator surface is its own module** (``screens/creator.py``, plan 0044 S3): this file
HOSTS it — the District step's creator branch and the creator-only "Your files" step both
mount ``build_creator`` and own what happens after each payoff — and imports every creator
constant and helper it needs from there. The dependency runs ONE WAY, wizard → creator, so
S6's Mapping surface can become the second host without dragging the wizard along.

**Two verified-fact refusals live on this surface** (plan 0044 S6 + its review): the
Settings folders card's Save and the wizard's STANDARD District step's Continue both write
``sis_type``, and both consult the pure ``config_editor.activation_allowed`` first, so
neither can switch this install onto a district it set up itself and never tested. Each
refuses whole — nothing written, nothing advanced, nothing re-registered — says so, and
routes to Mapping, where the test conversion lives.

**Two modes, one build entry (``build_setup``):**

* **Wizard mode** — while ``not cfg.has_completed_setup()``: a five-step guided path
  (District → Folders → Delivery → Schedule → Finish) with a "Step N of 5" indicator +
  Back, Enter/Continue gated per step, and focus moved to the new step's first field. The
  Schedule + Delivery steps are **skippable** ("Set up later") and **reconcile** against
  real side effects — a task the read-back already reports LIVE ("already scheduled") and a
  credential already in the keyring ("a delivery password is already saved") are shown
  instead of double-registering. **No step sets ``setup_completed``** — only the explicit
  finish confirmation does, so a mid-wizard abandonment never reads as "set up". Resume
  derives from real state (``setup_flow.derive_flow``), never a stored cursor.
* **Settings mode** — once completed: the flat scroll retitled **"Settings"** with the same
  folders/schedule/SFTP sections, plus **one reconciling Save** — when a task-baked field
  (input/output/district/SFTP flag/run time — ``setup_flow.task_args_changed``) changes and
  a schedule is live, the folders Save re-registers the task through the SAME register flow
  (incl. elevation) so tonight's run uses the new settings. The rail label stays "Setup".
  Save trustworthiness (0034 S3 + W3-C): the reconcile compares ONLY against the durable
  **last-REGISTERED record** (``setup_flow.registered_schedule`` over ``cfg.schedule_task_args``
  + ``cfg.schedule_unattended``, both written on every confirmed register) — never a mount-time
  config snapshot, which stops being a baseline the moment another surface persists a change
  before Setup remounts (a Mapping district switch). **No record ⇒ UNKNOWN, and unknown acts**:
  the Save re-registers rather than reporting a task it never checked as up to date, and the
  blank-password **explicit downgrade choice** fires for an unknown logon type as well as a
  known-unattended one (never silent). A valid run-time edit still persists as config with **no
  task registered**.

The register/unregister flow (Slice 5/6) and the SFTP test/save flow (Slice 7) are **reused
verbatim** in both modes — the wizard's Schedule/Delivery steps embed the SAME section
builders, so there is exactly one register flow and one keyring-write path.

**Password contracts (I1/I3 schedule · I4/I5 SFTP — security-critical):** unchanged from
Slice 5–7. The Windows account password is a handler-LOCAL variable whose only sink is
``register_task(run_as_password=...)`` (DPAPI elevation handshake / child-env — never argv,
never ``cfg``, never a log/message). The SFTP credential's only sinks are
``store_password`` (Save → keyring) and the transient ``test_connection(password_override=)``
(Test → ``client.connect`` only); a failed Test can never clobber a stored credential (D6).
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.authoring import current_digest
from src.scheduler import get_scheduler, windows
from src.scheduler.provision_session import (
    ProvisionAttempt,
    ProvisionOutcome,
    complete_handover,
    delivery_secret_unreadable,
    request_provision,
)
from src.scheduler.task_com import Principal, PrincipalKind
from src.sftp.uploader import LISTING_DENIED_NOTE, SFTPUploader
from src.ui_flet import components, handover_result, tokens
from src.ui_flet.config_editor import (
    CreatorForm,
    activation_allowed,
    has_unsaved_renames,
)
from src.ui_flet.filepicker import (
    ValidationResult,
    setup_state,
    validate_input_dir,
    validate_output_dir,
)
from src.ui_flet.handover_result import HandoverBanner
from src.ui_flet.home_status import machine_scope_line
from src.ui_flet.humanize import friendly_district_name, friendly_sftp_reason
from src.ui_flet.identity_gate import (
    MatchOutcome,
    matched_state,
    stored_identity_domain,
    stored_identity_email,
)

# IMPORTED, not re-typed: `launcher` owns one plain-language cause per bounded
# `MachineScopeRefusedReason` (with a completeness test), and the launcher's own next-launch
# dialog shows the same words — a second copy would drift. No cycle: `launcher` reaches `shell`
# (and therefore this file) only lazily, inside a function.
from src.ui_flet.launcher import _MACHINE_SCOPE_CAUSES
from src.ui_flet.mapping_catalog import (
    FilteredCatalog,
    disambiguated_labels,
    district_domain_index,
    filtered_catalog,
)
from src.ui_flet.picker_field import PickerField
from src.ui_flet.schedule_probe import foreign_task_account
from src.ui_flet.schedule_status import (
    ScheduleState,
    ScheduleStatus,
    interpret_unregister,
    is_transient_location,
)
from src.ui_flet.screens.creator import (
    CREATOR_DISCARDED_NOTE,
    CREATOR_ENTRY_LABEL,
    CREATOR_FINISH_NEEDS_GATE_NOTE,
    FILES_STEP_TITLE,
    CreatorStage,
    build_creator,
    creator_form_for_new,
    creator_form_from_overlay,
    creator_gate_current,
    pending_creator_sis,
)
from src.ui_flet.screens.identity import NOT_LISTED_NOTE_TAIL as UNMATCHED_DISTRICT_NOTE
from src.ui_flet.screens.identity import log_resolve, matched_headline
from src.ui_flet.setup_errors import classify_provision_step, classify_schedule_error
from src.ui_flet.setup_flow import (
    GMSA_IT_DOC_TITLE,
    GMSA_PREREQUISITES,
    GMSA_UNTESTED_CAPTION,
    SCHEDULE_ACCOUNT_FIELD_LABEL,
    SCHEDULE_GMSA_TOGGLE_LABEL,
    TRANSITION_CUE,
    DeliveryFact,
    DowngradeInterrupt,
    FinishSummaryRow,
    FlowInputs,
    FlowMode,
    ReconcileOutcome,
    RegisteredSchedule,
    ScheduleReconcile,
    SetupStep,
    TaskArgs,
    auto_selected_district,
    can_advance,
    default_window_bounds,
    derive_flow,
    downgrade_interrupt,
    finish_copy,
    finish_needs_attention,
    finish_summary_rows,
    folders_save_note,
    is_skippable,
    next_step,
    prev_step,
    registered_schedule,
    run_time_save_decision,
    schedule_delivery_desync,
    schedule_reconcile,
    sftp_reconcile_suffix,
    step_number,
    task_args_changed,
    task_args_from_persisted,
    task_args_to_persisted,
    total_steps,
)
from src.ui_flet.setup_gates import (
    RegisterBlock,
    ScheduleAccountFacts,
    can_register_schedule,
    can_save_sftp,
    principal_key,
    register_block,
    window_settings_valid,
    window_valid_from_config,
)
from src.ui_flet.sftp_copy import (
    PORT_ERROR_DETAIL,
    PORT_ERROR_HEADLINE,
    parse_port,
    sftp_form_differs_from_saved,
    sftp_test_copy,
)
from src.ui_flet.verdict import Verdict
from src.utils import paths
from src.utils.diagnostics import machine_scope_provenance
from src.utils.identity import extract_domain, normalize_email
from src.utils.unc import describe_folder_reach
from src.utils.validators import (
    ALLOWED_SFTP_HOSTS,
    IDENTITY_EMAIL_MAX_LEN,
    validate_identity_email,
    validate_month_day,
    validate_run_time,
)

# Surfaced after a successful registration when the running exe lives in a transient dir
# (Downloads/Temp): pinning a scheduled task there risks the "task fires, exe is gone,
# nothing recorded" blind spot (D4). A warning, not a block — the admin may re-register later.
_TRANSIENT_LOCATION_WARNING = (
    "Heads up: DistrictSync is running from a temporary location (like Downloads or Temp). "
    "If you move or delete it, the nightly sync will stop — move it to a permanent folder "
    "and schedule the nightly sync again."
)

# Calm fallbacks when an off-thread schedule worker itself raises (D5): the spinner + buttons
# must ALWAYS be released, so the worker marshals one of these instead of stranding the UI.
_WORKER_ERROR_REGISTER = "We couldn't set up the nightly sync just now. Please try again."
_WORKER_ERROR_UNREGISTER = "We couldn't remove the nightly sync just now. Please try again."
# The run-as account was refused by ``validate_run_as_user`` INSIDE the engine (it RAISES rather
# than returning a message). A copy-honesty floor, not a crash fix: `except Exception` already
# caught it and reported it as transient, which invites a retry that cannot work. The pre-gate
# uses the SAME validator, so this is drift defence. The exception's own text is never echoed —
# ``validate_run_as_user``'s message interpolates the typed value.
_WORKER_ERROR_ACCOUNT_SHAPE = (
    "Windows wouldn't accept that account name. Check it in the Daily schedule section and try again."
)

# The inline reasons painted beneath the run-as field, so a disabled Register button always has
# a visible cause (INCOMPLETE / RUN_TIME paint nothing here — that is today's behaviour, and the
# run-time error has its own inline slot).
_ACCOUNT_SHAPE_NOTE = (
    "That account name isn't valid. Use the account's Windows name — DOMAIN\\name if it has a "
    "domain. Letters, digits, dots, underscores and hyphens only; no spaces."
)
# The managed-service-account form (plan 0049 S-4). The note above is right for every other
# principal and WRONG for this one in the one way that matters: a gMSA's name MUST end with the
# ``$`` the general charset has no room for, so telling an admin who has just ticked the
# disclosure to drop it would send them round in a circle. Which one paints is decided by the
# DECLARED kind in ``account_block_note``, never by the spelling of what they typed.
_ACCOUNT_SHAPE_NOTE_MSA = (
    "That managed service account name isn't valid. Use DOMAIN\\name$ — the trailing $ is what "
    "makes it a managed service account. Letters, digits, dots, underscores and hyphens only; no "
    "spaces. If you meant an ordinary Windows account, clear the managed service account tick box."
)
# nosec B105 — a constant NAMED "..._PASSWORD_NOTE"; the value is on-screen copy, not a secret.
_ACCOUNT_PASSWORD_NOTE = (  # nosec B105
    "Enter the Windows password for this account below, or clear this box to run the nightly sync "
    "as the account you're signed in with."
)
_ACCOUNT_SWITCH_NOTE = (
    "The nightly sync is already scheduled to run as {recorded}. Choose Remove nightly sync, then "
    "schedule it again with the account you want — what you've typed here stays in the box. "
    "Windows never gives a task's stored password back, so the existing schedule can't be changed "
    "in place."
)
_ACCOUNT_SWITCH_NOTE_UNKNOWN = (
    "A nightly sync is already scheduled and DistrictSync has no record of which account it runs "
    "as. Choose Remove nightly sync, then schedule it again with the account you want — what "
    "you've typed here stays in the box."
)
# nosec B105 — a constant NAMED "..._SECRET_NOTE"; the value is on-screen copy, not a secret.
# The note for ``RegisterBlock.DELIVERY_SECRET_UNREADABLE`` (plan 0049 S-2b.1). It must say TWO
# things, and the second is not optional: the admin may not HAVE the delivery password. It is
# stored per Windows account, so a password saved by a colleague's account lives in a Credential
# Manager this one can never read — an escape that is only discoverable by guessing is not an
# escape, so turning delivery off is named here, with its cost stated plainly.
_ACCOUNT_DELIVERY_SECRET_NOTE = (  # nosec B105
    "DistrictSync can't read your delivery password on this account, so it has nothing to copy "
    "into this computer's shared settings for the nightly sync to use. Open Delivery to SpacesEDU "
    "below and save the password again, then schedule the nightly sync. If you don't have it — it "
    "may have been saved by a different Windows account, which DistrictSync can't read — turn "
    "delivery off instead: the nightly sync will still write your CSV files to the output folder, "
    "it just won't send them to SpacesEDU."
)

# --------------------------------------------------------------------------- #
# Machine-scope handover — the foreshadow, the confirm, the outcomes (0049 S-2b) #
# --------------------------------------------------------------------------- #
#: The shared folder, named in full. An admin asked to approve a permanent, machine-wide change
#: is entitled to know WHERE — and it is the one path this surface prints, deliberately: it is a
#: fixed product location, not a resolved user path.
MACHINE_SCOPE_FOLDER = r"C:\ProgramData\DistrictSync"
#: What lives in the profile, spelled ONCE. The foreshadow and the confirm must not list
#: different contents of the same folder.
_SCOPE_CONTENTS = "your district, folders, delivery password and run history"

# The FORESHADOW (S-2b.2). Painted the moment the typed account goes foreign — `_paint_account_note`
# repaints on every keystroke — because a permanent machine-wide relocation must not first be
# mentioned at the point of no return. Two variants, because both states are real and reachable:
# "Remove nightly sync" deliberately does NOT un-provision (the confirm says so), so an
# already-shared computer genuinely reaches this field again, and telling that admin their
# settings are about to MOVE would be false.
_SCOPE_FORESHADOW_NOTE = (
    f"Scheduling the nightly sync as another account moves this computer's DistrictSync settings — "
    f"{_SCOPE_CONTENTS} — into one shared folder, so that account can read them when it runs. "
    "You'll be asked to confirm first."
)
_SCOPE_FORESHADOW_NOTE_SHARED = (
    "This computer already keeps its DistrictSync settings in one shared folder. Scheduling the "
    "nightly sync as another account gives that account access to it. You'll be asked to confirm "
    "first."
)

# The CONFIRM. Three sentences, and each one is load-bearing:
#   1. WHAT moves (and to where);
#   2. who can then reach these settings — every administrator of this computer, permanently,
#      and nobody who is not an administrator at all;
#   3. that it cannot be undone, INCLUDING by "Remove nightly sync", which is the one thing an
#      admin would reasonably try. Un-provisioning is a ROADMAP item, so the copy says it does
#      not exist rather than implying it does.
SCOPE_CONFIRM_TITLE = "Set this computer up for shared settings?"
_SCOPE_CONFIRM_MOVE = (
    f"DistrictSync will move this computer's settings — {_SCOPE_CONTENTS} — into one shared "
    f"folder, {MACHINE_SCOPE_FOLDER}, so the account you entered can read them when the nightly "
    "sync runs."
)
_SCOPE_CONFIRM_ALREADY = (
    f"This computer already keeps its DistrictSync settings — {_SCOPE_CONTENTS} — in one shared "
    f"folder, {MACHINE_SCOPE_FOLDER}. The account you entered will be given access to it."
)
_SCOPE_CONFIRM_WHO = (
    "Every administrator of this computer will then be able to open DistrictSync and take over "
    "these settings, anyone who is not an administrator of this computer won't be able to open "
    "DistrictSync here at all, and once an administrator has been given access DistrictSync "
    "can't take it away again."
)
_SCOPE_CONFIRM_ONE_WAY = (
    "This version of DistrictSync can't move the settings back, and Remove nightly sync won't "
    "undo it — putting a computer back to per-account settings is something we still have to "
    "build."
)
SCOPE_CONFIRM_CONTINUE_LABEL = "Set up shared settings"
SCOPE_CONFIRM_CANCEL_LABEL = "Cancel"

# The folder-reach WARNING inside the confirm (S-2b.1). It is a warning and never a gate: the
# heuristic is wrong in both directions (`src/utils/unc.py` argues why), so it may not disable
# the confirm and may not be worded as a finding. "We can't confirm" is the whole claim.
SCOPE_FOLDER_WARNING_HEADLINE = "We can't confirm the account can reach these folders"
_SCOPE_FOLDER_WARNING_DETAIL = (
    "A drive letter like Z: only exists for the Windows account that mapped it, and a folder "
    "inside someone's user profile usually isn't readable by anyone else. The nightly sync runs "
    "as the account you entered, so it may not be able to open these."
)
# Said BEFORE the press, not after: swapping a folder re-renders this surface, so a note
# painted afterwards would not survive the thing that proves the swap worked (the folders card
# showing the new path).
_SCOPE_FOLDER_REPLACE_HINT = (
    "Swapping a folder here doesn't schedule anything — choose Schedule nightly sync again when you're ready."
)
SCOPE_FOLDER_INPUT_LABEL = "your input folder"
SCOPE_FOLDER_OUTPUT_LABEL = "your output folder"
SCOPE_FOLDER_REPLACE_LABEL = "Use {path} for {folder}"
SCOPE_FOLDER_REPLACE_FAILED_NOTE = "We couldn't save that folder just now — nothing was changed. Please try again."

# The TERMINAL refusal surface (S-2b.3). The child committed the HKLM switch and the parent's
# own re-pin then refused the folder, so the data-dir pin is left UNSET: every later
# `user_data_dir()` in this session raises, and the admin is sitting in a live window whose next
# click can only crash. This card is therefore paired with disabling the rest of the surface —
# a calm dead end beats a crash, and the next launch reports the same refusal through the
# launcher's own dialog, which is where it can actually be repaired.
SCOPE_REFUSED_HEADLINE = "DistrictSync can't use this computer's shared settings folder"
SCOPE_REFUSED_DETAIL = (
    "The shared folder was created, but DistrictSync will not use it as it stands. Nothing else "
    "on this computer was changed, and your nightly sync was not scheduled. Close DistrictSync "
    "and send the log file from the Help page to support."
)

# The non-handover failure headline for a provisioning attempt that never got as far as the
# switch. It claims nothing about the scope, because nothing happened to it.
SCOPE_ATTEMPT_FAILED_HEADLINE = "Couldn't set this computer up for shared settings"
# The elevation-timeout arm, classified through the SAME canonical the ordinary register path
# uses (`windows._MSG_ELEVATION_TIMEOUT`) rather than a second wording of "we can't tell".
SCOPE_UNCONFIRMED_HEADLINE = "Couldn't confirm the change"
# The child reported the WHOLE sequence done (the nightly included) and the parent's own switch
# read still says this session is not on the shared profile. The task exists — the record was
# written — so the copy leads with that and claims nothing about the scope but "we couldn't
# confirm it". The next launch re-reads the switch and settles it.
SCOPE_SWITCH_UNCONFIRMED_HEADLINE = "Nightly sync scheduled — shared settings not confirmed"
SCOPE_SWITCH_UNCONFIRMED_DETAIL = (
    "Your nightly sync is scheduled to run as the account you entered. DistrictSync couldn't "
    "confirm that this computer switched to shared settings — close DistrictSync, open it again, "
    "and check the line at the top of Settings. If it still doesn't mention shared settings, send "
    "the log file from the Help page to support."
)

# --------------------------------------------------------------------------- #
# The post-handover banner — rendered by HOME, spelled HERE.                   #
# --------------------------------------------------------------------------- #
# It lives in this file because this file owns the flow that produces it, and because the
# dependency only runs one way: ``screens/home.py`` already imports this module, so the copy
# can be single-sourced here and cannot be single-sourced there.
#
# Home renders it because Home is where re-entry LANDS: ``complete_handover``'s ``reenter``
# rebuilds the app body, ``nav.initial_destination_id`` is Home in every state, and the Setup
# surface that dispatched the change no longer exists by the time it finishes. It is the ONE
# deliberate exception to verdict-first, bounded to a single paint — the admin just pressed a
# button that permanently moved this computer's settings, and that outranks "did last night's
# roster sync?" until it has been read once. ``handover_result.take()`` clears, so the next
# mount is verdict-first again.
HANDOVER_DONE_HEADLINE = "Shared settings are set up"
HANDOVER_DONE_DETAIL = (
    "This computer now keeps DistrictSync's settings in one shared folder, and the nightly sync "
    "is scheduled to run as the account you entered."
)
# The scope change LEADS, and the registration failure is the SECOND sentence. A failed
# register painting its own red card over a successful, irreversible handover is how an admin
# comes to retry a move that cannot be repeated.
HANDOVER_UNCONFIRMED_HEADLINE = "Shared settings are set up — the nightly sync isn't confirmed"
HANDOVER_UNCONFIRMED_LEAD = (
    "This computer now keeps DistrictSync's settings in one shared folder. That part is done and "
    "does not need repeating."
)


def handover_banner(result: handover_result.HandoverResult) -> ft.Control:
    """The one-shot post-handover surface. TOTAL over :class:`HandoverBanner`.

    ``HandoverBanner.REFUSED`` is normally painted on the Setup surface instead — that outcome
    leaves ``complete_handover`` returning BEFORE re-entry, so Setup is still mounted — but the
    branch exists here anyway: a result that somehow reached the slot must be shown, never
    silently dropped, and this function is the only renderer.

    The refusal's CAUSE is ``launcher._MACHINE_SCOPE_CAUSES`` — the map that already owns one
    plain-language sentence per bounded reason, and the same words the launcher's own
    next-launch dialog uses. An unknown member (impossible today; the launcher has a
    completeness test) drops the clause rather than rendering an enum value.
    """
    if result.banner is HandoverBanner.HANDED_OVER:
        return components.HealthVerdictBanner(
            Verdict.HEALTHY, headline=HANDOVER_DONE_HEADLINE, detail=HANDOVER_DONE_DETAIL
        )
    if result.banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED:
        return components.HealthVerdictBanner(
            Verdict.WARNING,
            headline=HANDOVER_UNCONFIRMED_HEADLINE,
            detail=f"{HANDOVER_UNCONFIRMED_LEAD} {result.detail}".strip(),
        )
    return components.ErrorCard(SCOPE_REFUSED_HEADLINE, _scope_refusal_detail(result.refused_reason))


def _scope_refusal_detail(reason: paths.MachineScopeRefusedReason | None) -> str:
    """The refused-re-pin sentence: the bounded cause, then what is and is not true."""
    cause = _MACHINE_SCOPE_CAUSES.get(reason, "") if reason is not None else ""
    return f"{cause} {SCOPE_REFUSED_DETAIL}".strip()


def account_block_note(block: RegisterBlock, facts: ScheduleAccountFacts) -> str:
    """The ONE wording per Register-gate reason (pure, TOTAL; ``""`` for the silent ones).

    Read by BOTH the inline note under the account field and the card a refused attempt paints
    into ``result_slot``, so the two surfaces state the same cause by construction rather than
    by review. ``NONE``/``INCOMPLETE``/``RUN_TIME`` return ``""`` — the folders card and the
    run-time field own those, and ``INCOMPLETE`` is deliberately silent here.

    **A new ``RegisterBlock`` member with no branch here paints a dead control** — a disabled
    primary with no visible cause. That is why this is module level rather than a closure
    inside ``_build_schedule_section``: the completeness sweep in
    ``tests/test_ui_flet_machine_scope_handover.py`` can only reach it from out here.
    """
    if block is RegisterBlock.ACCOUNT_SHAPE:
        # 0049 S-4: forked on the DECLARED kind, because the two charsets disagree on exactly
        # the one character the admin has to get right.
        if facts.kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
            return _ACCOUNT_SHAPE_NOTE_MSA
        return _ACCOUNT_SHAPE_NOTE
    if block is RegisterBlock.ACCOUNT_NEEDS_PASSWORD:
        return _ACCOUNT_PASSWORD_NOTE
    if block is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE:
        recorded = facts.recorded
        return (
            _ACCOUNT_SWITCH_NOTE_UNKNOWN
            if recorded is None
            else _ACCOUNT_SWITCH_NOTE.format(recorded=recorded or facts.current or _keyring_owner_account())
        )
    if block is RegisterBlock.DELIVERY_SECRET_UNREADABLE:
        return _ACCOUNT_DELIVERY_SECRET_NOTE
    return ""


def _machine_scope_now() -> bool:
    """``paths.is_machine_scope()``, made TOTAL for a paint path.

    It RESOLVES the pin when one is not set, so it raises ``MachineScopeRefused`` on exactly
    the state this slice can produce: a committed switch whose folder the app refuses. Display
    copy may never take a surface down, and the clause is broad because the failures here are
    not all ``OSError`` (a Windows-only import on a platform-patched test is an ``ImportError``
    — see the note in ``src/utils/unc.py``). ``False`` is the safe answer: it foreshadows the
    MOVE, which over-states nothing an admin has to act on.
    """
    try:
        return paths.is_machine_scope()
    except Exception:  # noqa: BLE001 - see the docstring; a note may never break the surface
        return False


#: A provisioning outcome that is not ``PROVISIONED``, ``REFUSED`` or ``FAILED`` maps onto the
#: SAME canonical the ordinary register path produces for the same event, so ``setup_errors``
#: classifies it through the branches that already exist rather than through a second
#: vocabulary. ``UNAVAILABLE`` is the one without an elevation canonical — the handshake was
#: never built — and takes the section's calm transient instead.
_PROVISION_OUTCOME_CANONICAL: dict[ProvisionOutcome, str] = {
    ProvisionOutcome.DECLINED: windows._MSG_UAC_DECLINED,
    ProvisionOutcome.LAUNCH_FAILED: windows._MSG_ELEVATION_LAUNCH_FAILED,
    ProvisionOutcome.UNCONFIRMED: windows._MSG_ELEVATION_TIMEOUT,
    ProvisionOutcome.DIFFERENT_ACCOUNT: windows._MSG_DIFFERENT_ACCOUNT,
}
# The headline over a REFUSED attempt (2026-09-17). Deliberately says what did not happen and
# claims nothing about whether a task exists: the account-switch refusal renders over a nightly
# sync that IS scheduled, so "couldn't schedule the nightly sync" would contradict the readout
# right above it. The DETAIL is always one of the three notes above — the refusal never gets a
# second wording of a reason the field note already gives.
_REGISTER_REFUSED_HEADLINE = "We didn't make that change"
# Owner decision 2 (2026-09-16): the one-time ``--sftp-configure`` step is named HERE as well as
# in the partner guide. Credential Manager has no cross-user scope, so a delivery password stored
# by the admin is STRUCTURALLY invisible to a task running as the service account — the single
# most likely thing to break a district's nightly delivery, and an admin mid-setup does not have
# the guide open. No email address (scripts/check_no_emails.py scans every tracked file; the
# address lives on the Help page).
_SERVICE_ACCOUNT_DELIVERY_NOTE = (
    "Delivery passwords are stored per Windows account, so this account needs its own copy. Sign "
    "in as it once (or use Windows' Run as different user) and run DistrictSync with "
    "--sftp-configure. Your DistrictSync setup guide has the full steps; the Help page has our "
    "support contact. DistrictSync can't do this for you, and the nightly delivery will fail "
    "until it's done."
)
# The sibling for the case where scheduling itself will solve it (plan 0049 S-2a.3). The note
# above is keyed on WILL-PROVISION, never on today's scope: it renders while the admin is TYPING
# the service account, on an install that is still per-user precisely because provisioning fires
# at the Schedule press. Keyed on current scope it would say "DistrictSync can't do this for you,
# run --sftp-configure" seconds before the app does exactly that.
#
# It deliberately does NOT contain the ``--sftp-configure`` marker: that literal is how the tests
# tell the two forms apart, and a sibling that carried it would let a rendering assertion pass on
# either. No email address (scripts/check_no_emails.py scans every tracked file).
_SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE = (
    "DistrictSync will save your delivery password on this computer so this account can read it "
    "when the nightly sync runs. You won't need to sign in as it or set delivery up again. Your "
    "DistrictSync setup guide has the full steps; the Help page has our support contact."
)


def service_account_delivery_note(*, delivery_enabled: bool, foreign: bool, will_provision: bool) -> str:
    """Which delivery note a service-account principal earns — ``""`` for none (pure, TOTAL).

    Named only where it is TRUE and actionable: delivery is ON and a service account is in play on
    either side (typed now, or already registered). The WILL-PROVISION form replaces the manual
    one whenever pressing Schedule would set the credential up — never on a scope reading, which
    is still per-user at the moment the note is painted.
    """
    if not delivery_enabled or not foreign:
        return ""
    return _SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE if will_provision else _SERVICE_ACCOUNT_DELIVERY_NOTE


# The finish line's save FAILED (0038 S6). Two things must be true of this line and neither is
# decoration: it must not claim THIS save lost anything (it did not — the only field it adds is
# the completion flag, and the two steps that persist the admin's own answers, District and
# Folders, save inside `_forward` BEFORE advancing, so a failure there leaves them on the step
# rather than walking them past it), and it must leave the admin somewhere they can act. The
# summary they just earned STAYS on screen with this note beneath the Finish button, so pressing
# it again is the whole retry. What must never happen is the wizard re-deriving its resume step
# from a config that still says "unfinished" and dropping them back at step 1 — a bounce that
# would read as "it undid my setup".
#
# Scope this deliberately does NOT claim: the SKIPPABLE steps' own section-level saves (the
# seasonal-window write and the delivery write) are unguarded and do not block advancement, so
# under the same fault the admin can reach this note with those entries unpersisted. Tracked in
# `docs/claugentic-ROADMAP.md` ("Spotted during S6") — the fix is a step-level note, not a
# reword of the copy below.
FINISH_SAVE_FAILED_NOTE = "We couldn't save your settings just now — nothing was lost. Please try again."

logger = logging.getLogger(__name__)


def _kick_probe_thread(page: ft.Page, work: Callable[[], None]) -> None:  # pragma: no cover - view glue
    """Dispatch the schedule-readout probe worker off the UI thread.

    Module-level SEAM (not a closure) so tests can stub the kick itself and run with
    NO background probe thread at all: the readout probe is paint-then-refine glue,
    never load-bearing for a test assertion, and an unstubbed kick raced the
    render-smoke assertions (the 2026-07-15 flake). Behaviour is unchanged — the
    suppress matches the old inline call (a dead page must never crash the build).
    """
    with contextlib.suppress(Exception):
        page.run_thread(work)


# The ONE inline run-time error (shared by the register flow and the Settings-Save run-time
# persist, 0034 S3-b — single source so the two paths can never drift).
_RUN_TIME_ERROR_HEADLINE = "That run time isn't valid"
_RUN_TIME_ERROR_DETAIL = "Enter the time as HH:MM in 24-hour form, e.g. 03:00."

# The inline seasonal-window error (B): shown when the window is ON but a bound isn't a real
# month-day. Plain-language, no jargon — mirrors the run-time error's shape.
_WINDOW_ERROR = "Enter each date as MM-DD (month then day), e.g. 08-11."

# The A9 limitation, SURFACED not solved (plan 0046 C). ``src/main.py``'s nightly gate evaluates
# the seasonal window against ``AppConfig.load()`` — the config of the account the task RUNS as. A
# service account has no DistrictSync profile, so ``sync_window_enabled`` is the dataclass default
# ``False`` there and the window is simply never enforced: the sync keeps running all summer. The
# note states that plainly and names the ONE remedy that actually works today (remove the nightly
# schedule for the break); it never implies DistrictSync handles the pause for a foreign principal.
# The real fix — a shared/machine-scope profile — is on the ROADMAP, deliberately not built here.
SYNC_WINDOW_FOREIGN_NOTE = (
    "Your summer pause won't apply while the nightly sync runs as {account}. The pause is stored "
    "with your own Windows account, and the sync reads the settings of the account it runs as — so "
    "it will keep running through the break. To pause it, remove the nightly schedule for the summer."
)


# The SHARED-PROFILE sibling (plan 0049 S-2a.2). On a machine-scoped install the A9 limitation is
# GONE: ``src/main.py``'s nightly gate resolves the same shared ``config.json`` this window was
# saved to, so the pause the admin set here is the pause the service account's run obeys.
#
# The claim is POSITIVE, which is exactly why this note — unlike its per-user sibling — needs the
# schedule state. "Your pause applies to the nightly sync running as X" over a task Windows says is
# GONE, or over one we could not read, would assert a nightly that may not exist; CLAUDE.md's own
# rule is that a confirmed-MISSING schedule outranks the pause. So it renders on a CONFIRMED-LIVE
# read-back and on nothing else.
SYNC_WINDOW_SHARED_NOTE = (
    "Your summer pause applies to the nightly sync running as {account}. This computer's "
    "DistrictSync settings are shared, so the nightly reads the same pause you set here."
)


def sync_window_foreign_note(app_config: AppConfig, *, foreign_account: str) -> str | None:
    """The A9 limitation, stated only when it is BOTH enabled here AND unenforceable there.

    Returns ``None`` on every other combination — a limitation nobody has configured into is noise,
    and a note on an install with no foreign principal would be simply false. Pure and TOTAL.

    Scoped to a PER-USER install: :func:`window_scope_note` is the one entry point, and it hands a
    machine-scoped install to :func:`sync_window_shared_note` instead. The limitation this states
    is real there no longer, so this string must never render on one.
    """
    if not foreign_account:
        return None
    if not app_config.sync_window_enabled:
        return None
    return SYNC_WINDOW_FOREIGN_NOTE.format(account=foreign_account)


def sync_window_shared_note(app_config: AppConfig, *, foreign_account: str, state: ScheduleState | None) -> str | None:
    """The shared-profile reassurance — stated ONLY over a schedule we have confirmed LIVE.

    Pure and TOTAL. ``None`` unless the window is enabled here, the recorded principal is foreign,
    AND the read-back confirmed the task exists: a MISSING task will not resume in the fall and an
    UNKNOWN one was never seen, so neither may carry a claim about what "the nightly sync running
    as X" does. ``state=None`` (not yet probed) asserts nothing either.
    """
    if not foreign_account:
        return None
    if not app_config.sync_window_enabled:
        return None
    if state is not ScheduleState.LIVE:
        return None
    return SYNC_WINDOW_SHARED_NOTE.format(account=foreign_account)


def window_scope_note(
    app_config: AppConfig,
    *,
    foreign_account: str,
    shared_records: bool,
    state: ScheduleState | None,
) -> str | None:
    """The ONE entry point for the seasonal-window principal note (pure, TOTAL).

    Exactly one of the two siblings can fire, keyed on whether this install's profile is SHARED:
    per-user keeps A9's limitation byte-identical, machine scope states the reassurance that
    replaced it. Neither can ever render on the other's install, which is the whole point of
    routing both through one function rather than two call sites.
    """
    if shared_records:
        return sync_window_shared_note(app_config, foreign_account=foreign_account, state=state)
    return sync_window_foreign_note(app_config, foreign_account=foreign_account)


# Plain-language titles for the wizard steps (the "Step N of M · <title>" indicator).
_STEP_TITLES: dict[SetupStep, str] = {
    SetupStep.FOLDERS: "Choose your folders",
    SetupStep.DISTRICT: "Choose your district",
    SetupStep.SCHEDULE: "Set a nightly schedule",
    SetupStep.DELIVERY: "Set up delivery",
    # The creator-only gate step (plan 0044 S3) — titled from the ONE constant, which lives
    # in ``screens/creator.py`` with the rest of the creator copy it belongs to.
    SetupStep.FILES: FILES_STEP_TITLE,
    # #5: the step title is a neutral marker so the adaptive banner headline owns the peak moment
    # (avoids stacking "You're all set" twice — step title + banner).
    SetupStep.FINISH: "Finish",
}


def _schedule_readout_line(status: ScheduleStatus) -> ft.Control:
    """A one-line live readout of the REAL schedule state — styled by ATTENTION, not state (finding #3).

    A first-run install's Schedule step is legitimately MISSING ("not set up yet"); painting that
    red/error screams "broken" for a normal not-yet state. So the failed styling is reserved for
    ``attention`` (an expected-but-gone schedule, or a fired-but-no-record contradiction); a calm
    MISSING reads muted/neutral, LIVE reads green, UNKNOWN reads muted.
    """
    if status.attention:
        color, icon = tokens.color_status_failed, ft.Icons.ERROR_OUTLINE_ROUNDED
    elif status.state is ScheduleState.LIVE:
        color, icon = tokens.color_status_healthy, ft.Icons.CHECK_CIRCLE_ROUNDED
    elif status.state is ScheduleState.MISSING:
        color, icon = tokens.color_muted, ft.Icons.EVENT_BUSY_ROUNDED  # calm "not set up yet"
    else:  # UNKNOWN
        color, icon = tokens.color_muted, ft.Icons.HELP_OUTLINE_ROUNDED
    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Icon(icon, size=18, color=color), ft.Text(status.detail, size=13, color=color)],
    )


def _finish_summary_row_control(row: FinishSummaryRow) -> ft.Control:  # pragma: no cover - Flet view glue
    """One checked-summary row: a green ✓ for a configured step, else a subdued "later" cue.

    Uses M3 icons (never raw emoji): ``CHECK_CIRCLE_ROUNDED`` (healthy) for done, a muted
    ``PENDING_OUTLINED`` for a deferred skippable step. The whole deferred row reads subdued so an
    honest "you can do this later" never looks like a failure.
    """
    if row.done:
        icon, icon_color, detail_color = (
            ft.Icons.CHECK_CIRCLE_ROUNDED,
            tokens.color_status_healthy,
            tokens.color_text,
        )
    else:
        icon, icon_color, detail_color = ft.Icons.PENDING_OUTLINED, tokens.color_muted, tokens.color_muted
    return ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(icon, size=20, color=icon_color),
            ft.Text(row.label, size=14, weight=ft.FontWeight.W_700, color=tokens.color_text),
            ft.Text(row.detail, size=14, color=detail_color),
        ],
    )


def _finish_summary_card(rows: list[FinishSummaryRow]) -> ft.Control:  # pragma: no cover - Flet view glue
    """The honest checked-summary card — one row per input step, configured-vs-deferred (no confetti)."""
    return components.card(
        content=ft.Column(
            spacing=14,
            controls=[
                ft.Text("Here's what you set up", size=16, weight=ft.FontWeight.W_800, color=tokens.color_text),
                *[_finish_summary_row_control(row) for row in rows],
            ],
        )
    )


# The District step's instruction line, in its two shapes (0038 S5). The default INSTRUCTS a
# pick; when the step opens with a value already chosen for the admin, instructing them to
# "pick" a choice that has been made reads as though nothing happened — and quietly hides the
# fact that we made it. The acknowledging form names the district, says WHERE the guess came
# from (a public email domain, not a lookup of them), and puts the correction in the same
# breath. That is what keeps it a correctable pre-selection rather than a silent default.
DISTRICT_PICK_PROMPT = (
    "Pick the district whose MyEd BC layout matches your extract. You can switch it later from the Mapping tab."
)


def district_auto_seeded_note(district: str) -> str:
    return f"We've picked {district} from your email's domain — change it if that's wrong."


# ---- the verified-fact refusal on the wizard's District step (S6 review) - #
#: The FIFTH writer of ``sis_type`` (plan 0044 S6 review, BLOCKING 2): this step's Continue
#: persists the district, and the finish line then bakes it into the nightly task — so a
#: district set up on THIS computer that has not passed a test conversion as it now reads is
#: refused here too. Same wording pattern as :data:`FOLDERS_NEEDS_TEST_NOTE`: the OUTCOME
#: first ("nothing was saved"), then the reason, then the one act that fixes it. Structural —
#: no district name, no path, no digest. It names Mapping because that is where the test
#: conversion lives (and where the setup of a district added here can be changed); the rail
#: carries Mapping in every state, D7, so the note alone is never a dead end.
WIZARD_DISTRICT_NEEDS_TEST_NOTE = (
    "Nothing was saved. This mapping was set up on this computer, and it hasn't passed a "
    "test conversion as it now reads. Run one under Mapping, then come back and continue."
)


def _district_catalog(cfg: AppConfig, *, picked_sis: str = "") -> FilteredCatalog:
    """The district rows THIS admin should see — the one choke point, per mount (0038 S5).

    Scoped by the stored identity's DOMAIN when one is on file and it matches a district;
    otherwise the full list. The saved ``sis_type`` AND the working ``picked_sis`` both ride
    every result unconditionally, so neither the district this install converts nor the one
    the admin has just chosen can vanish from the picker that edits it. TOTAL — any failure
    inside the catalog degrades to the full, unfiltered list.

    The scoping is unconditional since 2026-08-04 (the per-surface show-all row retired);
    an admin who needs the full list clears the stored address in the identity section a
    few rows up this same scroll.
    """
    return filtered_catalog(
        stored_identity_domain(cfg),
        saved_sis=cfg.sis_type,
        picked_sis=picked_sis,
    )


def _district_options(catalog: FilteredCatalog) -> list[ft.dropdown.Option]:
    """SIS/district dropdown options — id keyed, the disambiguated display text shown (RC2).

    Labels come from ``disambiguated_labels`` rather than ``friendly_district_name`` directly:
    two rows that read identically make the highest-consequence wrong click in this product a
    coin flip, so any residual collision (a partner-authored YAML we do not control) carries
    its raw config id.
    """
    labels = disambiguated_labels(catalog.summaries)
    return [ft.dropdown.Option(key=s.sis_type, text=labels[s.sis_type]) for s in catalog.summaries]


def _district_window_defaults(cfg: AppConfig) -> tuple[str, str]:  # pragma: no cover - Flet view glue (I/O)
    """Pre-fill the seasonal-window bounds from the chosen district's academic calendar (B).

    Loads the district config (the same ``load_config`` path ``mapping_catalog`` uses) and feeds
    ``global_config.academic_start_month_day`` / ``academic_end_month_day`` to the COUNTED pure
    ``default_window_bounds``. TOTAL: no district chosen yet, or an unreadable/dateless config →
    the plain fallback (``default_window_bounds(None, None)``), never a crash.
    """
    try:
        from src.config.loader import load_config

        gc = load_config(cfg.sis_type).global_config
        return default_window_bounds(gc.academic_start_month_day, gc.academic_end_month_day)
    except Exception:  # noqa: BLE001 - total: any load failure falls back to the plain default bounds
        return default_window_bounds(None, None)


def _folders_valid(input_dir: str, output_dir: str) -> bool:
    """Both the input and output folders pass the boundary validators (the folders-step gate)."""
    return validate_input_dir(input_dir).ok and validate_output_dir(output_dir).ok


def _stored_delivery_present(cfg: AppConfig) -> bool:
    """Whether a delivery credential already sits in the keyring for the saved host/user (reconcile).

    A cheap synchronous keyring read (guarded — a blank/out-of-allowlist host makes the
    ``SFTPUploader`` construction raise, which we treat as "no stored credential"). Used to
    reconcile the Delivery step to "a delivery password is already saved" instead of forcing a
    fresh test, and to seed the wizard's ``DeliveryFact`` on resume.
    """
    if not (cfg.sftp_host and cfg.sftp_username):
        return False
    try:
        uploader = SFTPUploader(
            cfg.sftp_host, int(cfg.sftp_port or 22), cfg.sftp_username, cfg.sftp_remote_path or "/files"
        )
        return bool(uploader.get_stored_password())
    except Exception:  # noqa: BLE001 - any keyring/construction failure → "no stored credential"
        return False


@dataclass
class _ScheduleHandle:
    """A thin handle the Settings reconcile uses to drive the schedule section's register flow.

    ``trigger_register`` is the RECONCILE entry (0034 S3-a): unlike the Register button's
    direct handler, it first checks ``downgrade_interrupt`` and pauses on the explicit-choice
    dialog when a blank-password re-register would silently downgrade an unattended task. It
    returns a ``ReconcileOutcome`` — ``DISPATCHED`` when a re-register actually started,
    ``INTERRUPTED`` when the choice dialog was shown instead, ``BLOCKED`` when the register
    flow early-returned without dispatching (e.g. an invalid run time, whose inline error it
    paints) — so the Save sites can paint an honest note (never "updating…" when nothing was
    registered).
    ``BLOCKED_ACCOUNT`` / ``BLOCKED_ACCOUNT_SWITCH`` (0046 B) are the two principal refusals —
    the Windows account or its password, and a live task on a different principal, which the
    app never re-points in place.
    ``run_as_user_value`` is the live run-as field, so the reconcile compares the PENDING
    principal against the recorded one (a principal move with unchanged task args is still a
    re-register). ``persist_run_time`` is the S3-b seam: persist a valid run-time edit as plain
    config when no schedule is registered (invalid → the section's inline error, nothing
    persisted).
    ``last_schedule_state`` is the last CONFIRMED read-back (``None`` until one lands), which
    the delivery section reads for the four-form password line (0049 S-2a.3): its
    "no nightly sync is scheduled right now" arm may fire on a CONFIRMED-MISSING task and on
    NOTHING else — an UNKNOWN read-back (a probe timeout, access denied, a task registered
    elevated and unreadable by a filtered token) is not an absence, and rendering it as one
    would tell an admin their nightly is gone on the evidence that we could not look.
    ``is_busy`` reports whether a register/unregister dispatched EARLIER is still applying
    (its UAC prompt/worker is in flight) — the reconcile must return ``IN_FLIGHT`` then,
    because ``schedule_registered`` and the durable record describe the PRE-dispatch world
    (2026-08-31 race: a delivery Save during a registration's UAC window read "no task",
    and the registration then confirmed with its pre-delivery args).
    """

    trigger_register: Callable[[], ReconcileOutcome]
    run_time_value: Callable[[], str]
    run_as_user_value: Callable[[], str]
    persist_run_time: Callable[[], bool]
    is_busy: Callable[[], bool]
    last_schedule_state: Callable[[], ScheduleState | None]


# --------------------------------------------------------------------------- #
# Entry: wizard while not completed, else the flat Settings scroll.            #
# --------------------------------------------------------------------------- #
def build_setup(
    page: ft.Page,
    *,
    on_schedule_changed: Callable[[], None] | None = None,
    on_complete: Callable[[], None] | None = None,
    on_navigate: Callable[[str], None] | None = None,
    on_reenter: Callable[[], None] | None = None,
) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Setup surface — the first-run wizard, or the Settings page once completed.

    ``on_schedule_changed`` (0032 T1 #8, shell-owned): fired after a CONFIRMED
    register/unregister success so the shell can re-probe the rail's Setup attention
    badge (probed once at boot, so it would otherwise stay stale until a restart).
    Defensive ``None`` default — every caller without a badge to refresh is unchanged.

    ``on_complete`` (0038 S6) is the HOST seam: fired after the finish line's save is
    VERIFIED, so a host that owns the surrounding surface can take the admin somewhere the
    wizard cannot reach on its own (Home re-renders into its health view). It is fired
    INSTEAD of the in-place Settings graduation, never as well — two payoffs for one press
    would leave a Settings scroll flashing under a screen that is being replaced.

    With ``on_complete=None`` — the Setup RAIL item — the finish line behaves exactly as it
    always has: `_mount_settings(..., transition_cue=True)` in place. That equivalence is
    the point of the seam and is pinned by a test, because a rail item and a hosted wizard
    that quietly diverge are two wizards.

    ``on_navigate`` (plan 0044 S6, the shell's rail-follow lambda — Mapping/Convert/Home's
    exact pattern) is the ONE route out of this surface, for BOTH of its verified-fact
    refusals: the folders card's Save and — since the S6 review's BLOCKING 2 — the wizard
    District step's Continue can each be REFUSED for a district set up on this computer that
    has not passed a test conversion as it currently reads, and the test lives on Mapping.
    ``None`` (the default, and what Home's wizard host passes) renders each refusal note
    with no button rather than a dead one; the rail still carries Mapping either way, so a
    note alone is never a dead end.

    ``on_reenter`` (plan 0049 S-2b.3) rebuilds the whole app body and lands on Home. Only the
    shell can do that, so it is injected, and it is what ``complete_handover`` is handed after
    a machine-scope handover re-points the profile mid-session. Absent (the default) the
    handover's banner is painted on THIS surface instead of parked for a rebuild that will
    never happen — never a dead affordance, the same rule ``on_navigate`` follows above.
    """
    cfg = AppConfig.load()
    root = ft.Column(spacing=22)
    if not cfg.has_completed_setup():
        _mount_wizard(
            page,
            cfg,
            root,
            on_schedule_changed=on_schedule_changed,
            on_complete=on_complete,
            on_navigate=on_navigate,
            on_reenter=on_reenter,
        )
    else:
        _mount_settings(
            page,
            cfg,
            root,
            transition_cue=False,
            on_schedule_changed=on_schedule_changed,
            on_navigate=on_navigate,
            on_reenter=on_reenter,
        )
    return root


# --------------------------------------------------------------------------- #
# Wizard mode (D8).                                                            #
# --------------------------------------------------------------------------- #
def _mount_wizard(
    page: ft.Page,
    cfg: AppConfig,
    root: ft.Column,
    *,
    on_schedule_changed: Callable[[], None] | None = None,
    on_complete: Callable[[], None] | None = None,
    on_navigate: Callable[[str], None] | None = None,
    on_reenter: Callable[[], None] | None = None,
) -> None:  # pragma: no cover - Flet view glue
    """Render the first-run wizard into ``root`` (resume derived from real state).

    Two shapes, ONE mount (plan 0044 S3): the shipped five-step STANDARD walk, and the
    six-step CREATOR walk an admin takes while a self-service district of their own is still
    being set up. The mode is decided FIRST, from real state — a pending resume token whose
    overlay is actually on disk — because it decides the step tuple every later derivation
    reads (``step_order(mode)``), and because the D9 auto-seed must not fire inside it.
    """
    # The creator resume decision, before anything else derives from it. A token whose overlay
    # is GONE (an admin deleted the file by hand, or a discard's settings write was refused)
    # self-heals: the token is cleared and the standard walk resumes, rather than opening a
    # six-step flow around a district that does not exist.
    creator_sis = pending_creator_sis(cfg)
    mode: FlowMode = "creator" if creator_sis else "standard"

    # Shared mutable wizard state. Folders/district selections mirror the config; the schedule
    # status + delivery fact are the injected verification results the finish line + resume read.
    ws: dict[str, object] = {
        "step": SetupStep.DISTRICT,  # placeholder — overwritten by derive_flow(...).resume_step below
        "input": cfg.input_dir,
        "output": cfg.output_dir,
        # PERSISTED value only — the auto-selection is applied AFTER the resume derivation
        # below, deliberately. See the comment there.
        "sis": cfg.sis_type,
        "auto_seeded": False,  # set below iff D9's seed actually fired (drives the step's caption)
        # The District step's verified-fact refusal (plan 0044 S6 review, BLOCKING 2) — a
        # fact about the LAST Continue press, cleared the moment the pick changes.
        "district_refused": False,
        "schedule_skipped": False,
        "schedule_status": None,  # latest ScheduleStatus from the section's read-back
        "window_valid": True,  # the seasonal-window gate (B): enabled+invalid closes Continue
        "schedule_busy": False,  # a register/unregister is in flight (its UAC prompt is up)
        "delivery": DeliveryFact.STORED_CRED_PRESENT if _stored_delivery_present(cfg) else DeliveryFact.NONE,
        "delivery_host": cfg.sftp_host,
        "delivery_user": cfg.sftp_username,
        "forward_btn": None,  # the current step's forward button (re-gated in place on input change)
        "finish_error": "",  # set ONLY when the finish line's save raised (0038 S6)
        "finishing": False,  # the finish press is latched until it fails (0038 S6)
        # ---- creator mode (plan 0044 S3) ---------------------------------- #
        "mode": mode,
        "creator_sis": creator_sis,  # "" until the first overlay write
        "creator_form": creator_form_from_overlay(creator_sis) if creator_sis else None,
        "creator_note": "",  # a one-surface note (token refused / discarded), cleared on the next hop
        # The FILES gate fact is I/O (the overlay + the resolved digest), so it is probed once
        # and memoised per act rather than on every footer re-gate. ``None`` = not yet probed.
        "files_ok": None,
        # The filename form's pending rename map, HELD HERE and mutated in place by the
        # creator surface (plan 0044 S4 review, BLOCKING 2). It has to outlive a re-mount:
        # this wizard rebuilds the step body on every hop, and a surface that forgot what
        # was picked while this footer's Continue stayed open advanced past unsaved names.
        "files_pending": {},
        # The FILES footer's locked-Continue caption, created lazily by ``_files_lock_note``
        # and FILLED by the creator surface (never a second computation of the same reason).
        "files_lock_note": None,
    }

    def _files_step_satisfied() -> bool:
        """The injected "Your files" fact: the overlay exists AND what was tested still matches.

        Probed at most once per act (write / activate / discard / render), because
        ``current_digest`` loads and validates the resolved config — ``_inputs()`` is called
        several times per render and standard mode must pay nothing at all for this.
        """
        if ws["mode"] != "creator":
            return False
        if ws["files_ok"] is None:
            ws["files_ok"] = creator_gate_current(cfg, str(ws["creator_sis"]))
        return bool(ws["files_ok"])

    def _creator_activated() -> bool:
        """``sis_type`` IS this district and the resume token is cleared (the activation fact)."""
        sis = str(ws["creator_sis"]).strip()
        return bool(sis) and cfg.sis_type == sis and not cfg.creator_pending_sis.strip()

    def _inputs() -> FlowInputs:
        return FlowInputs(
            folders_valid=_folders_valid(str(ws["input"]), str(ws["output"])),
            district_chosen=bool(str(ws["sis"]).strip()),
            schedule=ws["schedule_status"],  # type: ignore[arg-type]
            schedule_skipped=bool(ws["schedule_skipped"]),
            delivery=ws["delivery"],  # type: ignore[arg-type]
            window_valid=bool(ws["window_valid"]),
            schedule_busy=bool(ws["schedule_busy"]),
            mode=ws["mode"],  # type: ignore[arg-type]
            # A creator's district is CHOSEN once their overlay is on disk — a creator picks no
            # bundled district, so the standard fact could never satisfy the step for them.
            creator_district_chosen=bool(str(ws["creator_sis"]).strip()),
            files_step_satisfied=_files_step_satisfied(),
            creator_activated=_creator_activated(),
        )

    # Resume: land on the first step real state says is unsatisfied (no stored cursor).
    ws["step"] = derive_flow(_inputs()).resume_step

    # ...and ONLY THEN pre-select the auto-selected district. Order is load-bearing (0038 S5):
    # `derive_flow` reads `district_chosen`, so seeding the auto-selection first would mark the
    # District step SATISFIED and resume past it — a matched admin would never see the step the
    # launch page just promised them ("you'll confirm it on the next step"), and the
    # "correctable pre-selection" would be neither confirmed nor correctable. Resume derives
    # from PERSISTED state (what the admin actually chose); the auto-selection is a
    # pre-selection ON that step, which is what D9 always meant.
    # ...and NEVER in creator mode (plan 0044 S3, obligation #8): seeding a bundled district
    # into a pending creator flow would satisfy the District step with the wrong answer and
    # resume the admin PAST the step they are half-way through.
    if mode == "standard" and not str(ws["sis"]).strip():
        # D9, re-scoped to the VISIBLE list (0038 S5 — see the dated DECISIONS entry): the seed
        # reads the FILTERED catalog, so a matched admin whose domain resolves to exactly one
        # district gets it pre-selected on the District step. That keeps D9's rule intact
        # ("auto-select only when there is no meaningful choice to make") while making the launch
        # page's promise — "you'll confirm it on the next step" — literally true.
        #
        # Built INSIDE this branch, not at mount: the catalog is a 21-YAML ``load_config`` sweep
        # (session-memoised, but somebody pays for the first build) and it is the ONLY consumer.
        # A creator mount — which can never seed — used to pay for all 21 to compute a list it
        # then discarded.
        visible_ids = [summary.sis_type for summary in _district_catalog(cfg).summaries]
        ws["sis"] = auto_selected_district(visible_ids)  # D9: auto-select iff exactly one VISIBLE
        # Drives the acknowledging caption on the step: a choice made FOR the admin says so.
        ws["auto_seeded"] = bool(ws["sis"])

    def _step_addressed(step: SetupStep) -> bool:
        """Whether a skippable step is done (LIVE / tested-ok / stored) OR explicitly deferred."""
        if step is SetupStep.SCHEDULE:
            status = ws["schedule_status"]
            return bool(ws["schedule_skipped"]) or (
                status is not None and status.state is ScheduleState.LIVE  # type: ignore[union-attr]
            )
        if step is SetupStep.DELIVERY:
            return ws["delivery"] in {
                DeliveryFact.TESTED_OK,
                DeliveryFact.STORED_CRED_PRESENT,
                DeliveryFact.SKIPPED,
            }
        return False

    def _refresh_footer() -> None:
        """Re-gate / re-label the forward button in place (input change, async status arrival)."""
        btn = ws["forward_btn"]
        if btn is None:
            return
        step = ws["step"]
        if step is SetupStep.FILES:
            # Same gate as the render above (``can_advance`` AND nothing pending), so an
            # in-place re-gate can never disagree with a freshly built footer.
            btn.disabled = not (can_advance(step, _inputs()) and not _files_names_pending())  # type: ignore[union-attr]
        elif step in (SetupStep.FOLDERS, SetupStep.DISTRICT):
            btn.disabled = not can_advance(step, _inputs())  # type: ignore[union-attr]
        elif is_skippable(step):
            # SCHEDULE is skippable but window-gated (an enabled+invalid window closes Continue);
            # DELIVERY stays always-advanceable. ``can_advance`` single-sources both.
            btn.disabled = not can_advance(step, _inputs())  # type: ignore[union-attr]
            btn.content = "Continue" if _step_addressed(step) else "Set up later"  # type: ignore[union-attr]
        page.update()

    def _go(step: SetupStep, *, note: str = "") -> None:
        """Move to ``step``, carrying at most ONE creator note onto the surface it lands on.

        The note defaults to ``""``, so every ordinary hop CLEARS whatever was showing — a
        "we couldn't remember where you got to" line has one surface's worth of life, and
        leaving it standing over a later step would make it read as that step's problem.
        """
        ws["step"] = step
        ws["creator_note"] = note
        _render()

    def _district_activation_allowed(picked: str) -> bool:
        """The verified-fact check on the wizard's STANDARD District step. TOTAL.

        This step's Continue is the FIFTH writer of ``sis_type`` (plan 0044 S6 review,
        BLOCKING 2), and the one the wizard's own finish line bakes into the nightly task.
        Its dropdown lists districts set up on THIS computer — ``_district_catalog`` is the
        same scoped build every picker uses — so it consults the SAME pure
        ``config_editor.activation_allowed`` Mapping's Apply, the folders card and the
        creator's own confirm do. One rule, one comparison, five writers.

        The origin comes from that catalog (the build is session-memoised, so this is a
        projection over what the dropdown was just built from, not a second parse), and an id
        missing from it reads as ``"bundled"`` — the fail-OPEN direction ``mapping_catalog``
        documents for itself, and the one this threat model requires: the check exists to
        stop an admin MISTAKE and may never strand an admin whose provenance we could not
        read. Read through a FRESH ``AppConfig`` for the same reason Mapping's Apply does:
        the digest it looks for is written by the creator panel on another surface, which
        this mount's instance would not have seen.
        """
        if not picked.strip():
            return True  # nothing chosen to check — ``can_advance`` already closed Continue
        origins = {summary.sis_type: summary.origin for summary in _district_catalog(cfg, picked_sis=picked).summaries}
        origin = origins.get(picked, "bundled")
        return activation_allowed(
            AppConfig.load(),
            sis_id=picked,
            origin=origin,
            # ``None`` on the bundled branch deliberately — the rule never reads it there, so
            # the shipped rows pay no config load.
            current_digest=current_digest(picked) if origin == "user" else None,
        ).allowed

    def _forward() -> None:
        step = ws["step"]
        if step is SetupStep.FILES and _files_names_pending():
            # The load-bearing half of the two-primaries fix (plan 0044 S4 review, BLOCKING
            # 2): advancing here would carry the district forward under file names that were
            # never written, and the write it skipped is the only record of them. Re-renders
            # rather than returning silently, because the button that was pressed was built
            # before the pick and is now painting the wrong tier — one press and the step
            # reads its own truth (the body's Save takes the primary tier, this Continue
            # drops to outlined and disabled, and the unsaved warning is on screen).
            _go(SetupStep.FILES)
            return
        if not can_advance(step, _inputs()):
            return  # gate closed (folders/district) — Enter/Continue is a no-op, matching the disabled button
        creator = ws["mode"] == "creator"
        if step is SetupStep.FOLDERS:
            cfg.input_dir = str(ws["input"])
            cfg.output_dir = str(ws["output"])
            cfg.save()
        elif step is SetupStep.DISTRICT and creator:
            # A creator's District step persists NOTHING here: its Continue lives in the
            # creator surface, where it writes the overlay and stores the resume token (and
            # ``sis_type`` is only ever set by the validated activation). Reachable via Enter,
            # so it is a deliberate no-op rather than an unreachable branch.
            pass
        elif step is SetupStep.DISTRICT:
            picked = str(ws["sis"])
            if not _district_activation_allowed(picked):
                # Nothing written, no advance (plan 0044 S6 review, BLOCKING 2): the standard
                # walk offers a district added on this computer like any other, and this press
                # is what would carry an untested one to a finish line that registers it as a
                # nightly task. Re-renders the step so the refusal is on screen beside the
                # dropdown that corrects it — the same shape as the FILES branch above.
                ws["district_refused"] = True
                _go(SetupStep.DISTRICT)
                return
            ws["district_refused"] = False
            cfg.sis_type = picked
            cfg.save()
        elif step is SetupStep.FILES:
            pass  # the gate + activation already persisted everything this step decides
        elif is_skippable(step) and not _step_addressed(step):
            # Advancing an unaddressed skippable step defers it ("Set up later") — marked skipped
            # so it counts as satisfied for the finish line WITHOUT asserting anything false.
            if step is SetupStep.SCHEDULE:
                ws["schedule_skipped"] = True
            else:
                ws["delivery"] = DeliveryFact.SKIPPED
        nxt = next_step(step, mode=ws["mode"])  # type: ignore[arg-type]
        if nxt is not None:
            _go(nxt)

    def _back() -> None:
        prev = prev_step(ws["step"], mode=ws["mode"])  # type: ignore[arg-type]
        if prev is not None:
            _go(prev)

    def _fire_schedule_changed() -> None:
        """Re-probe the rail's Setup badge, advisory — never let it break the graduation.

        The same guard the register/unregister callers use (``contextlib.suppress`` under
        "advisory: never let it break the result paint"): a badge that failed to refresh is
        a stale dot, while a raise here would abort a finish line that already saved.
        """
        if on_schedule_changed is not None:
            with contextlib.suppress(Exception):
                on_schedule_changed()

    def _finish() -> None:
        """Save FIRST, verify, and only then hand the payoff on (0038 S6).

        The ONLY completion signal (D8/D4a): reaching the finish line — never any single
        step — marks the install set up.

        The save is the whole risk here. The realistic failure is I/O — a locked, read-only
        or full settings folder — and NOT ``SettingsOverwriteRefused``: that refusal needs
        ``settings_unreadable() and not _carries_chosen_settings()``, and the assignment in
        the ``try`` below moves ``setup_completed`` off its constructor default first, which
        is neither transient nor ``_ADVISORY_FIELD_PREFIXES``-prefixed, so the second half is
        False by construction at this exact call site. The bare ``except`` covers both
        regardless (a future field rule must not re-open the hole silently), and the
        consequence of treating a failure as success is specific and bad: the host would
        re-render Home, Home would re-read a config that still says "unfinished", and the
        admin would be dropped back at step 1 having just been told they were done. So a
        raised save (a) rolls the in-memory flag back, so this instance never claims a state
        the disk lacks, (b) leaves the finish summary exactly where it is, with an honest
        note under the button they can press again, and (c) does NOT fire ``on_complete``.

        **The press is latched**, and only a FAILED attempt un-latches it. A second click
        while the first is still landing would save twice and hand off twice — on the hosted
        path that is two navigations, and the button is genuinely double-clickable because
        it stays on screen right up until the host replaces the surface. A failure must
        re-open it, because the note that failure prints promises a retry.
        """
        if ws["finishing"]:
            return
        ws["finishing"] = True
        try:
            cfg.setup_completed = True
            cfg.save()
        except Exception:  # noqa: BLE001 - honest on screen, LOUD in the log, never a silent bounce
            cfg.setup_completed = False
            ws["finishing"] = False  # the retry the note promises must actually be possible
            ws["finish_error"] = FINISH_SAVE_FAILED_NOTE
            logger.warning("Could not record setup completion; the finish step stays open.", exc_info=True)
            _render()
            return
        if ws["finish_error"]:
            # A previous attempt failed and THIS one succeeded. Drop the stale note before
            # the surface is handed on: what the host does next is the host's business, and
            # a "we couldn't save your settings" line left sitting over a save that did
            # happen is a lie no host should be able to leave on screen.
            ws["finish_error"] = ""
            _render()
        try:
            if on_complete is not None:
                # The host owns what happens next (Home re-renders into its health view). The
                # summary stays on screen until this press, which IS the "take me there" action.
                on_complete()
                return
            # The RAIL mount's own badge re-probe — the twin of the host's ``_on_setup_complete``.
            # The boot-time probe is suppressed while ``needs_setup``, so an admin who finishes
            # HERE and skipped the Schedule step keeps a silenced badge for the whole session,
            # and a leftover task firing with no record stays invisible until a restart. Same
            # guard as the register/unregister callers: advisory, never breaks the graduation.
            _fire_schedule_changed()
            # ``on_navigate`` rides the graduation too (plan 0044 S6): the Settings scroll this
            # finish line mounts IN PLACE is the same scroll the rail item mounts, and a folders
            # card whose refusal could route on one mount and not the other is two cards.
            _mount_settings(
                page,
                AppConfig.load(),
                root,
                transition_cue=True,
                on_schedule_changed=on_schedule_changed,
                on_navigate=on_navigate,
                on_reenter=on_reenter,
            )
            page.update()
        except Exception:
            # The save SUCCEEDED and the hand-off did not. Re-open the latch so the button
            # stays pressable (a one-shot latch set before risky work turns any transient
            # failure into a permanently dead button), then re-raise: this is not the save
            # failure, so ``FINISH_SAVE_FAILED_NOTE`` would be a lie, and a silent swallow
            # would leave the admin pressing a button that does nothing. Fail LOUD.
            ws["finishing"] = False
            raise

    def _on_sched_status(status: ScheduleStatus) -> None:
        ws["schedule_status"] = status
        if status.state is ScheduleState.LIVE:
            # A CONFIRMED-live task un-defers the step (QA, 2026-08-18). ``schedule_skipped`` was
            # write-once: deferring the step latched it, and nothing ever cleared it — so an
            # admin who deferred and then came BACK and scheduled successfully still met a finish
            # line that said the nightly sync was not set up, because ``_finish_body`` reads
            # ``(not schedule_skipped) and status is LIVE``. Clearing it here keeps the defer a
            # statement about the admin's INTENT and lets the read-back overrule it, which is the
            # direction this flow already trusts everywhere else (D8: never trust the config flag
            # for live-ness — trust the probe). Only LIVE clears it: UNKNOWN/MISSING leave the
            # defer standing, so a failed or removed registration can never silently un-skip.
            ws["schedule_skipped"] = False
        _refresh_footer()

    def _on_schedule_busy(busy: bool) -> None:
        # The Schedule step's second transient gate (the first is the seasonal window): while a
        # register/unregister is in flight the forward button closes, so Continue cannot abandon
        # a registration whose UAC prompt is still on screen. Advance-only — never persisted, and
        # never reaching ``derive_flow``.
        ws["schedule_busy"] = busy
        _refresh_footer()

    def _on_delivery(fact: DeliveryFact, host: str, username: str) -> None:
        ws["delivery"] = fact
        ws["delivery_host"] = host
        ws["delivery_user"] = username
        _refresh_footer()

    # ---- per-step body builders ---------------------------------------- #
    def _folders_body() -> ft.Control:
        def _on_input(path: str, _r: ValidationResult) -> None:
            ws["input"] = path
            _refresh_footer()

        def _on_output(path: str, _r: ValidationResult) -> None:
            ws["output"] = path
            _refresh_footer()

        input_field = PickerField(
            page=page,
            label="Input folder (MyEd BC extract)",
            helper="The folder DistrictSync reads your General Data Extract files from.",
            validator=validate_input_dir,
            on_change=_on_input,
            dialog_title="Select the MyEd BC extract folder",
            initial_value=str(ws["input"]),
        )
        output_field = PickerField(
            page=page,
            label="Output folder (SpacesEDU CSVs)",
            helper="Where DistrictSync writes the converted CSV files.",
            validator=validate_output_dir,
            on_change=_on_output,
            dialog_title="Select the output folder",
            initial_value=str(ws["output"]),
        )
        return ft.Column(spacing=22, controls=[input_field, output_field])

    # ---- creator mode: the host callbacks (plan 0044 S3 §3.5) ----------- #
    def _files_pending() -> dict[str, str]:
        """The creator surface's pending rename map (created once, then mutated in place)."""
        names = ws.setdefault("files_pending", {})
        if not isinstance(names, dict):  # defensive: the surface mutates whatever it is handed
            names = {}
            ws["files_pending"] = names
        return names

    def _files_lock_note() -> ft.Text:
        """The FILES footer's "why is Continue locked?" caption — created ONCE, host-owned.

        The owner's report (2026-09-02): the gate step's Continue sat disabled with no
        indication of why or what would open it. The host owns the control because it owns
        the footer and the Continue being explained (S6's Mapping host has neither, passes
        no note, and gets no caption); the creator surface FILLS it, because it holds every
        input the reason is a function of — see ``build_creator``'s ``continue_lock_note``.

        One control for this mount's lifetime, re-parented into each footer it is rendered
        into (``ws["forward_btn"]``'s neighbour), so the surface never has to be handed a
        fresh object it did not ask for.
        """
        note = ws.get("files_lock_note")
        if not isinstance(note, ft.Text):
            note = ft.Text("", size=tokens.type_caption, color=tokens.color_muted, visible=False)
            ws["files_lock_note"] = note
        return note

    def _files_names_pending() -> bool:
        """Whether the FILES step has file names the config on disk does not have.

        Read from the SAME pure comparison the surface tiers its Save on
        (``config_editor.has_unsaved_renames``), never a second spelling: a footer that
        disagreed with the body would put a second filled primary on the step and — worse —
        advance past names that were never written.
        """
        form = ws["creator_form"]
        saved = form.renames if isinstance(form, CreatorForm) else {}
        return has_unsaved_renames(_files_pending(), saved)

    def _on_creator_written(new_sis: str, form: CreatorForm, note: str) -> None:
        """The overlay is on disk and the token is stored — advance to the next step."""
        ws["creator_sis"] = new_sis
        ws["creator_form"] = form
        ws["files_ok"] = None  # the gate fact must be re-probed against the file just written
        nxt = next_step(SetupStep.DISTRICT, mode="creator")
        _go(nxt if nxt is not None else SetupStep.DISTRICT, note=note)

    def _on_creator_files_saved(form: CreatorForm, note: str) -> None:
        """The file names are on disk — re-render THIS step against what it now says.

        Never an advance (plan 0044 S4): a save is not a step being passed. The memoised
        gate fact is dropped because the saved config is a different config from the one any
        earlier test conversion ran — ``files_step_satisfied`` has to be re-probed, and it
        will now be False until another test run, which is what re-closes Continue.
        """
        ws["creator_form"] = form
        ws["files_ok"] = None
        _go(SetupStep.FILES, note=note)

    def _on_creator_activated() -> None:
        """``sis_type`` is now this district — move on, exactly as a passed step would."""
        ws["files_ok"] = None
        ws["sis"] = cfg.sis_type
        nxt = next_step(SetupStep.FILES, mode="creator")
        _go(nxt if nxt is not None else SetupStep.FILES)

    def _on_creator_discarded() -> None:
        """Back to the STANDARD walk, on the District step, saying what happened."""
        ws["mode"] = "standard"
        ws["creator_sis"] = ""
        ws["creator_form"] = None
        ws["files_ok"] = None
        ws["files_pending"] = {}  # the district is gone; its rows must not outlive it
        _go(SetupStep.DISTRICT, note=CREATOR_DISCARDED_NOTE)

    def _enter_creator(_e: ft.ControlEvent | None = None) -> None:
        """Switch the District step to the creator surface (nothing is written yet)."""
        ws["mode"] = "creator"
        ws["creator_sis"] = ""
        ws["creator_form"] = creator_form_for_new(cfg)
        ws["files_ok"] = None
        ws["files_pending"] = {}
        _go(SetupStep.DISTRICT)

    def _creator_body(stage: CreatorStage) -> ft.Control:
        form = ws["creator_form"]
        if not isinstance(form, CreatorForm):  # defensive: a creator surface always has a form
            form = creator_form_for_new(cfg)
            ws["creator_form"] = form
        return build_creator(
            page,
            cfg=cfg,
            sis_id=str(ws["creator_sis"]),
            form=form,
            on_written=_on_creator_written,
            on_files_saved=_on_creator_files_saved,
            on_activated=_on_creator_activated,
            on_discarded=_on_creator_discarded,
            stage=stage,
            pending=_files_pending(),
            continue_lock_note=_files_lock_note(),
        )

    def _district_body() -> ft.Control:
        if ws["mode"] == "creator":
            return _creator_body("forms")

        def _instruction_text() -> str:
            picked_now = str(ws["sis"])
            if ws["auto_seeded"] and picked_now:
                return district_auto_seeded_note(friendly_district_name(picked_now) or picked_now)
            return DISTRICT_PICK_PROMPT

        def _refusal_controls() -> list[ft.Control]:
            """The refused-Continue note, plus the route to where the test conversion lives.

            Never colour-alone (the glyph rides beside the words), and never a dead
            affordance: with no ``on_navigate`` (Home's wizard host passes none) the note
            renders alone and the rail still carries Mapping.
            """
            controls: list[ft.Control] = [
                ft.Row(
                    spacing=tokens.space_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=18, color=tokens.color_status_warning),
                        ft.Text(
                            WIZARD_DISTRICT_NEEDS_TEST_NOTE,
                            size=tokens.type_body,
                            color=tokens.color_status_warning,
                            expand=True,
                        ),
                    ],
                )
            ]
            if on_navigate is not None:
                controls.append(
                    ft.Row(
                        controls=[
                            components.text_button(
                                # ONE label for both refusal sites — the folders card and this
                                # step send an admin to the same screen for the same reason.
                                FOLDERS_NEEDS_TEST_LINK_LABEL,
                                lambda _e: on_navigate("mapping"),
                                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                            )
                        ]
                    )
                )
            return controls

        refusal_slot = ft.Column(
            spacing=tokens.space_sm,
            controls=_refusal_controls() if ws["district_refused"] else [],
        )

        def _on_pick(e: ft.ControlEvent) -> None:
            ws["sis"] = e.control.value or ""
            # An explicit pick supersedes the auto-seed, so the acknowledging caption retires —
            # it would otherwise credit us with a choice the admin has since made. Updated IN
            # PLACE rather than by re-rendering the step: a rebuild would replace the dropdown
            # mid-interaction and take the focus with it.
            ws["auto_seeded"] = False
            instruction_line.value = _instruction_text()
            # ...and the refusal goes with it: it named the district that was picked when
            # Continue was pressed, so leaving it standing over a NEW pick would report a
            # fault about a district it was never about. Cleared in place for the same reason
            # the caption is (the focus stays in the control the admin is using).
            ws["district_refused"] = False
            refusal_slot.controls = []
            _refresh_footer()

        picked = str(ws["sis"])
        catalog = _district_catalog(cfg, picked_sis=picked)
        dropdown = ft.Dropdown(
            label="District",
            hint_text="Choose your district",  # D9: no pre-selection; placeholder prompts an explicit pick
            value=picked or None,
            options=_district_options(catalog),
            on_select=_on_pick,  # Dropdown's value-change is on_select on 0.85.3 (not on_change)
            border_color=tokens.color_border,
            autofocus=True,  # focus the new step's first field (D8 keyboard flow)
        )
        instruction_line = ft.Text(_instruction_text(), size=14, color=tokens.color_muted)
        return ft.Column(
            spacing=12,
            controls=[
                # #4: ONE orientation line on the wizard's FIRST step (now District, 2026-07-15 reorder)
                # — the wizard shouldn't cold-open with zero context. (A fuller welcome screen is a
                # close-out question, not built here.)
                ft.Text(
                    "DistrictSync keeps your MyEd BC roster flowing to SpacesEDU — automatically, every "
                    "night. Let's set it up.",
                    size=14,
                    color=tokens.color_muted,
                ),
                instruction_line,
                dropdown,
                # The refusal sits directly under the control that corrects it (and directly
                # above the footer Continue that was refused).
                refusal_slot,
                # The creator door (plan 0044 S3): TEXT tier, so the step keeps its ONE filled
                # primary (Continue). It sits UNDER the dropdown because picking a shipped
                # district is the right answer for almost everyone who reaches this step.
                ft.Row(
                    controls=[
                        components.text_button(
                            CREATOR_ENTRY_LABEL, _enter_creator, icon=ft.Icons.ADD_CIRCLE_OUTLINE_ROUNDED
                        )
                    ]
                ),
            ],
        )

    def _on_window_valid(valid: bool) -> None:
        # The seasonal-window gate (B): an enabled+invalid window closes the Schedule step's
        # Continue button (re-gated in place via the footer), the same guarantee as the folders/
        # district value gates. Does not touch the register flow — the window is not a task arg.
        ws["window_valid"] = valid
        _refresh_footer()

    def _schedule_body() -> ft.Control:
        card, _handle = _build_schedule_section(
            page,
            cfg,
            on_status=_on_sched_status,
            on_schedule_changed=on_schedule_changed,
            on_window_valid=_on_window_valid,
            on_busy=_on_schedule_busy,
            on_reenter=on_reenter,
            # 0049 S-2b.1: re-render THIS step, which is what makes the confirm's one-click
            # folder swap honest — the Folders step reads its fields from `cfg` when it is
            # built, so a write with no re-render would leave a stale value for its next Save.
            on_remount=_render,
            on_terminal=lambda keep: _freeze_setup_actions(root, keep=keep),
        )
        return card

    def _delivery_body() -> ft.Control:
        controls: list[ft.Control] = []
        if ws["delivery"] is DeliveryFact.STORED_CRED_PRESENT:
            # Reconcile (D8): a credential is already saved — don't imply a fresh one is required.
            controls.append(
                components.HealthVerdictBanner(
                    Verdict.HEALTHY,
                    headline="A delivery password is already saved",
                    detail="A SpacesEDU credential is already stored on this computer. "
                    "Test it below, or continue to keep using it.",
                )
            )
        controls.append(_build_sftp_section(page, cfg, on_delivery=_on_delivery))
        return ft.Column(spacing=18, controls=controls)

    def _finish_body() -> ft.Control:
        status = ws["schedule_status"]
        schedule_live = (not ws["schedule_skipped"]) and status is not None and status.state is ScheduleState.LIVE  # type: ignore[union-attr]
        district = friendly_district_name(str(ws["sis"])) or str(ws["sis"])
        next_run = status.next_run_display if (schedule_live and status is not None) else None  # type: ignore[union-attr]
        # Backtrack guard (0029 close-out): a LIVE task baked WITHOUT --sftp while delivery is now
        # enabled (register → Back → save a credential → Finish) must NOT let the finish line claim
        # tonight delivers. The pure decision reads the durable last-REGISTERED record; the honest
        # downgraded copy points at the one Save in Settings that picks the change up (the Settings
        # reconcile self-heals from the same record — no re-register/UAC plumbing at the finish line).
        desync = schedule_delivery_desync(
            schedule_live=bool(schedule_live),
            registered=task_args_from_persisted(cfg.schedule_task_args),
            sftp_enabled=cfg.sftp_enabled,
        )
        headline, detail = finish_copy(
            schedule_live=bool(schedule_live),
            delivery=ws["delivery"],  # type: ignore[arg-type]  # F1: keyed off PERSISTED delivery, not a transient test
            district=district,
            schedule_time_display=next_run,
            host=str(ws["delivery_host"]),
            username=str(ws["delivery_user"]),
            delivery_desync=desync,
        )
        # The checked summary is derived from the SAME computed facts as the banner copy (single
        # source — no independent re-derivation), so the calm per-step card can never contradict it.
        rows = finish_summary_rows(
            schedule_live=bool(schedule_live),
            delivery=ws["delivery"],  # type: ignore[arg-type]
            district=district,
            schedule_time_display=next_run,
            delivery_desync=desync,
        )
        # Amber (attention) ONLY when the finish copy itself downgrades to the desync headline —
        # single-sourced via finish_needs_attention so tone and words always agree (W4a nit: the
        # raw desync fact alone painted an amber band under a confident "You're all set" headline
        # on the Save-then-Test path, whose Test flips the session delivery fact to TESTED_OK).
        attention = finish_needs_attention(delivery=ws["delivery"], delivery_desync=desync)  # type: ignore[arg-type]
        verdict = Verdict.WARNING if attention else Verdict.HEALTHY
        controls: list[ft.Control] = [
            components.HealthVerdictBanner(verdict, headline=headline, detail=detail),
            _finish_summary_card(rows),
        ]
        if ws["mode"] == "creator" and not _creator_activated():
            # ``derive_flow`` can land a creator here with ``can_finish`` False (every other
            # step satisfied, the district never switched over) — the Finish button is
            # disabled, and a disabled button with no reason beside it is a dead end. Says
            # what is missing and where to do it; never a silent flip of the precondition.
            controls.append(
                ft.Row(
                    spacing=tokens.space_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=18, color=tokens.color_status_warning),
                        ft.Text(
                            CREATOR_FINISH_NEEDS_GATE_NOTE,
                            size=tokens.type_body,
                            color=tokens.color_status_warning,
                            expand=True,
                        ),
                    ],
                )
            )
        if ws["finish_error"]:
            # Never colour-alone: the glyph rides beside words that say what happened.
            controls.append(
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=18, color=tokens.color_status_failed),
                        ft.Text(str(ws["finish_error"]), size=13, color=tokens.color_status_failed),
                    ],
                )
            )
        return ft.Column(spacing=18, controls=controls)

    _BODIES: dict[SetupStep, Callable[[], ft.Control]] = {
        SetupStep.FOLDERS: _folders_body,
        SetupStep.DISTRICT: _district_body,
        SetupStep.FILES: lambda: _creator_body("files"),
        SetupStep.SCHEDULE: _schedule_body,
        SetupStep.DELIVERY: _delivery_body,
        SetupStep.FINISH: _finish_body,
    }

    def _step_header(step: SetupStep) -> ft.Control:
        # Direction B (0033 Slice 2): the gradient step hero demotes to a compact page header —
        # the step title as the header, "Step N of 5" as the caption. (The 5→4 "Finish
        # unnumbered" count fix is 0032 Tier-1 #10, a separate slice — not folded in here.)
        return components.page_header(
            _STEP_TITLES[step],
            # Mode-aware denominator (plan 0044 S3): 5 on the standard walk, 6 on the creator
            # walk — both DERIVED from the one step tuple, never typed.
            f"Step {step_number(step, mode=ws['mode'])} of {total_steps(ws['mode'])}",  # type: ignore[arg-type]
        )

    def _step_footer(step: SetupStep) -> ft.Control:
        controls: list[ft.Control] = []
        creator = ws["mode"] == "creator"
        if prev_step(step, mode=ws["mode"]) is not None:  # type: ignore[arg-type]
            controls.append(components.secondary_button("Back", lambda _e: _back(), icon=ft.Icons.ARROW_BACK_ROUNDED))

        if creator and step is SetupStep.DISTRICT:
            # The creator surface owns this step's ONE filled primary (its Continue IS the
            # overlay write), so the footer contributes no forward button at all — two filled
            # primaries on one step is a bug, and a second "Continue" that skipped the write
            # would be worse than one.
            ws["forward_btn"] = None
            return ft.Row(spacing=16, controls=controls)

        if creator and step is SetupStep.FILES:
            # The gate step's tiers: while there is still a test to run or a district to
            # confirm, the BODY holds the filled primary and this Continue is the outlined
            # supporting action (disabled until the gate is genuinely passed — Enter can't
            # bypass it either). Once the district is active, the body has no primary left and
            # Continue becomes the screen's one filled action.
            # ...and CLOSED while a file name on screen is not in the config on disk (plan
            # 0044 S4 review, BLOCKING 2): the body's Save owns the primary tier then, and a
            # Continue that advanced would carry the district forward under names it never
            # wrote — silently, since the write it skipped is the only record of them.
            open_gate = can_advance(SetupStep.FILES, _inputs()) and not _files_names_pending()
            factory = components.primary_button if open_gate else components.secondary_button
            forward = factory(
                "Continue",
                lambda _e: _forward(),
                disabled=not open_gate,
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
            )
            ws["forward_btn"] = forward
            controls.append(forward)
            # ...and UNDER it, the caption saying why it is closed and what opens it — filled
            # by the creator surface on every render, hidden the moment Continue opens.
            return ft.Column(
                spacing=tokens.space_sm,
                controls=[ft.Row(spacing=16, controls=controls), _files_lock_note()],
            )

        if step is SetupStep.FINISH:
            forward = components.primary_button(
                "Finish setup",
                lambda _e: _finish(),
                disabled=not can_advance(SetupStep.FINISH, _inputs()),
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
            )
        elif step in (SetupStep.FOLDERS, SetupStep.DISTRICT):
            forward = components.primary_button(
                "Continue",
                lambda _e: _forward(),
                disabled=not can_advance(step, _inputs()),
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
            )
        else:  # skippable Schedule / Delivery (Schedule adds the window gate — B)
            forward = components.primary_button(
                "Continue" if _step_addressed(step) else "Set up later",
                lambda _e: _forward(),
                disabled=not can_advance(step, _inputs()),
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
            )
        ws["forward_btn"] = forward
        controls.append(forward)
        return ft.Row(spacing=16, controls=controls)

    def _render() -> None:
        step = ws["step"]  # type: ignore[assignment]
        controls: list[ft.Control] = [_step_header(step)]  # type: ignore[arg-type]
        if ws["creator_note"]:
            # ONE creator note, above the step it belongs to (a discard's confirmation, or a
            # stored-progress refusal). Never colour-alone — the glyph rides beside the words.
            controls.append(
                ft.Row(
                    spacing=tokens.space_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=18, color=tokens.color_muted),
                        ft.Text(str(ws["creator_note"]), size=tokens.type_body, color=tokens.color_muted, expand=True),
                    ],
                )
            )
        controls.extend([_BODIES[step](), _step_footer(step)])  # type: ignore[index]
        root.controls = controls
        page.update()

    _render()


# --------------------------------------------------------------------------- #
# Settings mode (D8): the flat scroll + one reconciling Save.                  #
# --------------------------------------------------------------------------- #
#: Every interactive control class this surface builds. The freeze below walks for these
#: explicitly rather than setting ``disabled`` on the root: Flet's inherited-disabled would also
#: kill the terminal card's own "Open log folder" button, which is the one action still worth
#: offering when nothing else on the surface can be trusted.
_INTERACTIVE_CONTROLS = (
    ft.FilledButton,
    ft.OutlinedButton,
    ft.TextButton,
    ft.IconButton,
    ft.TextField,
    ft.Dropdown,
    ft.Switch,
    ft.Checkbox,
)


def _freeze_setup_actions(root: ft.Control, *, keep: ft.Control | None = None) -> None:  # pragma: no cover - view glue
    """Disable every action on the Setup surface except the ``keep`` subtree (0049 S-2b.3).

    For exactly one state: the elevated child committed the machine-scope switch and the
    parent's re-pin then REFUSED the folder, so ``paths`` holds no pin and every later
    ``user_data_dir()`` in this session raises. A live window whose next click can only crash
    is worse than a calm dead end, and the next launch reports the same refusal through the
    launcher's own dialog — which is where an administrator can actually repair it.

    ``keep`` is the control that explains the state, and it stays live so its log-folder
    affordance still works. Walked rather than inherited (see ``_INTERACTIVE_CONTROLS``).
    """
    kept = {id(control) for control in _walk_controls(keep)} if keep is not None else set()
    for control in _walk_controls(root):
        if id(control) not in kept and isinstance(control, _INTERACTIVE_CONTROLS):
            control.disabled = True


def _walk_controls(control: ft.Control | None):  # pragma: no cover - view glue
    """Depth-first walk over ``.controls`` + a single ``.content`` child (the tree tests use)."""
    if control is None:
        return
    yield control
    children: list[object] = []
    nested = getattr(control, "controls", None)
    if isinstance(nested, list):
        children.extend(nested)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        children.append(content)
    for child in children:
        if isinstance(child, ft.Control):
            yield from _walk_controls(child)


def _registered_schedule(cfg: AppConfig) -> RegisteredSchedule:  # pragma: no cover - AppConfig→pure adapter
    """The durable "what the live task actually carries" record (the ONE resolution point).

    A thin adapter over the pure ``setup_flow.registered_schedule``: it only supplies the four
    persisted facets plus the running platform's unattended-logon capability. Both reconcile
    consumers (the task-args comparison and the logon-downgrade guard) read the record from HERE,
    so neither can re-derive a baseline from the *current* config (the W3-C silent no-op).
    """
    return registered_schedule(
        raw_task_args=cfg.schedule_task_args,
        unattended_flag=cfg.schedule_unattended,
        raw_run_as_user=cfg.schedule_run_as_user,
        raw_run_as_kind=cfg.schedule_run_as_kind,
        supports_unattended=get_scheduler().supports_unattended_password,
    )


def _mount_settings(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    root: ft.Column,
    *,
    transition_cue: bool,
    on_schedule_changed: Callable[[], None] | None = None,
    on_navigate: Callable[[str], None] | None = None,
    on_reenter: Callable[[], None] | None = None,
) -> None:
    """Render the completed-install Settings scroll into ``root`` (folders + schedule + SFTP)."""

    def _remount() -> None:
        """Re-render this scroll from a FRESH config (0049 S-2b.1).

        The confirm's one-click folder swap writes ``cfg`` and saves; the folders card seeded
        its own fields at BUILD time, so without this the live control would contradict the
        config and that card's next Save would write the stale value straight back. A fresh
        ``AppConfig.load()``, for the same reason every screen re-reads per mount (D1).
        """
        _mount_settings(
            page,
            AppConfig.load(),
            root,
            transition_cue=False,
            on_schedule_changed=on_schedule_changed,
            on_navigate=on_navigate,
            on_reenter=on_reenter,
        )

    # The ONE reconcile the folders Save AND the SFTP Save both drive (D8/F1): any change to a
    # task-baked field (folders/district/SFTP flag/run time) on a registered schedule re-registers
    # through the SAME flow, so the nightly action can never go stale — and enabling SFTP in
    # Settings finally adds --sftp to an already-registered task (the F1 gap).
    # Build the schedule section FIRST so both Saves can drive its register flow.
    schedule_card, sched_handle = _build_schedule_section(
        page,
        cfg,
        on_schedule_changed=on_schedule_changed,
        on_reenter=on_reenter,
        on_remount=_remount,
        on_terminal=lambda keep: _freeze_setup_actions(root, keep=keep),
        # 0049 S-4: the ONLY mount that offers the gMSA disclosure. The wizard's call above
        # leaves it at its default and keeps refusing a ``$``-suffixed account, deliberately.
        allow_gmsa=True,
    )

    def _reconcile() -> ReconcileOutcome:
        # 2026-08-31 race guard: while a register/unregister dispatched earlier is still
        # applying (UAC prompt up, worker running), ``schedule_registered`` and the durable
        # record describe the PRE-dispatch world — deciding from them here concluded "no task"
        # for a delivery Save landing mid-registration, and the registration then confirmed
        # with its pre-delivery args (the nightly ran without --sftp while delivery read ON).
        # Refuse to decide; the Save note tells the admin to save again once it finishes.
        if sched_handle.is_busy():
            return ReconcileOutcome.IN_FLIGHT
        pending = TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=sched_handle.run_time_value(),
        )
        # W3-C: the baseline is the durable last-REGISTERED record and NOTHING else. A mount-time
        # config snapshot is not a record of what the task carries — after another surface mutated
        # config (a Mapping district switch persists the new district *before* Setup remounts and
        # reads it) the snapshot already equals ``pending``, so the reconcile read "unchanged" and
        # silently skipped the very re-register the Mapping notice told the admin to do.
        action = schedule_reconcile(
            schedule_registered=cfg.schedule_registered,
            record=_registered_schedule(cfg),
            pending=pending,
            pending_run_as_user=sched_handle.run_as_user_value(),
            current_account=_keyring_owner_account(),
        )
        if action is ScheduleReconcile.REREGISTER:
            # May pause on the explicit downgrade choice first (S3-a). Returns DISPATCHED
            # (register started) vs INTERRUPTED (dialog shown, nothing registered) so the Save
            # sites paint an honest note — never "updating…" for an interrupt that registered
            # nothing (the S3 correctness fix).
            return sched_handle.trigger_register()
        if action is ScheduleReconcile.NO_TASK:
            # S3-b: with no task to re-register, a run-time edit is still CONFIG — persist a
            # valid one (invalid → the schedule section's inline error, nothing persisted).
            sched_handle.persist_run_time()
        return ReconcileOutcome.NONE

    # 0049 S-2a.3: the delivery section's password line needs the schedule read-back to tell a
    # CONFIRMED-MISSING task from one we simply could not read. Passed as the handle's accessor,
    # not a snapshot — the probe lands asynchronously, after this mount has returned.
    sftp_card = _build_sftp_section(page, cfg, on_saved=_reconcile, schedule_state=sched_handle.last_schedule_state)
    folders_card = _build_settings_folders(page, cfg, reconcile=_reconcile, on_navigate=on_navigate)

    # Direction B (0033 Slice 2): the Settings gradient hero demotes to a slim page header.
    header = components.page_header(
        "Settings",
        "Everything you set up lives here — edit your folders, district, schedule, or delivery anytime.",
    )

    controls: list[ft.Control] = [header]
    # 0049 S-2a.4: directly under the header, because on a shared install "whose settings am I
    # editing?" is the first question every card below inherits the answer to. ``None`` on a
    # per-user install — see ``home_status.machine_scope_line``.
    provisioned_by, provisioned_at = machine_scope_provenance()
    scope_line = machine_scope_line(
        machine_scope=paths.is_machine_scope(),
        provisioned_by=provisioned_by,
        provisioned_at=provisioned_at,
    )
    if scope_line is not None:
        controls.append(ft.Text(scope_line, size=tokens.type_caption, color=tokens.color_muted))
    if transition_cue:
        controls.append(
            components.HealthVerdictBanner(Verdict.HEALTHY, headline="Setup complete", detail=TRANSITION_CUE)
        )
    # Settings order (user decision 2026-07-15, overriding the earlier #2a "schedule FIRST"): folders
    # & district FIRST (what/where), then schedule (when), then delivery (destination) — the user's
    # stated mental model. Mirrors the wizard's lead-with-identity reorder. Wizard step order lives in
    # setup_flow.STEP_ORDER; this is the flat post-setup scroll.
    #
    # WHO leads (0038 S4a) — the launch page asks it, so this is where the answer is
    # changeable and clearable. Landing an ask without a way to change or remove the answer
    # would be a half-done feature, which is why this section ships in the same slice.
    controls += [_build_identity_section(page, cfg), folders_card, schedule_card, sftp_card]
    root.controls = controls
    page.update()


# --------------------------------------------------------------------------- #
# "Who looks after this sync" — the identity section (0038 S4a).               #
# --------------------------------------------------------------------------- #
IDENTITY_TITLE = "Who looks after this sync"
# MINIMISATION HONESTY: matching uses the domain, but the WHOLE address is what gets stored
# and rendered — so "we use only the part after the @" would understate what is kept. Say
# what is matched AND what is saved, in that order.
IDENTITY_EXPLAINER = (
    "We match on the part after the @ — your district's email domain. "
    "The whole address is saved on this computer and nowhere else."
)
IDENTITY_NONE = "No one on file yet."
IDENTITY_FIELD_LABEL = "Work email address"
IDENTITY_FIELD_HELPER = "Leave it blank to remove it."
IDENTITY_CHANGE_LABEL = "Change"
IDENTITY_ADD_LABEL = "Add an address"
IDENTITY_SAVE_LABEL = "Save"
IDENTITY_CANCEL_LABEL = "Cancel"
# Blank clears — and the note branches on what actually happened, because "we also deleted
# the older copies" is a FALSE claim in two of the three outcomes (nothing to delete, and
# every unlink failed). The copies (`config.corrupt-*.json`) hold byte-for-byte duplicates
# of whatever `config.json` held when they were taken, so removing them is a real side
# effect on the admin's settings backups: stated, never done quietly, and never over-stated.
IDENTITY_CLEARED_NOTE = "Removed."
IDENTITY_CLEARED_WITH_COPIES_NOTE = "Removed — including the older copies of your settings file that held it."
IDENTITY_CLEARED_COPIES_LEFT_NOTE = (
    "Removed from your settings. We couldn't remove {n} older {copies} — they're still in your settings folder."
)
IDENTITY_SEVERAL_NOTE = "That email matches more than one setup — you'll choose the right one under Folders & district."
IDENTITY_NO_MATCH_NOTE = (
    "We don't have a district on file for that address yet — no problem. Nothing else has changed. "
    + UNMATCHED_DISTRICT_NOTE
)
IDENTITY_REFUSED_NOTE = "We couldn't save that just now. Your other settings are untouched."


def identity_cleared_note(removed: int, remaining: int) -> str:
    """The erasure note, branched on what the purge ACTUALLY did (never a blanket claim)."""
    if remaining:
        return IDENTITY_CLEARED_COPIES_LEFT_NOTE.format(n=remaining, copies="copy" if remaining == 1 else "copies")
    return IDENTITY_CLEARED_WITH_COPIES_NOTE if removed else IDENTITY_CLEARED_NOTE


def _build_identity_section(page: ft.Page, cfg: AppConfig) -> ft.Control:  # pragma: no cover - Flet view glue
    """The Settings home of the launch page's question: shown, changeable, CLEARABLE.

    Three rules this section exists to keep true:

    * **the stored value is re-validated at READ time.** ``config.json`` is hand-editable,
      so ``stored_identity_email`` runs the boundary validator before anything is rendered;
      a value that fails reads as UNANSWERED and is never echoed to the screen.
    * **every write goes through ``identity_save`` / ``identity_clear``** — the choke point
      that re-checks ``settings_unreadable()`` at write time and structurally cannot touch
      ``sis_type``. Changing WHO looks after the sync never changes WHICH district converts.
    * **blank clears, including the copies.** See ``AppConfig.identity_clear``.

    It adds no reconcile of its own and no filled primary: the identity is not a task-baked
    argument (the nightly action carries folders/district/delivery/run-time), so there is
    nothing for the schedule to reconcile, and the scroll's ONE reconciling Save stays the
    folders/SFTP pair.
    """
    state = {"editing": not stored_identity_email(cfg)}
    field = ft.TextField(
        label=IDENTITY_FIELD_LABEL,
        helper=IDENTITY_FIELD_HELPER,
        value=stored_identity_email(cfg),
        width=420,
        max_length=IDENTITY_EMAIL_MAX_LEN,
        border_color=tokens.color_border,
    )
    note = ft.Text("", size=tokens.type_body, weight=ft.FontWeight.W_600, color=tokens.color_status_healthy)
    body = ft.Column(spacing=tokens.space_lg)

    def _set_note(text: str, *, color: str = tokens.color_status_healthy) -> None:
        note.value = text
        note.color = color

    def _resolved_note(validated: str) -> str:
        """Re-run the SAME resolution the launch page runs — one rule, two surfaces."""
        index = district_domain_index()
        match = matched_state(extract_domain(normalize_email(validated)), index)
        if match.outcome is MatchOutcome.MATCHED_ONE:
            log_resolve("matched", 1, index)
            return f"Saved. {matched_headline(friendly_district_name(match.configs[0]) or match.configs[0])}"
        if match.outcome is MatchOutcome.MATCHED_SEVERAL:
            log_resolve("matched", len(match.configs), index)
            return f"Saved. {IDENTITY_SEVERAL_NOTE}"
        log_resolve("no_match", 0, index)
        return f"Saved. {IDENTITY_NO_MATCH_NOTE}"

    def _save(_e: ft.ControlEvent | None = None) -> None:
        typed = (field.value or "").strip()
        if not typed:
            outcome = cfg.identity_clear()
            if outcome.cleared:
                _set_note(identity_cleared_note(outcome.removed, outcome.remaining))
            else:
                _set_note(IDENTITY_REFUSED_NOTE, color=tokens.color_status_failed)
            field.value = ""
            state["editing"] = False
            _render()
            return
        try:
            validated = validate_identity_email(typed)
        except ValueError as exc:
            # The validator's messages carry the RULE, never the value (it is personal data).
            log_resolve("invalid", 0, {})
            _set_note(str(exc), color=tokens.color_status_failed)
            _render()
            return
        if not cfg.identity_save(identity_email=validated):
            _set_note(IDENTITY_REFUSED_NOTE, color=tokens.color_status_failed)
            _render()
            return
        _set_note(_resolved_note(validated))
        field.value = validated
        state["editing"] = False
        _render()

    def _start_editing(_e: ft.ControlEvent | None = None) -> None:
        state["editing"] = True
        note.value = ""
        _render()

    def _cancel(_e: ft.ControlEvent | None = None) -> None:
        field.value = stored_identity_email(cfg)
        state["editing"] = False
        note.value = ""
        _render()

    def _render() -> None:
        controls: list[ft.Control] = [
            ft.Text(IDENTITY_TITLE, size=tokens.type_title, weight=ft.FontWeight.W_800, color=tokens.color_text),
            ft.Text(IDENTITY_EXPLAINER, size=tokens.type_emphasis, color=tokens.color_muted),
        ]
        stored = stored_identity_email(cfg)
        if state["editing"]:
            actions = [components.secondary_button(IDENTITY_SAVE_LABEL, _save, icon=ft.Icons.CHECK_ROUNDED)]
            if stored:
                actions.append(components.text_button(IDENTITY_CANCEL_LABEL, _cancel))
            controls += [field, ft.Row(spacing=tokens.space_lg, controls=actions)]
        elif stored:
            controls += [
                ft.Text(
                    stored,
                    size=tokens.type_section,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_text,
                    selectable=True,
                ),
                components.secondary_button(IDENTITY_CHANGE_LABEL, _start_editing, icon=ft.Icons.EDIT_ROUNDED),
            ]
        else:
            controls += [
                ft.Text(IDENTITY_NONE, size=tokens.type_emphasis, color=tokens.color_muted),
                components.secondary_button(IDENTITY_ADD_LABEL, _start_editing, icon=ft.Icons.EDIT_ROUNDED),
            ]
        if note.value:
            controls.append(note)
        body.controls = controls
        page.update()

    _render()
    return components.card(content=body)


# ---- the verified-fact refusal on this Save (plan 0044 S6 §6.1) ---------- #
#: A Save that changes nothing has to SAY it changed nothing — and this Save also drives the
#: reconcile, so the second fact ("your nightly schedule was not updated") is the one an
#: admin would otherwise discover a night later. Structural: no district name, no path, no
#: digest. It names Mapping because that is where the test conversion lives.
FOLDERS_NEEDS_TEST_NOTE = (
    "Nothing was saved, and your nightly schedule was not updated. This mapping was set up "
    "on this computer, and it hasn't passed a test conversion as it now reads. Run one under "
    "Mapping, then save again."
)
FOLDERS_NEEDS_TEST_LINK_LABEL = "Open Mapping"


def _build_settings_folders(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    *,
    reconcile: Callable[[], ReconcileOutcome],
    on_navigate: Callable[[str], None] | None = None,
) -> ft.Control:
    """The Settings folders/district card with the ONE reconciling Save (D8).

    Saving persists the folders + district, then calls the shared ``reconcile`` (which re-registers
    the task when a task-baked field changed AND a schedule is registered — the SAME reconcile the
    SFTP Save uses, so the nightly action can never go stale). The Save is still structurally gated
    on valid folders.

    **This Save can now be REFUSED** (plan 0044 S6): it is one of the writers of
    ``sis_type``, and for a district set up on THIS computer the pure
    ``config_editor.activation_allowed`` is consulted BEFORE any write. Refused ⇒ no
    ``cfg.save()`` and no ``reconcile()`` at all — not a partial save that keeps the folders
    and drops the district, because "Saved." would then be a lie about the field that
    matters — plus :data:`FOLDERS_NEEDS_TEST_NOTE` and, when ``on_navigate`` is given, a
    text-tier hop to Mapping (where the test lives). SHIPPED districts are untouched by the
    check: the ``origin`` map comes from the SAME memoised catalog build the dropdown options
    do, and an id missing from it reads as ``"bundled"`` (fail OPEN — this prevents a mistake
    and may never strand an admin whose provenance we could not read).

    **Only a district CHANGE is gated** (plan 0044 S6 review, SHOULD 2): a folders-only edit
    on the district this install already converts activates nothing, so it saves and
    reconciles exactly as it always has — even while that district's own test is out of date.
    And both halves of the decision are read from a FRESH ``AppConfig`` (SHOULD 3), because
    the test conversion that records the digest runs on Mapping, not here.
    """
    state = {"input": cfg.input_dir, "output": cfg.output_dir, "sis": cfg.sis_type}
    # ONE catalog build for BOTH the options and the provenance map — no second parse, and no
    # second source of "where did this mapping come from?". Every id the dropdown can hold is
    # a row of this catalog by construction, so the map answers for every reachable pick.
    district_catalog = _district_catalog(cfg, picked_sis=cfg.sis_type)
    origins = {summary.sis_type: summary.origin for summary in district_catalog.summaries}
    save_btn = components.primary_button(
        # Scope-accurate label (0034 S3-c): this button saves the folders + district (and runs
        # the shared reconcile) — "Save settings" over-claimed the whole surface.
        "Save folders & district",
        None,  # wired below
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
        radius=12,
        text_size=14,
        text_weight=ft.FontWeight.W_700,
    )
    saved_note = ft.Text("", size=13, weight=ft.FontWeight.W_600)
    # The refusal lives in its own slot rather than in ``saved_note``: it is two sentences and
    # a route, not a status word, and it must be clearable independently of the "Saved." line.
    refusal_slot = ft.Column(spacing=tokens.space_sm, controls=[])

    def _refresh_gate() -> None:
        save_btn.disabled = not setup_state(state["input"], state["output"], state["sis"]).can_save

    def _on_input_change(path: str, _r: ValidationResult) -> None:
        state["input"] = path
        _refresh_gate()
        page.update()

    def _on_output_change(path: str, _r: ValidationResult) -> None:
        state["output"] = path
        _refresh_gate()
        page.update()

    def _on_district_change(e: ft.ControlEvent) -> None:
        state["sis"] = e.control.value or ""
        _refresh_gate()
        page.update()

    def _refuse_needs_test() -> None:
        """Say what did NOT happen (both halves), and put the fix one click away."""
        saved_note.value = ""  # a stale "Saved." beside a refusal is the worst of both
        row: list[ft.Control] = [
            ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=18, color=tokens.color_status_warning),
            ft.Text(FOLDERS_NEEDS_TEST_NOTE, size=tokens.type_body, color=tokens.color_status_warning, expand=True),
        ]
        controls: list[ft.Control] = [
            ft.Row(spacing=tokens.space_sm, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=row)
        ]
        if on_navigate is not None:
            controls.append(
                ft.Row(
                    controls=[
                        components.text_button(
                            FOLDERS_NEEDS_TEST_LINK_LABEL,
                            lambda _e: on_navigate("mapping"),
                            icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                        )
                    ]
                )
            )
        refusal_slot.controls = controls
        page.update()

    def _save(_e: ft.ControlEvent | None = None) -> None:
        if not setup_state(state["input"], state["output"], state["sis"]).can_save:
            return  # structural gate (matches the disabled button)
        # The verified-fact check, BEFORE any write (plan 0044 S6): this Save both sets the
        # district and re-registers the nightly, so letting an untested one through would bake
        # it into a scheduled task.
        #
        # A FRESH instance for BOTH halves (plan 0044 S6 review, SHOULD 3 — Mapping's Apply
        # already did this): the digest the rule looks for is recorded by the creator panel on
        # ANOTHER surface, so this mount's shared instance may not have seen the very test
        # conversion the admin has just run, and "is the district changing?" has to be asked
        # of what is on DISK now rather than of a snapshot. Only READ from here — the write
        # still goes through the shared ``cfg`` every other section on this scroll holds.
        persisted = AppConfig.load()
        picked = str(state["sis"])
        # ...and the check applies only to a district CHANGE (plan 0044 S6 review, SHOULD 2):
        # a folders-only edit on the district this install ALREADY converts activates nothing
        # — it is what the nightly runs either way — and refusing to let an admin fix a folder
        # path prevents nothing while blocking the repair. The refusal belongs to the act that
        # would switch districts.
        if picked != persisted.sis_type:
            origin = origins.get(picked, "bundled")
            verdict = activation_allowed(
                persisted,
                sis_id=picked,
                origin=origin,
                # ``None`` on the bundled branch deliberately — the rule never reads it there,
                # so the shipped rows pay no config load.
                current_digest=current_digest(picked) if origin == "user" else None,
            )
            if not verdict.allowed:
                _refuse_needs_test()
                return
        refusal_slot.controls = []  # a Save that lands clears the refusal it replaces
        cfg.input_dir = state["input"]
        cfg.output_dir = state["output"]
        cfg.sis_type = state["sis"]
        cfg.save()
        # The shared reconcile re-registers the task when a task-baked field changed; the schedule
        # section surfaces its own in-flight + confirmed/failed states when it fires. The note is
        # honest to what actually happened (S3 fix): "updating…" ONLY when a register was
        # dispatched — an interrupt that merely opened the downgrade dialog defers instead.
        saved_note.value = folders_save_note(reconcile())
        saved_note.color = tokens.color_status_healthy
        page.update()

    save_btn.on_click = _save

    input_field = PickerField(
        page=page,
        label="Input folder (MyEd BC extract)",
        helper="The folder DistrictSync reads your General Data Extract files from.",
        validator=validate_input_dir,
        on_change=_on_input_change,
        dialog_title="Select the MyEd BC extract folder",
        initial_value=cfg.input_dir,
    )
    output_field = PickerField(
        page=page,
        label="Output folder (SpacesEDU CSVs)",
        helper="Where DistrictSync writes the converted CSV files.",
        validator=validate_output_dir,
        on_change=_on_output_change,
        dialog_title="Select the output folder",
        initial_value=cfg.output_dir,
    )
    district_dropdown = ft.Dropdown(
        label="District",
        hint_text="Choose your district",
        value=cfg.sis_type or None,
        # `picked_sis` carries the saved district unconditionally, so the control can never
        # point at a row it does not offer. Built ONCE: with the show-all toggle retired the
        # option list no longer changes during a visit.
        options=_district_options(district_catalog),
        on_select=_on_district_change,
        border_color=tokens.color_border,
    )

    _refresh_gate()

    return components.card(
        content=ft.Column(
            spacing=26,
            controls=[
                ft.Text("Folders & district", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
                input_field,
                output_field,
                district_dropdown,
                ft.Row(spacing=16, controls=[save_btn, saved_note]),
                refusal_slot,
            ],
        )
    )


# --------------------------------------------------------------------------- #
# Schedule section — reused verbatim by the wizard Schedule step AND Settings.  #
# --------------------------------------------------------------------------- #
def _build_schedule_section(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    *,
    on_status: Callable[[ScheduleStatus], None] | None = None,
    on_schedule_changed: Callable[[], None] | None = None,
    on_window_valid: Callable[[bool], None] | None = None,
    on_busy: Callable[[bool], None] | None = None,
    on_reenter: Callable[[], None] | None = None,
    on_remount: Callable[[], None] | None = None,
    on_terminal: Callable[[ft.Control], None] | None = None,
    allow_gmsa: bool = False,
) -> tuple[ft.Control, _ScheduleHandle]:
    """The scheduler section — run time + (where supported) run-as password → register (Slice 5/6).

    Returns the card AND a ``_ScheduleHandle`` so Settings mode can drive re-registration on a
    task-arg change. ``on_status`` (when given) is called with each schedule read-back so the
    wizard can track live-ness for its resume + finish copy. (There is no post-register snapshot
    callback: a confirmed register writes ``cfg.schedule_task_args``, which IS the reconcile
    baseline, so the next Save is change-gated off reality rather than a refreshed guess.)
    ``on_schedule_changed`` (when given — the shell's badge re-probe, 0032 T1 #8) fires after a
    CONFIRMED register OR unregister success; advisory and exception-suppressed, so it can never
    break the result paint. ``on_busy`` (when given — the wizard's Continue gate) brackets the
    off-thread register/unregister with ``True``/``False``; it is raised in the SAME place the
    section disables its own buttons and cleared in the SAME place it re-enables them, so the
    wizard footer can never disagree with the section about whether a UAC prompt is pending. The register/unregister flow is UNCHANGED from Slice 5/6 (off-thread,
    elevation-aware, save-after-success). Platform dispatch goes through the ONE
    ``get_scheduler()`` factory (W4a T2.3): affordances gate on the scheduler's honest
    capability flags, never on ``sys.platform`` here.

    **Three plan-0049 S-2b seams, all optional and all "absent ⇒ no affordance":**
    ``on_reenter`` rebuilds the app body after a machine-scope handover (only the shell can);
    ``on_remount`` re-renders the HOST surface in place, which is what makes the confirm's
    one-click folder replacement honest — the folders card reads its fields from ``cfg`` at
    build time, so writing a path without re-mounting would leave a live control contradicting
    the config and a later folders Save would write the stale value back; ``on_terminal``
    receives the control to keep live and freezes the rest of the host, for the one outcome
    that cannot be recovered from in-session (the refused re-pin).

    **``allow_gmsa`` is the FIRST wizard/Settings fork in this function** (plan 0049 S-4). Until
    now the two mounts ran byte-identical code and differed only in which optional callbacks
    they passed, none of which is a mode flag. It is ``True`` only from ``_mount_settings``, and
    the default is the safe one: a first-run admin choosing a credential model nobody has been
    able to test is the wrong default, the wizard has no room for three IT prerequisites, and
    with it ``False`` the wizard keeps refusing a ``$``-suffixed account exactly as today.
    """
    scheduler = get_scheduler()

    run_time_field = ft.TextField(
        label="Daily run time (24-hour, HH:MM)",
        value=cfg.schedule_time or "03:00",
        width=220,
        border_color=tokens.color_border,
        helper="When DistrictSync runs each day — pick a time after your SIS extract lands.",
    )

    # Clock affordance that opens a TimePicker. The TextField stays the SINGLE SOURCE OF TRUTH
    # (all gating — can_register_schedule / validate_run_time / TaskArgs — reads run_time_field.value);
    # the picker only writes HH:MM back and typing remains allowed (an affordance, not a replacement).
    def _seed_time() -> datetime.time:
        """Seed the picker from the field's current HH:MM, falling back to the 03:00 default."""
        raw = (run_time_field.value or "").strip()
        try:
            hours, minutes = raw.split(":")
            return datetime.time(int(hours), int(minutes))
        except (ValueError, TypeError):
            return datetime.time(3, 0)

    def _open_time_picker(_e: ft.ControlEvent | None = None) -> None:
        def _on_time_confirmed(e: ft.ControlEvent) -> None:
            picked = e.control.value  # datetime.time (None if dismissed)
            if picked is None:
                return
            run_time_field.value = f"{picked.hour:02d}:{picked.minute:02d}"
            _refresh_register_gate()  # the SAME handler the field's on_change uses — re-gates + page.update

        # flet 0.85.3: TimePicker is a DialogControl → open via page.show_dialog; value is a
        # datetime.time; confirm fires on_change, cancel fires on_dismiss (see FLET_1.0_CONVENTIONS.md).
        page.show_dialog(
            ft.TimePicker(
                value=_seed_time(),
                help_text="Daily run time",
                confirm_text="Set",
                on_change=_on_time_confirmed,
            )
        )

    time_pick_button = ft.IconButton(
        icon=ft.Icons.ACCESS_TIME_ROUNDED,
        tooltip="Pick a time",
        on_click=_open_time_picker,
    )

    result_slot = ft.Column(spacing=0, controls=[])
    readout_slot = ft.Column(spacing=0, controls=[])

    # The last CONFIRMED read-back state, shared by the readout probe and the two surfaces that
    # may not assert a schedule they have not seen: the shared-records seasonal-window note (which
    # claims the pause applies only on LIVE) and — through ``_mount_settings``' ``on_status`` — the
    # delivery-password line's "no nightly sync is scheduled right now" arm. ``None`` = not probed,
    # which asserts nothing. A dict, not a bare name, so the closures share one mutable cell.
    _last_schedule_state: dict[str, ScheduleState | None] = {"state": None}

    def _kick_readout_probe() -> None:
        """Fetch the real schedule OFF the UI thread and render the tri-state readout (where supported)."""
        if not scheduler.supports_read_schedule:
            return

        def _work() -> None:  # runs OFF the UI thread
            from src.ui_flet.schedule_probe import foreign_task_account, probe_schedule

            # 0046 C: the readout is where the run-result sentence and the records-elsewhere note
            # land. Resolved inside the worker thread; fails to "", which keeps the alarms on.
            status = probe_schedule(
                cfg.schedule_task_name,
                hint_registered=cfg.schedule_registered,
                latest_record_ts=None,
                foreign_account=foreign_task_account(cfg),
                # 0049 S-2a.1: the readout is also where the records-elsewhere sentence lands, so
                # the shared-profile fact has to reach the derivation that composes it.
                shared_records=paths.is_machine_scope(),
                surface="setup",  # de-circularize the MISSING copy → "add one below" (finding #3)
            )

            async def _apply() -> None:
                readout_slot.controls = [_schedule_readout_line(status)]
                # 0049 S-2a.2: the seasonal-window note asserts the pause only on a CONFIRMED-LIVE
                # task, so it can only be painted honestly once the read-back has landed.
                _last_schedule_state["state"] = status.state
                _paint_window_foreign_note()
                if on_status is not None:
                    on_status(status)
                page.update()

            page.run_task(_apply)

        _kick_probe_thread(page, _work)

    def _refresh_readout() -> None:
        if not scheduler.supports_read_schedule:
            return
        readout_slot.controls = [ft.Text("Checking the schedule…", size=13, color=tokens.color_muted)]
        page.update()
        _kick_readout_probe()

    section_controls: list[ft.Control] = [
        ft.Text("Daily schedule", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
        ft.Text(
            "Schedule an unattended nightly sync so the roster keeps flowing without anyone signing in.",
            size=14,
            color=tokens.color_muted,
        ),
    ]
    if scheduler.supports_read_schedule:
        readout_slot.controls = [ft.Text("Checking the schedule…", size=13, color=tokens.color_muted)]
        section_controls.append(readout_slot)
    section_controls.append(
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[run_time_field, time_pick_button],
        )
    )

    password_field: ft.TextField | None = None
    account_field: ft.TextField | None = None
    account_note_slot = ft.Column(spacing=4, controls=[])
    # The gMSA disclosure's SESSION state (plan 0049 S-4) — never persisted. A dict so the
    # closures below share one mutable cell, the same shape ``_flight`` / ``_last_schedule_state``
    # use. Its opening value is SEEDED from the durable record rather than defaulted to False:
    # the record is the thing that survives, and a Settings mount over an install whose nightly
    # already runs as a managed service account must come up with the disclosure on — otherwise
    # the prefilled ``$`` account would meet ``validate_run_as_user`` and the gate would refuse
    # the install's own live principal, with a note about spelling.
    _gmsa = {"on": False}
    gmsa_disclosure_slot = ft.Column(spacing=tokens.space_xs, controls=[])
    password_slot = ft.Column(spacing=tokens.space_xs, controls=[])
    if scheduler.supports_unattended_password:
        # 0046 B: the static "This task will run as: X" caption becomes an editable field,
        # PREFILLED with the signed-in account. What makes the prefill safe is that the typed
        # value is sent VERBATIM and ``register_task`` compares it case-insensitively against
        # the current account — naming your own account is not a principal change. Sanitising
        # or re-casing here would bypass exactly that comparison (INVARIANTS).
        account_field = ft.TextField(
            label=SCHEDULE_ACCOUNT_FIELD_LABEL,
            value=cfg.schedule_run_as_user or _keyring_owner_account(),
            width=340,
            border_color=tokens.color_border,
            helper=(
                "Leave this as it is to run the sync as you. To use a service account, type its "
                "name (DOMAIN\\name if it has a domain) and enter that account's password below."
            ),
            helper_max_lines=3,
        )
        section_controls.append(account_field)
        section_controls.append(account_note_slot)
        password_field = ft.TextField(
            label="Windows account password",
            password=True,
            can_reveal_password=True,
            width=340,
            border_color=tokens.color_border,
            helper=(
                "Lets the nightly sync run after a reboot with no one signed in. "
                "Used once to schedule the task — DistrictSync does not store it."
            ),
        )
        # 0049 S-4: the password field + its caption move into a SLOT so the disclosure can take
        # them off screen. A managed service account has no password, and this file already names
        # the dead-control problem ("a disabled primary with no visible cause"); a live credential
        # field that cannot matter is the same defect one step further — it invites an admin to
        # type a secret into a control whose value would be refused.
        password_slot.controls = [
            password_field,
            ft.Text(
                "Leave the password blank to schedule a logged-on-only task "
                "(it will not run after a reboot with no one signed in).",
                size=12,
                color=tokens.color_muted,
            ),
        ]
        section_controls.append(password_slot)
        if allow_gmsa:
            _gmsa["on"] = _registered_schedule(cfg).run_as_kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT
            # Built with ``components.check_row`` — "the ONE checkbox factory" — and NOT with the
            # raw ``ft.Switch`` the seasonal-window section a few hundred lines below uses. Both
            # patterns now co-exist in this file, so the choice is worth stating: the design
            # system's build-via-factories rule is authoritative and outranks the nearer
            # precedent (that switch predates the factory). ``check_row`` also hands the closure
            # the new BOOLEAN rather than the event, which is what stops a screen reading a
            # stale ``e.control.value``.
            #
            # The lambda is not redundant: the control has to be built HERE (its place in
            # ``section_controls`` is its place on screen) while ``_on_gmsa_toggled`` needs the
            # closures defined below it, so the reference has to be deferred to call time.
            section_controls.append(
                components.check_row(
                    SCHEDULE_GMSA_TOGGLE_LABEL,
                    value=_gmsa["on"],
                    on_toggle=lambda on: _on_gmsa_toggled(on),
                )
            )
            section_controls.append(gmsa_disclosure_slot)

    def _elevated_now() -> bool:
        return scheduler.is_elevated()  # always False where elevation has no meaning (cron)

    def _declared_kind() -> PrincipalKind:
        """Which :class:`PrincipalKind` THIS press declares (plan 0049 S-3/S-4).

        The ONE place the session's three-way answer is decided, read by the gate
        (``_account_facts``), by the classifier call sites and by the ``Principal`` each worker
        builds — so the thing the gate validated and the thing Windows is asked for cannot
        diverge. The disclosure's tick box is the only input that can produce
        ``MANAGED_SERVICE_ACCOUNT``; it exists on the Settings mount alone, so the wizard's
        answer is structurally the same two kinds it has always had.

        A typed password still selects ``PASSWORD`` over ``INTERACTIVE_TOKEN``, unchanged. It
        cannot collide with the MSA arm: turning the disclosure on hides AND CLEARS the password
        field, so ``password_supplied`` is False by the time this is read.
        """
        if _gmsa["on"]:
            return PrincipalKind.MANAGED_SERVICE_ACCOUNT
        typed_password = (password_field.value or "") if password_field is not None else ""
        return PrincipalKind.PASSWORD if typed_password else PrincipalKind.INTERACTIVE_TOKEN

    def _gmsa_disclosure_controls() -> list[ft.Control]:
        """What the gMSA tick box reveals: the hedge, the three prerequisites, where to get them.

        The caption is NEW copy on the existing muted-note primitive
        (``tokens.type_caption`` + ``color_muted``) — worth saying, because there is no
        "untested" caption anywhere else in ``src/ui_flet`` to reuse. Nothing in this app has
        ever been run against a live domain controller, so it is the only honest thing the
        surface can lead with, and it is single-sourced at
        ``setup_flow.GMSA_UNTESTED_CAPTION`` (the downgrade interrupt and the classifier's MSA
        arm say it in the same words).

        The prerequisites render as READ-ONLY tick glyphs, deliberately not ``check_row``: none
        of the three is knowable from here (``validate_gmsa_account`` is a shape check, and
        Windows answers the real questions only at registration), so a control an admin could
        tick would be recording a claim this app cannot check.

        There is no clickable document link and that is not an omission: the MkDocs site was
        removed, so a partner doc has no URL, and a bare one would be the dead click
        ``screens/help.py`` renders selectable text beside every button to avoid. The document
        is NAMED instead, so an admin can ask for it by name.
        """
        rows: list[ft.Control] = [
            ft.Text(GMSA_UNTESTED_CAPTION, size=tokens.type_caption, color=tokens.color_muted),
            ft.Text(
                "Before this can work, your IT team needs to have done all three of these:",
                size=tokens.type_caption,
                color=tokens.color_muted,
            ),
        ]
        rows += [
            ft.Row(
                spacing=tokens.space_sm,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    # Sized from the type scale, not a bare px number: the glyph reads as part
                    # of the caption line it sits beside, so one token governs both.
                    ft.Icon(ft.Icons.CHECK_ROUNDED, size=tokens.type_section, color=tokens.color_muted),
                    ft.Text(item, size=tokens.type_caption, color=tokens.color_muted, expand=True),
                ],
            )
            for item in GMSA_PREREQUISITES
        ]
        rows.append(
            ft.Text(
                f"Your SpacesEDU contact can send you '{GMSA_IT_DOC_TITLE}' — one page your IT "
                "team can work from. The Help page has our support contact.",
                size=tokens.type_caption,
                color=tokens.color_muted,
            )
        )
        return rows

    def _paint_gmsa_disclosure() -> None:
        """Show/hide the two halves the tick box swaps. Called at build time and on every toggle.

        Hiding the password field CLEARS it as well: a credential must not survive in a control
        that is off screen, and leaving one there would let ``_account_facts`` read a password
        for a principal that has none — the inconsistent state ``Principal.__post_init__``
        refuses with a ``ValueError``, reported as a generic account-shape failure.
        """
        on = bool(_gmsa["on"])
        password_slot.visible = not on
        if on and password_field is not None:
            password_field.value = ""
        gmsa_disclosure_slot.controls = _gmsa_disclosure_controls() if on else []

    def _on_gmsa_toggled(on: bool) -> None:
        """The tick box's ONE handler: swap the two halves, then re-gate + repaint the reason.

        Switching kind changes which validator ``typed`` goes through AND whether the password
        rung applies, so the gate's answer can change without a keystroke. Routing through the
        same ``_refresh_register_gate`` every field uses is what keeps the button's ``disabled``
        state and the note under the field in step with the tick box.
        """
        _gmsa["on"] = bool(on)
        _paint_gmsa_disclosure()
        _refresh_register_gate()

    def _delivery_unreadable() -> bool:
        """The delivery-secret gate input — ONE store read, resolved per PAINT PASS (0049 S-2b.1).

        Effectful (it reads the machine store or the keyring through ``select_store()``), which
        is why every paint pass resolves it ONCE and hands the value to both the button gate
        and the field note rather than letting each call site read again. It is deliberately
        NOT memoised for the section's lifetime: the note it drives tells the admin to save the
        delivery password in the card below, and a cached ``True`` would keep the gate shut
        after they did.

        Total by the engine's own contract, and fails CLOSED — "we could not find out" is
        reported as unreadable, because a wrong ``False`` hands them a permanently
        machine-scoped computer whose nightly silently stops delivering.
        """
        return delivery_secret_unreadable(
            enabled=bool(cfg.sftp_enabled),
            host=cfg.sftp_host or "",
            username=cfg.sftp_username or "",
        )

    def _gate_block(facts: ScheduleAccountFacts, unreadable: bool) -> RegisterBlock:
        """``register_block`` with this section's two live inputs — the ONE spelling.

        Both new keywords are required and undefaulted upstream; funnelling every call site
        through here is what stops one of them being reconstructed differently.
        """
        return register_block(
            cfg.is_complete(),
            run_time_field.value or "",
            account=facts,
            delivery_secret_unreadable=unreadable,
        )

    def _account_facts(*, force_blank_password: bool = False) -> ScheduleAccountFacts:
        """The ONE place the principal gate's inputs are assembled (0046 B).

        ``typed`` is ``.strip()``ed and NOTHING else — ``validate_run_as_user`` itself strips and
        never re-cases, so the value is byte-identical to what the validator would return and
        ``register_task``'s ``requested.casefold() != current.casefold()`` is preserved exactly.
        ``force_blank_password`` blanks the PASSWORD only, never the account: under the switch
        refusal the gate owns the principal, and clearing the field there would wipe a correct
        prefill on the ordinary signed-in-only path for no benefit.
        """
        typed = "" if account_field is None else (account_field.value or "").strip()
        password = "" if force_blank_password else ((password_field.value or "") if password_field is not None else "")
        record = _registered_schedule(cfg)
        return ScheduleAccountFacts(
            typed=typed,
            current=_keyring_owner_account(),
            password_supplied=bool(password),
            recorded=record.run_as_user,
            schedule_registered=bool(cfg.schedule_registered),
            # 0049 S-4: the SAME ``_declared_kind()`` the worker's ``Principal`` is built from,
            # so the kind the gate validated against is the kind Windows is asked for. Reading
            # it twice from two derivations is how a gate comes to pass a name its engine
            # refuses.
            kind=_declared_kind(),
        )

    def _register_refusal_controls(block: RegisterBlock, facts: ScheduleAccountFacts) -> list[ft.Control]:
        """What a REFUSED register press puts in ``result_slot`` — never what the last press left.

        The slot is where every dispatched attempt reports (spinner, banner, failure card), so a
        refusal that paints only the field note leaves the PREVIOUS attempt's failure card
        standing and reads as "nothing happened" (the 2026-09-17 report: a failed register, then a
        retry the gate refused, and the admin saw no change at all). A reason returns a card; the
        two silent reasons return an empty list, which still REPLACES the stale card.

        ``log_folder=False``: nothing was attempted, so the log holds nothing about this press —
        offering it would send the admin to a file that cannot explain the refusal.
        """
        detail = account_block_note(block, facts)
        if not detail:
            return []
        return [components.ErrorCard(_REGISTER_REFUSED_HEADLINE, detail, log_folder=False)]

    def _account_note_controls(
        facts: ScheduleAccountFacts | None = None, unreadable: bool | None = None
    ) -> list[ft.Control]:
        """The inline block reason + the scope foreshadow + the service-account delivery note.

        A disabled primary with no visible cause is a dead control, so the Register gate's
        REASON is painted right under the field it is about, live on every keystroke.

        ``facts`` / ``unreadable`` are the PAINT PASS's already-resolved inputs. They are
        parameters rather than reads because ``unreadable`` costs a store read: the caller that
        also re-gates the button resolves both once and hands them to both consumers. Omitted
        (the plain repaint) they are resolved here — so no call site can forget one.
        """
        if account_field is None:
            return []
        facts = _account_facts() if facts is None else facts
        unreadable = _delivery_unreadable() if unreadable is None else unreadable
        controls: list[ft.Control] = []
        block = _gate_block(facts, unreadable)
        note = account_block_note(block, facts)
        if note:
            controls.append(ft.Text(note, size=13, color=tokens.color_status_failed))
        # 0049 S-2b.2 — the FORESHADOW. Keyed on the TYPED account alone, not on the gate: a
        # permanent machine-wide relocation must be on screen while the admin is still typing
        # the name, not first mentioned in the modal that is the point of no return. It is
        # therefore shown even while the gate is closed — including under the delivery-secret
        # note above, which is exactly the pairing that explains why that note matters.
        if principal_key(facts.typed, facts.current) != "":
            controls.append(
                ft.Text(
                    _SCOPE_FORESHADOW_NOTE_SHARED if _machine_scope_now() else _SCOPE_FORESHADOW_NOTE,
                    size=tokens.type_caption,
                    color=tokens.color_muted,
                )
            )
        # Owner decision 2: named only where it is TRUE and actionable — delivery is on AND a
        # service account is in play on either side (typed now, or already registered).
        #
        # 0049 S-2a.3: WHICH note is keyed on "would pressing Schedule provision?" — the TYPED
        # principal being foreign and every gate open — never on ``is_machine_scope()``, which is
        # still False at the moment this paints.
        foreign = principal_key(facts.typed, facts.current) != "" or principal_key(facts.recorded, facts.current) != ""
        delivery_note = service_account_delivery_note(
            delivery_enabled=bool(cfg.sftp_enabled),
            foreign=foreign,
            will_provision=principal_key(facts.typed, facts.current) != "" and block is RegisterBlock.NONE,
        )
        if delivery_note:
            controls.append(ft.Text(delivery_note, size=tokens.type_caption, color=tokens.color_muted))
        return controls

    def _paint_account_note(facts: ScheduleAccountFacts | None = None, unreadable: bool | None = None) -> None:
        account_note_slot.controls = _account_note_controls(facts, unreadable)

    def _repaint_register_gate() -> None:
        """ONE paint pass: one delivery-secret read feeding BOTH the button gate and the note.

        Every caller that re-gates the button also repaints its reason (a disabled primary with
        no visible cause is a dead control), and both reads are the same ``register_block``
        call. Collapsing them here is what keeps the effectful ``_delivery_unreadable()`` to
        one store read per pass rather than one per call site.
        """
        facts = _account_facts()
        unreadable = _delivery_unreadable()
        register_btn.disabled = _gate_block(facts, unreadable) is not RegisterBlock.NONE
        _paint_account_note(facts, unreadable)

    # The section's own in-flight fact (2026-08-31): recorded UNCONDITIONALLY in _set_busy —
    # the same single place the buttons toggle — and exposed via the handle so the Settings
    # reconcile can refuse to decide anything while an apply dispatched earlier is still
    # running. A dict, not a bare bool, so the closures share one mutable cell.
    _flight = {"busy": False}

    def _set_busy(busy: bool) -> None:
        """Record the in-flight fact + tell the wizard footer (advisory) it changed.

        Exception-suppressed for the same reason ``on_schedule_changed`` is: this drives a
        Continue gate, and a raise from the wizard's callback must never strand the section's
        own result paint. Called ONLY where the section toggles its own buttons, so the
        buttons, the handle's ``is_busy`` and the wizard gate cannot drift apart.
        """
        _flight["busy"] = busy
        if on_busy is None:
            return
        with contextlib.suppress(Exception):
            on_busy(busy)

    def _dispatch(work: Callable[[], None]) -> None:
        """Run ``work`` off the UI thread with the busy flag RAISED, clearing it if dispatch fails.

        ``page.run_thread`` handing the worker off is what makes the flag safe to leave set —
        the worker's own result handler lowers it. A raise HERE means no worker ever runs, so
        the flag would latch and disable Continue for the rest of the wizard; clearing it on
        that path is what keeps the gate from outliving the thing it is gating.
        """
        _set_busy(True)
        try:
            page.run_thread(work)
        except Exception:
            _set_busy(False)
            raise

    def _register(
        _e: ft.ControlEvent | None = None,
        *,
        force_blank_password: bool = False,
        scope_confirmed: bool = False,
    ) -> bool:
        """Start the off-thread register; True iff a register was actually DISPATCHED.

        The return value is the reconcile's honesty seam (0034 S3 residual): an early
        return (gate closed, or an invalid run time — whose inline error paints below)
        dispatches NOTHING, and the Settings-Save note must not claim "updating…" for it.
        The button/Enter callers ignore the return value.

        ``scope_confirmed`` (0049 S-2b.2) is the confirm's ONE key. It is a parameter rather
        than a latch on the section precisely so it cannot outlive the press it belongs to: a
        foreign principal reaches the confirm dialog on EVERY press and dispatches only from
        inside its Continue handler. Nothing else in this file passes it, and the default is
        the refusing value.
        """
        facts = _account_facts(force_blank_password=force_blank_password)
        unreadable = _delivery_unreadable()
        block = _gate_block(facts, unreadable)
        if block is not RegisterBlock.NONE:
            # The note under the field stays (it is still right) but can no longer be the ONLY
            # feedback — it sits nowhere near the card the admin is looking at. The result slot is
            # REPLACED on every refusal, painted for the principal reasons and CLEARED for the two
            # silent ones, so a previous attempt's failure card can never survive a fresh press.
            _paint_account_note(facts, unreadable)
            result_slot.controls = _register_refusal_controls(block, facts)
            page.update()
            return False

        run_time = (run_time_field.value or "").strip()
        try:
            validate_run_time(run_time)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(_RUN_TIME_ERROR_HEADLINE, _RUN_TIME_ERROR_DETAIL),
            ]
            page.update()
            return False

        # 0049 S-2b.2/3: a FOREIGN principal does not get the ordinary register — it gets the
        # provisioning round trip, which permanently moves this computer's settings. The
        # confirm is therefore structurally in front of the dispatch: no confirm, no dispatch,
        # and the modal's own Continue handler is the only thing that can pass the key back.
        #
        # It sits AFTER the run-time check and BEFORE the password read, and both halves of that
        # placement are deliberate. ``register_block`` only refuses a BLANK run time, so a
        # MALFORMED one ("99:99") would otherwise ask an admin to approve an irreversible,
        # machine-wide change and only then tell them the time was wrong. And the password is
        # not read until the confirm is answered, so no credential sits in a closure for as
        # long as a modal is on screen.
        provisioning_run = _will_provision(facts)
        if provisioning_run and not scope_confirmed:
            _show_scope_confirm(lambda: _register(scope_confirmed=True))
            return False

        # I1/I3 (see module docstring — password contract): the Windows account password is a
        # handler-LOCAL var whose ONLY sink is register_task (DPAPI elevation handshake / child-env);
        # never cfg, never argv, never a log/message, never stashed past this handler.
        # ``force_blank_password`` (FIX 2) makes the signed-in-only downgrade choice register with an
        # EXPLICITLY blank password rather than depending on transient UI state — the logged-on-only
        # outcome no longer relies on the field happening to be empty at click time.
        password = None if force_blank_password else (password_field.value if password_field is not None else None)
        # 0046 B: the TYPED account, verbatim (stripped only), or None for "the signed-in
        # account". ``principal_key`` is deliberately NOT used to decide what to send — if the
        # view's reduction ever drifted from the engine's, sending None for a genuinely foreign
        # account would be exactly the silent substitution Slice 1 exists to prevent.
        sent_account = _account_facts(force_blank_password=force_blank_password).typed or None
        # 0049 S-4: captured ONCE, at click time, from the section's single reduction — then
        # read by the ``Principal`` each worker builds, by the unattended facet the record
        # persists, by the spinner's copy and by the classifier the result paints with. A
        # second read inside the worker could see a tick box the admin moved while a UAC
        # prompt was up, and the record would then describe a different principal from the
        # one Windows was asked for. ``force_blank_password`` is honoured through the same
        # reduction: it blanks the password, which downgrades PASSWORD to INTERACTIVE_TOKEN
        # and cannot touch the MSA answer (that arm has no password to blank).
        declared_kind = (
            PrincipalKind.INTERACTIVE_TOKEN
            if force_blank_password and _declared_kind() is PrincipalKind.PASSWORD
            else _declared_kind()
        )

        def _declared_principal() -> Principal:
            """WHO this press asks the nightly to run as — DECLARED, not inferred (0049 S-3).

            The engine no longer reads a logon type out of "is there a password?"; the caller
            says which of ``PrincipalKind``'s three shapes it means, through the section's ONE
            ``_declared_kind()`` reduction — the same one the gate validated the typed name
            against. Since S-4 the Settings mount can name all THREE: the gMSA tick box is the
            only input that produces ``MANAGED_SERVICE_ACCOUNT``, and the wizard, which never
            renders it, still names the same two kinds it always did.

            The password is passed only where one belongs. On the MSA arm it is dropped
            explicitly rather than relied upon to be empty: the disclosure clears the field, so
            it already is, but ``Principal`` REFUSES a managed service account carrying a
            password, and a structural ``None`` beats a field that happens to be blank.

            Called INSIDE each worker, never on the UI thread. ``Principal`` refuses the
            unrepresentable kind/account/password combinations with a ``ValueError``, and the
            one an admin could type — a ``$``-suffixed account with a password — is ALREADY
            refused in front of this by ``setup_gates.register_block``'s ``ACCOUNT_SHAPE``
            rung, which runs the kind's OWN validator over any foreign account before anything
            is dispatched. So this call cannot raise from this surface; building it inside the
            worker is what keeps that true if a future gate change lets one through — the
            register worker's ``except ValueError`` already answers with
            ``_WORKER_ERROR_ACCOUNT_SHAPE``, which is the right sentence for it.
            """
            if declared_kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
                return Principal(kind=declared_kind, user=sent_account or "", password=None)
            return Principal(kind=declared_kind, user=sent_account or "", password=password or None)

        exe_path = Path(sys.executable)
        transient = is_transient_location(str(exe_path))
        # 0049 S-4: keyed on the KIND, matching ``windows.register_task``'s own self-elevation
        # predicate ("the KIND, not the presence of a password"). A managed service account is
        # unattended and carries none, so keying the spinner's copy on the password would have
        # promised no UAC prompt and then raised one.
        uac_path = (
            scheduler.supports_unattended_password
            and declared_kind is not PrincipalKind.INTERACTIVE_TOKEN
            and not _elevated_now()
        )
        # 0034 S3-d: the exact task-baked args this registration carries (captured at click time,
        # alongside run_time) — persisted on confirmed success as the durable reconcile baseline.
        registered_args = TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=run_time,
        )

        def _persist_registered_record() -> None:
            """The four-facet register record — the ONE spelling, both dispatch paths.

            Extracted at 0049 S-2b.3 because the provisioning path needs exactly this and
            nothing else: ``complete_handover`` takes it as ``persist`` and runs it strictly
            AFTER the re-pin, so the facets land in the SHARED ``config.json`` rather than in
            the per-user one that is renamed seconds earlier. A second spelling of a
            four-field atomic record is how one of them comes to be forgotten.
            """
            cfg.schedule_time = run_time
            cfg.schedule_registered = True
            # 0034 S3-a/d: record what was ACTUALLY registered — the unattended fact (a boolean
            # only; the password itself stays handler-local per I1/I3) + the task-baked args.
            # Choosing the signed-in-only path re-registers with a blank password, so this line
            # also honestly flips the persisted flag to False on that path.
            #
            # 0049 S-4 keys it on the KIND rather than on ``bool(password)``. Byte-identical for
            # the two pre-S-4 kinds (a typed password IS what makes the kind PASSWORD), and it
            # is the only spelling that stays TRUE for a managed service account: that task runs
            # while nobody is signed in and carries no password, so ``bool(password)`` would
            # record an unattended task as logged-on-only and the reconcile would stop guarding
            # it against a silent downgrade.
            cfg.schedule_unattended = declared_kind is not PrincipalKind.INTERACTIVE_TOKEN
            cfg.schedule_task_args = task_args_to_persisted(registered_args)
            # 0046 B: the third facet of the ATOMIC record — the principal that was actually
            # registered. "" means the signed-in account. Written in the SAME save as the other
            # two, so the record can never be half-evidenced.
            cfg.schedule_run_as_user = sent_account or ""
            # 0049 S-4: the FOURTH facet, in the same save for the same reason. It is the one
            # fact nothing else can recover — the kind cannot be read back off the name without
            # re-introducing the ``$`` inference S-3 deleted.
            cfg.schedule_run_as_kind = declared_kind.value
            cfg.save()

        def _on_register_success(headline: str, detail: str, *, verdict: Verdict = Verdict.HEALTHY) -> None:
            _persist_registered_record()
            # 2026-08-31 race guard, confirm-side: the args were captured at CLICK time — if a
            # Save changed the config while this apply was in flight (a delivery Save during the
            # UAC window), the task just registered is ALREADY stale. Say so in the success note
            # rather than letting a green "scheduled" banner stand over a task that will not
            # deliver; the IN_FLIGHT save note has already told that Save to come back.
            current_args = TaskArgs.of(
                input_dir=cfg.input_dir,
                output_dir=cfg.output_dir,
                sis_type=cfg.sis_type,
                sftp_enabled=cfg.sftp_enabled,
                run_time=run_time,
            )
            if task_args_changed(registered_args, current_args):
                detail = (
                    f"{detail} Some settings changed while this was being set up, so tonight's "
                    "task doesn't include them yet — save your settings again to update it."
                )
            if on_schedule_changed is not None:
                # 0032 T1 #8: a confirmed register invalidates the boot-time rail badge —
                # let the shell re-probe. Advisory: never let it break the result paint.
                with contextlib.suppress(Exception):
                    on_schedule_changed()
            local_verdict, local_detail = verdict, detail
            if transient:
                local_verdict = Verdict.WARNING
                local_detail = f"{detail} {_TRANSIENT_LOCATION_WARNING}"
            result_slot.controls = [
                components.HealthVerdictBanner(local_verdict, headline=headline, detail=local_detail)
            ]

        # 0047 G4, wired at 0046 B: the coaching that names a Windows Hello PIN and a
        # microsoft.com password is right for the admin's OWN account and actively wrong — and
        # a credential-hygiene hazard — for a service account. Computed from what was SENT.
        account_is_current = principal_key(sent_account, _keyring_owner_account()) == ""

        async def _apply_result(ok: bool, msg: str) -> None:
            _set_busy(False)
            _repaint_register_gate()
            unregister_btn.disabled = False
            if ok and password:
                # Only reachable where the password affordance exists (supports_unattended_password).
                # G3: the banner names what was REGISTERED, never the signed-in account — a task
                # on a service account must not report someone else's name back.
                _on_register_success(
                    "Nightly sync scheduled",
                    f"Runs as {sent_account or _keyring_owner_account()}, whether or not you're "
                    f"signed in, daily at {run_time}.",
                )
            elif ok and scheduler.supports_unattended_password:
                _on_register_success(
                    "Scheduled — logged-on only",
                    "It will only run while you're logged in. Schedule it again with your "
                    "Windows password for unattended operation across reboots.",
                    verdict=Verdict.WARNING,
                )
            elif ok:
                # No unattended/interactive logon distinction on this platform (cron) — plain success.
                _on_register_success("Nightly sync scheduled", f"Runs daily at {run_time}.")
            elif msg in (_WORKER_ERROR_REGISTER, _WORKER_ERROR_ACCOUNT_SHAPE):
                result_slot.controls = [components.ErrorCard("Couldn't schedule the nightly sync", msg)]
            elif msg in (windows._MSG_ELEVATION_NO_RESULT, windows._MSG_ELEVATION_TIMEOUT):
                # Canonical elevation markers (exact equality) — cron never produces these strings.
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't confirm the schedule",
                        classify_schedule_error(
                            msg,
                            _elevated_now(),
                            account_is_current=account_is_current,
                            kind=declared_kind,
                        ),
                    )
                ]
            elif scheduler.supports_unattended_password:
                # The Windows message contract → the calm classifier (setup_errors).
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't schedule the nightly sync",
                        classify_schedule_error(
                            msg,
                            _elevated_now(),
                            account_is_current=account_is_current,
                            kind=declared_kind,
                        ),
                    )
                ]
            else:
                # Cron failures carry crontab's own short message — surfaced as-is (no Windows contract).
                result_slot.controls = [components.ErrorCard("Couldn't schedule the nightly sync", msg)]
            page.update()
            _refresh_readout()

        def _nightly_sentence(attempt: ProvisionAttempt) -> str:
            """The already-classified sentence about the NIGHTLY, for ``handover_result.compose``.

            Classified here, not in the engine, because only the view holds the two facts
            ``classify_schedule_error`` requires — ``elevated`` (the PARENT's token) and
            ``account_is_current`` (which selects personal-credential coaching that is wrong,
            and a credential-hygiene hazard, for a service account).

            A ``REFUSED`` attempt goes through ``classify_provision_step`` and NOT through
            ``classify_schedule_error``: that one keys on canonical messages by exact equality,
            and ``ProvisionRefused.message`` is interpolated (a step, sometimes an icacls exit
            code, sometimes a rollback clause), so it could never match a branch. The icacls
            exit code is appended HERE because the copy table deliberately carries no code.
            """
            if attempt.outcome is ProvisionOutcome.REFUSED and attempt.step is not None:
                detail = classify_provision_step(attempt.step)
                if attempt.icacls_exit is not None:
                    detail = f"{detail} (Windows permissions code {attempt.icacls_exit}.)"
                return detail
            if attempt.outcome is ProvisionOutcome.FAILED and attempt.message:
                return classify_schedule_error(
                    attempt.message, _elevated_now(), account_is_current=account_is_current, kind=declared_kind
                )
            canonical = _PROVISION_OUTCOME_CANONICAL.get(attempt.outcome, "")
            if canonical:
                return classify_schedule_error(
                    canonical, _elevated_now(), account_is_current=account_is_current, kind=declared_kind
                )
            # PROVISIONED (``compose`` ignores the detail on that arm) and UNAVAILABLE — the
            # handshake was never built, so there is no elevation canonical to classify.
            return "" if attempt.outcome is ProvisionOutcome.PROVISIONED else _WORKER_ERROR_REGISTER

        def _paint_scope_refusal(reason: paths.MachineScopeRefusedReason | None) -> None:
            """The TERMINAL surface: the switch committed, the re-pin refused.

            ``paths`` has no pin now, so every later ``user_data_dir()`` in this session
            raises — the admin is in a live window whose next click can only crash. Both of
            this section's buttons go dead and, when the host wired ``on_terminal``, the rest
            of the surface with them. The card itself stays live (it is what ``on_terminal``
            is told to keep) so its "Open log folder" affordance still works, which is the one
            action left that helps.
            """
            card = components.ErrorCard(SCOPE_REFUSED_HEADLINE, _scope_refusal_detail(reason))
            result_slot.controls = [card]
            register_btn.disabled = True
            unregister_btn.disabled = True
            if on_terminal is not None:
                # Advisory in the same sense ``on_schedule_changed`` is: a host that cannot
                # freeze itself must not cost the admin the card explaining why.
                with contextlib.suppress(Exception):
                    on_terminal(card)

        async def _apply_provision(attempt: ProvisionAttempt) -> None:
            """Finish the handover on the LOOP, then paint whichever outcome it ended in.

            ``complete_handover`` runs here rather than in the worker on purpose: its last step
            REBUILDS the app body, and the rest of it is a registry read plus a handful of
            renames — the same order of work ``_on_register_success`` already does on the loop.
            """
            _set_busy(False)
            nightly = _nightly_sentence(attempt)
            reentry = {"requested": False}

            def _persist() -> None:
                """The four-facet save — bound to the SUCCESS path and nothing else.

                ``complete_handover`` calls this on every outcome, including a declined UAC and
                a launch failure, because its own gate is the parent's switch read. Writing the
                record on those paths would make Home report a healthy nightly over a task that
                does not exist, which is why the guard is here rather than at the call site:
                today's ``_on_register_success`` is reached only when ``ok``, and that guard may
                not be dropped.
                """
                if attempt.outcome is not ProvisionOutcome.PROVISIONED:
                    return
                _persist_registered_record()

            def _reenter() -> None:
                """Record that the handover reached re-entry; the rebuild happens just below.

                ``complete_handover`` calls this strictly AFTER ``persist`` and then does
                nothing but build its return value, so deferring the rebuild by those few
                statements preserves the spec's ordering rule exactly — and lets the one-shot
                be composed from the REAL ``HandoverOutcome`` instead of one this file
                fabricated. ``remember`` still happens before the rebuild, which is the
                ordering that matters: the rebuild destroys this surface, banner and all.
                """
                reentry["requested"] = True

            try:
                handover = complete_handover(persist=_persist, reenter=_reenter)
            except Exception as exc:  # noqa: BLE001 - the handover already happened; report it
                # A raise out of ``persist`` propagates by design (a facet save that did not
                # happen must not look like one that did). The scope may or may not have
                # changed; say only what we know and leave the buttons live.
                logger.error("The shared-settings change did not finish cleanly: %s", type(exc).__name__)
                result_slot.controls = [components.ErrorCard(SCOPE_UNCONFIRMED_HEADLINE, _WORKER_ERROR_REGISTER)]
                _repaint_register_gate()
                unregister_btn.disabled = False
                page.update()
                _refresh_readout()
                return

            result = handover_result.compose(attempt, handover, nightly_detail=nightly)
            if result is not None and reentry["requested"] and on_reenter is not None:
                handover_result.remember(result)
                try:
                    on_reenter()
                    page.update()
                    return
                except Exception as exc:  # noqa: BLE001 - fall back to reporting it in place
                    logger.error("Could not rebuild DistrictSync after the change: %s", type(exc).__name__)
                    # Drain what we just parked: this surface is still on screen and paints the
                    # report itself below, and a result left in the slot would announce the
                    # change a SECOND time on the next hop to Home.
                    handover_result.take()

            if result is not None and result.banner is HandoverBanner.REFUSED:
                _paint_scope_refusal(result.refused_reason)
                page.update()
                return

            _repaint_register_gate()
            unregister_btn.disabled = False
            if result is not None:
                # Handed over, but nothing rebuilt (no ``on_reenter``, or the rebuild raised).
                # The SAME banner Home would have shown — one renderer, one wording.
                result_slot.controls = [handover_banner(result)]
                if attempt.outcome is ProvisionOutcome.PROVISIONED and on_schedule_changed is not None:
                    # The rail badge is re-probed by ``build_app_body``'s own tail on the
                    # re-entry path, so this fires only where no rebuild happened.
                    with contextlib.suppress(Exception):
                        on_schedule_changed()
            elif attempt.outcome is ProvisionOutcome.PROVISIONED:
                # The child reported everything done and the parent's switch read disagrees.
                # The nightly EXISTS (``persist`` just wrote the record); the scope change is
                # what could not be confirmed, and the copy claims exactly that much.
                result_slot.controls = [
                    components.HealthVerdictBanner(
                        Verdict.WARNING,
                        headline=SCOPE_SWITCH_UNCONFIRMED_HEADLINE,
                        detail=SCOPE_SWITCH_UNCONFIRMED_DETAIL,
                    )
                ]
                if on_schedule_changed is not None:
                    with contextlib.suppress(Exception):
                        on_schedule_changed()
            else:
                # Nothing irreversible happened: the switch is off, this session did not hand
                # over, and Setup is still mounted — so its ordinary result slot is the right
                # place for the failure, and a one-shot banner would ambush the admin on Home
                # for a change that never took place.
                headline = (
                    SCOPE_UNCONFIRMED_HEADLINE
                    if attempt.outcome is ProvisionOutcome.UNCONFIRMED
                    else SCOPE_ATTEMPT_FAILED_HEADLINE
                )
                result_slot.controls = [components.ErrorCard(headline, nightly)]
            page.update()
            _refresh_readout()

        def _provision_work() -> None:  # runs OFF the UI thread (it blocks on the UAC prompt)
            try:
                attempt = request_provision(
                    cfg.schedule_task_name,
                    exe_path,
                    cfg.sis_type,
                    Path(cfg.input_dir),
                    Path(cfg.output_dir),
                    run_time,
                    cfg.sftp_enabled,
                    sftp_host=cfg.sftp_host or "",
                    sftp_username=cfg.sftp_username or "",
                    # I1/I3: the password is still handler-local. ``request_provision`` seals
                    # it into a DPAPI payload — never argv, never an env var, never a log line,
                    # and it is not a field of the ``ProvisionAttempt`` that comes back. Built
                    # INSIDE the worker for the same reason the register twin is (below).
                    principal=_declared_principal(),
                )
            except Exception as exc:  # noqa: BLE001 - it contracts never to raise; belt anyway
                logger.error("The shared-settings change raised unexpectedly: %s", type(exc).__name__)
                attempt = ProvisionAttempt(outcome=ProvisionOutcome.UNAVAILABLE)
            page.run_task(_apply_provision, attempt)

        if provisioning_run:
            register_btn.disabled = True
            unregister_btn.disabled = True
            result_slot.controls = [
                components.inflight_row(
                    "Asking Windows for permission, setting up shared settings and scheduling the nightly sync…"
                )
            ]
            page.update()
            _dispatch(_provision_work)
            return True

        def _work() -> None:  # runs OFF the UI thread (the register call can block on the UAC prompt)
            try:
                ok, msg = scheduler.register(
                    task_name=cfg.schedule_task_name,
                    exe_path=exe_path,
                    sis_type=cfg.sis_type,
                    input_dir=Path(cfg.input_dir),
                    output_dir=Path(cfg.output_dir),
                    run_time=run_time,
                    sftp=cfg.sftp_enabled,
                    principal=_declared_principal(),
                )
            except ValueError:
                # validate_run_as_user, raised inside register_task or the elevated child. The
                # exception's own message interpolates the TYPED value, so neither the log line
                # nor the rendered copy echoes it.
                logger.error("Scheduling the nightly sync rejected the run-as account name.")
                ok, msg = False, _WORKER_ERROR_ACCOUNT_SHAPE
            except Exception as exc:  # noqa: BLE001 - a worker crash must not strand the spinner
                logger.error("Scheduling the nightly sync raised unexpectedly: %s", type(exc).__name__)
                ok, msg = False, _WORKER_ERROR_REGISTER
            page.run_task(_apply_result, ok, msg)

        register_btn.disabled = True
        unregister_btn.disabled = True
        result_slot.controls = [
            components.inflight_row(
                "Asking Windows for permission and scheduling the nightly sync…"
                if uac_path
                else "Scheduling the nightly sync…"
            )
        ]
        page.update()
        _dispatch(_work)
        return True

    def _unregister(_e: ft.ControlEvent | None = None) -> None:
        # Read from the RECORD before the clear (0046 B): on a service-account install the
        # honest value comes from the registered principal, not the signed-in account. Inert
        # today (a delete never produces MSG_LOGON_FAILURE) — but a hardcoded True beside two
        # wired sites is exactly the drift the required keyword was introduced to prevent.
        _removed_record = _registered_schedule(cfg)
        account_is_current = principal_key(_removed_record.run_as_user, _keyring_owner_account()) == ""
        # 0049 S-4: the RECORDED kind, read from the same record and before the same clear —
        # a remove classifies against the principal that WAS registered, never against the
        # section's live tick box, which the admin may have moved since. ``None`` (no usable
        # record) falls back to PASSWORD: it is the kind every pre-S-4 foreign task had, and
        # the copy it selects is today's, so an unproven record reads exactly as it does now.
        removed_kind = _removed_record.run_as_kind or PrincipalKind.PASSWORD

        async def _apply_unregister(ok: bool, msg: str) -> None:
            _set_busy(False)
            _repaint_register_gate()
            unregister_btn.disabled = False
            if not ok and msg == _WORKER_ERROR_UNREGISTER:
                result_slot.controls = [components.ErrorCard("Couldn't remove the nightly sync", msg)]
            elif not ok and msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED:
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't confirm the schedule was removed",
                        "We couldn't confirm the nightly schedule was removed — check the schedule "
                        "status above, then try again if it's still there.",
                    )
                ]
            elif not ok and msg in (
                windows._MSG_UAC_DECLINED,
                windows._MSG_ELEVATION_LAUNCH_FAILED,
                # A8: the cross-account rung fires on the remove path too (_apply serves both
                # ops). Without it here the diagnostic copy is swallowed by
                # interpret_unregister's generic floor and the admin is told to try again.
                windows._MSG_DIFFERENT_ACCOUNT,
            ):
                result_slot.controls = [
                    components.ErrorCard(
                        "Schedule not removed",
                        classify_schedule_error(
                            msg,
                            _elevated_now(),
                            account_is_current=account_is_current,
                            kind=removed_kind,
                        ),
                    )
                ]
            else:
                outcome = interpret_unregister(ok, msg)
                if outcome.success_shaped:
                    cfg.schedule_registered = False
                    # 0034 S3 + 0046 B + 0049 S-4: no task exists any more — all FOUR "what was
                    # registered" facts go together (an honest record; a later register rewrites
                    # them). ``account_field.value`` and the gMSA tick box are deliberately NOT
                    # touched, so Remove → Schedule works in one session without retyping the
                    # account or re-reading the three prerequisites.
                    cfg.schedule_unattended = False
                    cfg.schedule_task_args = None
                    cfg.schedule_run_as_user = ""
                    cfg.schedule_run_as_kind = ""
                    cfg.save()
                    if on_schedule_changed is not None:
                        # 0032 T1 #8: a confirmed removal invalidates the boot-time rail badge too.
                        with contextlib.suppress(Exception):
                            on_schedule_changed()
                    result_slot.controls = [
                        components.HealthVerdictBanner(
                            Verdict.HEALTHY, headline=outcome.headline, detail=outcome.detail
                        )
                    ]
                else:
                    result_slot.controls = [components.ErrorCard(outcome.headline, outcome.detail)]
            # 0046 B: a confirmed removal clears the record, so the switch refusal note must go
            # with it — the admin can press Schedule immediately with the account still typed.
            _repaint_register_gate()
            page.update()
            _refresh_readout()

        def _work() -> None:  # runs OFF the UI thread (an elevated delete can block on UAC)
            try:
                # The Windows adapter owns the access-denied → one-UAC-prompt elevated retry.
                ok, msg = scheduler.delete(cfg.schedule_task_name)
            except Exception as exc:  # noqa: BLE001 - a worker crash must not strand the spinner
                logger.error("Removing the nightly sync raised unexpectedly: %s", type(exc).__name__)
                ok, msg = False, _WORKER_ERROR_UNREGISTER
            page.run_task(_apply_unregister, ok, msg)

        register_btn.disabled = True
        unregister_btn.disabled = True
        result_slot.controls = [components.inflight_row("Removing the nightly sync…")]
        page.update()
        _dispatch(_work)

    def _persist_run_time_if_edited() -> bool:
        """0034 S3-b: persist a valid run-time edit as plain config (no register involved).

        Driven by the Settings reconcile when NO schedule is registered — the run time is
        config, not only a register side-effect, so a Save must not drop it. Invalid edits
        paint the SAME inline error the register flow shows and persist nothing.
        """
        decision = run_time_save_decision(saved_run_time=cfg.schedule_time, field_run_time=run_time_field.value or "")
        if decision.invalid:
            result_slot.controls = [
                components.ErrorCard(_RUN_TIME_ERROR_HEADLINE, _RUN_TIME_ERROR_DETAIL),
            ]
            page.update()
            return False
        if decision.persist is None:
            return False
        cfg.schedule_time = decision.persist
        cfg.save()
        return True

    def _will_provision(facts: ScheduleAccountFacts) -> bool:
        """Would pressing Schedule PROVISION this computer for shared settings? (0049 S-2b)

        The requested principal being FOREIGN is the whole condition, reduced through the SAME
        :func:`principal_key` every other principal comparison in the app goes through — never
        a second derivation of "is this a different account?". The capability flag rides along
        because it is what the account field's very existence is gated on; a platform without
        it cannot reach here anyway (no field ⇒ ``typed`` is ``""`` ⇒ the key reduces to the
        signed-in account).

        It is deliberately NOT gated on ``is_machine_scope()``. An already-shared computer
        still has to give the new principal access to the shared folder — "Remove nightly sync"
        does not un-provision, which is exactly what makes that state reachable — so the
        surfaces branch on the scope for their COPY, never for whether to run.
        """
        return scheduler.supports_unattended_password and principal_key(facts.typed, facts.current) != ""

    def _folder_reach_controls() -> list[ft.Control]:
        """The confirm's folder-reach WARNING — never a gate (0049 S-2b.1).

        ``unc.reach_unconfirmed`` is a heuristic that is wrong in BOTH directions, which is
        why ``FOLDER_NOT_SHAREABLE`` was demoted out of ``RegisterBlock``: it clears a UNC path
        the service account has no rights on, and it used to flag ``C:\\Users\\Public``, which
        every account can read (the helper exempts it). So this may not disable the confirm and
        may not be worded as a finding — "we can't confirm" is the whole claim it is entitled
        to make.

        A replacement button appears only for a folder Windows actually gave us a UNC target
        for AND only when the host wired ``on_remount``: writing a path without re-rendering
        would leave the folders card's live field contradicting the config, and its next Save
        would write the stale value straight back.
        """
        pairs = (
            (SCOPE_FOLDER_INPUT_LABEL, "input", describe_folder_reach(cfg.input_dir or "")),
            (SCOPE_FOLDER_OUTPUT_LABEL, "output", describe_folder_reach(cfg.output_dir or "")),
        )
        unconfirmed = [(label, which, reach) for label, which, reach in pairs if reach.unconfirmed]
        if not unconfirmed:
            return []
        listed = "  ".join(f"{label.capitalize()}: {reach.path}" for label, _which, reach in unconfirmed)
        controls: list[ft.Control] = [
            components.HealthVerdictBanner(
                Verdict.WARNING,
                headline=SCOPE_FOLDER_WARNING_HEADLINE,
                detail=f"{listed}  {_SCOPE_FOLDER_WARNING_DETAIL} {_SCOPE_FOLDER_REPLACE_HINT}",
            )
        ]
        if on_remount is None:
            return controls
        for label, which, reach in unconfirmed:
            if not reach.unc_replacement:
                continue
            controls.append(
                components.text_button(
                    SCOPE_FOLDER_REPLACE_LABEL.format(path=reach.unc_replacement, folder=label),
                    _folder_replacer(which, reach.unc_replacement),
                    icon=ft.Icons.DRIVE_FILE_MOVE_ROUNDED,
                )
            )
        return controls

    def _folder_replacer(which: str, path: str) -> Callable[[ft.ControlEvent], None]:
        """One-click swap of a mapped drive for the UNC target Windows resolved for it."""

        def _apply(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            try:
                if which == "input":
                    cfg.input_dir = path
                else:
                    cfg.output_dir = path
                cfg.save()
            except Exception as exc:  # noqa: BLE001 - a refused save (the MOVED.txt fence) included
                logger.warning("Could not save the network folder path: %s", type(exc).__name__)
                result_slot.controls = [
                    components.ErrorCard(_REGISTER_REFUSED_HEADLINE, SCOPE_FOLDER_REPLACE_FAILED_NOTE, log_folder=False)
                ]
                page.update()
                return
            # The host re-renders, so the folders card shows the new path — which is the proof
            # the swap worked. The "press Schedule again" instruction was given in the warning
            # ABOVE, before the click, because nothing painted here would survive this call.
            if on_remount is not None:
                with contextlib.suppress(Exception):
                    on_remount()

        return _apply

    def _show_scope_confirm(on_confirm: Callable[[], None]) -> None:
        """The point-of-no-return confirm for a machine-scope handover (0049 S-2b.2).

        Three sentences, and each one is there because an admin who was not told it would be
        entitled to feel misled: what moves and to where, who can reach it afterwards (every
        administrator of this computer — permanently — and nobody else at all), and that it
        cannot be undone, INCLUDING by the one thing they would try, "Remove nightly sync".

        Same shape as ``_show_downgrade_dialog`` and for the same reason: NO filled default.
        An irreversible, machine-wide change must be chosen, never defaulted into by pressing
        the button the dialog visually pre-selects. Continue is the OUTLINED tier; Cancel is
        text. The folder-reach warning rides inside and gates nothing.
        """

        def _cancel(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            # Nothing was attempted, so nothing is claimed and nothing is cleared: the account
            # and password the admin typed stay exactly where they were.
            page.update()

        def _continue(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            on_confirm()

        sentences = [
            _SCOPE_CONFIRM_ALREADY if _machine_scope_now() else _SCOPE_CONFIRM_MOVE,
            _SCOPE_CONFIRM_WHO,
            _SCOPE_CONFIRM_ONE_WAY,
        ]
        body: list[ft.Control] = [
            ft.Text(text, size=tokens.type_emphasis, color=tokens.color_text) for text in sentences
        ]
        body += _folder_reach_controls()
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(SCOPE_CONFIRM_TITLE),
                content=ft.Column(spacing=tokens.space_md, tight=True, controls=body),
                actions=[
                    components.text_button(SCOPE_CONFIRM_CANCEL_LABEL, _cancel),
                    components.secondary_button(SCOPE_CONFIRM_CONTINUE_LABEL, _continue),
                ],
            )
        )

    def _show_downgrade_dialog(interrupt: DowngradeInterrupt) -> None:
        """The explicit-choice dialog before a reconcile re-register may downgrade (S3-a).

        View glue only — every string comes from the pure ``DowngradeInterrupt``. The two
        choices are EQUAL-WEIGHT outlined buttons (no filled default that could read as "just
        continue" — the downgrade must be chosen, never defaulted); Cancel is the text tier.
        The password is never collected here: choosing to stay unattended routes the admin to
        the existing schedule-section password field (I1/I3 — the only sanctioned collection
        point), and the task stays untouched until they register.
        """

        def _keep(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            result_slot.controls = [
                components.HealthVerdictBanner(
                    Verdict.WARNING,
                    headline=interrupt.keep_next_headline,
                    detail=interrupt.keep_next_detail,
                )
            ]
            page.update()

        def _signed_in_only(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            # The explicit downgrade choice: register with an EXPLICITLY blank password (Interactive
            # / logged-on-only) via force_blank_password — never re-reading the transient password
            # field (FIX 2). Success persists schedule_unattended=False honestly.
            _register(force_blank_password=True)

        def _cancel(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            # No change — the task is untouched; record honestly that the schedule still runs
            # with the previous settings (the folders/SFTP Save itself already persisted).
            result_slot.controls = [
                components.HealthVerdictBanner(
                    Verdict.WARNING,
                    headline=interrupt.cancelled_headline,
                    detail=interrupt.cancelled_detail,
                )
            ]
            page.update()

        actions: list[ft.Control] = [components.text_button(interrupt.cancel_label, _cancel)]
        if interrupt.offers_signed_in_only:
            # 0046 B: omitted on the service-account variant — the Register gate refuses a
            # principal change on a live task, so this button would be a dead control.
            actions.append(components.secondary_button(interrupt.signed_in_only_label, _signed_in_only))
        actions.append(components.secondary_button(interrupt.keep_unattended_label, _keep))
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(interrupt.headline),
                content=ft.Text(interrupt.detail),
                actions=actions,
            )
        )

    def _trigger_register_reconciled() -> ReconcileOutcome:
        """The reconcile's register entry (S3-a): interrupt before any silent logon downgrade.

        The Register BUTTON deliberately does NOT route through here — a blank-password
        Register is a legitimate explicit choice there (the helper text offers it). Only the
        Settings-Save reconcile, which the admin never framed as a logon-type decision, must
        pause for the explicit choice.

        Returns ``DISPATCHED`` when a re-register was actually started, ``INTERRUPTED`` when the
        downgrade-choice dialog was shown instead (nothing registered), and ``BLOCKED`` when
        ``_register`` early-returned without dispatching (its gate refused — e.g. a malformed run
        time, whose inline error it just painted). The seam that lets both Save sites paint an
        honest note (S3 correctness fix): "updating…" is claimed ONLY for a real dispatch.

        The logon-type fact comes from the DURABLE record (W3-C), so an install with no record
        interrupts with its own can't-tell copy rather than trusting the ``False`` default and
        silently replacing a signed-out-capable task with a logged-on-only one.
        """
        # 0046 B — the order here is load-bearing and pinned. The SWITCH refusal and a malformed
        # account are decided BEFORE the interrupt (neither is a logon-type question), while
        # ACCOUNT_NEEDS_PASSWORD is decided AFTER it, because the service-account dialog variant
        # is the richer explanation for that state: it names the account whose password Windows
        # wants, and it is the path plan 0034 built.
        facts = _account_facts()
        unreadable = _delivery_unreadable()
        block = _gate_block(facts, unreadable)
        if block is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE:
            _paint_account_note(facts, unreadable)
            page.update()
            return ReconcileOutcome.BLOCKED_ACCOUNT_SWITCH
        if block is RegisterBlock.ACCOUNT_SHAPE:
            _paint_account_note(facts, unreadable)
            page.update()
            return ReconcileOutcome.BLOCKED_ACCOUNT
        record = _registered_schedule(cfg)
        interrupt = downgrade_interrupt(
            registered_unattended=record.unattended,
            password_supplied=facts.password_supplied,
            registered_foreign_account=(
                "" if principal_key(facts.recorded, facts.current) == "" else (facts.recorded or "")
            ),
            # 0049 S-4: the RECORDED kind, not the section's live tick box — this dialog is
            # about what the LIVE task is, and its whole job is to stop a re-register silently
            # changing that. ``None`` (no usable record) keeps today's copy, which is the
            # can't-tell variant the unknown-record path already owns.
            registered_kind=record.run_as_kind,
        )
        if interrupt is not None:
            _show_downgrade_dialog(interrupt)
            return ReconcileOutcome.INTERRUPTED
        if block not in (RegisterBlock.NONE, RegisterBlock.INCOMPLETE, RegisterBlock.RUN_TIME):
            # A TOTALITY floor, not a live path: today the only member that can reach here is
            # ACCOUNT_NEEDS_PASSWORD, and every live-task shape of it is already absorbed by the
            # service-account interrupt above (a foreign principal on a registered task with no
            # password IS that dialog). The arm exists because the alternative is worse than dead
            # code: falling through to `_register` would early-return and the Save would paint
            # BLOCKED's "fix the run time" over a principal problem — exactly the misdirect
            # carried item 5 is about. A new RegisterBlock member lands here rather than there.
            #
            # 0049 S-2b.1: DELIVERY_SECRET_UNREADABLE is the first member to make this arm a
            # LIVE path rather than a floor. It gets its OWN outcome rather than borrowing
            # BLOCKED_ACCOUNT's: that copy reads "check the Windows account and its password",
            # which points an admin at the SERVICE ACCOUNT's credentials over a fault in the
            # SpacesEDU DELIVERY password. Landing here to dodge BLOCKED's "fix the run time"
            # and then printing the wrong field anyway would just move the misdirect one field
            # over. The field note the repaint above puts on screen names the same remedy.
            _paint_account_note(facts, unreadable)
            page.update()
            if block is RegisterBlock.DELIVERY_SECRET_UNREADABLE:
                return ReconcileOutcome.BLOCKED_DELIVERY_SECRET
            return ReconcileOutcome.BLOCKED_ACCOUNT
        return ReconcileOutcome.DISPATCHED if _register(None) else ReconcileOutcome.BLOCKED

    # 0032 T1 #6 vocabulary: plain "Schedule nightly sync"/"Remove nightly sync" — never the
    # Windows-jargon "Register"/"Unregister" pair on a user-facing control.
    register_btn = components.primary_button(
        "Schedule nightly sync",
        _register,
        # The one gate read that cannot go through `_repaint_register_gate` — the button it
        # assigns to does not exist yet. One delivery-secret read, same `_gate_block` spelling.
        disabled=not can_register_schedule(
            cfg.is_complete(),
            run_time_field.value or "",
            account=_account_facts(),
            delivery_secret_unreadable=_delivery_unreadable(),
        ),
        icon=ft.Icons.SCHEDULE_ROUNDED,
    )
    unregister_btn = components.secondary_button(
        "Remove nightly sync",
        _unregister,
        icon=ft.Icons.EVENT_BUSY_ROUNDED,
    )

    def _refresh_register_gate(_e: ft.ControlEvent | None = None) -> None:
        # The gate's REASON repaints with the gate itself, so a disabled primary always has a
        # visible cause and the delivery note appears/disappears with the account as it is
        # typed. Both come out of ONE `_repaint_register_gate` pass — and therefore one
        # delivery-secret read per keystroke, not one per consumer.
        _repaint_register_gate()
        page.update()

    run_time_field.on_change = _refresh_register_gate
    run_time_field.on_submit = _register
    if account_field is not None:
        account_field.on_change = _refresh_register_gate
        account_field.on_submit = _register
    if password_field is not None:
        password_field.on_change = _refresh_register_gate
        password_field.on_submit = _register

    section_controls.append(ft.Row(spacing=16, controls=[register_btn, unregister_btn]))
    section_controls.append(result_slot)

    # --- Seasonal window (B): opt-in "only sync during the school year" -------------------- #
    # The window is NOT a task arg (``setup_flow.TaskArgs`` omits it) — the ENGINE reads it from
    # config each night, so changing it persists to ``cfg`` and NEVER re-registers the task. Fields
    # pre-fill from the district's academic calendar (COUNTED ``default_window_bounds``); a saved
    # window shows the saved value. Persistence is on-valid-change: an enabled+invalid window shows
    # the inline error, persists nothing, and (in the wizard) closes the Continue gate via
    # ``on_window_valid`` — the same "Enter can't bypass a disabled button" guarantee.
    prefill_start, prefill_end = _district_window_defaults(cfg)
    window_toggle = ft.Switch(
        label="Only sync during the school year",
        value=bool(cfg.sync_window_enabled),
        active_color=tokens.color_status_healthy,
    )
    window_start_field = ft.TextField(
        label="Sync starts (MM-DD)",
        value=cfg.sync_window_start or prefill_start,
        width=190,
        border_color=tokens.color_border,
        disabled=not bool(cfg.sync_window_enabled),
        helper="About two weeks before school starts.",
        helper_max_lines=3,
    )
    window_end_field = ft.TextField(
        label="Sync ends (MM-DD)",
        value=cfg.sync_window_end or prefill_end,
        width=190,
        border_color=tokens.color_border,
        disabled=not bool(cfg.sync_window_enabled),
        helper="Early summer — the sync pauses until next school year.",
        helper_max_lines=3,
    )
    window_error_slot = ft.Column(spacing=0, controls=[])
    # A9 (0046 C): the limitation note, muted tone, rendered only when the window is enabled AND the
    # recorded task principal is foreign. Resolved once at build time from the same single resolver
    # every probe uses; ``_on_window_change`` refreshes it when the toggle moves.
    window_foreign_slot = ft.Column(spacing=0, controls=[])

    def _window_foreign_note() -> str | None:
        # 0049 S-2a.2: ONE entry point picks the sibling. The shared-profile form asserts the pause
        # POSITIVELY, so it also needs the read-back state — hence ``_last_schedule_state``, and
        # hence the repaint from the probe's own ``_apply``.
        return window_scope_note(
            cfg,
            foreign_account=foreign_task_account(cfg),
            shared_records=paths.is_machine_scope(),
            state=_last_schedule_state["state"],
        )

    def _paint_window_foreign_note() -> None:
        note = _window_foreign_note()
        window_foreign_slot.controls = [ft.Text(note, size=13, color=tokens.color_muted)] if note else []

    def _on_window_change(_e: ft.ControlEvent | None = None) -> None:
        enabled = bool(window_toggle.value)
        start = window_start_field.value or ""
        end = window_end_field.value or ""
        valid = window_settings_valid(enabled, start, end)
        window_start_field.disabled = not enabled
        window_end_field.disabled = not enabled
        window_error_slot.controls = (
            [] if valid else [ft.Text(_WINDOW_ERROR, size=13, color=tokens.color_status_failed)]
        )
        if valid:
            # Persist to config ONLY (never a task arg) — the nightly gate reads it at run time.
            cfg.sync_window_enabled = enabled
            if enabled:
                cfg.sync_window_start = validate_month_day(start)
                cfg.sync_window_end = validate_month_day(end)
            cfg.save()
        _paint_window_foreign_note()
        if on_window_valid is not None:
            on_window_valid(valid)  # wizard footer gate — Continue blocks while enabled+invalid
        page.update()

    window_toggle.on_change = _on_window_change
    for _window_field in (window_start_field, window_end_field):
        _window_field.on_change = _on_window_change
        _window_field.on_submit = _on_window_change

    # FIX 3: re-derive the advance gate from the PERSISTED config on every (re)build. ``_on_window_change``
    # only fires on live user input, so a Back->Forward rebuild (which restores the fields from cfg's
    # last VALID bounds with an empty error slot) would otherwise leave a stale ``window_valid=False``
    # stranding the Schedule step's Continue / "Set up later". The wizard passes ``on_window_valid``;
    # Settings mode passes none (the flat-scroll Save has no advance gate), so the guard is required.
    if on_window_valid is not None:
        on_window_valid(
            window_valid_from_config(
                enabled=bool(cfg.sync_window_enabled),
                start_md=cfg.sync_window_start,
                end_md=cfg.sync_window_end,
                prefill_start=prefill_start,
                prefill_end=prefill_end,
            )
        )

    section_controls.append(
        ft.Column(
            spacing=10,
            controls=[
                ft.Divider(height=1, color=tokens.color_border),
                ft.Text("Seasonal pause", size=16, weight=ft.FontWeight.W_800, color=tokens.color_text),
                ft.Text(
                    "Pause the nightly sync over the summer and resume it automatically each school "
                    "year — set once, no yearly changes needed.",
                    size=13,
                    color=tokens.color_muted,
                ),
                window_toggle,
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[window_start_field, window_end_field],
                ),
                window_error_slot,
                window_foreign_slot,
            ],
        )
    )
    _paint_window_foreign_note()

    # 0049 S-4: paint the disclosure at BUILD time, because its opening state is SEEDED from the
    # durable record rather than always-off — a Settings mount over a live gMSA task comes up
    # with the tick box on, so the caption, the checklist and the hidden password field must
    # already match it on the first frame.
    _paint_gmsa_disclosure()
    # 0046 B: paint the gate's reason at BUILD time too — a section that mounts with the button
    # already disabled (a live task on a service account, say) must not show a dead primary.
    _paint_account_note()
    _kick_readout_probe()

    card = components.card(content=ft.Column(spacing=18, controls=section_controls))
    handle = _ScheduleHandle(
        trigger_register=_trigger_register_reconciled,
        run_time_value=lambda: run_time_field.value or "",
        run_as_user_value=lambda: "" if account_field is None else (account_field.value or "").strip(),
        persist_run_time=_persist_run_time_if_edited,
        is_busy=lambda: bool(_flight["busy"]),
        last_schedule_state=lambda: _last_schedule_state["state"],
    )
    return card, handle


def _keyring_owner_account() -> str:
    """The Windows account whose OS keyring holds the SFTP credential (defensive).

    This is the account DistrictSync is RUNNING as. Deliberately **not** the scheduled
    task's principal (plan 0046 A6). Credential Manager has no cross-user scope
    (``CRED_PERSIST_ENTERPRISE`` is per-user), so a task running as another account cannot
    read this credential; naming the task principal here would print a false all-clear on
    the single most likely real failure this feature creates.

    Also the defensive resolver the run-as gate reads on every keystroke (0046 B) — the
    ``try/except`` is load-bearing there too: ``getpass.getuser()`` can raise, and one raise
    must not strand the whole schedule section.
    """
    try:
        return get_scheduler().run_as_user()
    except Exception:
        return "this account"


# The delivery-password line's four forms (plan 0049 S-2a.3). Per-user is form 2 and is BYTE
# IDENTICAL to what shipped — the whole slice's promise on 20 districts rests on this one string.
#
# The account comes from the RECORD (``foreign_task_account``), not the read-back:
# ``ScheduleReadback`` carries no principal until S-3, and the record is what this app itself
# wrote at a confirmed registration. It fails to ``""`` on everything, which lands on the
# account-less variant — the conservative direction, since an empty name must never be rendered.
_DELIVERY_LINE_PER_USER = "Your delivery password is saved and readable by {owner}."
_DELIVERY_LINE_SHARED_NAMED = "Your delivery password is saved on this computer, where {account} can read it."
# D5 allows scheduling as the SIGNED-IN account on a machine-scoped install, and
# ``schedule_run_as_user`` is ``""`` by contract there — so this variant is reachable in normal
# use and must NOT fall back to ``_keyring_owner_account()``, which names a keyring the machine
# store has replaced.
_DELIVERY_LINE_SHARED_UNNAMED = (
    "Your delivery password is saved on this computer, where the account that runs the nightly sync can read it."
)
# MISSING ONLY. An UNKNOWN read-back (a probe timeout, access denied, a task registered elevated
# and unreadable by a filtered token) may never be rendered as an absence — it simply drops this
# sentence and keeps the variant above.
_DELIVERY_LINE_NO_SCHEDULE_TAIL = "No nightly sync is scheduled right now."
_DELIVERY_LINE_UNREADABLE_SHARED = (
    "Couldn't read the delivery password back from this computer's shared settings — the nightly "
    "delivery won't run until it's saved again."
)
_DELIVERY_LINE_UNREADABLE_PER_USER = (
    "Couldn't read the credential back on this account — SFTP uploads may fail. "
    "Try again, or run the app as this account."
)


def delivery_password_line(
    *,
    secret_readable: bool,
    machine_scope: bool,
    principal: str,
    schedule_state: ScheduleState | None,
    keyring_owner: str,
) -> str:
    """The "where is the delivery password, and who can read it?" line (pure, TOTAL).

    Four forms, in precedence order:

    1. **no readable credential** → the warning that delivery will not run (per-user keeps today's
       wording; the shared form drops "run the app as this account", which is wrong advice once
       the secret lives in the machine store);
    2. **per-user** → today's line, byte for byte — the keyring owner, never the task principal
       (0046 A6: Credential Manager has no cross-user scope, so naming the principal there would
       print a false all-clear on the most likely real failure);
    3. **machine scope, a CONFIRMED-MISSING schedule** → the account-less line plus "no nightly
       sync is scheduled right now". MISSING outranks a recorded principal: that record names a
       task Windows says does not exist, so naming it would describe a nightly that is gone;
    4. **machine scope otherwise** → named when the record has a principal, account-less when it
       does not.
    """
    if not secret_readable:
        return _DELIVERY_LINE_UNREADABLE_SHARED if machine_scope else _DELIVERY_LINE_UNREADABLE_PER_USER
    if not machine_scope:
        return _DELIVERY_LINE_PER_USER.format(owner=keyring_owner)
    if schedule_state is ScheduleState.MISSING:
        return f"{_DELIVERY_LINE_SHARED_UNNAMED} {_DELIVERY_LINE_NO_SCHEDULE_TAIL}"
    account = (principal or "").strip()
    return _DELIVERY_LINE_SHARED_NAMED.format(account=account) if account else _DELIVERY_LINE_SHARED_UNNAMED


# --------------------------------------------------------------------------- #
# SFTP section — reused verbatim by the wizard Delivery step AND Settings.      #
# --------------------------------------------------------------------------- #
def _build_sftp_section(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    *,
    on_delivery: Callable[[DeliveryFact, str, str], None] | None = None,
    on_saved: Callable[[], ReconcileOutcome] | None = None,
    schedule_state: Callable[[], ScheduleState | None] | None = None,
) -> ft.Control:
    """The SFTP section — store SpacesEDU credentials in the OS keyring + test (Slice 7, D6).

    ``on_delivery`` (when given) reports the Delivery outcome to the wizard: a successful Test →
    ``TESTED_OK``, a failed Test → ``TESTED_FAILED``, a successful Save → ``STORED_CRED_PRESENT``
    (with the host/user). ``on_saved`` (when given) is the Settings reconcile — after a successful
    Save flips/confirms ``sftp_enabled``, it re-registers a live task so the nightly action gains
    (or keeps) ``--sftp`` (the F1 gap: enabling delivery post-registration must reconcile). The
    side-effect-free Test + Save-only keyring writes are UNCHANGED.

    ``schedule_state`` (when given — Settings passes the schedule section's last read-back, see
    ``_mount_settings``) lets the saved-password line say "no nightly sync is scheduled right now"
    on a machine-scoped install. It is a CALLABLE because the read-back lands asynchronously, and
    it is optional because the wizard's Delivery step has no schedule section to read: absent, the
    state is ``None`` and the line asserts nothing about a schedule. It NEVER triggers a probe of
    its own — a subprocess on the Save click is exactly the trade this surface has always refused.
    """
    host_dropdown = ft.Dropdown(
        label="SFTP host (SpacesEDU)",
        value=cfg.sftp_host or None,
        options=[ft.dropdown.Option(key=h, text=h) for h in sorted(ALLOWED_SFTP_HOSTS)],
        border_color=tokens.color_border,
    )
    username_field = ft.TextField(
        label="Username", value=cfg.sftp_username or "", width=340, border_color=tokens.color_border
    )
    remote_field = ft.TextField(
        label="Remote path", value=cfg.sftp_remote_path or "/files", width=340, border_color=tokens.color_border
    )
    port_field = ft.TextField(label="Port", value=str(cfg.sftp_port or 22), width=140, border_color=tokens.color_border)
    password_field = ft.TextField(
        label="Password",
        password=True,
        can_reveal_password=True,
        width=340,
        border_color=tokens.color_border,
        helper="Leave blank to keep the existing stored credential.",
    )

    result_slot = ft.Column(spacing=0, controls=[])
    test_spinner = ft.ProgressRing(width=18, height=18, visible=False)

    def _current_fields() -> tuple[str, str, str, str]:
        return (
            (host_dropdown.value or "").strip(),
            (username_field.value or "").strip(),
            (remote_field.value or "").strip(),
            (port_field.value or "").strip(),
        )

    def _save(_e: ft.ControlEvent | None = None) -> None:
        # I4 (see module docstring — password contract): the SFTP credential is a handler-LOCAL var
        # whose ONLY sink on Save is store_password (OS keyring); never cfg, never a log/message.
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        if not can_save_sftp(
            host=host,
            username=username,
            remote_path=remote_path,
            password=password,
            already_configured=cfg.sftp_is_configured(),
        ):
            return

        # Parse the port FIRST (pure seam) — a port typo must get the port error, not fall into
        # the SFTPUploader ValueError below and misreport as a host-allowlist failure.
        port_num = parse_port(port)
        if port_num is None:
            result_slot.controls = [components.ErrorCard(PORT_ERROR_HEADLINE, PORT_ERROR_DETAIL)]
            page.update()
            return

        try:
            uploader = SFTPUploader(host, port_num, username, remote_path)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return

        if password:
            try:
                uploader.store_password(password)
            except Exception:  # noqa: BLE001 - surface any keyring failure calmly
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't save the SFTP credential",
                        "Couldn't save the SFTP credential on this account. Try again, or run "
                        "DistrictSync as the account the nightly task uses.",
                    )
                ]
                page.update()
                return

        read_back = uploader.get_stored_password()

        def _password_line(*, readable: bool) -> str:
            """The four-form line (0049 S-2a.3) — the ONE place this section states where the
            password lives. Both outcomes route through it, so the failure arm cannot keep telling
            a machine-scoped admin to "run the app as this account"."""
            return delivery_password_line(
                secret_readable=readable,
                machine_scope=paths.is_machine_scope(),
                principal=foreign_task_account(cfg),
                schedule_state=schedule_state() if schedule_state is not None else None,
                keyring_owner=_keyring_owner_account(),
            )

        if not read_back:
            result_slot.controls = [
                components.HealthVerdictBanner(
                    Verdict.FAILED,
                    headline="Couldn't read the SFTP credential back",
                    detail=_password_line(readable=False),
                )
            ]
            page.update()
            return

        cfg.sftp_enabled = True
        cfg.sftp_host = host
        cfg.sftp_port = port_num
        cfg.sftp_username = username
        cfg.sftp_remote_path = remote_path
        cfg.save()

        # Vocabulary (W4a sweep): delivery-flavored plain language — "SFTP" stays only on the
        # sanctioned technical host FIELD label. Truthful for a blank-password Save too: the
        # read-back above just verified a credential IS in the store and readable.
        detail = _password_line(readable=True)
        # F1 reconcile (Settings only): enabling/confirming delivery must add --sftp to an
        # already-registered nightly task, or tonight builds but never delivers. Routed through the
        # SAME task-args reconcile the folders Save uses; a blank-password re-register keeps the
        # existing visible-WARNING (logged-on-only) behaviour. The appended clause is honest to the
        # reconcile outcome (S3 fix): "updating…to deliver too" ONLY when a register was dispatched;
        # an interrupt that opened the downgrade dialog prompts confirmation instead (empty for NONE).
        if on_saved is not None:
            detail += sftp_reconcile_suffix(on_saved())
        result_slot.controls = [
            components.HealthVerdictBanner(Verdict.HEALTHY, headline="Delivery settings saved", detail=detail)
        ]
        if on_delivery is not None:
            on_delivery(DeliveryFact.STORED_CRED_PRESENT, host, username)
        page.update()

    save_btn = components.primary_button(
        "Save delivery settings",
        _save,
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
    )

    def _refresh_save_gate(_e: ft.ControlEvent | None = None) -> None:
        host, username, remote_path, _port = _current_fields()
        save_btn.disabled = not can_save_sftp(
            host=host,
            username=username,
            remote_path=remote_path,
            password=(password_field.value or ""),
            already_configured=cfg.sftp_is_configured(),
        )
        page.update()

    host_dropdown.on_select = _refresh_save_gate
    username_field.on_change = _refresh_save_gate
    remote_field.on_change = _refresh_save_gate
    port_field.on_change = _refresh_save_gate
    password_field.on_change = _refresh_save_gate
    username_field.on_submit = _save
    remote_field.on_submit = _save
    port_field.on_submit = _save
    password_field.on_submit = _save

    def _test(_e: ft.ControlEvent) -> None:
        # I4/D6 (see module docstring — password contract): the typed password rides ONLY the
        # transient test_connection(password_override=...) → client.connect(); never the keyring
        # (that is _save's job alone), never a log, never the returned message. A failed Test can
        # therefore never clobber a working stored credential.
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        # Same pre-parse as _save: a port typo gets the port error, never the host one.
        port_num = parse_port(port)
        if port_num is None:
            result_slot.controls = [components.ErrorCard(PORT_ERROR_HEADLINE, PORT_ERROR_DETAIL)]
            page.update()
            return

        try:
            uploader = SFTPUploader(host, port_num, username, remote_path)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return

        provenance = "typed" if password else "stored"
        unsaved_edits = sftp_form_differs_from_saved(
            cfg, host=host, username=username, remote_path=remote_path, port=port
        )

        test_btn.disabled = True
        test_spinner.visible = True
        result_slot.controls = []
        page.update()

        async def _show_result(ok: bool, msg: str) -> None:
            test_btn.disabled = False
            test_spinner.visible = False
            verdict = Verdict.HEALTHY if ok else Verdict.FAILED
            # Listing-denied is a SUCCESS-with-note (auth worked; the account just can't list
            # the remote folder — normal for upload-only delivery accounts). Detected by
            # EQUALITY against the uploader's canonical fixed note.
            listing_denied = ok and msg == LISTING_DENIED_NOTE
            if ok:
                headline, detail = sftp_test_copy(
                    provenance=provenance,
                    unsaved_edits=unsaved_edits,
                    host=host,
                    username=username,
                    listing_denied=listing_denied,
                )
            else:
                # Honest pair for the landed success headline ("Connected to SpacesEDU").
                headline, detail = "Couldn't connect to SpacesEDU", friendly_sftp_reason(msg)
            result_slot.controls = [components.HealthVerdictBanner(verdict, headline=headline, detail=detail)]
            if on_delivery is not None:
                on_delivery(DeliveryFact.TESTED_OK if ok else DeliveryFact.TESTED_FAILED, host, username)
            page.update()

        def _work() -> None:  # runs OFF the UI thread
            try:
                ok, msg = uploader.test_connection(password_override=password)
            except Exception as exc:  # noqa: BLE001 - surface any failure via the banner
                ok, msg = False, str(exc)
            page.run_task(_show_result, ok, msg)

        page.run_thread(_work)

    test_btn = components.secondary_button("Test connection", _test, icon=ft.Icons.WIFI_TETHERING_ROUNDED)

    _refresh_save_gate()

    section_controls: list[ft.Control] = [
        ft.Text("Delivery to SpacesEDU", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
        ft.Text(
            "Store your SpacesEDU delivery credentials so the nightly sync can deliver the roster. "
            "The password is saved in this computer's credential manager — never in plain files.",
            size=14,
            color=tokens.color_muted,
        ),
        host_dropdown,
        ft.Row(spacing=16, controls=[username_field, port_field]),
        remote_field,
        password_field,
        ft.Row(
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[save_btn, test_btn, test_spinner]
        ),
        result_slot,
    ]

    return components.card(content=ft.Column(spacing=18, controls=section_controls))
