"""DSYNC-33 — the schedule check: prove Windows can start DistrictSync as the nightly's account.

Every Windows-only API sits behind a seam and is FAKED here, exactly as the other scheduler
tests do: the check's four Task Scheduler calls (``CheckCom``), the LSA rights read, the
``NetQueryServiceAccount`` query, the SID lookup and the elevated child's launch. **No test in
this file registers, runs or deletes a real scheduled task, and none raises a UAC prompt.**

What is pinned:

* the probe flow — register with the nightly's IDENTICAL command, run, poll (injected clock and
  sleep), classify, and ALWAYS delete the check task and the marker, including on a raise;
* each failure class, the timeout, and the marker-missing-despite-success case;
* the hidden ``--schedule-check`` mode writes ONLY the marker and reaches no ETL / delivery /
  run store / log sink (with the positive twins that prove the tripwires are live);
* the advisory facts — rights DENIED / NOT_DIRECT / GRANTED / UNKNOWN, install states;
* the bounded channel — three enum values and an int, re-validated on the parent side;
* the elevated child runs the check only when asked, after the provision, in the same op;
* the parent's pre-UAC SID resolution refusal (both flavours), the check-result read-back and
  its one log line, and the Remove-with-revoke round trip.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.scheduler import elevated_apply, provision_session, provisioning, schedule_check, task_com
from src.scheduler.elevation import ElevationResult
from src.scheduler.schedule_check import (
    CHECK_EXIT_BAD_REQUEST,
    CHECK_EXIT_MARKER_UNWRITABLE,
    CHECK_EXIT_NOT_SHARED,
    CHECK_EXIT_OK,
    CHECK_EXIT_PROFILE_REFUSED,
    CheckVerdict,
    InstallState,
    RightState,
    ScheduleCheck,
)
from src.scheduler.task_com import CheckTaskFacts, PrincipalKind, RegisterParams, TaskComError
from src.scheduler.windows import ScheduleReadback
from src.utils import paths
from tests.test_provision_session import _child, _provision, _refusal, _store

_GMSA = "CORP\\svc_districtsync$"
_TOKEN = "0123456789abcdef0123456789abcdef"
_EXE = r"C:\Program Files\DistrictSync\DistrictSync.exe"
_WORKDIR = r"C:\Program Files\DistrictSync"

_TRUSTED_ACES = ((0, "S-1-5-18"), (0, "S-1-5-32-544"), (0, "S-1-5-21-1-2-3-1001"))


def _params(**over: object) -> RegisterParams:
    fields: dict[str, object] = {
        "task_name": "DistrictSync_Daily",
        "exe": _EXE,
        "arguments": '--sis myedbc --input "D:\\in" --output "D:\\out" --source scheduled',
        "working_dir": _WORKDIR,
        "run_time": "03:00",
        "user": _GMSA,
        "password": None,
        "kind": PrincipalKind.MANAGED_SERVICE_ACCOUNT,
        "run_highest": True,
        "wake_to_run": True,
    }
    fields.update(over)
    return RegisterParams(**fields)  # type: ignore[arg-type]


#: The injected ``now`` every probe-flow test runs at — the moment of the ``Run`` call.
_RUN_AT = datetime(2026, 10, 23, 14, 0, 0)
_AFTER_RUN = "2026-10-23T14:00:05"
_BEFORE_RUN = "2026-10-22T03:00:01"


def _ready(last_result: int, *, last_run: str | None = _AFTER_RUN) -> CheckTaskFacts:
    """A finished sample. By default it describes a run AFTER ours (``LastRunTime`` >= Run)."""
    return CheckTaskFacts(state=task_com.TASK_STATE_READY, last_result=last_result, last_run=last_run)


_RUNNING = CheckTaskFacts(state=task_com.TASK_STATE_RUNNING, last_result=task_com.RESULT_RUNNING)
_NOT_YET = CheckTaskFacts(state=task_com.TASK_STATE_READY, last_result=task_com.RESULT_HAS_NOT_RUN)
_NOT_FOUND = TaskComError(task_com.HR_NOT_FOUND, task_com.MSG_NOT_FOUND)


class _Clock:
    """An injected monotonic clock that ``sleep`` advances — no real waiting anywhere."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _FakeCom:
    """The four Task Scheduler calls, scripted.

    ``existing`` answers the lookup BEFORE registration (``None`` = no task by that name, an
    exception = the lookup failed); ``reads`` answer the poll AFTER it, in order (the last
    repeats). Each call records ``(op, name, timeout_s, clock_at_start, ...)``. With
    ``worst_case`` every call advances ``clock`` by its FULL bound — a call that hangs until its
    worker gives up — which is what the deadline arithmetic has to survive.
    """

    def __init__(
        self,
        *,
        reads: list[object] | None = None,
        existing: object = None,
        on_run=None,
        register_exc: BaseException | None = None,
        run_exc: BaseException | None = None,
        delete_exc: BaseException | None = None,
        clock: _Clock | None = None,
        worst_case: bool = False,
    ) -> None:
        self.reads = list(reads or [_ready(CHECK_EXIT_OK)])
        self.existing = existing
        self.on_run = on_run
        self.register_exc = register_exc
        self.run_exc = run_exc
        self.delete_exc = delete_exc
        self.clock = clock
        self.worst_case = worst_case
        self.registered = False
        self.calls: list[tuple] = []

    def _enter(self, op: str, name: str, timeout_s: float, *extra) -> None:
        at = self.clock.now if self.clock else None
        self.calls.append((op, name, timeout_s, at, *extra))
        if self.worst_case and self.clock is not None:
            self.clock.now += timeout_s

    def register(self, params, *, check_name, arguments, start_boundary, end_boundary, timeout_s):
        self._enter("register", check_name, timeout_s, params, arguments, start_boundary, end_boundary)
        self.registered = True
        if self.register_exc is not None:
            raise self.register_exc

    def run(self, check_name, *, timeout_s):
        self._enter("run", check_name, timeout_s)
        if self.run_exc is not None:
            raise self.run_exc
        if self.on_run is not None:
            self.on_run()

    def read(self, check_name, *, timeout_s):
        if not self.registered:
            self._enter("lookup", check_name, timeout_s)
            if self.existing is None:
                raise _NOT_FOUND
            if isinstance(self.existing, BaseException):
                raise self.existing
            return self.existing
        self._enter("read", check_name, timeout_s)
        item = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def delete(self, check_name, *, timeout_s):
        self._enter("delete", check_name, timeout_s)
        if self.delete_exc is not None:
            raise self.delete_exc

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def register_call(self) -> tuple:
        return next(c for c in self.calls if c[0] == "register")


def _write_marker(runs: Path, token: str = _TOKEN) -> None:
    """Write the marker through the REAL writer the hidden mode uses."""
    schedule_check._write_marker(schedule_check.marker_path(runs, token), token)


def _run(
    runs: Path,
    com: _FakeCom,
    *,
    clock: _Clock | None = None,
    params: RegisterParams | None = None,
    sweep_stale: bool = True,
):
    ticker = clock or com.clock or _Clock()
    return schedule_check.run_check(
        params or _params(),
        runs_dir=runs,
        com=com,
        token=_TOKEN,
        clock=ticker.clock,
        sleep=ticker.sleep,
        now=lambda: _RUN_AT,
        sweep_stale=sweep_stale,
    )


@pytest.fixture
def runs(tmp_path) -> Path:
    directory = tmp_path / "ProgramData" / "DistrictSync" / "runs"
    directory.mkdir(parents=True)
    return directory


