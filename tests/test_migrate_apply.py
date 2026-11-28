"""Plan 0055 D1b (DSYNC-116): ``--migrate --apply`` moves the live nightly to the machine-wide install.

Plan section 5, step by step: find (D1a's discovery), require the shared profile (``--machine-setup`` first), make sure
delivery's settings and password are in the machine store and TEST them before the nightly is touched, then move the
nightly in ONE elevated child: probe the new program as the nightly's account (the DSYNC-33 check), delete the old
task, confirm it is MISSING, register ``DistrictSync_Daily`` with the same facts and this program, and re-register
the old one from the facts already read when that fails. Never two nightlies, never a temporary task.

Everything OS-shaped is a fake: Task Scheduler (one dict keyed by task name, so "two nightlies" would have to be two
names), the schedule check, the delivery test, the elevated child (the REAL ``elevated_apply._apply`` runs in-process
against a plain-JSON "seal") and the per-user uninstaller.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.scheduler import (
    elevated_apply,
    elevation,
    migration,
    migration_apply,
    migration_move,
    provisioning,
    task_com,
    windows,
)
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.scheduler.schedule_check import CheckVerdict, ScheduleCheck
from src.scheduler.task_com import PrincipalKind, TaskComError, TaskFacts

NAME = "DistrictSync_Daily"
SECRET = "Tr0ub4dor&3-never-printed"
ACCOUNT = "CORP\\svc_zebradon"  # distinctive, so a scan for it means something
ADMIN = "CORP\\admin_quillfeather"
OK_CHECK = ScheduleCheck(verdict=CheckVerdict.OK, code=81)
BAD_CHECK = ScheduleCheck(verdict=CheckVerdict.LOGON_FAILURE, code=task_com.RESULT_LOGON_FAILURE)
NOON = datetime(2026, 11, 28, 12, 0)
FAR = 4_000_000_000.0  # a deadline far in the future (epoch seconds)


def CLOCK() -> float:  # noqa: N802 - the child's fixed clock
    return 1_000_000_000.0


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class Killed(BaseException):  # noqa: N818 - a simulated process kill, not an error the code may catch
    """The process was killed here: nothing after this line runs, no ``except Exception`` sees it."""


class FakeTaskScheduler:
    """The whole of Task Scheduler for these tests: ONE dict keyed by task name (so a second nightly would need a second
    name, which the "no temporary task" test watches for)."""

    def __init__(self) -> None:
        self.tasks: dict[str, TaskFacts] = {}
        self.events: list[tuple[str, str]] = []
        self.registered: list[task_com.RegisterParams] = []
        self.names_touched: set[str] = set()
        self.granted: set[str] = set()

    def facts(self, name: str) -> TaskFacts:
        self.names_touched.add(name)
        if name not in self.tasks:
            raise TaskComError(task_com.HR_NOT_FOUND, task_com.MSG_NOT_FOUND)
        return self.tasks[name]


class FakeOps:
    """The child's ``NightlyOps``: probe / delete / program / register against the fake Task Scheduler."""

    def __init__(
        self,
        sched: FakeTaskScheduler,
        *,
        probe: ScheduleCheck = OK_CHECK,
        fail: frozenset[str] = frozenset(),
        kill_at: str | None = None,
    ) -> None:
        self.sched, self._probe, self.fail, self.kill_at = sched, probe, fail, kill_at

    def _step(self, step: str) -> None:
        self.sched.events.append((step, ""))
        if self.kill_at == step:
            raise Killed(step)

    def grant(self, user: str, kind: PrincipalKind) -> bool:
        """Review H2: the principal's access to the shared profile, granted before the probe; True = new grant."""
        self.sched.events.append(("grant", ""))
        if "grant" in self.fail:
            raise RuntimeError("icacls refused")
        if self.kill_at == "grant":
            raise Killed("grant")
        new = user not in self.sched.granted
        self.sched.granted.add(user)
        return new

    def revoke(self, user: str, kind: PrincipalKind) -> None:
        self.sched.events.append(("revoke", ""))
        if "revoke" in self.fail:
            raise RuntimeError("icacls refused")
        self.sched.granted.discard(user)

    def probe(self, payload) -> ScheduleCheck:
        self.sched.events.append(("probe", str(payload.get("exe"))))
        if self.kill_at == "probe":
            raise Killed("probe")
        return self._probe

    def delete(self, task_name: str) -> None:
        self.sched.names_touched.add(task_name)
        if "delete_timeout" in self.fail:  # review M4: the bounded worker gave up; the delete may still land
            self.sched.events.append(("delete", "timeout"))
            raise task_com.BoundedTimeout("delete")
        if "delete" in self.fail:
            self.sched.events.append(("delete", "refused"))
            raise TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)
        self.sched.tasks.pop(task_name, None)
        self._step("delete")

    def program(self, task_name: str) -> str | None:
        self.sched.names_touched.add(task_name)
        if "read" in self.fail:
            raise TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)
        facts = self.sched.tasks.get(task_name)
        return facts.action_path if facts is not None else None

    def register(self, params: task_com.RegisterParams) -> None:
        self.sched.names_touched.add(params.task_name)
        which = "register_old" if params.exe == self.sched.old_exe else "register_new"  # type: ignore[attr-defined]
        if which in self.fail:
            self.sched.events.append((which, "refused"))
            raise TaskComError(task_com.HR_LOGON_FAILURE, task_com.MSG_LOGON_FAILURE)
        self.sched.registered.append(params)
        self.sched.tasks[params.task_name] = TaskFacts(
            next_run=None,
            last_run=None,
            last_result=None,
            action_path=params.exe,
            run_as=params.user,
            logon_type=task_com.logon_type_for(params.kind),
            wake_to_run=params.wake_to_run,
            arguments=params.arguments,
            working_dir=params.working_dir,
            run_time=params.run_time,
            state=task_com.TASK_STATE_READY,
            enabled=True,
        )
        self._step(which)


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """A machine-wide install, set up, with an old one-file nightly running as a password service account."""
    new_exe = tmp_path / "Program Files" / "myBlueprint.DistrictSync" / "current" / "DistrictSync.exe"
    new_exe.parent.mkdir(parents=True)
    new_exe.write_bytes(b"")
    old_exe = tmp_path / "Tools" / "DistrictSync.exe"
    old_exe.parent.mkdir(parents=True)
    old_exe.write_bytes(b"")
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()

    sched = FakeTaskScheduler()
    sched.old_exe = str(old_exe)  # type: ignore[attr-defined]
    old_args = f'--sis myedbc --input "{tmp_path / "in"}" --output "{tmp_path / "out"}" --sftp --source scheduled'
    sched.tasks[NAME] = TaskFacts(
        next_run=None,
        last_run=None,
        last_result=0,
        action_path=str(old_exe),
        run_as=ACCOUNT,
        logon_type=task_com.TASK_LOGON_PASSWORD,
        wake_to_run=True,
        arguments=old_args,
        working_dir=str(old_exe.parent),
        run_time="02:00",
        state=task_com.TASK_STATE_READY,
        enabled=True,
    )

    state = {
        "ops": FakeOps(sched),
        "elevation": ElevationResult.COMPLETED,
        "delivery": (True, "Connection to sftp.example.invalid:22 successful."),
        "uninstalled": [],
        "child_args": [],
        "payloads": [],
        "elevated": 0,
    }

    monkeypatch.setattr(
        migration,
        "discover",
        lambda task_name=None: migration.Findings(
            machine_wide=True,
            nightly_found=True,
            nightly_program=str(old_exe),
            nightly_uses_this_copy=False,
            per_user_copy=False,
            shared_profile=True,
            delivery_in_machine_store=True,
        ),
    )
    monkeypatch.setattr(migration, "_this_program", lambda: new_exe.resolve())
    monkeypatch.setattr(
        migration_apply, "_new_program", lambda: new_exe
    )  # review L3: unresolved, like --schedule-register
    monkeypatch.setattr(migration, "_delivery_in_machine_store", lambda settings: True)
    monkeypatch.setattr(migration_apply, "_in_machine_scope", lambda: True)
    monkeypatch.setattr(migration_apply, "_current_account", lambda: ADMIN)
    monkeypatch.setattr(migration_apply, "_read_task", sched.facts)
    monkeypatch.setattr(migration_apply, "_test_delivery", lambda cfg: state["delivery"])
    monkeypatch.setattr(migration_apply, "_own_update_exe", lambda: None)
    monkeypatch.setattr(migration_apply, "_uninstall", lambda exe: state["uninstalled"].append(exe) or 0)
    monkeypatch.setattr(migration_move, "_live_ops", lambda: state["ops"])
    monkeypatch.setattr(migration_move, "_clock", lambda: 0.0)  # always before the parent's deadline
    # The child (a real elevated_apply) checks an interactive-token registration against the signed-in account.
    monkeypatch.setattr(windows, "current_run_as_user", lambda: ADMIN)

    # The REAL elevation round trip, minus the OS: the "seal" is plain JSON, and the "elevated child" is the real
    # elevated_apply._apply run in this process.
    monkeypatch.setattr(elevation, "protect_blob", lambda data: data)
    monkeypatch.setattr(elevation, "unprotect_blob", lambda data: data)
    monkeypatch.setattr(elevation, "_set_owner_only_dacl", lambda path: None)
    real_write_request = elevation.write_request

    def write_request(payload):
        state["payloads"].append(dict(payload))
        return real_write_request(payload)

    monkeypatch.setattr(elevation, "write_request", write_request)

    def run_child(req_path, res_path, *, timeout_s=None):
        state["child_args"].append((str(req_path), str(res_path), timeout_s))
        state["elevated"] += 1
        if state["elevation"] is not ElevationResult.COMPLETED:
            return ElevationOutcome(result=state["elevation"])
        elevated_apply._apply(Path(req_path), Path(res_path))
        return ElevationOutcome(result=ElevationResult.COMPLETED, exit_code=0)

    monkeypatch.setattr(windows, "run_elevated_child", run_child)

    AppConfig(
        input_dir=str(tmp_path / "in"),
        output_dir=str(tmp_path / "out"),
        sis_type="myedbc",
        sftp_enabled=True,
        sftp_host="sftp.ca.spacesedu.com",
        sftp_username="district",
        sftp_remote_path="/upload",
    ).save()
    return {"sched": sched, "state": state, "new": str(new_exe.resolve()), "old": str(old_exe), "tmp": tmp_path}


