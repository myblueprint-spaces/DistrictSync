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
        assert code == 0
        assert any(line.startswith("Warning: the account's test run did not pass") for line in message.splitlines())
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


class TestTheBatchRight:
    """DSYNC-111: an unattended account must hold 'Log on as a batch job', or the nightly never runs.

    After a successful registration as a password or gMSA account the command prints the prerequisite lines and reads
    the right (the DSYNC-33 read, through ``batch_right.read_right``; the LSA is never touched). A positive NOT HELD
    keeps the task and exits ``EXIT_REGISTERED_WITHOUT_BATCH_RIGHT`` (4); anything it cannot see is an advisory, exit 0.
    """

    @pytest.fixture()
    def provision(self, monkeypatch):
        calls: dict = {"check": None, "reads": [], "outcome": provision_session.ProvisionOutcome.PROVISIONED}

        def request_provision(*args, **kwargs):
            calls["principal"] = kwargs["principal"]
            return provision_session.ProvisionAttempt(outcome=calls["outcome"], check=calls["check"])

        def complete_handover(*, persist, reenter):
            persist()
            return provision_session.HandoverOutcome(handed_over=True)

        monkeypatch.setattr(provision_session, "request_provision", request_provision)
        monkeypatch.setattr(provision_session, "complete_handover", complete_handover)
        return calls

    @pytest.fixture()
    def right(self, monkeypatch, provision):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        state = {"value": RightState.UNKNOWN}

        def read(account):
            provision["reads"].append(account)
            return state["value"]

        monkeypatch.setattr(batch_right, "read_right", read)
        return state

    def _password(self):
        return headless.register(
            run_time="03:00", spec=_spec("password:PC\\svc_local"), password=SECRET, wake_to_run=False
        )

    def _gmsa(self):
        return headless.register(
            run_time="03:00", spec=_spec("gmsa:CORP\\svc_dsync$"), password=None, wake_to_run=False
        )

    # --- password principal ----------------------------------------------------------------------------------------
    def test_password_not_held_exits_4_and_keeps_the_task(self, sched, provision, right):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.DENIED
        code, message = self._password()
        assert code == headless.EXIT_REGISTERED_WITHOUT_BATCH_RIGHT == 4
        assert "principal" in provision  # it WAS registered
        assert AppConfig.load().schedule_registered  # and the record kept: a later grant needs no re-run
        assert batch_right.NOT_HELD_DETAIL in message and batch_right.NOT_HELD_HEADLINE in message
        assert message.splitlines()[0].startswith("Scheduled:")

    def test_password_held_exits_0(self, sched, provision, right):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.GRANTED
        code, message = self._password()
        assert code == 0
        assert batch_right.HELD_LINE in message
        assert batch_right.NOT_HELD_DETAIL not in message and batch_right.INCONCLUSIVE_LINE not in message

    def test_password_through_a_group_exits_0_with_the_advisory(self, sched, provision, right):
        """The read sees only DIRECT rights: a member of a group holding the right reads NOT_DIRECT."""
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.NOT_DIRECT
        code, message = self._password()
        assert code == 0
        assert batch_right.INCONCLUSIVE_LINE in message and batch_right.NOT_HELD_DETAIL not in message

    def test_password_inconclusive_exits_0_with_the_advisory(self, sched, provision, right):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.UNKNOWN
        code, message = self._password()
        assert code == 0 and batch_right.INCONCLUSIVE_LINE in message

    def test_the_account_read_is_the_one_registered(self, sched, provision, right):
        self._password()
        assert provision["reads"] == ["PC\\svc_local"]

    def test_the_prerequisite_lines_are_printed_for_a_password_account(self, sched, provision, right):
        from src.scheduler import batch_right

        _, message = self._password()
        assert batch_right.PREREQUISITES_LEAD in message
        assert batch_right.BATCH_RIGHT_PREREQUISITE in message
        assert "Install-ADServiceAccount" not in message  # a gMSA line, not this account's

    def test_a_password_account_that_is_the_signed_in_one_is_checked_too(self, sched, right):
        """The signed-in account registered WITH its password is an unattended batch logon all the same."""
        from src.scheduler.schedule_check import RightState

        sched.account = "PC\\svc_local"
        right["value"] = RightState.DENIED
        code, _ = self._password()
        assert sched.registered and code == headless.EXIT_REGISTERED_WITHOUT_BATCH_RIGHT

    # --- gMSA principal --------------------------------------------------------------------------------------------
    def test_gmsa_not_held_exits_4(self, sched, provision, right):
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.DENIED
        code, _ = self._gmsa()
        assert code == 4 and AppConfig.load().schedule_registered

    def test_gmsa_windows_refused_the_logon_type_exits_4(self, sched, provision, right):
        from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck

        provision["check"] = ScheduleCheck(verdict=CheckVerdict.BATCH_LOGON, batch_right=RightState.NOT_DIRECT)
        code, _ = self._gmsa()
        assert code == 4

    def test_gmsa_held_exits_0(self, sched, provision, right):
        from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck

        provision["check"] = ScheduleCheck(verdict=CheckVerdict.OK, batch_right=RightState.GRANTED)
        code, _ = self._gmsa()
        assert code == 0

    def test_gmsa_through_a_group_exits_0(self, sched, provision, right):
        """NOT_DIRECT, and the check run started DistrictSync as the account: it holds the right through a group."""
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck

        provision["check"] = ScheduleCheck(verdict=CheckVerdict.OK, batch_right=RightState.NOT_DIRECT)
        code, message = self._gmsa()
        assert code == 0 and batch_right.NOT_HELD_DETAIL not in message

    def test_gmsa_inconclusive_exits_0_with_the_advisory(self, sched, provision, right):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck

        provision["check"] = ScheduleCheck(verdict=CheckVerdict.TIMEOUT, batch_right=RightState.NOT_DIRECT)
        code, message = self._gmsa()
        assert code == 0 and batch_right.INCONCLUSIVE_LINE in message

    def test_the_prerequisite_lines_are_printed_for_a_gmsa(self, sched, provision, right):
        from src.scheduler import batch_right

        _, message = self._gmsa()
        assert batch_right.PREREQUISITES_LEAD in message
        assert all(line in message for line in batch_right.GMSA_PREREQUISITES)

    # --- the signed-in account and the failure paths ---------------------------------------------------------------
    def test_the_signed_in_account_gets_no_check_and_no_lines(self, sched, provision, monkeypatch):
        from src.scheduler import batch_right

        monkeypatch.setattr(batch_right, "read_right", lambda account: pytest.fail("checked a signed-in task"))
        code, message = headless.register(run_time="02:30", spec=_spec("current"), password=None, wake_to_run=False)
        assert code == 0 and message.startswith("Scheduled:")
        assert batch_right.PREREQUISITES_LEAD not in message and batch_right.INCONCLUSIVE_LINE not in message

    def test_a_failed_registration_reads_nothing_and_stays_1(self, sched, provision, right):
        provision["outcome"] = provision_session.ProvisionOutcome.FAILED
        code, _ = self._password()
        assert code == 1 and provision["reads"] == []
        provision["outcome"] = provision_session.ProvisionOutcome.PROVISIONED  # positive twin: success reads it
        code, _ = self._password()
        assert code == 0 and provision["reads"] == ["PC\\svc_local"]

    def test_no_account_name_in_the_log(self, sched, provision, right, caplog):
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.DENIED
        with caplog.at_level(logging.DEBUG):
            self._password()
        assert "batch" in caplog.text.lower()  # positive twin: the outcome was logged
        assert "svc_local" not in caplog.text

    def test_the_command_line_returns_4(self, sched, provision, right, monkeypatch, capsys):
        from src.main import cli
        from src.scheduler.schedule_check import RightState

        right["value"] = RightState.DENIED
        monkeypatch.setenv("DISTRICTSYNC_TASK_PASSWORD", SECRET)
        argv = ["--schedule-register", "--time", "03:00", "--principal", "password:PC\\svc_local"]
        assert cli(argv) == 4
        assert "Log on as a batch job" in capsys.readouterr().out