# --------------------------------------------------------------------------- #
# classify — pure                                                             #
# --------------------------------------------------------------------------- #
class TestClassify:
    @pytest.mark.parametrize(
        ("code", "verdict"),
        [
            (task_com.RESULT_LOGON_FAILURE, CheckVerdict.LOGON_FAILURE),
            (task_com.RESULT_BATCH_LOGON_PROBLEM, CheckVerdict.BATCH_LOGON),
            (task_com.RESULT_PASSWORD_EXPIRED, CheckVerdict.PASSWORD_EXPIRED),
            (task_com.RESULT_ACCESS_DENIED, CheckVerdict.ACCESS_DENIED),
            (task_com.RESULT_FILE_NOT_FOUND, CheckVerdict.PROGRAM_NOT_FOUND),
            (task_com.RESULT_BAD_DIRECTORY, CheckVerdict.BAD_START_FOLDER),
            (CHECK_EXIT_PROFILE_REFUSED, CheckVerdict.PROFILE_REFUSED),
            (CHECK_EXIT_NOT_SHARED, CheckVerdict.NOT_SHARED),
            (CHECK_EXIT_MARKER_UNWRITABLE, CheckVerdict.MARKER_UNWRITABLE),
            (CHECK_EXIT_BAD_REQUEST, CheckVerdict.OTHER_RESULT),
            (0, CheckVerdict.OTHER_RESULT),  # a plain 0 is NOT the check's success exit
            (1, CheckVerdict.OTHER_RESULT),
            (0x80041234, CheckVerdict.OTHER_RESULT),
        ],
    )
    def test_each_code_lands_on_its_verdict_and_keeps_its_code(self, code, verdict):
        assert schedule_check.classify(code, finished=True, marker_ok=True) == (verdict, code)

    def test_the_success_exit_is_ok_only_with_the_marker(self):
        assert schedule_check.classify(CHECK_EXIT_OK, finished=True, marker_ok=True)[0] is CheckVerdict.OK
        assert schedule_check.classify(CHECK_EXIT_OK, finished=False, marker_ok=True)[0] is CheckVerdict.TIMEOUT
        assert schedule_check.classify(CHECK_EXIT_OK, finished=True, marker_ok=False)[0] is CheckVerdict.MARKER_MISSING

    def test_unfinished_is_a_timeout_whatever_was_last_read(self):
        assert schedule_check.classify(task_com.RESULT_RUNNING, finished=False, marker_ok=False) == (
            CheckVerdict.TIMEOUT,
            task_com.RESULT_RUNNING,
        )
        assert schedule_check.classify(None, finished=False, marker_ok=False) == (CheckVerdict.TIMEOUT, None)

    def test_a_signed_code_classifies_as_its_unsigned_twin(self):
        signed = task_com.RESULT_LOGON_FAILURE - 0x1_0000_0000
        assert schedule_check.classify(signed, finished=True, marker_ok=False) == (
            CheckVerdict.LOGON_FAILURE,
            task_com.RESULT_LOGON_FAILURE,
        )

    def test_the_check_exit_codes_are_distinct_from_the_real_runs_and_from_every_result_code(self):
        exits = {
            CHECK_EXIT_OK,
            CHECK_EXIT_PROFILE_REFUSED,
            CHECK_EXIT_NOT_SHARED,
            CHECK_EXIT_MARKER_UNWRITABLE,
            CHECK_EXIT_BAD_REQUEST,
        }
        assert len(exits) == 5
        assert exits.isdisjoint({0, 1, 2, 3}), "the real run's exit-code contract"
        results = {v for k, v in vars(task_com).items() if k.startswith("RESULT_")}
        assert exits.isdisjoint(results)

    def test_the_windows_rows_agree_with_the_nightlys_own_verdict_table(self):
        """Parity: the check and ``schedule_status.run_result_verdict`` key on the SAME RESULT_*
        constants, so a code cannot mean one thing for the nightly and another for its check."""
        from src.ui_flet.schedule_status import RunResult, run_result_verdict

        pairs = {
            CheckVerdict.LOGON_FAILURE: RunResult.LOGON_FAILURE,
            CheckVerdict.BATCH_LOGON: RunResult.BATCH_LOGON_SUSPECTED,
            CheckVerdict.PASSWORD_EXPIRED: RunResult.PASSWORD_EXPIRED,
            CheckVerdict.ACCESS_DENIED: RunResult.ACCESS_DENIED,
            CheckVerdict.PROGRAM_NOT_FOUND: RunResult.PROGRAM_NOT_FOUND,
            CheckVerdict.BAD_START_FOLDER: RunResult.BAD_START_FOLDER,
        }
        windows_rows = {code: verdict for code, verdict in schedule_check._RESULT_VERDICTS.items() if verdict in pairs}
        assert set(windows_rows.values()) == set(pairs), "every Windows verdict has a RESULT_* row"
        for code, verdict in windows_rows.items():
            assert run_result_verdict(code).result is pairs[verdict], hex(code)


class TestIsFinished:
    @pytest.mark.parametrize(
        ("facts", "finished"),
        [
            (None, False),
            (CheckTaskFacts(state=None, last_result=None), False),
            (_NOT_YET, False),
            (_RUNNING, False),
            (CheckTaskFacts(state=task_com.TASK_STATE_QUEUED, last_result=0), False),
            (CheckTaskFacts(state=task_com.TASK_STATE_RUNNING, last_result=CHECK_EXIT_OK), False),
            (_ready(CHECK_EXIT_OK), True),
            (_ready(task_com.RESULT_LOGON_FAILURE), True),
            (CheckTaskFacts(state=None, last_result=task_com.RESULT_LOGON_FAILURE), True),
        ],
    )
    def test_only_a_real_end_counts(self, facts, finished):
        assert schedule_check.is_finished(facts) is finished


