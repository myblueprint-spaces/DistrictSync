"""Pure tests for the scheduler Protocol factory + platform dispatch (W4a, T2.3).

``get_scheduler()`` is the ONE platform-dispatch point the UI callers use instead of
scattered ``sys.platform`` branches. These tests pin:

  - dispatch (``win32`` → :class:`WindowsTaskScheduler`, everything else → :class:`CronScheduler`,
    default read from ``sys.platform`` at CALL time so platform-patching tests keep working);
  - the HONEST capability flags (the two platforms are asymmetric by design);
  - thin delegation to the platform modules at CALL time (so monkeypatching
    ``src.scheduler.windows`` / ``src.scheduler.linux`` attributes — the pattern every
    existing UI test uses — still intercepts calls made through the adapters);
  - the fail-loud cron password rejection (cron cannot store a credential — silently
    dropping it would misrepresent what was scheduled);
  - the UNKNOWN-shaped cron read-back (``found=None`` — never "absent");
  - the Windows delete's access-denied → one-UAC-prompt elevated retry (the caller-side
    fallback that moved into the adapter, behavior unchanged).

All platform-module calls are monkeypatched — no OS scheduler interaction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.scheduler import CronScheduler, WindowsTaskScheduler, get_scheduler
from src.scheduler import linux as linux_mod
from src.scheduler import windows as windows_mod

_REGISTER_KWARGS = dict(
    task_name="DistrictSync_Daily",
    exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
    sis_type="myedbc",
    input_dir=Path("C:/GDE2Data/input"),
    output_dir=Path("C:/GDE2Data/output"),
    run_time="03:00",
    sftp=True,
)


# --------------------------------------------------------------------------- #
# Dispatch                                                                      #
# --------------------------------------------------------------------------- #
class TestFactoryDispatch:
    def test_win32_returns_the_windows_scheduler(self):
        assert isinstance(get_scheduler("win32"), WindowsTaskScheduler)

    def test_linux_returns_the_cron_scheduler(self):
        assert isinstance(get_scheduler("linux"), CronScheduler)

    def test_darwin_returns_the_cron_scheduler(self):
        assert isinstance(get_scheduler("darwin"), CronScheduler)

    def test_default_reads_sys_platform_at_call_time(self, monkeypatch):
        # The UI tests patch the global sys.platform to drive platform behavior — the factory
        # must observe that patch (call-time read), never a value frozen at import time.
        monkeypatch.setattr(sys, "platform", "win32")
        assert isinstance(get_scheduler(), WindowsTaskScheduler)
        monkeypatch.setattr(sys, "platform", "linux")
        assert isinstance(get_scheduler(), CronScheduler)


# --------------------------------------------------------------------------- #
# Honest capability flags                                                       #
# --------------------------------------------------------------------------- #
class TestCapabilities:
    def test_windows_supports_password_and_read_back(self):
        scheduler = WindowsTaskScheduler()
        assert scheduler.supports_unattended_password is True
        assert scheduler.supports_read_schedule is True

    def test_cron_supports_neither(self):
        scheduler = CronScheduler()
        assert scheduler.supports_unattended_password is False
        assert scheduler.supports_read_schedule is False


# --------------------------------------------------------------------------- #
# Windows adapter — thin call-time delegation                                   #
# --------------------------------------------------------------------------- #
class TestWindowsAdapter:
    def test_register_forwards_every_kwarg(self, monkeypatch):
        recorded: dict = {}

        def fake_register(**kwargs):
            recorded.update(kwargs)
            return True, "ok"

        monkeypatch.setattr(windows_mod, "register_task", fake_register)
        ok, msg = WindowsTaskScheduler().register(
            **_REGISTER_KWARGS, run_as_user="DOMAIN\\admin", run_as_password="pw", run_highest=False
        )
        assert (ok, msg) == (True, "ok")
        assert recorded == {
            **_REGISTER_KWARGS,
            "run_as_user": "DOMAIN\\admin",
            "run_as_password": "pw",
            "run_highest": False,
        }

    def test_delete_success_never_elevates(self, monkeypatch):
        elevated_calls: list = []
        monkeypatch.setattr(windows_mod, "delete_task", lambda name: (True, "ok"))
        monkeypatch.setattr(windows_mod, "delete_task_elevated", lambda name: elevated_calls.append(name))
        assert WindowsTaskScheduler().delete("T") == (True, "ok")
        assert elevated_calls == []

    def test_delete_access_denied_unelevated_retries_via_elevation(self, monkeypatch):
        # The exact fallback the Setup surface performed caller-side before W4a.
        monkeypatch.setattr(windows_mod, "delete_task", lambda name: (False, "ERROR: Access is denied."))
        monkeypatch.setattr(windows_mod, "is_elevated", lambda: False)
        monkeypatch.setattr(windows_mod, "delete_task_elevated", lambda name: (True, "Schedule removed and confirmed."))
        assert WindowsTaskScheduler().delete("T") == (True, "Schedule removed and confirmed.")

    def test_delete_access_denied_while_elevated_does_not_reprompt(self, monkeypatch):
        # Already elevated + still denied → a UAC re-prompt cannot help; surface the failure.
        monkeypatch.setattr(windows_mod, "delete_task", lambda name: (False, "ERROR: Access is denied."))
        monkeypatch.setattr(windows_mod, "is_elevated", lambda: True)
        monkeypatch.setattr(
            windows_mod, "delete_task_elevated", lambda name: pytest.fail("must not elevate when already elevated")
        )
        assert WindowsTaskScheduler().delete("T") == (False, "ERROR: Access is denied.")

    def test_delete_other_failure_does_not_elevate(self, monkeypatch):
        monkeypatch.setattr(windows_mod, "delete_task", lambda name: (False, "ERROR: The system cannot find the task."))
        monkeypatch.setattr(
            windows_mod,
            "delete_task_elevated",
            lambda name: pytest.fail("must not elevate a non-access-denied failure"),
        )
        assert WindowsTaskScheduler().delete("T") == (False, "ERROR: The system cannot find the task.")

    def test_read_schedule_delegates(self, monkeypatch):
        sentinel = windows_mod.ScheduleReadback(found=True, next_run="2026-07-21T03:00:00.0000000")
        monkeypatch.setattr(windows_mod, "read_schedule", lambda name: sentinel)
        assert WindowsTaskScheduler().read_schedule("T") is sentinel

    def test_is_elevated_and_run_as_user_delegate(self, monkeypatch):
        monkeypatch.setattr(windows_mod, "is_elevated", lambda: True)
        monkeypatch.setattr(windows_mod, "current_run_as_user", lambda: "DOMAIN\\user")
        scheduler = WindowsTaskScheduler()
        assert scheduler.is_elevated() is True
        assert scheduler.run_as_user() == "DOMAIN\\user"


# --------------------------------------------------------------------------- #
# Cron adapter — honest asymmetry                                               #
# --------------------------------------------------------------------------- #
class TestCronAdapter:
    def test_register_delegates_without_task_name_or_logon_concepts(self, monkeypatch):
        recorded: dict = {}

        def fake_cron(exe, sis, inp, out, run_time, *, sftp=False):
            recorded.update(exe=exe, sis=sis, inp=inp, out=out, run_time=run_time, sftp=sftp)
            return True, "Cron entry registered."

        monkeypatch.setattr(linux_mod, "register_cron", fake_cron)
        ok, msg = CronScheduler().register(**_REGISTER_KWARGS)
        assert (ok, msg) == (True, "Cron entry registered.")
        assert recorded == {
            "exe": _REGISTER_KWARGS["exe_path"],
            "sis": "myedbc",
            "inp": _REGISTER_KWARGS["input_dir"],
            "out": _REGISTER_KWARGS["output_dir"],
            "run_time": "03:00",
            "sftp": True,
        }

    def test_register_with_a_password_fails_loud(self, monkeypatch):
        monkeypatch.setattr(
            linux_mod, "register_cron", lambda *a, **k: pytest.fail("must not touch crontab with a password")
        )
        with pytest.raises(ValueError, match="not supported on this platform"):
            CronScheduler().register(**_REGISTER_KWARGS, run_as_password="pw")

    def test_delete_delegates_to_the_sentinel_removal(self, monkeypatch):
        monkeypatch.setattr(linux_mod, "delete_cron", lambda: (True, "Cron entry removed."))
        assert CronScheduler().delete("ignored-name") == (True, "Cron entry removed.")

    def test_read_schedule_is_unknown_shaped_never_absent(self):
        readback = CronScheduler().read_schedule("T")
        assert readback.found is None  # "could not confirm" — NEVER found=False ("absent")
        assert readback.error  # carries the honest platform note

    def test_is_elevated_is_always_false(self):
        assert CronScheduler().is_elevated() is False

    def test_run_as_user_is_the_invoking_user(self, monkeypatch):
        import getpass

        monkeypatch.setattr(getpass, "getuser", lambda: "districtadmin")
        assert CronScheduler().run_as_user() == "districtadmin"
