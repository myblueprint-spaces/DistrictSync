"""Pure submit-gate predicates for the Setup screen (COUNTED, no flet import).

VIEW glue lives in ``screens/setup.py`` (coverage-omitted). The trust-critical
*decision* — "may this action fire?" — is extracted here so it is unit-tested and
single-sourced: the button's ``disabled`` state AND the Enter-to-submit
(``on_submit``) handler read the SAME predicate, so pressing Enter can never bypass
a gate that a disabled button structurally enforces.

Mirrors the folders save-gate that already lives purely in
``filepicker.setup_state`` — these cover the *schedule* and *SFTP* sections.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.scheduler.task_com import PrincipalKind, validate_principal_account
from src.utils.validators import validate_month_day


def window_settings_valid(enabled: bool, start_md: str, end_md: str) -> bool:
    """The seasonal-window save/advance gate — the "Enter can't bypass an invalid window" guarantee.

    Single-sources the gate the wizard's Continue button (via ``setup_flow.FlowInputs.window_valid``
    → ``can_advance``) AND the section's on-change persistence both read, so an invalid enabled
    window can neither be advanced past nor saved. Reuses the ENGINE validator ``validate_month_day``
    (one definition of "is this a real MM-DD boundary?") rather than re-parsing here.

    * **Disabled** → always valid: the window is off (year-round), the fields are ignored.
    * **Enabled** → both ``start_md`` and ``end_md`` must be real ``"MM-DD"`` calendar days. A blank
      / malformed either bound closes the gate (``None`` is tolerated as blank — never a raise).
    """
    if not enabled:
        return True
    try:
        validate_month_day(start_md or "")
        validate_month_day(end_md or "")
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def window_valid_from_config(
    *,
    enabled: bool,
    start_md: str | None,
    end_md: str | None,
    prefill_start: str,
    prefill_end: str,
) -> bool:
    """Re-derive the seasonal-window advance gate from PERSISTED config + the district pre-fill (FIX 3).

    The Schedule section rebuilds (Back->Forward) from ``cfg`` — the last VALID bounds, since an
    enabled+invalid edit persists nothing — with an empty error slot, yet the live on-change handler
    that sets the wizard's ``window_valid`` flag never re-fires on a rebuild. Without a re-derive the
    flag stays stale-``False`` and strands the Schedule step's Continue AND "Set up later" (both gate
    on it) with no on-screen cause. Calling this on every (re)build re-syncs the gate to the
    freshly-rebuilt valid UI: the saved bounds (or the district pre-fill when a bound is unset) run
    back through ``window_settings_valid``. Single-sources the "or pre-fill" fallback so the view
    holds no gate logic of its own.
    """
    return window_settings_valid(enabled, start_md or prefill_start, end_md or prefill_end)


def principal_key(account: str | None, current: str) -> str:
    """The comparable identity of a scheduled-task principal (pure, TOTAL).

    ``""`` means "the signed-in account"; anything else is the case-folded foreign account
    name. The ONE reduction every principal comparison in the app goes through — the gate,
    the reconcile, the record and the delivery note — restating the exact equivalence
    :func:`src.scheduler.windows.register_task` applies (blank ≡ current;
    ``requested.casefold() != current.casefold()`` decides "a different account"), so the
    view and the engine can never disagree about what a principal change IS.

    It is used ONLY to decide gates and notes. It is NEVER used to decide what to SEND: the
    typed value goes to ``register_task`` verbatim (stripped only), so if this reduction ever
    drifts from the engine's, the engine's refusal still catches it.
    """
    value = (account or "").strip()
    if not value or value.casefold() == (current or "").strip().casefold():
        return ""
    return value.casefold()


@dataclass(frozen=True)
class ScheduleAccountFacts:
    """The principal half of the Register gate (plan 0046 B).

    Attributes:
        typed: EXACTLY what the admin typed, ``.strip()``ed and nothing else — never
            case-folded, never re-cased, never domain-qualified. Sanitising here would bypass
            the case-insensitive comparison that makes the PREFILLED field safe.
        current: the signed-in account the field is prefilled with
            (``scheduler.run_as_user()``, resolved defensively by the view).
        password_supplied: whether the Windows-password field currently holds anything.
        recorded: ``RegisteredSchedule.run_as_user`` — ``None`` = no usable record, ``""`` =
            recorded as the signed-in account.
        schedule_registered: whether a nightly task is believed live.
        kind: which of :class:`~src.scheduler.task_com.PrincipalKind`'s shapes THIS press is
            asking for (plan 0049 S-4) — the view's own declaration, taken from the Settings
            gMSA disclosure, not read back off the name. It is REQUIRED and undefaulted like
            every other field here: it decides which validator ``typed`` goes through, and the
            two charsets are disjoint on exactly one character, so a defaulted kind would refuse
            a legitimate managed service account for the wrong reason.
    """

    typed: str
    current: str
    password_supplied: bool
    recorded: str | None
    schedule_registered: bool
    kind: PrincipalKind


class RegisterBlock(Enum):
    """Why the Register gate is closed — the SINGLE source the disabled button, the inline
    field note, the ``on_submit`` floor and the Settings reconcile all read.

    **DECLARATION order is not EVALUATION order.** The checks run
    ``INCOMPLETE → RUN_TIME → ACCOUNT_SHAPE → ACCOUNT_SWITCH_NEEDS_REMOVE →
    DELIVERY_SECRET_UNREADABLE → ACCOUNT_NEEDS_PASSWORD`` (see :func:`register_block`,
    where the order is argued and asserted); the members below are in the order they were
    ADDED. Do not "fix" one to match the other — reordering the members changes nothing,
    and reordering the checks changes which cause an admin is told about first.
    """

    NONE = "none"
    INCOMPLETE = "incomplete"
    RUN_TIME = "run_time"
    ACCOUNT_SHAPE = "account_shape"
    # nosec B105 — an enum member NAMED "..._PASSWORD"; the value is a gate reason, not a secret.
    ACCOUNT_NEEDS_PASSWORD = "account_needs_password"  # nosec B105
    ACCOUNT_SWITCH_NEEDS_REMOVE = "account_switch_needs_remove"
    #: Plan 0049 S-2b.1. Scheduling as a SERVICE ACCOUNT provisions this computer for a
    #: shared profile, and step 5 of that provision seals the delivery password into it.
    #: If we cannot read the password now, there is nothing to seed and the nightly would
    #: come up delivering nothing — silently, because on a machine-scoped install
    #: ``sftp_is_configured()`` then answers False and no run is attempted.
    #:
    #: **The view owns this member's NOTE**, exactly as it owns the other four (see
    #: ``screens/setup.py``'s ``_ACCOUNT_*_NOTE`` constants and ``_account_block_note``).
    #: Two things that note MUST say, because the admin may not be able to do the first:
    #: re-save the delivery password in Delivery, AND — plainly — that turning delivery
    #: off is the other way through, and that the nightly then writes the CSVs without
    #: sending them. The password may have been saved by a DIFFERENT Windows account,
    #: whose Credential Manager this one can never reach, so an escape that is only
    #: discoverable by guessing is not an escape.
    # nosec B105 — a gate reason naming a secret's READABILITY; the value is not a secret.
    DELIVERY_SECRET_UNREADABLE = "delivery_secret_unreadable"  # nosec B105


def register_block(
    config_complete: bool,
    run_time: str,
    *,
    account: ScheduleAccountFacts,
    delivery_secret_unreadable: bool,
) -> RegisterBlock:
    """The Register-schedule gate with its REASON (pure, TOTAL).

    Order is load-bearing and asserted: config completeness, then run time (today's two
    conditions, byte-identical), then the account's SHAPE, then the switch refusal, then the
    delivery secret, then the password rung — the admin is told the FIRST thing that is
    wrong, not the last.

    **Shape is checked ONLY for a FOREIGN principal, and against its DECLARED KIND.**
    ``current_run_as_user()`` legitimately returns a name containing a space
    (``PC\\John Smith``), which ``_RUN_AS_USER_RE`` rejects; the field is PREFILLED with that
    value, so validating it unconditionally would close the gate on mount for those districts,
    which register logged-on-only fine today (G5). This mirrors the engine, which deliberately
    never validates the machine-derived fallback. Which validator runs comes from
    ``account.kind`` through ``task_com.validate_principal_account`` (plan 0049 S-4) — both
    rungs that can refuse an account dispatch on the kind, because this rung runs FIRST and
    unconditionally, and a gMSA refused here never reaches the password rung at all.

    ``ACCOUNT_SWITCH_NEEDS_REMOVE`` fires when a task is registered and the requested principal
    is not PROVABLY the recorded one. Both directions, and the unknown record:

      * ``typed_key == "" and recorded_key in ("", None)`` → not a switch (today's world);
      * ``typed_key != "" and recorded_key == typed_key``  → not a switch (re-registering the
        same service account, e.g. a run-time change);
      * anything else, with ``schedule_registered`` → SWITCH.

    The ``recorded is None`` arm is deliberate: a pre-v3.7.0 or hand-edited install has no
    record, so we cannot prove the live task is already on that account — and re-pointing a live
    task in place is the T1053.005 shape the owner's delete-then-create decision exists to avoid
    (owner decision, 2026-09-16). The remedy is the same instruction either way, so refusing
    costs nothing and never asserts an unchecked state.

    ``ACCOUNT_NEEDS_PASSWORD`` mirrors ``windows._MSG_ACCOUNT_NEEDS_PASSWORD``. It does not
    replace the engine refusal (which closes three blank-password paths structurally); it makes
    the common one legible BEFORE a UAC prompt is raised. It is skipped for
    ``MANAGED_SERVICE_ACCOUNT``, which has no password to supply.

    ``DELIVERY_SECRET_UNREADABLE`` (plan 0049 S-2b.1) sits AFTER the switch refusal and
    BEFORE the password rung, and both halves of that placement are deliberate: a valid,
    non-switching principal has to be established before a delivery fact is worth raising,
    and the cheapest rung (a field that is simply empty) stays last.

    **It fires only when the register would PROVISION**, and the conjunction is computed
    HERE rather than by the caller so no call site can get it wrong: the requested
    principal must be FOREIGN — through the same :func:`principal_key` reduction every
    other principal comparison uses, never a second derivation of "is this a different
    account?". On a per-user install scheduling as the signed-in account, an unreadable
    delivery secret is a real problem but it is the Delivery section's, not this gate's,
    and today's behaviour there stays byte-identical.

    Args:
        delivery_secret_unreadable: delivery is configured but its password cannot be
            produced, so there would be nothing to seal into the shared profile. Required,
            keyword-only and UNDEFAULTED for the same reason ``account`` is: this plan
            exists because one defaulted parameter substituted a security principal.
            Compute it with :func:`src.scheduler.provision_session.delivery_secret_unreadable`,
            which reads through ``secret_store.select_store()`` — never "the keyring" by
            name, because on a SECOND provisioning of an already machine-scoped install the
            secret lives in the machine store and a literal keyring read would block a
            perfectly healthy register.
    """
    if not bool(config_complete):
        return RegisterBlock.INCOMPLETE
    if not (run_time or "").strip():
        return RegisterBlock.RUN_TIME

    typed_key = principal_key(account.typed, account.current)
    if typed_key:
        try:
            # 0049 S-4: dispatched on the DECLARED kind, through the ONE kind→validator
            # dispatcher the engine and both halves of the elevation handshake already use.
            # ``validate_run_as_user`` unconditionally would refuse every managed service
            # account HERE, on the first rung, for the wrong reason — a trailing ``$`` is
            # exactly what makes one — and the admin would never reach a sentence about
            # credentials at all. Never a second spelling of the rule: if this file decided
            # for itself what a ``$`` meant, the gate and the engine could disagree about
            # which names are registrable.
            validate_principal_account(account.kind, account.typed)
        except (ValueError, TypeError, AttributeError):
            return RegisterBlock.ACCOUNT_SHAPE

    if account.schedule_registered:
        recorded = account.recorded
        recorded_key = None if recorded is None else principal_key(recorded, account.current)
        provably_same = recorded_key == typed_key if recorded_key is not None else typed_key == ""
        if not provably_same:
            return RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE

    if typed_key and bool(delivery_secret_unreadable):
        return RegisterBlock.DELIVERY_SECRET_UNREADABLE

    if (
        typed_key
        # 0049 S-4: a managed service account HAS no password — the directory holds its
        # credential — so this rung would close the gate on a request that is complete, and
        # its note would send the admin to find something that does not exist. The engine
        # makes the same distinction one layer down: ``windows.register_task``'s MSA branch
        # deliberately has no ``_MSG_ACCOUNT_NEEDS_PASSWORD`` refusal.
        and account.kind is not PrincipalKind.MANAGED_SERVICE_ACCOUNT
        and not account.password_supplied
    ):
        return RegisterBlock.ACCOUNT_NEEDS_PASSWORD
    return RegisterBlock.NONE


def can_register_schedule(
    config_complete: bool,
    run_time: str,
    *,
    account: ScheduleAccountFacts,
    delivery_secret_unreadable: bool,
) -> bool:
    """The Register-schedule gate (bool form) — ``register_block(...) is RegisterBlock.NONE``.

    The folders/district config must be complete, a non-blank run time entered, AND the
    requested principal must be registrable (shape, password, not an in-place switch, and —
    when the register would provision — a delivery secret we can actually seed). Single-sources
    the gate the Register button encodes so the button's ``disabled`` state and the run-time /
    account / Windows-password ``on_submit`` handlers agree.

    ``account`` and ``delivery_secret_unreadable`` are required keyword-only and deliberately
    UNDEFAULTED: a defaulted ``ScheduleAccountFacts`` would let a forgotten call site skip the
    principal gate silently, and a defaulted ``False`` would let one provision a computer with
    no delivery credential to seed — both the exact shape CLAUDE.md bans on a safety-relevant
    parameter.
    """
    return (
        register_block(
            config_complete,
            run_time,
            account=account,
            delivery_secret_unreadable=delivery_secret_unreadable,
        )
        is RegisterBlock.NONE
    )


def can_save_sftp(
    *,
    host: str,
    username: str,
    remote_path: str,
    password: str,
    already_configured: bool,
) -> bool:
    """The Save-SFTP-credentials gate.

    Host, username and remote path are always required. A password is required the
    FIRST time (no stored credential yet); on a re-save an existing stored credential
    may be kept by leaving the password blank. Single-sources the gate the Save button
    encodes so the four SFTP ``on_submit`` handlers can't bypass it.
    """
    has_required = bool((host or "").strip() and (username or "").strip() and (remote_path or "").strip())
    return has_required and (bool(password) or already_configured)
