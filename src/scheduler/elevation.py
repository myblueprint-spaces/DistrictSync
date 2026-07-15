"""Generic Windows per-operation elevation IPC primitive (Plan 0029, D5).

This module contains **no Task-Scheduler logic** — it is the reusable privilege-
elevation plumbing that ``src/scheduler/windows.py`` consumes to run one bounded
elevated PowerShell operation behind a single UAC prompt, without the app itself
ever running as administrator.

**Security model — where the secret lives at each hop (audited end-to-end):**

  - **Inbound (parent → elevated child), password-bearing.** The caller's payload
    (which may contain a Windows account password) is JSON-encoded and sealed with
    **DPAPI at CurrentUser scope** (:func:`protect_blob`) plus a fixed app-scoped
    optional-entropy constant, then written to a random-named ``dsync_elev_*.req``
    file under :func:`~src.utils.paths.user_data_dir` with an explicit owner-only
    DACL. **CurrentUser scope is the confidentiality boundary:** only the same
    user SID can decrypt the blob — an over-the-shoulder UAC consent under a
    *different* administrator account literally cannot decrypt it, so the child
    **fails closed**. LocalMachine scope is NEVER used (it would let any account on
    the box decrypt, downgrading a domain credential's confidentiality). The
    password therefore rides ONLY the DPAPI-sealed file — never argv (ShellExecuteEx
    gets only the encoded bootstrap + paths), never the parent's environment,
    never a log.

  - **Outbound (child → parent), no secret.** The child writes a **plaintext** JSON
    result (``{ok, message}``) **atomically** (temp + rename). It carries a
    sanitized ok/message only — never the password — so it does not need DPAPI
    (encrypting it would add nothing; panel-rejected). :func:`read_result` reads it
    defensively (missing / partial / unparseable → ``None``, never raises).

  - **Launch + wait.** :func:`run_elevated_powershell` uses ``ShellExecuteExW`` with
    ``lpVerb="runas"`` on the **absolute System32 WindowsPowerShell v1.0
    powershell.exe path** (elevation raises the stakes of a PATH hijack — never a
    bare ``"powershell"``), ``SEE_MASK_NOCLOSEPROCESS`` + ``SW_HIDE``, then a
    **bounded** ``WaitForSingleObject`` (never ``INFINITE``; on timeout the child is
    terminated) and ``GetExitCodeProcess``; the handle is always closed. Outcomes:
    ``LAUNCH_FAILED`` / ``DECLINED`` (``ERROR_CANCELLED`` 1223) / ``TIMEOUT`` /
    ``COMPLETED(exit_code)``. UAC-success is only ever *confirmed* by the caller
    reading the real task back — never assumed from an exit code.

  - **Hygiene.** :func:`sweep_orphans` best-effort deletes stale handshake files at
    both app entry points, so a crash between write and cleanup can't leave a
    lingering (DPAPI-sealed, SID-locked) request file around.

All Windows-only ``ctypes`` calls execute only under a ``sys.platform == "win32"``
guard (and are individually mockable seams), so the module imports and its
orchestration is unit-tested on any platform; the real DPAPI round-trip is a
Windows-only, UAC-free test.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import secrets
import subprocess  # nosec B404 - icacls (owner-only DACL) is a trusted System32 binary
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.utils import paths
from src.utils.helpers import subprocess_no_window_flags

logger = logging.getLogger(__name__)

# Fixed app-scoped optional entropy for DPAPI. NOT a secret — it is a namespacing /
# tamper-binding value that BOTH sides (Python here + the PowerShell child) must
# supply identically. Exposed as the UTF-8 string so the child can rebuild the exact
# same bytes via ``[Text.Encoding]::UTF8.GetBytes(...)`` (single source of the value).
DPAPI_ENTROPY_UTF8 = "DistrictSync/elevation/v1"
_DPAPI_ENTROPY = DPAPI_ENTROPY_UTF8.encode("utf-8")

# Random-named handshake files live under user_data_dir with this prefix so the
# startup sweep can find orphans and so they are excluded from any *.csv delivery glob.
_HANDSHAKE_PREFIX = "dsync_elev_"

# The child result is a tiny sanitized {ok, message} JSON. Cap the read so a corrupt /
# runaway file can never be slurped whole — anything larger is treated as unparseable (None).
_MAX_RESULT_BYTES = 64 * 1024

# DPAPI: no UI ever, CurrentUser scope (the default — LocalMachine 0x4 is NEVER set).
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

# ShellExecuteEx / process-wait constants.
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_ERROR_CANCELLED = 1223  # user declined the UAC prompt
_WAIT_TIMEOUT = 0x00000102


class ElevationResult(Enum):
    """The bounded outcome of a single elevated launch (never a raw win32 code)."""

    COMPLETED = "completed"  # the child ran to exit — inspect exit_code / read the result file
    DECLINED = "declined"  # ShellExecuteEx returned ERROR_CANCELLED (1223) — user said No
    TIMEOUT = "timeout"  # the child did not exit within the bounded wait — terminated
    LAUNCH_FAILED = "launch_failed"  # ShellExecuteEx failed for another reason / non-Windows


@dataclass(frozen=True)
class ElevationOutcome:
    """The result of :func:`run_elevated_powershell` — a bounded state, never a raw error."""

    result: ElevationResult
    exit_code: int | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# DPAPI (CurrentUser scope) — the inbound password-bearing channel.            #
# --------------------------------------------------------------------------- #


class _DataBlob(ctypes.Structure):
    """Win32 ``DATA_BLOB`` — a length-prefixed byte buffer for the DPAPI APIs."""

    _fields_ = (("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char)))


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    """Wrap ``data`` in a ``DATA_BLOB``; the returned buffer must be kept alive by the caller."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _dpapi(func_name: str, data: bytes, entropy: bytes) -> bytes:  # pragma: no cover - Windows-only ctypes
    """Call ``CryptProtectData`` / ``CryptUnprotectData`` at CurrentUser scope.

    Raises ``OSError`` on any API failure (a wrong-entropy or cross-SID unprotect
    returns FALSE → we raise, so the caller fails closed). Never widens to
    LocalMachine scope. The plaintext buffer the API allocates is ``LocalFree``'d.
    """
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    func = getattr(crypt32, func_name)
    func.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    func.restype = ctypes.c_bool

    in_blob, _in_buf = _to_blob(data)
    ent_blob, _ent_buf = _to_blob(entropy)
    out_blob = _DataBlob()
    ok = func(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        ctypes.c_uint32(_CRYPTPROTECT_UI_FORBIDDEN),
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(f"{func_name} failed (error {ctypes.get_last_error()}).")  # type: ignore[attr-defined]
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
    return raw


def protect_blob(data: bytes) -> bytes:  # pragma: no cover - thin Windows-only wrapper
    """DPAPI-seal ``data`` at CurrentUser scope with the app entropy. Never logs contents."""
    return _dpapi("CryptProtectData", data, _DPAPI_ENTROPY)


def unprotect_blob(data: bytes) -> bytes:  # pragma: no cover - thin Windows-only wrapper
    """DPAPI-unseal ``data`` (CurrentUser scope, app entropy). Raises on cross-SID / tamper."""
    return _dpapi("CryptUnprotectData", data, _DPAPI_ENTROPY)


# --------------------------------------------------------------------------- #
# Request / result handshake files.                                           #
# --------------------------------------------------------------------------- #


def _current_user() -> str:
    """The current account (``DOMAIN\\user`` when available) for the owner-only DACL grant."""
    domain = os.environ.get("USERDOMAIN", "")
    username = os.environ.get("USERNAME", "")
    if domain and username:
        return f"{domain}\\{username}"
    if username:
        return username
    import getpass

    return getpass.getuser()


def _set_owner_only_dacl(path: Path) -> None:  # pragma: no cover - Windows-only icacls call
    """Restrict ``path`` to the current user only (defense-in-depth).

    Chosen mechanism = ``icacls`` (a trusted System32 binary, auditable, no new
    dependency, shell=False) over a large ``SetNamedSecurityInfo`` ctypes dance,
    **because the DPAPI CurrentUser encryption is the real confidentiality boundary**
    — another account cannot decrypt the blob even with raw byte access. The DACL
    only additionally restricts read access on a shared box, so it is best-effort:
    an ``icacls`` failure is logged, never fatal (DPAPI still guarantees secrecy).
    """
    if sys.platform != "win32":
        return
    owner = _current_user()
    try:
        result = subprocess.run(  # nosec B603,B607 - trusted System32 binary, our own file, shell=False
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{owner}:F"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=subprocess_no_window_flags(),  # no console flash in the windowed exe
        )
        if result.returncode != 0:
            logger.warning(
                "Could not set owner-only ACL on the elevation request file "
                "(icacls exit %s); DPAPI CurrentUser encryption remains the boundary.",
                result.returncode,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "Could not set owner-only ACL on the elevation request file (%s); "
            "DPAPI CurrentUser encryption remains the boundary.",
            exc,
        )


def write_request(payload: dict[str, object]) -> Path:
    """Seal ``payload`` (may contain a password) into a random DPAPI-encrypted request file.

    Returns the request file path. The bytes on disk are DPAPI-opaque (CurrentUser
    scope + app entropy) — NOT plaintext-readable — and the file is given an
    owner-only DACL. The caller deletes it after the elevated operation.
    """
    raw = json.dumps(payload).encode("utf-8")
    protected = protect_blob(raw)
    path = paths.user_data_dir() / f"{_HANDSHAKE_PREFIX}{secrets.token_hex(16)}.req"
    path.write_bytes(protected)
    _set_owner_only_dacl(path)
    return path


def new_result_path() -> Path:
    """Reserve (do NOT create) a random result-file path the elevated child will write."""
    return paths.user_data_dir() / f"{_HANDSHAKE_PREFIX}{secrets.token_hex(16)}.res"


def read_result(path: Path) -> dict[str, object] | None:
    """Read the child's plaintext JSON result. Missing / partial / unparseable → ``None``; never raises.

    The child writes the result atomically (temp + rename), so a partially-written
    file is never observed under ``path``; a defensive parse still degrades any
    surprise to ``None`` rather than raising.
    """
    try:
        if not path.exists():
            return None
        if path.stat().st_size > _MAX_RESULT_BYTES:
            return None  # oversized → treat as unparseable rather than slurp it whole
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def sweep_orphans(max_age_s: float = 3600.0) -> int:
    """Best-effort delete stale ``dsync_elev_*`` handshake files (older than ``max_age_s``).

    Called at both app entry points so a crash between write and cleanup cannot
    leave a lingering (SID-locked, DPAPI-sealed) request file around. Fresh files
    (an in-flight handshake) are left untouched. Never raises — returns the count deleted.
    """
    try:
        directory = paths.user_data_dir()
        candidates = list(directory.glob(f"{_HANDSHAKE_PREFIX}*"))
    except OSError:
        return 0
    now = time.time()
    removed = 0
    for candidate in candidates:
        try:
            if now - candidate.stat().st_mtime > max_age_s:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --------------------------------------------------------------------------- #
# Elevated launch — ShellExecuteExW("runas") on the absolute System32 path.    #
# --------------------------------------------------------------------------- #


def _system_powershell_path() -> str:
    """The ABSOLUTE System32 WindowsPowerShell v1.0 powershell.exe path.

    Never a bare ``"powershell"``: elevation raises the stakes of a PATH hijack, so
    the elevated child is pinned to the in-box Windows PowerShell by full path.
    """
    # os.environ is case-insensitive on Windows, so the canonical SYSTEMROOT key resolves.
    system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


class _ShellExecuteInfoW(ctypes.Structure):
    """Win32 ``SHELLEXECUTEINFOW`` (the hIcon/hMonitor union collapsed to one handle field)."""

    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    )


