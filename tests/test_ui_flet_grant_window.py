"""The pre-shell auto-grant window (plan 0049 S-1b-ii.2) — copy, branches, floor.

Two halves, matching how the window is built: the PURE decision table and copy
(:mod:`src.ui_flet.grant_access`), and the assembled body (:mod:`src.ui_flet.grant_window`),
which is exercised against a stub page exactly as ``tests/test_ui_flet_render_smoke.py``
does. No test here can reach a UAC prompt: ``request`` is a REQUIRED argument with no
default, so the real round trip has to be passed in deliberately.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from src.scheduler.provision_session import GrantOutcome
from src.ui_flet import grant_access, grant_window
from src.ui_flet.launcher import offers_grant_window
from src.utils.paths import MachineScopeRefused, MachineScopeRefusedReason


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


def _iter_controls(control):  # noqa: ANN001, ANN202 - a walker over an untyped Flet tree
    yield control
    for attr in ("controls", "content", "action"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, ft.Control):
                yield from _iter_controls(item)


def _all_text(control) -> str:  # noqa: ANN001 - untyped Flet tree
    chunks: list[str] = []
    for c in _iter_controls(control):
        for attr in ("value", "content", "tooltip"):
            found = getattr(c, attr, None)
            if isinstance(found, str):
                chunks.append(found)
    return "\n".join(chunks)


def _filled(control) -> list:  # noqa: ANN001 - untyped Flet tree
    return [c for c in _iter_controls(control) if isinstance(c, ft.FilledButton)]


def _handler(control, label: str):  # noqa: ANN001, ANN202 - untyped Flet tree
    for c in _iter_controls(control):
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and c.content == label:
            return c.on_click
    raise AssertionError(f"no button labelled {label!r}")


def _press(control, label: str) -> None:  # noqa: ANN001 - untyped Flet tree
    _handler(control, label)(None)


async def _press_async(control, label: str) -> None:  # noqa: ANN001 - untyped Flet tree
    """Drive an ASYNC handler (the Close button — ``window.destroy()`` is a coroutine)."""
    await _handler(control, label)(None)


def _body(page, *, outcomes, reexec=None):  # noqa: ANN001 - untyped Flet tree
    """Build the window with a scripted sequence of grant outcomes."""
    calls: list[str] = []
    queue = list(outcomes)

    def _request() -> GrantOutcome:
        calls.append("request")
        return queue.pop(0)

    def _reexec() -> None:
        calls.append("reexec")
        if reexec is not None:
            reexec()

    return grant_window.build_grant_body(page, request=_request, reexec=_reexec), calls


# --------------------------------------------------------------------------- #
# The typed branch: which refusals offer a grant at all                        #
# --------------------------------------------------------------------------- #
class TestOffersGrant:
    def test_every_reason_is_decided_and_only_access_denial_is_grantable(self):
        """Total over the enum — a reason added without a decision must not slip through."""
        grantable = {reason for reason in MachineScopeRefusedReason if grant_access.offers_grant(reason)}
        assert grantable == {MachineScopeRefusedReason.INACCESSIBLE}

    def test_the_launcher_branches_on_the_typed_reason(self):
        denied = MachineScopeRefused(MachineScopeRefusedReason.INACCESSIBLE, "C:\\ProgramData\\DistrictSync")
        assert offers_grant_window(denied) is True

    def test_a_folder_a_grant_cannot_repair_keeps_the_repair_dialog(self):
        for reason in (
            MachineScopeRefusedReason.FOREIGN_OWNER,
            MachineScopeRefusedReason.OPEN_ACE,
            MachineScopeRefusedReason.REPARSE,
            MachineScopeRefusedReason.MISSING,
            MachineScopeRefusedReason.SWITCH_UNREADABLE,
            MachineScopeRefusedReason.REDIRECTED,
        ):
            assert offers_grant_window(MachineScopeRefused(reason, "C:\\x")) is False

    def test_an_unrelated_boot_failure_is_never_a_grant(self):
        assert offers_grant_window(OSError("the disk is full")) is False
        assert offers_grant_window(RuntimeError("boom")) is False


# --------------------------------------------------------------------------- #
# Copy                                                                         #
# --------------------------------------------------------------------------- #
class TestCopy:
    def test_every_outcome_that_is_not_a_success_has_its_own_copy(self):
        for outcome in GrantOutcome:
            copy = grant_access.failure_copy(outcome)
            if outcome is GrantOutcome.GRANTED:
                assert copy is None, "a success is not painted — it re-execs"
            else:
                assert copy is not None, f"{outcome} would render nothing"
                assert copy.headline and copy.detail and copy.primary_label

    def test_no_two_failures_read_identically(self):
        headlines = [
            grant_access.failure_copy(o).headline  # type: ignore[union-attr]
            for o in GrantOutcome
            if o is not GrantOutcome.GRANTED
        ]
        assert len(headlines) == len(set(headlines))

    def test_the_cross_sid_branch_has_copy_of_its_own(self):
        """The `DSYNC_DIFFERENT_ACCOUNT` sentinel is a DIFFERENT answer from a refusal."""
        cross = grant_access.failure_copy(GrantOutcome.DIFFERENT_ACCOUNT)
        refused = grant_access.failure_copy(GrantOutcome.REFUSED)
        assert cross is not None and refused is not None
        assert cross.headline != refused.headline
        assert cross.detail != refused.detail

    def test_no_copy_republishes_the_sentinel(self):
        every = [grant_access.ask_copy(), *[c for c in (grant_access.failure_copy(o) for o in GrantOutcome) if c]]
        for copy in every:
            for text in (copy.headline, copy.detail, copy.primary_label):
                assert "DSYNC" not in text.upper()

    def test_the_ask_states_the_limitation_the_handshake_enforces(self):
        """D5's non-administrator promise cannot be kept, so the ask says so BEFORE the trip."""
        note = grant_access.ASK_ADMIN_NOTE.lower()
        assert "administrator" in note
        assert "will not work" in note or "does not work" in note

    def test_the_cross_sid_copy_does_not_promise_approval_on_someone_s_behalf(self):
        detail = grant_access.failure_copy(GrantOutcome.DIFFERENT_ACCOUNT).detail  # type: ignore[union-attr]
        assert "only grants the account that asked" in detail


