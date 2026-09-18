"""Turning this computer into a machine-scoped install — the elevated engine (0049 S-1b-i).

Three operations run behind ONE UAC prompt, dispatched by
:mod:`src.scheduler.elevated_apply`. **Nothing in the app calls them yet** — Schedule-time
dispatch is S-2. That is deliberate: the riskiest code in the plan lands and gets tested
before any surface can reach it.

* ``provision`` — create ``C:\\ProgramData\\DistrictSync``, migrate the admin's per-user
  profile into it, seal the delivery secret, VERIFY, then commit the HKLM switch.
* ``grant_current_user`` — one additive ``:M`` ace for a second administrator.
* ``prune_principal`` — remove a retired task principal's aces, after a confirmed delete.

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

import ctypes
import json
import os
import secrets
import shutil
import sqlite3
import subprocess  # nosec B404 - icacls (DACL edits) is a trusted, absolute-path System32 binary
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from src.scheduler import task_com
from src.scheduler.windows import current_run_as_user
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
    GRANT = "grant"  # the additive :M ace failed
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


def _account_sid(name: str) -> str:  # pragma: no cover - Windows-only ctypes
    """Resolve ``DOMAIN\\user`` (or a bare name) to its SID string.

    Raises ``LookupError`` when Windows cannot resolve it — which is a REFUSAL, not a
    fallback: a DACL built around an unresolvable principal would either fail to apply or
    grant the wrong trustee.
    """
    if sys.platform != "win32":
        raise LookupError("Account SIDs can only be resolved on Windows.")

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
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or sid_size.value == 0:  # type: ignore[attr-defined]
        raise LookupError(f"Windows did not recognise the account (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]

    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not advapi32.LookupAccountNameW(
        None, name, sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size), ctypes.byref(use)
    ):
        raise LookupError(f"Windows did not recognise the account (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]

    text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.cast(sid, ctypes.c_void_p), ctypes.byref(text)):
        raise LookupError(f"The account's SID could not be read (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
    try:
        return str(text.value)
    finally:
        kernel32.LocalFree(text)


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
            timeout=60,
            check=False,
            creationflags=subprocess_no_window_flags(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ICACLS_NOT_LAUNCHED
    return completed.returncode


def _commit_machine_switch(provisioned_by: str) -> None:  # pragma: no cover - Windows-only registry write
    """Write ``HKLM\\SOFTWARE\\DistrictSync`` — the COMMIT POINT of the whole sequence.

    The access mask is :data:`src.utils.paths.MACHINE_SCOPE_KEY_ACCESS` **imported, never
    re-spelled**, plus ``KEY_WRITE``. That constant carries ``KEY_WOW64_64KEY``: a writer in
    the redirected 32-bit view would commit ``WOW6432Node\\DistrictSync``, report success,
    and leave the app per-user with the config and the secret already copied — while the
    reader looked at the 64-bit key and saw nothing.

    ``MachineScope`` is written LAST. The two display values mean nothing on their own, so
    a failure between them and the switch leaves the install un-provisioned, which is the
    resume case rather than a half-committed one.

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
        winreg.SetValueEx(key, "ProvisionedBy", 0, winreg.REG_SZ, provisioned_by)
        winreg.SetValueEx(key, paths.MACHINE_SCOPE_VALUE_NAME, 0, winreg.REG_DWORD, 1)


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


def _machine_root_sddl(*, setup_sid: str, principal_sid: str, owner_sid: str) -> str:
    """The shared profile root (D2): SYSTEM F · Administrators F · setup user M · principal RX.

    The setup user is granted **explicitly** because ``BUILTIN\\Administrators`` in a DACL
    grants nothing to a non-elevated process (the UAC filtered token) — the admin's own
    un-elevated session has to be able to write ``config.json``.

    The principal gets READ-EXECUTE here and Modify only on ``runs/``: the service account
    reads what it needs and writes only run artefacts; it cannot alter the admin's
    trust-bearing state. When the principal IS the setup user its single ``M`` ace stands
    alone — a second ace for the same SID would be redundant and confusing in a read-back.
    """
    aces = [
        _ace(FULL_CONTROL, SID_SYSTEM),
        _ace(FULL_CONTROL, SID_ADMINISTRATORS),
        _ace(MODIFY, setup_sid),
    ]
    if principal_sid != setup_sid:
        aces.append(_ace(READ_EXECUTE, principal_sid))
    return _descriptor(owner_sid=owner_sid, aces=aces)