class Collect:
    """The routes ``main`` supplies: the task password (env / stdin / prompt) and the delivery setup."""

    def __init__(self, password: str = SECRET, delivery: str | None = None) -> None:
        self.password, self.delivery_error = password, delivery
        self.asked_password: list[str] = []
        self.delivery_calls = 0

    def task_password(self, account: str) -> str:
        self.asked_password.append(account)
        return self.password

    def prepare_delivery(self) -> str | None:
        self.delivery_calls += 1
        return self.delivery_error

    def save_delivery(self) -> None:
        self.saved = True


def _run(*, declared=None, collect=None, flags=False, now=NOON) -> migration_apply.ApplyResult:
    from src.scheduler.headless import parse_principal

    spec = parse_principal(declared) if isinstance(declared, str) else declared
    return migration_apply.run(declared=spec, collect=collect or Collect(), delivery_flags_given=flags, now=lambda: now)


def _with_record(kind: PrincipalKind = PrincipalKind.PASSWORD, account: str = ACCOUNT) -> None:
    from src.scheduler.schedule_record import TaskArgs, write_registered_record

    cfg = AppConfig.load()
    write_registered_record(
        cfg,
        run_time="02:00",
        args=TaskArgs.of(
            input_dir=cfg.input_dir, output_dir=cfg.output_dir, sis_type="myedbc", sftp_enabled=True, run_time="02:00"
        ),
        account=account,
        kind=kind,
        wake_to_run=True,
    )


