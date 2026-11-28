"""Turning this computer into a machine-scoped install — the elevated engine (0049 S-1b-i).

Three operations run behind ONE UAC prompt, dispatched by
:mod:`src.scheduler.elevated_apply`. They landed INERT (S-1b-i) so the riskiest code was
tested before any surface could reach it; ``provision`` has been reached from Setup's Schedule
press since S-2b and ``grant_current_user`` from the launcher's grant window, and since DSYNC-33
``prune_principal`` is reached from Setup's Remove for a managed-service-account principal
(``provision_session.request_removal_with_revoke``).

* ``provision`` — create ``C:\\ProgramData\\DistrictSync``, migrate the admin's per-user
  profile into it, seal the delivery secret, VERIFY, then commit the HKLM switch.
* ``grant_current_user`` — additive aces for a second administrator: the setup admin's rights on
  every protected folder (:data:`PROTECTED_SUBTREES`, DSYNC-117).
* ``prune_principal`` — remove a retired task principal's aces, after a confirmed delete.

**Every grant to a task principal is RECORDED (DSYNC-115)** — its SID, in ``HKLM\\SOFTWARE\\DistrictSync`` →
``GrantedPrincipalSids``, before the grant — and a Remove (``prune_principal``) or a principal switch
(:func:`prune_retired_grants`) revokes only what is recorded, only where the ace is exactly the shape DistrictSync
grants. Since DSYNC-115 Setup's Remove and ``--schedule-remove`` produce ``prune_principal`` for every principal a
registration granted access to (a password account, local or domain, and a managed service account).

**The directory is created WITH its DACL, atomically** (S-1b-i.1). The plan's original
sequence (create → ``icacls /inheritance:r`` → verify empty → apply DACL → ``/setowner``)
cannot work, and both reasons were MEASURED on this machine: ``/inheritance:r`` leaves a
**zero-ACE** DACL, which denies ``FILE_LIST_DIRECTORY`` to everyone including the owner —
``Path.iterdir()`` raises ``PermissionError``, so the "verify empty" check the whole
anti-plant design rested on can never observe a file — and ``shutil.rmtree`` on that same
directory raises too, so the rollback could not remove what it had created. Instead the D2
descriptor is built as SDDL, converted with
``ConvertStringSecurityDescriptorToSecurityDescriptorW`` and handed to ``CreateDirectoryW``
through ``SECURITY_ATTRIBUTES``: owner and protected DACL exist **at creation**, there is no
window to plant into, and ``Administrators:(OI)(CI)F`` means the elevated child can always
remove its own rollback.

``CreateDirectoryW``'s ``ERROR_ALREADY_EXISTS`` is therefore **the atomic gate**: anything
already at the path routes to a refusal, never to an adopt-and-fix path. "Never adopt a
directory the app did not create" is structural here, not a promise.

**Every principal in the SDDL is a SID string** — ``SY``/``BA`` well-known abbreviations or
an explicit ``S-1-…`` resolved through ``LookupAccountNameW``. ``BUILTIN\\Administrators``
is localised; an ``icacls /grant Administrators:F`` fails outright on a German Windows and a
name comparison passes on a box that renamed a group to match.

**Where ``icacls`` is still used** — the ``grant``/``prune`` ops and the resume path, which
modify an EXISTING DACL — **every non-zero exit is fatal.** That is the deliberate inverse
of :func:`src.scheduler.elevation._set_owner_only_dacl`, which logs a warning and continues
because DPAPI CurrentUser is its real confidentiality boundary. Here the DACL **is** the
boundary (D3: a LocalMachine-sealed secret lives in this directory), so a permission change
that did not happen may not be reported as one that did.

**The result carries a fixed vocabulary only** — a :class:`ProvisionStep` id plus, where one
exists, the ``icacls`` exit code. Never stderr, never a resolved path, never either secret
(the SFTP delivery password or the task password). There is no logger in this module: like
``elevated_apply``, an elevated child performing best-effort filesystem work in
user-writable directories is an EoP surface, and diagnostics ride the result file.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess  # nosec B404 - icacls (DACL edits) is a trusted, absolute-path System32 binary
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from src.config.app_config import decode_settings_bytes
from src.scheduler import task_com
from src.scheduler.windows import current_run_as_user
from src.update import policy
from src.utils import paths
from src.utils.helpers import subprocess_no_window_flags, system_binary

# --------------------------------------------------------------------------- #
# The D2 descriptor.                                                          #
# --------------------------------------------------------------------------- #

# SDDL right abbreviations / masks. The two hex masks are icacls' OWN spellings of
# ``Modify`` and ``Read & execute``, verified by reading a created directory back through
# ``icacls`` (the Windows-only test in tests/test_provisioning.py is that read-back).
FULL_CONTROL = "FA"
MODIFY = "0x1301bf"
READ_EXECUTE = "0x1200a9"

# Well-known SID abbreviations, never localised names.
SID_SYSTEM = "SY"
SID_ADMINISTRATORS = "BA"

# The owner a provisioned profile must have for ``paths._assert_machine_dir_trusted`` to
# accept it. Passed explicitly at every call site rather than defaulted: the owner is the
# fact that separates a provisioned profile from a planted one.
OWNER_ADMINISTRATORS = SID_ADMINISTRATORS

_SDDL_REVISION_1 = 1
_ERROR_ALREADY_EXISTS = 183
_ERROR_INSUFFICIENT_BUFFER = 122

# An ``icacls`` we could not launch at all. Distinct from every real exit code, and still
# NON-ZERO, so the "every non-zero exit is fatal" rule stays total.
ICACLS_NOT_LAUNCHED = -1


class ProvisionStep(StrEnum):
    """The BOUNDED result vocabulary. A step id is the only thing a failure may name.

    A free-text reason would inevitably carry a resolved path (which embeds an account
    name), an ``icacls`` stderr line, or — once the payload holds two passwords — a value
    that must never leave this process.
    """

    OVERRIDE = "override"  # DISTRICTSYNC_DATA_DIR is in play — refuse, in both halves
    SOURCE = "source"  # the payload named a per-user profile that is not ours
    PRINCIPAL = "principal"  # the account could not be validated, resolved, or may not be touched
    PRE_EXISTING = "pre_existing"  # something is already there and it is not a profile we may resume
    CREATE = "create"  # CreateDirectoryW failed for any other reason
    MIGRATE = "migrate"  # the per-user profile could not be copied intact
    # nosec B105 - a STEP ID. Bandit keys on the member's name; the value is the word that
    # appears in an admin-facing message, and no credential is involved (the whole point of
    # this vocabulary is that a secret can never reach the result).
    SECRET = "secret"  # nosec B105 - the delivery secret could not be sealed into the profile
    VERIFY = "verify"  # the app's OWN predicate rejects what we just built
    COMMIT = "commit"  # the HKLM switch could not be written
    ROLLBACK = "rollback"  # a pre-commit failure left a directory we could not remove
    GRANT = "grant"  # an additive ace (root or a protected folder under it) failed
    DELETE = "delete"  # the task's removal was not confirmed, so nothing was pruned
    PRUNE = "prune"  # the retired principal's aces could not be removed


class ProvisionRefused(RuntimeError):
    """A provisioning operation refused or failed. Carries a step id and nothing else.

    ``icacls_exit`` is included where one exists because it is the single most useful
    diagnostic an admin can relay and it is a small integer — unlike stderr, which quotes
    paths and account names.
    """

    def __init__(
        self,
        step: ProvisionStep,
        *,
        icacls_exit: int | None = None,
        rollback_failed: bool = False,
    ) -> None:
        self.step = step
        self.icacls_exit = icacls_exit
        self.rollback_failed = rollback_failed
        message = f"DistrictSync could not set up the shared settings folder (step: {step.value})."
        if icacls_exit is not None:
            message += f" [icacls exit {icacls_exit}]"
        if rollback_failed:
            message += f" The folder it created could not be removed (step: {ProvisionStep.ROLLBACK.value})."
        self.message = message
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Win32 seams. Each is one syscall behind one name, so the decision table above #
# them runs on every OS and the seams get real-API tests where the API exists.  #
# --------------------------------------------------------------------------- #


class _SecurityAttributes(ctypes.Structure):
    """``SECURITY_ATTRIBUTES`` — plain ctypes types so the module imports on any OS."""

    _fields_ = (
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    )


def _create_directory_with_sddl(path: Path, sddl: str) -> None:  # pragma: no cover - Windows-only ctypes
    """Create ``path`` with ``sddl`` as its security descriptor, in ONE call.

    Raises ``FileExistsError`` for ``ERROR_ALREADY_EXISTS`` — the atomic never-adopt gate —
    and ``OSError`` for anything else.

    ``GetLastError`` is read ONLY after a failed call. ``CreateDirectoryW`` does not reset
    it on success, so a success can otherwise "report" the previous call's error (observed
    while probing this mechanism: a successful ``runs/`` creation carried the 183 left by
    the deliberate duplicate-create before it).
    """
    if sys.platform != "win32":
        raise OSError("Machine-scope provisioning is Windows-only.")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_bool
    kernel32.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SecurityAttributes)]
    kernel32.CreateDirectoryW.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    descriptor = ctypes.c_void_p()
    size = ctypes.c_uint32()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)
    ):
        raise OSError(f"The security descriptor could not be built (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
    try:
        attributes = _SecurityAttributes()
        attributes.nLength = ctypes.sizeof(_SecurityAttributes)
        attributes.lpSecurityDescriptor = descriptor
        attributes.bInheritHandle = False
        if not kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            if error == _ERROR_ALREADY_EXISTS:
                raise FileExistsError(error, "The directory already exists.", str(path))
            raise OSError(error, "CreateDirectoryW failed.", str(path))
    finally:
        kernel32.LocalFree(descriptor)


#: ``ERROR_NONE_MAPPED`` — ``LookupAccountNameW`` reached an answer and the answer was "no
#: such account". Every OTHER failure (no logon servers, a broken trust, an RPC timeout) means
#: the question could not be asked, which is why :class:`AccountLookupError` carries the code.
ERROR_NONE_MAPPED = 1332


class AccountLookupError(LookupError):
    """``LookupAccountNameW`` could not resolve an account. Carries the Win32 error and nothing else.

    A ``LookupError`` subclass so every existing ``except LookupError`` (``_sid_for``) keeps
    refusing exactly as before. ``winerror`` is ``None`` off Windows. It never carries the
    account name: the pre-UAC refusal it feeds (DSYNC-33) is logged, and a log line names no
    account.
    """

    def __init__(self, winerror: int | None) -> None:
        super().__init__(f"Windows could not resolve the account (error {winerror}).")
        self.winerror = winerror


def account_sid(name: str) -> str:
    """Resolve an account to its SID string — the public face of :func:`_account_sid`.

    Used UNELEVATED by ``provision_session.request_provision`` to refuse an unresolvable
    managed service account BEFORE the UAC prompt (DSYNC-33), and by the elevated schedule
    check for its rights lookup. One wrapper, so the two halves cannot resolve a name two
    different ways. Raises :class:`AccountLookupError`.
    """
    return _account_sid(name)


def _account_sid(name: str) -> str:  # pragma: no cover - Windows-only ctypes
    """Resolve ``DOMAIN\\user`` (or a bare name) to its SID string.

    Raises :class:`AccountLookupError` (a ``LookupError``) when Windows cannot resolve it —
    which is a REFUSAL, not a fallback: a DACL built around an unresolvable principal would
    either fail to apply or grant the wrong trustee.
    """
    if sys.platform != "win32":
        raise AccountLookupError(None)

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    advapi32.LookupAccountNameW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.LookupAccountNameW.restype = ctypes.c_bool
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    sid_size = ctypes.c_uint32(0)
    domain_size = ctypes.c_uint32(0)
    use = ctypes.c_uint32(0)
    # First pass: sizes only. This call is EXPECTED to fail.
    advapi32.LookupAccountNameW(
        None, name, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size), ctypes.byref(use)
    )
    first_error = ctypes.get_last_error()  # type: ignore[attr-defined]
    if first_error != _ERROR_INSUFFICIENT_BUFFER or sid_size.value == 0:
        raise AccountLookupError(first_error)

    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not advapi32.LookupAccountNameW(
        None, name, sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size), ctypes.byref(use)
    ):
        raise AccountLookupError(ctypes.get_last_error())  # type: ignore[attr-defined]

    text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.cast(sid, ctypes.c_void_p), ctypes.byref(text)):
        raise LookupError(f"The account's SID could not be read (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
    try:
        return str(text.value)
    finally:
        kernel32.LocalFree(text)


#: The bound on ONE ``icacls`` call. Named (second review, LOW 3) because the elevated ``--migrate --apply`` op makes up
#: to four of them and the parent's wait (``migration_move.CHILD_BUDGET_S``) is sized from it.
ICACLS_TIMEOUT_S = 60


def _run_icacls(args: list[str]) -> int:
    """Run ``icacls`` with ``args`` and return its exit code. TOTAL — never raises.

    ``icacls.exe`` resolves through :func:`~src.utils.helpers.system_binary` (an absolute
    System32 path), because ``CreateProcess`` probes the calling executable's directory and
    the current directory BEFORE System32 — and this process is elevated.

    A launch failure returns :data:`ICACLS_NOT_LAUNCHED` rather than raising, so every
    caller's "non-zero is fatal" branch covers it too.
    """
    try:
        completed = subprocess.run(  # nosec B603 - absolute System32 binary, our own paths, shell=False
            [system_binary("icacls.exe"), *args],
            capture_output=True,
            text=True,
            timeout=ICACLS_TIMEOUT_S,
            check=False,
            creationflags=subprocess_no_window_flags(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ICACLS_NOT_LAUNCHED
    return completed.returncode


def _commit_machine_switch(*, update_mode: str | None) -> None:  # pragma: no cover - registry write
    """Write ``HKLM\\SOFTWARE\\DistrictSync`` — the COMMIT POINT of the whole sequence.

    The access mask is :data:`src.utils.paths.MACHINE_SCOPE_KEY_ACCESS` **imported, never
    re-spelled**, plus ``KEY_WRITE``. That constant carries ``KEY_WOW64_64KEY``: a writer in
    the redirected 32-bit view would commit ``WOW6432Node\\DistrictSync``, report success,
    and leave the app per-user with the config and the secret already copied — while the
    reader looked at the 64-bit key and saw nothing.

    ``MachineScope`` is written LAST. The two display values mean nothing on their own, so
    a failure between them and the switch leaves the install un-provisioned, which is the
    resume case rather than a half-committed one. ``update_mode`` (plan 0055 M2, machine setup) is
    written with them, BEFORE the switch, for the same reason; ``None`` (the schedule-time provision)
    leaves any existing value as it is. It is REQUIRED and undefaulted so no caller forgets to decide.

    The ``sys.platform`` guard is what lets a type-checker running on Linux skip this body —
    ``winreg``'s typeshed stubs mark every attribute Windows-only (the trap that reddened
    PR #129 at 42s on CI's ubuntu leg).
    """
    if sys.platform != "win32":
        raise OSError("The machine-scope switch is Windows-only.")

    import winreg

    access = paths.MACHINE_SCOPE_KEY_ACCESS | winreg.KEY_WRITE
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, access) as key:
        winreg.SetValueEx(
            key, "ProvisionedAt", 0, winreg.REG_SZ, datetime.now().astimezone().isoformat(timespec="seconds")
        )
        # Plan 0055 N1: no account name in the registry. An existing ProvisionedBy (a 0049 install) is deleted.
        with contextlib.suppress(FileNotFoundError):
            winreg.DeleteValue(key, "ProvisionedBy")
        if update_mode is not None:
            winreg.SetValueEx(
                key, policy.MACHINE_SETTING_VALUE_NAME, 0, winreg.REG_SZ, policy.parse_update_mode(update_mode)
            )
        winreg.SetValueEx(key, paths.MACHINE_SCOPE_VALUE_NAME, 0, winreg.REG_DWORD, 1)


#: DSYNC-115: the grant record — ``HKLM\SOFTWARE\DistrictSync`` → ``GrantedPrincipalSids`` (REG_MULTI_SZ), beside the
#: switch. The SID of every task principal DistrictSync granted access to the shared folder, and nothing else: no
#: account name (plan 0055 N1), and only an administrator can write it, so no standard user can name an account into a
#: prune. Absent = an install provisioned before the record existed (see :func:`apply_prune_principal`).
GRANT_LEDGER_VALUE_NAME = "GrantedPrincipalSids"
_REG_VALUE_NOT_FOUND = 2  # ERROR_FILE_NOT_FOUND: the key or the value is not there


def _read_grant_ledger_value() -> list[str] | None:  # pragma: no cover - Windows-only registry read
    """The raw record: ``None`` when the key or the value is absent, else its strings. Raises ``OSError`` otherwise."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, paths.MACHINE_SCOPE_KEY_ACCESS
        ) as key:
            value, kind = winreg.QueryValueEx(key, GRANT_LEDGER_VALUE_NAME)
    except FileNotFoundError:
        return None
    if kind != winreg.REG_MULTI_SZ or not isinstance(value, list):
        raise OSError("The grant record is not a list of SIDs.")
    return [str(item) for item in value]