def _runs_sddl(*, setup_sid: str, principal_sid: str, owner_sid: str) -> str:
    """``runs/`` — the same, except the principal gets Modify (its log + the run store).

    ``provision`` creates this directory: S-1a deliberately left ``user_log_file()``
    non-creating, because a ``runs/`` created later by an admin process would inherit the
    root's RX and silently break the principal's log.
    """
    aces = [
        _ace(FULL_CONTROL, SID_SYSTEM),
        _ace(FULL_CONTROL, SID_ADMINISTRATORS),
        _ace(MODIFY, setup_sid),
    ]
    if principal_sid != setup_sid:
        aces.append(_ace(MODIFY, principal_sid))
    return _descriptor(owner_sid=owner_sid, aces=aces)


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
                    json.dumps(json.loads(origin.read_text(encoding="utf-8")), indent=2), encoding="utf-8"
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

    Raises:
        ProvisionRefused: with :attr:`ProvisionStep.OVERRIDE`.
    """
    _refuse_under_override()
    return {
        "op": "provision",
        "source_data_dir": str(paths.per_user_data_dir()),
        "task_name": task_name,
        "exe": exe,
        "arguments": arguments,
        "working_dir": working_dir,
        "run_time": run_time,
        "user": user,
        "kind": kind.value,
        "run_highest": run_highest,
        "password": password or "",
        "sftp_host": sftp_host,
        "sftp_username": sftp_username,
        "sftp_password": sftp_password,
    }


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


def apply_provision(payload: Mapping[str, object], *, register: Callable[[], None]) -> None:
    """Provision this computer for a shared profile, fail-closed, in S-1b-i.3's order.

    ``register`` is injected rather than called here so the registration keeps going
    through ``elevated_apply._do_register`` — the same re-validating path the plain
    ``register`` op uses — and so its ``task_com`` canonical reaches the admin unchanged
    instead of being flattened into a step id. It runs AFTER the commit, and a failure
    there deliberately leaves the switch: the directory is valid, only the task is missing.

    Raises:
        ProvisionRefused: at any step up to and including the commit.
    """
    _refuse_under_override()  # 1a
    root = _machine_root()
    source = _agreed_source(payload)  # 1b

    setup_account = current_run_as_user()
    setup_sid = _sid_for(setup_account)
    principal_account = _principal_account(payload, setup_account=setup_account)
    principal_sid = _sid_for(principal_account)

    resuming = _resume_or_refuse(root)  # 2
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
            _commit_machine_switch(setup_account)  # 7 — the commit point
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
    120 s ``run_elevated`` budget has to cover UAC dwell plus a full ``history.db`` backup
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
    """Step 3 — the root and ``runs/``, each created WITH its descriptor."""
    for path, sddl in (
        (root, _machine_root_sddl(setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=OWNER_ADMINISTRATORS)),
        (
            root / paths.MACHINE_RUNS_SUBDIR,
            _runs_sddl(setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=OWNER_ADMINISTRATORS),
        ),
    ):
        try:
            _create_directory_with_sddl(path, sddl)
        except FileExistsError as exc:
            # The atomic gate. Reached only when step 2 saw nothing there, i.e. something
            # was planted in between — refuse, never adopt-and-fix.
            raise ProvisionRefused(ProvisionStep.PRE_EXISTING) from exc
        except OSError as exc:
            raise ProvisionRefused(ProvisionStep.CREATE) from exc


def _ensure_runs_and_principal(root: Path, *, setup_sid: str, principal_sid: str, principal_account: str) -> None:
    """Step 3b — the idempotent half of step 3, for a resumed or re-scheduled profile.

    ``runs/`` may be missing (a kill right after the root was created) and the principal may
    be a NEW one (Remove pruned the last). Both are repaired additively with ``icacls``,
    where **every non-zero exit is fatal** — the inverse of
    ``elevation._set_owner_only_dacl``'s warn-and-continue, because here the DACL is the
    confidentiality boundary rather than a defence-in-depth layer over DPAPI.
    """
    runs = root / paths.MACHINE_RUNS_SUBDIR
    if not runs.exists():
        try:
            _create_directory_with_sddl(
                runs, _runs_sddl(setup_sid=setup_sid, principal_sid=principal_sid, owner_sid=OWNER_ADMINISTRATORS)
            )
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

    for target, rights in ((root, "RX"), (runs, "M")):
        exit_code = _run_icacls([str(target), "/grant", f"{principal_account}:(OI)(CI){rights}"])
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
        paths.assert_machine_dir_trusted(root)
        paths.assert_no_open_aces(root / paths.MACHINE_RUNS_SUBDIR)
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

    Consequence, stated rather than solved here: root aces only ever GROW. A granted admin
    keeps Modify on ``config.json`` and read access to ``sftp_secret.bin`` after demotion,
    and only the *task principal* has a prune. → ROADMAP.

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
    exit_code = _run_icacls([str(root), "/grant", f"{grantee}:(OI)(CI)M"])
    if exit_code != 0:
        raise ProvisionRefused(ProvisionStep.GRANT, icacls_exit=exit_code)


# SIDs whose aces hold the shared profile together. Pruning any of them would lock the
# admin, SYSTEM or this very child out of a directory nothing else can repair.
_UNPRUNABLE_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})


def apply_prune_principal(payload: Mapping[str, object]) -> None:
    """Remove the nightly, then the retired principal's aces — in that order, never before.

    The delete and the prune are ONE elevated operation so the admin sees ONE UAC prompt,
    and the prune runs only after a read-back confirms the task is MISSING.

    The secret deliberately STAYS: Convert's manual delivery still needs it, and the setup
    user's ``:M`` is what owns it thereafter.

    The principal is taken from the payload (it is the RECORDED account, which the child
    cannot re-derive) but it is fenced: a blank value, the child's own account, SYSTEM and
    Administrators are all refused. Unlike the grant, a payload-named account here removes
    access rather than adding it — the damage is a locked-out profile, not an escalation,
    and this fence makes the unsafe call unrepresentable rather than merely unlikely.

    **The name is validated against its KIND** (plan 0049 S-4), mirroring
    :func:`_principal_account` rung for rung. Until S-4 this was ``validate_run_as_user``
    unconditionally, which rejects a trailing ``$`` — so the one principal that most needs
    pruning could not be pruned at all, and a retired managed service account would keep its
    ACEs on the shared profile with nothing in the app able to revoke them. The kind travels
    on the payload and an ABSENT one resolves through
    ``task_com.principal_kind_from_record`` (the same evidenced rule the durable record uses):
    a request built by an earlier build carries no ``kind``, and the only foreign principal
    those builds could register was a password logon. An unrecognised value lands there too
    rather than raising, and the ``$``-disagreement is then caught by the validator itself —
    a ``SVC$`` name checked as a password logon is REFUSED, which is the safe direction.

    Raises:
        ProvisionRefused: with ``OVERRIDE``, ``PRE_EXISTING``, ``PRINCIPAL``, ``DELETE`` or ``PRUNE``.
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
    principal_sid = _sid_for(principal)
    if principal_sid in _UNPRUNABLE_SIDS or principal_sid == _sid_for(current_run_as_user()):
        raise ProvisionRefused(ProvisionStep.PRINCIPAL)

    task_name = str(payload.get("task_name", "")).strip()
    if not task_name or not _delete_task_confirmed_missing(task_name):
        raise ProvisionRefused(ProvisionStep.DELETE)

    for target in (root, root / paths.MACHINE_RUNS_SUBDIR):
        if not target.exists():
            continue
        exit_code = _run_icacls([str(target), "/remove:g", principal])
        if exit_code != 0:
            raise ProvisionRefused(ProvisionStep.PRUNE, icacls_exit=exit_code)
