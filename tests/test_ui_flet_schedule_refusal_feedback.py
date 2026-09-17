"""A REFUSED register press must replace what the last press left on screen (2026-09-17).

The reported defect: a register failed with **"Couldn't confirm the schedule"**, the admin
retried, and — asked which of three things they saw — answered *"nothing changed"*. That rules
out the success path (a dispatched attempt swaps ``result_slot`` twice, spinner then banner), so
the retry was never dispatched: ``register_block`` refused it and the early return painted only
the note under the account field, nowhere near the red card the admin was looking at. The stale
card survived a fresh attempt and read as a dead button.

What is pinned here is the SEQUENCE, which nothing else exercises — every schedule test in
``test_ui_flet_render_smoke.py`` and ``test_ui_flet_service_account.py`` is single-shot. Both
mounts of the ONE shared section (``_build_schedule_section``) are driven through their real
hosts, because a fix proven only on the Settings scroll is unproven for the wizard step an admin
meets first. Each defect test carries its positive twin: a refusal that clears the slot must not
be a section that can no longer report anything, so the next attempt still reaches its banner.

Helpers come from the two existing view-test modules — this file adds no second way to find a
control, and no existing test is touched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

import src.ui_flet.screens.setup as setup_mod
from src.scheduler import windows
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.setup_flow import SCHEDULE_ACCOUNT_FIELD_LABEL
from tests.test_ui_flet_render_smoke import (
    _button_by_content,
    _has_text,
    _has_text_containing,
    _iter_controls,
    _textfield_by_label,
)
from tests.test_ui_flet_service_account import (
    _SERVICE,
    _SIGNED_IN,
    _drain,
    _registered_args,
    _settings,
)

_FAILED_HEADLINE = "Couldn't confirm the schedule"  # the card the owner was looking at
_PASSWORD_FIELD = "Windows account password"


@pytest.fixture
def stub_page() -> MagicMock:
    """A permissive stub Page — any attr/method access returns a child mock no-op."""
    return MagicMock()


# --------------------------------------------------------------------------- #
# Harness                                                                      #
# --------------------------------------------------------------------------- #
def _queue_register_results(monkeypatch, results) -> dict:
    """Queue a per-call ``register_task`` result (the existing capture helper is single-shot).

    A two-attempt sequence needs attempt 1 to FAIL and a later attempt to SUCCEED, which a fixed
    return value cannot express. Exhausting the queue falls through to success, so a test can
    never pass by silently re-reading the failure.
    """
    seen: dict = {"calls": 0}
    queue = list(results)

    def _fake(**kwargs):
        seen["calls"] += 1
        seen.update(kwargs)
        return queue.pop(0) if queue else (True, "ok")

    monkeypatch.setattr("src.scheduler.windows.register_task", _fake)
    return seen


def _settings_tree(tmp_path, stub_page, monkeypatch, **over):
    """The flat Settings scroll, with a LIVE task recorded on the signed-in account."""
    cfg = _settings(tmp_path, monkeypatch, **over)
    if cfg.schedule_registered:
        cfg.schedule_task_args = _registered_args(cfg)
    return cfg, build_setup(stub_page)


def _wizard_tree_on_schedule_step(tmp_path, stub_page, monkeypatch):
    """The first-run wizard, advanced to its Schedule step (step 4) with the REAL section.

    ``setup_completed=False`` with nothing registered is the only shape that renders the wizard:
    ``has_completed_setup()`` ORs in ``is_complete() and schedule_registered``, so a wizard with a
    live task is not a reachable state — which is why this mount's refusal is the password one.
    """
    cfg = _settings(tmp_path, monkeypatch, setup_completed=False)
    tree = build_setup(stub_page)  # district + folders satisfied → resumes at Delivery (step 3)
    _button_by_content(tree, "Set up later").on_click(None)  # defer delivery → Schedule
    assert _has_text(tree, "Step 4 of 5")
    return cfg, tree


def _press_register(tree) -> None:
    _button_by_content(tree, "Schedule nightly sync").on_click(None)


def _enter_from(field) -> None:
    """Type-then-Enter, the way the app itself runs it.

    ``on_change`` repaints the gate + its inline note; ``on_submit`` is Enter, the path that
    still reaches ``_register`` while the primary is gated (deliberately unchanged — the gate is
    re-evaluated on entry, so Enter cannot bypass a refusal). The press is therefore a genuine
    user action, not a test reaching past a disabled control.
    """
    field.on_change(None)
    field.on_submit(None)


def _refusal_card(tree):
    """The refusal ``ErrorCard`` itself — the innermost container carrying its headline.

    Depth-first order puts ancestors before descendants, so the LAST match is the card rather
    than the section it sits in. Asserting on the CARD is what proves the reason reached
    ``result_slot``; asserting on the whole tree would pass on the inline note under the account
    field, which was already there and is exactly what the admin did not see.
    """
    cards = [
        c
        for c in _iter_controls(tree)
        if isinstance(c, ft.Container)
        and any(getattr(x, "value", None) == setup_mod._REGISTER_REFUSED_HEADLINE for x in _iter_controls(c))
    ]
    return cards[-1] if cards else None


# --------------------------------------------------------------------------- #
# Settings mount — the reported reason (a live task on another principal)       #
# --------------------------------------------------------------------------- #
def test_settings_refused_retry_replaces_the_failed_register_card(tmp_path, stub_page, monkeypatch):
    """Failing register → a retry the gate REFUSES ⇒ the old card is gone and the reason is up."""
    cfg, tree = _settings_tree(
        tmp_path,
        stub_page,
        monkeypatch,
        schedule_registered=True,
        schedule_unattended=True,
        schedule_run_as_user="",  # recorded as the signed-in account
    )
    seen = _queue_register_results(monkeypatch, [(False, windows._MSG_ELEVATION_NO_RESULT)])

    # Attempt 1 — the prefilled (recorded) account: the gate is open, so this DISPATCHES and the
    # elevated step comes back unconfirmed. The positive half of the pair: without this card on
    # screen, "the card is gone" below would be vacuous.
    _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
    _press_register(tree)
    _drain(stub_page)
    assert seen["calls"] == 1
    assert _has_text(tree, _FAILED_HEADLINE)

    # Attempt 2 — a service account instead. A task is live on another principal, so the gate
    # refuses the in-place switch and nothing is dispatched.
    account = _textfield_by_label(tree, SCHEDULE_ACCOUNT_FIELD_LABEL)
    account.value = _SERVICE
    _enter_from(account)
    _drain(stub_page)

    assert seen["calls"] == 1, "a refused attempt must not reach register_task"
    assert not _has_text(tree, _FAILED_HEADLINE), "the previous attempt's failure card survived a fresh press"
    card = _refusal_card(tree)
    assert card is not None, "the refusal painted nothing where the failure had been"
    # The remedy is the app's ONE wording for this cause, not a second copy of it.
    assert _has_text(card, setup_mod._ACCOUNT_SWITCH_NOTE.format(recorded=_SIGNED_IN))
    assert _has_text_containing(card, "Remove nightly sync")
    assert cfg.schedule_run_as_user == "", "a refusal must not touch the record"


def test_settings_a_dispatched_attempt_after_a_refusal_still_reaches_its_banner(tmp_path, stub_page, monkeypatch):
    """The twin: clearing the slot on a refusal must not leave a section that can't report."""
    _cfg, tree = _settings_tree(
        tmp_path,
        stub_page,
        monkeypatch,
        schedule_registered=True,
        schedule_unattended=True,
        schedule_run_as_user="",
    )
    seen = _queue_register_results(monkeypatch, [(False, windows._MSG_ELEVATION_NO_RESULT)])
    _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
    _press_register(tree)
    _drain(stub_page)

    account = _textfield_by_label(tree, SCHEDULE_ACCOUNT_FIELD_LABEL)
    account.value = _SERVICE  # refused — the switch
    _enter_from(account)
    _drain(stub_page)
    assert _refusal_card(tree) is not None

    account.value = _SIGNED_IN  # back to the recorded principal → the gate opens again
    account.on_change(None)
    _press_register(tree)
    _drain(stub_page)

    assert seen["calls"] == 2
    assert _has_text(tree, "Nightly sync scheduled")
    assert _refusal_card(tree) is None, "the refusal card outlived the attempt that replaced it"


