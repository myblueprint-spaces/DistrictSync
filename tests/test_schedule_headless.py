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

    def test_a_logged_on_only_task_is_said_never_silent(self, sched):
        _, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        assert headless.LOGGED_ON_ONLY_NOTE in message
        sched.supports_unattended_password = False  # the cron adapter: no such distinction to state
        _, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        assert headless.LOGGED_ON_ONLY_NOTE not in message

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
            if calls.get("refused") is not None:
                return provision_session.HandoverOutcome(handed_over=False, refused=calls["refused"])
            move = calls.get("move_profile_to")
            if move is not None:  # what the real handover does: re-pin to the shared profile, settings moved along
                import shutil

                from src.utils import paths

                move.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paths.user_data_dir() / "config.json", move / "config.json")
                monkeypatch.setattr("src.utils.paths._user_scope_data_dir", lambda *, create: move)
                paths.reset_data_dir_pin()
            persist()
            calls["persisted"] += 1
            return provision_session.HandoverOutcome(handed_over=calls.get("handed_over", True))

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

    def test_an_unreadable_delivery_secret_is_refused_before_any_prompt(self, sched, provision, monkeypatch):
        monkeypatch.setattr(provision_session, "delivery_secret_unreadable", lambda **kw: True)
        code, message = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert (code, message) == (1, headless.GATE_COPY[RegisterBlock.DELIVERY_SECRET_UNREADABLE])
        assert "principal" not in provision

    def test_the_record_lands_in_the_shared_settings_after_the_handover(self, sched, provision, tmp_path):
        from src.utils import paths

        before = paths.user_data_dir()
        provision["move_profile_to"] = tmp_path / "ProgramData" / "DistrictSync"
        code, _ = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert code == 0
        assert '"schedule_registered": true' in (provision["move_profile_to"] / "config.json").read_text(
            encoding="utf-8"
        )
        assert '"schedule_registered": true' not in (before / "config.json").read_text(encoding="utf-8")

    def test_an_unusable_shared_folder_records_nothing(self, sched, provision):
        provision["refused"] = next(iter(__import__("src.utils.paths", fromlist=["x"]).MachineScopeRefusedReason))
        code, message = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert code == 1 and "not usable" in message and not AppConfig.load().schedule_registered

    def test_an_unconfirmed_switch_does_not_claim_the_shared_settings(self, sched, provision):
        provision["handed_over"] = False
        code, message = headless.register(
            run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
        )
        assert code == 1 and "could not be confirmed" in message and "using the shared settings" not in message

    def test_a_failed_test_run_is_a_warning_the_nightly_is_still_scheduled(self, sched, provision, monkeypatch):
        from src.scheduler import schedule_check

        failed = schedule_check.ScheduleCheck(
            verdict=next(v for v in schedule_check.CheckVerdict if v is not schedule_check.CheckVerdict.OK)
        )

        def request_provision(*args, **kwargs):
            provision["principal"] = kwargs["principal"]
            return provision_session.ProvisionAttempt(
                outcome=provision_session.ProvisionOutcome.PROVISIONED, check=failed
            )

        monkeypatch.setattr(provision_session, "request_provision", request_provision)
        code, message = headless.register(
            run_time="03:00", spec=_spec("gmsa:CORP\\svc$"), password=None, wake_to_run=False
        )
        assert code == 0 and message.splitlines()[-1].startswith("Warning: the account's test run did not pass")
        assert AppConfig.load().schedule_registered

    def test_an_engine_error_is_a_fixed_line_never_the_account_or_a_traceback(
        self, sched, provision, monkeypatch, caplog
    ):
        def boom(*args, **kwargs):
            raise ValueError("bad principal CORP\\svc")

        monkeypatch.setattr(provision_session, "request_provision", boom)
        with caplog.at_level(logging.DEBUG):
            code, message = headless.register(
                run_time="03:00", spec=_spec("password:CORP\\svc"), password=SECRET, wake_to_run=False
            )
        assert code == 1 and "unexpected error" in message
        assert "svc" not in message and "svc" not in caplog.text and SECRET not in caplog.text
        assert "ValueError" in caplog.text


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

    # --- DSYNC-115: every account a registration granted access to is revoked on Remove ---------
    def _recorded(self, user: str, kind: str) -> None:
        headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)  # a full record
        cfg = AppConfig.load()
        cfg.schedule_run_as_user, cfg.schedule_run_as_kind = user, kind
        cfg.save()

    def _revoke_spy(self, monkeypatch, revoke=provision_session.RevokeOutcome.REVOKED) -> list[tuple]:
        from src.utils import paths

        monkeypatch.setattr(paths, "is_machine_scope", lambda: True)
        seen: list[tuple] = []

        def _revoke(task_name, *, user, kind):
            seen.append((task_name, user, kind))
            return provision_session.RemovalAttempt(ok=True, message="", revoke=revoke)

        monkeypatch.setattr(provision_session, "request_removal_with_revoke", _revoke)
        return seen

    @pytest.mark.parametrize("user", ["LAB\\svc_dsyncpw", "PC01\\svc_local"], ids=["domain", "local"])
    def test_a_password_account_on_shared_settings_is_removed_with_its_access(self, sched, monkeypatch, caplog, user):
        """Lab row T4: ``--principal password:LAB\\svc_dsyncpw`` then ``--schedule-remove`` left the account its ACEs."""
        self._recorded(user, "password")
        seen = self._revoke_spy(monkeypatch)
        with caplog.at_level(logging.DEBUG):
            code, message = headless.remove()
        assert code == 0 and "Removed" in message
        assert seen == [(AppConfig.load().schedule_task_name, user, PrincipalKind.PASSWORD)]
        assert sched.deleted == [], "ONE elevated op - no second removal"
        assert not AppConfig.load().schedule_registered
        account = user.split("\\")[1]
        assert account not in message and account not in caplog.text

    def test_the_signed_in_account_is_removed_the_ordinary_way(self, sched, monkeypatch):
        """It was granted nothing at registration, so there is nothing to revoke (and no prompt to show)."""
        self._recorded("", "interactive_token")
        seen = self._revoke_spy(monkeypatch)
        code, _ = headless.remove()
        assert code == 0 and seen == [] and sched.deleted

    def test_an_access_that_was_left_in_place_is_said(self, sched, monkeypatch):
        self._recorded("LAB\\svc_dsyncpw", "password")
        self._revoke_spy(monkeypatch, revoke=provision_session.RevokeOutcome.NOT_REVOKED)
        code, message = headless.remove()
        assert code == 0 and "could not be removed" in message and "svc_dsyncpw" not in message

    def test_a_switch_revokes_the_old_account_before_the_new_one_is_scheduled(self, sched, monkeypatch):
        """The gate makes a switch Remove-then-Schedule; the Remove is what revokes A (B's grant comes with its
        provision, which also retires any grant still recorded for A - pinned in tests/test_provisioning.py)."""
        self._recorded("LAB\\svc_a", "password")
        seen = self._revoke_spy(monkeypatch)
        code, message = headless.register(
            run_time="02:30", spec=_spec("password:LAB\\svc_b"), password=SECRET, wake_to_run=False
        )
        assert code == 1 and message == headless.GATE_COPY[RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE]
        assert headless.remove()[0] == 0
        assert [call[1] for call in seen] == ["LAB\\svc_a"]


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
            # argparse accepts abbreviations: the check is on parsed values, never spellings
            ["--sis", "myedbc", "--input", "x", "--wak"],
            ["--schedule-register", "--time", "02:00", "--sftp-h", "x"],
            ["--schedule-register", "--time", "02:00", "--dry"],
            ["--schedule-remove", "--updates", "off"],
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


