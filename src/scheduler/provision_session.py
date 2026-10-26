"""The PARENT half of machine-scope provisioning (plan 0049 S-1b-ii.1 / ii.2).

Everything here runs UNELEVATED, in the session that asked for the change. Its opposite
number is :mod:`src.scheduler.provisioning`, which runs behind the UAC boundary and may
not log at all; this module is the side that has a log sink, a window and an ``AppConfig``.

Four jobs:

* :func:`request_provision` — the ``provision`` round trip behind one UAC prompt (S-2b.3):
  build the payload, seal it, launch the elevated child, reduce whatever comes back to a
  bounded :class:`ProvisionAttempt`. The caller then calls :func:`complete_handover` on
  **every** outcome, including a timeout.
* :func:`complete_handover` — the post-provision session steps. Gated on **the parent's
  OWN re-read of the HKLM switch** (:func:`src.utils.paths.machine_switch_on`), never on
  the child's claim: ``run_elevated`` kills the child on its bounded wait, so the result
  file is absent on exactly the failures where it may nonetheless have committed. Trusting
  that absence would keep the session writing through a stale per-user pin, and every edit
  made after the commit would vanish at the next launch.
* :func:`delivery_secret_unreadable` — the pre-UAC gate input (S-2b.1). Provisioning seals
  the delivery password into the shared profile; if there is nothing readable to seal, the
  nightly would come up delivering nothing, silently.
* :func:`request_access` — the ``grant_current_user`` round trip behind one UAC prompt, for
  a SECOND administrator on an already-provisioned computer. It deliberately depends on
  nothing the pin provides: the handshake lives under
  :func:`~src.utils.paths.handshake_dir`, which is per-user in every scope and still
  resolves when :func:`~src.utils.paths.user_data_dir` REFUSES — which is the only state
  this function is ever called in.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from src.config.app_config import CONFIG_FILENAME
from src.scheduler import elevation, provisioning, schedule_check, task_com, windows
from src.scheduler.elevated_apply import DIFFERENT_ACCOUNT_SENTINEL
from src.scheduler.elevation import ElevationResult
from src.scheduler.provisioning import ProvisionRefused, ProvisionStep, build_provision_payload
from src.scheduler.schedule_check import ScheduleCheck
from src.scheduler.task_com import Principal, PrincipalKind, validate_principal_account
from src.utils import paths
from src.utils.logger import get_logger
from src.utils.validators import validate_run_time, validate_sis_type, validate_task_name

logger = logging.getLogger(__name__)

# The per-user artefacts a completed handover sets aside, in the order they are reported.
# ``config.json`` and the run store were COPIED into the shared profile by the elevated
# child; what is left here is a predecessor, and a predecessor that still answers to
# ``AppConfig.load()`` is a second profile waiting to happen. Spelled from their owners
# (``app_config`` and ``paths``) rather than re-typed — the run store's sidecars are
# derived from its name for the same reason.
_SUPERSEDED_ARTEFACTS: tuple[str, ...] = (
    CONFIG_FILENAME,
    paths.RUN_STORE_NAME,
    f"{paths.RUN_STORE_NAME}-wal",
    f"{paths.RUN_STORE_NAME}-shm",
)

# The set-aside suffix. Deliberately APPENDED (``config.json.pre-machine-<ts>``) rather
# than infixed: nothing globbing ``config*.json`` or ``history*.db`` can then mistake a
# predecessor for a live file — including ``AppConfig``'s own ``config.corrupt-*.json``
# sweep and the run store's quarantine scan.
_SET_ASIDE_SUFFIX = "pre-machine"


def _stamp() -> str:
    """The set-aside timestamp — one call per handover, so every artefact shares it."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class HandoverOutcome:
    """What the post-provision session steps actually managed to do.

    ``handed_over`` is the only load-bearing field: it says this session now reads and
    writes the SHARED profile. The rest exists so a caller's copy cannot claim a
    housekeeping step that did not happen — the same discipline as
    :class:`~src.config.app_config.ClearOutcome`.
    """

    handed_over: bool
    renamed: tuple[str, ...] = ()
    unrenamed: tuple[str, ...] = ()
    breadcrumb: bool = False
    # Set only when the switch reads ON but the shared profile is not usable: the child
    # committed against a directory the app's own trust predicate rejects. Nothing was
    # renamed and nothing was persisted, because there is nowhere to persist TO.
    refused: paths.MachineScopeRefusedReason | None = None