def _write_grant_ledger_value(sids: list[str]) -> None:  # pragma: no cover - Windows-only registry write
    """Replace the record with ``sids`` in the 64-bit view of ``HKLM\\SOFTWARE\\DistrictSync`` (the switch's key)."""
    if sys.platform != "win32":
        raise OSError("The grant record is Windows-only.")
    import winreg

    access = paths.MACHINE_SCOPE_KEY_ACCESS | winreg.KEY_WRITE
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, access) as key:
        winreg.SetValueEx(key, GRANT_LEDGER_VALUE_NAME, 0, winreg.REG_MULTI_SZ, list(sids))


_SID_RE = re.compile(r"S-1-\d+(-\d+)+", re.ASCII)  # ASCII digits only


def read_grant_ledger() -> tuple[str, ...] | None:
    """The SIDs DistrictSync recorded granting, or ``None`` for an install from before the record (DSYNC-115).

    ``()`` (nothing granted) and ``None`` (never recorded) are different facts and stay different. An entry that is not
    a SID string is dropped rather than handed to ``icacls`` as a trustee. Raises ``OSError`` when the value cannot be
    read; every caller refuses (or skips) on that rather than guessing.
    """
    raw = _read_grant_ledger_value()
    if raw is None:
        return None
    sids: list[str] = []
    for entry in raw:
        sid = entry.strip().upper()
        if _SID_RE.fullmatch(sid) and sid not in sids:
            sids.append(sid)
    return tuple(sids)


