"""Plan 0055 M2a (DSYNC-95): machine setup, headless.

``--machine-setup [--updates off|notify|on]`` provisions the shared profile (plan 0049's op) with NO nightly task and the
signed-in administrator as the only principal, and writes the update mode in the same HKLM commit, before the switch.
The elevated half validates before it changes anything; the parent half is ``complete_handover``, as after a Schedule press.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import main as main_mod
from src.main import cli
from src.scheduler import elevated_apply, provision_session, provisioning
from src.scheduler.provision_session import HandoverOutcome, ProvisionAttempt, ProvisionOutcome
from src.scheduler.provisioning import ProvisionRefused, ProvisionStep
from src.update import policy


class TestTheVocabulary:
    @pytest.mark.parametrize(("raw", "mode"), [("off", "off"), (" Notify ", "notify"), ("ON", "on")])
    def test_the_three_modes_parse(self, raw, mode):
        assert policy.parse_update_mode(raw) == mode

    @pytest.mark.parametrize("raw", ["", "auto", "1", None, 1, "on;off"])
    def test_anything_else_is_refused(self, raw):
        with pytest.raises(ValueError):
            policy.parse_update_mode(raw)

    def test_the_default_is_notify(self):
        """Owner decision O1, 2026-11-23."""
        assert policy.DEFAULT_UPDATE_MODE == "notify"


class TestThePayload:
    def test_it_names_no_principal_and_carries_the_mode(self, monkeypatch):
        monkeypatch.delenv("DISTRICTSYNC_DATA_DIR", raising=False)
        monkeypatch.setattr(provisioning.paths, "per_user_data_dir", lambda: Path("C:/profile"))
        monkeypatch.setattr(provisioning, "_refuse_under_override", lambda: None)
        payload = provisioning.build_machine_setup_payload(update_mode="ON")
        assert payload["op"] == "machine_setup"
        assert payload["user"] == ""
        assert payload["update_mode"] == "on"
        assert "task_name" not in payload  # no nightly is registered

    def test_it_refuses_under_the_data_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path))
        with pytest.raises(ProvisionRefused) as caught:
            provisioning.build_machine_setup_payload(update_mode="notify")
        assert caught.value.step is ProvisionStep.OVERRIDE

    def test_an_unknown_mode_is_refused_before_anything_is_asked_of_windows(self, monkeypatch):
        monkeypatch.setattr(provisioning, "_refuse_under_override", lambda: None)
        with pytest.raises(ValueError):
            provisioning.build_machine_setup_payload(update_mode="auto")


class TestTheChildHalf:
    @pytest.fixture
    def applied(self, monkeypatch):
        calls: list[dict[str, object]] = []

        def fake_apply(payload, *, register, update_mode=None):
            register()  # must be a no-op: nothing to register
            calls.append({"payload": payload, "update_mode": update_mode})

        monkeypatch.setattr(provisioning, "apply_provision", fake_apply)
        return calls

    def _payload(self, **over: object) -> dict[str, object]:
        payload: dict[str, object] = {"op": "machine_setup", "source_data_dir": "C:/p", "user": "", "update_mode": "on"}
        payload.update(over)
        return payload

    def test_it_provisions_with_the_setup_admin_only_and_writes_the_mode_in_the_commit(self, applied):
        elevated_apply._do_machine_setup(self._payload())
        assert len(applied) == 1
        assert applied[0]["update_mode"] == "on"
        assert applied[0]["payload"]["user"] == ""

    @pytest.mark.parametrize(
        "bad",
        [{"update_mode": "auto"}, {"user": "CORP\\svc"}],
        ids=["unknown-mode", "a-named-principal"],
    )
    def test_a_bad_request_is_refused_before_anything_changes(self, applied, bad):
        with pytest.raises(ValueError):
            elevated_apply._do_machine_setup(self._payload(**bad))
        assert applied == []

    def test_a_request_missing_a_field_is_refused(self, applied):
        payload = self._payload()
        del payload["update_mode"]
        with pytest.raises(ValueError):
            elevated_apply._do_machine_setup(payload)
        assert applied == []

    def test_apply_machine_setup_refuses_a_named_principal_itself_too(self, applied):
        with pytest.raises(ValueError):
            provisioning.apply_machine_setup(self._payload(user="CORP\\svc"))
        assert applied == []


class TestTheParentHalf:
    def test_a_refused_build_is_a_bounded_refusal_and_nothing_is_elevated(self, monkeypatch):
        def refuse(**_kw):
            raise ProvisionRefused(ProvisionStep.OVERRIDE)

        elevated: list[object] = []
        monkeypatch.setattr(provision_session, "build_machine_setup_payload", refuse)
        monkeypatch.setattr(provision_session, "_elevate_and_read", lambda *a, **k: elevated.append(a))
        attempt = provision_session.request_machine_setup(update_mode="on", sftp=False, sftp_host="", sftp_username="")
        assert attempt.outcome is ProvisionOutcome.REFUSED and attempt.step is ProvisionStep.OVERRIDE
        assert elevated == []

    def test_a_good_request_is_elevated_with_no_schedule_check(self, monkeypatch):
        seen: dict[str, object] = {}
        monkeypatch.setattr(
            provision_session, "build_machine_setup_payload", lambda **kw: {"op": "machine_setup", **kw}
        )

        def elevate(payload, *, wants_check):
            seen.update(payload=payload, wants_check=wants_check)
            return ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED)

        monkeypatch.setattr(provision_session, "_elevate_and_read", elevate)
        attempt = provision_session.request_machine_setup(update_mode="off", sftp=False, sftp_host="", sftp_username="")
        assert attempt.outcome is ProvisionOutcome.PROVISIONED
        assert seen["wants_check"] is False
        assert seen["payload"]["update_mode"] == "off"


class TestTheCommandLine:
    @pytest.fixture
    def windows(self, monkeypatch):
        monkeypatch.setattr(main_mod.sys, "platform", "win32")
        monkeypatch.setattr("src.scheduler.task_com.runs_from_per_user_install", lambda exe: False)

    def _wire(self, monkeypatch, *, outcome: ProvisionOutcome, handed_over: bool) -> dict[str, object]:
        seen: dict[str, object] = {}

        def request(**kw):
            seen["request"] = kw
            return ProvisionAttempt(outcome=outcome)

        def handover(*, persist, reenter):
            seen["handover"] = True
            return HandoverOutcome(handed_over=handed_over)

        monkeypatch.setattr(provision_session, "request_machine_setup", request)
        monkeypatch.setattr(provision_session, "complete_handover", handover)
        return seen

    def test_it_sets_the_computer_up_and_exits_0(self, windows, monkeypatch, capsys):
        seen = self._wire(monkeypatch, outcome=ProvisionOutcome.PROVISIONED, handed_over=True)
        assert cli(["--machine-setup", "--updates", "on"]) == 0
        out = capsys.readouterr().out
        assert main_mod.MACHINE_SETUP_DONE in out and "Automatic updates: on." in out
        assert seen["request"]["update_mode"] == "on"
        assert seen["handover"] is True

    def test_the_default_mode_is_notify(self, windows, monkeypatch):
        seen = self._wire(monkeypatch, outcome=ProvisionOutcome.PROVISIONED, handed_over=True)
        assert cli(["--machine-setup"]) == 0
        assert seen["request"]["update_mode"] == "notify"

    def test_a_declined_prompt_still_runs_the_handover_and_exits_1(self, windows, monkeypatch, capsys):
        seen = self._wire(monkeypatch, outcome=ProvisionOutcome.DECLINED, handed_over=False)
        assert cli(["--machine-setup"]) == 1
        out = capsys.readouterr().out
        assert main_mod.MACHINE_SETUP_COPY["declined"] in out
        assert "still keeps DistrictSync settings per Windows account" in out
        assert seen["handover"] is True  # complete_handover runs on EVERY outcome

    def test_an_unconfirmed_child_is_settled_by_the_handover_not_the_child(self, windows, monkeypatch):
        """A killed child may have committed: the switch read inside complete_handover decides the exit code."""
        self._wire(monkeypatch, outcome=ProvisionOutcome.UNCONFIRMED, handed_over=True)
        assert cli(["--machine-setup"]) == 0

    def test_a_per_user_installed_copy_is_refused_before_any_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr(main_mod.sys, "platform", "win32")
        monkeypatch.setattr("src.scheduler.task_com.runs_from_per_user_install", lambda exe: True)
        seen = self._wire(monkeypatch, outcome=ProvisionOutcome.PROVISIONED, handed_over=True)
        assert cli(["--machine-setup"]) == 1
        assert "installed for one Windows account only" in capsys.readouterr().out
        assert seen == {}

    def test_it_is_refused_off_windows(self, monkeypatch, capsys):
        monkeypatch.setattr(main_mod.sys, "platform", "linux")
        seen = self._wire(monkeypatch, outcome=ProvisionOutcome.PROVISIONED, handed_over=True)
        assert cli(["--machine-setup"]) == 1
        assert seen == {}

    def test_it_cannot_be_combined_with_an_sftp_subcommand(self, capsys):
        assert cli(["--machine-setup", "--sftp-test"]) == 2

    def test_an_unknown_mode_is_a_usage_error(self):
        assert cli(["--machine-setup", "--updates", "auto"]) == 2
