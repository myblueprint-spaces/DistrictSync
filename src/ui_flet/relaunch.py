"""Re-launch DistrictSync from a pre-shell window: close it, start a fresh copy, end this one (DSYNC-114).

The machine setup window and the auto-grant window both re-launch the app once the shared profile is usable (a second
``ft.run`` in one process is unproven, and a fresh process is the honest proof the change worked). They used
``os.execv``, which on Windows neither quotes its arguments nor closes the window: an exe under
``C:\\Program Files\\`` split at the space, the child's argparse exited 2 on ``Files\\...``, and the old window stayed
on "Working…" for good. This module is the ONE replacement both windows share.

* :func:`spawn` starts the new process with :class:`subprocess.Popen` and a LIST — CreateProcess quoting
  (``list2cmdline``), no shell, ``close_fds``, no inherited stdio.
* On Windows the child is created ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``: attached to no console (the
  windowed exe has none anyway; a source run's console is about to lose its owner) and outside this process's
  Ctrl+C/Ctrl+Break group. ``CREATE_BREAKAWAY_FROM_JOB`` is deliberately NOT set: it raises access-denied inside a
  job that forbids breakaway, and neither the PyInstaller bootloader nor the app uses a job object.
* A frozen child is made independent of the parent's ``_MEI`` folder in TWO ways, and needs both:
  ``PYINSTALLER_RESET_ENVIRONMENT=1`` (:func:`child_environment`) stops it taking itself for the bootloader's own
  child and running from the parent's folder, and a working directory beside the exe (:func:`child_cwd`) stops it
  inheriting the parent's cwd — ``launcher.main`` has ``chdir``'d into ``_MEIPASS``, and a child sitting in that
  folder holds it open, so the old bootloader cannot delete it as it exits (WinError 32, review F1).
* :func:`relaunch` closes the window FIRST (awaited ``destroy()``, bounded by :data:`DESTROY_TIMEOUT_SECONDS`), then
  spawns, then ends this process with ``os._exit`` — 0 when the new copy started, 1 when it could not, after a plain
  message box (:func:`show_failure_notice`), because by then the window is gone. The spawn and the exit sit in a
  ``finally``: when the view exits, ``ft.run`` winds down and cancels the pending ``destroy``, and the relaunch must
  still happen.

The argv itself is computed by the pure :func:`src.ui_flet.grant_access.reexec_argv`. No flet import: the page is
duck-typed, so this module stays on the flet-free side of the architecture fitness rule. The notice is a native
``MessageBoxW`` rather than ``launcher._show_error_dialog``: that dialog starts a second ``ft.run``, which cannot run
inside the event loop this code is running on, and its tkinter fallback is not bundled.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.ui_flet import grant_access

logger = logging.getLogger(__name__)

#: The PyInstaller (6.10+) bootloader switch that stops a frozen app's child reusing the parent's unpacked bundle.
RESET_ENVIRONMENT_VAR = "PYINSTALLER_RESET_ENVIRONMENT"

#: How long the window gets to close before the relaunch goes ahead regardless.
DESTROY_TIMEOUT_SECONDS = 5.0

#: What the user sees when the new copy could not be started (the window has already closed).
RESTART_FAILED_TITLE = "DistrictSync"
RESTART_FAILED_MESSAGE = "DistrictSync couldn't restart. Open it again from the Start menu."

# Literal values so the pure functions answer the same on every OS (the subprocess constants exist only on Windows).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
# MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST: nothing else is on screen to stand in front of.
_MESSAGE_BOX_STYLE = 0x00000000 | 0x00000010 | 0x00010000 | 0x00040000


def creation_flags(*, windows: bool) -> int:
    """The CreateProcess flags for the relaunched copy: detached, in its own process group (none off Windows)."""
    return _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP if windows else 0


def child_environment(base: Mapping[str, str], *, frozen: bool) -> dict[str, str]:
    """A copy of ``base`` for the child; a frozen parent adds the PyInstaller fresh-instance switch."""
    env = dict(base)
    if frozen:
        env[RESET_ENVIRONMENT_VAR] = "1"
    return env


def child_cwd(*, frozen: bool, executable: str, current: str) -> str:
    """The child's working directory: the exe's own folder when frozen, else ``current``.

    Frozen, the current directory is the parent's ``_MEIPASS`` (``launcher.main`` chdir'd there), which a child must
    not hold open. A source run keeps ``current``: ``python -m src.main`` resolves ``src`` from it.
    """
    return os.path.dirname(executable) if frozen else current


def spawn(argv: Sequence[str]) -> None:
    """Start ``argv`` as a new, independent process. Raises what ``Popen`` raises; never waits for it."""
    args = list(argv)
    if not args:
        raise ValueError("relaunch needs an executable to start")
    windows = sys.platform == "win32"
    frozen = bool(getattr(sys, "frozen", False))
    # A list from reexec_argv (our own exe or interpreter + our own argv), no shell: bandit B603 is skipped repo-wide.
    subprocess.Popen(
        args,
        cwd=child_cwd(frozen=frozen, executable=sys.executable, current=os.getcwd()),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment(os.environ, frozen=frozen),
        creationflags=creation_flags(windows=windows),
        start_new_session=not windows,
    )


async def relaunch(page: Any) -> None:
    """Close ``page``'s window, start a fresh DistrictSync, and end this process. Never returns normally."""
    argv = grant_access.reexec_argv()
    try:
        await asyncio.wait_for(page.window.destroy(), timeout=DESTROY_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the window is going either way; the relaunch must still happen
        logger.warning(
            "Relaunch: the window did not close cleanly (%s); starting DistrictSync anyway.", type(exc).__name__
        )
    finally:
        code = _start(argv)
        if code != 0:
            show_failure_notice()
        os._exit(code)


def _start(argv: Sequence[str]) -> int:
    """Spawn and answer the exit code for THIS process: 0 when the new copy started, 1 when it did not.

    Total: anything escaping here would skip ``os._exit`` and leave a process with no window. Only the exception's
    type and errno are logged — the argv holds the install path, which can carry a Windows account name.
    """
    try:
        spawn(argv)
    except Exception as exc:  # noqa: BLE001 - total by contract; the failure is logged and becomes exit code 1
        logger.error(
            "Relaunch: DistrictSync could not be started again (%s, errno %s). Open it from the Start menu.",
            type(exc).__name__,
            getattr(exc, "errno", None),
        )
        return 1
    logger.info("Relaunch: started a fresh DistrictSync (%d argument(s)); this window is closing.", len(argv) - 1)
    return 0


def _message_box() -> Callable[..., int] | None:
    """``user32.MessageBoxW`` on Windows, else ``None`` (there is no native box to show)."""
    if sys.platform != "win32":
        return None
    import ctypes

    box: Callable[..., int] = ctypes.windll.user32.MessageBoxW  # type: ignore[attr-defined]
    return box


def show_failure_notice() -> None:
    """Tell the user the restart failed, in plain words. Never raises: the process exits right after."""
    try:
        box = _message_box()
        if box is not None:
            box(None, RESTART_FAILED_MESSAGE, RESTART_FAILED_TITLE, _MESSAGE_BOX_STYLE)
    except Exception as exc:  # noqa: BLE001 - total by contract; the log line above already records the failure
        logger.warning("Relaunch: the failure notice could not be shown (%s).", type(exc).__name__)


def relaunch_seam(page: Any) -> Callable[[], None]:
    """The windows' synchronous ``reexec`` seam: schedule :func:`relaunch` on ``page``'s event loop."""

    def _reexec() -> None:
        page.run_task(relaunch, page)

    return _reexec