def _record_grant(sid: str) -> None:
    """Add ``sid`` to the record BEFORE it is granted anything. Raises :class:`ProvisionRefused` (``PRINCIPAL``).

    Recorded first so no failure between the two can leave a grant DistrictSync does not know it made; the opposite
    leftover (a recorded SID with no ACE, after a refusal) is harmless: a prune finds nothing for it and drops it.
    """
    try:
        recorded = read_grant_ledger() or ()
        if sid not in recorded:
            _write_grant_ledger_value([*recorded, sid])
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc


def _dacl_entries(path: Path) -> tuple[tuple[int, int, int, str], ...]:
    """``(ace type, flags, mask, SID)`` for ``path``'s DACL — :func:`paths.read_dacl_entries` behind one seam."""
    return paths.read_dacl_entries(path)


_ACCESS_ALLOWED = 0
#: The DENY family (plain, object, callback, callback-object). ``icacls /remove:g`` never touches these and a deny ace
#: is a hardening measure, so it neither blocks nor counts toward a revoke. Every OTHER non-plain type (an object,
#: callback or conditional ALLOW) is something DistrictSync never writes, so it keeps the account (review I1).
_DENY_ACE_TYPES = frozenset({1, 6, 10, 12})
_INHERIT_OI_CI = 0x3  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE — icacls' (OI)(CI), and nothing else
_READ_EXECUTE_MASK = int(READ_EXECUTE, 16)
_MODIFY_MASK = int(MODIFY, 16)
#: ``icacls`` right -> the access mask a DACL read returns for it.
_PRINCIPAL_ACCESS_MASKS = {"RX": _READ_EXECUTE_MASK, "M": _MODIFY_MASK}


def _granted_targets(root: Path) -> tuple[tuple[Path, int], ...]:
    """Where a task principal is granted, and with which mask: RX on the root, Modify on ``runs`` (D2).

    Built from :data:`PROTECTED_SUBTREES`' ``principal_rights`` (DSYNC-117 F6), the same column the
    descriptors and :func:`_ensure_runs_and_principal`'s grant read, so the exact shape the revoke accepts
    is the shape that was granted. A second administrator's grant (``setup_rights``: Modify on the root)
    is never this shape, so the revoke keeps it.
    """
    return tuple(
        (root / subtree.relative, _PRINCIPAL_ACCESS_MASKS[subtree.principal_rights]) for subtree in PROTECTED_SUBTREES
    )


def _revoke(root: Path, sids: tuple[str, ...], *, protected: frozenset[str]) -> tuple[set[str], set[str]]:
    """Remove each SID's grant ONLY where every ALLOW ace it holds is exactly the one DistrictSync makes.

    Per SID, all or nothing: if it holds any other ALLOW ace on the root or on ``runs`` (full control, an extra
    non-inheritable, inherited or inherit-only ace, the setup administrator's Modify on the root, an object, callback
    or conditional allow ace — including one the DACL reader cannot attribute to a SID), nothing of that SID's is
    touched; deny aces are ignored either way — the ace
    is not DistrictSync's to remove and ``icacls /remove:g`` cannot remove one ace of several. ``protected`` SIDs
    (plus SYSTEM and Administrators) are never touched at all. The trustee is passed as ``*S-1-…``: no name lookup
    (a retired managed service account may no longer resolve) and no account name in the argument list.

    Returns ``(revoked, kept)``. A SID with nothing left is reported revoked. Raises :class:`ProvisionRefused`
    (``PRUNE``) when a DACL cannot be read or ``icacls`` exits non-zero.
    """
    targets = tuple((path, mask) for path, mask in _granted_targets(root) if path.exists())
    try:
        dacls = {path: _dacl_entries(path) for path, _mask in targets}
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.PRUNE) from exc

    revoked: set[str] = set()
    kept: set[str] = set()
    for sid in sids:
        if sid in protected or sid in _UNPRUNABLE_SIDS:
            kept.add(sid)
            continue
        to_remove: list[Path] = []
        ours = True
        for path, mask in targets:
            entries = dacls[path]
            if any(
                kind != _ACCESS_ALLOWED and kind not in _DENY_ACE_TYPES and who.upper() in (sid, "")
                for kind, _flags, _mask, who in entries
            ):
                ours = False
                continue
            held = [
                (flags, held_mask)
                for kind, flags, held_mask, who in entries
                if kind == _ACCESS_ALLOWED and who.upper() == sid
            ]
            if not held:
                continue
            if all(entry == (_INHERIT_OI_CI, mask) for entry in held):
                to_remove.append(path)
            else:
                ours = False
        if not ours:
            kept.add(sid)
            continue
        for path in to_remove:
            exit_code = _run_icacls([str(path), "/remove:g", f"*{sid}"])
            if exit_code != 0:
                raise ProvisionRefused(ProvisionStep.PRUNE, icacls_exit=exit_code)
        revoked.add(sid)
    return revoked, kept


def _delete_task_confirmed_missing(task_name: str) -> bool:
    """Delete the task and return True ONLY when a read-back proves it is gone.

    An unconfirmed delete must leave the principal's aces alone: pruning a LIVE task's
    principal makes it hit ``MachineScopeRefused(INACCESSIBLE)`` every night and exit 1 with
    no surface anywhere — exactly the silent-nightly failure this plan exists to remove.
    """
    from src.scheduler import task_com

    try:
        task_com.delete_task_by_name(task_name)
    except task_com.TaskComError as exc:
        if exc.scode != task_com.HR_NOT_FOUND:
            return False  # an already-absent task is fine; anything else is not
    except ImportError:
        return False

    try:
        task_com.read_task(task_name)
    except task_com.TaskComError as exc:
        return exc.scode == task_com.HR_NOT_FOUND
    except ImportError:
        return False
    return False  # it read back — it is still there


def _machine_secret_store() -> object:
    """The machine-scoped delivery-secret store (lazy: ``src/sftp`` pulls in keyring)."""
    from src.sftp.secret_store import MachineSecretStore

    return MachineSecretStore()


# --------------------------------------------------------------------------- #
# The descriptors.                                                            #
# --------------------------------------------------------------------------- #