def _text(result: migration_apply.ApplyResult) -> str:
    return "\n".join(result.lines)


# --------------------------------------------------------------------------- #
# The happy path: delete + register                                           #
# --------------------------------------------------------------------------- #


class TestTheMove:
    def test_delete_then_register_moves_the_nightly_with_the_same_facts(self, world):
        _with_record()
        result = _run()
        assert result.code == 0, _text(result)
        sched = world["sched"]
        task = sched.tasks[NAME]
        assert task.action_path == world["new"]  # the installed copy
        assert task.run_as == ACCOUNT and task.logon_type == task_com.TASK_LOGON_PASSWORD  # same principal and kind
        assert task.run_time == "02:00" and task.wake_to_run is True  # same time and wake
        assert '--sis myedbc --input "' in task.arguments and "--sftp" in task.arguments  # same district and folders
        assert "--source scheduled" in task.arguments
        assert task.working_dir == str(Path(world["new"]).parent)
        # The ORDER is the safety property: probe the new program, delete, confirm missing, register.
        steps = [step for step, _ in sched.events]
        assert steps == ["grant", "probe", "delete", "register_new"]
        assert [p.exe for p in sched.registered] == [world["new"]]
        assert any(line.startswith("Moved: ") for line in result.lines)

    def test_the_probe_runs_against_the_new_program_as_the_principal(self, world):
        _with_record()
        _run()
        probe = [detail for step, detail in world["sched"].events if step == "probe"]
        assert probe == [world["new"]]
        payload = world["state"]["payloads"][0]
        assert payload["exe"] == world["new"] and payload["user"] == ACCOUNT
        assert payload["kind"] == PrincipalKind.PASSWORD.value

    def test_the_record_describes_the_moved_nightly(self, world):
        _with_record()
        _run()
        cfg = AppConfig.load()
        assert cfg.schedule_registered is True
        assert cfg.schedule_run_as_user == ACCOUNT  # config.json is the declared N1 exception
        assert cfg.schedule_run_as_kind == PrincipalKind.PASSWORD.value
        assert cfg.schedule_time == "02:00" and cfg.schedule_wake_to_run is True

    def test_one_permission_prompt_for_the_whole_move(self, world):
        _with_record()
        _run()
        assert world["state"]["elevated"] == 1

    def test_the_parent_waits_long_enough_for_every_bounded_step_of_the_child(self, world):
        """The child is KILLED at the parent's bound and runs no ``finally``; the wait must cover the worst case."""
        _with_record()
        _run()
        (_req, _res, timeout_s) = world["state"]["child_args"][0]
        steps = (
            migration_move.schedule_check.TOTAL_BUDGET_S
            + task_com.DELETE_TIMEOUT_S
            + task_com.READ_TIMEOUT_S
            + 2 * task_com.REGISTER_TIMEOUT_S
            + 2 * task_com.READ_TIMEOUT_S
            + 4 * provisioning.ICACLS_TIMEOUT_S  # grant x2 and take-back x2 (second review, LOW 3)
        )
        assert timeout_s == migration_move.CHILD_BUDGET_S
        assert steps < migration_move.CHILD_BUDGET_S

    def test_a_nightly_already_on_this_copy_is_not_touched_and_is_success(self, world):
        world["sched"].tasks[NAME] = TaskFacts(
            **{**world["sched"].tasks[NAME].__dict__, "action_path": world["new"]},
        )
        result = _run()
        assert result.code == 0, _text(result)
        assert world["state"]["elevated"] == 0  # positive twin of the move: no prompt at all
        assert "already runs this copy" in _text(result)


