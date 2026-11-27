"""Plan 0055 D1a (DSYNC-101): ``--migrate`` reports what moving to the machine-wide install involves, and changes nothing."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduler import migration

F = migration.Findings


def _f(**over) -> migration.Findings:
    base = dict(
        machine_wide=True,
        nightly_found=True,
        nightly_program="C:\\old\\DistrictSync.exe",
        nightly_uses_this_copy=False,
        per_user_copy=False,
        shared_profile=True,
        delivery_in_machine_store=True,
    )
    base.update(over)
    return F(**base)


class TestTheReport:
    def test_a_nightly_on_another_copy_says_so_and_how_to_move_it(self):
        lines = migration.report_lines(_f())
        assert any("still runs another copy" in line for line in lines)
        assert migration.MOVE_NOT_BUILT in lines
        assert migration.needs_action(_f())

    def test_nothing_left_to_do(self):
        done = _f(nightly_uses_this_copy=True)
        assert any("already runs this copy" in line for line in migration.report_lines(done))
        assert not migration.needs_action(done)

    def test_not_the_machine_wide_copy(self):
        lines = migration.report_lines(_f(machine_wide=False))
        assert len(lines) == 2 and "not the machine-wide" in lines[1]

    def test_an_unreadable_task_is_never_called_absent(self):
        lines = migration.report_lines(_f(nightly_found=None, nightly_program=None, nightly_uses_this_copy=None))
        assert any("could not be read" in line for line in lines)
        assert not any("No nightly is scheduled" in line for line in lines)

    def test_a_per_user_copy_and_delivery_are_reported_never_removed(self):
        lines = migration.report_lines(_f(per_user_copy=True, delivery_in_machine_store=False))
        assert any("not removed for you" in line for line in lines)
        assert any("--sftp-configure" in line for line in lines)

    def test_it_says_nothing_was_changed(self):
        assert "nothing was changed" in migration.report_lines(_f())[0]


class TestDiscovery:
    def test_not_found_only_when_task_scheduler_answered_so(self, monkeypatch):
        from src.scheduler import task_com

        def absent(name):
            raise task_com.TaskComError(task_com.HR_NOT_FOUND, "not found")

        monkeypatch.setattr(task_com, "read_task", absent)
        assert migration._read_nightly("DistrictSync_Daily") == (False, None)

        def denied(name):
            raise task_com.TaskComError(task_com.HR_ACCESS_DENIED, "denied")

        monkeypatch.setattr(task_com, "read_task", denied)
        assert migration._read_nightly("DistrictSync_Daily") == (None, None)

    def test_the_program_is_compared_to_this_copy(self, monkeypatch, tmp_path):
        me = tmp_path / "current" / "DistrictSync.exe"
        me.parent.mkdir()
        me.write_bytes(b"")
        monkeypatch.setattr(migration, "_this_program", lambda: me.resolve())
        assert migration._same_program(f'"{me}"') is True
        assert migration._same_program(str(tmp_path / "old.exe")) is False
        assert migration._same_program(None) is None

    def test_discovery_never_raises_and_never_writes(self, monkeypatch):
        """Read-only by construction: the module CALLS no write, delete, register or elevation function."""
        import ast

        tree = ast.parse(
            (Path(__file__).resolve().parent.parent / "src" / "scheduler" / "migration.py").read_text(encoding="utf-8")
        )
        called = {
            getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        banned = {
            "register",
            "register_task",
            "delete",
            "delete_task",
            "delete_task_by_name",
            "save",
            "write_text",
            "write_bytes",
            "unlink",
            "rmtree",
            "request_provision",
            "request_machine_setup",
            "run_elevated_child",
        }
        assert not called & banned, called & banned
        assert "read_task" in called  # positive twin: the scan sees the module's calls
        findings = migration.discover()
        assert isinstance(findings, migration.Findings)


class TestTheCommand:
    @pytest.fixture()
    def findings(self, monkeypatch):
        holder = {"f": _f(nightly_uses_this_copy=True)}
        monkeypatch.setattr(migration, "discover", lambda task_name="DistrictSync_Daily": holder["f"])
        return holder

    def test_exit_0_when_nothing_is_left(self, findings, capsys):
        from src.main import cli

        assert cli(["--migrate"]) == 0
        assert "already runs this copy" in capsys.readouterr().out

    def test_exit_1_when_something_is_left(self, findings):
        from src.main import cli

        findings["f"] = _f()
        assert cli(["--migrate"]) == 1

    @pytest.mark.parametrize("argv", [["--migrate", "--sis", "x"], ["--migrate", "--machine-setup"]])
    def test_misuse_is_exit_2(self, findings, argv):
        from src.main import cli

        assert cli(argv) == 2