def _ace(mask: str, sid: str) -> str:
    """One inheritable ALLOW ace. ``OICI`` = object + container inherit (icacls' ``(OI)(CI)``)."""
    return f"(A;OICI;{mask};;;{sid})"


def _descriptor(*, owner_sid: str, aces: list[str]) -> str:
    """``O:…G:…D:P(…)`` — ``P`` is ``SE_DACL_PROTECTED``, i.e. inheritance off AT CREATION.

    ``paths._assert_machine_dir_trusted`` refuses a directory without that bit, so the
    protection is not decoration: it is the fact the app's own predicate checks.
    """
    return f"O:{owner_sid}G:{owner_sid}D:P" + "".join(aces)


@dataclass(frozen=True)
class ProtectedSubtree:
    """One folder of the shared profile created with a PROTECTED descriptor (inheritance off).

    Rights are ``icacls`` spellings (``M``/``RX``); :data:`_SDDL_MASKS` maps them for the
    descriptor. ``relative`` is the path under the profile root (``""`` = the root itself).
    """

    relative: str
    setup_rights: str  # the setup administrator, and every administrator granted later (DSYNC-117)
    principal_rights: str  # the nightly's task principal


#: THE table (DSYNC-117): every protected folder provisioning creates, and what each account gets
#: on it. The descriptors, the creation step and the second-administrator grant all read it, so a
#: new protected folder cannot be created without the grant reaching it. Before this table the
#: grant added one ace to the root only, and because ``runs/`` is protected, nothing granted on the
#: root ever reached it: the granted administrator could open the profile but not create its own
#: log (lab, MSI 0.57.1: ``[Errno 13]`` and PyInstaller's traceback dialog).
PROTECTED_SUBTREES: tuple[ProtectedSubtree, ...] = (
    # The principal READS the profile; it cannot alter the admin's trust-bearing state.
    ProtectedSubtree("", setup_rights="M", principal_rights="RX"),
    # The principal WRITES its log and the run store here, and nowhere else.
    ProtectedSubtree(paths.MACHINE_RUNS_SUBDIR, setup_rights="M", principal_rights="M"),
)

_SDDL_MASKS = {"M": MODIFY, "RX": READ_EXECUTE}


def _protected_subtree(relative: str) -> ProtectedSubtree:
    return next(subtree for subtree in PROTECTED_SUBTREES if subtree.relative == relative)


def _subtree_sddl(subtree: ProtectedSubtree, *, setup_sid: str, principal_sid: str, owner_sid: str) -> str:
    """SYSTEM F · Administrators F · setup user (the row's right) · principal (the row's right), protected.

    The setup user is granted **explicitly** because ``BUILTIN\\Administrators`` in a DACL
    grants nothing to a non-elevated process (the UAC filtered token) — the admin's own
    un-elevated session has to be able to write ``config.json``. When the principal IS the
    setup user its single ace stands alone — a second ace for the same SID would be redundant
    and confusing in a read-back.
    """
    aces = [
        _ace(FULL_CONTROL, SID_SYSTEM),
        _ace(FULL_CONTROL, SID_ADMINISTRATORS),
        _ace(_SDDL_MASKS[subtree.setup_rights], setup_sid),
    ]
    if principal_sid != setup_sid:
        aces.append(_ace(_SDDL_MASKS[subtree.principal_rights], principal_sid))
    return _descriptor(owner_sid=owner_sid, aces=aces)


def _machine_root_sddl(*, setup_sid: str, principal_sid: str, owner_sid: str) -> str:
    """The shared profile root (D2): SYSTEM F · Administrators F · setup user M · principal RX.

    The principal gets READ-EXECUTE here and Modify only on ``runs/``: the service account
    reads what it needs and writes only run artefacts. Rights come from :data:`PROTECTED_SUBTREES`.
    """
    return _subtree_sddl(_protected_subtree(""), setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=owner_sid)


def _runs_sddl(*, setup_sid: str, principal_sid: str, owner_sid: str) -> str:
    """``runs/`` — the same, except the principal gets Modify (its log + the run store).

    ``provision`` creates this directory: S-1a deliberately left ``user_log_file()``
    non-creating, because a ``runs/`` created later by an admin process would inherit the
    root's RX and silently break the principal's log.
    """
    return _subtree_sddl(
        _protected_subtree(paths.MACHINE_RUNS_SUBDIR),
        setup_sid=setup_sid,
        principal_sid=principal_sid,
        owner_sid=owner_sid,
    )


# --------------------------------------------------------------------------- #
# Migration (S-1b-i.4). Its own function; migrate_legacy_data_dir is untouched. #
# --------------------------------------------------------------------------- #

_MIGRATED_FILES = ("config.json", "known_hosts")
_MIGRATED_DIRS = ("mappings",)


def migrate_profile(source: Path, destination: Path) -> None:
    """Copy the admin's per-user profile into the shared one. Never deletes the source.

    Technique from :func:`src.utils.paths.migrate_legacy_data_dir`: stage into a temp dir,
    promote with ``os.replace``, retry ``PermissionError`` three times with backoff, remove
    staging on any failure.

    **Idempotent by SKIPPING, not by overwriting.** An artefact already present at the
    destination is left alone. That is what makes the resume path safe: a re-run after a
    kill (or a later Schedule on an already-provisioned computer) must never overwrite the
    live shared ``config.json`` with the stale per-user copy it was made from.

    Promotion is per-artefact, so a failure partway through can leave some promoted and
    some not. That state SELF-HEALS rather than wedging: the directory still passes the
    trust predicate, so the next attempt resumes and the skip rule fills exactly the gaps
    (a fresh provision rolls the whole directory back instead). Step 6 additionally
    refuses to commit while the run store is missing from where the reader looks.

    Per artefact:

    * ``config.json`` — **parsed, then written**, never byte-copied. A torn source must fail
      HERE, where the admin is watching a progress indicator, not at the next
      ``AppConfig.load()`` in a nightly that has no surface.
    * ``history.db`` — ``sqlite3.Connection.backup()``, then ``PRAGMA integrity_check`` AND a
      row-count comparison. A plain file copy would silently drop everything sitting in an
      uncheckpointed ``-wal``; the row count is taken **inside the same read transaction as
      the backup**, because a second query against a live source races a concurrent writer
      and would fail a perfectly good copy.
    * ``mappings/`` and ``known_hosts`` — plain copies. A self-service district's overlay
      (plan 0044) lives there and the nightly must find it.

    Raises:
        ProvisionRefused: with :attr:`ProvisionStep.MIGRATE`.
    """
    staging = destination / f".migrating-{secrets.token_hex(8)}"
    try:
        staging.mkdir(parents=True)
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.MIGRATE) from exc

    try:
        promotions: list[tuple[Path, Path]] = []

        for name in _MIGRATED_FILES:
            origin, target = source / name, destination / name
            if not origin.is_file() or target.exists():
                continue
            staged = staging / name
            if name == "config.json":
                staged.write_text(
                    json.dumps(json.loads(decode_settings_bytes(origin.read_bytes())), indent=2), encoding="utf-8"
                )
            else:
                shutil.copy2(origin, staged)
            promotions.append((staged, target))

        for name in _MIGRATED_DIRS:
            origin, target = source / name, destination / name
            if not origin.is_dir() or target.exists():
                continue
            staged = staging / name
            shutil.copytree(origin, staged, copy_function=shutil.copy2)
            promotions.append((staged, target))

        origin = source / paths.RUN_STORE_NAME
        target = paths.history_db_in(destination, machine_scope=True)
        if origin.is_file() and not target.exists():
            staged = staging / paths.RUN_STORE_NAME
            _copy_run_store(origin, staged)
            promotions.append((staged, target))

        for staged, target in promotions:
            _promote(staged, target)
    except ProvisionRefused:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, ValueError, sqlite3.Error, shutil.Error) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ProvisionRefused(ProvisionStep.MIGRATE) from exc
    shutil.rmtree(staging, ignore_errors=True)


_TABLE_NAMES_SQL = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"