# --------------------------------------------------------------------------- #
# The window                                                                   #
# --------------------------------------------------------------------------- #
class TestWindow:
    def test_the_ask_renders_with_exactly_one_filled_primary(self, page):
        body, _ = _body(page, outcomes=[])
        assert len(_filled(body)) == 1
        text = _all_text(body)
        assert grant_access.ASK_HEADLINE in text
        assert grant_access.ASK_ADMIN_NOTE in text
        assert grant_access.CLOSE_LABEL in text

    def test_a_granted_attempt_reexecs_exactly_once(self, page):
        body, calls = _body(page, outcomes=[GrantOutcome.GRANTED])
        _press(body, grant_access.ASK_PRIMARY_LABEL)
        assert calls == ["request", "reexec"]
        # Nothing was repainted — the process is on its way out.
        assert grant_access.ASK_HEADLINE in _all_text(body)

    @pytest.mark.parametrize(
        "outcome",
        [o for o in GrantOutcome if o is not GrantOutcome.GRANTED],
    )
    def test_every_failure_is_named_on_screen_and_never_reexecs(self, page, outcome):
        body, calls = _body(page, outcomes=[outcome])
        _press(body, grant_access.ASK_PRIMARY_LABEL)

        copy = grant_access.failure_copy(outcome)
        assert copy is not None
        text = _all_text(body)
        assert copy.headline in text
        assert copy.detail in text
        assert calls == ["request"], "a failed grant must never re-exec"

    def test_a_declined_prompt_is_a_named_error_not_a_silent_exit(self, page):
        body, calls = _body(page, outcomes=[GrantOutcome.DECLINED])
        _press(body, grant_access.ASK_PRIMARY_LABEL)

        assert grant_access.DECLINED_HEADLINE in _all_text(body)
        assert calls == ["request"]
        page.window.destroy.assert_not_called()

    def test_a_failure_state_still_has_exactly_one_filled_primary(self, page):
        body, _ = _body(page, outcomes=[GrantOutcome.REFUSED])
        _press(body, grant_access.ASK_PRIMARY_LABEL)
        assert len(_filled(body)) == 1

    def test_the_failure_state_can_retry_and_then_succeed(self, page):
        body, calls = _body(page, outcomes=[GrantOutcome.DECLINED, GrantOutcome.GRANTED])
        _press(body, grant_access.ASK_PRIMARY_LABEL)
        _press(body, grant_access.RETRY_LABEL)
        assert calls == ["request", "request", "reexec"]

    def test_a_raising_grant_is_named_on_screen_not_a_dead_button(self, page):
        """Flet SWALLOWS an exception raised in an event handler.

        Without the guard, the one screen between this admin and the app has a primary
        button that silently does nothing, forever.
        """

        def _boom() -> GrantOutcome:
            raise RuntimeError("something nobody predicted")

        body = grant_window.build_grant_body(page, request=_boom, reexec=lambda: None)
        _press(body, grant_access.ASK_PRIMARY_LABEL)

        assert grant_access.UNAVAILABLE_HEADLINE in _all_text(body)
        assert "something nobody predicted" not in _all_text(body)

    def test_close_AWAITS_destroy_not_merely_calls_it(self, page):
        """``page.window.destroy()`` is a coroutine on 0.85.3 — an un-awaited call is a
        silent no-op, so the window would simply never close (the 0029 Exit-button class).

        ``page.window.destroy`` MUST be an ``AsyncMock`` here: awaiting a plain ``MagicMock``
        raises, and the handler's floor is ``os._exit(1)``, which would kill the test runner.
        """
        page.window.destroy = AsyncMock()
        body, _ = _body(page, outcomes=[])

        asyncio.run(_press_async(body, grant_access.CLOSE_LABEL))

        page.window.destroy.assert_awaited_once()

    def test_close_falls_back_to_os_exit_when_destroy_cannot_complete(self, page, monkeypatch):
        """The same floor ``shell._close_window`` carries — this window must always close."""

        async def _boom() -> None:
            raise RuntimeError("destroy could not complete")

        page.window.destroy = _boom
        exit_spy = MagicMock()
        monkeypatch.setattr(grant_window.os, "_exit", exit_spy)
        body, _ = _body(page, outcomes=[])

        asyncio.run(_press_async(body, grant_access.CLOSE_LABEL))

        # 1, not 0: closing this window without a grant means the app did not start.
        exit_spy.assert_called_once_with(1)

    def test_a_render_failure_falls_to_the_error_card_never_a_traceback(self, page, monkeypatch):
        monkeypatch.setattr(
            grant_window, "_ask_card", lambda _on: (_ for _ in ()).throw(TypeError("FilledButton(text=) again"))
        )
        body, _ = _body(page, outcomes=[])
        assert "couldn't show this window" in _all_text(body)

    def test_the_grant_seam_has_no_default(self):
        """A defaulted ``request`` would put a live UAC prompt one accident away."""
        import inspect

        params = inspect.signature(grant_window.build_grant_body).parameters
        assert params["request"].default is inspect.Parameter.empty
        assert params["reexec"].default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