# --------------------------------------------------------------------------- #
# Refusals before anything changes                                           #
# --------------------------------------------------------------------------- #


class TestRefusedBeforeAnyChange:
    def _untouched(self, world) -> None:
        assert world["sched"].tasks[NAME].action_path == world["old"]
        assert world["state"]["elevated"] == 0

    @pytest.mark.parametrize("clock", ["01:31", "02:00", "02:29", "01:45"])
    def test_within_30_minutes_of_the_run_time_is_refused(self, world, clock):
        _with_record()
        hour, minute = (int(x) for x in clock.split(":"))
        result = _run(now=datetime(2026, 11, 28, hour, minute))
        assert result.code == 1
        assert "within 30 minutes" in _text(result)
        self._untouched(world)

    @pytest.mark.parametrize("clock", ["01:30", "02:30", "12:00"])
    def test_positive_twin_30_minutes_away_is_allowed(self, world, clock):
        _with_record()
        hour, minute = (int(x) for x in clock.split(":"))
        result = _run(now=datetime(2026, 11, 28, hour, minute))
        assert result.code == 0, _text(result)

    def test_the_window_wraps_midnight(self):
        assert migration_apply.near_run_time("23:50", datetime(2026, 11, 28, 0, 10))
        assert migration_apply.near_run_time("00:10", datetime(2026, 11, 28, 23, 50))
        assert not migration_apply.near_run_time("00:10", datetime(2026, 11, 28, 23, 40))  # exactly 30: allowed

    def test_a_failed_delivery_test_leaves_the_nightly_untouched(self, world):
        _with_record()
        world["state"]["delivery"] = (False, "Authentication failed.")
        result = _run()
        assert result.code == 1
        assert "delivery test failed" in _text(result).lower()
        self._untouched(world)

    def test_positive_twin_a_passed_delivery_test_lets_it_move(self, world):
        _with_record()
        result = _run()
        assert result.code == 0 and "delivery test passed" in _text(result).lower()

    def test_delivery_missing_from_the_machine_store_is_asked_for_then_tested(self, world, monkeypatch):
        _with_record()
        monkeypatch.setattr(migration, "_delivery_in_machine_store", lambda settings: False)
        collect = Collect()
        result = _run(collect=collect)
        assert collect.delivery_calls == 1
        assert result.code == 0, _text(result)

    def test_delivery_that_cannot_be_set_up_is_refused_before_the_nightly(self, world, monkeypatch):
        _with_record()
        monkeypatch.setattr(migration, "_delivery_in_machine_store", lambda settings: False)
        result = _run(collect=Collect(delivery="Delivery's settings are not in the shared settings."))
        assert result.code == 1
        self._untouched(world)

    def test_delivery_off_skips_the_test_and_says_so(self, world):
        _with_record()
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(
            **{**facts.__dict__, "arguments": facts.arguments.replace(" --sftp", "")}
        )
        world["state"]["delivery"] = (False, "would fail")
        result = _run()
        assert result.code == 0, _text(result)
        assert "Delivery is off" in _text(result)

    def test_no_nightly_is_reported_and_is_not_success(self, world):
        world["sched"].tasks.clear()
        result = _run()
        assert result.code == 1
        assert "No nightly is scheduled" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_an_unreadable_nightly_is_never_called_absent(self, world, monkeypatch):
        def denied(name):
            raise TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)

        monkeypatch.setattr(migration_apply, "_read_task", denied)
        result = _run()
        assert result.code == 1
        assert "could not be read" in _text(result)
        assert "No nightly is scheduled" not in _text(result)

    def test_not_the_machine_wide_copy(self, world, monkeypatch):
        monkeypatch.setattr(
            migration,
            "discover",
            lambda task_name=None: migration.Findings(False, True, world["old"], False, False, False, None),
        )
        result = _run()
        assert result.code == 1 and "not the machine-wide" in _text(result)
        self._untouched(world)

    def test_the_shared_profile_must_be_set_up_first(self, world, monkeypatch):
        monkeypatch.setattr(migration_apply, "_in_machine_scope", lambda: False)
        result = _run()
        assert result.code == 1 and "--machine-setup" in _text(result)
        self._untouched(world)

    def test_a_password_principal_password_is_collected_before_the_prompt(self, world):
        _with_record()
        collect = Collect()
        _run(collect=collect)
        assert collect.asked_password == [ACCOUNT]
        assert world["state"]["payloads"][0]["password"] == SECRET

    def test_a_managed_service_account_needs_no_password(self, world):
        gmsa = "CORP\\gmsa_zebradon$"
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(**{**facts.__dict__, "run_as": gmsa})
        _with_record(PrincipalKind.MANAGED_SERVICE_ACCOUNT, gmsa)
        collect = Collect()
        result = _run(collect=collect)
        assert result.code == 0, _text(result)
        assert collect.asked_password == []
        assert "password" not in world["state"]["payloads"][0]


# --------------------------------------------------------------------------- #
# Discovery cases                                                             #
# --------------------------------------------------------------------------- #


