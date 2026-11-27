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
        from src.scheduler import task_com

        def boom(name):
            raise RuntimeError("no live query in a test")

        import pytest as _pytest

        mp = _pytest.MonkeyPatch()
        mp.setattr(task_com, "read_task", boom)
        mp.setattr(migration, "_settings", lambda: None)
        try:
            findings = migration.discover()
        finally:
            mp.undo()
        assert findings.nightly_found is None  # an unread task is never "absent"
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


class TestReviewFixes:
    def test_the_program_path_is_never_printed(self):
        """Review G3: a per-user install's path names a person; the report says the KIND of copy."""
        lines = migration.report_lines(_f(nightly_program="C:\\Users\\jdoe\\AppData\\Local\\x\\DistrictSync.exe"))
        assert not any("jdoe" in line for line in lines)
        assert any(migration.OTHER_COPY_ELSEWHERE in line or migration.OTHER_COPY_PER_USER in line for line in lines)

    def test_an_unread_nightly_or_a_per_user_copy_is_never_done(self):
        """Review G4."""
        assert migration.needs_action(_f(nightly_found=None, nightly_program=None, nightly_uses_this_copy=None))
        assert migration.needs_action(_f(nightly_uses_this_copy=True, per_user_copy=True))
        assert not migration.needs_action(_f(nightly_uses_this_copy=True))  # positive twin

    def test_the_task_name_comes_from_the_settings(self, monkeypatch):
        """Review G5."""
        seen = []

        class Settings:
            schedule_task_name = "DistrictSync_Custom"
            sftp_enabled = False

        monkeypatch.setattr(migration, "_settings", lambda: Settings())
        monkeypatch.setattr(migration, "_read_nightly", lambda name: (seen.append(name), (False, None))[1])
        migration.discover()
        assert seen == ["DistrictSync_Custom"]

    def test_a_read_error_is_not_a_missing_password(self, monkeypatch):
        """Review G6."""
        from src.sftp import secret_store

        class Settings:
            sftp_enabled = True
            sftp_host = "h.example.invalid"
            sftp_username = "u"

        def boom(self, host, username):
            raise OSError("DPAPI")

        monkeypatch.setattr(secret_store.MachineSecretStore, "get_password", boom)
        assert migration._delivery_in_machine_store(Settings()) is None
        monkeypatch.setattr(secret_store.MachineSecretStore, "get_password", lambda self, h, u: None)
        assert migration._delivery_in_machine_store(Settings()) is False  # positive twin: really absent

    def test_the_move_advice_puts_settings_and_delivery_first_and_names_the_risk(self):
        """Review G7."""
        text = migration.MOVE_NOT_BUILT
        assert text.index("--sftp-test") < text.index("--schedule-remove") < text.index("--schedule-register")
        assert "--principal" in text and "there is no nightly" in text


class TestItIsDispatchedFirst:
    """Review G1/G2: ``--migrate`` runs before the preamble's writes and before the machine-setup gate."""

    def test_no_preamble_and_no_gate(self, monkeypatch, capsys):
        import src.main as main_mod
        from src.scheduler import machine_setup

        monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", lambda: pytest.fail("legacy migration ran"))
        monkeypatch.setattr(main_mod, "pin_data_dir", lambda: pytest.fail("pinned a profile"))
        monkeypatch.setattr(machine_setup, "needs_machine_setup", lambda: True)
        monkeypatch.setattr(migration, "discover", lambda task_name=None: _f(nightly_uses_this_copy=True))
        assert main_mod.cli(["--migrate"]) == 0
        assert "nothing was changed" in capsys.readouterr().out