def _row_census(connection: sqlite3.Connection) -> dict[str, int]:
    """``{table: row count}`` for every non-internal table. Schema-agnostic on purpose.

    Counting ``runs`` by name would put a FOURTH spelling of that table name outside
    ``src/history/store.py`` (which inlines it in its DDL, its INSERT and its SELECT), and a
    constant only this module used would not be a single source either. Censusing whatever
    the file actually contains is both looser coupling AND a stronger check — it covers
    ``meta``, and the ``runs`` index, without knowing the schema.
    """
    tables = [str(row[0]) for row in connection.execute(_TABLE_NAMES_SQL).fetchall()]
    census: dict[str, int] = {}
    for name in tables:
        # A table name cannot be a bound parameter in SQLite, so it has to be interpolated.
        # The value comes from ``sqlite_master`` — the database's OWN identifier list, never
        # a caller's string — and is re-quoted as an SQLite delimited identifier (a literal
        # ``"`` inside a name is escaped by doubling it), so a table created as
        # ``CREATE TABLE "a""b"`` counts correctly instead of producing broken SQL.
        quoted = '"' + name.replace('"', '""') + '"'
        census[name] = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]  # nosec B608
    return census


def _backup_into(live: sqlite3.Connection, copy: sqlite3.Connection) -> None:
    """The raw ``sqlite3`` backup call, behind one name.

    A seam for the same reason every Win32 call here has one: ``sqlite3.Connection`` is an
    immutable type, so the row-census check below cannot otherwise be shown to catch a
    short copy — and an unfalsifiable integrity check is not an integrity check.
    """
    live.backup(copy)


def _copy_run_store(origin: Path, staged: Path) -> None:
    """Back the run store up into ``staged`` and PROVE the copy before it is promoted."""
    live = sqlite3.connect(origin)
    try:
        # BEGIN + the first read opens a read transaction, so the census and the backup
        # describe ONE snapshot. A second census against a live source would race a
        # concurrent writer and fail a perfectly good copy. (MEASURED: ``backup()``
        # tolerates an open read transaction on the source connection, and carries
        # uncheckpointed ``-wal`` content that a file copy would silently drop.)
        live.execute("BEGIN")
        expected = _row_census(live)
        copy = sqlite3.connect(staged)
        try:
            _backup_into(live, copy)
        finally:
            copy.close()
        live.rollback()
    finally:
        live.close()

    verify = sqlite3.connect(staged)
    try:
        if verify.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ProvisionRefused(ProvisionStep.MIGRATE)
        if _row_census(verify) != expected:
            raise ProvisionRefused(ProvisionStep.MIGRATE)
    finally:
        verify.close()


def _promote(staged: Path, target: Path) -> None:
    """One atomic ``os.replace``, with the AV/indexer retry ``paths.py`` already uses."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            os.replace(staged, target)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.2 * (attempt + 1))


# --------------------------------------------------------------------------- #
# The parent half.                                                            #
# --------------------------------------------------------------------------- #


def build_provision_payload(
    *,
    task_name: str,
    exe: str,
    arguments: str,
    working_dir: str,
    run_time: str,
    user: str,
    kind: task_com.PrincipalKind,
    run_highest: bool,
    schedule_check: bool,
    wake_to_run: bool,
    password: str | None = None,
    sftp_host: str = "",
    sftp_username: str = "",
    sftp_password: str = "",
) -> dict[str, object]:
    """Build the ``provision`` request. Refuses under ``DISTRICTSYNC_DATA_DIR``.

    The refusal is in BOTH halves deliberately. The override is mandated for every local,
    CI and QA run, and a UAC-launched child gets an environment rebuilt from the consenting
    token — so a parent-only check would let the owner's own fresh-profile QA walk
    permanently switch their laptop to machine scope and copy their real ``config.json``,
    identity address included, into ``C:\\ProgramData``.

    ``source_data_dir`` is stamped here so the child can refuse a request that names a
    different profile from the one it resolves itself.

    ``kind`` is REQUIRED and undefaulted (plan 0049 S-3). The provision op registers the
    nightly as its last step, through the SAME ``elevated_apply._do_register`` the plain
    register op uses, so its payload must declare the principal the same way — and a
    default here would be a substituted security principal wearing a payload builder's
    clothes. It travels as the enum's stable string value: this dict is JSON, sealed and
    unsealed across a process boundary.

    ``schedule_check`` (DSYNC-33) is REQUIRED and undefaulted: it asks the child to PROVE,
    after registering, that Windows can start DistrictSync as the principal (the elevated
    half runs it only for a kind ``schedule_check.applies_to``). It is the caller's
    DECLARATION that the result will be read; a request without the key (an older parent)
    gets no check, never a guessed one.

    Raises:
        ProvisionRefused: with :attr:`ProvisionStep.OVERRIDE`.
    """
    _refuse_under_override()
    return {
        "op": "provision",
        "schedule_check": schedule_check,
        "source_data_dir": str(paths.per_user_data_dir()),
        "task_name": task_name,
        "exe": exe,
        "arguments": arguments,
        "working_dir": working_dir,
        "run_time": run_time,
        "user": user,
        "kind": kind.value,
        "run_highest": run_highest,
        "wake_to_run": wake_to_run,
        "password": password or "",
        "sftp_host": sftp_host,
        "sftp_username": sftp_username,
        "sftp_password": sftp_password,
    }


def build_machine_setup_payload(
    *,
    update_mode: str,
    sftp_host: str = "",
    sftp_username: str = "",
    sftp_password: str = "",
) -> dict[str, object]:
    """Build the ``machine_setup`` request (plan 0055 M2): provision the shared profile with NO nightly task.

    The same profile, DACL, migration, secret and commit as ``provision``, with the signed-in administrator as the only
    principal (``user`` blank: the shared folder grants nobody else anything until a nightly is scheduled for another
    account, which grants that account then) and the update mode written in the same commit. Refuses under
    ``DISTRICTSYNC_DATA_DIR`` exactly as :func:`build_provision_payload` does, and an unknown update mode with
    ``ValueError`` before anything is asked of Windows.

    Raises:
        ProvisionRefused: with :attr:`ProvisionStep.OVERRIDE`.
        ValueError: ``update_mode`` is not one of :data:`src.update.policy.UPDATE_MODES`.
    """
    _refuse_under_override()
    return {
        "op": "machine_setup",
        "source_data_dir": str(paths.per_user_data_dir()),
        "user": "",
        "update_mode": policy.parse_update_mode(update_mode),
        "sftp_host": sftp_host,
        "sftp_username": sftp_username,
        "sftp_password": sftp_password,
    }


def apply_machine_setup(payload: Mapping[str, object]) -> None:
    """The child half of ``machine_setup``: :func:`apply_provision` with no task to register and the update mode.

    A blank ``user`` makes the setup administrator the principal (``_principal_account``), so no other account is
    granted anything. Re-running it on a provisioned computer is the resume path, which is how ``--machine-setup
    --updates ...`` changes the update mode later.

    Raises:
        ProvisionRefused: at any step up to and including the commit.
        ValueError: the payload names a principal or an unknown update mode.
    """
    if str(payload.get("user", "")).strip():
        raise ValueError("machine setup grants no other account")
    mode = policy.parse_update_mode(payload.get("update_mode"))
    # At EVERY machine setup, whatever the mode (U2a review A3): made by an administrator before anyone can plant it,
    # and there when Group Policy later turns updates on. BEFORE the commit: a planted folder refuses the whole setup.
    ensure_updater_dir()
    apply_provision(payload, register=lambda: None, update_mode=mode)


def build_update_mode_payload(update_mode: str) -> dict[str, object]:
    """Build the ``set_update_mode`` request (plan 0055 U1b): change ONLY the machine update mode.

    Settings' Apply on a machine-wide install. Unlike ``machine_setup`` it copies no profile, seals no delivery password
    and hands nothing over: a second administrator changing the mode must never replace the shared delivery password
    with their own old one. Refuses under ``DISTRICTSYNC_DATA_DIR``; an unknown mode is ``ValueError`` before Windows is
    asked anything.
    """
    _refuse_under_override()
    return {"op": "set_update_mode", "update_mode": policy.parse_update_mode(update_mode)}


def apply_update_mode(payload: Mapping[str, object]) -> None:
    """The child half of ``set_update_mode``: write ``UpdateMode`` beside a switch that is already ON.

    Raises:
        ProvisionRefused: :attr:`ProvisionStep.OVERRIDE` under ``DISTRICTSYNC_DATA_DIR``; :attr:`ProvisionStep.COMMIT`
            when the value cannot be written.
        ValueError: an unknown mode, or a computer that is not set up (machine setup writes the mode then).
    """
    _refuse_under_override()
    mode = policy.parse_update_mode(payload.get("update_mode"))
    if paths.machine_switch_on() is not True:
        raise ValueError("this computer is not set up for the shared profile")
    ensure_updater_dir()
    try:
        _write_update_mode(mode)
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.COMMIT) from exc


def updater_dir_sddl() -> str:
    """The SYSTEM updater's state folder (plan 0055 U2): SYSTEM and Administrators full, Users read, PROTECTED."""
    return _descriptor(
        owner_sid=OWNER_ADMINISTRATORS,
        aces=[_ace(FULL_CONTROL, SID_SYSTEM), _ace(FULL_CONTROL, SID_ADMINISTRATORS), _ace(READ_EXECUTE, "BU")],
    )