class TestDiscoveryCases:
    def test_one_file_exe_is_reported_never_deleted(self, world):
        _with_record()
        result = _run()
        assert result.code == 0
        assert Path(world["old"]).is_file()  # never deleted
        assert any(line.startswith("Left: ") and "one-file" in line for line in result.lines)
        assert world["state"]["uninstalled"] == []

    def test_a_per_user_install_of_this_account_is_uninstalled_as_this_account(self, world, monkeypatch):
        _with_record()
        own = world["tmp"] / "LocalAppData" / "myBlueprint.DistrictSync" / "Update.exe"
        own.parent.mkdir(parents=True)
        own.write_bytes(b"")
        monkeypatch.setattr(migration_apply, "_own_update_exe", lambda: own)
        result = _run()
        assert result.code == 0
        assert world["state"]["uninstalled"] == [own]
        assert any(line.startswith("Moved: ") and "removed" in line for line in result.lines)

    def test_a_per_user_install_is_not_removed_while_the_old_nightly_still_needs_it(self, world, monkeypatch):
        _with_record()
        own = world["tmp"] / "LocalAppData" / "myBlueprint.DistrictSync" / "Update.exe"
        monkeypatch.setattr(migration_apply, "_own_update_exe", lambda: own)
        world["state"]["ops"] = FakeOps(world["sched"], probe=BAD_CHECK)
        result = _run()
        assert result.code == 1
        assert world["state"]["uninstalled"] == []

    def test_another_accounts_per_user_install_gets_the_one_command(self, world, monkeypatch):
        """The signed-in administrator's nightly ran a copy installed under another account's profile. (Run by a
        DIFFERENT account, that copy could not be put back: review M3 refuses it.)"""
        monkeypatch.setattr(migration_apply, "_old_is_per_user_install", lambda program: True)
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(
            **{**facts.__dict__, "run_as": ADMIN, "logon_type": task_com.TASK_LOGON_INTERACTIVE_TOKEN}
        )
        result = _run()
        assert result.code == 0
        assert world["state"]["uninstalled"] == []
        assert 'Update.exe" uninstall --silent' in _text(result)
        assert "%LOCALAPPDATA%" in _text(result)

    def test_a_raw_schtasks_nightly_with_no_record_needs_its_kind_declared(self, world):
        """No ``AppConfig.schedule_*`` record: a password logon could be a password account or a gMSA, and the kind is
        DECLARED, never inferred from a ``$`` (0049 S-4)."""
        result = _run()
        assert result.code == 1
        assert "--principal" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_positive_twin_a_raw_schtasks_nightly_moves_once_declared(self, world):
        result = _run(declared=f"password:{ACCOUNT}")
        assert result.code == 0, _text(result)
        assert world["sched"].tasks[NAME].action_path == world["new"]

    def test_a_declared_account_must_be_the_nightlys_own(self, world):
        result = _run(declared="password:CORP\\someone_else")
        assert result.code == 1
        assert world["state"]["elevated"] == 0

    def test_a_signed_in_nightly_moves_as_the_signed_in_account(self, world):
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(
            **{**facts.__dict__, "run_as": ADMIN, "logon_type": task_com.TASK_LOGON_INTERACTIVE_TOKEN}
        )
        collect = Collect()
        result = _run(collect=collect)  # a raw task: the logon type itself says "signed-in account"
        assert result.code == 0, _text(result)
        assert collect.asked_password == []
        assert world["sched"].tasks[NAME].logon_type == task_com.TASK_LOGON_INTERACTIVE_TOKEN

    def test_another_accounts_signed_in_nightly_is_refused(self, world):
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(
            **{**facts.__dict__, "run_as": "CORP\\other", "logon_type": task_com.TASK_LOGON_INTERACTIVE_TOKEN}
        )
        result = _run()
        assert result.code == 1
        assert world["state"]["elevated"] == 0

    def test_a_command_line_this_app_never_writes_is_refused(self, world):
        _with_record()
        facts = world["sched"].tasks[NAME]
        world["sched"].tasks[NAME] = TaskFacts(**{**facts.__dict__, "arguments": facts.arguments + " --quality"})
        result = _run()
        assert result.code == 1
        assert world["state"]["elevated"] == 0


class TestParseArguments:
    def test_the_apps_own_command_line_parses(self):
        cmd = migration_apply.parse_arguments(
            '--sis sd48myedbc --input "C:\\GDE in" --output "C:\\out" --sftp --source scheduled'
        )
        assert cmd == migration_apply.NightlyCommand("sd48myedbc", "C:\\GDE in", "C:\\out", True)

    def test_an_older_command_line_without_source_parses(self):
        cmd = migration_apply.parse_arguments('--sis myedbc --input "C:\\in" --output "C:\\out"')
        assert cmd == migration_apply.NightlyCommand("myedbc", "C:\\in", "C:\\out", False)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            '--input "C:\\in" --output "C:\\out"',  # no district
            '--sis myedbc --input "C:\\in" --output "C:\\out" --dry-run',  # a flag the nightly never carries
            '--sis myedbc --input "C:\\in" --output "C:\\out" --source manual',
            '--sis "../x" --input "C:\\in" --output "C:\\out"',  # not a district name
        ],
    )
    def test_anything_else_is_not_ours(self, text):
        assert migration_apply.parse_arguments(text) is None


