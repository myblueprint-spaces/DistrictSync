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
* A frozen parent sets ``PYINSTALLER_RESET_ENVIRONMENT=1`` for the child (:func:`child_environment`). Without it a
  one-file build's child inherits the ``_PYI_*`` variables, takes itself for the bootloader's own child and runs
  from the PARENT's ``_MEI`` folder — which the parent deletes as it exits.
* :func:`relaunch` closes the window FIRST (awaited ``destroy()``, bounded by :data:`DESTROY_TIMEOUT_SECONDS`), then
  spawns, then ends this process with ``os._exit`` — 0 when the new copy started, 1 when it could not. The spawn and
  the exit sit in a ``finally``: when the view exits, ``ft.run`` winds down and cancels the pending ``destroy``, and
  the relaunch must still happen.

The argv itself is computed by the pure :func:`src.ui_flet.grant_access.reexec_argv`. No flet import: the page is
duck-typed, so this module stays on the flet-free side of the architecture fitness rule.
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

#: The PyInstaller (6.10+) bootloader switch that makes a frozen app's child a fresh, independent instance.
RESET_ENVIRONMENT_VAR = "PYINSTALLER_RESET_ENVIRONMENT"

#: How long the window gets to close before the relaunch goes ahead regardless.
DESTROY_TIMEOUT_SECONDS = 5.0

# Literal values so the pure function answers the same on every OS (the subprocess constants exist only on Windows).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def creation_flags(*, windows: bool) -> int:
    """The CreateProcess flags for the relaunched copy: detached, in its own process group (none off Windows)."""
    return _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP if windows else 0


def child_environment(base: Mapping[str, str], *, frozen: bool) -> dict[str, str]:
    """A copy of ``base`` for the child; a frozen parent adds the PyInstaller fresh-instance switch."""
    env = dict(base)
    if frozen:
        env[RESET_ENVIRONMENT_VAR] = "1"
    return env


def spawn(argv: Sequence[str]) -> None:
    """Start ``argv`` as a new, independent process. Raises what ``Popen`` raises; never waits for it."""
    args = list(argv)
    if not args:
        raise ValueError("relaunch needs an executable to start")
    windows = sys.platform == "win32"
    # A list from reexec_argv (our own exe or interpreter + our own argv), no shell: bandit B603 is skipped repo-wide.
    subprocess.Popen(
        args,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment(os.environ, frozen=bool(getattr(sys, "frozen", False))),
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
        os._exit(_start(argv))


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


def relaunch_seam(page: Any) -> Callable[[], None]:
    """The windows' synchronous ``reexec`` seam: schedule :func:`relaunch` on ``page``'s event loop."""

    def _reexec() -> None:
        page.run_task(relaunch, page)

    return _reexec