def complete_handover(
    *,
    persist: Callable[[], None],
    reenter: Callable[[], None],
) -> HandoverOutcome:
    """Finish an elevated ``provision`` on the parent side. Call this on EVERY outcome.

    There is deliberately no parameter for the child's result: see the module docstring.
    The switch is read here, by us, and that read is the whole gate.

    Order, and every step of it is load-bearing:

    1. read the switch; OFF (or unreadable — we cannot PROVE a commit) → ``persist`` and
       return, having touched nothing;
    2. :func:`~src.utils.paths.reset_data_dir_pin` → :func:`~src.utils.paths.pin_data_dir`;
    3. **assert :func:`~src.utils.paths.is_machine_scope` before anything destructive** —
       a set switch does not by itself mean this process resolved the shared profile
       (``DISTRICTSYNC_DATA_DIR`` wins outright and is never machine scope);
    4. re-point the log sink, so the rest of this session's lines land in the shared
       ``runs/`` log rather than the profile we are about to supersede;
    5. set the per-user ``config.json`` / ``history.db`` (+ ``-wal`` / ``-shm``) aside and
       drop the ``MOVED.txt`` breadcrumb that FENCES them
       (:func:`~src.utils.paths.profile_superseded`);
    6. ``persist`` — the three-facet schedule save, strictly AFTER the re-pin. Before it,
       the facets would land in the ``config.json`` renamed seconds earlier, and a
       machine-scoped install would report "no nightly scheduled" against a live task;
    7. ``reenter`` — rebuild the app body. Both callbacks are REQUIRED and undefaulted: a
       defaulted ``None`` silently skips a step whose absence is invisible until the next
       launch, which is the class of mistake this plan exists to remove.

    ``persist`` runs BEFORE ``reenter`` because re-entry rebuilds every screen from a fresh
    ``AppConfig.load()`` — a re-entry that ran first would paint state the disk lacks. A
    raise out of ``persist`` therefore skips ``reenter`` and propagates: the handover has
    already happened, and a facet save that did not happen must not look like one that did.
    """
    if not _switch_reads_on():
        persist()
        return HandoverOutcome(handed_over=False)

    paths.reset_data_dir_pin()
    try:
        paths.pin_data_dir()
    except paths.MachineScopeRefused as exc:
        # The child committed HKLM against a directory this app will not use. Nothing is
        # renamed and nothing is persisted — ``user_data_dir()`` refuses for every caller
        # now, so there is no profile to write to. The next launch reports the same
        # refusal through the launcher's dialog, which is where an admin can act on it.
        logger.error(
            "The shared settings folder was committed but cannot be used (%s). Nothing on this "
            "computer was changed; an administrator needs to repair it.",
            exc.reason.value,
        )
        return HandoverOutcome(handed_over=False, refused=exc.reason)

    if not paths.is_machine_scope():
        # Reachable under DISTRICTSYNC_DATA_DIR (the override wins outright and is never
        # machine scope). Provisioning refuses under it in both halves, so this is a belt
        # — but it is the belt that stands between a stale answer and an irreversible
        # rename, so it is checked rather than assumed.
        logger.error(
            "The shared-settings switch is on, but this session did not resolve the shared folder. "
            "Nothing was moved or renamed."
        )
        persist()
        return HandoverOutcome(handed_over=False)

    _repoint_log_sink()

    live = paths.user_data_dir()
    superseded = paths.per_user_data_dir()
    renamed: tuple[str, ...] = ()
    unrenamed: tuple[str, ...] = ()
    breadcrumb = False
    if superseded == live:
        # Unreachable by construction (the machine root is never the per-user profile), and
        # checked anyway: this is the one comparison standing between the handover and
        # fencing off the profile it just moved INTO. Only the set-aside is skipped — the
        # scope DID change, so the facets and the re-entry below still have to happen.
        logger.error("Refusing to supersede %s: it is the profile this session just moved to.", live)
    else:
        renamed, unrenamed = _set_aside(superseded)
        breadcrumb = _breadcrumb(superseded, live)

    persist()
    reenter()
    return HandoverOutcome(
        handed_over=True,
        renamed=renamed,
        unrenamed=unrenamed,
        breadcrumb=breadcrumb,
    )


def _switch_reads_on() -> bool:
    """The parent's own read of the HKLM switch. Unreadable counts as OFF, loudly.

    "Cannot prove it is on" must not rename a live profile: if the switch really was
    committed, the next launch re-pins to the shared profile and finds the per-user one
    intact; if it was not, a rename here would have set aside the only settings there are.
    """
    try:
        return paths.machine_switch_on()
    except paths.MachineScopeRefused as exc:
        logger.warning(
            "Could not read this computer's shared-settings switch (%s) — leaving this session's "
            "profile exactly as it is.",
            exc.reason.value,
        )
        return False


def _repoint_log_sink() -> None:
    """Re-open the file sink at the RE-PINNED location. Never fatal.

    ``user_log_file()`` answers ``runs/etl_tool-<account>.log`` once the pin is machine
    scope, so this call is what stops the rest of the session writing into the profile it
    just superseded. A failure here (the shared ``runs/`` unwritable for this admin) is
    logged and survived: the handover is already committed, and losing the sink is not a
    reason to abandon it.
    """
    try:
        get_logger(__name__)
    except OSError as exc:
        logger.warning("Could not re-open the log file in the shared folder (%s); this session keeps its own.", exc)