# --------------------------------------------------------------------------- #
# The never-lose-the-nightly rule                                             #
# --------------------------------------------------------------------------- #


class TestNeverLoseTheNightly:
    def test_a_failed_probe_leaves_the_nightly_untouched(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], probe=BAD_CHECK)
        result = _run()
        assert result.code == 1
        assert world["sched"].tasks[NAME].action_path == world["old"]
        # nothing after the probe but taking back the grant made for it (review H2)
        assert [step for step, _ in world["sched"].events] == ["grant", "probe", "revoke"]
        assert "logon_failure" in _text(result).lower() or "LOGON_FAILURE" in _text(result)

    def test_an_inconclusive_probe_is_not_a_pass(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], probe=ScheduleCheck(verdict=CheckVerdict.TIMEOUT))
        result = _run()
        assert result.code == 1
        assert world["sched"].tasks[NAME].action_path == world["old"]

    def test_a_refused_delete_changes_nothing(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"delete"}))
        result = _run()
        assert result.code == 1
        assert world["sched"].tasks[NAME].action_path == world["old"]
        assert world["sched"].registered == []

    def test_a_failed_register_re_registers_the_old_task_and_says_so(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new"}))
        result = _run()
        assert result.code == 1
        task = world["sched"].tasks[NAME]
        assert task.action_path == world["old"]  # the old nightly is back
        assert task.arguments == (
            f'--sis myedbc --input "{world["tmp"] / "in"}" --output "{world["tmp"] / "out"}" --sftp --source scheduled'
        )  # verbatim, from the facts read BEFORE the delete
        assert task.run_as == ACCOUNT and task.run_time == "02:00" and task.wake_to_run is True
        assert [p.exe for p in world["sched"].registered] == [world["old"]]
        assert world["sched"].registered[0].password == SECRET  # the password already in hand
        assert "registered again" in _text(result) and "run level" in _text(result)  # review L2

    def test_a_failed_register_and_a_failed_rollback_say_there_is_no_nightly(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new", "register_old"}))
        result = _run()
        assert result.code == 1
        assert NAME not in world["sched"].tasks
        text = _text(result)
        assert "NO nightly" in text
        assert "Way back: " in text and "02:00" in text

    def test_an_unconfirmable_delete_puts_the_old_task_back(self, world):
        """The delete answered but MISSING could not be confirmed: register the OLD facts (same name, so it can only
        replace, never add), never the new ones."""
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"read"}))
        result = _run()
        assert result.code == 1
        assert world["sched"].tasks[NAME].action_path == world["old"]
        assert [p.exe for p in world["sched"].registered] == [world["old"]]

    def test_no_temporary_task_ever_exists(self, world):
        _with_record()
        _run()
        assert world["sched"].names_touched == {NAME}

    @pytest.mark.parametrize("kill_at", ["grant", "probe", "delete", "register_new"])
    def test_interrupted_at_each_step_leaves_one_nightly_or_a_state_the_report_names(self, world, kill_at):
        """T7c: the child is killed after each step. Afterwards there is exactly one ``DistrictSync_Daily`` (old or
        new) or none, and when there is none the parent's report (the child wrote no result) names it."""
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], kill_at=kill_at)

        real_apply = elevated_apply._apply

        def killed_apply(req, res):
            try:
                return real_apply(req, res)
            except Killed:
                return 0  # the process died: no result file

        import unittest.mock as um

        with um.patch.object(elevated_apply, "_apply", killed_apply):
            result = _run()
        program = world["sched"].tasks.get(NAME)
        assert len([n for n in world["sched"].tasks if n == NAME]) <= 1
        text = _text(result)
        if program is None:
            assert "NO nightly" in text and "Way back: " in text
            assert result.code == 1
        elif program.action_path == world["new"]:
            assert result.code == 0
        else:
            assert program.action_path == world["old"]
            assert result.code == 1
        expected = {"grant": world["old"], "probe": world["old"], "delete": None, "register_new": world["new"]}[kill_at]
        assert (program.action_path if program else None) == expected

    def test_a_timed_out_child_is_resolved_by_reading_the_task_back(self, world):
        _with_record()
        world["state"]["elevation"] = ElevationResult.TIMEOUT
        result = _run()
        assert result.code == 1
        assert "old nightly is still in place" in _text(result)

    @pytest.mark.parametrize(
        "outcome", [ElevationResult.DECLINED, ElevationResult.LAUNCH_FAILED], ids=["declined", "launch_failed"]
    )
    def test_no_prompt_answered_means_nothing_changed(self, world, outcome):
        _with_record()
        world["state"]["elevation"] = outcome
        result = _run()
        assert result.code == 1
        assert "nothing was changed" in _text(result)
        assert world["sched"].tasks[NAME].action_path == world["old"]