# --------------------------------------------------------------------------- #
# Wizard mount — the same sequence on the surface an admin meets first          #
# --------------------------------------------------------------------------- #
def test_wizard_refused_retry_replaces_the_failed_register_card(tmp_path, stub_page, monkeypatch):
    """Same defect, wizard Schedule step: here the refusal is the missing service-account password."""
    _cfg, tree = _wizard_tree_on_schedule_step(tmp_path, stub_page, monkeypatch)
    seen = _queue_register_results(monkeypatch, [(False, windows._MSG_ELEVATION_NO_RESULT)])

    account = _textfield_by_label(tree, SCHEDULE_ACCOUNT_FIELD_LABEL)
    account.value = _SERVICE
    password = _textfield_by_label(tree, _PASSWORD_FIELD)
    password.value = "pw"
    _press_register(tree)
    _drain(stub_page)
    assert seen["calls"] == 1
    assert _has_text(tree, _FAILED_HEADLINE)

    # The retry an admin who blames the password actually makes: clear it and try again.
    password.value = ""
    _enter_from(password)
    _drain(stub_page)

    assert seen["calls"] == 1, "a refused attempt must not reach register_task"
    assert not _has_text(tree, _FAILED_HEADLINE), "the previous attempt's failure card survived a fresh press"
    card = _refusal_card(tree)
    assert card is not None, "the refusal painted nothing where the failure had been"
    assert _has_text(card, setup_mod._ACCOUNT_PASSWORD_NOTE)