# --------------------------------------------------------------------------- #
# run_check — the probe flow                                                  #
# --------------------------------------------------------------------------- #
class TestTheProbeFlow:
    def test_success_registers_the_nightlys_own_command_runs_it_and_cleans_up(self, runs):
        com = _FakeCom(reads=[_NOT_YET, _RUNNING, _ready(CHECK_EXIT_OK)], on_run=lambda: _write_marker(runs))
        params = _params()

        verdict, code = _run(runs, com, params=params)

        assert (verdict, code) == (CheckVerdict.OK, CHECK_EXIT_OK)
        _op, check_name, _bound, _at, sent, arguments, start, end = com.register_call()
        # IDENTICAL principal, program, start-in folder and run level — the whole point.
        assert sent is params
        assert check_name == "DistrictSync_Daily_Check"
        assert arguments == f"--schedule-check {_TOKEN}"
        assert start < "2026-10-23T14:00:00" < end, "starts in the past (never fires), expires soon"
        assert com.names() == ["lookup", "register", "run", "read", "read", "read", "delete"]
        assert com.calls[-1][:3] == ("delete", "DistrictSync_Daily_Check", schedule_check.DELETE_RESERVE_S)
        assert list(runs.iterdir()) == [], "the marker is removed after it was read"

    def test_a_source_run_invokes_the_module_like_the_nightly_does(self, runs):
        com = _FakeCom(on_run=lambda: _write_marker(runs))
        _run(runs, com, params=_params(exe=r"C:\Python313\python.exe"))
        assert com.register_call()[5] == f"-m src.main --schedule-check {_TOKEN}"

    @pytest.mark.parametrize(
        ("code", "verdict"),
        [
            (task_com.RESULT_LOGON_FAILURE, CheckVerdict.LOGON_FAILURE),
            (task_com.RESULT_BATCH_LOGON_PROBLEM, CheckVerdict.BATCH_LOGON),
            (task_com.RESULT_ACCESS_DENIED, CheckVerdict.ACCESS_DENIED),
            (task_com.RESULT_FILE_NOT_FOUND, CheckVerdict.PROGRAM_NOT_FOUND),
            (task_com.RESULT_BAD_DIRECTORY, CheckVerdict.BAD_START_FOLDER),
            (CHECK_EXIT_PROFILE_REFUSED, CheckVerdict.PROFILE_REFUSED),
            (CHECK_EXIT_NOT_SHARED, CheckVerdict.NOT_SHARED),
            (CHECK_EXIT_MARKER_UNWRITABLE, CheckVerdict.MARKER_UNWRITABLE),
            (0x8007FFFF, CheckVerdict.OTHER_RESULT),
        ],
    )
    def test_each_failure_class_is_reported_and_still_cleaned_up(self, runs, code, verdict):
        com = _FakeCom(reads=[_NOT_YET, _ready(code)])
        assert _run(runs, com) == (verdict, code)
        assert com.names()[-1] == "delete"

    def test_the_success_exit_without_a_marker_is_marker_missing(self, runs):
        com = _FakeCom(reads=[_ready(CHECK_EXIT_OK)])
        assert _run(runs, com) == (CheckVerdict.MARKER_MISSING, CHECK_EXIT_OK)

    def test_a_marker_carrying_another_token_does_not_count(self, runs):
        marker = schedule_check.marker_path(runs, _TOKEN)
        com = _FakeCom(on_run=lambda: marker.write_bytes(json.dumps({"token": "f" * 32}).encode()))
        assert _run(runs, com)[0] is CheckVerdict.MARKER_MISSING
        assert not marker.exists(), "even a wrong marker is removed"

    def test_an_oversized_marker_does_not_count(self, runs):
        marker = schedule_check.marker_path(runs, _TOKEN)
        payload = json.dumps({"token": _TOKEN, "pad": "x" * 5000}).encode()
        com = _FakeCom(on_run=lambda: marker.write_bytes(payload))
        assert _run(runs, com)[0] is CheckVerdict.MARKER_MISSING

    def test_a_check_that_never_finishes_times_out_within_the_bound_and_is_cleaned_up(self, runs):
        clock = _Clock()
        com = _FakeCom(reads=[_RUNNING], on_run=lambda: _write_marker(runs))

        verdict, code = _run(runs, com, clock=clock)

        assert (verdict, code) == (CheckVerdict.TIMEOUT, task_com.RESULT_RUNNING)
        # The poll stops before a sleep + a full sample would cross its window — never after.
        window = schedule_check.POLL_TIMEOUT_S
        step = schedule_check.POLL_INTERVAL_S + schedule_check.POLL_READ_TIMEOUT_S
        assert window - step <= sum(clock.slept) <= window
        assert com.names()[-1] == "delete"
        assert list(runs.iterdir()) == [], "the marker is removed even on a timeout"

    def test_failed_reads_are_missed_samples_not_verdicts(self, runs):
        com = _FakeCom(
            reads=[TaskComError(0x80070005, task_com.MSG_ACCESS_DENIED), _ready(CHECK_EXIT_OK)],
            on_run=lambda: _write_marker(runs),
        )
        assert _run(runs, com)[0] is CheckVerdict.OK

    def test_a_check_that_cannot_be_registered_carries_the_registration_code(self, runs):
        com = _FakeCom(register_exc=TaskComError(0x80070534, task_com.MSG_ACCOUNT_NOT_RECOGNIZED))
        assert _run(runs, com) == (CheckVerdict.CHECK_NOT_REGISTERED, 0x80070534)
        assert "run" not in com.names()
        assert com.names()[-1] == "delete", "a half-registered check task is still deleted"

    @pytest.mark.parametrize("exc", [ImportError("pywin32"), task_com.BoundedTimeout("check-register")])
    def test_any_registration_failure_is_the_same_verdict(self, runs, exc):
        assert _run(runs, _FakeCom(register_exc=exc)) == (CheckVerdict.CHECK_NOT_REGISTERED, None)

    def test_a_check_that_cannot_be_started_is_its_own_verdict(self, runs):
        com = _FakeCom(run_exc=TaskComError(0x80041326, "The task is disabled. (0x80041326)"))
        assert _run(runs, com) == (CheckVerdict.CHECK_NOT_STARTED, 0x80041326)
        assert com.names()[-1] == "delete"

    def test_the_task_and_the_marker_are_deleted_even_when_the_poll_raises(self, runs):
        """The ``finally`` is the guarantee — a raise from INSIDE the poll still cleans up."""

        def _boom(_seconds: float) -> None:
            raise RuntimeError("the poll fell over")

        com = _FakeCom(reads=[_RUNNING], on_run=lambda: _write_marker(runs))
        verdict = schedule_check.run_check(
            _params(),
            runs_dir=runs,
            com=com,
            token=_TOKEN,
            clock=time.monotonic,
            sleep=_boom,
            now=datetime.now,
            sweep_stale=True,
        )
        assert verdict == (CheckVerdict.UNAVAILABLE, None)
        assert com.names()[-1] == "delete"
        assert list(runs.iterdir()) == []

    def test_a_failed_task_delete_does_not_keep_the_marker(self, runs):
        com = _FakeCom(on_run=lambda: _write_marker(runs), delete_exc=TaskComError(None, "nope"))
        assert _run(runs, com)[0] is CheckVerdict.OK
        assert list(runs.iterdir()) == []

    def test_an_invalid_token_is_refused_before_anything_is_registered(self, runs):
        com = _FakeCom()
        verdict = schedule_check.run_check(
            _params(),
            runs_dir=runs,
            com=com,
            token="../../evil",
            clock=time.monotonic,
            sleep=lambda s: None,
            now=datetime.now,
            sweep_stale=True,
        )
        assert verdict == (CheckVerdict.UNAVAILABLE, None)
        assert com.calls == []


class TestTheDeadline:
    """Review fix 1: ONE deadline every step respects, with the cleanup delete's time RESERVED.

    ``worst_case`` makes every COM call take its FULL bound (a call that hangs until its worker
    gives up) — the shape that used to overrun the parent's elevation wait.
    """

    def _worst(self, runs, **com_kwargs):
        clock = _Clock()
        com = _FakeCom(clock=clock, worst_case=True, **com_kwargs)
        start = clock.now
        verdict = _run(runs, com)
        return verdict, com, start, clock

    def test_every_call_finishes_inside_the_deadline_including_the_trailing_read(self, runs):
        verdict, com, start, clock = self._worst(runs, reads=[_RUNNING])
        assert verdict[0] is CheckVerdict.TIMEOUT
        deadline = start + schedule_check.CHECK_DEADLINE_S
        work_end = deadline - schedule_check.DELETE_RESERVE_S
        work = [c for c in com.calls if c[0] != "delete"]
        assert [c[0] for c in work][:3] == ["lookup", "register", "run"]
        assert sum(1 for c in work if c[0] == "read") >= 2, "non-vacuity: the poll really sampled"
        for op, _name, bound, at, *_rest in work:
            assert at + bound <= work_end + 1e-9, f"{op} could run past the delete reserve"
        assert clock.now <= deadline + 1e-9, "the whole check ended inside its deadline"

    def test_the_delete_always_gets_its_reserved_time(self, runs):
        _verdict, com, start, _clock = self._worst(runs, reads=[_RUNNING])
        op, _name, bound, at, *_rest = com.calls[-1]
        assert op == "delete"
        assert bound == schedule_check.DELETE_RESERVE_S
        assert at + bound <= start + schedule_check.CHECK_DEADLINE_S + 1e-9

    def test_a_check_with_no_time_left_starts_no_call_it_cannot_finish(self, runs):
        """A deadline already spent before registration: nothing is registered, nothing leaks."""
        clock = _Clock()
        com = _FakeCom(clock=clock)
        verdict = schedule_check.run_check(
            _params(),
            runs_dir=runs,
            com=com,
            token=_TOKEN,
            clock=clock.clock,
            sleep=clock.sleep,
            now=lambda: _RUN_AT,
            sweep_stale=False,
            deadline_s=schedule_check.DELETE_RESERVE_S,  # zero work budget
        )
        assert verdict == (CheckVerdict.TIMEOUT, None)
        assert com.calls == [], "no call was started that could not finish"

    def test_the_elevation_wait_covers_provision_plus_the_whole_check_plus_margin(self):
        """The parent KILLS the child at this bound and a killed child runs no ``finally`` — so
        the wait must be at least the sum of what the child can legitimately spend."""
        from src.scheduler import windows

        parts = (
            windows._ELEV_PROVISION_BUDGET_S
            + schedule_check.ADVISORY_BUDGET_S
            + schedule_check.CHECK_DEADLINE_S
            + windows._ELEV_MARGIN_S
        )
        assert parts <= windows._ELEV_TIMEOUT_S
        assert schedule_check.TOTAL_BUDGET_S >= schedule_check.ADVISORY_BUDGET_S + schedule_check.CHECK_DEADLINE_S
        assert schedule_check.ADVISORY_BUDGET_S >= 2 * schedule_check.ADVISORY_CALL_TIMEOUT_S
        assert windows._ELEV_PROVISION_BUDGET_S >= 120.0, "the provision budget this wait was sized for"
        assert schedule_check.DELETE_RESERVE_S < schedule_check.CHECK_DEADLINE_S

    def test_a_slow_advisory_read_is_unknown_and_does_not_hold_the_check(self, tmp_path, monkeypatch):
        root = tmp_path / "ProgramData" / "DistrictSync"
        (root / "runs").mkdir(parents=True)
        monkeypatch.setattr(paths, "machine_data_dir", lambda: root)
        monkeypatch.setattr(schedule_check, "run_check", lambda *a, **k: (CheckVerdict.OK, CHECK_EXIT_OK))

        def _slow_sid(_name: str) -> str:
            time.sleep(1.0)
            return "S-1"

        def _slow_query(_name: str) -> int:
            time.sleep(1.0)
            return 5

        started = time.monotonic()
        check = schedule_check.run_for_payload(
            _provision_payload(),
            com=_FakeCom(),
            account_sid=_slow_sid,
            install_query=_slow_query,
            advisory_timeout_s=0.05,
        )
        assert time.monotonic() - started < 0.9, "the bound, not the slow call, decided"
        assert (check.batch_right, check.installed) == (RightState.UNKNOWN, InstallState.UNKNOWN)
        assert check.verdict is CheckVerdict.OK, "the check still ran"


