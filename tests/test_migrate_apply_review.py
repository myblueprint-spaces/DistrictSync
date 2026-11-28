# ruff: noqa: F811 - ``world`` is a fixture imported from test_migrate_apply; pytest injects it by name
"""DSYNC-116 refuting review (CHANGES_REQUIRED): one class per finding, written before the fixes.

Reuses ``test_migrate_apply``'s fake Task Scheduler and its ``world`` fixture (the real ``elevated_apply._apply`` run
in-process against a plain-JSON "seal").
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock

import pytest

from src.config.app_config import AppConfig
from src.scheduler import migration, migration_apply, migration_move, task_com, windows
from src.scheduler.task_com import PrincipalKind, TaskFacts
from tests.test_migrate_apply import (  # noqa: F401 - ``world`` is a fixture
    ACCOUNT,
    ADMIN,
    BAD_CHECK,
    NAME,
    NOON,
    SECRET,
    Collect,
    FakeOps,
    _run,
    _text,
    _with_record,
    world,
)

SFTP_SECRET = "sftp-S3cret-never-inherited"


def _set(world, **changes) -> None:
    facts = world["sched"].tasks[NAME]
    world["sched"].tasks[NAME] = TaskFacts(**{**facts.__dict__, **changes})


# --------------------------------------------------------------------------- #
# H1 - Windows command-line rules, never POSIX shlex                          #
# --------------------------------------------------------------------------- #


class TestH1WindowsCommandLine:
    @pytest.mark.parametrize(
        "folder",
        [
            r"\\srv\share\in",  # UNC: POSIX shlex ate one leading backslash
            r"C:\My District Data\in",  # spaces
            "D:\\",  # a drive root: the closing quote follows a backslash
            r"C:\in\trailing" + "\\",  # a trailing backslash
            r'C:\odd"name',  # a quote inside the value
        ],
    )
    def test_quote_then_split_is_the_identity(self, folder):
        assert windows.split_command_line(f"--input {windows.quote_arg(folder)}") == ["--input", folder]

    @pytest.mark.parametrize(
        "folder",
        [r"\\srv\share\in", r"C:\My District Data\in", "D:\\", r"C:\GDE\in"],
    )
    def test_build_parse_build_gives_identical_text(self, folder):
        exe = Path("C:/DistrictSync/DistrictSync.exe")
        built, _ = windows._build_action_args(exe, "myedbc", Path(folder), Path(folder), True)
        command = migration_apply.parse_arguments(built)
        assert command is not None
        assert command.input_dir == str(Path(folder)) and command.output_dir == str(Path(folder))
        rebuilt, _ = windows._build_action_args(exe, "myedbc", Path(command.input_dir), Path(command.output_dir), True)
        assert rebuilt == built

    def test_an_unquoted_backslash_path_is_kept_literally(self):
        command = migration_apply.parse_arguments(r"--sis myedbc --input C:\GDE\in --output C:\GDE\out")
        assert command is not None
        assert command.input_dir == r"C:\GDE\in" and command.output_dir == r"C:\GDE\out"

    def test_the_split_follows_command_line_to_argv_w(self):
        # 2n backslashes + quote -> n backslashes and a quote mark; 2n+1 -> n and a literal quote
        assert windows.split_command_line(r'a\\"b c" d') == ["a\\b c", "d"]
        assert windows.split_command_line(r"a\\\"b c") == ['a\\"b', "c"]
        assert windows.split_command_line(r"a\b\\c") == [r"a\b\\c"]

    def test_the_folders_must_exist_before_anything_changes(self, world):
        _with_record()
        missing = world["tmp"] / "gone"
        args = world["sched"].tasks[NAME].arguments.replace(str(world["tmp"] / "in"), str(missing))
        _set(world, arguments=args)
        result = _run()
        assert result.code == 1
        assert "extract folder" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_the_output_folders_parent_must_exist(self, world):
        _with_record()
        args = (
            world["sched"].tasks[NAME].arguments.replace(str(world["tmp"] / "out"), str(world["tmp"] / "nope" / "out"))
        )
        _set(world, arguments=args)
        result = _run()
        assert result.code == 1 and world["state"]["elevated"] == 0

    def test_positive_twin_an_output_folder_not_created_yet_is_fine(self, world):
        _with_record()
        args = world["sched"].tasks[NAME].arguments.replace(str(world["tmp"] / "out"), str(world["tmp"] / "new-out"))
        _set(world, arguments=args)
        assert _run().code == 0


# --------------------------------------------------------------------------- #
# H2 - the principal's access to the shared profile                          #
# --------------------------------------------------------------------------- #


class TestH2TheGrant:
    def test_a_service_account_is_granted_before_the_probe(self, world):
        _with_record()
        assert _run().code == 0
        steps = [step for step, _ in world["sched"].events]
        assert steps.index("grant") < steps.index("probe")
        assert ACCOUNT in world["sched"].granted

    def test_a_failed_probe_takes_a_new_grant_back(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], probe=BAD_CHECK)
        _run()
        assert ACCOUNT not in world["sched"].granted

    def test_a_grant_that_was_already_there_is_never_taken_back(self, world):
        """The old nightly (same account, machine scope already provisioned) still needs it."""
        _with_record()
        world["sched"].granted.add(ACCOUNT)
        world["state"]["ops"] = FakeOps(world["sched"], probe=BAD_CHECK)
        _run()
        assert ACCOUNT in world["sched"].granted

    def test_a_rollback_takes_a_new_grant_back(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new"}))
        _run()
        assert ACCOUNT not in world["sched"].granted

    def test_a_failed_grant_changes_nothing(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"grant"}))
        result = _run()
        assert result.code == 1
        assert world["sched"].tasks[NAME].action_path == world["old"]
        assert [step for step, _ in world["sched"].events] == ["grant"]
        assert "access to the shared settings" in _text(result)

    def test_the_signed_in_account_is_never_granted(self, world):
        _set(world, run_as=ADMIN, logon_type=task_com.TASK_LOGON_INTERACTIVE_TOKEN)
        assert _run().code == 0
        assert "grant" not in [step for step, _ in world["sched"].events]

    # The grant itself (recorded first, 115's revoke, the record kept in step) is pinned against 115's own rig:
    # tests/test_provisioning.py::TestMigrateGrantRecord.


# --------------------------------------------------------------------------- #
# M1 - enabled, and a trigger that is really "once a day at HH:MM"            #
# --------------------------------------------------------------------------- #


def _trigger_task(**trigger):
    task = MagicMock(name="IRegisteredTask")
    task.Enabled = True
    task.State = task_com.TASK_STATE_READY
    task.NextRunTime = task.LastRunTime = None
    task.LastTaskResult = 0
    task.Definition.Actions.Count = 1
    task.Definition.Triggers.Count = 1
    item = task.Definition.Triggers.Item.return_value
    item.Type = task_com.TASK_TRIGGER_DAILY
    item.Enabled = True
    item.StartBoundary = "2024-01-01T02:05:00"
    item.DaysInterval = 1
    item.RandomDelay = ""
    item.EndBoundary = ""
    item.Repetition.Interval = ""
    for key, value in trigger.items():
        if key == "Repetition":
            item.Repetition.Interval = value
        else:
            setattr(item, key, value)
    return task


class TestM1TheTrigger:
    def test_the_apps_own_trigger_reads(self):
        facts = task_com.task_facts(_trigger_task())
        assert facts.run_time == "02:05" and facts.enabled is True and facts.state == task_com.TASK_STATE_READY

    @pytest.mark.parametrize(
        "change",
        [
            {"DaysInterval": 2},
            {"RandomDelay": "PT10M"},
            {"Repetition": "PT1H"},
            {"EndBoundary": "2030-01-01T00:00:00"},
            {"StartBoundary": "2024-01-01T02:05:00Z"},
            {"StartBoundary": "2024-01-01T02:05:00-08:00"},
            {"StartBoundary": "2024-01-01T02:05:00 trailing"},
        ],
        ids=["every-2-days", "random-delay", "repetition", "end-boundary", "utc-z", "offset", "trailing"],
    )
    def test_anything_but_once_a_day_at_one_local_time_is_no_run_time(self, change):
        assert task_com.task_facts(_trigger_task(**change)).run_time is None

    @pytest.mark.parametrize(("task_on", "trigger_on", "expected"), [(False, True, False), (True, False, False)])
    def test_a_switched_off_task_or_trigger_reads_disabled(self, task_on, trigger_on, expected):
        task = _trigger_task(Enabled=trigger_on)
        task.Enabled = task_on
        assert task_com.task_facts(task).enabled is expected

    def test_a_disabled_nightly_is_not_moved(self, world):
        """Re-registering would switch it on; DistrictSync never turns a nightly on that someone switched off."""
        _with_record()
        _set(world, enabled=False)
        result = _run()
        assert result.code == 1
        assert "switched off" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_an_unreadable_enabled_state_is_not_moved(self, world):
        _with_record()
        _set(world, enabled=None)
        assert _run().code == 1 and world["state"]["elevated"] == 0


# --------------------------------------------------------------------------- #
# M2 - the run-time window, again, and in the child                           #
# --------------------------------------------------------------------------- #


class TestM2TheWindow:
    def test_it_is_checked_again_right_before_the_prompt(self, world):
        """The prompts for the passwords can take minutes: the clock is read again just before the elevated step."""
        _with_record()
        clock = iter([NOON, datetime(2026, 11, 28, 1, 45)])
        result = migration_apply.run(
            declared=None, collect=Collect(), delivery_flags_given=False, now=lambda: next(clock)
        )
        assert result.code == 1 and "within 30 minutes" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_the_child_gets_a_deadline_and_refuses_the_delete_after_it(self, world):
        _with_record()
        _run()
        deadline = world["state"]["payloads"][0]["deadline"]
        run_at = datetime(2026, 11, 29, 2, 0)  # tomorrow 02:00, seen from noon
        assert deadline == pytest.approx(run_at.timestamp() - 30 * 60)

        sched = world["sched"]
        sched.events.clear()  # only this call's steps
        new = old = _params(world["new"])
        out = migration_move.move_nightly(
            NAME, new=new, old=old, probe_payload={}, ops=FakeOps(sched), deadline=100.0, clock=lambda: 101.0
        )
        assert out.stage is migration_move.MoveStage.TOO_LATE
        assert "delete" not in [step for step, _ in sched.events]

    def test_a_running_nightly_is_not_moved(self, world):
        _with_record()
        _set(world, state=task_com.TASK_STATE_RUNNING)
        collect = Collect()
        result = _run(collect=collect)
        assert result.code == 1 and "running now" in _text(result)
        assert world["state"]["elevated"] == 0
        assert collect.asked_password == []  # refused before anyone is asked for a password

    def test_a_nightly_that_starts_while_the_passwords_are_typed_is_not_moved(self, world):
        _with_record()

        class StartsRunning(Collect):
            def task_password(self, account):
                _set(world, state=task_com.TASK_STATE_RUNNING)
                return super().task_password(account)

        result = _run(collect=StartsRunning())
        assert result.code == 1 and "running now" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_no_uninstall_while_the_nightly_runs(self, world, monkeypatch):
        own = world["tmp"] / "LocalAppData" / "myBlueprint.DistrictSync" / "Update.exe"
        monkeypatch.setattr(migration_apply, "_own_update_exe", lambda: own)
        _set(world, action_path=world["new"], state=task_com.TASK_STATE_RUNNING)
        result = _run()
        assert world["state"]["uninstalled"] == []
        assert "running" in _text(result)


def _params(exe: str) -> task_com.RegisterParams:
    return task_com.RegisterParams(
        task_name=NAME,
        exe=exe,
        arguments="--sis myedbc",
        working_dir=str(Path(exe).parent),
        run_time="02:00",
        user=ACCOUNT,
        password=SECRET,
        kind=PrincipalKind.PASSWORD,
        run_highest=True,
        wake_to_run=True,
    )


# --------------------------------------------------------------------------- #
# M3 - a put-back the child would refuse is refused first, in words           #
# --------------------------------------------------------------------------- #


class TestM3PerUserOldCopy:
    def test_an_old_per_user_copy_run_by_another_account_is_named(self, world, monkeypatch):
        _with_record()
        monkeypatch.setattr(migration_apply, "_old_is_per_user_install", lambda program: True)
        result = _run()
        assert result.code == 1
        assert "installed for one Windows account" in _text(result)
        assert world["state"]["elevated"] == 0

    def test_positive_twin_the_signed_in_owner_of_that_copy_moves(self, world, monkeypatch):
        monkeypatch.setattr(migration_apply, "_old_is_per_user_install", lambda program: True)
        _set(world, run_as=ADMIN, logon_type=task_com.TASK_LOGON_INTERACTIVE_TOKEN)
        assert _run().code == 0

    def test_a_child_validation_refusal_is_did_not_start_with_its_reason(self, world, monkeypatch):
        _with_record()
        from src.scheduler import elevated_apply

        def refuse(payload, **kw):
            raise ValueError("refused")

        monkeypatch.setattr(migration_move, "run_in_child", refuse)
        result = _run()
        assert result.code == 1
        text = _text(result)
        assert "did not start" in text and "refused the request" in text
        assert migration_apply.STILL_OLD not in text
        assert elevated_apply  # imported for the patch target's module


# --------------------------------------------------------------------------- #
# M4 - a timed-out step is UNSETTLED                                          #
# --------------------------------------------------------------------------- #


class TestM4Unsettled:
    def test_a_timed_out_delete_is_unsettled_and_the_parent_waits_and_says_check(self, world, monkeypatch):
        _with_record()
        waited: list[float] = []
        monkeypatch.setattr(migration_apply, "_sleep", waited.append)
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"delete_timeout"}))
        result = _run()
        assert result.code == 1
        assert waited == [migration_apply.UNSETTLED_WAIT_S]
        text = _text(result)
        assert "check Task Scheduler" in text and "Way back: " in text

    def test_in_isolation_any_timeout_is_unsettled_whatever_the_read_back(self, world):
        sched = world["sched"]

        class LateOps(FakeOps):
            def register(self, params):
                super().register(params)
                raise task_com.BoundedTimeout("register")

        out = migration_move.move_nightly(
            NAME,
            new=_params(world["new"]),
            old=_params(world["old"]),
            probe_payload={},
            ops=LateOps(sched),
            deadline=4e9,
            clock=lambda: 1e9,
        )
        assert out.stage is migration_move.MoveStage.UNSETTLED


# --------------------------------------------------------------------------- #
# M5 - neither password variable reaches a child                              #
# --------------------------------------------------------------------------- #


class TestM5Environment:
    def test_both_variables_are_gone_before_any_child_starts(self, world, monkeypatch):
        import src.main as main_mod
        from src.main import SFTP_PASSWORD_ENV_VAR, TASK_PASSWORD_ENV_VAR, cli

        _with_record()
        monkeypatch.setattr(main_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(main_mod, "_has_console", lambda: False)
        monkeypatch.setenv(TASK_PASSWORD_ENV_VAR, SECRET)
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, SFTP_SECRET)
        seen: list[set[str]] = []
        real = windows.run_elevated_child

        def spy(req, res, *, timeout_s=None):
            seen.append({TASK_PASSWORD_ENV_VAR, SFTP_PASSWORD_ENV_VAR} & set(os.environ))
            return real(req, res, timeout_s=timeout_s)

        monkeypatch.setattr(windows, "run_elevated_child", spy)
        assert cli(["--migrate", "--apply"]) == 0
        assert seen == [set()]  # the elevated child inherits neither
        assert world["state"]["payloads"][0]["password"] == SECRET  # positive twin: the value still travelled

    def test_the_uninstaller_gets_a_clean_environment(self, monkeypatch, tmp_path):
        from src.main import SFTP_PASSWORD_ENV_VAR, TASK_PASSWORD_ENV_VAR

        monkeypatch.setenv(TASK_PASSWORD_ENV_VAR, SECRET)
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, SFTP_SECRET)
        monkeypatch.setenv("DSYNC_PROBE_KEEP", "1")
        captured: dict = {}

        def fake_run(argv, **kw):
            captured.update(kw, argv=argv)
            return MagicMock(returncode=0)

        monkeypatch.setattr(migration_apply.subprocess, "run", fake_run)
        assert migration_apply._uninstall(tmp_path / "Update.exe") == 0
        assert captured["argv"][1:] == ["uninstall", "--silent"]
        env = captured["env"]
        assert TASK_PASSWORD_ENV_VAR not in env and SFTP_PASSWORD_ENV_VAR not in env
        assert env.get("DSYNC_PROBE_KEEP") == "1"  # positive twin: the rest of the environment is passed on


# --------------------------------------------------------------------------- #
# M6 - the district's config resolves under the shared profile                #
# --------------------------------------------------------------------------- #


class TestM6TheDistrict:
    def test_a_district_that_does_not_resolve_here_is_refused(self, world, monkeypatch):
        _with_record()

        def missing(sis):
            raise FileNotFoundError("no mapping")

        monkeypatch.setattr(migration_apply, "_load_district", missing)
        result = _run()
        assert result.code == 1 and "district" in _text(result).lower()
        assert world["state"]["elevated"] == 0

    def test_positive_twin_the_real_bundled_district_resolves(self, world):
        _with_record()
        seen: list[str] = []
        real = migration_apply._load_district

        def spy(sis):
            seen.append(sis)
            return real(sis)

        import unittest.mock as um

        with um.patch.object(migration_apply, "_load_district", spy):
            assert _run().code == 0
        assert seen == ["myedbc"]


# --------------------------------------------------------------------------- #
# L1-L6                                                                       #
# --------------------------------------------------------------------------- #


class TestLows:
    def test_l1_a_failed_put_back_with_an_unreadable_read_back_is_no_nightly(self, world, monkeypatch):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new", "register_old"}))
        real = world["sched"].facts

        def flaky(name):
            if world["state"]["elevated"]:  # every read before the child works; the read-back after it fails
                raise task_com.TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)
            return real(name)

        monkeypatch.setattr(migration_apply, "_read_task", flaky)
        result = _run()
        assert "NO nightly" in _text(result)

    def test_l2_the_put_back_is_described_in_the_apps_shape(self):
        assert "as it was" not in migration_apply.ROLLED_BACK
        assert "run level" in migration_apply.ROLLED_BACK
        guide = (Path(__file__).resolve().parent.parent / "docs" / "partner" / "it-deployment.md").read_text(
            encoding="utf-8"
        )
        assert "put back as it was" not in guide

    def test_l3_the_new_program_is_the_unresolved_executable(self, monkeypatch):
        monkeypatch.setattr(
            migration_apply.sys, "executable", r"C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe"
        )
        assert str(migration_apply._new_program()) == str(
            Path(r"C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe")
        )

    def test_l4_an_empty_task_password_stdin_exits_2_before_delivery_is_saved(self, world, monkeypatch):
        import io

        import src.main as main_mod
        from src.main import cli

        _with_record()
        monkeypatch.setattr(migration, "_delivery_in_machine_store", lambda settings: False)
        monkeypatch.setattr(main_mod, "_is_windows", lambda: True)
        monkeypatch.setattr(main_mod, "_has_console", lambda: False)
        monkeypatch.setenv(main_mod.SFTP_PASSWORD_ENV_VAR, SFTP_SECRET)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        saved: list[str] = []
        monkeypatch.setattr(main_mod, "_save_sftp", lambda cfg, **kw: saved.append("saved"))
        assert cli(["--migrate", "--apply", "--task-password-stdin"]) == 2
        assert saved == [] and world["state"]["elevated"] == 0

    def test_l5_the_per_user_copy_is_found_through_the_known_folder(self, monkeypatch, tmp_path):
        from src.utils import installation

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "env-says-elsewhere"))
        monkeypatch.setattr(installation, "_local_app_data", lambda: tmp_path)
        (tmp_path / "myBlueprint.DistrictSync").mkdir()
        (tmp_path / "myBlueprint.DistrictSync" / "Update.exe").write_bytes(b"")
        assert installation.per_user_update_exe() == tmp_path / "myBlueprint.DistrictSync" / "Update.exe"

    def test_l6_the_report_points_at_apply(self):
        assert "--migrate --apply" in migration.MOVE_ADVICE
        assert "not automatic yet" not in migration.MOVE_ADVICE

    def test_a_kill_during_the_put_back_leaves_a_state_the_report_names(self, world):
        _with_record()
        world["state"]["ops"] = FakeOps(world["sched"], fail=frozenset({"register_new"}), kill_at="register_old")
        # register_old registers THEN is killed: the old nightly is back, and nothing else ran
        from src.scheduler import elevated_apply
        from tests.test_migrate_apply import Killed

        real_apply = elevated_apply._apply

        def killed_apply(req, res):
            try:
                return real_apply(req, res)
            except Killed:
                return 0

        import unittest.mock as um

        with um.patch.object(elevated_apply, "_apply", killed_apply):
            result = _run()
        assert world["sched"].tasks[NAME].action_path == world["old"]
        assert result.code == 1 and migration_apply.STILL_OLD in _text(result)

    def test_a_kill_before_the_put_back_lands_says_no_nightly(self, world):
        _with_record()

        class KillBeforeOld(FakeOps):
            def register(self, params):
                if params.exe == world["old"]:
                    from tests.test_migrate_apply import Killed

                    raise Killed("register_old")
                return super().register(params)

        world["state"]["ops"] = KillBeforeOld(world["sched"], fail=frozenset({"register_new"}))
        from src.scheduler import elevated_apply
        from tests.test_migrate_apply import Killed

        real_apply = elevated_apply._apply

        def killed_apply(req, res):
            try:
                return real_apply(req, res)
            except Killed:
                return 0

        import unittest.mock as um

        with um.patch.object(elevated_apply, "_apply", killed_apply):
            result = _run()
        assert NAME not in world["sched"].tasks
        assert "NO nightly" in _text(result) and "Way back: " in _text(result)


def test_the_windows_path_helpers_are_pure():
    """``PureWindowsPath`` keeps these tests meaningful on the POSIX CI legs."""
    assert str(PureWindowsPath("D:\\")) == "D:\\"
    assert AppConfig  # imported for the fixtures' config writes
