"""``JobRunner`` — run a blocking callable off the UI thread + marshal the result back.

The reusable worker-thread frame for any long-running Flet action (its first real
consumer is Convert's ``convert_job``). Mirrors ``filepicker.py``'s split:

  * **COUNTED (pure, no flet):** ``JobState`` + ``JobStateMachine`` (the
    trust-critical single-flight guard — a double-click can't launch two jobs) and
    ``route`` (the catch-and-route seam that encodes the ``SystemExit``-vs-``Exception``
    asymmetry) — both unit-tested headless.
  * **``# pragma: no cover`` glue:** ``JobRunner.run`` wires ``page.run_thread`` →
    ``page.run_task`` (needs a live Flet event loop).

**The ``SystemExit``-vs-``Exception`` asymmetry (THE #1 correctness trap — see
``docs/FLET_1.0_CONVENTIONS.md`` §Worker-thread):** ``SystemExit`` is NOT an
``Exception`` subclass — a bare ``except Exception`` in the worker lets it
propagate and silently kill the worker thread. On the ``run_pipeline`` bad-input
path (``sys.exit(1)``) NO run-log record is written, so the UI's ``on_error`` is
the ONLY failure signal. ``route`` therefore catches ``SystemExit`` FIRST, in a
SEPARATE clause, and surfaces it to ``on_error`` (a caught ``Exception`` also
routes to ``on_error``). Convert's adapter path isn't expected to ``sys.exit``,
but the catch is contract + defense-in-depth for the next consumer.

**No control mutation on the worker thread (C2):** ``_work`` calls ONLY ``work()``
+ ``page.run_task(...)`` — it never touches a Flet control. Every UI update happens
inside the ``on_done``/``on_error`` handlers the loop owns.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import flet as ft


class JobState(Enum):
    """The lifecycle of a single job run."""

    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class JobStateMachine:
    """Single-flight job lifecycle: IDLE → RUNNING → DONE/ERROR → (reset) IDLE.

    Pure + total: illegal transitions are no-ops returning ``False`` rather than
    corrupting state. ``start()`` returning ``False`` from RUNNING is THE guard
    against a double-click launching two conversions (C4) — the authoritative
    single-flight invariant, cheaply testable headless.
    """

    def __init__(self) -> None:
        self._state: JobState = JobState.IDLE

    @property
    def state(self) -> JobState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is JobState.RUNNING

    @property
    def can_start(self) -> bool:
        """A job may start only from a settled state (IDLE / DONE / ERROR)."""
        return self._state is not JobState.RUNNING

    def start(self) -> bool:
        """Begin a run. Returns ``True`` if it started, ``False`` if one is already RUNNING.

        Single-flight: from RUNNING this is a no-op returning ``False`` (a second
        click can't launch a second job). From IDLE/DONE/ERROR it transitions to
        RUNNING and returns ``True``.
        """
        if self._state is JobState.RUNNING:
            return False
        self._state = JobState.RUNNING
        return True

    def finish_ok(self) -> bool:
        """RUNNING → DONE. No-op returning ``False`` from any other state."""
        if self._state is not JobState.RUNNING:
            return False
        self._state = JobState.DONE
        return True

    def finish_error(self) -> bool:
        """RUNNING → ERROR. No-op returning ``False`` from any other state."""
        if self._state is not JobState.RUNNING:
            return False
        self._state = JobState.ERROR
        return True

    def reset(self) -> bool:
        """DONE/ERROR → IDLE (ready for a re-run). No-op returning ``False`` otherwise.

        Deliberately refuses to reset a RUNNING job (that would drop the
        single-flight guard mid-flight); only a settled terminal state resets.
        """
        if self._state in (JobState.DONE, JobState.ERROR):
            self._state = JobState.IDLE
            return True
        return False


def route(
    work: Callable[[], Any],
    *,
    on_success: Callable[[Any], None],
    on_failure: Callable[[BaseException], None],
) -> None:
    """Run ``work`` and route its outcome — the COUNTED catch-and-route seam.

    Encodes the load-bearing ``SystemExit``-vs-``Exception`` asymmetry so it is
    testable WITHOUT a live Flet loop: ``SystemExit`` is caught FIRST, in a
    SEPARATE clause (it is not an ``Exception`` subclass), and routed to
    ``on_failure``; a caught ``Exception`` also routes to ``on_failure``; a clean
    return routes to ``on_success``. Never re-raises — a failure always surfaces
    via ``on_failure`` (never silently kills a thread).

    ``JobRunner.run`` calls this inside ``_work`` with the ``page.run_task``-marshalled
    deliver callbacks; this function itself does NO threading and NO flet import.
    """
    try:
        result = work()
    except SystemExit as ex:  # NOT an Exception subclass — catch FIRST/separate
        on_failure(ex)
        return
    except Exception as ex:  # noqa: BLE001 - surface EVERY failure to the UI, never swallow
        on_failure(ex)
        return
    on_success(result)


class JobRunner:
    """Runs ``work`` off the UI thread and marshals ``on_done``/``on_error`` back to the loop.

    Owns a ``JobStateMachine`` (the single-flight guard). ``run`` is the flet glue
    (``# pragma: no cover``); its catch-and-route logic delegates to the COUNTED
    ``route`` seam.
    """

    def __init__(self) -> None:
        self.state = JobStateMachine()

    def run(
        self,
        page: ft.Page,
        work: Callable[[], Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException], None],
    ) -> bool:  # pragma: no cover - flet glue: needs a live page.run_thread/run_task loop
        """Start ``work`` off the UI thread. Returns ``False`` (no-op) if already RUNNING.

        Single-flight: ``state.start()`` gates a second launch. ``_work`` runs on
        the worker thread and calls ONLY ``work()`` (via ``route``) +
        ``page.run_task(...)`` — it never mutates a control (C2). The terminal
        state transition + the ``on_done``/``on_error`` handler run inside the
        marshalled coroutines the loop owns.
        """
        if not self.state.start():
            return False

        async def _deliver_done(result: Any) -> None:
            self.state.finish_ok()
            on_done(result)

        async def _deliver_error(exc: BaseException) -> None:
            self.state.finish_error()
            on_error(exc)

        def _work() -> None:  # runs OFF the UI thread — NO control mutation here
            route(
                work,
                on_success=lambda result: page.run_task(_deliver_done, result),
                on_failure=lambda exc: page.run_task(_deliver_error, exc),
            )

        page.run_thread(_work)
        return True