class TestTheSequenceInIsolation:
    """``move_nightly`` against the fake ops directly: the privileged half's own contract."""

    def _params(self, world, exe: str, args: str) -> task_com.RegisterParams:
        return task_com.RegisterParams(
            task_name=NAME,
            exe=exe,
            arguments=args,
            working_dir=str(Path(exe).parent),
            run_time="02:00",
            user=ACCOUNT,
            password=SECRET,
            kind=PrincipalKind.PASSWORD,
            run_highest=True,
            wake_to_run=True,
        )

    def test_the_outcomes(self, world):
        sched = world["sched"]
        new = self._params(world, world["new"], "--sis myedbc")
        old = self._params(world, world["old"], "--sis myedbc")
        out = migration_move.move_nightly(
            NAME, new=new, old=old, probe_payload={"exe": world["new"]}, ops=FakeOps(sched), deadline=FAR, clock=CLOCK
        )
        assert out.stage is migration_move.MoveStage.MOVED and out.check == OK_CHECK

    def test_a_register_that_raised_but_landed_is_moved(self, world):
        """A register that RAISED may still have completed: the read-back decides, never the raise. (A TIMED-OUT one
        is UNSETTLED instead, review M4: tests/test_migrate_apply_review.py.)"""
        sched = world["sched"]

        class LateOps(FakeOps):
            def register(self, params):
                super().register(params)
                raise TaskComError(None, task_com.MSG_OPERATION_FAILED)

        new = self._params(world, world["new"], "--sis myedbc")
        old = self._params(world, world["old"], "--sis myedbc")
        out = migration_move.move_nightly(
            NAME, new=new, old=old, probe_payload={}, ops=LateOps(sched), deadline=FAR, clock=CLOCK
        )
        assert out.stage is migration_move.MoveStage.MOVED
        assert sched.tasks[NAME].action_path == world["new"]


# --------------------------------------------------------------------------- #
# Secrets and names                                                           #
# --------------------------------------------------------------------------- #


class TestSecretsAndNames:
    def test_the_password_never_reaches_argv_the_report_or_a_log(self, world, caplog, capsys):
        _with_record()
        caplog.set_level(logging.DEBUG)
        result = _run()
        assert result.code == 0
        for req, res, _timeout in world["state"]["child_args"]:
            assert SECRET not in req and SECRET not in res  # argv carries only the handshake paths
        assert SECRET not in _text(result)
        assert SECRET not in caplog.text
        # positive twin: it DID travel, inside the sealed request
        assert world["state"]["payloads"][0]["password"] == SECRET

    def test_no_account_name_in_the_report_or_the_log(self, world, caplog):
        _with_record()
        caplog.set_level(logging.DEBUG)
        result = _run()
        for name in ("zebradon", "quillfeather"):
            assert name not in _text(result).lower()
            assert name not in caplog.text.lower()
        # positive twin: the report IS logged
        assert any("Moved: " in record.getMessage() for record in caplog.records)

    def test_the_failure_report_names_no_account_either(self, world, caplog):
        _with_record()
        caplog.set_level(logging.DEBUG)
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new", "register_old"}))
        result = _run()
        assert "zebradon" not in _text(result).lower() and "zebradon" not in caplog.text.lower()
        assert "password:<that account>" in _text(result)

    def test_the_child_result_carries_no_secret_and_no_account(self, world, monkeypatch):
        _with_record()
        written: list[dict] = []
        real = elevated_apply._write_result

        def spy(res_path, ok, message="", **kw):
            real(res_path, ok, message, **kw)
            written.append(json.loads(Path(res_path).read_text(encoding="utf-8")))

        monkeypatch.setattr(elevated_apply, "_write_result", spy)
        _run()
        assert written, "the spy saw no result"
        blob = json.dumps(written)
        assert SECRET not in blob and "zebradon" not in blob
        assert written[0]["message"] == migration_move.MoveStage.MOVED.value


# --------------------------------------------------------------------------- #
# The report                                                                  #
# --------------------------------------------------------------------------- #


class TestTheReport:
    SECTIONS = ("Found: ", "Moved: ", "Left: ", "Next: ", "Way back: ")

    def test_its_shape(self, world):
        _with_record()
        result = _run()
        assert result.lines[0] == migration_apply.HEADER
        body = result.lines[1:]
        assert body and all(line.startswith(self.SECTIONS) for line in body), body
        order = [next(i for i, s in enumerate(self.SECTIONS) if line.startswith(s)) for line in body]
        assert order == sorted(order), "sections out of order"
        assert any(line.startswith("Found: ") for line in body)
        assert any(line.startswith("Next: ") for line in body)

    def test_the_way_back_gives_the_old_facts(self, world):
        _with_record()
        text = _text(_run())
        way_back = next(line for line in text.splitlines() if line.startswith("Way back: "))
        assert "02:00" in way_back and "--wake" in way_back and "--schedule-register" in way_back


# --------------------------------------------------------------------------- #
# The command line                                                            #
# --------------------------------------------------------------------------- #