def _set_aside(superseded: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Rename the per-user artefacts to ``<name>.pre-machine-<ts>``; report both halves.

    Never overwrites an existing target and never deletes anything: a previous handover's
    predecessor is evidence, and this is the last copy of settings an admin may still want.
    A failure is a WARNING, not a raise — the breadcrumb is what actually fences the
    directory, and it is written whether or not these renames land.
    """
    stamp = _stamp()
    renamed: list[str] = []
    unrenamed: list[str] = []
    for name in _SUPERSEDED_ARTEFACTS:
        source = superseded / name
        if not source.exists():
            continue
        target = superseded / f"{name}.{_SET_ASIDE_SUFFIX}-{stamp}"
        if target.exists():
            logger.warning("Left %s in place: %s is already there.", name, target.name)
            unrenamed.append(name)
            continue
        try:
            os.rename(source, target)
        except OSError as exc:
            logger.warning("Could not set %s aside (%s); it is fenced by MOVED.txt instead.", name, exc)
            unrenamed.append(name)
        else:
            renamed.append(name)
    if renamed:
        logger.info("Set aside %d file(s) from the previous per-user profile.", len(renamed))
    return tuple(renamed), tuple(unrenamed)


def _breadcrumb(superseded: Path, live: Path) -> bool:
    """Drop ``MOVED.txt`` — the fence itself, not a courtesy.

    :func:`~src.utils.paths.profile_superseded` reads exactly this file, and both writers
    (``AppConfig.save`` and ``write_run_record``) refuse while it is there. Without it the
    renames fence nothing: ``AppConfig.load()`` maps ``FileNotFoundError`` to defaults with
    no log, and ``save()`` / ``store._open`` both recreate silently.
    """
    paths.write_moved_breadcrumb(superseded, live)
    return paths.profile_superseded(superseded)


# --------------------------------------------------------------------------- #
# The delivery secret — the ONE pre-UAC gate (S-2b.1)                          #
# --------------------------------------------------------------------------- #


def _delivery_configured(enabled: bool, host: str, username: str) -> bool:
    """Is delivery set up enough for there to BE a secret? (pure)

    Deliberately NOT ``AppConfig.sftp_is_configured()``: that method adds a
    ``has_secret`` conjunct on a machine-scoped install, so asking it "is delivery
    configured?" in order to decide "can we read its secret?" would answer the question
    with itself — and on the SECOND provisioning of an already machine-scoped computer it
    would report "not configured" for exactly the install whose secret we can read.
    """
    return bool(enabled and (host or "").strip() and (username or "").strip())


def delivery_secret_unreadable(*, enabled: bool, host: str, username: str) -> bool:
    """Is delivery configured with a secret we CANNOT read? (TOTAL — never raises)

    The input to :data:`~src.ui_flet.setup_gates.RegisterBlock.DELIVERY_SECRET_UNREADABLE`.
    Scheduling as a service account provisions this computer, and step 5 of that provision
    seals the delivery password into the shared profile. With nothing to seal, the nightly
    comes up delivering nothing and says nothing: on a machine-scoped install
    ``sftp_is_configured()`` answers False without a secret, so no upload is even attempted.

    **Read through** :func:`~src.sftp.secret_store.select_store`, never "the keyring" by
    name. On a second provisioning the secret already lives in the machine store, and a
    literal keyring read would block a perfectly healthy register.

    Answered with ``has_secret``, which is total by its own contract and — unlike
    ``get_password`` — never materialises the password to answer a boolean.

    **Fails CLOSED.** "We could not find out" is reported as unreadable, because the cost
    of the two mistakes is not symmetric: a wrong ``True`` shows a note naming a remedy the
    admin can carry out in a minute, while a wrong ``False`` hands them a permanently
    machine-scoped computer whose nightly silently stops delivering.
    """
    if not _delivery_configured(enabled, host, username):
        return False
    try:
        from src.sftp import secret_store

        return not secret_store.select_store().has_secret(host, username)
    except Exception as exc:  # noqa: BLE001 - totality is the contract; the reason is logged
        # ``has_secret`` is total by its own contract; this guards the SELECTION (a refused
        # profile, a missing keyring backend, an import failure in a frozen build) so this
        # function's promise does not depend on another module keeping its.
        logger.warning("Could not confirm the delivery password is readable: %s", type(exc).__name__)
        return True


def _read_delivery_secret(*, enabled: bool, host: str, username: str) -> str:
    """The delivery password to SEED the shared profile with — ``""`` when there is none.

    The payload half of the same fact :func:`delivery_secret_unreadable` gates on, and the
    only place in the parent that materialises the value. ``""`` is what
    ``provisioning._seed_secret`` reads as "nothing was sent": it then writes no blob and
    **makes no claim**, which is the correct end state for an install with delivery off.

    Total, and silent about the value: the return is handed straight to
    :func:`~src.scheduler.provisioning.build_provision_payload` and never logged, never
    formatted into a message, never put on :class:`ProvisionAttempt`.
    """
    if not _delivery_configured(enabled, host, username):
        return ""
    try:
        from src.sftp import secret_store

        return secret_store.select_store().get_password(host, username) or ""
    except Exception as exc:  # noqa: BLE001 - the gate already refused this; never raise here
        logger.warning("Could not read the delivery password to copy into the shared folder: %s", type(exc).__name__)
        return ""


# --------------------------------------------------------------------------- #
# provision — the round trip (S-2b.3)                                          #
# --------------------------------------------------------------------------- #

#: ``ProvisionRefused.__init__`` interpolates its step as ``"(step: <value>)"``. Recovered
#: with a pattern rather than a second copy of the sentence, and the FIRST match is taken:
#: a rollback failure appends a SECOND marker (``"(step: rollback)"``) after the primary
#: one, and the primary step is the cause. Pinned against the real exception in
#: ``tests/test_provision_session.py`` for every member, so a re-worded refusal is red here
#: rather than silently generic.
_STEP_MARKER = re.compile(r"\(step: ([a-z_]+)\)")
#: The one diagnostic a refusal is allowed to carry besides the step — a small integer, not
#: stderr (which quotes paths and account names).
_ICACLS_MARKER = re.compile(r"\[icacls exit (-?\d+)\]")


class ProvisionOutcome(StrEnum):
    """The bounded result of :func:`request_provision` — a typed state, never a message.

    Mirrors :class:`GrantOutcome`, for the same reason: the view branches on the MEMBER, so
    a re-worded child message can never move a surface, and the one outcome that carries a
    sentinel (:attr:`DIFFERENT_ACCOUNT`) can never republish it.

    **There is deliberately no "committed but the nightly failed" member here.** The child
    registers the task LAST, after the HKLM commit, so that state is real — but this
    function cannot honestly report it: the message alone cannot tell a post-commit
    registration failure from a child that refused before it started, and the child is
    KILLED on a timeout, when it may have done everything. The authority on "did the switch
    commit?" is the parent's own re-read inside :func:`complete_handover`, and the two
    facts are combined — explicitly, and in one pure place — by
    :func:`src.ui_flet.handover_result.compose`.
    """

    PROVISIONED = "provisioned"  # the child reported the whole sequence done, nightly included
    REFUSED = "refused"  # the child refused with a bounded ProvisionStep
    FAILED = "failed"  # the child ran and failed with a schedule-side canonical
    DECLINED = "declined"  # the admin said No at the UAC prompt
    LAUNCH_FAILED = "launch_failed"  # Windows would not show the prompt at all
    UNCONFIRMED = "unconfirmed"  # timed out, or wrote no readable result
    DIFFERENT_ACCOUNT = "different_account"  # a DIFFERENT administrator consented (cross-SID)
    UNAVAILABLE = "unavailable"  # the handshake could not even be built (or not Windows)
    # DSYNC-33 - PRE-UAC refusals for a managed service account: the name did not resolve to a
    # SID, so nothing was asked of Windows and no prompt was shown. Two members because the two
    # facts need different advice: the directory ANSWERED "no such account" (check the name)
    # versus the question could not be asked (a domain controller out of reach - not a typo).
    ACCOUNT_NOT_FOUND = "account_not_found"
    ACCOUNT_LOOKUP_FAILED = "account_lookup_failed"


def _lookup_outcome(winerror: int | None) -> ProvisionOutcome:
    """``LookupAccountNameW``'s Win32 error -> the pre-UAC outcome. Pure and total.

    Only ``ERROR_NONE_MAPPED`` is an ANSWER ("no such account"); every other code (no logon
    servers, a broken trust, an RPC failure, or no code at all) means the lookup could not be
    made, and calling that a typo would send an admin to re-check a name that is right.
    """
    if winerror == provisioning.ERROR_NONE_MAPPED:
        return ProvisionOutcome.ACCOUNT_NOT_FOUND
    return ProvisionOutcome.ACCOUNT_LOOKUP_FAILED


@dataclass(frozen=True)
class ProvisionAttempt:
    """What one elevated ``provision`` request came back with.

    Attributes:
        outcome: the bounded state. Everything the view branches on.
        step: the refusal's step id, and ONLY for :attr:`ProvisionOutcome.REFUSED`. The
            interpolated ``ProvisionRefused.message`` is deliberately NOT carried: it can
            contain an icacls exit code and a rollback clause, and a view rendering it
            would be rendering text no copy review ever saw. ``setup_errors``'
            ``classify_provision_step`` turns this into prose.
        icacls_exit: the one extra diagnostic a refusal may carry — a small integer support
            can quote. ``None`` whenever the refusal had none.
        message: the schedule-side canonical for :attr:`ProvisionOutcome.FAILED` ONLY, so
            the view can run it through ``setup_errors.classify_schedule_error`` exactly as
            an ordinary register failure. Already ``DSYNC_``-sanitized, and re-labelled to
            ``_MSG_ELEVATED_ACCESS_DENIED`` where it applies, by the same rules
            ``windows._register_elevated`` applies to its own child's message.
        check: the schedule check's bounded result (DSYNC-33), on
            :attr:`ProvisionOutcome.PROVISIONED` only, and only when the check was asked for
            (a managed service account). ``None`` everywhere else - no check ran.
    """

    outcome: ProvisionOutcome
    step: ProvisionStep | None = None
    icacls_exit: int | None = None
    message: str = ""
    check: ScheduleCheck | None = None


def request_provision(
    task_name: str,
    exe_path: Path,
    sis_type: str,
    input_dir: Path,
    output_dir: Path,
    run_time: str,
    sftp: bool = False,
    *,
    sftp_host: str,
    sftp_username: str,
    principal: Principal,
    run_highest: bool = True,
    wake_to_run: bool,
) -> ProvisionAttempt:
    """Provision this computer for shared settings and register the nightly — one UAC prompt.

    The argument shape mirrors :func:`src.scheduler.windows.register_task` rather than
    :func:`~src.scheduler.provisioning.build_provision_payload`'s lower-level one, so the
    view keeps passing the fields it already holds and the task's action line is composed
    by ``windows._build_action_args`` — the SAME function the ordinary register path uses.
    A second spelling of that command line is how a nightly ends up running with different
    arguments depending on which door it was created through.

    **The delivery secret is read HERE, not passed in.** The value then exists in exactly
    one place (this frame) before it becomes a field of a DPAPI-sealed payload — never in a
    view local, never in a closure the UI holds for the length of a session.

    **Call** :func:`complete_handover` **on every outcome this returns**, including
    :attr:`ProvisionOutcome.DECLINED` and :attr:`~ProvisionOutcome.UNCONFIRMED`. That
    function's gate is the parent's own switch read, and a child killed on the bounded wait
    may well have committed.

    ``principal`` is required and undefaulted (plan 0049 S-3): a provision only ever happens
    for a FOREIGN principal, and :func:`src.ui_flet.setup_gates.register_block` must read
    ``NONE`` before this is called. It replaced the ``run_as_user`` / ``run_as_password``
    pair for the reason the whole slice exists — the two unattended kinds differ in whether a
    password exists at all, so "is there a password?" cannot decide which one was asked for.
    The account goes through the validator its OWN kind names
    (``task_com.validate_principal_account``), and a blank account is refused here rather
    than resolved: there is nothing to provision for "the signed-in account".
    **A managed service account is resolved to a SID BEFORE the prompt (DSYNC-33).** The
    shape check cannot tell a typo, a local account or an unreachable domain controller from a
    real gMSA, and Windows would otherwise answer only after the admin approved a machine-wide
    change. A name that does not resolve returns :attr:`ProvisionOutcome.ACCOUNT_NOT_FOUND` or
    :attr:`~ProvisionOutcome.ACCOUNT_LOOKUP_FAILED` with nothing asked of Windows. The same
    kind asks the child for the post-registration schedule check (``schedule_check``); its
    bounded result rides back on :attr:`ProvisionAttempt.check` and is logged here as ONE line.

    Never raises — every failure is a bounded :class:`ProvisionAttempt`.
    """
    try:
        user = validate_principal_account(principal.kind, principal.user)
    except (ValueError, TypeError, AttributeError):
        # Exactly what the child's ``_principal_account`` would refuse with, decided here so
        # the admin reads the account-name copy instead of a generic "couldn't start".
        return ProvisionAttempt(outcome=ProvisionOutcome.REFUSED, step=ProvisionStep.PRINCIPAL)

    wants_check = schedule_check.applies_to(principal.kind)
    if principal.kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
        refused = _resolve_before_prompt(user)
        if refused is not None:
            return ProvisionAttempt(outcome=refused)

    req_path: Path | None = None
    res_path: Path | None = None
    try:
        # Validated in BOTH halves (the child re-validates every field): a request file is
        # attacker-influencable in ways argv is not, and the parent has the log sink.
        task_name = validate_task_name(task_name)
        validate_run_time(run_time)
        arguments, working_dir = windows._build_action_args(
            exe_path, validate_sis_type(sis_type), input_dir, output_dir, sftp
        )
        payload = build_provision_payload(
            task_name=task_name,
            exe=str(exe_path),
            arguments=arguments,
            working_dir=str(working_dir),
            run_time=run_time,
            user=user,
            kind=principal.kind,
            run_highest=run_highest,
            schedule_check=wants_check,
            wake_to_run=wake_to_run,
            password=principal.password,
            sftp_host=sftp_host,
            sftp_username=sftp_username,
            sftp_password=_read_delivery_secret(enabled=sftp, host=sftp_host, username=sftp_username),
        )

        logger.info("Setting this computer up for shared DistrictSync settings via one-time elevation (UAC).")
        req_path = elevation.write_request(payload)
        res_path = req_path.with_suffix(".res")
        outcome = windows.run_elevated_child(req_path, res_path)

        if outcome.result is ElevationResult.DECLINED:
            return ProvisionAttempt(outcome=ProvisionOutcome.DECLINED)
        if outcome.result is ElevationResult.LAUNCH_FAILED:
            return ProvisionAttempt(outcome=ProvisionOutcome.LAUNCH_FAILED)
        if outcome.result is ElevationResult.TIMEOUT:
            # Post-consent, and the child is TERMINATED — it may have completed every step,
            # including the commit. Its own result file is not evidence of anything here
            # (``request_access`` makes the same refusal for the same reason), so the answer
            # is UNCONFIRMED and ``complete_handover``'s switch read settles it.
            return ProvisionAttempt(outcome=ProvisionOutcome.UNCONFIRMED)

        result = elevation.read_result(res_path)
        if result is None:
            return ProvisionAttempt(outcome=ProvisionOutcome.UNCONFIRMED)
        if result.get("ok"):
            return ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED, check=_read_check(result, asked=wants_check))
        return _classify_child_refusal(str(result.get("message", "")))
    except ProvisionRefused as exc:
        # FIRST, because ``ProvisionRefused`` IS a ``RuntimeError`` — the generic rung below
        # would otherwise swallow the one refusal that arrives with a step already in hand
        # (``build_provision_payload``'s own ``OVERRIDE``) and report it as "unavailable".
        return ProvisionAttempt(outcome=ProvisionOutcome.REFUSED, step=exc.step, icacls_exit=exc.icacls_exit)
    except (OSError, RuntimeError, ValueError) as exc:
        # The PRE-CONSENT handshake (DPAPI seal, profile dir, icacls) can raise straight past
        # this contract — the same shape ``windows._register_elevated`` catches, for the same
        # reason: a boot-path failure with no log line is what a district reports as
        # "nothing happened".
        logger.error("Could not start the shared-settings change (%s).", type(exc).__name__)
        return ProvisionAttempt(outcome=ProvisionOutcome.UNAVAILABLE)
    finally:
        for path in (req_path, res_path):
            if path is not None:
                # Best-effort, exactly as ``windows._cleanup_handshake`` is: an unremovable
                # handshake file is swept by ``elevation.sweep_orphans`` within the hour.
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)


#: The ONE schedule-check log line (DSYNC-33): the verdict NAME, the code through the one hex
#: formatter, and the two advisory facts. No account, no path - the same rule as ``_fail``'s line.
CHECK_LOG_FORMAT = "Schedule check after registration: %s [result %s] batch-logon right: %s; installed here: %s"


def _resolve_before_prompt(user: str) -> ProvisionOutcome | None:
    """``None`` when ``user`` resolves to a SID, else the pre-UAC refusal. Logs a bounded line."""
    try:
        provisioning.account_sid(user)
    except provisioning.AccountLookupError as exc:
        outcome = _lookup_outcome(exc.winerror)
        logger.warning(
            "The managed service account did not resolve before the permission prompt (%s, Windows error %s); "
            "nothing was changed.",
            outcome.value,
            exc.winerror,
        )
        return outcome
    except (LookupError, OSError) as exc:
        logger.warning(
            "The managed service account could not be looked up (%s); nothing was changed.", type(exc).__name__
        )
        return ProvisionOutcome.ACCOUNT_LOOKUP_FAILED
    return None


def _read_check(result: dict[str, object], *, asked: bool) -> ScheduleCheck | None:
    """The child's bounded check result, re-validated, logged as ONE line. ``None`` = not asked.

    Asked but absent (a child that could not run it at all) is reported as UNAVAILABLE rather
    than as silence: the admin was promised a check, so its absence is itself the answer.
    """
    if not asked:
        return None
    check = ScheduleCheck.from_wire(result.get("check"))
    if check is None:
        check = ScheduleCheck(verdict=schedule_check.CheckVerdict.UNAVAILABLE)
    logger.log(
        logging.INFO if check.passed else logging.WARNING,
        CHECK_LOG_FORMAT,
        check.verdict.name,
        task_com.format_hresult(check.code),
        check.batch_right.value,
        check.installed.value,
    )
    return check


def _classify_child_refusal(child_message: str) -> ProvisionAttempt:
    """Reduce an ``ok: False`` child result to a bounded attempt. Never echoes a sentinel.

    Three shapes reach here, and they are told apart by what the message IS rather than by
    what it says:

    1. the cross-SID sentinel — a DIFFERENT administrator answered the prompt, so the child
       could not read (or unseal) the request. Detected, never republished: the sentinel
       carries the ``DSYNC_`` prefix no admin-facing string may carry;
    2. a :class:`~src.scheduler.provisioning.ProvisionRefused` message — recognised by its
       step marker, reduced to the STEP (plus the icacls exit code where one exists) and
       otherwise discarded;
    3. anything else — a ``task_com`` canonical from the registration the child runs last,
       or one of ``elevated_apply``'s own refusals. Sanitized and re-labelled by the same
       two rules ``windows._register_elevated`` applies, so the message the view hands to
       ``classify_schedule_error`` is one that function can key on by exact equality.
    """
    if DIFFERENT_ACCOUNT_SENTINEL in child_message:
        return ProvisionAttempt(outcome=ProvisionOutcome.DIFFERENT_ACCOUNT)

    step_match = _STEP_MARKER.search(child_message)
    if step_match is not None:
        try:
            step = ProvisionStep(step_match.group(1))
        except ValueError:
            # A step id this build does not know — an older/newer child. Treat it as a
            # refusal without a step rather than as a schedule failure: it is still true
            # that the child refused, and inventing a step would be worse than naming none.
            return ProvisionAttempt(outcome=ProvisionOutcome.REFUSED)
        exit_match = _ICACLS_MARKER.search(child_message)
        return ProvisionAttempt(
            outcome=ProvisionOutcome.REFUSED,
            step=step,
            icacls_exit=int(exit_match.group(1)) if exit_match is not None else None,
        )

    message = windows._sanitize_child_message(child_message)
    if message == task_com.MSG_ACCESS_DENIED:
        # The parent's own token is irrelevant here: the prompt WAS approved, so the admin
        # must not be told to answer it again (the loop SD60 ran on 2026-09-14).
        message = windows._MSG_ELEVATED_ACCESS_DENIED
    return ProvisionAttempt(outcome=ProvisionOutcome.FAILED, message=message)


# --------------------------------------------------------------------------- #
# grant_current_user — a second administrator, one UAC prompt                  #
# --------------------------------------------------------------------------- #


class GrantOutcome(StrEnum):
    """The bounded result of :func:`request_access` — a typed state, never a message.

    The window that renders these branches on the MEMBER. Matching on ``str(exc)`` (or on
    the child's message) is the fragility ``setup_errors`` exists to avoid, and one of these
    outcomes — :attr:`DIFFERENT_ACCOUNT` — carries a sentinel that may never be republished.
    """

    GRANTED = "granted"  # the child applied the ace; re-exec and try the profile again
    DECLINED = "declined"  # the admin said No at the UAC prompt
    DIFFERENT_ACCOUNT = "different_account"  # a DIFFERENT administrator consented (cross-SID)
    REFUSED = "refused"  # the child ran and refused — a provisioning step id
    UNCONFIRMED = "unconfirmed"  # timed out, or wrote no readable result
    LAUNCH_FAILED = "launch_failed"  # Windows would not show the prompt at all
    UNAVAILABLE = "unavailable"  # the handshake could not even be built (or not Windows)


def request_access() -> GrantOutcome:
    """Ask an administrator to grant THIS account access to the shared profile.

    One UAC prompt, one additive ``:M`` ace. The grantee is derived inside the elevated
    child from its own token and is **not** in the payload — a payload-named account would
    let any admin-consented request grant an arbitrary principal Modify on the shared
    profile (see :func:`src.scheduler.provisioning.apply_grant_current_user`). The payload
    therefore carries the op and nothing else.

    **Success is not confirmed here, and deliberately so.** What the caller does with
    :attr:`GrantOutcome.GRANTED` is re-exec; the new process re-runs the real trust
    predicate against the real directory, which is a stronger check than anything this
    function could perform with the access it is trying to obtain.

    Never raises: every failure is one of the bounded :class:`GrantOutcome` members.
    """
    req_path: Path | None = None
    res_path: Path | None = None
    try:
        req_path = elevation.write_request({"op": "grant_current_user"})
        res_path = req_path.with_suffix(".res")
        outcome = windows.run_elevated_child(req_path, res_path)

        if outcome.result is ElevationResult.DECLINED:
            return GrantOutcome.DECLINED
        if outcome.result is ElevationResult.LAUNCH_FAILED:
            return GrantOutcome.LAUNCH_FAILED
        if outcome.result is ElevationResult.TIMEOUT:
            # Post-consent: the terminated child may have applied the ace. Unconfirmed is
            # the honest answer, and the caller's retry is idempotent (an additive ace).
            return GrantOutcome.UNCONFIRMED

        result = elevation.read_result(res_path)
        if result is None:
            return GrantOutcome.UNCONFIRMED
        if result.get("ok"):
            return GrantOutcome.GRANTED
        if DIFFERENT_ACCOUNT_SENTINEL in str(result.get("message", "")):
            # Detected, never echoed: the sentinel carries the DSYNC_ prefix no admin-facing
            # string may republish, and the copy for this branch is written from the TYPE.
            return GrantOutcome.DIFFERENT_ACCOUNT
        return GrantOutcome.REFUSED
    except (OSError, RuntimeError, ValueError) as exc:
        # The PRE-CONSENT handshake (DPAPI seal, profile dir, icacls) can raise straight
        # past this contract — the same shape ``windows._register_elevated`` catches, for
        # the same reason: a boot-path failure with no log line is what a district reports
        # as "nothing happened".
        logger.error("Could not ask Windows for permission to use the shared folder (%s).", type(exc).__name__)
        return GrantOutcome.UNAVAILABLE
    finally:
        for path in (req_path, res_path):
            if path is not None:
                # Best-effort, exactly as ``windows._cleanup_handshake`` is: an unremovable
                # handshake file is swept by ``elevation.sweep_orphans`` within the hour.
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# prune_principal — Remove for a managed service account (DSYNC-33)            #
# --------------------------------------------------------------------------- #


class RevokeOutcome(StrEnum):
    """What happened to the retired account's access to the shared folder on a Remove."""

    REVOKED = "revoked"  # the task is confirmed gone AND the account's aces were removed
    NOT_REVOKED = "not_revoked"  # the access was not removed, or its removal could not be confirmed
    # The elevated op refused BEFORE it deleted anything (an unresolvable account, an untrusted
    # or overridden profile, a malformed request, a handshake that could not be built): nothing
    # changed, and the caller removes the nightly the ORDINARY way — a revoke that cannot run
    # must never be the reason a nightly cannot be removed.
    FALL_BACK = "fall_back"


@dataclass(frozen=True)
class RemovalAttempt:
    """``(ok, message)`` in the remove path's existing vocabulary, plus the revoke's outcome.

    ``ok`` / ``message`` are exactly what ``scheduler.delete`` would return for the same
    event (the ``windows._MSG_*`` elevation canonicals, through ``windows._fail`` /
    ``windows._confirm_removal``), so Setup's remove handler classifies them unchanged.
    ``revoke`` is meaningful only when ``ok``; on :attr:`RevokeOutcome.FALL_BACK` neither
    ``ok`` nor ``message`` is — nothing happened.
    """

    ok: bool
    message: str
    revoke: RevokeOutcome


def request_removal_with_revoke(task_name: str, *, user: str, kind: PrincipalKind) -> RemovalAttempt:
    """Remove the nightly AND the retired account's folder access — ONE UAC prompt. Never raises.

    Plan 0049's named hole: ``prune_principal`` had a handler and an implementation and no
    producer, so a retired gMSA kept read/execute on the shared profile root and modify on its
    ``runs`` folder. This is its producer, for the MANAGED_SERVICE_ACCOUNT principal.

    The child's ordering is the safety property and it is not re-implemented here:
    ``apply_prune_principal`` deletes the task and prunes ONLY after a read-back proves the
    task MISSING — pruning a live task's principal would make every night fail on the
    trust check with nothing on screen. So:

    * an UNCONFIRMED delete (step ``delete``) prunes nothing, and the removal is resolved by
      this side's own read-back, exactly as ``windows.delete_task_elevated`` resolves it;
    * a failed prune (step ``prune``) means the task IS gone and the access is not;
    * a refusal BEFORE the delete is :attr:`RevokeOutcome.FALL_BACK` — the caller then
      removes the nightly the ordinary way.

    Success is never taken from the child's ``ok`` alone: it is CONFIRMED by
    ``windows._confirm_removal`` (security F4, the same rule as every elevated delete).
    """
    try:
        task_name = validate_task_name(task_name)
        user = validate_principal_account(kind, user)
    except ValueError:
        return RemovalAttempt(ok=False, message="", revoke=RevokeOutcome.FALL_BACK)

    req_path: Path | None = None
    res_path: Path | None = None
    try:
        req_path = elevation.write_request(
            {"op": "prune_principal", "task_name": task_name, "user": user, "kind": kind.value}
        )
        res_path = req_path.with_suffix(".res")
        logger.info(
            "Removing scheduled task '%s' and its account's access to the shared folder via one-time elevation (UAC).",
            task_name,
        )
        outcome = windows.run_elevated_child(req_path, res_path)

        if outcome.result is ElevationResult.DECLINED:
            return _removal(
                windows._fail(task_name, windows._MSG_UAC_DECLINED, verb="remove"), RevokeOutcome.NOT_REVOKED
            )
        if outcome.result is ElevationResult.LAUNCH_FAILED:
            return _removal(
                windows._fail(task_name, windows._MSG_ELEVATION_LAUNCH_FAILED, verb="remove"), RevokeOutcome.NOT_REVOKED
            )
        if outcome.result is ElevationResult.TIMEOUT:
            return _removal(_confirm(task_name), RevokeOutcome.NOT_REVOKED)

        result = elevation.read_result(res_path)
        if result is None:
            return _removal(_confirm(task_name), RevokeOutcome.NOT_REVOKED)
        if result.get("ok"):
            confirmed = _confirm(task_name)
            return _removal(confirmed, RevokeOutcome.REVOKED if confirmed[0] else RevokeOutcome.NOT_REVOKED)

        child_message = str(result.get("message", ""))
        if DIFFERENT_ACCOUNT_SENTINEL in child_message:
            return _removal(
                windows._fail(task_name, windows._MSG_DIFFERENT_ACCOUNT, verb="remove"), RevokeOutcome.NOT_REVOKED
            )
        step = _refusal_step(child_message)
        if step in (ProvisionStep.DELETE, ProvisionStep.PRUNE):
            if step is ProvisionStep.PRUNE:
                logger.warning("The nightly's account kept its access to the shared folder (step: prune).")
            return _removal(_confirm(task_name), RevokeOutcome.NOT_REVOKED)
        logger.warning(
            "The account's access could not be removed with the nightly (%s); removing the nightly the ordinary way.",
            step.value if step is not None else "refused",
        )
        return RemovalAttempt(ok=False, message="", revoke=RevokeOutcome.FALL_BACK)
    except (OSError, RuntimeError, ValueError) as exc:
        # The PRE-CONSENT handshake could not be built — nothing was asked of Windows, so the
        # ordinary remove is still available and still right.
        logger.warning(
            "Could not prepare the access removal (%s); removing the nightly the ordinary way.", type(exc).__name__
        )
        return RemovalAttempt(ok=False, message="", revoke=RevokeOutcome.FALL_BACK)
    finally:
        for path in (req_path, res_path):
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)


def _confirm(task_name: str) -> tuple[bool, str]:
    """The elevated remove's own read-back confirmation — the SAME function ``delete_task_elevated`` uses."""
    return windows._confirm_removal(
        task_name, on_unconfirmed=windows._MSG_ELEVATION_REMOVE_UNCONFIRMED, path_label="Elevated removal"
    )


def _removal(result: tuple[bool, str], revoke: RevokeOutcome) -> RemovalAttempt:
    ok, message = result
    return RemovalAttempt(ok=ok, message=message, revoke=revoke)


def _refusal_step(child_message: str) -> ProvisionStep | None:
    """The FIRST ``(step: …)`` marker of a ``ProvisionRefused`` message, or ``None``."""
    match = _STEP_MARKER.search(child_message)
    if match is None:
        return None
    try:
        return ProvisionStep(match.group(1))
    except ValueError:
        return None