# The re-exec argv (the half that CAN be tested)                               #
# --------------------------------------------------------------------------- #
class TestReexecArgv:
    def test_a_frozen_exe_relaunches_itself_with_the_same_arguments(self, monkeypatch):
        monkeypatch.setattr(grant_access.sys, "frozen", True, raising=False)
        monkeypatch.setattr(grant_access.sys, "executable", "C:\\Apps\\DistrictSync.exe")
        monkeypatch.setattr(grant_access.sys, "argv", ["C:\\Apps\\DistrictSync.exe", "--diagnose"])

        assert grant_access.reexec_argv() == ["C:\\Apps\\DistrictSync.exe", "--diagnose"]

    def test_a_source_checkout_relaunches_through_the_module_entry_point(self, monkeypatch):
        monkeypatch.delattr(grant_access.sys, "frozen", raising=False)
        monkeypatch.setattr(grant_access.sys, "executable", "C:\\Python\\python.exe")
        monkeypatch.setattr(grant_access.sys, "argv", ["src/main.py"])

        assert grant_access.reexec_argv() == ["C:\\Python\\python.exe", "-m", "src.main"]

    def test_the_first_element_is_always_the_thing_that_gets_executed(self, monkeypatch):
        """``os.execv(argv[0], argv)`` — the executable must lead its own argv."""
        monkeypatch.delattr(grant_access.sys, "frozen", raising=False)
        monkeypatch.setattr(grant_access.sys, "executable", "C:\\Python\\python.exe")
        monkeypatch.setattr(grant_access.sys, "argv", ["src/main.py", "--quality"])

        argv = grant_access.reexec_argv()
        assert argv[0] == "C:\\Python\\python.exe"
        assert argv[-1] == "--quality"
