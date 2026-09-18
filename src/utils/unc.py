"""Mapped-drive → UNC resolution, and the folder-reach HEURISTIC built on it (plan 0049 S-2b.1).

This exists for ONE warning sentence inside the machine-scope confirm: *we can't confirm
the account can reach this folder*. It is deliberately **not** a gate, and the distinction
is the whole reason this module is small.

**Why it is not a gate.** ``FOLDER_NOT_SHAREABLE`` was specified as a
:class:`~src.ui_flet.setup_gates.RegisterBlock` member and was demoted out of it, because
the heuristic is wrong in BOTH directions and a gate cannot be wrong in either:

* it clears a UNC path the service account has no rights on (a local account has no
  network identity at all, so ``\\\\server\\share`` fails exactly as silently as ``Z:\\``);
* it flagged ``C:\\Users\\Public\\…``, which is genuinely readable by every account on the
  computer.

A gate that refuses a working setup, and passes a broken one, is worse than a sentence
that says plainly what it does not know. So: :func:`reach_unconfirmed` answers "can we
CONFIRM another account could reach this?" — never "is this reachable?" — and the copy
built on it may never be phrased as a finding.

**Fail-open, totally.** Not Windows, not a mapped drive, ``mpr.dll`` unavailable, a
pywin32-free frozen build, any error at all → :func:`unc_target` returns ``""`` and the
caller simply does not offer a replacement. The syscall is isolated behind
:func:`_read_universal_name` exactly as ``paths._read_machine_switch_value`` is, so every
decision built on it is testable on every OS.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ``UNIVERSAL_NAME_INFO_LEVEL`` — the cheapest of the two levels ``WNetGetUniversalName``
# accepts, and the only one we want: it returns the ``\\server\share\rest`` form and
# nothing else. (``REMOTE_NAME_INFO_LEVEL`` adds the connection name, which we never show.)
_UNIVERSAL_NAME_INFO_LEVEL = 1

# winerror.h. ``ERROR_MORE_DATA`` is the only failure worth a second call: it hands back
# the buffer size it wants. Every other non-zero return is a "not a mapped drive" answer.
_ERROR_MORE_DATA = 234

# Comfortably larger than ``MAX_PATH`` doubled (wide chars) plus the pointer prefix, so the
# common case is one call. The retry exists for the pathological share name, not the norm.
_INITIAL_BUFFER_BYTES = 2048

# The two path words the heuristic knows. ``Users`` is NOT localised on disk — a German
# Windows shows "Benutzer" in Explorer but the directory is still ``Users`` — so matching
# the real name is correct and matching a display name would be the bug.
_USERS_DIR = "users"
_PUBLIC_DIR = "public"


class _UniversalNameInfo(ctypes.Structure):
    """Win32 ``UNIVERSAL_NAME_INFOW`` — one pointer into the tail of the same buffer."""

    _fields_ = (("lpUniversalName", ctypes.c_wchar_p),)


def _read_universal_name(path: str) -> str:  # pragma: no cover - Windows-only ctypes
    """Raw ``WNetGetUniversalNameW`` — the ONE syscall seam. Raises on anything else.

    Isolated so the decisions above it run (and are tested) on every OS, following
    ``paths._read_machine_switch_value``'s convention. There is deliberately NO
    ``sys.platform`` guard here: it would be worthless at runtime — a test that patches
    the platform sails straight past it into ``ctypes.WinDLL``, which is an
    ``AttributeError`` on Linux and outside ``OSError`` — and the caller's broad catch is
    what actually makes this safe. The guard's other job, keeping a Linux type-checker
    happy, is done by the ``type: ignore`` below (the same shape ``utils/dpapi.py`` uses).

    Returns the ``\\\\server\\share\\rest`` form, or ``""`` when the path is not on a
    mapped drive (any non-zero return other than ``ERROR_MORE_DATA``).
    """
    mpr = ctypes.WinDLL("mpr", use_last_error=True)  # type: ignore[attr-defined]
    func = mpr.WNetGetUniversalNameW
    func.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    func.restype = ctypes.c_uint32

    size = ctypes.c_uint32(_INITIAL_BUFFER_BYTES)
    buf = ctypes.create_string_buffer(size.value)
    rc = func(path, _UNIVERSAL_NAME_INFO_LEVEL, buf, ctypes.byref(size))
    if rc == _ERROR_MORE_DATA:
        # ONE retry, with the size Windows asked for. Not a loop: a second ERROR_MORE_DATA
        # would mean the answer is growing under us, which is not a state to spin on.
        buf = ctypes.create_string_buffer(size.value)
        rc = func(path, _UNIVERSAL_NAME_INFO_LEVEL, buf, ctypes.byref(size))
    if rc != 0:
        return ""
    info = ctypes.cast(buf, ctypes.POINTER(_UniversalNameInfo)).contents
    return info.lpUniversalName or ""


def unc_target(path: str) -> str:
    """The UNC form of a path on a mapped drive, or ``""``. **TOTAL — never raises.**

    ``Z:\\Exports`` → ``\\\\server\\share\\Exports``; anything that is not a mapped drive,
    and any failure whatsoever, → ``""``.

    The catch is deliberately broad (``Exception``), and the breadth is the point rather
    than laziness: this is called from a paint path, and the failures here are not all
    ``OSError``. On Linux ``ctypes.WinDLL`` does not exist (``AttributeError``); a frozen
    build missing ``mpr.dll`` raises ``OSError``; a ctypes signature surprise raises
    ``ValueError`` or ``TypeError``. Narrowing the clause is exactly what reddened CI's
    Linux leg on this plan (plan 0049 S-2a, PR #136) — a display-copy helper may never take
    a surface down. DEBUG, not WARNING: "this is not a mapped drive" is the normal answer
    and the failure it is indistinguishable from costs the admin nothing.
    """
    text = (path or "").strip()
    if not text:
        return ""
    try:
        return _read_universal_name(text).strip()
    except Exception as exc:  # noqa: BLE001 - see the docstring; fail OPEN, never raise
        logger.debug("Could not resolve a UNC target for the chosen folder: %s", type(exc).__name__)
        return ""


def reach_unconfirmed(path: str, *, mapped: bool) -> bool:
    """Can we CONFIRM another Windows account could reach ``path``? (pure, TOTAL)

    ``True`` means *we cannot confirm it* — which is the only claim this function is
    entitled to make, and the only one its copy may make. It is a HEURISTIC and it is
    wrong in both directions (see the module docstring); that is why it warns instead of
    blocking.

    Two facts close it, and nothing else:

    * ``mapped`` — a drive letter is per-user and per-logon-session, so it does not exist
      in a scheduled task's session at all. This one is not really a heuristic: it is the
      known silent-failure shape a district already hit (ROADMAP, 2026-09-17);
    * the path sits under a user profile (``<drive>:\\Users\\…``), **except**
      ``C:\\Users\\Public``, which every account on the computer can read by design.

    A UNC path, and an ordinary local folder such as ``D:\\Exports``, answer ``False`` — we
    have nothing against them, not that we have checked them. A blank path answers
    ``False`` too: the folders gate owns "you have not chosen one", and a second surface
    saying so would just be noise.

    ``mapped`` is required and keyword-only: it is the fact that needs a syscall, and a
    defaulted ``False`` would let a call site quietly drop the half of the rule that
    matters most.
    """
    text = (path or "").strip()
    if not text:
        return False
    if mapped:
        return True
    return _under_a_user_profile(text)


def _under_a_user_profile(path: str) -> bool:
    """``<drive>:\\Users\\<anything but Public>`` — pure, and deliberately spelling-based.

    Comparing against the *resolved* profile of the account running Setup would be worse,
    not better: the folder only has to be reachable by the SERVICE account, whose profile
    this process cannot enumerate, and ``USERPROFILE`` in an elevated or scheduled session
    is not the one the admin picked from.
    """
    parts = [part for part in path.replace("/", "\\").split("\\") if part]
    if len(parts) < 2 or not _is_drive(parts[0]) or parts[1].casefold() != _USERS_DIR:
        return False
    # ``C:\Users`` itself (no third component) is still a profile container, not a folder
    # anyone else is entitled to; only ``Public`` is exempt.
    return len(parts) < 3 or parts[2].casefold() != _PUBLIC_DIR


def _is_drive(part: str) -> bool:
    """``"C:"`` — a drive designator, on ANY letter (a UNC's ``server`` must not match)."""
    return len(part) == 2 and part[1] == ":" and part[0].isalpha()


@dataclass(frozen=True)
class FolderReach:
    """What the confirm can honestly say about one chosen folder.

    ``unc_replacement`` is ``""`` whenever there is nothing to offer — including when the
    resolution FAILED — so a caller that renders a "use this instead" button on a non-empty
    string can never offer a path we did not actually get from Windows.
    """

    path: str
    unconfirmed: bool
    unc_replacement: str = ""


def describe_folder_reach(path: str) -> FolderReach:
    """The ONE call the confirm makes per folder: one syscall, then the pure rule.

    Effectful only in :func:`unc_target`, which fails open — so on a non-Windows box, or
    when ``mpr.dll`` will not answer, this degrades to the pure profile test rather than
    to an exception or to a warning nobody can act on.
    """
    target = unc_target(path)
    return FolderReach(
        path=path,
        unconfirmed=reach_unconfirmed(path, mapped=bool(target)),
        unc_replacement=target,
    )