def ensure_updater_dir() -> None:
    """Create ``%ProgramData%\\DistrictSync-Updater`` with its protected descriptor, or accept it only if it is ALREADY
    trusted (admin-owned, no write ace for anyone else). Never adopts a folder someone else made.

    Raises:
        ProvisionRefused: :attr:`ProvisionStep.PRE_EXISTING` for a folder that is there and untrusted;
            :attr:`ProvisionStep.CREATE` when it cannot be made.
    """
    from src.update.service import state_dir, state_dir_trusted

    folder = state_dir()
    try:
        _create_directory_with_sddl(folder, updater_dir_sddl())
    except FileExistsError:
        if not state_dir_trusted(folder):
            raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from None
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.CREATE) from exc


def _write_update_mode(mode: str) -> None:  # pragma: no cover - Windows-only registry write
    if sys.platform != "win32":
        raise OSError("The machine update mode is Windows-only.")
    import winreg

    access = paths.MACHINE_SCOPE_KEY_ACCESS | winreg.KEY_WRITE
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, access) as key:
        winreg.SetValueEx(key, policy.MACHINE_SETTING_VALUE_NAME, 0, winreg.REG_SZ, mode)


# --------------------------------------------------------------------------- #
# The child half — the three ops.                                             #
# --------------------------------------------------------------------------- #


def _refuse_under_override() -> None:
    try:
        in_play = paths._override_data_dir() is not None
    except (OSError, RuntimeError, ValueError):
        # An unresolvable value is still a value that was SET. Refuse.
        in_play = True
    if in_play:
        raise ProvisionRefused(ProvisionStep.OVERRIDE)


def _machine_root() -> Path:
    """Resolve the machine root ONCE, refusing a ``WIN_PD_OVERRIDE_*`` redirect."""
    try:
        return paths.machine_data_dir()
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.OVERRIDE) from exc


def _agreed_source(payload: Mapping[str, object]) -> Path:
    """The per-user profile to migrate — the child's OWN answer, cross-checked against the payload."""
    mine = paths.per_user_data_dir()
    theirs = str(payload.get("source_data_dir", ""))
    if not theirs or Path(theirs) != mine:
        raise ProvisionRefused(ProvisionStep.SOURCE)
    return mine


def _sid_for(account: str) -> str:
    try:
        return _account_sid(account)
    except (LookupError, OSError) as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc


def _principal_account(payload: Mapping[str, object], *, setup_account: str) -> str:
    """The account the nightly will run as. Blank means the setup user (0046's ``""``).

    The validator is chosen by the payload's declared ``kind`` (plan 0049 S-3), matching
    ``elevated_apply._do_register``'s ladder rung for rung. It has to: this is the name that
    gets ACEs on ``C:\\ProgramData\\DistrictSync`` and a SID looked up for it, and refusing
    a managed service account here would leave the nightly registered to a principal the
    shared profile had never granted anything to. An unknown kind is a refusal, never a
    default — the enum call raises and it lands on the same ``PRINCIPAL`` step id as a
    malformed name.
    """
    requested = str(payload.get("user", "")).strip()
    if not requested:
        return setup_account
    try:
        kind = task_com.PrincipalKind(str(payload.get("kind", "")))
        return task_com.validate_principal_account(kind, requested)
    except ValueError as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc


def apply_provision(payload: Mapping[str, object], *, register: Callable[[], None], update_mode: str | None) -> None:
    """Provision this computer for a shared profile, fail-closed, in S-1b-i.3's order.

    ``register`` is injected rather than called here so the registration keeps going
    through ``elevated_apply._do_register`` — the same re-validating path the plain
    ``register`` op uses — and so its ``task_com`` canonical reaches the admin unchanged
    instead of being flattened into a step id. It runs AFTER the commit, and a failure
    there deliberately leaves the switch: the directory is valid, only the task is missing.

    Raises:
        ProvisionRefused: at any step up to and including the commit.
    """
    if update_mode is not None:
        # Parsed BEFORE step 1, so a bad mode can never reach the commit after the display values are written.
        update_mode = policy.parse_update_mode(update_mode)
    _refuse_under_override()  # 1a
    root = _machine_root()
    source = _agreed_source(payload)  # 1b

    setup_account = current_run_as_user()
    setup_sid = _sid_for(setup_account)
    principal_account = _principal_account(payload, setup_account=setup_account)
    principal_sid = _sid_for(principal_account)

    resuming = _resume_or_refuse(root)  # 2
    if principal_sid != setup_sid:
        _record_grant(principal_sid)  # 2b (DSYNC-115) — recorded BEFORE anything is granted
    created = False
    try:
        if not resuming:
            _create_profile(root, setup_sid=setup_sid, principal_sid=principal_sid)  # 3
            created = True
        else:
            _ensure_runs_and_principal(  # 3b
                root,
                setup_sid=setup_sid,
                principal_sid=principal_sid,
                principal_account=principal_account,
            )
        migrate_profile(source, root)  # 4
        _seed_secret(payload)  # 5
        _verify_provisioned(root, source)  # 6
        try:
            _commit_machine_switch(update_mode=update_mode)  # 7 — the commit point
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.COMMIT) from exc
    except ProvisionRefused as exc:
        if created:
            _rollback(root, exc)
        raise
    register()  # 8


def _resume_or_refuse(root: Path) -> bool:
    """``False`` = fresh install · ``True`` = resume · raise = something else is there.

    Only an elevated administrator can produce an Administrators-owned,
    ``SE_DACL_PROTECTED`` directory with no open-group ace, so a directory that passes the
    app's own trust predicate is provably OURS. Re-running the remaining steps against it is
    what stops a kill between the secret write and the commit — the child IS killed, the
    bounded ``run_elevated`` wait (``windows._ELEV_TIMEOUT_S``) has to cover a full ``history.db`` backup
    on an AV-scanned server — from stranding a sealed credential in a directory every future
    attempt refuses forever.

    The HKLM switch is deliberately NOT consulted. Trusted-and-already-committed resumes
    too, and lands on the same steps: :func:`migrate_profile` skips what is already there,
    so the shared ``config.json`` is safe, and step 3b grants the (possibly new) principal —
    which is exactly D4 step 3's "already provisioned → grant the new principal". A branch
    that read the switch and then did the same thing either way would be dead code.
    """
    if not root.exists():
        return False
    try:
        paths.assert_machine_dir_trusted(root)
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc
    return True


def _create_profile(root: Path, *, setup_sid: str, principal_sid: str) -> None:
    """Step 3 — every :data:`PROTECTED_SUBTREES` folder (root first), each created WITH its descriptor."""
    for subtree in PROTECTED_SUBTREES:
        sddl = _subtree_sddl(subtree, setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=OWNER_ADMINISTRATORS)
        try:
            _create_directory_with_sddl(root / subtree.relative, sddl)
        except FileExistsError as exc:
            # The atomic gate. Reached only when step 2 saw nothing there, i.e. something
            # was planted in between — refuse, never adopt-and-fix.
            raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.CREATE) from exc