class TestTheCheckTaskName:
    """Review fix 2: never overwrite somebody else's task; clear our own leftover first."""

    def test_our_own_leftover_is_deleted_then_registered_fresh(self, runs):
        leftover = CheckTaskFacts(
            state=task_com.TASK_STATE_READY,
            last_result=task_com.RESULT_LOGON_FAILURE,  # the stale code a killed child left
            last_run=_BEFORE_RUN,
            arguments=f"--schedule-check {'e' * 32}",
        )
        com = _FakeCom(existing=leftover, on_run=lambda: _write_marker(runs))
        assert _run(runs, com) == (CheckVerdict.OK, CHECK_EXIT_OK)
        assert com.names()[:3] == ["lookup", "delete", "register"], "the leftover goes BEFORE we register"

    def test_a_source_runs_leftover_is_recognised_too(self, runs):
        leftover = CheckTaskFacts(state=3, last_result=0, arguments=f"-m src.main --schedule-check {'e' * 32}")
        com = _FakeCom(existing=leftover, on_run=lambda: _write_marker(runs))
        assert _run(runs, com)[0] is CheckVerdict.OK

    @pytest.mark.parametrize(
        "arguments",
        ["--sis myedbc --source scheduled", "", None, "--schedule-checker x", "backup.ps1 --schedule-check-ish"],
    )
    def test_somebody_elses_task_is_never_touched(self, runs, arguments):
        foreign = CheckTaskFacts(state=3, last_result=0, arguments=arguments)
        com = _FakeCom(existing=foreign)
        assert _run(runs, com) == (CheckVerdict.CHECK_NOT_REGISTERED, None)
        assert com.names() == ["lookup"], "not registered over, and not deleted by the finally"

    def test_a_foreign_name_reads_as_inconclusive_not_as_a_finding(self):
        assert ScheduleCheck(CheckVerdict.CHECK_NOT_REGISTERED).inconclusive is True

    def test_a_lookup_that_cannot_answer_overwrites_nothing(self, runs):
        com = _FakeCom(existing=TaskComError(0x80070005, task_com.MSG_ACCESS_DENIED))
        assert _run(runs, com) == (CheckVerdict.CHECK_NOT_REGISTERED, 0x80070005)
        assert com.names() == ["lookup"]

    def test_a_leftover_that_cannot_be_cleared_is_not_registered_over(self, runs):
        leftover = CheckTaskFacts(state=3, last_result=0, arguments=f"--schedule-check {'e' * 32}")
        com = _FakeCom(existing=leftover, delete_exc=TaskComError(0x80070005, task_com.MSG_ACCESS_DENIED))
        assert _run(runs, com) == (CheckVerdict.CHECK_NOT_REGISTERED, 0x80070005)
        assert "register" not in com.names()


class TestFreshResultsOnly:
    """Review fix 2b: a result recorded BEFORE our Run call is never read as this check's."""

    def test_a_stale_last_task_result_is_ignored_until_a_fresh_one_arrives(self, runs):
        stale = _ready(task_com.RESULT_LOGON_FAILURE, last_run=_BEFORE_RUN)
        com = _FakeCom(reads=[stale, stale, _ready(CHECK_EXIT_OK)], on_run=lambda: _write_marker(runs))
        assert _run(runs, com) == (CheckVerdict.OK, CHECK_EXIT_OK)

    def test_only_stale_results_end_in_a_timeout_that_reports_no_stale_code(self, runs):
        stale = _ready(task_com.RESULT_LOGON_FAILURE, last_run=_BEFORE_RUN)
        assert _run(runs, _FakeCom(reads=[stale])) == (CheckVerdict.TIMEOUT, None)

    def test_a_result_with_no_run_time_counts_once_the_task_was_seen_running(self, runs):
        undated = _ready(task_com.RESULT_BATCH_LOGON_PROBLEM, last_run=None)
        assert _run(runs, _FakeCom(reads=[undated])) == (CheckVerdict.TIMEOUT, None), "undated alone is stale"
        com = _FakeCom(reads=[_RUNNING, undated])
        assert _run(runs, com) == (CheckVerdict.BATCH_LOGON, task_com.RESULT_BATCH_LOGON_PROBLEM)

    def test_a_run_in_the_same_second_as_the_run_call_counts(self, runs):
        same_second = _ready(task_com.RESULT_ACCESS_DENIED, last_run="2026-10-23T14:00:00")
        assert _run(runs, _FakeCom(reads=[same_second]))[0] is CheckVerdict.ACCESS_DENIED


class TestStaleMarkerSweep:
    def test_the_sweep_is_prefix_scoped_and_age_scoped(self, runs):
        old = time.time() - 2 * 3600
        stale = runs / "schedule-check-aaaa.json"
        fresh = runs / "schedule-check-bbbb.json"
        log = runs / "etl_tool-corp_svc.log"
        store = runs / "history.db"
        for path in (stale, fresh, log, store):
            path.write_bytes(b"x")
        for path in (stale, log, store):
            os.utime(path, (old, old))

        assert schedule_check.sweep_stale_markers(runs, now=time.time()) == 1
        assert sorted(p.name for p in runs.iterdir()) == sorted([fresh.name, log.name, store.name])

    def test_a_missing_runs_folder_is_zero_not_a_raise(self, tmp_path):
        assert schedule_check.sweep_stale_markers(tmp_path / "absent", now=time.time()) == 0

    def _stale_marker(self, runs: Path) -> Path:
        stale = runs / "schedule-check-aaaa.json"
        stale.write_bytes(b"x")
        old = time.time() - 2 * 3600
        os.utime(stale, (old, old))
        return stale

    def test_the_check_sweeps_only_when_told_the_folder_is_trusted(self, runs):
        stale = self._stale_marker(runs)
        _run(runs, _FakeCom(on_run=lambda: _write_marker(runs)), sweep_stale=False)
        assert stale.exists(), "an untrusted folder is not swept by the elevated child"
        _run(runs, _FakeCom(on_run=lambda: _write_marker(runs)), sweep_stale=True)
        assert not stale.exists(), "the positive twin: a trusted folder is"

    @pytest.mark.parametrize("trusted", [True, False])
    def test_run_for_payload_gates_the_sweep_on_the_trust_check_and_runs_the_check_either_way(
        self, tmp_path, monkeypatch, trusted
    ):
        root = tmp_path / "ProgramData" / "DistrictSync"
        (root / "runs").mkdir(parents=True)
        monkeypatch.setattr(paths, "machine_data_dir", lambda: root)

        def _trust(path: Path) -> None:
            assert path == root
            if not trusted:
                raise paths.MachineScopeRefused(paths.MachineScopeRefusedReason.OPEN_ACE, path)

        monkeypatch.setattr(paths, "assert_machine_dir_trusted", _trust)
        seen: dict = {}

        def _fake_run_check(params, **kwargs):
            seen.update(kwargs)
            return CheckVerdict.OK, CHECK_EXIT_OK

        monkeypatch.setattr(schedule_check, "run_check", _fake_run_check)
        check = schedule_check.run_for_payload(
            _provision_payload(), com=_FakeCom(), account_sid=lambda n: "S-1", install_query=lambda n: 5
        )
        assert seen["sweep_stale"] is trusted
        assert check.verdict is CheckVerdict.OK

    def test_a_trust_check_that_cannot_run_here_skips_the_sweep_never_the_check(self, tmp_path, monkeypatch):
        # e.g. the Windows security read on a POSIX runner raises AttributeError, not OSError.
        root = tmp_path / "ProgramData" / "DistrictSync"
        (root / "runs").mkdir(parents=True)
        monkeypatch.setattr(paths, "machine_data_dir", lambda: root)

        def _unavailable(path: Path) -> None:
            raise AttributeError("no windll here")

        monkeypatch.setattr(paths, "assert_machine_dir_trusted", _unavailable)
        seen: dict = {}

        def _fake_run_check(params, **kwargs):
            seen.update(kwargs)
            return CheckVerdict.OK, CHECK_EXIT_OK

        monkeypatch.setattr(schedule_check, "run_check", _fake_run_check)
        check = schedule_check.run_for_payload(
            _provision_payload(), com=_FakeCom(), account_sid=lambda n: "S-1", install_query=lambda n: 5
        )
        assert seen["sweep_stale"] is False
        assert check.verdict is CheckVerdict.OK


