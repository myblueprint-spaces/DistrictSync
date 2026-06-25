"""Graceful UI shutdown lifecycle for the DistrictSync Streamlit app.

Streamlit has no built-in "exit when the last browser disconnects", so on an
unattended district server the server + console leak forever after the tab is
closed (and the leaked port-8501 occupant blocks the next launch). This module
concentrates the lifecycle logic so ``launcher.py`` / ``Home.py`` stay thin:

- :func:`should_exit` — the **pure** exit decision (the only fully unit-testable
  part); never exit before the first browser connects, start an idle clock when
  sessions hit 0 after that, exit only after ``grace`` seconds continuously idle.
- :func:`start_idle_watchdog` — a daemon thread (one per server process) that
  feeds the live browser-session count into :func:`should_exit` and exits when it
  says so **and** no write is in flight. Any Streamlit-internal failure degrades
  to a no-op + logged warning — it never crashes the UI (the Exit button is the
  always-works fallback).
- :func:`write_guard` / :func:`safe_to_exit` — mark a critical write in progress.
  ``02_Convert.py`` wraps its SFTP upload path (``DataLoader.save_all`` into a
  ``TemporaryDirectory`` + ``upload_csvs``) in this. **Why it matters:** the guard
  stops an ``os._exit`` from truncating the in-flight SFTP upload into a partial
  remote delivery. (The UI's ``save_all`` stages into a temp dir, NOT the real
  output dir, so it cannot tear ``data/output``; the guard is also
  defense-in-depth should a future UI path ever ``save_all`` straight to the output
  dir, whose atomicity relies on a ``finally`` a hard ``os._exit`` would skip.) So
  neither the watchdog nor :func:`request_exit` exits while a write is in flight.
- :func:`request_exit` — the Exit / Finish & Close handler: wait (bounded) for
  ``safe_to_exit()``, render a goodbye line, then ``os._exit(0)``.
- :func:`already_running` — the single-instance guard for ``launcher.py``: GET the
  Streamlit health endpoint and treat the port as "already running" **only if the
  body is exactly ``ok``** (not bare connectivity — a non-Streamlit 8501 occupant
  won't match).

The reach into Streamlit's **private** ``Runtime._session_mgr`` /
``num_active_sessions()`` is isolated to exactly one helper
(:func:`_active_session_count`) so a future Streamlit rename fails loudly in one
place. The dependency is pinned (``streamlit>=1.54,<1.55``) and recorded in
``docs/claugentic-DECISIONS.md``.
"""

import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Streamlit's health endpoint returns this exact body once the runtime is ready
# for browser connections — the single-instance guard keys off the BODY, not a
# bare 200, so a non-Streamlit occupant of port 8501 can't be mistaken for us.
_HEALTH_OK_BODY = "ok"
_HEALTH_PATH = "/_stcore/health"

# Bounded wait for an in-flight write to finish before request_exit() force-exits.
_EXIT_WAIT_TIMEOUT_S = 30.0
_EXIT_WAIT_POLL_S = 0.25

# ---------------------------------------------------------------------------
# In-flight-write guard (single source of truth: counter + lock)
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()
_write_count = 0


@contextmanager
def write_guard() -> Iterator[None]:
    """Mark a critical write in progress for the duration of the ``with`` block.

    While any guard is held, :func:`safe_to_exit` is False, so neither the idle
    watchdog nor :func:`request_exit` will ``os._exit`` mid-write (which would
    skip ``DataLoader.save_all``'s atomic ``finally`` and tear the output dir).
    Re-entrant / concurrent-safe via a counter under a lock.
    """
    global _write_count
    with _write_lock:
        _write_count += 1
    try:
        yield
    finally:
        with _write_lock:
            _write_count -= 1


def safe_to_exit() -> bool:
    """Return True when no critical write is in flight (count == 0)."""
    with _write_lock:
        return _write_count == 0


# ---------------------------------------------------------------------------
# Pure exit decision (the unit-testable core)
# ---------------------------------------------------------------------------


def should_exit(
    active_count: int,
    ever_connected: bool,
    idle_since: float | None,
    now: float,
    grace: float,
) -> tuple[bool, float | None]:
    """Decide whether the server should exit, and track the idle clock.

    Pure — no I/O, no globals. Given the current active browser-session count and
    the prior idle state, returns ``(exit_now, new_idle_since)``:

    - **Startup window** (``not ever_connected``): never exit and keep the clock
      cleared. ``ever_connected`` flips True the first tick a session is seen
      (the caller ORs it with ``active_count > 0`` before the next call).
    - **Connected** (``active_count > 0``): clear the idle clock, never exit — an
      open-but-idle tab keeps the app alive (exit keys off *disconnect*, not
      inactivity).
    - **Idle** (``active_count == 0`` after ever connecting): start the clock on
      the first idle tick; exit only once continuously idle for ``grace`` seconds.
      A reconnect clears the clock, absorbing refreshes / brief reconnects.

    Args:
        active_count: Live browser sessions this tick.
        ever_connected: Whether ≥1 session has been observed before this tick.
        idle_since: Monotonic time the current idle streak began, or None.
        now: Current monotonic time.
        grace: Seconds of continuous idle required before exiting.

    Returns:
        ``(exit_now, new_idle_since)`` — feed ``new_idle_since`` back in next tick.
    """
    if not ever_connected:
        # Haven't seen a browser yet — this is the startup window (0 sessions is
        # normal). Never exit; keep the clock cleared.
        return False, None

    if active_count > 0:
        # A browser is connected — clear the idle clock; never exit.
        return False, None

    # Idle: no sessions, but we have been connected before.
    if idle_since is None:
        # First idle tick — start the clock.
        return False, now

    if now - idle_since >= grace:
        return True, idle_since

    return False, idle_since


