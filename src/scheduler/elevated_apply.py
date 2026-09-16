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
touches exactly two files — the DPAPI request it reads and the result it writes — and
diagnostics ride the result file's message, never a log sink it would have to configure.

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
* success → ``{ok: True}``.

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

# The request file is DPAPI-sealed by the parent and small; anything larger is not ours.
_MAX_REQUEST_BYTES = 64 * 1024

# The cross-SID sentinel — MUST match what src/scheduler/windows.py maps to
# _MSG_DIFFERENT_ACCOUNT (the contract the retired PS bootstrap established).
DIFFERENT_ACCOUNT_SENTINEL = "DSYNC_DIFFERENT_ACCOUNT"

_REQUIRED_REGISTER_FIELDS = frozenset(
    {"op", "task_name", "exe", "arguments", "working_dir", "run_time", "user", "run_highest"}
)

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


def _write_result(res_path: Path, ok: bool, message: str = "") -> None:
    """Atomic plaintext result — temp + ``os.replace`` so the parent never sees a partial.

    The result carries NO secret by design (the parent's ``read_result`` cap and the D5
    handshake both assume that), so plain UTF-8 JSON is correct here.
    """
    payload = json.dumps({"ok": ok, "message": message})
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
    from src.scheduler import elevation, task_com

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
    try:
        if op == "register":
            _do_register(payload)
        elif op == "delete":
            _do_delete(payload)
        else:
            _write_result(res_path, False, _MSG_REQUEST_UNREADABLE)
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

    _write_result(res_path, True)
    return 0


def _do_register(payload: dict[str, object]) -> None:
    from src.scheduler import task_com
    from src.utils.validators import validate_run_as_user, validate_run_time, validate_task_name

    if not set(payload) >= _REQUIRED_REGISTER_FIELDS:
        raise ValueError("missing fields")
    task_name = validate_task_name(str(payload["task_name"]))
    run_time = str(payload["run_time"])
    validate_run_time(run_time)
    password = payload.get("password") or None  # ONE spelling of "no password" (0046 A1)
    # UNCONDITIONAL: this module promises to re-validate every input in the privileged
    # half, and ``user`` is the field that names the principal an elevated registration
    # creates. It used to be validated only when a password was present — the one input
    # whose absence is exactly the case worth refusing. Nothing the parent sends reaches
    # here unvalidated, so this is a fail-closed floor, not a second opinion.
    user = validate_run_as_user(str(payload["user"]))

    task_com.register_task_definition(
        task_com.RegisterParams(
            task_name=task_name,
            exe=str(payload["exe"]),
            arguments=str(payload["arguments"]),
            working_dir=str(payload["working_dir"]),
            run_time=run_time,
            user=user,
            password=str(password) if password is not None else None,
            run_highest=bool(payload["run_highest"]),
        )
    )


def _do_delete(payload: dict[str, object]) -> None:
    from src.scheduler import task_com
    from src.utils.validators import validate_task_name

    task_com.delete_task_by_name(validate_task_name(str(payload.get("task_name", ""))))
