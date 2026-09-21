"""Path resolution helpers — single source of truth.

Separates read-only bundle paths (built-in mappings, logging config,
shipped docs) from user-writable data paths (logs, custom mappings,
runtime config). Works identically when running from source or from
a PyInstaller one-file bundle.

Why this exists: relative paths like ``Path("config/mappings")`` break
in the frozen exe because the launcher chdirs to ``sys._MEIPASS`` (a
temp directory that's deleted on exit) and the scheduled-task runtime
has cwd set to ``%SystemRoot%\\System32``. Both scenarios need
absolute paths resolved against the right anchor.

Since plan 0049 the writable profile also has a SCOPE: per-user (every install in the
field) or machine-wide, shared by every principal on the computer. The scope is a
switch, not a discovery; it is decided together with the path, ONCE per process
(:func:`user_data_dir` / :func:`is_machine_scope` / :func:`pin_data_dir`); and with the
switch on it either yields a directory that passes :func:`_assert_machine_dir_trusted`
or REFUSES — it never falls back to the per-user profile. See :class:`MachineScopeRefused`.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import platformdirs

from src.utils.accounts import process_account, sanitise_account_for_filename

logger = logging.getLogger(__name__)

# The industry-standard per-OS user-data directory is keyed off this app name on
# EVERY OS. platformdirs uses the name verbatim (it does NOT case-fold), so all
# three platforms share the same ``DistrictSync`` leaf — a single, professional,
# consistent identity:
#   Windows  %LOCALAPPDATA%\DistrictSync
#   macOS    ~/Library/Application Support/DistrictSync
#   Linux    $XDG_DATA_HOME/DistrictSync  (default ~/.local/share/DistrictSync)
_APP_NAME = "DistrictSync"

# The pre-relocation location every existing install used. Kept as BOTH the
# migration source and the deterministic fallback, so a user is never stranded
# between two locations.
_LEGACY_DIR_NAME = ".districtsync"

# Documented SUPPORT / TEST override for the whole user-data profile — see
# ``_override_data_dir`` and ``user_data_dir`` for the contract and the why.
_DATA_DIR_ENV_VAR = "DISTRICTSYNC_DATA_DIR"

# ``platformdirs``' OWN environment override prefix (``WIN_PD_OVERRIDE_LOCAL_APPDATA``,
# ``WIN_PD_OVERRIDE_COMMON_APPDATA``, …). NOT ours and never set by this app — see
# :func:`machine_data_dir`, which REFUSES while any of them is in play.
_PD_OVERRIDE_PREFIX = "WIN_PD_OVERRIDE_"

# Breadcrumb dropped in the legacy dir after a successful migration.
_MOVED_BREADCRUMB = "MOVED.txt"

# The machine-scoped profile's run-artefact subtree (the ONLY one the task principal may
# write) and the run store's file name. Spelled once — :func:`history_db_in`,
# :func:`user_log_file` and the elevated provisioner all derive from these.
MACHINE_RUNS_SUBDIR = "runs"
RUN_STORE_NAME = "history.db"

# --------------------------------------------------------------------------- #
# Machine scope (plan 0049 D0) — a SWITCH, not a discovery.                    #
# --------------------------------------------------------------------------- #
# ``HKLM\SOFTWARE\DistrictSync`` → ``MachineScope`` (REG_DWORD 1) is world-readable and
# admin-only-writable, so a standard user cannot pre-create anything that redirects the
# app. It is written ONLY by the elevated ``provision`` op (plan 0049 S-1b), as its commit
# point — which is why ``is_machine_scope()`` is False on every install in the field today.
MACHINE_SCOPE_KEY_PATH = r"SOFTWARE\DistrictSync"
MACHINE_SCOPE_VALUE_NAME = "MachineScope"

# ``winreg.KEY_READ | winreg.KEY_WOW64_64KEY``. Spelled numerically because ``winreg`` does
# not import off Windows (this module is imported on every OS), and pinned to the winreg
# constants by a Windows-only parity test. EXPORTED because S-1b's writer must open the key
# with the same view: a writer in the redirected 32-bit view would commit
# ``WOW6432Node\DistrictSync``, report success, and leave the app per-user with the config
# and the secret already copied.
MACHINE_SCOPE_KEY_ACCESS = 0x20119
_REG_DWORD = 4

# The only owners a machine-scoped profile may have. Compared as SID STRINGS, never account
# names: ``BUILTIN\Administrators`` is localised, so a name comparison fails on a German or
# French Windows and passes on a box that renamed a group to match.
_TRUSTED_OWNER_SIDS = frozenset({"S-1-5-32-544", "S-1-5-18"})  # Administrators, SYSTEM

# ``SE_DACL_PROTECTED`` — inheritance has been stripped from the directory's DACL. Without
# it ``C:\ProgramData``'s inherited ``Users:(OI)(CI)(RX)`` is still live, and the
# LocalMachine-sealed delivery secret S-1a-ii writes there would be world-readable.
_SE_DACL_PROTECTED = 0x1000

# ``SE_FILE_OBJECT`` / ``OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION``.
_SE_FILE_OBJECT = 1
_OWNER_AND_DACL_INFORMATION = 0x1 | 0x4
_DACL_INFORMATION = 0x4

# ``ACCESS_ALLOWED_ACE_TYPE`` — the ONLY ACE type the open-group walk may refuse on. A
# DENY ace for Everyone is *more* restrictive, and refusing it would turn a hardening
# measure into a startup failure.
_ACCESS_ALLOWED_ACE_TYPE = 0

# The offset of ``SidStart`` inside ``ACCESS_ALLOWED_ACE`` / ``ACCESS_DENIED_ACE``:
# ``ACE_HEADER`` (AceType + AceFlags + AceSize = 4 bytes) followed by ``Mask`` (4 bytes).
_ACE_SID_OFFSET = 8

# Groups that every (or nearly every) local account lands in. An ALLOW ace for any of
# them on the shared profile means the LocalMachine-sealed delivery secret and the
# admin's trust-bearing settings are readable by a standard user — the exact confidentiality
# boundary D3 says the NTFS DACL is. Compared as SID STRINGS: ``Everyone`` / ``Users`` are
# localised, so a name comparison silently passes on a German Windows.
OPEN_GROUP_SIDS = frozenset(
    {
        "S-1-1-0",  # Everyone
        "S-1-5-11",  # NT AUTHORITY\Authenticated Users
        "S-1-5-32-545",  # BUILTIN\Users
    }
)


class MachineScopeRefusedReason(StrEnum):
    """Why a machine-scoped profile was refused — a BOUNDED vocabulary.

    S-1b's auto-grant screen branches on ``INACCESSIBLE`` (D5: "switch on ∧ access
    denied") and ``--diagnose`` prints the reason. A single untyped ``RuntimeError`` would
    force both to string-match ``str(exc)`` — the fragility ``setup_errors`` exists to
    avoid.
    """

    SWITCH_UNREADABLE = "switch_unreadable"
    MISSING = "missing"
    NOT_A_DIRECTORY = "not_a_directory"
    REPARSE = "reparse"
    FOREIGN_OWNER = "foreign_owner"
    INHERITED_ACL = "inherited_acl"
    INACCESSIBLE = "inaccessible"
    # S-1b-i.1: the explicit open-group ACE walk, promised by S-1a's predicate docstring.
    OPEN_ACE = "open_ace"
    # S-1b-i.2, MEASURED: ``platformdirs`` 4.9.6 consults ``WIN_PD_OVERRIDE_*`` BEFORE
    # ``SHGetKnownFolderPath`` (``platformdirs/windows.py:356-361``), so an unprivileged
    # environment variable can redirect the machine root.
    REDIRECTED = "redirected"


class MachineScopeRefused(RuntimeError):
    """The machine-scope switch is ON but the shared profile cannot be trusted.

    **Never fall through to the per-user profile.** A switch pointing at a missing or
    untrusted directory is a support case, not a fresh install: falling back would give a
    provisioned install two profiles — the admin's settings in one, the nightly writing
    the other — which is precisely the exit-3-every-night split-brain machine scope exists
    to fix. Raised at resolution time and caught at both entry points, which report it.
    """

    def __init__(self, reason: MachineScopeRefusedReason, path: Path | str) -> None:
        self.reason = reason
        self.path = str(path)
        super().__init__(
            f"DistrictSync could not use the shared (machine-scoped) profile: "
            f"{self.path} ({reason.value}). Nothing was read or written — an administrator "
            f"needs to repair the shared folder or the HKLM\\{MACHINE_SCOPE_KEY_PATH} setting."
        )


def bundle_root() -> Path:
    """Return the root of the PyInstaller bundle (or the project root in dev)."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file builds extract to sys._MEIPASS.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Dev layout: src/utils/paths.py -> ../../.. = project root.
    return Path(__file__).resolve().parent.parent.parent