# ---------------------------------------------------------------------------
# Streamlit-internal reach — ISOLATED to one helper (private-API blast radius)
# ---------------------------------------------------------------------------


def _active_session_count() -> int:
    """Return the live browser-session count from the Streamlit runtime.

    THE single place that touches Streamlit internals: the private
    ``Runtime._session_mgr`` attribute and its ``num_active_sessions()`` method
    (verified present on streamlit 1.54.0; minor pinned ``<1.55``). Isolated here
    so a future rename fails loudly in exactly one location. Raises if the runtime
    isn't up or the internal shape changed — the caller (:func:`start_idle_watchdog`)
    catches and degrades to a no-op + warning.
    """
    import streamlit.runtime as runtime

    if not runtime.exists():
        # Runtime not yet initialized — treat as "no sessions" (startup window).
        return 0
    instance = runtime.get_instance()
    # `_session_mgr` is PRIVATE Streamlit internal — named explicitly so a rename
    # blows up here, in one guarded place, not silently elsewhere.
    return instance._session_mgr.num_active_sessions()


# ---------------------------------------------------------------------------
# Idle watchdog (daemon thread, one per server process)
# ---------------------------------------------------------------------------


def start_idle_watchdog(grace: float = 90, poll: float = 5) -> threading.Thread:
    """Start a daemon thread that exits the server after ``grace`` idle seconds.

    Each ``poll``-second tick reads the live session count via
    :func:`_active_session_count`, feeds :func:`should_exit`, and ``os._exit(0)``s
    **only when** ``should_exit`` is True AND :func:`safe_to_exit` (no write in
    flight — otherwise it defers to the next tick). Any Streamlit-internal failure
    is caught and degrades the tick to a no-op + a logged warning, so an API change
    can never crash the UI; the Exit button stays the manual fallback.

    Started once per server process via the ``@st.cache_resource`` singleton in
    ``Home.py``. Returns the (already-started) daemon thread.
    """

    def _loop() -> None:
        ever_connected = False
        idle_since: float | None = None
        # Degrade-to-noop guard: if the private reach breaks, log ONCE then stop
        # polling (the Exit button remains the fallback) rather than spamming.
        while True:
            time.sleep(poll)
            try:
                active = _active_session_count()
            except Exception as exc:  # noqa: BLE001 — degrade, never crash the UI
                logger.warning(
                    "Idle watchdog disabled — could not read Streamlit session count "
                    "(internal API may have changed): %s. Use the Exit control to quit.",
                    exc,
                )
                return

            ever_connected = ever_connected or active > 0
            now = time.monotonic()
            do_exit, idle_since = should_exit(active, ever_connected, idle_since, now, grace)
            if do_exit and safe_to_exit():
                logger.info("No active browser sessions for %ss — shutting down DistrictSync.", grace)
                os._exit(0)
            # If do_exit but a write is in flight, defer: keep idle_since so we
            # re-evaluate on the next tick once the write completes.

    thread = threading.Thread(target=_loop, name="districtsync-idle-watchdog", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Explicit exit (Exit / Finish & Close buttons)
# ---------------------------------------------------------------------------


def request_exit() -> None:
    """Shut the server down now — used by the Exit / Finish & Close buttons.

    Waits (bounded, ~30s) for :func:`safe_to_exit` so an in-flight conversion
    write finishes its atomic commit first, renders a goodbye line, then
    ``os._exit(0)`` to promptly kill the blocking Streamlit server thread.
    """
    import streamlit as st

    deadline = time.monotonic() + _EXIT_WAIT_TIMEOUT_S
    waited = False
    while not safe_to_exit() and time.monotonic() < deadline:
        waited = True
        st.info("Finishing your conversion before closing…")
        time.sleep(_EXIT_WAIT_POLL_S)

    if waited and not safe_to_exit():
        logger.warning(
            "Exit requested but a write is still in flight after %ss — exiting anyway.", _EXIT_WAIT_TIMEOUT_S
        )

    st.success("DistrictSync is shutting down. You can close this window.")
    logger.info("Exit requested from the UI — shutting down DistrictSync.")
    os._exit(0)


# ---------------------------------------------------------------------------
# Single-instance guard (launcher.py)
# ---------------------------------------------------------------------------


def already_running(port: int = 8501) -> bool:
    """Return True if a DistrictSync Streamlit server is already up on ``port``.

    GETs ``http://localhost:<port>/_stcore/health`` with a short timeout and
    returns True **only if** the response body is exactly ``ok`` — Streamlit's
    health body. Keying off the body (not bare connectivity / a 200) means a
    non-Streamlit occupant of the port is correctly treated as "not us". Any
    connection error / timeout / non-``ok`` body → False (start our own server).
    """
    try:
        import requests

        resp = requests.get(f"http://localhost:{port}{_HEALTH_PATH}", timeout=1.5)
    except Exception:  # noqa: BLE001 — any failure means "not reachable as us"
        return False
    return resp.text.strip() == _HEALTH_OK_BODY
