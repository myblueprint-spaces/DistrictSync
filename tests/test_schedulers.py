"""Tests for src/scheduler/windows.py and src/scheduler/linux.py.

The WINDOWS scheduler is in-process COM end to end since plan 0041 (S1a: read +
delete; S1b: registration + the elevated self-child) — these tests inject
``TaskFacts``/``TaskComError`` at the ``task_com.bounded`` seam and pin the
transport's ABSENCE (``TestNoScheduleSubprocess``). The COM-object contract rows
(settings quintet, logon matrix, password hygiene) live in
``tests/test_scheduler_runas.py``; the elevated flow in
``tests/test_scheduler_elevation.py``; the child mode in
``tests/test_elevated_apply.py``. The Linux cron tests still mock ``_run``.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# -----------------------------------------------------------------------
# _build_action_args — the task action's command line (transport-independent)
# -----------------------------------------------------------------------


class TestBuildActionArgs:
    """The action shape rows (plan 0041 row 11) — shared verbatim by PS-then-COM.

    These were previously asserted through the register subprocess's DSYNC_ARGS env;
    with the transport gone they pin the BUILDER directly, which is where the behaviour
    always lived (`_build_action_args` is reused unchanged by the COM path).
    """

    def _args(self, exe="C:/DistrictSync/DistrictSync.exe", sftp=False):
        # Forward slashes ON PURPOSE: a raw C:\ backslash path parses as one giant
        # filename on the POSIX CI legs (`.name` never matches "python", `.parent` is
        # "."), which is a test artifact, not a behaviour — caught red on Linux CI.
        from src.scheduler.windows import _build_action_args

        return _build_action_args(
            Path(exe),
            "myedbc",
            Path("C:/data/in"),
            Path("C:/data/out"),
            sftp,
        )

    def test_frozen_exe_mode_bare_args_and_parent_workdir(self):
        arguments, working_dir = self._args()
        assert "-m src.main" not in arguments
        assert arguments.startswith("--sis myedbc")
        assert working_dir == Path("C:/DistrictSync")

    def test_python_source_mode_uses_m_flag_and_project_root(self):
        arguments, working_dir = self._args(exe="C:/Python313/python.exe")
        assert arguments.startswith("-m src.main --sis myedbc")
        assert (working_dir / "src" / "main.py").exists()  # the project root, really

    def test_sftp_flag_appended_when_requested(self):
        arguments, _ = self._args(sftp=True)
        assert "--sftp" in arguments

    def test_source_scheduled_always_labels_the_nightly_run(self):
        """D2c: losing this silently relabels every nightly run 'cli' in Run History."""
        for sftp in (False, True):
            arguments, _ = self._args(sftp=sftp)
            assert arguments.rstrip().endswith("--source scheduled")

    def test_space_bearing_paths_are_quoted(self):
        # Forward-slash inputs + separator-neutral asserts: the QUOTING is the behaviour
        # under test; the path separator is the platform's (this runs on the POSIX CI legs).
        from src.scheduler.windows import _build_action_args

        arguments, _ = _build_action_args(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/My District Data/in"),
            Path("C:/My District Data/out"),
            False,
        )
        assert '"' + str(Path("C:/My District Data/in")) + '"' in arguments
        assert '"' + str(Path("C:/My District Data/out")) + '"' in arguments

    def test_validation_rejects_bad_inputs_before_any_com_call(self):
        """Boundary validation still precedes every OS interaction (row 16)."""
        from src.scheduler.windows import register_task

        with patch("src.scheduler.windows.task_com.bounded") as mock_bounded:
            for kwargs in (
                {"task_name": "bad;name"},
                {"sis_type": "not-a-config!"},
                {"run_time": "25:99"},
            ):
                base = dict(
                    task_name="DistrictSync_Daily",
                    exe_path=Path("x.exe"),
                    sis_type="myedbc",
                    input_dir=Path("i"),
                    output_dir=Path("o"),
                    run_time="03:00",
                )
                base.update(kwargs)
                with pytest.raises(ValueError):
                    register_task(**base)
            mock_bounded.assert_not_called()


# -----------------------------------------------------------------------
# is_elevated — administrator detection (used by the wizard classifier)
# -----------------------------------------------------------------------


class TestIsElevated:
    def test_win32_admin_true(self):
        from src.scheduler import windows

        fake_ctypes = MagicMock()
        fake_ctypes.windll.shell32.IsUserAnAdmin.return_value = 1
        with (
            patch.object(windows.sys, "platform", "win32"),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            assert windows.is_elevated() is True

    def test_win32_admin_false(self):
        from src.scheduler import windows

        fake_ctypes = MagicMock()
        fake_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0
        with (
            patch.object(windows.sys, "platform", "win32"),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            assert windows.is_elevated() is False

    def test_win32_error_is_false(self):
        from src.scheduler import windows

        fake_ctypes = MagicMock()
        fake_ctypes.windll.shell32.IsUserAnAdmin.side_effect = OSError("boom")
        with (
            patch.object(windows.sys, "platform", "win32"),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            assert windows.is_elevated() is False

    def test_non_win32_is_false(self):
        from src.scheduler import windows

        with patch.object(windows.sys, "platform", "linux"):
            assert windows.is_elevated() is False


class TestWindowsDeleteTask:
    """COM delete (plan 0041 Slice 1a) — behavioural parity with the retired schtasks pins.

    The subprocess asserts (absolute System32 argv, no-window flag) retired WITH the
    subprocess: there is no child process to pin. What replaces them: no-subprocess
    absence pins (below, ``TestNoScheduleSubprocess``) and the two message contracts
    consumers key on — the "cannot find" idempotency marker and the "access is denied"
    elevated-retry substring (contract row 13).
    """

    @patch("src.scheduler.windows.task_com.bounded")
    def test_delete_success(self, mock_bounded):
        from src.scheduler.windows import delete_task

        mock_bounded.return_value = None  # delete_task_by_name returns None on success

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is True
        assert msg  # a human-readable confirmation, never blank

    @patch("src.scheduler.windows.task_com.bounded")
    def test_absent_task_keeps_the_cannot_find_marker(self, mock_bounded):
        """The idempotency contract: `interpret_unregister` maps "cannot find" to
        success-shaped ("there was no schedule to remove — nothing changed"). The COM
        HR_NOT_FOUND canonical must keep that marker or deleting an already-gone task
        starts presenting as a failure."""
        from src.scheduler import task_com
        from src.scheduler.windows import delete_task

        mock_bounded.side_effect = task_com.TaskComError(
            task_com.HR_NOT_FOUND, "The system cannot find the file specified."
        )

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is False
        assert "cannot find" in msg.lower()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_access_denied_keeps_the_adapter_retry_substring(self, mock_bounded):
        """Contract row 13: `WindowsTaskScheduler.delete` retries elevated ONLY when the
        failure message contains "access is denied". The COM canonical must preserve it,
        or un-elevated admins permanently lose the ability to remove a Highest task."""
        from src.scheduler import task_com
        from src.scheduler.windows import delete_task

        mock_bounded.side_effect = task_com.TaskComError(task_com.HR_ACCESS_DENIED, "Access is denied.")

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is False
        assert "access is denied" in msg.lower()

    @patch("src.scheduler.windows._confirm_removal")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_timeout_resolves_through_read_back_never_a_bare_verdict(self, mock_bounded, mock_confirm):
        """A timed-out COM worker may still complete the delete — the verdict comes from
        reading the real task back, the same honesty rule the elevated path applies."""
        from src.scheduler import task_com
        from src.scheduler.windows import delete_task

        mock_bounded.side_effect = task_com.BoundedTimeout("delete")
        mock_confirm.return_value = (True, "Schedule removed and confirmed.")

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is True
        mock_confirm.assert_called_once()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_pywin32_missing_degrades_to_the_canonical_message(self, mock_bounded):
        """Contract row 10: a frozen build without pywin32 fails readable, never raises."""
        from src.scheduler import task_com
        from src.scheduler.windows import delete_task

        mock_bounded.side_effect = ImportError("No module named 'win32com'")

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is False
        assert msg == task_com.MSG_COM_UNAVAILABLE

    def test_invalid_task_name_raises_before_any_com_call(self):
        from src.scheduler.windows import delete_task

        with pytest.raises(ValueError):
            delete_task("bad;name|rm -rf")


class TestReadSchedule:
    """D4 tri-state read-back — found / definitively-absent / query-failed (COM since 0041).

    The load-bearing contract is UNCHANGED by the transport swap: the definitive
    not-found → ``found=False`` (MISSING), but ANY other failure (denied, timeout,
    pywin32 missing, non-Windows) → ``found=None`` (UNKNOWN), never a false "absent".
    Classification is now HRESULT-keyed at the ``task_com`` boundary — these tests inject
    ``TaskFacts`` / ``TaskComError`` where the retired fixtures injected subprocess stdout.
    """

    @staticmethod
    def _facts(**overrides):
        from src.scheduler.task_com import TaskFacts

        base = dict(
            next_run="2026-07-09T03:00:00",
            last_run="2026-07-08T03:00:00",
            last_result=0,
            action_path=r"C:\Program Files\DistrictSync\DistrictSync.exe",
        )
        base.update(overrides)
        return TaskFacts(**base)

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_found_task_parses_all_fields(self, mock_bounded):
        from src.scheduler.windows import read_schedule

        mock_bounded.return_value = self._facts()
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is True
        # Datetimes ride through as the naive-local ISO strings task_com emits.
        assert rb.next_run == "2026-07-09T03:00:00"
        assert rb.last_run == "2026-07-08T03:00:00"
        assert rb.last_result == 0
        assert rb.action_path.endswith("DistrictSync.exe")

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_the_probe_is_bounded_by_the_read_timeout(self, mock_bounded):
        """The 10s bound survived the transport swap — a hung Task Scheduler RPC can
        never freeze the UI probe (it fires on nearly every nav click)."""
        from src.scheduler import task_com
        from src.scheduler.windows import read_schedule

        mock_bounded.return_value = self._facts()
        read_schedule("DistrictSync_Daily")
        assert mock_bounded.call_args[1]["timeout_s"] == task_com.READ_TIMEOUT_S == 10.0

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_definitively_absent_is_found_false(self, mock_bounded):
        """ONLY the unwrapped 0x80070002 may produce found=False (contract row 5)."""
        from src.scheduler import task_com
        from src.scheduler.windows import read_schedule

        mock_bounded.side_effect = task_com.TaskComError(
            task_com.HR_NOT_FOUND, "The system cannot find the file specified."
        )
        rb = read_schedule("NonExistent")
        assert rb.found is False  # MISSING — the only state that may claim "not scheduled"

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_access_denied_is_unknown_not_absent(self, mock_bounded):
        from src.scheduler import task_com
        from src.scheduler.windows import read_schedule

        mock_bounded.side_effect = task_com.TaskComError(task_com.HR_ACCESS_DENIED, "Access is denied.")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None  # a failed query is NEVER reported as absent
        assert rb.error == "Access is denied."

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_timeout_is_unknown(self, mock_bounded):
        from src.scheduler import task_com
        from src.scheduler.windows import read_schedule

        mock_bounded.side_effect = task_com.BoundedTimeout("read")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert "timed out" in (rb.error or "").lower()

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_pywin32_missing_is_unknown(self, mock_bounded):
        """Contract row 10: the COM analogue of the retired "PowerShell not found"."""
        from src.scheduler import task_com
        from src.scheduler.windows import read_schedule

        mock_bounded.side_effect = ImportError("No module named 'win32com'")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert rb.error == task_com.MSG_COM_UNAVAILABLE

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_an_unexpected_raise_is_unknown_never_propagated(self, mock_bounded):
        """The never-raises probe contract holds even for a failure shape nobody mapped."""
        from src.scheduler.windows import read_schedule

        mock_bounded.side_effect = RuntimeError("marshalling exploded")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert rb.error  # readable, generic — never the raw exception repr
        assert "marshalling" not in rb.error

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_never_run_task_has_null_last_run(self, mock_bounded):
        """task_com nulls the never-run epoch; the passthrough must not resurrect it."""
        from src.scheduler.windows import read_schedule

        mock_bounded.return_value = self._facts(last_run=None, last_result=267011)
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is True
        assert rb.last_run is None
        assert rb.last_result == 267011

    @patch("src.scheduler.windows.sys.platform", "linux")
    def test_non_windows_is_unknown_with_platform_note(self):
        from src.scheduler.windows import read_schedule

        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert "Windows" in (rb.error or "")

    @patch("src.scheduler.windows.sys.platform", "win32")
    def test_invalid_task_name_is_unknown_never_raises(self):
        # F1: validation is guarded — an invalid name degrades to UNKNOWN (found=None),
        # honouring the "never raises" probe contract, not a ValueError.
        from src.scheduler.windows import read_schedule

        rb = read_schedule("bad;name|rm -rf")
        assert rb.found is None
        assert rb.error


class TestNoScheduleSubprocess:
    """Absence pins (plan 0041 Slice 1a): the read/delete transport is GONE, not dormant.

    These are source-level pins because behaviour mocks can't prove a negative about
    paths not taken. Register-path PowerShell is EXEMPT until Slice 1b — the pins scope
    to what 1a retired, and 1b's sweep widens them to the whole module.
    """

    def _source(self) -> str:
        from pathlib import Path

        import src.scheduler.windows as w

        return Path(w.__file__).read_text(encoding="utf-8")

    def test_schtasks_and_powershell_left_the_allowlist(self):
        """The allowlist SHRANK with each last caller — an unused allowance is surface.

        schtasks.exe went at S1a (delete → COM); powershell.exe at S1b (register + the
        elevated child → COM/our own exe). icacls.exe remains elevation's one subprocess.
        """
        from src.utils.helpers import system_binary

        for retired in ("schtasks.exe", "powershell.exe"):
            with pytest.raises(ValueError):
                system_binary(retired)
        assert system_binary("icacls.exe").lower().endswith("icacls.exe")

    def test_windows_scheduler_imports_no_subprocess_machinery(self):
        """S1b: `subprocess`, `base64` and `re` all left windows.py WITH the transport —
        the scheduler spawns no child process at all (the elevated launch is
        ShellExecuteExW inside elevation.py). AST-level so prose stays free."""
        import ast

        import src.scheduler.windows as w

        tree = ast.parse(Path(w.__file__).read_text(encoding="utf-8"))
        imported = {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names} | {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "subprocess" not in imported
        assert "base64" not in imported

    def test_no_encoded_command_string_anywhere_in_the_scheduler(self):
        """The `-EncodedCommand` literal — the AV-weighted signature this plan exists to
        remove — appears in NO string constant in src/scheduler (docstrings exempt)."""
        import ast

        import src.scheduler as pkg

        for path in Path(pkg.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Prose in docstrings may NAME the retired flag; only an exact-token string
                # constant (argv material) trips the pin.
                is_literal = isinstance(node, ast.Constant) and isinstance(node.value, str)
                if is_literal and node.value.strip() == "-EncodedCommand":
                    raise AssertionError(f"{path.name}:{node.lineno}: -EncodedCommand literal")

    def test_no_code_invokes_schtasks(self):
        """No `system_binary("schtasks…")` call survives anywhere in src/ (prose may)."""
        import re
        from pathlib import Path

        import src

        src_root = Path(src.__file__).parent
        pattern = re.compile(r"system_binary\(\s*['\"]schtasks", re.IGNORECASE)
        hits = [p for p in src_root.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
        assert hits == []

    def test_no_gencache_or_ensuredispatch_anywhere_in_the_scheduler(self):
        """Dynamic Dispatch ONLY: gencache/EnsureDispatch/makepy write generated code to a
        cache dir and import it at runtime — the frozen-exe failure that disqualified
        comtypes AND an AV-shaped behaviour in a plan about removing AV-shaped behaviours.

        Pinned at the AST level so the ban rationale in ``task_com``'s docstring (which
        must NAME the banned calls to explain them) can never trip its own pin: prose is
        invisible to ``ast``, code is not.
        """
        import ast
        from pathlib import Path

        import src.scheduler as pkg

        banned = {"gencache", "EnsureDispatch", "makepy"}
        for path in Path(pkg.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in banned:
                    raise AssertionError(f"{path.name}:{node.lineno}: banned attribute {node.attr!r}")
                if isinstance(node, ast.Name) and node.id in banned:
                    raise AssertionError(f"{path.name}:{node.lineno}: banned name {node.id!r}")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                    hit = banned & {part for n in names for part in n.split(".")}
                    if hit:
                        raise AssertionError(f"{path.name}:{node.lineno}: banned import {sorted(hit)}")

    def test_win32com_import_is_lazy_on_every_platform(self):
        """Importing the scheduler must NOT import pywin32 — `scheduler/__init__` imports
        `windows` at module level on every OS, so an eager win32com import breaks the
        Linux and macOS CI legs outright. Run in a fresh interpreter so this dev box's
        already-imported modules can't mask it (this row is the one that keeps the
        Linux leg importable, and it runs THERE too)."""
        import subprocess as sp
        import sys

        code = "import src.scheduler.windows, sys; raise SystemExit(1 if 'win32com' in sys.modules else 0)"
        proc = sp.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr


# -----------------------------------------------------------------------
# Linux scheduler tests
# -----------------------------------------------------------------------


class TestLinuxRegisterCron:
    @patch("src.scheduler.linux._run")
    def test_register_creates_cron_entry(self, mock_run):
        from src.scheduler.linux import register_cron

        # First call: crontab -l (empty)
        # Second call: crontab - (install)
        mock_run.side_effect = [
            (1, "no crontab for user"),  # crontab -l
            (0, ""),  # crontab -
        ]

        ok, msg = register_cron(
            exe_path=Path("/opt/districtsync/DistrictSync"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="03:00",
        )
        assert ok is True
        # Verify the crontab - call included the sentinel
        install_call = mock_run.call_args_list[1]
        assert "DistrictSync managed entry" in install_call[1].get(
            "stdin", install_call[0][1] if len(install_call[0]) > 1 else ""
        )

    @patch("src.scheduler.linux._run")
    def test_register_replaces_existing_entry(self, mock_run):
        from src.scheduler.linux import CRON_SENTINEL, register_cron

        existing = f"0 5 * * * /old/command {CRON_SENTINEL}\n30 12 * * * /other/job\n"
        mock_run.side_effect = [
            (0, existing),  # crontab -l
            (0, ""),  # crontab -
        ]

        ok, msg = register_cron(
            exe_path=Path("/opt/districtsync/DistrictSync"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="04:30",
        )
        assert ok is True
        # The new crontab should keep /other/job but replace the old sentinel entry
        install_stdin = mock_run.call_args_list[1][1].get(
            "stdin", mock_run.call_args_list[1][0][1] if len(mock_run.call_args_list[1][0]) > 1 else ""
        )
        assert "/other/job" in install_stdin
        assert "30 04" in install_stdin  # new time

    @patch("src.scheduler.linux._run")
    def test_register_python_source_uses_m_flag(self, mock_run):
        """Running from source via python must prepend 'cd <root> && python -m src.main'."""
        from src.scheduler.linux import register_cron

        mock_run.side_effect = [
            (1, "no crontab for user"),
            (0, ""),
        ]
        register_cron(
            exe_path=Path("/usr/bin/python3"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="03:00",
        )
        install_stdin = mock_run.call_args_list[1][1].get("stdin", "")
        assert "-m src.main" in install_stdin
        assert "cd " in install_stdin and "&&" in install_stdin

    @patch("src.scheduler.linux._run")
    def test_register_with_sftp(self, mock_run):
        from src.scheduler.linux import register_cron

        mock_run.side_effect = [
            (1, "no crontab for user"),
            (0, ""),
        ]

        register_cron(
            exe_path=Path("/opt/districtsync/DistrictSync"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="03:00",
            sftp=True,
        )
        install_stdin = mock_run.call_args_list[1][1].get(
            "stdin", mock_run.call_args_list[1][0][1] if len(mock_run.call_args_list[1][0]) > 1 else ""
        )
        assert "--sftp" in install_stdin

    def test_register_rejects_invalid_sis(self):
        from src.scheduler.linux import register_cron

        with pytest.raises(ValueError, match="Invalid SIS type"):
            register_cron(
                exe_path=Path("/opt/districtsync"),
                sis_type="bad;type",
                input_dir=Path("/data/input"),
                output_dir=Path("/data/output"),
                run_time="03:00",
            )

    @patch("src.scheduler.linux._run")
    def test_register_read_failure_aborts_without_rewrite(self, mock_run):
        """A failed `crontab -l` (permission denied etc.) must ABORT loudly, never rewrite.

        The old code treated ANY read failure as an empty crontab and then installed a
        crontab containing only the DistrictSync line — destroying the user's other jobs.
        """
        from src.scheduler.linux import register_cron

        mock_run.return_value = (1, "crontab: you are not allowed to use this program")

        ok, msg = register_cron(
            exe_path=Path("/opt/districtsync/DistrictSync"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="03:00",
        )
        assert ok is False
        assert "Couldn't read the existing crontab" in msg
        # Only the read ran — no `crontab -` install call may follow a failed read.
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["crontab", "-l"]

    @patch("src.scheduler.linux._run")
    def test_register_exit_zero_content_mentioning_no_crontab_is_preserved(self, mock_run):
        """An exit-0 crontab whose JOB TEXT happens to contain "no crontab" is real content.

        The read is classified by EXIT CODE first — the "no crontab" phrase only matters on a
        non-zero exit (the genuinely-empty case). The old text-only match discarded such lines.
        """
        from src.scheduler.linux import register_cron

        mock_run.side_effect = [
            (0, '30 12 * * * /bin/echo "no crontab here"'),  # crontab -l (real job)
            (0, ""),  # crontab -
        ]

        ok, _ = register_cron(
            exe_path=Path("/opt/districtsync/DistrictSync"),
            sis_type="myedbc",
            input_dir=Path("/data/input"),
            output_dir=Path("/data/output"),
            run_time="03:00",
        )
        assert ok is True
        install_stdin = mock_run.call_args_list[1][1].get("stdin", "")
        assert '/bin/echo "no crontab here"' in install_stdin  # the user's job survives


class TestLinuxDeleteCron:
    @patch("src.scheduler.linux._run")
    def test_delete_removes_entry(self, mock_run):
        from src.scheduler.linux import CRON_SENTINEL, delete_cron

        existing = f"0 3 * * * /opt/districtsync {CRON_SENTINEL}\n30 12 * * * /other/job\n"
        mock_run.side_effect = [
            (0, existing),  # crontab -l
            (0, ""),  # crontab -
        ]

        ok, msg = delete_cron()
        assert ok is True

    @patch("src.scheduler.linux._run")
    def test_delete_when_no_crontab(self, mock_run):
        from src.scheduler.linux import delete_cron

        mock_run.return_value = (1, "no crontab for user")

        ok, msg = delete_cron()
        assert ok is True
        assert "No crontab" in msg

    @patch("src.scheduler.linux._run")
    def test_delete_read_failure_aborts_without_rewrite(self, mock_run):
        """Same fail-loud contract as register: an unreadable crontab is never rewritten."""
        from src.scheduler.linux import delete_cron

        mock_run.return_value = (1, "crontab: permission denied")

        ok, msg = delete_cron()
        assert ok is False
        assert "Couldn't read the existing crontab" in msg
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["crontab", "-l"]


class TestLinuxCronEntryExists:
    @patch("src.scheduler.linux._run")
    def test_exists_when_present(self, mock_run):
        from src.scheduler.linux import CRON_SENTINEL, cron_entry_exists

        mock_run.return_value = (0, f"0 3 * * * /opt/districtsync {CRON_SENTINEL}")
        assert cron_entry_exists() is True

    @patch("src.scheduler.linux._run")
    def test_not_exists_when_absent(self, mock_run):
        from src.scheduler.linux import cron_entry_exists

        mock_run.return_value = (0, "30 12 * * * /other/job")
        assert cron_entry_exists() is False