def bundle_config_dir() -> Path:
    """Directory containing bundled read-only config (logging.conf, base mappings)."""
    return bundle_root() / "config"


def bundle_mappings_dir() -> Path:
    """Directory containing built-in mapping YAMLs shipped with the binary."""
    return bundle_config_dir() / "mappings"


def bundle_known_hosts_file() -> Path:
    """Bundled SSH ``known_hosts`` file pinning the SpacesEDU SFTP host keys.

    Read-only bundle asset (shipped via ``--add-data "config;config"``, so it
    rides along with the mappings). The user-writable override lives at
    :func:`user_known_hosts_file` and takes precedence.
    """
    return bundle_config_dir() / "known_hosts"


def user_known_hosts_file() -> Path:
    """Per-user ``known_hosts`` override for pinned SFTP host keys.

    Mirrors the mappings hotfix path: a file dropped here wins over the bundled
    :func:`bundle_known_hosts_file`, so host keys can be added or rotated on a
    district server without shipping a new release.
    """
    return user_data_dir() / "known_hosts"


def app_icon_path() -> Path:
    """Path to the DistrictSync sync-mark ``.ico`` (the EXE/file icon).

    A read-only *bundle* asset (not user-writable), so it resolves against
    ``bundle_root()`` exactly like the config dir: in dev this is
    ``<project root>/assets/districtsync.ico``; in a frozen PyInstaller build it is
    ``<_MEIPASS>/assets/districtsync.ico`` (the file is shipped there via the
    ``flet pack`` ``--add-data "assets;assets"`` arg). The EXE file icon is baked
    from this same asset by ``flet pack --icon`` at build time. Pure — resolves a
    path only. The running WINDOW's icon is :func:`window_icon_path` (the
    myBlueprint mark) — owner decision 2026-07-15: myB on the title bar, the sync
    mark for the app file itself.
    """
    return bundle_root() / "assets" / "districtsync.ico"


def window_icon_path() -> Path:
    """Path to the myBlueprint-mark ``.ico`` (the running window/title-bar/taskbar icon).

    Same bundle-asset resolution as :func:`app_icon_path` (``--add-data "assets;assets"``
    ships it into ``<_MEIPASS>/assets`` in the frozen exe). Sourced from the official
    myB favicon (transparent 16/32/48 layers — native title-bar sizes, no upscaling).
    Pure — resolves a path only; ``shell`` decides whether to set ``page.window.icon``.
    """
    return bundle_root() / "assets" / "myblueprint.ico"