class TestTheCommand:
    @pytest.fixture()
    def cli_world(self, world, monkeypatch):
        import src.main as main_mod

        monkeypatch.setattr(main_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(main_mod, "_has_console", lambda: False)
        _with_record()
        return world

    def test_exit_0_when_moved_and_the_env_password_is_consumed(self, cli_world, monkeypatch, capsys, caplog):
        from src.main import TASK_PASSWORD_ENV_VAR, cli

        caplog.set_level(logging.DEBUG)
        monkeypatch.setenv(TASK_PASSWORD_ENV_VAR, SECRET)
        assert cli(["--migrate", "--apply"]) == 0
        out = capsys.readouterr().out
        assert "Moved: " in out and SECRET not in out and SECRET not in caplog.text
        assert TASK_PASSWORD_ENV_VAR not in os.environ  # popped: no child inherits it
        assert cli_world["state"]["payloads"][0]["password"] == SECRET

    def test_exit_1_when_not_moved(self, cli_world, monkeypatch):
        from src.main import TASK_PASSWORD_ENV_VAR, cli

        monkeypatch.setenv(TASK_PASSWORD_ENV_VAR, SECRET)
        cli_world["state"]["ops"] = FakeOps(cli_world["sched"], probe=BAD_CHECK)
        assert cli(["--migrate", "--apply"]) == 1

    @pytest.mark.parametrize(
        "argv",
        [
            ["--apply"],
            ["--migrate", "--apply", "--time", "02:00"],
            ["--migrate", "--apply", "--wake"],
            ["--migrate", "--apply", "--sis", "myedbc"],
            ["--migrate", "--apply", "--sftp-host", "sftp.ca.spacesedu.com"],
            ["--migrate", "--apply", "--task-password-stdin", "--sftp-password-stdin"],
            ["--migrate", "--principal", "current"],
            ["--migrate", "--apply", "--principal", "nonsense"],
        ],
    )
    def test_misuse_is_exit_2(self, cli_world, argv):
        from src.main import cli

        assert cli(argv) == 2
        assert cli_world["state"]["elevated"] == 0

    def test_a_machine_wide_install_not_set_up_is_refused_by_the_gate(self, cli_world, monkeypatch, capsys):
        from src.main import cli
        from src.scheduler import machine_setup

        monkeypatch.setattr(machine_setup, "needs_machine_setup", lambda: True)
        assert cli(["--migrate", "--apply"]) == 1
        assert machine_setup.NOT_SET_UP_CLI in capsys.readouterr().out
        assert cli_world["state"]["elevated"] == 0


class TestTheChildOp:
    def test_an_invalid_request_changes_nothing(self, world, tmp_path):
        """Validation runs BEFORE the probe: a malformed request is refused with nothing asked of Task Scheduler."""
        req = tmp_path / "x.req"
        res = tmp_path / "x.res"
        req.write_bytes(json.dumps({"op": migration_move.OP, "task_name": NAME}).encode())
        elevated_apply._apply(req, res)
        assert json.loads(res.read_text(encoding="utf-8"))["ok"] is False
        assert world["sched"].events == []

    def test_the_wire_form_is_built_in_one_place(self):
        """Every key the child reads is one the parent's builder writes (and no password key when there is none)."""
        payload = migration_move.build_payload(
            task_name=NAME,
            run_time="02:00",
            user=ACCOUNT,
            kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT,
            password=None,
            wake_to_run=False,
            exe="C:\\new.exe",
            arguments="--sis myedbc",
            working_dir="C:\\",
            old_exe="C:\\old.exe",
            old_arguments="--sis myedbc",
            old_working_dir="C:\\",
            deadline=0.0,
        )
        assert payload["op"] == migration_move.OP
        assert set(payload) >= migration_move.REQUIRED_FIELDS
        assert "password" not in payload


class TestModuleBoundaries:
    def test_the_report_only_module_still_calls_nothing_that_writes(self):
        """D1a's read-only pin still holds: the move lives in its own modules."""
        tree = ast.parse((Path(migration.__file__)).read_text(encoding="utf-8"))
        called = {
            getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert not called & {"register", "delete", "move_nightly", "run_elevated_child", "write_request"}

    def test_the_move_never_deletes_a_program_file(self):
        """Old copies are reported, never deleted: neither move module unlinks or removes a tree."""
        for module in (migration_apply, migration_move):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            called = {
                getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
            }
            assert not called & {"unlink", "rmtree", "remove", "rmdir"}, module.__name__


class TestThePartnerDocsQuoteTheApp:
    """The IT guide and the troubleshooting page quote the report's words; each quote is pinned to the source."""

    ROOT = Path(__file__).resolve().parent.parent

    def _doc(self, relative: str) -> str:
        return " ".join((self.ROOT / relative).read_text(encoding="utf-8").split())

    def test_the_troubleshooting_page_quotes_the_no_nightly_line_and_the_sections(self):
        text = self._doc("docs/partner/troubleshooting.md")
        quote = "There is NO nightly now"
        assert migration_apply.NO_NIGHTLY_NOW.startswith(quote) and f'"{quote}"' in text
        for section in ("Left:", "Way back:"):
            assert f"**{section}**" in text
            assert any(line.startswith(section) for line in migration_apply.Report(left=["x"], way_back=["y"]).lines())

    def test_the_it_guide_names_the_report_sections_the_app_prints(self):
        text = self._doc("docs/partner/it-deployment.md")
        assert "(Found, Moved, Left, Next, and a **Way back** line" in text
        lines = migration_apply.Report(found=["a"], moved=["b"], left=["c"], next=["d"], way_back=["e"]).lines()
        assert [line.split(":")[0] for line in lines[1:]] == ["Found", "Moved", "Left", "Next", "Way back"]
        assert f"at least {migration_apply.NEAR_RUN_MINUTES} minutes away" in text