def _ensure_runs_and_principal(root: Path, *, setup_sid: str, principal_sid: str, principal_account: str) -> None:
    """Step 3b — the idempotent half of step 3, for a resumed or re-scheduled profile.

    A protected folder (``runs/``; any :data:`PROTECTED_SUBTREES` row) may be missing (a kill right
    after the root was created) and the principal may
    be a NEW one (Remove pruned the last). Both are repaired additively with ``icacls``,
    where **every non-zero exit is fatal** — the inverse of
    ``elevation._set_owner_only_dacl``'s warn-and-continue, because here the DACL is the
    confidentiality boundary rather than a defence-in-depth layer over DPAPI.
    """
    for subtree in PROTECTED_SUBTREES:
        folder = root / subtree.relative
        if folder.exists():
            continue
        sddl = _subtree_sddl(subtree, setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=OWNER_ADMINISTRATORS)
        try:
            _create_directory_with_sddl(folder, sddl)
        except FileExistsError as exc:
            raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.CREATE) from exc

    if principal_sid == setup_sid:
        # The setup user already holds Modify on both, granted in the descriptor itself.
        # Skipping here keeps a RESUMED profile's DACL byte-for-byte the same as a freshly
        # created one (``_machine_root_sddl`` omits the duplicate for the same reason), so
        # two installs of the same shape never read back differently through ``icacls``.
        return

    # One source (DSYNC-117 F6): the same rows and rights the descriptors and the revoke read.
    for subtree in PROTECTED_SUBTREES:
        target = root / subtree.relative
        exit_code = _run_icacls([str(target), "/grant", f"{principal_account}:(OI)(CI){subtree.principal_rights}"])
        if exit_code != 0:
            raise ProvisionRefused(ProvisionStep.PRINCIPAL, icacls_exit=exit_code)


def _seed_secret(payload: Mapping[str, object]) -> None:
    """Step 5 — seal the delivery password into the shared profile, if one was sent.

    Nothing sent means delivery is not configured (or its secret was unreadable, which the
    parent gates on in S-2): provision proceeds with no blob and **makes no claim**.
    """
    host = str(payload.get("sftp_host", "")).strip()
    username = str(payload.get("sftp_username", "")).strip()
    password = str(payload.get("sftp_password", ""))
    if not (host and username and password):
        return
    try:
        store = _machine_secret_store()
        store.store_password(host, username, password)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - the step id is the whole report; see below
        # Deliberately broad AND deliberately silent about the cause: everything this can
        # raise (SecretStoreError, OSError from DPAPI) has a message this module must not
        # forward, because the payload it was handling holds the password itself.
        raise ProvisionRefused(ProvisionStep.SECRET) from exc


def _verify_provisioned(root: Path, source: Path) -> None:
    """Step 6 — verify with the APP'S OWN predicate, never with an ``icacls`` exit code.

    An exit code says a command succeeded, not that the result is one this app will accept.
    Without this, a grant that resolved oddly commits a switch pointing at a directory the
    trust predicate later rejects — and since D0 forbids falling through, the app then
    refuses to start for the admin *and* the nightly, recoverable only by hand-editing HKLM.

    The run store is checked against :func:`src.utils.paths.history_db_in`, the same
    function ``user_history_db()`` calls, so the migration cannot land it somewhere the
    reader will not look.
    """
    try:
        paths.assert_machine_dir_trusted(root)  # the root's ace walk is part of the trust predicate
        for subtree in PROTECTED_SUBTREES:
            if subtree.relative:
                paths.assert_no_open_aces(root / subtree.relative)
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.VERIFY) from exc
    if (source / paths.RUN_STORE_NAME).is_file() and not paths.history_db_in(root, machine_scope=True).is_file():
        raise ProvisionRefused(ProvisionStep.VERIFY)


def _rollback(root: Path, failure: ProvisionRefused) -> None:
    """Remove the directory THIS call created. A removal failure gets its own step id.

    Never silent: a standing, admin-owned ``C:\\ProgramData\\DistrictSync`` that provisioning
    left behind is the one state a later attempt would have to reason about, so it is
    reported rather than swallowed.
    """
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise ProvisionRefused(failure.step, icacls_exit=failure.icacls_exit, rollback_failed=True) from exc


def apply_grant_current_user(payload: Mapping[str, object]) -> None:
    """Give the administrator running THIS child Modify on the shared profile.

    The grantee is derived **only from the child's own token** and the payload is not
    consulted for it at all: a payload-named account would let any admin-consented request
    grant an arbitrary principal Modify on the shared profile — including the service
    account's, or a standard user's.

    **Every protected folder, not just the root (DSYNC-117).** The grantee gets the setup
    administrator's rights on each :data:`PROTECTED_SUBTREES` row: ``runs/`` is protected, so an
    ace on the root never reaches it, and a root-only grant left the administrator unable to
    create its own log. ``icacls /grant`` MERGES into an existing explicit ace, so the op is
    idempotent, and re-running it on an install granted by an earlier build adds exactly the
    missing ``runs/`` ace (the repair). A row whose folder is absent is skipped: this op never
    creates a profile folder (it cannot know the setup administrator's SID); ``provision``'s
    resume re-creates ``runs/`` with its descriptor.

    Consequence, stated rather than solved here: these aces only ever GROW. A granted admin
    keeps Modify on ``config.json`` and read access to ``sftp_secret.bin`` after demotion,
    and only the *task principal* has a prune. → backlog.

    Raises:
        ProvisionRefused: with ``OVERRIDE``, ``PRE_EXISTING``, ``PRINCIPAL`` or ``GRANT``.
    """
    del payload  # the grantee is the token's, never the request's
    _refuse_under_override()
    root = _machine_root()
    try:
        paths.assert_machine_dir_trusted(root)
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc

    grantee = current_run_as_user()
    for subtree in PROTECTED_SUBTREES:
        target = root / subtree.relative
        if not target.exists():
            continue
        exit_code = _run_icacls([str(target), "/grant", f"{grantee}:(OI)(CI){subtree.setup_rights}"])
        if exit_code != 0:
            raise ProvisionRefused(ProvisionStep.GRANT, icacls_exit=exit_code)


# SIDs whose aces hold the shared profile together. Pruning any of them would lock the
# admin, SYSTEM or this very child out of a directory nothing else can repair.
_UNPRUNABLE_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})


def apply_prune_principal(payload: Mapping[str, object]) -> None:
    """Remove the nightly, then revoke what DistrictSync granted its account — in that order, never before.

    The delete and the prune are ONE elevated operation so the admin sees ONE UAC prompt,
    and the prune runs only after a read-back confirms the task is MISSING.

    The secret deliberately STAYS: Convert's manual delivery still needs it, and the setup
    user's ``:M`` is what owns it thereafter.

    **What is revoked comes from the grant record** (DSYNC-115, :data:`GRANT_LEDGER_VALUE_NAME`), for every
    principal kind: every recorded SID, because once the one nightly is gone none of them has a task to run. The
    record — written by this module, before each grant, in a key only an administrator can write — is what decides.
    An account the request names that is NOT recorded (a pre-record principal still in the schedule record, review
    L2) gets only the legacy rule below: exact shape, fenced, never added to the record.
    A recorded SID that no longer resolves to a name (a retired managed service account) is still revoked: the
    trustee is the SID. Each revoke goes through :func:`_revoke`'s shape rule, so an ace DistrictSync did not make
    stays, and SYSTEM, Administrators and the administrator running this child are never touched. What was
    revoked (or found already gone) leaves the record; what was kept stays in it.

    **An install from before the record** (the value is ABSENT, not empty) falls back to the request: the account
    the schedule record names is revoked only when its aces are EXACTLY the shape DistrictSync grants (``(OI)(CI)``
    read-and-execute on the root, ``(OI)(CI)`` Modify on ``runs``) and it is none of the fenced accounts; anything
    else is kept and reported. No record is created by a Remove. An account a pre-record build granted and whose
    Remove (under that build) left it in place is not known to DistrictSync at all, and stays: the developer guide
    ``docs/developer/machine-scope.md`` gives the ``icacls`` line that removes it by hand.

    The request's account is validated against its KIND either way (plan 0049 S-4), mirroring
    :func:`_principal_account` rung for rung: an ABSENT kind resolves through
    ``task_com.principal_kind_from_record`` (a pre-S-4 parent's request means a password logon), and the
    ``$``-disagreement is caught by the validator itself — a ``SVC$`` name checked as a password logon is REFUSED.
    Every refusal up to here happens BEFORE the delete, so the parent falls back to the plain remove.

    Raises:
        ProvisionRefused: ``OVERRIDE``, ``PRE_EXISTING`` or ``PRINCIPAL`` before the delete; ``DELETE`` when the
            task's removal was not confirmed (nothing revoked); ``PRUNE`` after a confirmed delete when an ace was
            kept, a DACL could not be read, or ``icacls`` failed (with its exit code).
    """
    _refuse_under_override()
    root = _machine_root()
    try:
        paths.assert_machine_dir_trusted(root)
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc

    requested = str(payload.get("user", "")).strip()
    if not requested:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL)
    try:
        kind = task_com.principal_kind_from_record(payload.get("kind"), user=requested)
        principal = task_com.validate_principal_account(kind, requested)
    except ValueError as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc
    try:
        recorded = read_grant_ledger()
    except OSError as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc
    admin_sid = _sid_for(current_run_as_user()).upper()

    if recorded is None:
        principal_sid = _sid_for(principal).upper()
        if principal_sid in _UNPRUNABLE_SIDS or principal_sid == admin_sid:
            raise ProvisionRefused(ProvisionStep.PRINCIPAL)
        candidates: tuple[str, ...] = (principal_sid,)
    else:
        candidates = recorded
        try:
            requested_sid = _sid_for(principal).upper()
        except ProvisionRefused:
            requested_sid = ""  # it no longer resolves: the record still revokes what IT holds
        if requested_sid and requested_sid not in recorded:
            # Review L2: a pre-record principal the schedule record still names (its Remove was declined, then a
            # later registration started the record). The legacy rule applies to it: exact shape only, and
            # ``_revoke``'s fence (SYSTEM, Administrators, this administrator) keeps — and reports — the rest.
            candidates = (*recorded, requested_sid)

    task_name = str(payload.get("task_name", "")).strip()
    if not task_name or not _delete_task_confirmed_missing(task_name):
        raise ProvisionRefused(ProvisionStep.DELETE)

    revoked, kept = _revoke(root, candidates, protected=frozenset({admin_sid}))
    if recorded is not None and revoked:
        try:
            _write_grant_ledger_value([sid for sid in recorded if sid not in revoked])
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.PRUNE) from exc
    if kept:
        raise ProvisionRefused(ProvisionStep.PRUNE)