def _platform_data_dir() -> Path:
    """The industry-standard per-OS user-data directory (NO side effects).

    Non-roaming on Windows — correct for the WAL SQLite run store, which must not
    be synced across machines mid-write. Resolves the location ONLY; it never
    creates the directory, so ``migrate_legacy_data_dir()`` can run before anything
    materializes the new location.
    """
    return Path(platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=False))


def _legacy_data_dir() -> Path:
    """The pre-relocation location (``~/.districtsync``) — migration source + fallback.

    ``Path.home()`` lives ONLY here (single source of truth for the legacy anchor).
    """
    return Path.home() / _LEGACY_DIR_NAME


def _override_data_dir() -> Path | None:
    """The ``DISTRICTSYNC_DATA_DIR`` override, or ``None`` when it is not in play.

    A support/test seam, NOT a user setting — the app never writes this variable.
    It exists because a FROZEN exe cannot otherwise be pointed at a throwaway
    profile: ``platformdirs`` resolves the Windows location through
    ``SHGetKnownFolderPath`` and **ignores a ``LOCALAPPDATA`` env var** (verified),
    so redirecting a packed ``DistrictSync.exe`` — for the CI exe smokes, for a
    non-destructive fresh-profile QA walk, or for a support repro on a district
    machine — is impossible without an explicit seam.

    Boundary validation (the value is untrusted operator input): an unset, empty,
    or whitespace-only value means "not in play" (``FOO=`` in a shell must not
    resolve the profile to the process CWD). ``~`` expands (an expanded ``~`` is
    already absolute).

    A RELATIVE value is REFUSED with :class:`ValueError` rather than silently
    absolutized against the CWD. The frozen launcher chdirs into a temp
    ``sys._MEIPASS`` that is deleted on exit, and a scheduled task runs with cwd
    ``%SystemRoot%\\System32`` — so "relative" means the profile lands in a
    directory that is about to vanish, or in a system directory, and the NEXT run
    resolves somewhere else again. Silently absolutizing hides that; refusing makes
    it a one-line fix. Always pass an absolute path.

    Raises:
        ValueError: the value is set but not absolute.
    """
    raw = os.environ.get(_DATA_DIR_ENV_VAR, "").strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"{_DATA_DIR_ENV_VAR} must be an absolute path (got {raw!r})")
    return expanded.resolve()


def machine_data_dir() -> Path:
    """The shared, machine-scoped data directory (``C:\\ProgramData\\DistrictSync``).

    Resolves the location only; it creates nothing and applies nothing. The elevated
    ``provision`` op (plan 0049 S-1b) creates it WITH its owner and DACL in one
    ``CreateDirectoryW`` call; :func:`assert_machine_dir_trusted` is what decides whether
    the app may USE it.

    **It REFUSES while any ``WIN_PD_OVERRIDE_*`` variable is set** (S-1b-i.2). MEASURED on
    ``platformdirs`` 4.9.6: ``get_win_folder`` consults ``WIN_PD_OVERRIDE_<CSIDL>`` before
    ``SHGetKnownFolderPath`` (``platformdirs/windows.py:356-361``). Without this guard an
    unprivileged environment variable redirects the very directory the provisioner
    ``/setowner``s, ACLs, seals a LocalMachine secret into and commits HKLM against — and
    the app would then read its settings from wherever that variable pointed. The refusal
    covers the whole prefix rather than the one CSIDL we happen to use today: a future
    platformdirs could route this call through a different folder id.

    Raises:
        MachineScopeRefused: a ``WIN_PD_OVERRIDE_*`` variable is redirecting the location.
    """
    redirected = sorted(
        name for name, value in os.environ.items() if name.upper().startswith(_PD_OVERRIDE_PREFIX) and value.strip()
    )
    if redirected:
        raise MachineScopeRefused(MachineScopeRefusedReason.REDIRECTED, ", ".join(redirected))
    return Path(platformdirs.site_data_dir(_APP_NAME, appauthor=False))


def _read_machine_switch_value() -> tuple[object, int]:  # pragma: no cover - Windows-only registry read
    """Read ``HKLM\\SOFTWARE\\DistrictSync\\MachineScope`` → ``(value, REG_* type)``.

    The raw syscall seam, isolated so every decision built on it is tested through a
    monkeypatch on every OS. Raises ``FileNotFoundError`` when the key or the value is
    absent (the normal state), and any other ``OSError`` on a real read failure.

    The ``sys.platform`` guard is not defensive — it is what lets a type-checker running on
    Linux skip this body. ``winreg``'s typeshed stubs mark every attribute Windows-only, so
    without it CI's Linux mypy leg fails on ``OpenKey``/``QueryValueEx`` while a local
    Windows run passes. :func:`_machine_switch_on` already returns before calling here off
    Windows, so the raise is unreachable at runtime.
    """
    if sys.platform != "win32":
        raise FileNotFoundError("the machine-scope switch is Windows-only")

    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MACHINE_SCOPE_KEY_PATH, 0, MACHINE_SCOPE_KEY_ACCESS) as key:
        return winreg.QueryValueEx(key, MACHINE_SCOPE_VALUE_NAME)


