"""Unit tests for the COUNTED job-runner core (state machine + the routing seam).

The flet-touching ``JobRunner.run`` glue is ``# pragma: no cover``; these tests
exercise the two trust-critical pure pieces headless:

  * ``JobStateMachine`` — the single-flight guard (a double-click can't launch two
    conversions) + illegal-transition no-ops.
  * ``route`` — the ``SystemExit``-vs-``Exception``-vs-success routing (the
    load-bearing asymmetry: ``SystemExit`` is NOT an ``Exception`` subclass, is
    caught FIRST, and routes to ``on_failure`` rather than killing the thread).
"""

from __future__ import annotations

import pytest

from src.ui_flet.job_runner import JobRunner, JobState, JobStateMachine, route


# --------------------------------------------------------------------------- #
# JobStateMachine — the single-flight lifecycle (C4)                           #
# --------------------------------------------------------------------------- #
class TestJobStateMachine:
    def test_starts_idle(self) -> None:
        sm = JobStateMachine()
        assert sm.state is JobState.IDLE
        assert sm.can_start is True
        assert sm.is_running is False

    def test_start_moves_idle_to_running(self) -> None:
        sm = JobStateMachine()
        assert sm.start() is True
        assert sm.state is JobState.RUNNING
        assert sm.is_running is True
        assert sm.can_start is False

    def test_start_from_running_is_single_flight_noop(self) -> None:
        """A second start() while RUNNING is a no-op returning False (double-click guard)."""
        sm = JobStateMachine()
        assert sm.start() is True
        assert sm.start() is False  # the double-click cannot launch a second job
        assert sm.state is JobState.RUNNING

    def test_finish_ok_only_from_running(self) -> None:
        sm = JobStateMachine()
        assert sm.finish_ok() is False  # illegal from IDLE — no-op
        assert sm.state is JobState.IDLE
        sm.start()
        assert sm.finish_ok() is True
        assert sm.state is JobState.DONE

    def test_finish_error_only_from_running(self) -> None:
        sm = JobStateMachine()
        assert sm.finish_error() is False  # illegal from IDLE — no-op
        assert sm.state is JobState.IDLE
        sm.start()
        assert sm.finish_error() is True
        assert sm.state is JobState.ERROR

    def test_finish_ok_illegal_from_done(self) -> None:
        sm = JobStateMachine()
        sm.start()
        sm.finish_ok()
        assert sm.finish_ok() is False  # already terminal — no-op
        assert sm.state is JobState.DONE

    def test_reset_from_done_returns_to_idle(self) -> None:
        sm = JobStateMachine()
        sm.start()
        sm.finish_ok()
        assert sm.reset() is True
        assert sm.state is JobState.IDLE
        assert sm.can_start is True

    def test_reset_from_error_returns_to_idle(self) -> None:
        sm = JobStateMachine()
        sm.start()
        sm.finish_error()
        assert sm.reset() is True
        assert sm.state is JobState.IDLE

    def test_reset_from_idle_is_noop(self) -> None:
        sm = JobStateMachine()
        assert sm.reset() is False
        assert sm.state is JobState.IDLE

    def test_reset_refuses_to_drop_a_running_job(self) -> None:
        """reset() must NOT clear a RUNNING job (that would drop the single-flight guard)."""
        sm = JobStateMachine()
        sm.start()
        assert sm.reset() is False
        assert sm.state is JobState.RUNNING

    def test_full_cycle_allows_re_run(self) -> None:
        sm = JobStateMachine()
        sm.start()
        sm.finish_ok()
        sm.reset()
        assert sm.start() is True  # a second run is allowed after reset
        assert sm.state is JobState.RUNNING


# --------------------------------------------------------------------------- #
# route — the SystemExit / Exception / success routing seam (C3)               #
# --------------------------------------------------------------------------- #
class _Capture:
    """A synchronous test double standing in for the page.run_task-marshalled deliver."""

    def __init__(self) -> None:
        self.success_calls: list[object] = []
        self.failure_calls: list[BaseException] = []

    def on_success(self, result: object) -> None:
        self.success_calls.append(result)

    def on_failure(self, exc: BaseException) -> None:
        self.failure_calls.append(exc)


class TestRoute:
    def test_success_routes_to_on_success(self) -> None:
        cap = _Capture()
        route(lambda: {"Students": 42}, on_success=cap.on_success, on_failure=cap.on_failure)
        assert cap.success_calls == [{"Students": 42}]
        assert cap.failure_calls == []

    def test_none_result_still_routes_to_on_success(self) -> None:
        cap = _Capture()
        route(lambda: None, on_success=cap.on_success, on_failure=cap.on_failure)
        assert cap.success_calls == [None]
        assert cap.failure_calls == []

    def test_exception_routes_to_on_failure(self) -> None:
        cap = _Capture()
        boom = ValueError("a required output column is missing")

        def work() -> None:
            raise boom

        route(work, on_success=cap.on_success, on_failure=cap.on_failure)
        assert cap.success_calls == []
        assert cap.failure_calls == [boom]

    def test_system_exit_routes_to_on_failure_not_reraised(self) -> None:
        """THE load-bearing asymmetry: SystemExit is caught (not an Exception subclass).

        A bare ``except Exception`` would let it propagate and silently kill the
        worker thread — ``route`` must catch it FIRST and surface it to on_failure.
        """
        cap = _Capture()

        def work() -> None:
            raise SystemExit(1)

        # Must NOT propagate (would kill the worker thread) — route swallows-and-routes.
        route(work, on_success=cap.on_success, on_failure=cap.on_failure)
        assert cap.success_calls == []
        assert len(cap.failure_calls) == 1
        assert isinstance(cap.failure_calls[0], SystemExit)

    def test_system_exit_is_caught_before_generic_exception(self) -> None:
        """A subclass of SystemExit still routes (proves the separate first clause)."""
        cap = _Capture()

        class _CustomExit(SystemExit):
            pass

        def work() -> None:
            raise _CustomExit(3)

        route(work, on_success=cap.on_success, on_failure=cap.on_failure)
        assert isinstance(cap.failure_calls[0], _CustomExit)

    def test_keyboard_interrupt_propagates(self) -> None:
        """KeyboardInterrupt is neither SystemExit nor Exception — it must NOT be swallowed."""
        cap = _Capture()

        def work() -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            route(work, on_success=cap.on_success, on_failure=cap.on_failure)
        assert cap.success_calls == []
        assert cap.failure_calls == []


# --------------------------------------------------------------------------- #
# JobRunner — the state machine is wired (the run() glue itself is pragma-omitted)
# --------------------------------------------------------------------------- #
class TestJobRunner:
    def test_owns_a_fresh_idle_state_machine(self) -> None:
        runner = JobRunner()
        assert isinstance(runner.state, JobStateMachine)
        assert runner.state.state is JobState.IDLE