def _kernel32() -> ctypes.CDLL:  # pragma: no cover - Windows-only DLL handle
    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def _shell_execute_runas(file: str, params: str) -> tuple[int, int]:  # pragma: no cover - Windows-only ctypes
    """Show the UAC prompt and start ``file params`` elevated, hidden.

    Returns ``(hProcess, 0)`` on success (the child process handle, kept open via
    ``SEE_MASK_NOCLOSEPROCESS``) or ``(0, GetLastError())`` on failure — a
    ``GetLastError()`` of 1223 means the user declined the prompt.
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfoW)]
    shell32.ShellExecuteExW.restype = ctypes.c_bool

    sei = _ShellExecuteInfoW()
    sei.cbSize = ctypes.sizeof(_ShellExecuteInfoW)
    sei.fMask = _SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = file
    sei.lpParameters = params
    sei.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return 0, ctypes.get_last_error()  # type: ignore[attr-defined]
    return int(sei.hProcess or 0), 0


def _wait_for_process(handle: int, timeout_ms: int) -> int:  # pragma: no cover - Windows-only ctypes
    """Bounded ``WaitForSingleObject`` — returns ``WAIT_TIMEOUT`` (0x102) on timeout."""
    kernel32 = _kernel32()
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    return int(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), ctypes.c_uint32(timeout_ms)))


def _get_exit_code(handle: int) -> int:  # pragma: no cover - Windows-only ctypes
    kernel32 = _kernel32()
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    code = ctypes.c_uint32(0)
    kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
    return int(code.value)


def _terminate_process(handle: int) -> None:  # pragma: no cover - Windows-only ctypes
    kernel32 = _kernel32()
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess(ctypes.c_void_p(handle), ctypes.c_uint32(1))


def _close_handle(handle: int) -> None:  # pragma: no cover - Windows-only ctypes
    kernel32 = _kernel32()
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(ctypes.c_void_p(handle))


def run_elevated_powershell(encoded_command: str, *, timeout_s: float) -> ElevationOutcome:
    """Run one ``powershell.exe -EncodedCommand <b64>`` elevated behind a single UAC prompt.

    ``encoded_command`` is the UTF-16LE-base64 script blob (built by the caller); it
    carries NO secret (the secret rides the DPAPI request file the script reads).
    The launch is pinned to the absolute System32 powershell.exe, hidden, with a
    bounded wait (never ``INFINITE``). Never raises — maps every path to a bounded
    :class:`ElevationOutcome`.
    """
    if sys.platform != "win32":
        return ElevationOutcome(ElevationResult.LAUNCH_FAILED, error="Elevation is only available on Windows.")

    powershell = _system_powershell_path()
    params = f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded_command}"

    handle, err = _shell_execute_runas(powershell, params)
    if not handle:
        if err == _ERROR_CANCELLED:
            return ElevationOutcome(ElevationResult.DECLINED)
        return ElevationOutcome(ElevationResult.LAUNCH_FAILED, error=f"ShellExecuteEx failed (error {err}).")

    try:
        wait = _wait_for_process(handle, int(timeout_s * 1000))
        if wait == _WAIT_TIMEOUT:
            _terminate_process(handle)
            return ElevationOutcome(ElevationResult.TIMEOUT)
        return ElevationOutcome(ElevationResult.COMPLETED, exit_code=_get_exit_code(handle))
    finally:
        _close_handle(handle)
