"""The PARENT half of machine-scope provisioning (plan 0049 S-1b-ii.1 / ii.2).

Everything here runs UNELEVATED, in the session that asked for the change. Its opposite
number is :mod:`src.scheduler.provisioning`, which runs behind the UAC boundary and may
not log at all; this module is the side that has a log sink, a window and an ``AppConfig``.

Two jobs:

* :func:`complete_handover` — the post-provision session steps. Gated on **the parent's
  OWN re-read of the HKLM switch** (:func:`src.utils.paths.machine_switch_on`), never on
  the child's claim: ``run_elevated`` kills the child on its bounded wait, so the result
  file is absent on exactly the failures where it may nonetheless have committed. Trusting
  that absence would keep the session writing through a stale per-user pin, and every edit
  made after the commit would vanish at the next launch.
* :func:`request_access` — the ``grant_current_user`` round trip behind one UAC prompt, for
  a SECOND administrator on an already-provisioned computer. It deliberately depends on
  nothing the pin provides: the handshake lives under
  :func:`~src.utils.paths.handshake_dir`, which is per-user in every scope and still
  resolves when :func:`~src.utils.paths.user_data_dir` REFUSES — which is the only state
  this function is ever called in.

**Nothing in the app calls :func:`complete_handover` yet** — Schedule-time dispatch is S-2.
:func:`request_access` IS live, from the launcher's refusal path (S-1b-ii.2).
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from src.config.app_config import CONFIG_FILENAME
from src.scheduler import elevation, windows
from src.scheduler.elevated_apply import DIFFERENT_ACCOUNT_SENTINEL
from src.scheduler.elevation import ElevationResult
from src.utils import paths
from src.utils.logger import get_logger

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