# --------------------------------------------------------------------------- #
# The advisory facts                                                          #
# --------------------------------------------------------------------------- #
class TestBatchRight:
    _SID = "S-1-5-21-1-2-3-4567"

    @pytest.mark.parametrize(
        ("rights", "state"),
        [
            ({"SeDenyBatchLogonRight"}, RightState.DENIED),
            ({"SeDenyBatchLogonRight", "SeBatchLogonRight"}, RightState.DENIED),
            ({"SeBatchLogonRight", "SeServiceLogonRight"}, RightState.GRANTED),
            ({"SeServiceLogonRight"}, RightState.NOT_DIRECT),
            (set(), RightState.NOT_DIRECT),
        ],
    )
    def test_the_accounts_own_rights_decide(self, rights, state):
        seen: list[str] = []

        def _reader(sid: str):
            seen.append(sid)
            return rights

        assert schedule_check.batch_right_state(self._SID, reader=_reader) is state
        assert seen == [self._SID]

    def test_no_rights_assigned_is_not_direct_not_unknown(self):
        def _reader(_sid: str):
            raise schedule_check.NoRightsAssigned

        assert schedule_check.batch_right_state(self._SID, reader=_reader) is RightState.NOT_DIRECT

    @pytest.mark.parametrize("exc", [OSError("LsaOpenPolicy"), ImportError("win32security"), RuntimeError("x")])
    def test_an_api_that_cannot_answer_is_unknown_never_a_failure(self, exc):
        def _reader(_sid: str):
            raise exc

        assert schedule_check.batch_right_state(self._SID, reader=_reader) is RightState.UNKNOWN


class TestInstallState:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (5, InstallState.INSTALLED),
            (4, InstallState.NOT_INSTALLED),
            (3, InstallState.NOT_PERMITTED),
            (2, InstallState.UNKNOWN),  # MsaInfoNotService — could be a name-form artefact
            (1, InstallState.UNKNOWN),  # MsaInfoNotExist — the account already resolved to a SID
            (99, InstallState.UNKNOWN),
        ],
    )
    def test_the_msa_info_state_table(self, state, expected):
        seen: list[str] = []

        def _query(name: str) -> int:
            seen.append(name)
            return state

        assert schedule_check.install_state(_GMSA, query=_query) is expected
        assert seen == ["svc_districtsync$"], "asked with the name after the domain prefix"

    def test_an_api_that_cannot_answer_is_unknown_never_a_failure(self):
        def _query(_name: str) -> int:
            raise OSError("NetQueryServiceAccount returned 0xc0000022")

        assert schedule_check.install_state(_GMSA, query=_query) is InstallState.UNKNOWN


# --------------------------------------------------------------------------- #
# The bounded channel                                                         #
# --------------------------------------------------------------------------- #
class TestTheBoundedChannel:
    def test_the_wire_form_is_three_enum_values_and_an_int(self):
        wire = ScheduleCheck(
            verdict=CheckVerdict.BATCH_LOGON,
            code=task_com.RESULT_BATCH_LOGON_PROBLEM,
            batch_right=RightState.NOT_DIRECT,
            installed=InstallState.INSTALLED,
        ).to_wire()
        assert set(wire) == {"verdict", "code", "batch_right", "installed"}
        assert wire["verdict"] in {v.value for v in CheckVerdict}
        assert wire["batch_right"] in {v.value for v in RightState}
        assert wire["installed"] in {v.value for v in InstallState}
        assert isinstance(wire["code"], int)

    def test_it_round_trips(self):
        check = ScheduleCheck(CheckVerdict.OK, CHECK_EXIT_OK, RightState.GRANTED, InstallState.INSTALLED)
        assert ScheduleCheck.from_wire(json.loads(json.dumps(check.to_wire()))) == check

    def test_no_check_is_none(self):
        assert ScheduleCheck.from_wire(None) is None

    @pytest.mark.parametrize(
        "raw",
        [
            "free text from somewhere",
            ["ok"],
            {"verdict": "C:\\Users\\jane\\AppData", "code": "0x8007052E"},
            {"verdict": "Windows said: CORP\\svc$ denied", "code": True},
            {"verdict": "ok", "code": -5, "batch_right": "maybe", "installed": "CORP\\svc$"},
            {"verdict": "ok", "code": 1 << 40},
        ],
    )
    def test_anything_unrecognised_is_dropped_never_carried(self, raw):
        check = ScheduleCheck.from_wire(raw)
        assert check is not None
        text = repr(check)
        for leak in ("C:\\", "CORP", "free text", "0x8007052E", "maybe"):
            assert leak not in text
        if isinstance(raw, dict) and raw.get("verdict") == "ok":
            assert check.verdict is CheckVerdict.OK
            assert check.code is None
        else:
            assert check.verdict is CheckVerdict.UNAVAILABLE


class TestRequested:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"schedule_check": True, "kind": "managed_service_account"}, True),
            ({"schedule_check": True, "kind": "password"}, False),
            ({"schedule_check": True, "kind": "interactive_token"}, False),
            ({"schedule_check": False, "kind": "managed_service_account"}, False),
            ({"kind": "managed_service_account"}, False),  # an older parent never asked
            ({"schedule_check": "true", "kind": "managed_service_account"}, False),
            ({"schedule_check": True, "kind": "service_account"}, False),
        ],
    )
    def test_only_an_explicit_ask_for_a_kind_it_applies_to(self, payload, expected):
        assert schedule_check.requested(payload) is expected


# --------------------------------------------------------------------------- #
# run_for_payload — the elevated child's entry                                #
# --------------------------------------------------------------------------- #
def _provision_payload(**over: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "op": "provision",
        "schedule_check": True,
        "task_name": "DistrictSync_Daily",
        "exe": _EXE,
        "arguments": "--sis myedbc --source scheduled",
        "working_dir": _WORKDIR,
        "run_time": "03:00",
        "user": _GMSA,
        "kind": PrincipalKind.MANAGED_SERVICE_ACCOUNT.value,
        "run_highest": True,
        "wake_to_run": True,
        "password": "",
    }
    payload.update(over)
    return payload


