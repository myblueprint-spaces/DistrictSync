"""Plan 0055 M3 (DSYNC-96): headless ``--schedule-register`` / ``--schedule-remove``.

The command line is another CALLER of what the Schedule button uses: the same gate (``register_gate.register_block``,
G0 included), the same engines (the ``Scheduler`` protocol for the signed-in account, ``request_provision`` +
``complete_handover`` for another account) and the same record (``schedule_record``). A password never reaches a
message, a log line or ``AppConfig``.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.scheduler import headless, provision_session
from src.scheduler.register_gate import RegisterBlock
from src.scheduler.task_com import PrincipalKind

SECRET = "Tr0ub4dor&3-never-printed"
SRC = Path(__file__).resolve().parent.parent / "src"


class FakeScheduler:
    supports_unattended_password = True

    def __init__(self, ok: bool = True, message: str = "Schedule registered.", account: str = "CORP\\admin") -> None:
        self.ok, self.message, self.account = ok, message, account
        self.registered: list[dict] = []
        self.deleted: list[str] = []
        self.delete_result: tuple[bool, str] = (True, "deleted")

    def run_as_user(self) -> str:
        return self.account

    def register(self, **kwargs):
        self.registered.append(kwargs)
        return self.ok, self.message

    def delete(self, task_name: str):
        self.deleted.append(task_name)
        return self.delete_result


@pytest.fixture()
def sched(monkeypatch, tmp_path):
    fake = FakeScheduler()
    monkeypatch.setattr(headless, "get_scheduler", lambda: fake)
    monkeypatch.setattr(headless, "_install_not_shareable", lambda: False)
    monkeypatch.setattr(provision_session, "delivery_secret_unreadable", lambda **kw: False)
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    AppConfig(input_dir=str(tmp_path / "in"), output_dir=str(tmp_path / "out"), sis_type="myedbc").save()
    return fake


def _spec(text: str) -> headless.PrincipalSpec:
    return headless.parse_principal(text)


class TestThePrincipalFlag:
    @pytest.mark.parametrize(
        ("text", "kind", "account"),
        [
            ("current", PrincipalKind.INTERACTIVE_TOKEN, ""),
            ("CURRENT", PrincipalKind.INTERACTIVE_TOKEN, ""),
            ("gmsa:CORP\\svc_dsync$", PrincipalKind.MANAGED_SERVICE_ACCOUNT, "CORP\\svc_dsync$"),
            ("password:CORP\\svc", PrincipalKind.PASSWORD, "CORP\\svc"),
        ],
    )
    def test_it_parses(self, text, kind, account):
        assert _spec(text) == headless.PrincipalSpec(kind, account)

    @pytest.mark.parametrize("text", ["", "gmsa:", "svc", "token:CORP\\x", "password"])
    def test_anything_else_is_refused(self, text):
        with pytest.raises(ValueError, match="--principal"):
            _spec(text)

    def test_every_gate_rung_has_a_line(self):
        assert set(headless.GATE_COPY) == set(RegisterBlock) - {RegisterBlock.NONE}


class TestRegisterTheSignedInAccount:
    def test_it_registers_and_writes_the_record(self, sched):
        code, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=True)
        assert code == 0 and "02:30" in message
        (call,) = sched.registered
        assert call["run_time"] == "02:30" and call["sis_type"] == "myedbc" and call["wake_to_run"] is True
        assert call["principal"].kind is PrincipalKind.INTERACTIVE_TOKEN
        cfg = AppConfig.load()
        assert cfg.schedule_registered and cfg.schedule_time == "02:30"
        assert cfg.schedule_run_as_kind == "interactive_token" and cfg.schedule_run_as_user == ""
        assert cfg.schedule_task_args["sis_type"] == "myedbc" and cfg.schedule_wake_to_run is True

    def test_a_failed_registration_writes_no_record(self, sched):
        sched.ok, sched.message = False, "Access is denied."
        code, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        assert code == 1 and "Access is denied" in message
        assert not AppConfig.load().schedule_registered

    def test_an_incomplete_setup_is_refused_before_windows_is_asked(self, sched):
        AppConfig(input_dir="", output_dir="", sis_type="").save()
        code, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        assert (code, message) == (1, headless.GATE_COPY[RegisterBlock.INCOMPLETE])
        assert sched.registered == []

    @pytest.mark.parametrize("bad", ["", "25:00", "2pm"])
    def test_a_bad_run_time_is_refused(self, sched, bad):
        code, _ = headless.register(run_time=bad, spec=_spec("current"), password=None, wake_to_run=False)
        assert code == 1 and sched.registered == []


class TestRegisterAnotherAccount:
    @pytest.fixture()
    def provision(self, monkeypatch):
        calls: dict = {"persisted": 0}

        def request_provision(*args, **kwargs):
            calls["principal"] = kwargs["principal"]
            return provision_session.ProvisionAttempt(
                outcome=calls.get("outcome", provision_session.ProvisionOutcome.PROVISIONED)
            )

        def complete_handover(*, persist, reenter):
            persist()
            calls["persisted"] += 1
            return provision_session.HandoverOutcome(handed_over=True)

        monkeypatch.setattr(provision_session, "request_provision", request_provision)
        monkeypatch.setattr(provision_session, "complete_handover", complete_handover)
        return calls

    def test_a_password_account_provisions_and_records(self, sched, provision):
        code, _ = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert code == 0
        assert provision["principal"].kind is PrincipalKind.PASSWORD and sched.registered == []
        cfg = AppConfig.load()
        assert cfg.schedule_run_as_user == "CORP\\svc" and cfg.schedule_run_as_kind == "password"

    def test_a_refused_provision_records_nothing(self, sched, provision):
        provision["outcome"] = provision_session.ProvisionOutcome.DECLINED
        code, message = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert code == 1 and "declined" in message
        assert not AppConfig.load().schedule_registered

    def test_a_password_account_without_a_password_is_refused_by_the_gate(self, sched, provision):
        code, message = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=None, wake_to_run=False
        )
        assert (code, message) == (1, headless.GATE_COPY[RegisterBlock.ACCOUNT_NEEDS_PASSWORD])
        assert "principal" not in provision

    def test_a_gmsa_never_carries_a_password(self, sched, provision):
        code, _ = headless.register(
            run_time="03:00", spec=_spec("gmsa:CORP\\svc_dsync$"), password=SECRET, wake_to_run=False
        )
        assert code == 0 and provision["principal"].password is None

    def test_g0_a_per_user_copy_is_refused_before_any_prompt(self, sched, provision, monkeypatch):
        monkeypatch.setattr(headless, "_install_not_shareable", lambda: True)
        code, message = headless.register(
            run_time="03:00", spec=_spec("gmsa:CORP\\svc_dsync$"), password=None, wake_to_run=False
        )
        assert (code, message) == (1, headless.GATE_COPY[RegisterBlock.INSTALL_NOT_SHAREABLE])
        assert "principal" not in provision

    def test_switching_accounts_needs_a_remove_first(self, sched, provision):
        headless.register(run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False)
        code, message = headless.register(
            run_time="03:00", spec=_spec("gmsa:CORP\\other$"), password=None, wake_to_run=False
        )
        assert (code, message) == (1, headless.GATE_COPY[RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE])

    def test_the_password_reaches_no_message_and_no_log(self, sched, provision, caplog):
        with caplog.at_level(logging.DEBUG):
            _, ok_message = headless.register(
                run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
            )
            provision["outcome"] = provision_session.ProvisionOutcome.FAILED
            _, bad_message = headless.register(
                run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
            )
        assert provision["principal"].password == SECRET  # positive twin: it did reach the engine
        assert SECRET not in ok_message + bad_message + caplog.text
        from src.utils import paths

        files = [p for p in paths.user_data_dir().rglob("*") if p.is_file()]
        assert files and SECRET not in "".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


class TestRemove:
    def test_it_removes_and_clears_the_record(self, sched):
        headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        code, message = headless.remove()
        assert code == 0 and "Removed" in message and sched.deleted
        cfg = AppConfig.load()
        assert not cfg.schedule_registered and cfg.schedule_run_as_kind == "" and cfg.schedule_task_args is None
        assert cfg.schedule_time == "02:30"  # kept, like the window

    def test_an_absent_task_is_success_shaped(self, sched):
        sched.delete_result = (False, "The system cannot find the file specified.")
        code, message = headless.remove()
        assert code == 0 and "nothing changed" in message

    def test_a_real_failure_keeps_the_record(self, sched):
        headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        sched.delete_result = (False, "Access is denied.")
        code, _ = headless.remove()
        assert code == 1 and AppConfig.load().schedule_registered

    def test_a_gmsa_on_shared_settings_is_removed_with_its_access(self, sched, monkeypatch):
        from src.utils import paths

        headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)  # a full record
        cfg = AppConfig.load()
        cfg.schedule_run_as_user, cfg.schedule_run_as_kind = "CORP\\svc$", "managed_service_account"
        cfg.save()
        monkeypatch.setattr(paths, "is_machine_scope", lambda: True)
        seen = {}

        def revoke(task_name, *, user, kind):
            seen["user"] = user
            return provision_session.RemovalAttempt(ok=True, message="", revoke=provision_session.RevokeOutcome.REVOKED)

        monkeypatch.setattr(provision_session, "request_removal_with_revoke", revoke)
        code, _ = headless.remove()
        assert code == 0 and seen["user"] == "CORP\\svc$" and sched.deleted == []


class TestTheCommandLine:
    def _main(self, argv):
        from src.main import cli

        return cli(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["--schedule-register", "--schedule-remove"],
            ["--schedule-register", "--sftp-show"],
            ["--schedule-register", "--machine-setup"],
            ["--time", "02:00"],
            ["--schedule-remove", "--principal", "current"],
            ["--schedule-register", "--time", "02:00", "--sis", "myedbc"],
        ],
    )
    def test_misuse_is_exit_2(self, argv, sched):
        assert self._main(argv) == 2
        assert sched.registered == []

    def test_the_password_comes_from_the_environment_never_argv(self, sched, monkeypatch):
        seen = {}

        def register(**kwargs):
            seen.update(kwargs)
            return 0, "ok"

        monkeypatch.setattr(headless, "register", register)
        monkeypatch.setenv("DISTRICTSYNC_TASK_PASSWORD", SECRET)
        assert self._main(["--schedule-register", "--time", "03:00", "--principal", "password:CORP\\svc"]) == 0
        assert seen["password"] == SECRET

    def test_an_empty_password_stdin_is_exit_2(self, sched, monkeypatch):
        import io

        monkeypatch.delenv("DISTRICTSYNC_TASK_PASSWORD", raising=False)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        argv = ["--schedule-register", "--time", "03:00", "--principal", "password:CORP\\svc", "--task-password-stdin"]
        assert self._main(argv) == 2


def test_the_record_has_one_writer():
    """``schedule_run_as_kind`` is assigned only by ``schedule_record`` (the window and the command share it)."""
    writers = []
    for source in SRC.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Attribute) and t.attr == "schedule_run_as_kind" for t in targets):
                    writers.append(source.name)
    assert sorted(set(writers)) == ["schedule_record.py"]