def _machine_switch_on() -> bool:
    """Whether this computer is provisioned for a SHARED (machine-scoped) profile.

    Three outcomes, deliberately not two:
      * absent key/value → ``False``, SILENTLY. This is every install in the field and a
        deterministic ``FileNotFoundError``; warning about it nightly would be noise.
      * present but not ``REG_DWORD`` ``1`` → ``False`` + ONE warning naming the key. A
        hand-edited ``REG_SZ "1"`` is a mistake worth surfacing, not an instruction.
      * any other ``OSError`` → :class:`MachineScopeRefused`, **not** ``False``. "Any
        exception → off" is the forbidden fall-through moved one step earlier: an
        unreadable switch cannot prove the switch is unset, and on a provisioned install
        that answer silently selects the principal's blank profile.
    """
    if sys.platform != "win32":
        return False
    try:
        value, value_type = _read_machine_switch_value()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MachineScopeRefused(
            MachineScopeRefusedReason.SWITCH_UNREADABLE, f"HKLM\\{MACHINE_SCOPE_KEY_PATH}"
        ) from exc
    if value_type != _REG_DWORD or value != 1:
        logger.warning(
            "HKLM\\%s\\%s is %r (type %s), not REG_DWORD 1 — treating machine scope as OFF",
            MACHINE_SCOPE_KEY_PATH,
            MACHINE_SCOPE_VALUE_NAME,
            value,
            value_type,
        )
        return False
    return True


def machine_switch_on() -> bool:
    """The switch's PUBLIC face — the parent's OWN re-read after an elevated ``provision``.

    Delegates rather than aliases (the :func:`assert_machine_dir_trusted` pattern), so the
    ONE monkeypatch seam every test drives stays :func:`_machine_switch_on`.

    It exists because the post-provision handover may NOT be gated on the child's claim
    (plan 0049 S-1b-ii.1): the child is killed on the bounded wait, so the result file is
    absent on exactly the failures where it may nonetheless have committed. Reading the
    switch ourselves is the only answer that is true in that state.

    Raises:
        MachineScopeRefused: ``SWITCH_UNREADABLE`` — the caller decides, and the handover
            treats "cannot prove it is on" as off rather than renaming a live profile.
    """
    return _machine_switch_on()


