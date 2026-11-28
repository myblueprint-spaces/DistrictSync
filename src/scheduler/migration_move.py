"""Moving the live nightly to the machine-wide install: the PRIVILEGED half (plan 0055 D1b, DSYNC-116).

``--migrate --apply`` (:mod:`src.scheduler.migration_apply`, unelevated) collects every fact and secret first, then
asks for ONE elevated child (``elevated_apply``'s ``migrate_nightly`` op), which runs :func:`move_nightly`:

0. **grant** a service account the shared profile (RX on the root, M on ``runs``; provisioning's own step 3b,
   ``provisioning.grant_principal``). The signed-in account needs none. A grant that was NEW is taken back whenever
   the move does not happen (review H2);
1. **probe** the NEW program as the nightly's own account: the DSYNC-33 schedule check
   (``schedule_check.run_for_payload``), a one-shot check task with the new nightly's exact principal, program,
   start-in folder and run level. Anything but a pass (an inconclusive check included) stops here: nothing changed;
2. refuse once the parent's **deadline** (30 minutes before the run time) has passed; otherwise **delete** the old
   task, then **confirm it is MISSING** with a read-back;
3. **register** ``DistrictSync_Daily`` with the same facts and the new program, through the same validators and the
   same ``RegisterTaskDefinition`` call every registration uses (``elevated_apply.register_params`` +
   ``task_com.register_task_definition``);
4. if that register fails, **put the old task back** from the facts read before the delete (same principal, the
   password already in hand, the old program and its exact command line).

**Never two nightlies, never a temporary task.** Every delete and register names the ONE task; Task Scheduler cannot
hold two tasks of one name, and registering the old facts over an unconfirmed delete can only replace, never add. The
only window with no task is between the delete and the register, which the parent keeps 30 minutes clear of the run.

**One process, one prompt.** The whole sequence runs inside one elevated child, so a parent killed after the prompt
does not interrupt it, and every step is bounded (``task_com.bounded``) so the parent's wait
(:data:`CHILD_BUDGET_S`) covers the worst case before it would kill the child. A step that RAISED is never taken as
"did not happen": the read-back decides (a bounded register that timed out may still have landed).

The result crosses back as a bounded :class:`MoveStage` value (the result's ``message``) and the probe's bounded wire
form (``check``): no account, no path, no password. This module never logs (the child has no log sink).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.scheduler import schedule_check, task_com
from src.scheduler.provisioning import ICACLS_TIMEOUT_S
from src.scheduler.schedule_check import ScheduleCheck
from src.scheduler.task_com import PrincipalKind, RegisterParams

#: The ``op`` of the elevated request.
OP = "migrate_nightly"

#: What the request must carry: the nightly's facts, the new program's action and the old one's (for the put-back).
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "op",
        "task_name",
        "run_time",
        "user",
        "kind",
        "wake_to_run",
        "run_highest",
        "exe",
        "arguments",
        "working_dir",
        "old_exe",
        "old_arguments",
        "old_working_dir",
        "deadline",
    }
)

#: Each bounded step the child can take, worst case (the icacls calls are listed, not hidden in the margin): probe, delete, confirm, register, read-back after a raise,
#: put-back register, its read-back. The parent's wait is their sum plus a margin (the child is KILLED at that bound and
#: runs no ``finally``, so the bound must cover the slowest path, not a typical one).
_STEP_BUDGETS_S: tuple[float, ...] = (
    schedule_check.TOTAL_BUDGET_S,
    task_com.DELETE_TIMEOUT_S,
    task_com.READ_TIMEOUT_S,
    task_com.REGISTER_TIMEOUT_S,
    task_com.READ_TIMEOUT_S,
    task_com.REGISTER_TIMEOUT_S,
    task_com.READ_TIMEOUT_S,
    # The grant (two icacls calls) and the take-back (up to two): second review, LOW 3.
    *(4 * (ICACLS_TIMEOUT_S,)),
)
_MARGIN_S = 20.0
CHILD_BUDGET_S = sum(_STEP_BUDGETS_S) + _MARGIN_S


class MoveStage(StrEnum):
    """Where the sequence ended. The wire value is the member VALUE (the result's ``message``)."""

    MOVED = "migrate_moved"  # the new nightly is registered
    GRANT_FAILED = "migrate_grant_failed"  # the account could not be given the shared profile: nothing changed
    TOO_LATE = "migrate_too_late"  # the deadline passed before the delete: nothing changed
    UNSETTLED = "migrate_unsettled"  # a bounded delete or register timed out: it may still land (review M4)
    PROBE_FAILED = "migrate_probe_failed"  # the new program did not start as the account: nothing changed
    DELETE_FAILED = "migrate_delete_failed"  # the old task is still there (confirmed): nothing changed
    ROLLED_BACK = "migrate_rolled_back"  # the new one could not be registered; the old one is registered again
    ROLLBACK_FAILED = "migrate_rollback_failed"  # neither could be registered: NO nightly


@dataclass(frozen=True)
class MoveOutcome:
    stage: MoveStage
    check: ScheduleCheck | None = None
    #: A NEW grant was to be taken back and taking it back failed (second review, MEDIUM 2): the account keeps access,
    #: and its SID stays in the grant record so the next Remove retries.
    access_kept: bool = False

    def wire(self) -> dict[str, object] | None:
        """The probe's bounded result-file form (``ScheduleCheck.to_wire``), or ``None`` when no probe ran."""
        return self.check.to_wire() if self.check is not None else None


class NightlyOps(Protocol):
    """Every Task Scheduler call the sequence makes: one seam, so a fake can drive every path (including a kill)."""

    def grant(self, user: str, kind: PrincipalKind) -> bool:
        """Give the principal the shared profile; ``True`` when the access was NEW (only that is ever taken back)."""
        ...

    def revoke(self, user: str, kind: PrincipalKind) -> None: ...

    def probe(self, payload: Mapping[str, object]) -> ScheduleCheck: ...

    def delete(self, task_name: str) -> None: ...

    def program(self, task_name: str) -> str | None:
        """The task's program; ``None`` ONLY when Task Scheduler answered "no such task". Raises when unreadable."""
        ...

    def register(self, params: RegisterParams) -> None: ...


class _LiveOps:  # pragma: no cover - drives the real Task Scheduler (Windows, elevated); the lab's T7/T7c
    def grant(self, user: str, kind: PrincipalKind) -> bool:
        from src.scheduler import provisioning

        return provisioning.grant_principal(user, kind)

    def revoke(self, user: str, kind: PrincipalKind) -> None:
        from src.scheduler import provisioning

        provisioning.revoke_principal(user, kind)

    def probe(self, payload: Mapping[str, object]) -> ScheduleCheck:
        return schedule_check.run_for_payload(payload)

    def delete(self, task_name: str) -> None:
        task_com.bounded(
            lambda: task_com.delete_task_by_name(task_name), timeout_s=task_com.DELETE_TIMEOUT_S, label="delete"
        )

    def program(self, task_name: str) -> str | None:
        try:
            facts = task_com.bounded(
                lambda: task_com.read_task(task_name), timeout_s=task_com.READ_TIMEOUT_S, label="read"
            )
        except task_com.TaskComError as exc:
            if exc.scode == task_com.HR_NOT_FOUND:
                return None
            raise
        return facts.action_path

    def register(self, params: RegisterParams) -> None:
        task_com.bounded(
            lambda: task_com.register_task_definition(params), timeout_s=task_com.REGISTER_TIMEOUT_S, label="register"
        )


def _live_ops() -> NightlyOps:
    """A seam: the tests' fake Task Scheduler replaces this."""
    return _LiveOps()


_UNKNOWN = object()


def _program(ops: NightlyOps, task_name: str) -> object:
    """The read-back: the program, ``None`` (confirmed missing) or :data:`_UNKNOWN`. Never raises."""
    try:
        return ops.program(task_name)
    except Exception:  # noqa: BLE001 - an unreadable task is UNKNOWN, never "missing"
        return _UNKNOWN


def _same(program: object, exe: str) -> bool:
    return isinstance(program, str) and program.strip().strip('"').casefold() == exe.strip().strip('"').casefold()


def _register(ops: NightlyOps, task_name: str, params: RegisterParams) -> tuple[bool, bool]:
    """``(landed, timed_out)``. A raise is settled by the read-back, never taken as "did not happen"; a TIMED-OUT one
    is also reported, because its worker cannot be cancelled and may still land after the read (review M4)."""
    try:
        ops.register(params)
        return True, False
    except task_com.BoundedTimeout:
        return _same(_program(ops, task_name), params.exe), True
    except Exception:  # noqa: BLE001 - settled by the read-back
        return _same(_program(ops, task_name), params.exe), False


def move_nightly(
    task_name: str,
    *,
    new: RegisterParams,
    old: RegisterParams,
    probe_payload: Mapping[str, object],
    ops: NightlyOps,
    deadline: float,
    clock: Callable[[], float],
) -> MoveOutcome:
    """Grant, probe, delete, confirm missing, register; put the old one back if that fails. TOTAL for ``Exception``.

    * The principal's access to the shared profile is granted FIRST (review H2): the probe writes its marker into
      ``runs/`` and the moved nightly reads the profile. The signed-in account needs none. A grant that was NEW is taken
      back whenever the move does not happen (a failed probe, the deadline, a refused delete, a put-back).
    * ``deadline`` (epoch seconds, from the parent, review M2): after it the delete is refused, so the only window with
      no task can never reach the run time.
    * Any bounded delete or register that TIMED OUT makes the outcome :attr:`MoveStage.UNSETTLED` (review M4).

    A ``BaseException`` (the process being killed) is deliberately NOT caught: what is left then is exactly what the
    steps so far made, and the parent's read-back reports it (T7c).
    """
    granted = False
    if new.kind is not PrincipalKind.INTERACTIVE_TOKEN:
        try:
            granted = ops.grant(new.user, new.kind)
        except Exception:  # noqa: BLE001 - the grant is the first change; a refusal leaves nothing changed
            return MoveOutcome(MoveStage.GRANT_FAILED)

    def take_back() -> bool:
        """Revoke a NEW grant; ``True`` when that FAILED (reported, never swallowed: second review, MEDIUM 2)."""
        if not granted:
            return False
        try:
            ops.revoke(new.user, new.kind)
        except Exception:  # noqa: BLE001 - carried back as ``access_kept``; the SID stays recorded for the next Remove
            return True
        return False

    try:
        check = ops.probe(probe_payload)
    except Exception:  # noqa: BLE001 - a probe that could not run is not a pass
        check = ScheduleCheck(verdict=schedule_check.CheckVerdict.UNAVAILABLE)
    if not check.passed:
        return MoveOutcome(MoveStage.PROBE_FAILED, check, access_kept=take_back())
    if clock() > deadline:
        return MoveOutcome(MoveStage.TOO_LATE, check, access_kept=take_back())

    timed_out = False
    try:
        ops.delete(task_name)
    except task_com.BoundedTimeout:
        timed_out = True
    except Exception:  # noqa: BLE001 # nosec B110 - settled by the read-back below ("already gone" included)
        pass
    after_delete = _program(ops, task_name)
    if isinstance(after_delete, str):
        if timed_out:
            return MoveOutcome(MoveStage.UNSETTLED, check)  # still there now; the timed-out delete may land later
        # confirmed still there: nothing changed
        return MoveOutcome(MoveStage.DELETE_FAILED, check, access_kept=take_back())
    if after_delete is None:  # confirmed MISSING: register the new one
        landed, late = _register(ops, task_name, new)
        timed_out = timed_out or late
        if landed:
            return MoveOutcome(MoveStage.UNSETTLED if timed_out else MoveStage.MOVED, check)
    # The register failed, or the delete could not be confirmed: register the OLD facts (same name, so this can only
    # replace, never add a second nightly).
    landed, late = _register(ops, task_name, old)
    if timed_out or late:
        return MoveOutcome(MoveStage.UNSETTLED, check)
    kept = take_back()
    return MoveOutcome(MoveStage.ROLLED_BACK if landed else MoveStage.ROLLBACK_FAILED, check, access_kept=kept)


def build_payload(
    *,
    task_name: str,
    run_time: str,
    user: str,
    kind: PrincipalKind,
    password: str | None,
    wake_to_run: bool,
    exe: str,
    arguments: str,
    working_dir: str,
    old_exe: str,
    old_arguments: str,
    old_working_dir: str,
    deadline: float,
) -> dict[str, object]:
    """The ONE spelling of the request (the parent's side). ``password`` only where the kind has one."""
    payload: dict[str, object] = {
        "op": OP,
        "task_name": task_name,
        "run_time": run_time,
        "user": user,
        "kind": kind.value,
        "wake_to_run": bool(wake_to_run),
        "run_highest": True,  # every registration the app makes asks for Highest; run_level_for decides what it gets
        "exe": exe,
        "arguments": arguments,
        "working_dir": working_dir,
        "old_exe": old_exe,
        "old_arguments": old_arguments,
        "old_working_dir": old_working_dir,
        "deadline": float(deadline),
    }
    if password:
        payload["password"] = password
    return payload


def _register_payload(payload: Mapping[str, object], *, old: bool) -> dict[str, object]:
    prefix = "old_" if old else ""
    fields = {
        key: payload[key]
        for key in ("task_name", "run_time", "user", "kind", "wake_to_run", "run_highest")
        if key in payload
    }
    if payload.get("password"):
        fields["password"] = payload["password"]
    return {
        "op": "register",
        **fields,
        "exe": payload[f"{prefix}exe"],
        "arguments": payload[f"{prefix}arguments"],
        "working_dir": payload[f"{prefix}working_dir"],
    }


def _clock() -> float:
    """A seam: the child's wall clock, compared with the parent's deadline (epoch seconds)."""
    return time.time()


def run_in_child(payload: Mapping[str, object], *, ops: NightlyOps | None = None) -> MoveOutcome:
    """The ``migrate_nightly`` op. ``ValueError`` BEFORE anything is touched when the request is not one this build
    writes (both registrations are validated up front, by kind, through ``elevated_apply.register_params``); total
    after that."""
    from src.scheduler import elevated_apply  # lazy: elevated_apply imports this module lazily too

    if not set(payload) >= REQUIRED_FIELDS or payload.get("op") != OP:
        raise ValueError("missing fields")
    new_payload = _register_payload(payload, old=False)
    new = elevated_apply.register_params(new_payload)
    old = elevated_apply.register_params(_register_payload(payload, old=True))
    deadline = payload["deadline"]
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise ValueError("deadline")
    if new.task_name != old.task_name:  # pragma: no cover - both are built from the one field
        raise ValueError("two task names")
    return move_nightly(
        new.task_name,
        new=new,
        old=old,
        probe_payload=new_payload,
        ops=ops or _live_ops(),
        deadline=float(deadline),
        clock=_clock,
    )