def prune_retired_grants(payload: Mapping[str, object]) -> None:
    """After a successful ``provision``: revoke every recorded grant but the new principal's (DSYNC-115). TOTAL.

    This is the principal SWITCH. The Register gate already makes a switch Remove-then-Schedule, and that Remove
    revokes; this covers a Remove whose revoke did not happen (declined, kept, or an ``icacls`` failure) — the old
    account is still recorded, so scheduling the new one retires it. Called by ``elevated_apply._do_provision`` only
    AFTER the new nightly registered, which replaced any task under the same name, so no live task loses its account.
    ``machine_setup`` never calls it: a nightly may be live there.

    The same :func:`_revoke` rule as a Remove: only recorded SIDs, only DistrictSync's shape, never SYSTEM,
    Administrators, the administrator running this child or the new principal.

    **Never raises — whatever the exception** (review L1). The nightly is registered and recorded when this runs;
    an escape would reach ``elevated_apply._apply`` and report that registration as failed. A retired grant that
    could not be revoked must not turn that into a reported failure. It stays in the record, so the next Remove revokes it and
    reports the outcome. An install with no record has nothing recorded to retire.
    """
    try:
        recorded = read_grant_ledger()
        if not recorded:
            return
        setup_account = current_run_as_user()
        admin_sid = _sid_for(setup_account).upper()
        keep = _sid_for(_principal_account(payload, setup_account=setup_account)).upper()
        retired = tuple(sid for sid in recorded if sid != keep)
        if not retired:
            return
        revoked, _kept = _revoke(_machine_root(), retired, protected=frozenset({admin_sid, keep}))
        if revoked:
            _write_grant_ledger_value([sid for sid in recorded if sid not in revoked])
    except Exception:  # noqa: BLE001 - best-effort by design: the nightly is already registered
        # No log line: this module runs elevated and has no logger by design (module docstring). Nothing is lost —
        # whatever was not revoked is still recorded, and the next Remove revokes it and reports the outcome.
        return


# --------------------------------------------------------------------------- #
# --migrate --apply (DSYNC-116 review H2): the moved nightly's access         #
# --------------------------------------------------------------------------- #


def _trusted_root() -> Path:
    _refuse_under_override()
    root = _machine_root()
    try:
        paths.assert_machine_dir_trusted(root)
    except paths.MachineScopeRefused as exc:
        raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc
    return root


def _migrating_principal(account: str, kind: task_com.PrincipalKind) -> tuple[str, str]:
    """``(account, sid)`` for the nightly's principal, validated by its KIND. Raises ``PRINCIPAL``."""
    try:
        principal = task_com.validate_principal_account(kind, account)
    except ValueError as exc:
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc
    return principal, _sid_for(principal).upper()


def grant_principal(account: str, kind: task_com.PrincipalKind) -> bool:
    """Give a moved nightly's principal its access to the shared profile: RX on the root, M on ``runs/``.

    The SAME steps a Schedule press for that account runs in ``apply_provision``: the SID goes into the grant record
    FIRST (DSYNC-115's :func:`_record_grant`, before anything is granted), then the additive step 3b
    (:func:`_ensure_runs_and_principal`). So ``--migrate --apply``'s schedule check (which writes its marker into
    ``runs/``) and the moved nightly can use the shared profile on a computer that machine setup provisioned for the
    administrator only, and a later Remove (``apply_prune_principal``) revokes it from the record.

    Returns whether the account was NEW to the shared profile (no ALLOW ace on the root before): only a new grant is
    ever taken back by the move (:func:`revoke_principal`), because an account that already had access may be the
    one the OLD nightly uses. It is recorded either way, so a Remove of the moved nightly revokes it.

    Raises:
        ProvisionRefused: ``OVERRIDE``, ``PRE_EXISTING`` or ``PRINCIPAL`` (an unwritable record included; then
            nothing was granted), with an icacls exit code when the grant itself failed.
    """
    root = _trusted_root()
    principal, principal_sid = _migrating_principal(account, kind)
    setup_sid = _sid_for(current_run_as_user()).upper()
    if principal_sid in _UNPRUNABLE_SIDS or principal_sid == setup_sid:
        return False  # SYSTEM, Administrators or the administrator: their access is in the descriptor itself
    try:
        already = any(
            kind_ == _ACCESS_ALLOWED and who.upper() == principal_sid
            for kind_, _flags, _mask, who in _dacl_entries(root)
        )
    except OSError as exc:
        # Without the answer a later take-back could strip access someone else relies on: refuse, change nothing.
        raise ProvisionRefused(ProvisionStep.PRINCIPAL) from exc
    _record_grant(principal_sid)  # DSYNC-115: recorded BEFORE anything is granted
    try:
        _ensure_runs_and_principal(root, setup_sid=setup_sid, principal_sid=principal_sid, principal_account=principal)
    except ProvisionRefused:
        # Second review, MEDIUM 1: the root grant may have landed before ``runs`` refused. An account that had no
        # access before is taken back (best-effort, through the record: a failed take-back leaves its SID recorded,
        # so the next Remove retries it); the grant's own refusal is what is reported.
        if not already:
            with contextlib.suppress(ProvisionRefused):
                _take_back(root, principal_sid, admin_sid=setup_sid)
        raise
    return not already


def revoke_principal(account: str, kind: task_com.PrincipalKind) -> None:
    """Take back a grant :func:`grant_principal` made NEW, after a move that did not happen, and drop it from the record.

    DSYNC-115's :func:`_revoke` does the revoke (by SID, only DistrictSync's exact shape, never SYSTEM, Administrators
    or the administrator running this child); what was revoked leaves the record, as a Remove does. An ace DistrictSync
    did not make is kept AND stays recorded, so the next Remove reports it. Raises ``PRUNE`` when anything was kept.
    """
    root = _trusted_root()
    _principal, principal_sid = _migrating_principal(account, kind)
    _take_back(root, principal_sid, admin_sid=_sid_for(current_run_as_user()).upper())


def _take_back(root: Path, principal_sid: str, *, admin_sid: str) -> None:
    """115's :func:`_revoke` for ONE SID, then drop it from the record if (and only if) it was revoked."""
    revoked, kept = _revoke(root, (principal_sid,), protected=frozenset({admin_sid}))
    if revoked:
        try:
            recorded = read_grant_ledger()
            if recorded is not None and principal_sid in recorded:
                _write_grant_ledger_value([sid for sid in recorded if sid != principal_sid])
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.PRUNE) from exc
    if kept:
        raise ProvisionRefused(ProvisionStep.PRUNE)
