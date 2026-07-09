"""View-level behaviour of the Setup SFTP section (Slice 7, D6).

The section itself is coverage-omitted view glue, but the trust-critical WIRING is
pinned here by driving the real handlers through the built control tree (the
``_driving_page`` pattern the schedule crash-net tests already use):

  - **Bug pin (red-first):** a failed/typo'd Test must NOT overwrite the working
    stored credential — today ``_test`` writes the keyring BEFORE the network call.
  - the typed password threads to ``test_connection(password_override=...)``, not
    ``store_password``; the keyring is written ONLY on Save (exactly once);
  - the success copy is decided by the pure ``sftp_test_copy`` with the right
    provenance (typed vs stored) and unsaved-edits flag.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.sftp.uploader import KEYRING_SERVICE, SFTPUploader


def _iter_controls(control):
    yield control
    children: list[object] = []
    ctrls = getattr(control, "controls", None)
    if isinstance(ctrls, list):
        children.extend(ctrls)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        children.append(content)
    for child in children:
        if isinstance(child, ft.Control):
            yield from _iter_controls(child)


def _button_by_content(tree, content):
    return next(c for c in _iter_controls(tree) if getattr(c, "content", None) == content)


def _textfield_by_label(tree, label):
    return next((f for f in _iter_controls(tree) if isinstance(f, ft.TextField) and (f.label or "") == label), None)


def _driving_page(captured: list):
    """A page stub that runs off-thread workers inline and EXECUTES marshalled coroutines.

    ``run_thread`` runs the worker body synchronously; ``run_task`` records the args and
    then runs the (result-rendering) coroutine to completion so the copy wiring is exercised.
    """
    page = MagicMock()
    page.run_thread = lambda fn: fn()

    def _run_task(coro, *args):
        captured.append(args)
        asyncio.run(coro(*args))

    page.run_task = _run_task
    return page


def _benign_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the on-mount schedule read-back from firing a real PowerShell subprocess."""
    from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

    benign = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
    monkeypatch.setattr("src.ui_flet.schedule_probe.probe_schedule", lambda *a, **k: benign)


def _configured_cfg() -> AppConfig:
    return AppConfig(
        input_dir="/in",
        output_dir="/out",
        sis_type="myedbc",
        sftp_enabled=True,
        sftp_host="sftp.ca.spacesedu.com",
        sftp_username="district_x",
        sftp_remote_path="/files",
        sftp_port=22,
    )


def _mount(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig, captured: list):
    from src.ui_flet.screens.setup import build_setup

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    _benign_schedule(monkeypatch)
    tree = build_setup(_driving_page(captured))
    captured.clear()  # discard the on-mount schedule readout marshal
    return tree


class TestTestConnectionIsSideEffectFree:
    def test_failed_test_leaves_stored_credential_intact(self, monkeypatch):
        """RED-first bug pin: a typo'd Test can no longer clobber a working credential."""
        import keyring as kr

        kr.set_password(KEYRING_SERVICE, "district_x", "working-pw")
        monkeypatch.setattr(
            SFTPUploader,
            "test_connection",
            lambda self, password_override=None: (False, "authentication failed"),
        )

        captured: list = []
        tree = _mount(monkeypatch, _configured_cfg(), captured)
        _textfield_by_label(tree, "Password").value = "typo-pw"
        _button_by_content(tree, "Test connection").on_click(None)

        # The working stored credential survives a failed Test with a typo'd password.
        assert kr.get_password(KEYRING_SERVICE, "district_x") == "working-pw"

    def test_typed_password_threads_to_override_not_store(self, monkeypatch):
        seen: dict[str, object] = {}

        def fake_test(self, password_override=None):
            seen["override"] = password_override
            return True, f"Connection to {self.host}:{self.port} successful."

        store_spy = MagicMock()
        monkeypatch.setattr(SFTPUploader, "test_connection", fake_test)
        monkeypatch.setattr(SFTPUploader, "store_password", store_spy)

        captured: list = []
        tree = _mount(monkeypatch, _configured_cfg(), captured)
        _textfield_by_label(tree, "Password").value = "typed-pw"
        _button_by_content(tree, "Test connection").on_click(None)

        assert seen["override"] == "typed-pw"  # rode the transient override
        store_spy.assert_not_called()  # never the keyring on the test path
        assert captured and captured[-1][0] is True  # one success result marshalled


class TestSuccessCopyProvenanceWiring:
    """The view feeds the right provenance + unsaved flag into the pure copy helper."""

    def _spy_copy(self, monkeypatch) -> list:
        calls: list = []

        def fake_copy(**kwargs):
            calls.append(kwargs)
            return ("SFTP connection succeeded", "detail")

        monkeypatch.setattr("src.ui_flet.screens.setup.sftp_test_copy", fake_copy)
        return calls

    def test_typed_password_is_typed_provenance(self, monkeypatch):
        monkeypatch.setattr(
            SFTPUploader,
            "test_connection",
            lambda self, password_override=None: (True, "ok"),
        )
        calls = self._spy_copy(monkeypatch)
        tree = _mount(monkeypatch, _configured_cfg(), [])
        _textfield_by_label(tree, "Password").value = "typed-pw"
        _button_by_content(tree, "Test connection").on_click(None)

        assert calls[-1]["provenance"] == "typed"

    def test_blank_password_matching_saved_is_stored_and_saved(self, monkeypatch):
        monkeypatch.setattr(
            SFTPUploader,
            "test_connection",
            lambda self, password_override=None: (True, "ok"),
        )
        calls = self._spy_copy(monkeypatch)
        tree = _mount(monkeypatch, _configured_cfg(), [])
        # Password left blank + all fields match the saved config.
        _button_by_content(tree, "Test connection").on_click(None)

        assert calls[-1]["provenance"] == "stored"
        assert calls[-1]["unsaved_edits"] is False

    def test_edited_username_flags_unsaved(self, monkeypatch):
        monkeypatch.setattr(
            SFTPUploader,
            "test_connection",
            lambda self, password_override=None: (True, "ok"),
        )
        calls = self._spy_copy(monkeypatch)
        tree = _mount(monkeypatch, _configured_cfg(), [])
        _textfield_by_label(tree, "Username").value = "someone_else"
        _button_by_content(tree, "Test connection").on_click(None)

        assert calls[-1]["unsaved_edits"] is True


class TestSaveWritesKeyringExactlyOnce:
    def test_save_stores_password_once(self, monkeypatch):
        store_spy = MagicMock()
        monkeypatch.setattr(SFTPUploader, "store_password", store_spy)
        monkeypatch.setattr(SFTPUploader, "get_stored_password", lambda self: "typed-pw")

        captured: list = []
        tree = _mount(monkeypatch, _configured_cfg(), captured)
        _textfield_by_label(tree, "Password").value = "typed-pw"
        _button_by_content(tree, "Save SFTP credentials").on_click(None)

        store_spy.assert_called_once_with("typed-pw")
