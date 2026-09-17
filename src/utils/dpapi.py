"""Win32 DPAPI — the ONE ctypes call site, with the protection scope as a required argument.

``CryptProtectData`` / ``CryptUnprotectData`` reached through a single helper so the two
callers that need DPAPI cannot drift apart:

* :mod:`src.scheduler.elevation` seals the elevation handshake at **CurrentUser** scope
  (``flags=CRYPTPROTECT_UI_FORBIDDEN``) — a blob that crosses a privilege boundary within
  ONE user, where the SID binding IS the boundary (decision 2026-06-25 / plan 0049 D5,
  not reopened here).
* ``src/sftp/secret_store.py`` (plan 0049 S-1a-ii) seals the delivery password at
  **LocalMachine** scope (``CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN``) on a
  machine-scoped install, where cross-account readability is the POINT and the NTFS DACL
  is the confidentiality boundary.

**``flags`` is required and undefaulted.** The protection scope is the one thing a caller
may choose here, and a defaulted scope is exactly the permissive default on a
safety-relevant parameter that ``CLAUDE.md`` bans: a helper defaulting to ``0`` would
silently drop ``UI_FORBIDDEN`` (a modal DPAPI prompt inside the ``SW_HIDE`` elevated child
or the non-interactive nightly turns a clean ``OSError`` into a bounded-wait hang), and one
defaulting to LocalMachine would widen the handshake blob's scope on the quiet. A round
trip cannot catch either — both scopes round-trip for the process that sealed the blob —
so ``tests/test_dpapi.py`` asserts the exact ``dwFlags`` word reaching the API.

**Why ``src/utils`` and not an export from ``scheduler/elevation``:** ``src/sftp`` importing
from ``src/scheduler`` would be a worse dependency inversion than a shared utility module;
:mod:`src.utils.identity` is the standing precedent for a small module of counted
primitives. This module knows nothing about DistrictSync's entropy values or payload
shapes — each caller owns its own.
"""

from __future__ import annotations

import ctypes

# CRYPTPROTECT_* flag words (wincrypt.h). Spelled here ONCE; callers compose them.
#   UI_FORBIDDEN  — never raise a DPAPI prompt; fail with an error instead. Mandatory for
#                   any call that can run headless (the elevated child, the nightly).
#   LOCAL_MACHINE — seal to the machine key rather than the user's, so any account on THIS
#                   computer can unseal it. Deliberately opt-in, per call.
CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _DataBlob(ctypes.Structure):
    """Win32 ``DATA_BLOB`` — a length-prefixed byte buffer for the DPAPI APIs."""

    _fields_ = (("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char)))


def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    """Wrap ``data`` in a ``DATA_BLOB``; the returned buffer must be kept alive by the caller."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def dpapi_call(func_name: str, data: bytes, entropy: bytes, *, flags: int) -> bytes:
    """Call ``CryptProtectData`` / ``CryptUnprotectData`` with the given ``flags``.

    Args:
        func_name: ``"CryptProtectData"`` or ``"CryptUnprotectData"``.
        data: the plaintext to seal, or the sealed blob to open.
        entropy: the caller's optional-entropy bytes — a namespacing / tamper-binding
            value, NOT a secret. Both halves of a round trip must supply the same bytes.
        flags: the ``dwFlags`` word (see the ``CRYPTPROTECT_*`` constants above).

    Returns:
        The API's output bytes.

    Raises:
        OSError: on ANY API failure — a wrong entropy, a cross-SID unprotect, a tampered
            blob. The caller fails closed; the message names the call and the Windows
            error number and never echoes the payload.
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
        ctypes.c_uint32(flags),
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