class TestThePasswordRoutes:
    def _cli(self, argv):
        from src.main import cli

        return cli(argv)

    ARGV = ["--schedule-register", "--time", "03:00", "--principal", "password:CORP\\svc"]

    def test_the_environment_variable_is_removed_once_read(self, sched, monkeypatch):
        import os

        monkeypatch.setattr(headless, "register", lambda **kw: (0, "ok"))
        monkeypatch.setenv("DISTRICTSYNC_TASK_PASSWORD", SECRET)
        assert self._cli(self.ARGV) == 0
        assert "DISTRICTSYNC_TASK_PASSWORD" not in os.environ  # no child this process starts inherits it

    def test_no_console_is_exit_2_never_a_hang(self, sched, monkeypatch):
        import src.main as main_mod

        monkeypatch.delenv("DISTRICTSYNC_TASK_PASSWORD", raising=False)
        monkeypatch.setattr(main_mod, "_has_console", lambda: False)
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: pytest.fail("prompted with no console"))
        assert self._cli(self.ARGV) == 2

    def test_positive_twin_a_console_is_prompted(self, sched, monkeypatch):
        import src.main as main_mod

        seen = {}
        monkeypatch.delenv("DISTRICTSYNC_TASK_PASSWORD", raising=False)
        monkeypatch.setattr(main_mod, "_has_console", lambda: True)
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: SECRET)
        monkeypatch.setattr(headless, "register", lambda **kw: (seen.update(kw), (0, "ok"))[1])
        assert self._cli(self.ARGV) == 0 and seen["password"] == SECRET

    def test_an_incomplete_setup_never_asks_for_the_password(self, sched, monkeypatch):
        import src.main as main_mod

        AppConfig(input_dir="", output_dir="", sis_type="").save()
        monkeypatch.delenv("DISTRICTSYNC_TASK_PASSWORD", raising=False)
        monkeypatch.setattr(main_mod, "_has_console", lambda: True)
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: pytest.fail("asked before the settings were checked"))
        assert self._cli(self.ARGV) == 1
