"""Moving the live nightly to the machine-wide install: the PRIVILEGED half (plan 0055 D1b, DSYNC-116).

``--migrate --apply`` (:mod:`src.scheduler.migration_apply`, unelevated) collects every fact and secret first, then
asks for ONE elevated child (``elevated_apply``'s ``migrate_nightly`` op), which runs :func:`move_nightly`:

1. **probe** the NEW program as the nightly's own account: the DSYNC-33 schedule check
   (``schedule_check.run_for_payload``), a one-shot check task with the new nightly's exact principal, program,
   start-in folder and run level. Anything but a pass (an inconclusive check included) stops here: nothing changed;
2. **delete** the old task, then **confirm it is MISSING** with a read-back;
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

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.scheduler import schedule_check, task_com
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
    }
)

#: Each bounded step the child can take, worst case: probe, delete, confirm, register, read-back after a raise,
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
)
_MARGIN_S = 20.0
CHILD_BUDGET_S = sum(_STEP_BUDGETS_S) + _MARGIN_S


class MoveStage(StrEnum):
    """Where the sequence ended. The wire value is the member VALUE (the result's ``message``)."""

    MOVED = "migrate_moved"  # the new nightly is registered
    PROBE_FAILED = "migrate_probe_failed"  # the new program did not start as the account: nothing changed
    DELETE_FAILED = "migrate_delete_failed"  # the old task is still there (confirmed): nothing changed
    ROLLED_BACK = "migrate_rolled_back"  # the new one could not be registered; the old one is registered again
    ROLLBACK_FAILED = "migrate_rollback_failed"  # neither could be registered: NO nightly


@dataclass(frozen=True)
class MoveOutcome:
    stage: MoveStage
    check: ScheduleCheck | None = None

    def wire(self) -> dict[str, object] | None:
        """The probe's bounded result-file form (``ScheduleCheck.to_wire``), or ``None`` when no probe ran."""
        return self.check.to_wire() if self.check is not None else None


class NightlyOps(Protocol):
    """Every Task Scheduler call the sequence makes: one seam, so a fake can drive every path (including a kill)."""

    def probe(self, payload: Mapping[str, object]) -> ScheduleCheck: ...

    def delete(self, task_name: str) -> None: ...

    def program(self, task_name: str) -> str | None:
        """The task's program; ``None`` ONLY when Task Scheduler answered "no such task". Raises when unreadable."""
        ...

    def register(self, params: RegisterParams) -> None: ...


class _LiveOps:  # pragma: no cover - drives the real Task Scheduler (Windows, elevated); the lab's T7/T7c
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


def _register(ops: NightlyOps, task_name: str, params: RegisterParams) -> bool:
    """Register and say whether it LANDED: a raise is settled by the read-back, never taken as "did not happen"."""
    try:
        ops.register(params)
        return True
    except Exception:  # noqa: BLE001 - a bounded register that timed out may still have landed: read it back
        return _same(_program(ops, task_name), params.exe)


def move_nightly(
    task_name: str,
    *,
    new: RegisterParams,
    old: RegisterParams,
    probe_payload: Mapping[str, object],
    ops: NightlyOps,
) -> MoveOutcome:
    """Probe, delete, confirm missing, register; put the old one back if that fails. TOTAL for ``Exception``.

    A ``BaseException`` (the process being killed) is deliberately NOT caught: what is left then is exactly what the
    steps so far made, and the parent's read-back reports it (T7c).
    """
    try:
        check = ops.probe(probe_payload)
    except Exception:  # noqa: BLE001 - a probe that could not run is not a pass
        check = ScheduleCheck(verdict=schedule_check.CheckVerdict.UNAVAILABLE)
    if not check.passed:
        return MoveOutcome(MoveStage.PROBE_FAILED, check)

    # A delete that raised ("already gone" included) is settled by the read-back below, never by the raise.
    with contextlib.suppress(Exception):
        ops.delete(task_name)
    after_delete = _program(ops, task_name)
    if isinstance(after_delete, str):
        return MoveOutcome(MoveStage.DELETE_FAILED, check)  # confirmed still there: nothing changed
    if after_delete is None and _register(ops, task_name, new):  # confirmed MISSING: register the new one
        return MoveOutcome(MoveStage.MOVED, check)
    # The register failed, or the delete could not be confirmed: register the OLD facts (same name, so this can only
    # replace, never add a second nightly).
    if _register(ops, task_name, old):
        return MoveOutcome(MoveStage.ROLLED_BACK, check)
    return MoveOutcome(MoveStage.ROLLBACK_FAILED, check)


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
    if new.task_name != old.task_name:  # pragma: no cover - both are built from the one field
        raise ValueError("two task names")
    return move_nightly(new.task_name, new=new, old=old, probe_payload=new_payload, ops=ops or _live_ops())