class TestTheLabCaseLocalAccount:
    """DSYNC-111 round 2: the LAB case — ``password:<COMPUTER>\\svc_local``, a plain local account — must exit 4.

    The elevated child reads the local account's whole batch-logon token (``batch_right._read_right_live``) and hands the
    state back on ``ProvisionAttempt.batch_right``. Driven here through the real chain with only the two OS seams
    replaced: ``batch_right._local_facts`` (LSA + Net API) and ``schedule_check.read_batch_right`` (the direct read).
    """

    MACHINE = "S-1-5-21-1000-2000-3000"
    USER = f"{MACHINE}-1001"

    @pytest.fixture()
    def run(self, sched, monkeypatch):
        from src.scheduler import batch_right
        from src.scheduler.schedule_check import RightState

        facts: dict = {"value": None, "direct": RightState.NOT_DIRECT}
        monkeypatch.setattr(batch_right, "_local_facts", lambda account: facts["value"])
        monkeypatch.setattr("src.scheduler.schedule_check.read_batch_right", lambda account, **kw: facts["direct"])

        def request_provision(*args, **kwargs):
            # What the elevated child does: run the live read and carry the state back.
            state = batch_right._read_right_live(kwargs["principal"].user)
            return provision_session.ProvisionAttempt(
                outcome=provision_session.ProvisionOutcome.PROVISIONED, batch_right=state
            )

        def complete_handover(*, persist, reenter):
            persist()
            return provision_session.HandoverOutcome(handed_over=True)

        monkeypatch.setattr(provision_session, "request_provision", request_provision)
        monkeypatch.setattr(provision_session, "complete_handover", complete_handover)
        monkeypatch.setattr(batch_right, "read_right", lambda account: pytest.fail("the parent re-read the right"))
        return facts

    def _local(self, *, holders=(), denies=(), groups=("S-1-5-32-545",)):
        from src.scheduler import batch_right

        return batch_right.LocalFacts(
            account_sid=self.USER,
            user_group_sids=frozenset(groups),
            alias_members={},
            batch_holders=frozenset(holders),
            deny_holders=frozenset(denies),
        )

    def _register(self, account="PC\\svc_local"):
        return headless.register(
            run_time="02:00", spec=_spec(f"password:{account}"), password=SECRET, wake_to_run=False
        )

    def test_a_local_account_with_no_right_exits_4(self, run):
        from src.scheduler import batch_right

        run["value"] = self._local(holders=("S-1-5-32-544", "S-1-5-32-551", "S-1-5-32-559"))  # Windows' defaults
        code, message = self._register()
        assert code == 4 and AppConfig.load().schedule_registered
        assert batch_right.NOT_HELD_DETAIL in message

    def test_a_local_group_that_holds_it_exits_0(self, run):
        group = f"{self.MACHINE}-1010"
        run["value"] = self._local(holders=(group,), groups=("S-1-5-32-545", group))
        code, _ = self._register()
        assert code == 0

    def test_administrators_exits_0(self, run):
        run["value"] = self._local(holders=("S-1-5-32-544",), groups=("S-1-5-32-545", "S-1-5-32-544"))
        assert self._register()[0] == 0

    def test_everyone_exits_0(self, run):
        run["value"] = self._local(holders=("S-1-1-0",))
        assert self._register()[0] == 0

    def test_explicitly_denied_exits_4(self, run):
        run["value"] = self._local(holders=("S-1-1-0",), denies=(self.USER,))
        assert self._register()[0] == 4

    def test_a_domain_account_is_unchanged(self, run):
        """Not local (``_local_facts`` answers None): the direct read's NOT_DIRECT stays an advisory, exit 0."""
        from src.scheduler import batch_right

        run["value"] = None
        code, message = self._register("CORP\\svc")
        assert code == 0 and batch_right.INCONCLUSIVE_LINE in message


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