def test_wizard_a_dispatched_attempt_after_a_refusal_still_reaches_its_banner(tmp_path, stub_page, monkeypatch):
    """The wizard's twin — the step can still report a success after clearing a refusal."""
    _cfg, tree = _wizard_tree_on_schedule_step(tmp_path, stub_page, monkeypatch)
    seen = _queue_register_results(monkeypatch, [(False, windows._MSG_ELEVATION_NO_RESULT)])

    account = _textfield_by_label(tree, SCHEDULE_ACCOUNT_FIELD_LABEL)
    account.value = _SERVICE
    password = _textfield_by_label(tree, _PASSWORD_FIELD)
    password.value = "pw"
    _press_register(tree)
    _drain(stub_page)

    password.value = ""  # refused — a foreign account with no password
    _enter_from(password)
    _drain(stub_page)
    assert _refusal_card(tree) is not None

    password.value = "pw"  # the real retry
    password.on_change(None)
    _press_register(tree)
    _drain(stub_page)

    assert seen["calls"] == 2
    assert _has_text(tree, "Nightly sync scheduled")
    assert _refusal_card(tree) is None, "the refusal card outlived the attempt that replaced it"


# --------------------------------------------------------------------------- #
# The two silent reasons still clear the slot                                   #
# --------------------------------------------------------------------------- #
def test_a_blank_run_time_refusal_clears_the_stale_card_without_painting_one(tmp_path, stub_page, monkeypatch):
    """RUN_TIME / INCOMPLETE keep their existing homes — but the stale card still goes.

    The run time is answered by the field right above the button, so this refusal deliberately
    paints nothing; what it must NOT do is leave a failure card claiming a state that the press
    just superseded.
    """
    _cfg, tree = _settings_tree(
        tmp_path,
        stub_page,
        monkeypatch,
        schedule_registered=True,
        schedule_unattended=True,
        schedule_run_as_user="",
    )
    seen = _queue_register_results(monkeypatch, [(False, windows._MSG_ELEVATION_NO_RESULT)])
    _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
    _press_register(tree)
    _drain(stub_page)
    assert _has_text(tree, _FAILED_HEADLINE)

    run_time = _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)")
    run_time.value = ""
    _enter_from(run_time)
    _drain(stub_page)

    assert seen["calls"] == 1
    assert not _has_text(tree, _FAILED_HEADLINE)
    assert _refusal_card(tree) is None, "a blank run time is answered by its own field, not a card"