class TestRunForPayload:
    def test_the_check_runs_against_the_shared_runs_folder_with_the_payloads_command(self, tmp_path, monkeypatch):
        root = tmp_path / "ProgramData" / "DistrictSync"
        (root / "runs").mkdir(parents=True)
        monkeypatch.setattr(paths, "machine_data_dir", lambda: root)
        seen: dict = {}

        def _fake_run_check(params, *, runs_dir, com, token, clock, sleep, now, sweep_stale):
            seen.update(params=params, runs_dir=runs_dir, token=token, sweep_stale=sweep_stale)
            return CheckVerdict.OK, CHECK_EXIT_OK

        monkeypatch.setattr(schedule_check, "run_check", _fake_run_check)
        check = schedule_check.run_for_payload(
            _provision_payload(),
            com=_FakeCom(),
            account_sid=lambda name: "S-1-5-21-9",
            rights_reader=lambda sid: {"SeBatchLogonRight"},
            install_query=lambda name: 5,
        )
        assert check == ScheduleCheck(CheckVerdict.OK, CHECK_EXIT_OK, RightState.GRANTED, InstallState.INSTALLED)
        assert seen["runs_dir"] == root / "runs"
        params = seen["params"]
        assert (params.user, params.password, params.kind) == (_GMSA, None, PrincipalKind.MANAGED_SERVICE_ACCOUNT)
        assert (params.exe, params.working_dir, params.run_highest) == (_EXE, _WORKDIR, True)
        assert len(seen["token"]) == 32

    def test_a_sid_that_does_not_resolve_leaves_the_right_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "machine_data_dir", lambda: tmp_path)
        monkeypatch.setattr(schedule_check, "run_check", lambda *a, **k: (CheckVerdict.OK, CHECK_EXIT_OK))

        def _no_sid(_name: str) -> str:
            raise provisioning.AccountLookupError(1311)

        check = schedule_check.run_for_payload(
            _provision_payload(), com=_FakeCom(), account_sid=_no_sid, install_query=lambda n: 4
        )
        assert check.batch_right is RightState.UNKNOWN
        assert check.installed is InstallState.NOT_INSTALLED

    @pytest.mark.parametrize(
        "over",
        [{"user": "svc && calc$"}, {"kind": "service_account"}, {"run_time": "99:99"}, {"task_name": "a;b"}],
    )
    def test_a_malformed_payload_is_unavailable_and_nothing_is_registered(self, monkeypatch, over):
        com = _FakeCom()
        monkeypatch.setattr(schedule_check, "run_check", lambda *a, **k: pytest.fail("must not run"))
        check = schedule_check.run_for_payload(_provision_payload(**over), com=com)
        assert check.verdict is CheckVerdict.UNAVAILABLE
        assert com.calls == []

    def test_an_unresolvable_machine_root_is_unavailable_not_a_raise(self, monkeypatch):
        def _refused():
            raise paths.MachineScopeRefused(paths.MachineScopeRefusedReason.REDIRECTED, Path("x"))

        monkeypatch.setattr(paths, "machine_data_dir", _refused)
        check = schedule_check.run_for_payload(
            _provision_payload(), com=_FakeCom(), account_sid=lambda n: "S-1", install_query=lambda n: 5
        )
        assert check.verdict is CheckVerdict.UNAVAILABLE


# --------------------------------------------------------------------------- #
# The hidden CLI mode — runs AS the account                                   #
# --------------------------------------------------------------------------- #
def _force_machine_scope(monkeypatch, tmp_path, *, with_runs: bool = True, trusted: bool = True) -> Path:
    """Machine scope through ALL THREE raw reads of the trust predicate (never the real API)."""
    root = tmp_path / "ProgramData" / "DistrictSync"
    root.mkdir(parents=True)
    if with_runs:
        (root / "runs").mkdir()
    owner = "S-1-5-32-544" if trusted else "S-1-5-21-6-6-6-666"
    monkeypatch.setattr(paths, "machine_data_dir", lambda: root)
    monkeypatch.setattr(paths, "_machine_switch_on", lambda: True)
    monkeypatch.setattr(paths, "_read_dir_security", lambda path: (owner, paths._SE_DACL_PROTECTED))
    monkeypatch.setattr(paths, "_read_dacl_aces", lambda path: _TRUSTED_ACES)
    paths.reset_data_dir_pin()
    return root


class _Tripwires:
    """Record every reach for ETL, delivery, the run store, config, migration or a log sink."""

    def __init__(self, monkeypatch) -> None:
        self.hits: list[str] = []
        import src.main as main_mod
        from src.config.app_config import AppConfig
        from src.history import store

        def _hit(name: str, value: object = None):
            def _record(*_a, **_k):
                self.hits.append(name)
                return value

            return _record

        monkeypatch.setattr(
            main_mod, "run_pipeline", _hit("run_pipeline", SimpleNamespace(sftp_attempted=False, sftp_ok=True))
        )
        monkeypatch.setattr(main_mod, "SFTPUploader", _hit("sftp"))
        monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", _hit("migrate"))
        monkeypatch.setattr(main_mod, "_configure_cli_logging", _hit("log_sink", logging.getLogger("tripwire")))
        monkeypatch.setattr(main_mod, "_attach_parent_console", _hit("console", False))
        monkeypatch.setattr(store, "write_run_record", _hit("history"))
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: self.hits.append("config") or AppConfig()))


def _all_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


class TestTheHiddenMode:
    def test_it_writes_only_the_marker_and_exits_with_its_own_code(self, tmp_path, monkeypatch):
        from src.main import cli

        root = _force_machine_scope(monkeypatch, tmp_path)
        wires = _Tripwires(monkeypatch)
        before = _all_files(tmp_path)

        assert cli(["--schedule-check", _TOKEN]) == CHECK_EXIT_OK

        marker = root / "runs" / f"schedule-check-{_TOKEN}.json"
        assert _all_files(tmp_path) - before == {marker}, "the marker is the ONLY write"
        assert wires.hits == [], f"the check reached {wires.hits}"
        body = json.loads(marker.read_bytes())
        assert set(body) == {"token", "account", "at", "version"}
        assert body["token"] == _TOKEN
        assert "\\" not in body["account"] and "/" not in body["account"], "sanitised, never DOMAIN\\user"
        assert schedule_check.marker_ok(marker, _TOKEN)

    def test_the_positive_twin_the_tripwires_are_live(self, tmp_path, monkeypatch):
        """Without this, the row above would pass on tripwires that can never fire."""
        from src.main import cli

        _force_machine_scope(monkeypatch, tmp_path)
        wires = _Tripwires(monkeypatch)
        cli(["--sis", "myedbc", "--input", str(tmp_path), "--output", str(tmp_path / "out")])
        assert {"migrate", "console", "log_sink", "run_pipeline"} <= set(wires.hits)

    @pytest.mark.parametrize("args", [[], ["nothex"], ["../../x"], [_TOKEN, "extra"], [_TOKEN.upper()]])
    def test_a_bad_request_is_refused_and_writes_nothing(self, tmp_path, monkeypatch, args):
        from src.main import cli

        _force_machine_scope(monkeypatch, tmp_path)
        before = _all_files(tmp_path)
        assert cli(["--schedule-check", *args]) == CHECK_EXIT_BAD_REQUEST
        assert _all_files(tmp_path) == before

    def test_a_per_user_resolution_is_not_shared_and_writes_no_marker(self, tmp_path):
        """The suite's own isolation forces the switch OFF — exactly a nightly that would run
        without the shared settings."""
        assert schedule_check.run_check_mode([_TOKEN]) == CHECK_EXIT_NOT_SHARED
        assert not list(tmp_path.rglob("schedule-check-*"))

    def test_a_refused_shared_folder_is_profile_refused(self, tmp_path, monkeypatch):
        _force_machine_scope(monkeypatch, tmp_path, trusted=False)
        assert schedule_check.run_check_mode([_TOKEN]) == CHECK_EXIT_PROFILE_REFUSED
        assert not list(tmp_path.rglob("schedule-check-*"))

    def test_an_unwritable_runs_folder_is_its_own_code(self, tmp_path, monkeypatch):
        _force_machine_scope(monkeypatch, tmp_path, with_runs=False)
        assert schedule_check.run_check_mode([_TOKEN]) == CHECK_EXIT_MARKER_UNWRITABLE

    def test_the_flag_is_recognised_above_the_preamble(self):
        """The argv pre-check spells the SAME flag the check task's action carries."""
        import inspect

        import src.main as main_mod

        source = inspect.getsource(main_mod._cli)
        assert f'"{schedule_check.CHECK_MODE_FLAG}"' in source
        assert source.index(schedule_check.CHECK_MODE_FLAG) < source.index("migrate_legacy_data_dir()")


