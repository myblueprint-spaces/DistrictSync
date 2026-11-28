"""The elevated child — ``DistrictSync --elevated-apply <request> <result>`` (plan 0041 S1b).

The unattended (stored-password / RunLevel Highest) schedule paths need ONE elevated
process. Until S1b that child was ``powershell.exe -EncodedCommand`` running a bootstrap
script; it is now **DistrictSync itself**, running THIS module — which executes the very
same ``task_com`` functions the direct path uses. The single-source property the old PS
``_register_body`` text-sharing protected is now structural: registration logic exists
once, in Python, on both sides of the UAC boundary.

**Dispatch-first, minimal child (Round-1 security blocker, plan 0041).** ``src/main.py``
recognises ``--elevated-apply`` by an argv check ABOVE its CLI preamble, so this module
runs with NONE of the preamble's side effects: no legacy-profile migration, no console
attach, no log-sink configuration, no orphan sweep. An elevated process performing
best-effort filesystem work in user-writable directories is an EoP surface; this child
touches the DPAPI request it reads and the result it writes — plus, for a ``provision`` that
asked for it (DSYNC-33), the schedule check's one-shot task and its marker in the shared
``runs`` folder, both deleted before it exits (``schedule_check.run_check``) — and diagnostics
ride the result file, never a log sink it would have to configure.

**Fail-closed ladder** (mirrors the retired PS bootstrap, pinned by the refusal table in
``tests/test_elevated_apply.py``):

* wrong argument count → exit 2 (no result path is trustworthy, so nothing is written);
* request missing / oversized / unreadable → a refusal result, exit 0;
* the request cannot be READ (``PermissionError`` — the owner-only DACL refuses a DIFFERENT
  administrator) **or** DPAPI unprotect fails (cross-SID consent — a DIFFERENT admin clicked
  Yes) → the ``DSYNC_DIFFERENT_ACCOUNT`` sentinel result, exit 0 — the parent maps it to the
  canonical different-account message. The read rung MUST precede the generic ``OSError``
  one, or the cross-account case surfaces as "the request was missing" (defect A8);
* malformed payload / unknown op / invalid field values → a refusal result, exit 0;
* the ``task_com`` call raises → ``{ok: False, message: <canonical>}``;
* success → ``{ok: True}`` — and, after a ``provision`` whose payload asked for the schedule
  check (DSYNC-33), ``{ok: True, check: {verdict, code, batch_right, installed}}``: three
  enum values and one integer, never free text, a path or an account.

The child ALWAYS exits 0 on a written result: the parent's verdict comes from the result
file plus the read-back confirm (D5 — a child exit code is never trusted as success).
**Every input is re-validated here** even though the parent validated it too: the child is
the privileged half, and a request file is attacker-influencable in ways argv is not.

The password exists in this process only as the payload field handed to
``task_com.register_task_definition`` (an in-process BSTR to ``RegisterTaskDefinition``) —
never argv, never env, never the result, and there is no logger here to leak it to.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.scheduler import task_com

# The request file is DPAPI-sealed by the parent and small; anything larger is not ours.
_MAX_REQUEST_BYTES = 64 * 1024

# The cross-SID sentinel — MUST match what src/scheduler/windows.py maps to
# _MSG_DIFFERENT_ACCOUNT (the contract the retired PS bootstrap established).
DIFFERENT_ACCOUNT_SENTINEL = "DSYNC_DIFFERENT_ACCOUNT"

_REQUIRED_REGISTER_FIELDS = frozenset(
    {"op", "task_name", "exe", "arguments", "working_dir", "run_time", "user", "kind", "run_highest", "wake_to_run"}
)

# ``provision`` registers the nightly as its last step, so it carries every register field
# PLUS the per-user profile the parent resolved (which the child re-derives and cross-checks
# — a request file is attacker-influencable in ways argv is not).
_REQUIRED_PROVISION_FIELDS = _REQUIRED_REGISTER_FIELDS | {"source_data_dir"}

# The child's OWN refusal vocabulary — named so the classifier can decide each one explicitly
# (plan 0047) instead of matching a literal typed twice. Every value is admin-facing copy and
# must stay free of the markers `messages.carries_foreign_marker` guards (pinned in the tests).
_MSG_REQUEST_INVALID = "The elevated request was not valid."
# GENUINELY absent — swept, or never written. Since plan 0047 a request the child cannot READ
# (a different administrator answered the prompt, so the owner-only DACL refuses it) no longer
# lands here: that is the DIFFERENT_ACCOUNT_SENTINEL rung below (defect A8, SD51 2026-09-16).
_MSG_REQUEST_MISSING = "The elevated request was missing."
_MSG_REQUEST_UNREADABLE = "The elevated request could not be read."
_MSG_CHILD_FLOOR = "The schedule change failed in the elevated step."

CHILD_REFUSALS: tuple[str, ...] = (
    _MSG_REQUEST_INVALID,
    _MSG_REQUEST_MISSING,
    _MSG_REQUEST_UNREADABLE,
    _MSG_CHILD_FLOOR,
)


def _write_result(res_path: Path, ok: bool, message: str = "", *, check: dict[str, object] | None = None) -> None:
    """Atomic plaintext result — temp + ``os.replace`` so the parent never sees a partial.

    The result carries NO secret by design (the parent's ``read_result`` cap and the D5
    handshake both assume that), so plain UTF-8 JSON is correct here. ``check`` is the
    schedule check's BOUNDED wire form (``ScheduleCheck.to_wire``) and is present only when a
    check ran — every other result keeps its exact pre-DSYNC-33 shape.
    """
    result: dict[str, object] = {"ok": ok, "message": message}
    if check is not None:
        result["check"] = check
    payload = json.dumps(result)
    tmp = res_path.with_name(res_path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, res_path)


def run_elevated_apply(args: list[str]) -> int:
    """The child entry. Returns the process exit code (0 whenever a result was written)."""
    if len(args) != 2:
        return 2  # no trustworthy result path — the parent resolves via read-back

    res_path = Path(args[1])
    try:
        return _apply(Path(args[0]), res_path)
    except Exception:  # noqa: BLE001 - the child floor: a written refusal beats a traceback
        with contextlib.suppress(OSError):
            _write_result(res_path, False, _MSG_CHILD_FLOOR)
        return 0


def _apply(req_path: Path, res_path: Path) -> int:
    # Lazy, like every other import here: ``provisioning`` imports ``windows``, which
    # imports THIS module for the cross-SID sentinel, so a module-level import would close
    # the cycle at whichever of the three got imported first.
    from src.scheduler import elevation, provisioning, task_com

    # --- read + unseal the request (fail closed at every rung) ----------------------
    try:
        if req_path.stat().st_size > _MAX_REQUEST_BYTES:
            _write_result(res_path, False, _MSG_REQUEST_INVALID)
            return 0
        sealed = req_path.read_bytes()
    except PermissionError:
        # A8 (SD51, 2026-09-16): the request is sealed under the SIGNED-IN user's profile with
        # an owner-only DACL (elevation.write_request). A DIFFERENT administrator answering the
        # UAC prompt is refused the READ — so a refused read IS the cross-account signature, and
        # it must fail closed on the SAME sentinel the DPAPI rung below uses. PermissionError is
        # an OSError subclass, so this rung MUST precede the generic one.
        _write_result(res_path, False, DIFFERENT_ACCOUNT_SENTINEL)
        return 0
    except OSError:
        _write_result(res_path, False, _MSG_REQUEST_MISSING)
        return 0

    try:
        raw = elevation.unprotect_blob(sealed)
    except OSError:
        # DPAPI CurrentUser under a DIFFERENT SID than the one that sealed it — the
        # consenting admin is not the requesting user. Fail CLOSED with the sentinel.
        _write_result(res_path, False, DIFFERENT_ACCOUNT_SENTINEL)
        return 0

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _write_result(res_path, False, _MSG_REQUEST_UNREADABLE)
        return 0
    if not isinstance(payload, dict):
        _write_result(res_path, False, _MSG_REQUEST_UNREADABLE)
        return 0

    # --- dispatch (re-validating EVERYTHING in the privileged half) ------------------
    op = payload.get("op")
    check: dict[str, object] | None = None
    try:
        if op == "register":
            _do_register(payload)
        elif op == "delete":
            _do_delete(payload)
        elif op == "provision":
            _do_provision(payload)
            check = _schedule_check(payload)
        elif op == "machine_setup":
            _do_machine_setup(payload)
        elif op == "set_update_mode":
            _do_set_update_mode(payload)
        elif op == "grant_current_user":
            _do_grant_current_user(payload)
        elif op == "prune_principal":
            _do_prune_principal(payload)
        elif op == "migrate_nightly":
            # DSYNC-116: probe, delete, confirm missing, register, or put the old one back. Validated before any
            # change (a ValueError below means nothing was touched); total after that. The bounded stage id is the
            # whole message, and the probe's bounded wire form rides ``check``: never an account or a password.
            from src.scheduler import migration_move

            moved = migration_move.run_in_child(payload)
            _write_result(
                res_path, moved.stage is migration_move.MoveStage.MOVED, moved.stage.value, check=moved.wire()
            )
            return 0
        else:
            _write_result(res_path, False, _MSG_REQUEST_UNREADABLE)
            return 0
    except provisioning.ProvisionRefused as exc:
        # A BOUNDED step id (plus an icacls exit code where one exists) and nothing else —
        # never a resolved path, never stderr, never either of the two passwords the
        # provision payload can carry. Ordered ABOVE the task_com rung only for clarity;
        # the two exception types are unrelated.
        _write_result(res_path, False, exc.message)
        return 0
    except task_com.TaskComError as exc:
        _write_result(res_path, False, exc.message)
        return 0
    except ImportError:
        _write_result(res_path, False, task_com.MSG_COM_UNAVAILABLE)
        return 0
    except ValueError:
        # A validator refusal — the payload asked for something the app never asks for.
        _write_result(res_path, False, _MSG_REQUEST_INVALID)
        return 0

    _write_result(res_path, True, check=check)
    return 0


def _schedule_check(payload: dict[str, object]) -> dict[str, object] | None:
    """Prove the nightly can start, when the parent asked (DSYNC-33). ``None`` = not asked.

    Runs only AFTER ``_do_provision`` returned — i.e. the nightly is registered — and inside
    this same elevated process, so the admin answers ONE UAC prompt for both. The check is
    advisory and total (``schedule_check.run_for_payload`` never raises): whatever it finds,
    the op still reports ``ok`` and the nightly stays registered.
    """
    from src.scheduler import schedule_check

    if not schedule_check.requested(payload):
        return None
    return schedule_check.run_for_payload(payload).to_wire()


def _do_register(payload: dict[str, object]) -> None:
    """Register the nightly in the privileged half: :func:`register_params`, then the ONE registration call."""
    from src.scheduler import task_com

    task_com.register_task_definition(register_params(payload))


def register_params(payload: dict[str, object]) -> task_com.RegisterParams:
    """Re-validate a register payload BY KIND and build the command (``ValueError`` on any refusal).

    Split out of :func:`_do_register` (DSYNC-116) so ``--migrate --apply``'s op can validate BOTH registrations it may
    make (the new nightly, and the old one it puts back if that fails) before it changes anything, through the same
    validators every elevated registration uses.

    **The account validator is chosen by the declared kind, and that is the make-or-break
    line of plan 0049 S-3.** Until then this called ``validate_run_as_user`` unconditionally
    — a deliberate fail-closed floor, and correct for the two kinds that existed. But that
    validator's charset has no ``$`` in it, and EVERY unattended registration comes through
    this function (a non-elevated ``register_task`` self-elevates for both unattended kinds),
    so a managed service account would have been refused here no matter what the rest of the
    engine could express. One validator per kind is what makes the third kind reachable —
    and it narrows the floor rather than widening it: a password-logon account still cannot
    carry a ``$``, and a service account still cannot be anything but ``DOMAIN\\name$``.

    An unknown ``kind`` string raises ``ValueError`` from the enum call itself, which
    ``_apply`` maps to ``_MSG_REQUEST_INVALID`` — a request naming a principal shape this
    build does not have is refused, never coerced into one it does.
    """
    from src.scheduler import task_com  # noqa: F811 - the runtime import; the module-level one is TYPE_CHECKING only
    from src.utils.validators import validate_run_time, validate_task_name

    if not set(payload) >= _REQUIRED_REGISTER_FIELDS:
        raise ValueError("missing fields")
    wake_to_run = _strict_bool(payload["wake_to_run"])
    task_name = validate_task_name(str(payload["task_name"]))
    run_time = str(payload["run_time"])
    validate_run_time(run_time)
    password = payload.get("password") or None  # ONE spelling of "no password" (0046 A1)
    kind = task_com.PrincipalKind(str(payload["kind"]))
    # UNCONDITIONAL, and now kind-aware: this module promises to re-validate every input in
    # the privileged half, and ``user`` is the field that names the principal an elevated
    # registration creates. It used to be validated only when a password was present — the
    # one input whose absence is exactly the case worth refusing.
    user = task_com.validate_principal_account(kind, str(payload["user"]))
    exe = str(payload["exe"])
    _refuse_per_user_install(kind, user, exe)

    return task_com.RegisterParams(
        task_name=task_name,
        exe=exe,
        arguments=str(payload["arguments"]),
        working_dir=str(payload["working_dir"]),
        run_time=run_time,
        user=user,
        password=str(password) if password is not None else None,
        kind=kind,
        run_highest=bool(payload["run_highest"]),
        wake_to_run=wake_to_run,
    )


def _strict_bool(value: object) -> bool:
    """A payload flag that must be a JSON boolean — anything else is refused (DSYNC-37).

    ``bool(value)`` would read the string ``"false"`` as True and wake a computer the admin
    asked us not to. The parent always writes a real boolean, so a non-boolean here is a
    request this build did not write; the ``ValueError`` maps to ``_MSG_REQUEST_INVALID``.
    """
    if not isinstance(value, bool):
        raise ValueError("not a boolean")
    return value


def _do_delete(payload: dict[str, object]) -> None:
    from src.scheduler import task_com
    from src.utils.validators import validate_task_name

    task_com.delete_task_by_name(validate_task_name(str(payload.get("task_name", ""))))


# --------------------------------------------------------------------------- #
# Machine scope (plan 0049 S-1b-i). NOTHING in the app reaches these yet —      #
# Schedule-time dispatch is S-2. The engine is in src/scheduler/provisioning.py.#
# --------------------------------------------------------------------------- #


def _refuse_per_user_install(kind: object, user: str, exe: str) -> None:
    """Plan 0055 G0, the privileged half's own floor: a per-user installed copy cannot run as another account.

    Raises ``ValueError`` (``_apply`` maps it to ``_MSG_REQUEST_INVALID``); the parent's own checks refuse first.
    The child runs as the same user as the parent (a different consenting administrator is refused earlier, at
    the request read or the DPAPI unprotect), so ``current_run_as_user`` is the parent's account. Imported lazily:
    ``windows`` imports this module at module level.
    """
    from src.scheduler import task_com
    from src.scheduler.windows import current_run_as_user

    foreign = (
        kind is task_com.PrincipalKind.MANAGED_SERVICE_ACCOUNT or user.casefold() != current_run_as_user().casefold()
    )
    if foreign and task_com.runs_from_per_user_install(exe):
        raise ValueError("a per-user installed copy cannot run as another account")


def _do_provision(payload: dict[str, object]) -> None:
    """Provision this computer for a shared profile, then register the nightly into it.

    The registration is handed to ``apply_provision`` as a callable rather than repeated
    there, so the task is created by the SAME re-validating ``_do_register`` the plain
    ``register`` op uses — the structural single source, and the reason a bad password
    still surfaces as its ``task_com`` canonical instead of a provisioning step id.

    **The task fields are validated BEFORE the sequence starts, not only inside that
    callable.** Registration is step 8, i.e. AFTER the HKLM commit — so a payload carrying
    (say) a malformed ``run_time`` would otherwise permanently switch the install to
    machine scope and only then refuse, leaving a provisioned computer with no nightly.
    Validating here keeps the whole operation a no-op for a malformed request. The
    callable still re-validates: this is a pre-flight, not a replacement for the floor.

    ``kind`` joins that pre-flight for exactly the same reason (plan 0049 S-3). It is also
    read much earlier than step 8 — ``apply_provision`` needs it to decide which validator
    the principal's name goes through before it grants that principal anything on disk — so
    an unrecognised value must refuse here, not halfway through a DACL.
    """
    from src.scheduler import provisioning, task_com
    from src.utils.validators import validate_run_time, validate_task_name

    if not set(payload) >= _REQUIRED_PROVISION_FIELDS:
        raise ValueError("missing fields")
    validate_task_name(str(payload["task_name"]))
    validate_run_time(str(payload["run_time"]))
    kind = task_com.PrincipalKind(str(payload["kind"]))  # an unknown kind refuses the whole op
    _strict_bool(payload["wake_to_run"])  # DSYNC-37: refused BEFORE the HKLM commit, like run_time
    # Plan 0055 G0: refused BEFORE the HKLM commit too - registration is step 8, after the switch, the
    # DACLs and the sealed secret, so a refusal there would leave a provisioned computer with no nightly.
    _refuse_per_user_install(kind, str(payload["user"]), str(payload["exe"]))
    provisioning.apply_provision(
        payload, register=lambda: _do_register({**payload, "op": "register"}), update_mode=None
    )


#: Plan 0055 M2: what a ``machine_setup`` request must carry.
_REQUIRED_MACHINE_SETUP_FIELDS = frozenset({"op", "source_data_dir", "user", "update_mode"})


def _do_machine_setup(payload: dict[str, object]) -> None:
    """Provision the shared profile with no nightly task, and write the update mode (plan 0055 M2).

    Validated BEFORE the sequence starts, as ``_do_provision`` is: an unknown update mode or a named principal must
    refuse the whole op, never switch the computer to machine scope and then refuse.
    """
    from src.scheduler import provisioning
    from src.update import policy

    if not set(payload) >= _REQUIRED_MACHINE_SETUP_FIELDS:
        raise ValueError("missing fields")
    policy.parse_update_mode(payload["update_mode"])
    if str(payload["user"]).strip():
        raise ValueError("machine setup grants no other account")
    provisioning.apply_machine_setup(payload)


def _do_set_update_mode(payload: dict[str, object]) -> None:
    """Change ONLY the machine update mode (plan 0055 U1b); nothing else is touched."""
    from src.scheduler import provisioning

    if set(payload) != {"op", "update_mode"}:
        raise ValueError("unexpected fields")
    provisioning.apply_update_mode(payload)


def _do_grant_current_user(payload: dict[str, object]) -> None:
    from src.scheduler import provisioning

    provisioning.apply_grant_current_user(payload)


def _do_prune_principal(payload: dict[str, object]) -> None:
    from src.scheduler import provisioning
    from src.utils.validators import validate_task_name

    validate_task_name(str(payload.get("task_name", "")))
    provisioning.apply_prune_principal(payload)
