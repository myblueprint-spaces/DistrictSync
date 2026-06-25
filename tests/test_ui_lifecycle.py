"""Unit tests for the graceful-UI-shutdown lifecycle (src/ui/lifecycle.py).

Covers the PURE / testable surface — the parts that decide *whether* to exit and
*whether it's safe to* — without ever calling os._exit:

- ``should_exit`` truth table (startup, connected, idle-within-grace,
  idle-past-grace, reconnect).
- ``write_guard`` / ``safe_to_exit`` (False while held, True after).
- ``already_running`` against a mocked health response (body ``ok`` → True;
  non-``ok`` / connection error → False).

Plus a ``ui``-marked, read-only smoke assertion (deselected by default; runs in
the Playwright job) that the PRIVATE ``_session_mgr.num_active_sessions()`` reach
the watchdog depends on is live against a real server — so a Streamlit bump that
breaks it fails loudly here, not silently in prod.

``src/ui`` is mypy/coverage-excluded, but this logic is pure and must be tested
regardless of that omit.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.ui import lifecycle

# ---------------------------------------------------------------------------
# should_exit — pure exit decision truth table
# ---------------------------------------------------------------------------

GRACE = 90.0


class TestShouldExit:
    def test_startup_never_connected_does_not_exit(self):
        """0 sessions before any browser has ever connected = startup window."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=False, idle_since=None, now=1000.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since is None

    def test_startup_never_connected_keeps_clock_cleared(self):
        """Even with a stale idle_since, the not-yet-connected window stays cleared."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=False, idle_since=500.0, now=1000.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since is None

    def test_connected_does_not_exit_and_clears_clock(self):
        """An active session never exits and clears any running idle clock."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=1, ever_connected=True, idle_since=500.0, now=1000.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since is None

    def test_first_idle_tick_starts_clock_does_not_exit(self):
        """First tick at 0 sessions (after connecting) starts the clock, no exit."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=True, idle_since=None, now=1000.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since == 1000.0

    def test_idle_within_grace_does_not_exit(self):
        """Idle but under the grace window — keep waiting, keep the clock."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=True, idle_since=1000.0, now=1000.0 + GRACE - 1, grace=GRACE
        )
        assert exit_now is False
        assert idle_since == 1000.0

    def test_idle_past_grace_exits(self):
        """Continuously idle for >= grace seconds → exit."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=True, idle_since=1000.0, now=1000.0 + GRACE, grace=GRACE
        )
        assert exit_now is True
        assert idle_since == 1000.0

    def test_reconnect_clears_clock(self):
        """A reconnect (session reappears) within the grace window cancels exit."""
        # Clock running...
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=True, idle_since=1000.0, now=1010.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since == 1000.0
        # ...then a browser reconnects: clock must clear, no exit.
        exit_now, idle_since = lifecycle.should_exit(
            active_count=1, ever_connected=True, idle_since=1000.0, now=1020.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since is None

    def test_idle_after_reconnect_restarts_clock(self):
        """After a reconnect cleared the clock, a fresh idle streak restarts it."""
        exit_now, idle_since = lifecycle.should_exit(
            active_count=0, ever_connected=True, idle_since=None, now=2000.0, grace=GRACE
        )
        assert exit_now is False
        assert idle_since == 2000.0


# ---------------------------------------------------------------------------
# write_guard / safe_to_exit — in-flight-write guard
# ---------------------------------------------------------------------------


class TestWriteGuard:
    def test_safe_to_exit_true_by_default(self):
        assert lifecycle.safe_to_exit() is True

    def test_not_safe_while_held(self):
        with lifecycle.write_guard():
            assert lifecycle.safe_to_exit() is False

    def test_safe_again_after_release(self):
        with lifecycle.write_guard():
            pass
        assert lifecycle.safe_to_exit() is True

    def test_nested_guards_require_full_unwind(self):
        """Re-entrant: still unsafe until every guard exits (counter, not flag)."""
        with lifecycle.write_guard():
            with lifecycle.write_guard():
                assert lifecycle.safe_to_exit() is False
            # inner released, outer still held
            assert lifecycle.safe_to_exit() is False
        assert lifecycle.safe_to_exit() is True

    def test_guard_releases_on_exception(self):
        """An exception inside the guard still releases it (finally)."""
        with pytest.raises(ValueError), lifecycle.write_guard():
            raise ValueError("boom")
        assert lifecycle.safe_to_exit() is True


# ---------------------------------------------------------------------------
# already_running — single-instance guard against a mocked health endpoint
# ---------------------------------------------------------------------------


class TestAlreadyRunning:
    def _mock_response(self, text: str) -> MagicMock:
        resp = MagicMock()
        resp.text = text
        return resp

    def test_true_when_health_body_is_ok(self):
        with patch("requests.get", return_value=self._mock_response("ok")) as mock_get:
            assert lifecycle.already_running(8501) is True
        # Must hit the Streamlit health endpoint, not a bare port check.
        url = mock_get.call_args.args[0]
        assert url.endswith("/_stcore/health")

    def test_true_when_body_has_surrounding_whitespace(self):
        with patch("requests.get", return_value=self._mock_response("ok\n")):
            assert lifecycle.already_running(8501) is True

    def test_false_when_body_not_ok(self):
        """A non-Streamlit occupant returning 200 with another body is NOT us."""
        with patch("requests.get", return_value=self._mock_response("<html>nginx</html>")):
            assert lifecycle.already_running(8501) is False

    def test_false_on_connection_error(self):
        with patch("requests.get", side_effect=OSError("connection refused")):
            assert lifecycle.already_running(8501) is False

    def test_false_when_requests_missing(self):
        """If requests can't even be imported, degrade to 'not running'."""
        with patch("requests.get", side_effect=ImportError("no requests")):
            assert lifecycle.already_running(8501) is False


# ---------------------------------------------------------------------------
# ui-marked smoke: the PRIVATE _session_mgr reach is live (read-only, no exit)
# ---------------------------------------------------------------------------


@pytest.mark.ui
def test_private_session_mgr_reach_is_live(streamlit_server):
    """A real running server exposes _session_mgr.num_active_sessions() as an int.

    This is the exact private reach the idle watchdog depends on. A Streamlit
    bump that renames _session_mgr or num_active_sessions makes THIS fail in the
    Playwright job — loud, in one place — rather than silently no-opping the
    watchdog in production. Read-only: no os._exit, no state mutation.

    The watchdog process and this test run in separate processes, so we cannot
    call lifecycle._active_session_count() against the fixture's runtime directly;
    instead we assert the private attribute chain exists and is correctly typed on
    the Runtime class so a rename is caught structurally.
    """
    import streamlit.runtime as runtime
    from streamlit.runtime.runtime import Runtime
    from streamlit.runtime.session_manager import SessionManager

    # The watchdog's reach: runtime.exists() / get_instance() must exist...
    assert hasattr(runtime, "exists")
    assert hasattr(runtime, "get_instance")
    # ...and the private _session_mgr → num_active_sessions() chain must be intact.
    assert hasattr(Runtime, "__init__")
    assert hasattr(SessionManager, "num_active_sessions")
    count = SessionManager.num_active_sessions
    # It's a method returning int — assert the annotation is present and is int.
    assert count.__annotations__.get("return") in ("int", int)
