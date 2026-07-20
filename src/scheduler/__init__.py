"""Cross-platform task scheduler integration for DistrictSync.

- Windows: PowerShell ``Register-ScheduledTask`` (:mod:`src.scheduler.windows`) —
  unattended stored-password registration, per-operation UAC elevation, and the
  tri-state schedule read-back.
- Linux/macOS: ``crontab`` (:mod:`src.scheduler.linux`) — a sentinel-tagged crontab
  entry; no stored password, no elevation, no schedule read-back.

**The one platform-dispatch point (W4a, T2.3):** :func:`get_scheduler` returns the
platform's :class:`Scheduler`, so UI callers never branch on ``sys.platform`` or import
``windows`` vs ``linux`` themselves. The two platforms are **deliberately asymmetric**
and the Protocol models that honestly instead of papering over it:

- ``supports_unattended_password`` — only Windows can register a task that runs while
  nobody is signed in (stored-credential logon). :meth:`CronScheduler.register` FAILS
  LOUD (``ValueError``) if a password is passed anyway — cron cannot store one, and
  silently dropping it would misrepresent what was scheduled.
- ``supports_read_schedule`` — only Windows can read the real task back.
  :meth:`CronScheduler.read_schedule` returns the honest UNKNOWN shape
  (``found=None`` — "could not confirm", never "absent"), exactly what
  :func:`src.scheduler.windows.read_schedule` reports off Windows today.

The adapters are thin, stateless delegates: every security invariant (password only in
the ``DSYNC_TASK_PW`` child env / DPAPI handshake, canonical fail-loud messages,
de-CLIXML) lives — unchanged — inside :mod:`src.scheduler.windows`; the fail-loud
crontab read-before-rewrite lives inside :mod:`src.scheduler.linux`. Message contracts
pass through verbatim so the pure UI classifiers keep working.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path
from typing import Protocol

from src.scheduler import linux, windows
from src.scheduler.windows import ScheduleReadback


class Scheduler(Protocol):
    """What a platform scheduler ACTUALLY exposes (structural, capability-honest).

    ``register``/``delete`` return the platform module's ``(success, message)``
    contract unchanged — messages pass through verbatim so callers (and the pure
    ``ui_flet.setup_errors`` classifier) can key off the canonical strings.
    Capabilities a platform lacks are modeled explicitly (the two ``supports_*``
    flags) rather than pretended: callers gate affordances on the flags, and the
    methods behind an unsupported capability either fail loud (``register`` with a
    password on cron) or return the honest degenerate shape (``read_schedule`` →
    UNKNOWN on cron).
    """

    supports_unattended_password: bool
    supports_read_schedule: bool

    def register(
        self,
        *,
        task_name: str,
        exe_path: Path,
        sis_type: str,
        input_dir: Path,
        output_dir: Path,
        run_time: str,
        sftp: bool = False,
        run_as_user: str | None = None,
        run_as_password: str | None = None,
        run_highest: bool = True,
    ) -> tuple[bool, str]:
        """Create or replace the daily scheduled run; returns ``(success, message)``."""
        ...

    def delete(self, task_name: str) -> tuple[bool, str]:
        """Remove the scheduled run; returns ``(success, message)``."""
        ...

    def read_schedule(self, task_name: str) -> ScheduleReadback:
        """Read the real schedule back, tri-state; UNKNOWN-shaped where unsupported."""
        ...

    def is_elevated(self) -> bool:
        """Whether the current process runs with administrator rights (False where N/A)."""
        ...

    def run_as_user(self) -> str:
        """The account a registered task would run as (the interactive user)."""
        ...


class WindowsTaskScheduler:
    """The Windows Task Scheduler adapter — delegates to :mod:`src.scheduler.windows`.

    Pure delegation at CALL time (``windows.register_task(...)`` etc.), so tests that
    monkeypatch the ``src.scheduler.windows`` module attributes keep working and none
    of the module's security invariants are duplicated here.
    """

    supports_unattended_password = True
    supports_read_schedule = True

    def register(
        self,
        *,
        task_name: str,
        exe_path: Path,
        sis_type: str,
        input_dir: Path,
        output_dir: Path,
        run_time: str,
        sftp: bool = False,
        run_as_user: str | None = None,
        run_as_password: str | None = None,
        run_highest: bool = True,
    ) -> tuple[bool, str]:
        """Register via :func:`src.scheduler.windows.register_task` (all kwargs pass through)."""
        return windows.register_task(
            task_name=task_name,
            exe_path=exe_path,
            sis_type=sis_type,
            input_dir=input_dir,
            output_dir=output_dir,
            run_time=run_time,
            sftp=sftp,
            run_as_user=run_as_user,
            run_as_password=run_as_password,
            run_highest=run_highest,
        )

    def delete(self, task_name: str) -> tuple[bool, str]:
        """Delete the task; an un-elevated access-denied retries once behind ONE UAC prompt.

        The plain ``schtasks`` delete fails with access-denied when the task was
        registered ``RunLevel Highest`` and the caller isn't elevated — the same
        fallback the Setup surface always performed now lives here (caller-side
        platform logic collapsed into the adapter, behavior unchanged):
        :func:`src.scheduler.windows.delete_task_elevated` confirms the removal via
        read-back and returns the canonical elevation messages verbatim.
        """
        ok, msg = windows.delete_task(task_name)
        if not ok and "access is denied" in (msg or "").lower() and not windows.is_elevated():
            ok, msg = windows.delete_task_elevated(task_name)
        return ok, msg

    def read_schedule(self, task_name: str) -> ScheduleReadback:
        """The tri-state read-back (:func:`src.scheduler.windows.read_schedule`)."""
        return windows.read_schedule(task_name)

    def is_elevated(self) -> bool:
        """Whether this process has administrator rights (:func:`windows.is_elevated`)."""
        return windows.is_elevated()

    def run_as_user(self) -> str:
        """``DOMAIN\\user`` for the interactive account (:func:`windows.current_run_as_user`)."""
        return windows.current_run_as_user()


class CronScheduler:
    """The crontab adapter (Linux/macOS) — delegates to :mod:`src.scheduler.linux`.

    Honest asymmetry: cron entries have no name (``task_name`` is accepted for the
    shared Protocol signature but the managed entry is identified by the crontab
    sentinel comment), no stored-password logon (a supplied password FAILS LOUD),
    no elevation concept, and no schedule read-back (UNKNOWN-shaped, never "absent").
    """

    supports_unattended_password = False
    supports_read_schedule = False

    def register(
        self,
        *,
        task_name: str,  # accepted for the shared signature; cron entries are sentinel-identified
        exe_path: Path,
        sis_type: str,
        input_dir: Path,
        output_dir: Path,
        run_time: str,
        sftp: bool = False,
        run_as_user: str | None = None,
        run_as_password: str | None = None,
        run_highest: bool = True,
    ) -> tuple[bool, str]:
        """Register via :func:`src.scheduler.linux.register_cron`; a password fails loud.

        ``run_as_password`` raises ``ValueError`` — cron cannot store a credential, and
        silently discarding it would let a caller believe an unattended stored-password
        task exists (gate the affordance on ``supports_unattended_password`` instead).
        ``run_as_user`` / ``run_highest`` are Windows logon concepts with no cron
        equivalent and are ignored, mirroring ``register_task``'s own no-password path.
        """
        del task_name, run_as_user, run_highest  # no cron equivalent (see docstring)
        if run_as_password is not None:
            raise ValueError("Unattended (stored-password) scheduling is not supported on this platform.")
        return linux.register_cron(exe_path, sis_type, input_dir, output_dir, run_time, sftp=sftp)

    def delete(self, task_name: str) -> tuple[bool, str]:
        """Remove the sentinel-tagged crontab entry (``task_name`` is not used by cron)."""
        del task_name  # cron entries are identified by the managed sentinel comment
        return linux.delete_cron()

    def read_schedule(self, task_name: str) -> ScheduleReadback:
        """The honest UNKNOWN shape — cron read-back is unsupported, never claimed absent."""
        del task_name
        return ScheduleReadback(found=None, error=windows._MSG_NOT_WINDOWS)

    def is_elevated(self) -> bool:
        """Always False — there is no Windows-style elevation concept here."""
        return False

    def run_as_user(self) -> str:
        """The invoking user — cron runs the entry as the crontab's owner."""
        return getpass.getuser()


def get_scheduler(platform: str | None = None) -> Scheduler:
    """The platform :class:`Scheduler` — the ONE dispatch point UI callers use.

    ``platform`` defaults to ``sys.platform`` read at CALL time (so tests that patch
    ``sys.platform`` drive dispatch without touching this module); pass ``"win32"`` /
    ``"linux"`` explicitly for pure dispatch tests. Adapters are stateless, so a fresh
    instance per call is free and keeps this side-effect-free.
    """
    plat = sys.platform if platform is None else platform
    if plat == "win32":
        return WindowsTaskScheduler()
    return CronScheduler()
