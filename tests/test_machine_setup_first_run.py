"""Plan 0055 M2b (DSYNC-106): a machine-wide install refuses a per-user profile until it is set up.

Pinned here:

* the predicate (:func:`machine_setup.needs_machine_setup`): only a MACHINE install, only a switch PROVABLY off, never
  under ``DISTRICTSYNC_DATA_DIR``, and any surprise reads as "not needed" (the old behaviour);
* the command line refuses everything but ``--machine-setup`` / ``--version`` / ``--help`` there, BEFORE the profile is
  pinned or the log sink opens (a positive twin proves the gate is what stopped it);
* the window: one filled primary, the update question defaulting to ``notify``, the choice reaching the ladder, a
  re-launch once the shared profile is in use, and an ``ErrorCard`` (with no log-folder button, which would pin a
  per-user profile) otherwise; never a UAC prompt from a test (``request`` is required);
* the launcher shows the window BEFORE the migration and the pin;
* ``--machine-setup`` and the window share one ladder and one copy table.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.scheduler import machine_setup, provision_session
from src.ui_flet import machine_setup_window as win
from src.update import policy
from src.utils import paths
from src.utils.install_kind import InstallKind

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def machine_not_set_up(monkeypatch):
    monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: InstallKind.MACHINE)
    monkeypatch.setattr(provision_session, "switch_state", lambda: False)
    monkeypatch.delenv("DISTRICTSYNC_DATA_DIR", raising=False)


class TestThePredicate:
    def test_a_machine_install_with_the_switch_off_needs_setup(self, machine_not_set_up):
        assert machine_setup.needs_machine_setup() is True

    @pytest.mark.parametrize("kind", [InstallKind.PORTABLE, InstallKind.PER_USER])
    def test_other_install_kinds_never_do(self, machine_not_set_up, monkeypatch, kind):
        monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: kind)
        assert machine_setup.needs_machine_setup() is False

    @pytest.mark.parametrize("state", [True, None])
    def test_a_switch_on_or_unreadable_does_not(self, machine_not_set_up, monkeypatch, state):
        monkeypatch.setattr(provision_session, "switch_state", lambda: state)
        assert machine_setup.needs_machine_setup() is False

    def test_the_test_and_support_profile_is_exempt(self, machine_not_set_up, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path))
        assert machine_setup.needs_machine_setup() is False

    def test_a_surprise_is_not_needed(self, machine_not_set_up, monkeypatch):
        def boom():
            raise RuntimeError("registry")

        monkeypatch.setattr(provision_session, "switch_state", boom)
        assert machine_setup.needs_machine_setup() is False


class TestTheCommandLine:
    @pytest.fixture()
    def pins(self, monkeypatch):
        calls: list[str] = []
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", lambda: calls.append("migrate"))
        monkeypatch.setattr(main_mod, "pin_data_dir", lambda: calls.append("pin"))
        return calls

    def test_a_conversion_is_refused_before_any_profile_is_touched(self, machine_not_set_up, pins, capsys):
        from src.main import cli

        assert cli(["--sis", "myedbc", "--input", "x"]) == 1
        assert machine_setup.NOT_SET_UP_CLI in capsys.readouterr().out
        assert pins == []

    @pytest.mark.parametrize("argv", [["--sftp-show"], ["--schedule-remove"]])
    def test_every_other_command_is_refused_too(self, machine_not_set_up, pins, argv):
        from src.main import cli

        assert cli(argv) == 1 and pins == []

    def test_machine_setup_itself_runs(self, machine_not_set_up, pins, monkeypatch):
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "_machine_setup", lambda args: 0)
        from src.main import cli

        assert cli(["--machine-setup"]) == 0
        assert "pin" in pins  # positive twin: the refusal above was the gate, not something else

    def test_a_set_up_computer_runs_normally(self, machine_not_set_up, pins, monkeypatch):
        monkeypatch.setattr(provision_session, "switch_state", lambda: True)
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "_sftp_show", lambda args: 0)
        from src.main import cli

        assert cli(["--sftp-show"]) == 0 and "pin" in pins


def _iter(control):  # noqa: ANN001, ANN202 - untyped Flet tree
    yield control
    for attr in ("controls", "content", "action"):
        child = getattr(control, attr, None)
        for item in child if isinstance(child, list) else [child] if child is not None else []:
            if isinstance(item, ft.Control):
                yield from _iter(item)


def _text(control) -> str:  # noqa: ANN001
    return "\n".join(
        str(getattr(c, a)) for c in _iter(control) for a in ("value", "content") if isinstance(getattr(c, a, None), str)
    )


def _button(control, label):  # noqa: ANN001, ANN202
    for c in _iter(control):
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and c.content == label:
            return c
    raise AssertionError(label)


def _body(results):  # noqa: ANN001, ANN202
    calls: list[str] = []
    queue = list(results)

    def request(mode: str) -> machine_setup.MachineSetupResult:
        calls.append(f"request:{mode}")
        return queue.pop(0)

    body = win.build_machine_setup_body(MagicMock(), request=request, reexec=lambda: calls.append("reexec"))
    return body, calls


DONE = machine_setup.MachineSetupResult(0, (machine_setup.MACHINE_SETUP_DONE,), True)
DECLINED = machine_setup.MachineSetupResult(1, (machine_setup.MACHINE_SETUP_COPY["declined"],), False)


class TestTheWindow:
    def test_it_asks_with_one_filled_primary_and_notify_by_default(self):
        body, _ = _body([])
        assert [c.content for c in _iter(body) if isinstance(c, ft.FilledButton)] == [win.PRIMARY_LABEL]
        (group,) = [c for c in _iter(body) if isinstance(c, ft.RadioGroup)]
        assert group.value == policy.DEFAULT_UPDATE_MODE == policy.NOTIFY
        assert {r.value for r in _iter(group.content) if isinstance(r, ft.Radio)} == set(policy.UPDATE_MODES)

    def test_the_chosen_mode_reaches_the_ladder_and_success_relaunches(self):
        body, calls = _body([DONE])
        (group,) = [c for c in _iter(body) if isinstance(c, ft.RadioGroup)]
        event = MagicMock()
        event.control.value = policy.ON
        group.on_change(event)
        _button(body, win.PRIMARY_LABEL).on_click(None)
        assert calls == [f"request:{policy.ON}", "reexec"]

    def test_a_failure_is_an_error_card_with_a_retry_and_no_log_folder_button(self):
        body, calls = _body([DECLINED, DONE])
        _button(body, win.PRIMARY_LABEL).on_click(None)
        assert calls == [f"request:{policy.NOTIFY}"]
        text = _text(body)
        assert win.FAILED_HEADLINE in text and machine_setup.MACHINE_SETUP_COPY["declined"] in text
        assert "log folder" not in text.lower()
        _button(body, win.RETRY_LABEL).on_click(None)
        assert calls[-1] == "reexec"

    def test_a_raising_request_is_never_a_dead_button(self):
        def request(mode):  # noqa: ANN001, ANN202
            raise RuntimeError("boom")

        body = win.build_machine_setup_body(MagicMock(), request=request, reexec=lambda: None)
        _button(body, win.PRIMARY_LABEL).on_click(None)
        assert win.FAILED_HEADLINE in _text(body)

    def test_the_request_and_reexec_seams_are_required(self):
        import inspect

        params = inspect.signature(win.build_machine_setup_body).parameters
        assert (
            params["request"].default is inspect.Parameter.empty and params["reexec"].default is inspect.Parameter.empty
        )


class TestTheLauncher:
    def test_the_window_is_shown_before_the_migration_and_the_pin(self):
        source = (ROOT / "src" / "ui_flet" / "launcher.py").read_text(encoding="utf-8")
        body = source[source.index("def main(") :]
        assert (
            body.index("needs_machine_setup()") < body.index("migrate_legacy_data_dir()") < body.index("pin_data_dir()")
        )

    def test_the_cli_gate_comes_before_the_migration_and_the_pin(self):
        source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
        body = source[source.index("def _cli(") :]
        assert (
            body.index("needs_machine_setup()") < body.index("migrate_legacy_data_dir()") < body.index("pin_data_dir()")
        )


class TestOneLadder:
    def test_the_cli_and_the_window_call_the_same_run(self):
        main_tree = ast.parse((ROOT / "src" / "main.py").read_text(encoding="utf-8"))
        assert any(
            isinstance(n, ast.Attribute) and n.attr == "run" and getattr(n.value, "id", "") == "machine_setup"
            for n in ast.walk(main_tree)
        )
        assert "request=run" in (ROOT / "src" / "ui_flet" / "machine_setup_window.py").read_text(encoding="utf-8")

    def test_the_delivery_seed_is_read_without_pinning_a_profile(self, monkeypatch, tmp_path):
        (tmp_path / "config.json").write_text(
            '{"sftp_enabled": true, "sftp_host": "h.example", "sftp_username": "u"}', encoding="utf-8"
        )
        monkeypatch.setattr(paths, "per_user_data_dir", lambda: tmp_path)
        monkeypatch.setattr(paths, "pin_data_dir", lambda: pytest.fail("pinned"))
        assert machine_setup.delivery_seed() == (True, "h.example", "u")

    def test_no_settings_yet_is_no_delivery(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "per_user_data_dir", lambda: tmp_path)
        assert machine_setup.delivery_seed() == (False, "", "")