def _read_dir_security(path: Path) -> tuple[str, int]:  # pragma: no cover - Windows-only ctypes
    """Return ``(owner SID string, security-descriptor control word)`` for ``path``.

    The raw syscall seam (``GetNamedSecurityInfoW`` + ``ConvertSidToStringSidW`` +
    ``GetSecurityDescriptorControl``), following ``scheduler/elevation.py``'s ctypes
    convention: ``WinDLL(..., use_last_error=True)``, explicit argtypes/restype,
    ``LocalFree`` in a ``finally``. Raises ``OSError`` on any failure — the caller turns
    that into an ``INACCESSIBLE`` refusal, never into a pass.
    """
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    advapi32.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_bool
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetSecurityDescriptorControl.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    owner_sid = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _OWNER_AND_DACL_INFORMATION,
        ctypes.byref(owner_sid),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise OSError(f"GetNamedSecurityInfoW failed for {path} (error {status}).")
    try:
        sid_text = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(owner_sid, ctypes.byref(sid_text)):
            raise OSError(f"ConvertSidToStringSidW failed for {path} (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
        try:
            owner = str(sid_text.value)
        finally:
            kernel32.LocalFree(sid_text)
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        if not advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise OSError(f"GetSecurityDescriptorControl failed for {path} (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
        return owner, int(control.value)
    finally:
        kernel32.LocalFree(descriptor)


def _read_dacl_aces(path: Path) -> tuple[tuple[int, str], ...]:  # pragma: no cover - Windows-only ctypes
    """Return ``((ace type, trustee SID string), …)`` for ``path``'s DACL.

    The second raw-syscall seam, beside :func:`_read_dir_security` — deliberately its own
    call rather than a widened return, because they answer two different questions and ten
    pinned tests drive the first one's shape.

    A **NULL or absent DACL** is reported as one synthetic ``Everyone`` ALLOW entry: both
    mean "no access control at all", and returning an empty tuple would make the open-group
    walk pass the most open state there is.

    Raises ``OSError`` on any failure — the caller turns that into ``INACCESSIBLE``, never
    into a pass. Same ctypes convention as ``scheduler/elevation.py``:
    ``WinDLL(..., use_last_error=True)``, explicit argtypes/restype, ``LocalFree`` in a
    ``finally``.

    The ``sys.platform`` guard makes the off-Windows answer a FAIL-CLOSED ``OSError``
    rather than the ``AttributeError`` ``ctypes.WinDLL`` would otherwise raise — an
    exception type the caller does not catch would escape a security predicate as a crash.
    Machine scope cannot be on off Windows (``_machine_switch_on`` returns False there), so
    this is unreachable at runtime; it is the type-checker's narrowing point and the
    honest shape.
    """
    if sys.platform != "win32":
        raise OSError("Directory ACLs can only be read on Windows.")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    advapi32.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    advapi32.GetAclInformation.restype = ctypes.c_bool
    advapi32.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = ctypes.c_bool
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _DACL_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise OSError(f"GetNamedSecurityInfoW (DACL) failed for {path} (error {status}).")
    try:
        if not dacl:
            # A NULL DACL grants everyone full control. Report it as exactly that.
            return ((_ACCESS_ALLOWED_ACE_TYPE, "S-1-1-0"),)

        class _AclSizeInformation(ctypes.Structure):
            _fields_ = (
                ("AceCount", ctypes.c_uint32),
                ("AclBytesInUse", ctypes.c_uint32),
                ("AclBytesFree", ctypes.c_uint32),
            )

        info = _AclSizeInformation()
        # 2 == AclSizeInformation.
        if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise OSError(f"GetAclInformation failed for {path} (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]

        aces: list[tuple[int, str]] = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise OSError(f"GetAce({index}) failed for {path} (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
            ace_type = ctypes.cast(ace, ctypes.POINTER(ctypes.c_uint8))[0]
            sid_ptr = ctypes.c_void_p((ace.value or 0) + _ACE_SID_OFFSET)
            sid_text = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_text)):
                raise OSError(f"ConvertSidToStringSidW failed for {path} (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
            try:
                aces.append((int(ace_type), str(sid_text.value)))
            finally:
                kernel32.LocalFree(sid_text)
        return tuple(aces)
    finally:
        kernel32.LocalFree(descriptor)


def assert_no_open_aces(path: Path) -> None:
    """Raise ``MachineScopeRefused(OPEN_ACE)`` if any open group can reach ``path``.

    The explicit walk S-1a's predicate docstring promised. ``SE_DACL_PROTECTED`` proves
    inheritance was stripped; it does NOT prove the resulting DACL is closed — an admin who
    hand-creates the folder, or an ``icacls /grant`` that resolved oddly, can leave
    ``Users:(OI)(CI)(RX)`` on a protected, Administrators-owned directory. That directory
    holds a LocalMachine-sealed delivery password whose only confidentiality boundary IS
    this DACL (D3).

    Only **ALLOW** aces count (:data:`_ACCESS_ALLOWED_ACE_TYPE`) — a DENY ace for Everyone
    is a hardening measure, and refusing it would invert the check.

    Fails closed: an unreadable DACL is ``INACCESSIBLE``, never a pass.
    """
    try:
        aces = _read_dacl_aces(path)
    except OSError as exc:
        raise MachineScopeRefused(MachineScopeRefusedReason.INACCESSIBLE, path) from exc
    for ace_type, sid in aces:
        if ace_type == _ACCESS_ALLOWED_ACE_TYPE and sid in OPEN_GROUP_SIDS:
            raise MachineScopeRefused(MachineScopeRefusedReason.OPEN_ACE, path)


def assert_machine_dir_trusted(path: Path) -> None:
    """The trust predicate's PUBLIC face — used by the elevated provisioner's verify step.

    Delegates rather than aliases, so ``_assert_machine_dir_trusted`` stays the ONE
    monkeypatch seam every existing test (and this module's own resolver) drives: a patch
    on the private name is honoured through this call too.
    """
    _assert_machine_dir_trusted(path)


def _assert_machine_dir_trusted(path: Path) -> None:
    """Raise :class:`MachineScopeRefused` unless ``path`` is a profile we may trust.

    All of: it exists and is a directory · it is not a reparse point · its OWNER is
    ``BUILTIN\\Administrators`` or ``SYSTEM`` · DACL inheritance is disabled
    (``SE_DACL_PROTECTED``).

    **Fails closed** — any failure to read the ownership or the control word is
    ``INACCESSIBLE``, never a pass. Ownership is the fact that separates a provisioned
    profile from a planted one: ``C:\\ProgramData``'s default ACL lets ANY standard user
    create and own a subdirectory there. Inheritance is checked here, in the slice before
    the one that writes a credential, because an admin who hand-creates the directory
    passes owner-and-reparse with ``Users:(OI)(CI)(RX)`` still inherited.

    Reparse is checked BEFORE the directory test only so a directory symlink reports the
    precise reason; both outcomes are a refusal. The explicit open-group ACE walk
    (:func:`assert_no_open_aces`) runs LAST, so the cheap ``lstat`` rungs still report the
    precise reason for a missing or planted directory before any DACL is walked.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise MachineScopeRefused(MachineScopeRefusedReason.MISSING, path) from exc
    except OSError as exc:
        raise MachineScopeRefused(MachineScopeRefusedReason.INACCESSIBLE, path) from exc

    if getattr(info, "st_reparse_tag", 0) != 0:
        raise MachineScopeRefused(MachineScopeRefusedReason.REPARSE, path)
    if not stat.S_ISDIR(info.st_mode):
        raise MachineScopeRefused(MachineScopeRefusedReason.NOT_A_DIRECTORY, path)

    try:
        owner_sid, control = _read_dir_security(path)
    except OSError as exc:
        raise MachineScopeRefused(MachineScopeRefusedReason.INACCESSIBLE, path) from exc

    if owner_sid not in _TRUSTED_OWNER_SIDS:
        raise MachineScopeRefused(MachineScopeRefusedReason.FOREIGN_OWNER, path)
    if not control & _SE_DACL_PROTECTED:
        raise MachineScopeRefused(MachineScopeRefusedReason.INHERITED_ACL, path)
    assert_no_open_aces(path)


def _user_scope_data_dir(*, create: bool) -> Path:
    """Persistent PER-USER data directory — the ladder every install has always used.

    Extracted verbatim from the pre-0049 ``user_data_dir()`` body, which now sits behind
    the machine-scope decision (see :func:`user_data_dir` for the full contract).

    ``create`` is REQUIRED and undefaulted. ``create=False`` is the non-creating
    resolution :func:`handshake_dir` needs: that resolver runs unconditionally at both
    entry points, so a creating one would have every nightly under a service principal
    materialise an empty second profile. Keeping ONE resolver (rather than a second,
    near-identical ladder) also keeps ONE test-isolation seam.

    Raises:
        ValueError: the override is set but not absolute (see :func:`_override_data_dir`).
        RuntimeError: the override is set but unusable as a directory (``create`` only).
    """
    override = _override_data_dir()
    if override is not None:
        if create:
            try:
                override.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"{_DATA_DIR_ENV_VAR}={override} could not be used as the profile directory "
                    f"({exc}). Unset it or point it at a writable absolute path."
                ) from exc
        return override
    new = _platform_data_dir()
    if new.exists():
        return new
    legacy = _legacy_data_dir()
    if legacy.exists():
        return legacy
    if create:
        new.mkdir(parents=True, exist_ok=True)
    return new


# The resolved profile, decided ONCE per process: ``(path, machine_scope)``. Path and scope
# are decided in one breath so they can never disagree, and the answer cannot change
# mid-run (a mid-run flip would split a single run's writes across two profiles).
# Resolution is idempotent, so a rare double-resolve from two threads is harmless.
_PIN: tuple[Path, bool] | None = None


def _pinned() -> tuple[Path, bool]:
    """Resolve (once) and return ``(profile path, machine scope)``.

    Ladder: ``DISTRICTSYNC_DATA_DIR`` wins outright and is **never** machine scope (it is a
    support/test seam pointed at a throwaway, un-ACL'd directory) → the HKLM switch, whose
    directory must pass :func:`_assert_machine_dir_trusted` or the whole resolution is
    REFUSED → the per-user ladder. A refusal leaves the pin unset, so the next caller
    re-decides rather than inheriting a latched failure.
    """
    global _PIN
    if _PIN is None:
        if _override_data_dir() is not None:
            _PIN = (_user_scope_data_dir(create=True), False)
        elif _machine_switch_on():
            shared = machine_data_dir()
            _assert_machine_dir_trusted(shared)
            _PIN = (shared, True)
        else:
            _PIN = (_user_scope_data_dir(create=True), False)
    return _PIN


def is_machine_scope() -> bool:
    """Whether this install reads and writes the SHARED machine-scoped profile.

    The ONE predicate consumers branch on (S-1a-ii's secret-store selection, S-2's copy).
    Resolves the pin if it is not yet set, so it can never disagree with
    :func:`user_data_dir`.
    """
    return _pinned()[1]


def pin_data_dir() -> Path:
    """Force resolution NOW, log the answer, and return the profile directory.

    Called at both entry points (``main._cli``, ``ui_flet/launcher.main``) BEFORE the log
    sink is configured — the sink's own path depends on this answer, and a
    :class:`MachineScopeRefused` must be reported rather than escape as a traceback into a
    stderr the Task Scheduler discards.
    """
    path, machine = _pinned()
    logger.info("DistrictSync data dir: %s (machine scope: %s)", path, "yes" if machine else "no")
    return path


def reset_data_dir_pin() -> None:
    """Forget the resolved profile (tests; S-1b's post-provision re-pin)."""
    global _PIN
    _PIN = None


def per_user_data_dir() -> Path:
    """The PER-USER profile root, resolved WITHOUT consulting the machine-scope switch.

    Non-creating. Two callers, both of which need the per-user answer specifically rather
    than "wherever this install reads its settings":

    * :func:`handshake_dir` — the elevation blobs must stay per-user in every scope;
    * the elevated ``provision`` op — it MIGRATES this directory, and it re-derives the
      answer itself so it can refuse a payload that names a different one (a request file
      is attacker-influencable, and the child is the privileged half).

    Public because ``src/`` has no precedent for one module reaching into another's
    privates, and a security-relevant resolution is the wrong place to start.
    """
    return _user_scope_data_dir(create=False)


def handshake_dir() -> Path:
    """The PER-USER directory for elevation handshake files — in EVERY scope, non-creating.

    The request/result blobs are a per-session, CurrentUser-DPAPI artefact: on a
    machine-scoped install the profile is shared and ``runs/`` grants the service principal
    Modify, so they must not follow the profile into a directory another principal can
    write. It also stays resolvable when :func:`user_data_dir` REFUSES, which is what lets
    both entry points report a machine-scope refusal into a real log file.

    Creates nothing — :func:`src.scheduler.elevation.write_request` /
    :func:`~src.scheduler.elevation.new_result_path` mkdir explicitly, and
    ``sweep_orphans`` returns 0 when the directory is absent.
    """
    return per_user_data_dir()


def user_data_dir() -> Path:
    """The resolved data directory (logs, custom mappings, app config, run store).

    Machine scope (plan 0049 D0) is decided FIRST and ONCE per process — see
    :func:`_pinned` for the ladder and :func:`is_machine_scope` for the predicate. With the
    switch off (every install in the field) this is exactly the per-user ladder it has
    always been:
      0. ``DISTRICTSYNC_DATA_DIR`` (see :func:`_override_data_dir`) — when set it
         **wins outright**: the entire profile lives there, with NO legacy fallback
         and NO migration (``migrate_legacy_data_dir`` is a no-op while it is set,
         so the resolver and the migration can never disagree about the location),
         else
      1. the platform-standard dir if it already exists (fresh install here, or a
         completed migration), else
      2. the legacy ``~/.districtsync`` dir if it exists (pre-migration, or a
         migration that safely fell back), else
      3. create + return the platform-standard dir (a brand-new install).

    Step 0 creates the directory for the same reason step 3 does: the override
    names where the profile *is*, and the log sink opens a file in it immediately.
    The startup banner (``utils/version.startup_banner``) logs the RESOLVED dir on
    every entry, so which step won is always diagnosable from the log.

    The move from (2) to (1) is an explicit, failure-safe entry-point step
    (``migrate_legacy_data_dir``) — NOT a side effect of this resolver — so a read
    can never half-move data.

    Raises:
        ValueError: the override is set but not absolute (see :func:`_override_data_dir`).
        RuntimeError: the override is set but unusable as a directory. Fail LOUD rather
            than fall through to the platform dir — a silent fallback would write the
            profile somewhere the operator did not ask for and did not know to look,
            which is precisely the confusion the override exists to remove.
        MachineScopeRefused: (a ``RuntimeError``) the machine-scope switch is ON but the
            shared directory is missing or untrusted. Never falls back to the per-user
            profile — see :class:`MachineScopeRefused`.
    """
    return _pinned()[0]


def _write_moved_breadcrumb(legacy: Path, new: Path) -> None:
    """Drop a ``MOVED.txt`` breadcrumb in the legacy dir (best-effort; never raises).

    Written only AFTER a successful promote, so a breadcrumb failure cannot affect
    the migration outcome — the new location is already live and complete.
    """
    try:
        (legacy / _MOVED_BREADCRUMB).write_text(
            "DistrictSync moved its data on "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} to:\n"
            f"{new}\n\n"
            f"This folder ({legacy}) is no longer used by DistrictSync and is safe "
            "to delete. Your configuration, logs, and run history now live in the "
            "location above.\n",
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - cosmetic; migration already succeeded
        logger.warning("Could not write migration breadcrumb in %s (%s)", legacy, exc)


def write_moved_breadcrumb(superseded: Path, live: Path) -> None:
    """Public face of the ``MOVED.txt`` breadcrumb — the PUBLIC face, not a second spelling.

    Delegates to :func:`_write_moved_breadcrumb` (the same delegate pattern
    :func:`assert_machine_dir_trusted` uses) so the machine-scope handover
    (:mod:`src.scheduler.provision_session`) drops the same file, with the same words, as
    the legacy migration — and so a test that patches the private name still sees this
    call. Best-effort; never raises.
    """
    _write_moved_breadcrumb(superseded, live)


def profile_superseded(directory: Path) -> bool:
    """Whether a ``MOVED.txt`` breadcrumb marks ``directory`` as a profile we LEFT.

    **The fence** (plan 0049 S-1b-ii.1, amendment 2). After provisioning copies the
    per-user profile into the shared one, the per-user ``config.json`` and ``history.db``
    are renamed aside — but a rename alone fences nothing: :meth:`AppConfig.load` maps
    ``FileNotFoundError`` to defaults with no log, and both :meth:`AppConfig.save` and the
    run store's ``_open`` recreate what they cannot find. A process still pinned to the
    per-user profile (a second window, or a nightly that started before the handover)
    would therefore write a brand-new ORPHAN profile there, silently, and every edit in it
    would be invisible to the shared install. The two writers consult this first and refuse.

    ``MOVED.txt`` beside a live profile unambiguously means "superseded": the legacy
    migration writes its breadcrumb into the dir it LEFT, and the resolver never returns a
    legacy dir once the platform one exists.

    That sentence is only true because :func:`migrate_legacy_data_dir` EXCLUDES the
    breadcrumb from the tree it copies — the two halves are coupled, so do not relax
    either alone. Without the exclusion a SECOND migration (platform dir lost, legacy dir
    intact) copies the old breadcrumb into the fresh profile and fences it: the app then
    refuses every settings write, with no in-app way out.

    Total by construction — ``Path.is_file()`` answers ``False`` rather than raising on an
    unreadable directory. That direction is deliberate: a transient stat failure must not
    start refusing every settings write on an install that was never provisioned.
    """
    return (directory / _MOVED_BREADCRUMB).is_file()


def _override_suppresses_migration() -> bool:
    """Whether ``DISTRICTSYNC_DATA_DIR`` should suppress the legacy migration.

    NARROW by design: only an override pointing SOMEWHERE ELSE suppresses. An override
    aimed AT the canonical platform dir resolves to the exact location the migration
    targets, so there is no split-brain to prevent — suppressing there would strand
    ``~/.districtsync`` forever behind a variable that changed nothing.

    Never raises. :func:`migrate_legacy_data_dir` documents a never-raises contract and
    is called unconditionally at entry, while :func:`_override_data_dir` deliberately
    fails LOUD (``ValueError`` on a relative value; ``Path.expanduser`` raises
    ``RuntimeError`` for an unknown ``~user`` on POSIX). An unresolvable value is
    treated as unset HERE and still fails loud at :func:`user_data_dir` — the boundary
    that actually decides where data goes.
    """
    try:
        override = _override_data_dir()
        if override is None:
            return False
        canonical = _platform_data_dir().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(
            "%s could not be resolved (%s) — treating it as unset for the legacy migration",
            _DATA_DIR_ENV_VAR,
            exc,
        )
        return False
    if override == canonical:
        return False
    logger.debug("%s is set — skipping the legacy app-data migration", _DATA_DIR_ENV_VAR)
    return True


def migrate_legacy_data_dir() -> bool:
    """Relocate ``~/.districtsync`` to the platform data dir once, failure-safely.

    Mechanism — **stage-then-atomic-promote**, chosen precisely so a mid-migration
    failure can neither strand nor lose data:

      1. Run only when the legacy dir exists AND the new dir does not. This makes
         the call idempotent — a no-op on a fresh install or an already-migrated
         machine (the common case at every startup: one cheap ``exists()`` check).
      2. COPY the entire legacy tree — ``config.json``, ``etl_tool.log`` + its
         rotations, the ``mappings/`` dir, and ``history.db`` together with its
         ``-wal``/``-shm`` sidecars, as one unit — into a fresh staging dir under
         the NEW dir's *parent*. Same filesystem as the final location (so the
         promote is atomic), while the copy itself tolerates a cross-device
         home→appdata layout. EXCEPT ``MOVED.txt``, which is excluded: step 4
         leaves one behind and step 1 never deletes the legacy dir, so a dir
         migrated once carries a breadcrumb forever. Copying it forward would hand
         the destination a supersede fence (:func:`profile_superseded`) and make
         every settings write refuse. Reachable whenever the platform dir is later
         lost while the legacy dir survives — an IT profile reset, a roaming-profile
         rebuild, or a support "delete the folder and retry" — because that is
         exactly the state step 1 re-arms on.
      3. Promote the fully-staged copy with a single ``os.replace``: the new dir
         becomes "live" only once EVERY file has copied. If any copy fails first,
         the new dir is never created, the staging copy is discarded, and the legacy
         dir stays fully intact and live — ``user_data_dir()`` keeps returning it,
         so a partial migration is invisible and no data is lost.
      4. Leave a ``MOVED.txt`` breadcrumb in the legacy dir. Legacy files are
         deliberately left in place (this is a copy, never a move/delete), so there
         is no window in which the only copy of the data is in flight.

    Returns ``True`` iff data was migrated in THIS call; ``False`` when there was
    nothing to migrate OR the migration failed and we safely fell back to the legacy
    location (logged WARNING). Never raises — safe to call unconditionally at entry.

    A ``DISTRICTSYNC_DATA_DIR`` pointing ELSEWHERE makes this a **no-op**: this function
    resolves ``_platform_data_dir()``/``_legacy_data_dir()`` directly (it deliberately
    does not go through :func:`user_data_dir`), so without the guard an overridden run
    would migrate the legacy tree into the *platform* dir while reading its profile from
    the *override* — a split-brain the override exists to prevent. An override pointing
    AT the platform dir is NOT suppressed (see :func:`_override_suppresses_migration`).
    """
    if _override_suppresses_migration():
        return False

    new = _platform_data_dir()
    legacy = _legacy_data_dir()

    # Idempotent, fail-safe guard: only the legacy-exists-and-new-does-not state
    # warrants a migration. Every other state (fresh install, already migrated,
    # a prior safe fallback) is a no-op.
    if new.exists() or not legacy.exists():
        return False

    staging: Path | None = None
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{new.name}.migrating-", dir=new.parent))
        # Copy the whole tree into staging; promote only when it fully succeeds.
        # NEVER carry a MOVED.txt forward (see step 2 of the docstring). The legacy dir is
        # a copy SOURCE that is never deleted, so one migrated once keeps its breadcrumb
        # for good; copying it into the destination would make `profile_superseded(new)`
        # true and fence every settings write on a profile that was only ever migrated.
        shutil.copytree(
            legacy,
            staging,
            dirs_exist_ok=True,
            copy_function=shutil.copy2,
            ignore=shutil.ignore_patterns(_MOVED_BREADCRUMB),
        )
        # Windows AV/indexers can briefly hold a freshly-written directory, failing
        # the promote with a transient Access-denied — retry a couple of times
        # before falling back (the fallback itself stays safe either way).
        for attempt in range(3):
            try:
                os.replace(staging, new)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        staging = None  # promoted — must NOT be cleaned up in the except path
    except (OSError, shutil.Error) as exc:
        # `new` can exist here despite the entry guard: a concurrent process may
        # have promoted its own staging first (our os.replace then fails) — in
        # that case this process continues on the winner's complete copy.
        logger.warning(
            "Legacy app-data migration to %s failed (%s); data is intact — continuing to use %s",
            new,
            exc,
            new if new.exists() else legacy,
        )
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        return False

    _write_moved_breadcrumb(legacy, new)
    logger.info("Migrated DistrictSync data from %s to %s", legacy, new)
    return True


def user_mappings_dir() -> Path:
    """Per-user directory for district mapping overrides and custom configs."""
    path = user_data_dir() / "mappings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_log_file() -> Path:
    """Canonical log-file path, shared by CLI, wizard, and scheduled runs.

    Per-user (every install in the field): ``<profile>/etl_tool.log`` — unchanged.

    Machine-scoped: ``<profile>/runs/etl_tool-<sanitised account>.log``. The name is
    per-WRITER because a shared profile has two of them — the admin's session and the
    nightly's service principal — and two processes on one ``RotatingFileHandler`` tear
    each other's rotations apart. ``runs/`` is the only subtree the principal may write.
    """
    base = user_data_dir()
    if is_machine_scope():
        return base / MACHINE_RUNS_SUBDIR / f"etl_tool-{sanitise_account_for_filename(process_account())}.log"
    return base / "etl_tool.log"


def user_history_db() -> Path:
    """Canonical run-history SQLite store path (consumed by the run store, Slice 4b).

    Resolves through ``user_data_dir()`` at call time — never a module-level
    constant — so the test-isolation seam redirects it too (a store keyed off an
    import-time path would write the real ``history.db`` from every pipeline test).

    Machine-scoped installs put it under ``runs/`` (with its WAL sidecars), so every
    principal's runs land in ONE ledger that Run History can read — the gap plan 0046
    Slice C could only document.
    """
    return history_db_in(user_data_dir(), machine_scope=is_machine_scope())


def history_db_in(root: Path, *, machine_scope: bool) -> Path:
    """Where the run store lives inside a profile ``root`` — the ONE layout rule.

    Extracted so the elevated provisioner can assert its migrated ``history.db`` landed
    exactly where :func:`user_history_db` will look for it, WITHOUT re-spelling the rule.
    Step 6 of ``provision`` exists to catch a directory the app would later reject; a
    second spelling of the layout is precisely the drift it could not catch.

    ``machine_scope`` is REQUIRED and undefaulted: a defaulted value here silently picks
    a profile layout, which is the class of mistake this plan exists to remove.
    """
    return root / MACHINE_RUNS_SUBDIR / RUN_STORE_NAME if machine_scope else root / RUN_STORE_NAME