# --------------------------------------------------------------------------- #
# The elevated child                                                          #
# --------------------------------------------------------------------------- #
def _child_run(tmp_path: Path, payload: dict, *, check: ScheduleCheck | None = None, provision_exc=None):
    req = tmp_path / "dsync_elev_x.req"
    req.write_bytes(b"sealed")
    res = tmp_path / "dsync_elev_x.res"
    order: list[str] = []

    def _provision(_payload, *, register):
        order.append("provision")
        if provision_exc is not None:
            raise provision_exc

    def _check(_payload):
        order.append("check")
        return check or ScheduleCheck(CheckVerdict.OK, CHECK_EXIT_OK)

    with (
        patch("src.scheduler.elevation.unprotect_blob", return_value=json.dumps(payload).encode()),
        patch("src.scheduler.provisioning.apply_provision", side_effect=_provision),
        patch("src.scheduler.schedule_check.run_for_payload", side_effect=_check),
    ):
        assert elevated_apply.run_elevated_apply([str(req), str(res)]) == 0
    return json.loads(res.read_text(encoding="utf-8")), order


class TestTheElevatedChild:
    def test_a_requested_check_runs_after_the_provision_in_the_same_op(self, tmp_path):
        found = ScheduleCheck(
            CheckVerdict.LOGON_FAILURE, task_com.RESULT_LOGON_FAILURE, RightState.UNKNOWN, InstallState.NOT_PERMITTED
        )
        out, order = _child_run(tmp_path, _provision_payload(source_data_dir="C:\\x"), check=found)
        assert order == ["provision", "check"]
        assert out == {"ok": True, "message": "", "check": found.to_wire()}, "ok either way — never rolled back"

    def test_no_check_unless_asked(self, tmp_path):
        payload = _provision_payload(source_data_dir="C:\\x")
        payload.pop("schedule_check")
        out, order = _child_run(tmp_path, payload)
        assert order == ["provision"]
        assert out == {"ok": True, "message": ""}, "the pre-DSYNC-33 result shape, byte for byte"

    def test_no_check_for_a_password_principal(self, tmp_path):
        payload = _provision_payload(
            source_data_dir="C:\\x", kind=PrincipalKind.PASSWORD.value, user="CORP\\svc", password="pw"
        )
        out, order = _child_run(tmp_path, payload)
        assert order == ["provision"]
        assert "check" not in out

    def test_a_failed_provision_runs_no_check(self, tmp_path):
        out, order = _child_run(
            tmp_path,
            _provision_payload(source_data_dir="C:\\x"),
            provision_exc=provisioning.ProvisionRefused(provisioning.ProvisionStep.CREATE),
        )
        assert order == ["provision"]
        assert out["ok"] is False
        assert "check" not in out

    def test_the_result_file_carries_no_free_text_account_or_path(self, tmp_path):
        out, _order = _child_run(tmp_path, _provision_payload(source_data_dir="C:\\Users\\jane"))
        text = json.dumps(out)
        for leak in ("svc_districtsync", "CORP", "C:\\\\", "Program Files", "jane"):
            assert leak not in text


# --------------------------------------------------------------------------- #
# The parent — pre-UAC resolution, the check read-back, the log line          #
# --------------------------------------------------------------------------- #
def _gmsa_principal() -> task_com.Principal:
    return task_com.Principal(kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT, user=_GMSA)


class TestPreUacResolution:
    @pytest.mark.parametrize(
        ("winerror", "outcome"),
        [
            (provisioning.ERROR_NONE_MAPPED, provision_session.ProvisionOutcome.ACCOUNT_NOT_FOUND),
            (1311, provision_session.ProvisionOutcome.ACCOUNT_LOOKUP_FAILED),  # ERROR_NO_LOGON_SERVERS
            (1789, provision_session.ProvisionOutcome.ACCOUNT_LOOKUP_FAILED),  # trust relationship failed
            (None, provision_session.ProvisionOutcome.ACCOUNT_LOOKUP_FAILED),
        ],
    )
    def test_an_unresolvable_account_is_refused_before_any_prompt(self, monkeypatch, caplog, winerror, outcome):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch)

        def _lookup(_name: str) -> str:
            raise provisioning.AccountLookupError(winerror)

        monkeypatch.setattr(provisioning, "account_sid", _lookup)
        with caplog.at_level(logging.WARNING, logger="src.scheduler.provision_session"):
            attempt = _provision(principal=_gmsa_principal())

        assert attempt.outcome is outcome
        assert sent == [], "nothing was sealed, so no prompt could have been shown"
        assert _GMSA not in caplog.text and "svc_districtsync" not in caplog.text

    def test_the_positive_twin_a_resolved_account_proceeds_and_asks_for_the_check(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch)
        looked_up: list[str] = []
        monkeypatch.setattr(provisioning, "account_sid", lambda name: looked_up.append(name) or "S-1-5-21-9")

        _provision(principal=_gmsa_principal())

        assert looked_up == [_GMSA]
        assert len(sent) == 1
        assert sent[0]["schedule_check"] is True

    def test_a_password_principal_is_not_looked_up_and_asks_for_no_check(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch)
        monkeypatch.setattr(provisioning, "account_sid", lambda name: pytest.fail("not for a password principal"))

        attempt = _provision()

        assert sent[0]["schedule_check"] is False
        assert attempt.check is None


class TestTheCheckReadBack:
    def _ok_with(self, monkeypatch, check_wire):
        payload: dict[str, object] = {"ok": True, "message": ""}
        if check_wire is not None:
            payload["check"] = check_wire
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload=payload)
        _store(monkeypatch)
        monkeypatch.setattr(provisioning, "account_sid", lambda name: "S-1-5-21-9")

    def test_the_childs_check_rides_back_on_the_attempt(self, monkeypatch):
        found = ScheduleCheck(CheckVerdict.BATCH_LOGON, task_com.RESULT_BATCH_LOGON_PROBLEM, RightState.NOT_DIRECT)
        self._ok_with(monkeypatch, found.to_wire())
        attempt = _provision(principal=_gmsa_principal())
        assert attempt.outcome is provision_session.ProvisionOutcome.PROVISIONED
        assert attempt.check == found

    def test_asked_but_absent_is_unavailable_not_silence(self, monkeypatch):
        self._ok_with(monkeypatch, None)
        attempt = _provision(principal=_gmsa_principal())
        assert attempt.check == ScheduleCheck(CheckVerdict.UNAVAILABLE)

    def test_one_bounded_log_line_names_the_verdict_and_code_and_no_account(self, monkeypatch, caplog):
        self._ok_with(monkeypatch, ScheduleCheck(CheckVerdict.LOGON_FAILURE, task_com.RESULT_LOGON_FAILURE).to_wire())
        with caplog.at_level(logging.INFO, logger="src.scheduler.provision_session"):
            _provision(principal=_gmsa_principal())
        lines = [r for r in caplog.records if r.getMessage().startswith("Schedule check after registration")]
        assert len(lines) == 1
        line = lines[0]
        assert line.levelno == logging.WARNING
        assert "LOGON_FAILURE" in line.getMessage()
        assert "0x8007052E" in line.getMessage()
        assert "svc_districtsync" not in caplog.text and "Program Files" not in caplog.text

    def test_a_passing_check_logs_at_info(self, monkeypatch, caplog):
        self._ok_with(monkeypatch, ScheduleCheck(CheckVerdict.OK, CHECK_EXIT_OK).to_wire())
        with caplog.at_level(logging.INFO, logger="src.scheduler.provision_session"):
            _provision(principal=_gmsa_principal())
        line = next(r for r in caplog.records if r.getMessage().startswith("Schedule check after registration"))
        assert line.levelno == logging.INFO
        assert "OK" in line.getMessage()


# --------------------------------------------------------------------------- #
# Remove — the managed service account's folder access goes with the task     #
# --------------------------------------------------------------------------- #
class TestRemovalWithRevoke:
    def _confirm(self, monkeypatch, found: bool | None) -> None:
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=found))

    def _remove(self):
        return provision_session.request_removal_with_revoke(
            "DistrictSync_Daily", user=_GMSA, kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT
        )

    def test_the_payload_is_the_prune_op_with_the_recorded_principal(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        self._confirm(monkeypatch, False)
        attempt = self._remove()
        assert sent == [
            {
                "op": "prune_principal",
                "task_name": "DistrictSync_Daily",
                "user": _GMSA,
                "kind": "managed_service_account",
            }
        ]
        assert (attempt.ok, attempt.revoke) == (True, provision_session.RevokeOutcome.REVOKED)

    def test_success_is_confirmed_by_read_back_never_taken_from_the_child(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        self._confirm(monkeypatch, True)  # the child said ok, the task is still there
        attempt = self._remove()
        assert attempt.ok is False
        assert attempt.message == provision_session.windows._MSG_ELEVATION_REMOVE_UNCONFIRMED
        assert attempt.revoke is provision_session.RevokeOutcome.NOT_REVOKED

    def test_a_failed_prune_still_removed_the_nightly(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": _refusal(provisioning.ProvisionStep.PRUNE, icacls_exit=5)},
        )
        self._confirm(monkeypatch, False)
        attempt = self._remove()
        assert (attempt.ok, attempt.revoke) == (True, provision_session.RevokeOutcome.NOT_REVOKED)

    def test_an_unconfirmed_delete_prunes_nothing_and_resolves_by_read_back(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": _refusal(provisioning.ProvisionStep.DELETE)},
        )
        self._confirm(monkeypatch, None)
        attempt = self._remove()
        assert attempt.ok is False
        assert attempt.message == provision_session.windows._MSG_ELEVATION_REMOVE_UNCONFIRMED

    @pytest.mark.parametrize(
        "step",
        [
            provisioning.ProvisionStep.PRINCIPAL,
            provisioning.ProvisionStep.PRE_EXISTING,
            provisioning.ProvisionStep.OVERRIDE,
        ],
    )
    def test_a_refusal_before_the_delete_falls_back_to_the_ordinary_remove(self, monkeypatch, step):
        """A gMSA deleted from the directory no longer resolves — that must never be the reason
        a nightly cannot be removed."""
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": False, "message": _refusal(step)})
        assert self._remove().revoke is provision_session.RevokeOutcome.FALL_BACK

    def test_a_declined_prompt_is_the_ordinary_declined_message(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.DECLINED)
        attempt = self._remove()
        assert (attempt.ok, attempt.message) == (False, provision_session.windows._MSG_UAC_DECLINED)

    def test_a_different_administrator_is_the_ordinary_message_and_never_the_sentinel(self, monkeypatch):
        _child(
            monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": False, "message": "DSYNC_DIFFERENT_ACCOUNT"}
        )
        attempt = self._remove()
        assert attempt.message == provision_session.windows._MSG_DIFFERENT_ACCOUNT
        assert "DSYNC_" not in repr(attempt)

    def test_a_timeout_resolves_by_read_back(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.TIMEOUT)
        self._confirm(monkeypatch, False)
        attempt = self._remove()
        assert (attempt.ok, attempt.revoke) == (True, provision_session.RevokeOutcome.NOT_REVOKED)

    def test_a_handshake_that_cannot_be_built_falls_back(self, monkeypatch):
        def _boom(_payload):
            raise OSError("no profile")

        monkeypatch.setattr(provision_session.elevation, "write_request", _boom)
        assert self._remove().revoke is provision_session.RevokeOutcome.FALL_BACK

    def test_a_malformed_account_falls_back_without_a_prompt(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        attempt = provision_session.request_removal_with_revoke(
            "DistrictSync_Daily", user="svc && calc$", kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT
        )
        assert attempt.revoke is provision_session.RevokeOutcome.FALL_BACK
        assert sent == []


# --------------------------------------------------------------------------- #
# task_com — the check task's definition, against fake COM objects            #
# --------------------------------------------------------------------------- #
class TestTheCheckTaskDefinition:
    def _register(self, params: RegisterParams):
        from unittest.mock import MagicMock

        service, folder = MagicMock(), MagicMock()
        task_com.apply_check_definition(
            service,
            folder,
            params,
            check_name="DistrictSync_Daily_Check",
            arguments=f"--schedule-check {_TOKEN}",
            start_boundary="2026-10-22T14:00:00",
            end_boundary="2026-10-23T14:15:00",
        )
        assert folder.RegisterTaskDefinition.call_count == 1
        return service, folder.RegisterTaskDefinition.call_args[0]

    def test_it_carries_the_nightlys_principal_program_folder_and_run_level(self):
        params = _params()
        service, (name, definition, flags, user, password, logon) = self._register(params)
        assert name == "DistrictSync_Daily_Check"
        assert (user, password, logon) == (_GMSA, None, task_com.TASK_LOGON_PASSWORD)
        assert flags == task_com.TASK_CREATE_OR_UPDATE
        assert definition.Principal.RunLevel == task_com.TASK_RUNLEVEL_HIGHEST
        action = definition.Actions.Create.return_value
        assert (action.Path, action.WorkingDirectory) == (_EXE, _WORKDIR)
        assert action.Arguments == f"--schedule-check {_TOKEN}"
        definition.Actions.Create.assert_called_once_with(task_com.TASK_ACTION_EXEC)

    def test_it_never_fires_on_its_own_and_expires_into_deletion(self):
        _service, (_n, definition, *_rest) = self._register(_params())
        definition.Triggers.Create.assert_called_once_with(task_com.TASK_TRIGGER_TIME)
        trigger = definition.Triggers.Create.return_value
        assert (trigger.StartBoundary, trigger.EndBoundary) == ("2026-10-22T14:00:00", "2026-10-23T14:15:00")
        settings = definition.Settings
        assert settings.StartWhenAvailable is False, "a past start boundary must not catch up"
        assert settings.DeleteExpiredTaskAfter == task_com.CHECK_DELETE_EXPIRED_AFTER
        assert settings.ExecutionTimeLimit == task_com.CHECK_EXECUTION_LIMIT

    def test_the_nightlys_own_definition_is_unchanged_by_the_refactor(self):
        """The nightly still gets the daily trigger and the PT2H limit — and no expiry."""
        from unittest.mock import MagicMock

        service, folder = MagicMock(), MagicMock()
        task_com.apply_definition(service, folder, _params())
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        definition.Triggers.Create.assert_called_once_with(task_com.TASK_TRIGGER_DAILY)
        assert definition.Settings.ExecutionTimeLimit == "PT2H"
        assert definition.Actions.Create.return_value.Arguments == _params().arguments
        assert not isinstance(definition.Settings.DeleteExpiredTaskAfter, str)

    @pytest.mark.parametrize(
        ("state", "result", "expected"),
        [
            (3, 0x8007052E, CheckTaskFacts(state=3, last_result=0x8007052E)),
            (4, -2147023570, CheckTaskFacts(state=4, last_result=0x8007052E)),
            (True, None, CheckTaskFacts(state=None, last_result=None)),
            ("x", "y", CheckTaskFacts(state=None, last_result=None)),
        ],
    )
    def test_check_task_facts_is_total(self, state, result, expected):
        task = SimpleNamespace(State=state, LastTaskResult=result)
        assert task_com.check_task_facts(task) == expected

    def test_check_task_facts_reads_the_run_time_and_the_first_actions_arguments(self):
        from unittest.mock import MagicMock

        task = MagicMock()
        task.State = 3
        task.LastTaskResult = 0x8007052E
        task.LastRunTime = datetime(2026, 10, 23, 14, 0, 5)
        task.Definition.Actions.Count = 1
        task.Definition.Actions.Item.return_value.Arguments = f"  --schedule-check {_TOKEN} "
        facts = task_com.check_task_facts(task)
        assert facts == CheckTaskFacts(
            state=3, last_result=0x8007052E, last_run=_AFTER_RUN, arguments=f"--schedule-check {_TOKEN}"
        )
        task.Definition.Actions.Item.assert_called_once_with(1)
        assert schedule_check.is_own_check_task(facts)
