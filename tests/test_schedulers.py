"""Tests for src/scheduler/windows.py and src/scheduler/linux.py.

All subprocess calls are mocked — no OS scheduler interaction needed.

Windows registration uses PowerShell ``Register-ScheduledTask`` (the
``ScheduledTasks`` module): a fixed script is handed to ``powershell.exe
-EncodedCommand`` (UTF-16LE-base64) and all dynamic values flow through the
child process environment, so these tests assert against the argv, the decoded
script, and the child env rather than a legacy ``schtasks`` command form.
``delete_task`` stays on ``schtasks.exe`` (name-only) — its tests are unchanged.
The dead ``query_task`` was replaced by the tri-state ``read_schedule`` (D4); its
parse-fixture tests live in ``TestReadSchedule`` below.
"""

import base64
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _argv(mock_run) -> list[str]:
    """The argv list passed to the (mocked) subprocess.run."""
    return mock_run.call_args[0][0]


def _ps_script(mock_run) -> str:
    """The PowerShell script, decoded from the ``-EncodedCommand`` argv value."""
    argv = _argv(mock_run)
    encoded = argv[argv.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


def _child_env(mock_run) -> dict:
    """The ``env=`` dict passed to subprocess.run."""
    return mock_run.call_args[1]["env"]


# -----------------------------------------------------------------------
# Windows scheduler tests — _build_register_script (the fixed PS string)
# -----------------------------------------------------------------------


class TestBuildRegisterScript:
    def test_password_variant_is_explicit_password_principal(self):
        from src.scheduler.windows import _build_register_script

        script = _build_register_script(has_password=True)
        # Explicit stored-password principal — not parameter-set inference.
        assert "New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER -LogonType Password" in script
        assert "-RunLevel $env:DSYNC_RUNLEVEL" in script
        # The credential is stored via -User/-Password on Register-ScheduledTask.
        assert "Register-ScheduledTask -TaskName $env:DSYNC_TASKNAME -InputObject $task" in script
        assert "-User $env:DSYNC_USER -Password $env:DSYNC_TASK_PW -Force" in script
        # S4U is never used.
        assert "S4U" not in script

    def test_no_password_variant_is_interactive_limited_not_s4u(self):
        from src.scheduler.windows import _build_register_script

        script = _build_register_script(has_password=False)
        assert "New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER -LogonType Interactive -RunLevel Limited" in script
        # No credential parameters, no password reference, no S4U.
        assert "-Password" not in script
        assert "DSYNC_TASK_PW" not in script
        assert "S4U" not in script

    def test_settings_parity_with_prior_xml(self):
        from src.scheduler.windows import _build_register_script

        for has_pw in (True, False):
            script = _build_register_script(has_password=has_pw)
            assert "-MultipleInstances IgnoreNew" in script
            assert "-ExecutionTimeLimit (New-TimeSpan -Hours 2)" in script
            assert "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries" in script
            assert "-StartWhenAvailable:$false" in script

    def test_run_time_parsed_with_invariant_culture(self):
        from src.scheduler.windows import _build_register_script

        script = _build_register_script(has_password=True)
        assert "[DateTime]::ParseExact($env:DSYNC_RUNTIME,'HH:mm'" in script
        assert "[System.Globalization.CultureInfo]::InvariantCulture" in script

    def test_script_references_env_not_literals(self):
        from src.scheduler.windows import _build_register_script

        script = _build_register_script(has_password=True)
        # Action/trigger/settings all read from env, never an interpolated value.
        assert "New-ScheduledTaskAction -Execute $env:DSYNC_EXE -Argument $env:DSYNC_ARGS" in script
        assert "-WorkingDirectory $env:DSYNC_WORKDIR" in script
        assert "New-ScheduledTaskTrigger -Daily -At $at" in script

    def test_catch_emits_plain_text_not_write_error(self):
        """The catch uses [Console]::Error.WriteLine — NOT Write-Error.

        Write-Error to a redirected stderr is CLIXML-serialized by PowerShell
        (a noisy ``#< CLIXML`` blob that echoes the whole script); the bare
        [Console]::Error.WriteLine emits a clean plain-text one-liner.
        """
        from src.scheduler.windows import _build_register_script

        for has_pw in (True, False):
            script = _build_register_script(has_password=has_pw)
            assert "[Console]::Error.WriteLine($_.Exception.Message)" in script
            assert "Write-Error" not in script
            # The success path is unchanged.
            assert "Write-Output 'DSYNC_OK'" in script
            assert "exit 1" in script


# -----------------------------------------------------------------------
# _clean_ps_stderr — de-CLIXML the PowerShell error surface
# -----------------------------------------------------------------------


# A real PowerShell 5.1 CLIXML stderr blob from a failed Register-ScheduledTask
# (Write-Error to a redirected stderr). It echoes the whole failing script and
# buries "Access is denied." inside <S S="Error"> nodes with _x000D_/_x000A_
# line breaks. _clean_ps_stderr must return ONLY the human message.
_REAL_CLIXML_STDERR = (
    "#< CLIXML\r\n"
    '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<S S="Error">Register-ScheduledTask : Access is denied._x000D__x000A_</S>'
    '<S S="Error">At line:9 char:3_x000D__x000A_</S>'
    '<S S="Error">+   Register-ScheduledTask -TaskName $env:DSYNC_TASKNAME -InputObject $task -User _x000D__x000A_</S>'
    '<S S="Error">+   $env:DSYNC_USER -Password $env:DSYNC_TASK_PW -Force | Out-Null_x000D__x000A_</S>'
    '<S S="Error">+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~_x000D__x000A_</S>'
    '<S S="Error">    + CategoryInfo          : PermissionDenied: (PS_ScheduledTask:Root/Microsoft/...) [Register-ScheduledTask], CimException_x000D__x000A_</S>'
    '<S S="Error">    + FullyQualifiedErrorId : HRESULT 0x80070005,Register-ScheduledTask_x000D__x000A_</S>'
    "</Objs>"
)


class TestCleanPsStderr:
    def test_clixml_blob_yields_clean_access_denied_no_leak(self):
        from src.scheduler.windows import _clean_ps_stderr

        cleaned = _clean_ps_stderr(_REAL_CLIXML_STDERR)

        # The human message survives ...
        assert "Access is denied" in cleaned
        # ... and the CLIXML noise / echoed script body / secret literal do not.
        assert "CLIXML" not in cleaned
        assert "<Objs" not in cleaned
        assert "Register-ScheduledTask -TaskName" not in cleaned
        assert "DSYNC_TASK_PW" not in cleaned
        assert "_x000D_" not in cleaned

    def test_clixml_strips_leading_positional_prefix(self):
        from src.scheduler.windows import _clean_ps_stderr

        cleaned = _clean_ps_stderr(_REAL_CLIXML_STDERR)
        # The "Register-ScheduledTask : " positional prefix is stripped.
        assert not cleaned.startswith("Register-ScheduledTask")

    def test_plain_text_passes_through_stripped(self):
        from src.scheduler.windows import _clean_ps_stderr

        assert _clean_ps_stderr("  Access is denied.  ") == "Access is denied."

    def test_clixml_without_error_node_is_safe_generic(self):
        from src.scheduler.windows import _clean_ps_stderr

        cleaned = _clean_ps_stderr("#< CLIXML\r\n<Objs></Objs>")
        # No error node → a safe generic message, never the raw blob.
        assert "CLIXML" not in cleaned
        assert "<Objs" not in cleaned

    def test_first_node_with_dsync_ref_is_dropped_not_echoed(self):
        """Defense-in-depth: if the script body collapses into node 0, drop it.

        Not a shape PS 5.1 emits (the message line is node 0; script echoes are
        nodes 1+), but the CLIXML branch is untrusted — a first node still
        carrying a ``DSYNC_*`` reference must yield the safe generic message,
        never echo the variable name.
        """
        from src.scheduler.windows import _clean_ps_stderr

        # Node 0 carries a DSYNC_ ref that SURVIVES the positional-prefix strip
        # (it sits after the "<cmd> : " separator), so the guard — not the prefix
        # regex — is what must drop it.
        blob = (
            "#< CLIXML\r\n"
            '<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
            '<S S="Error">Register-ScheduledTask : failed binding $env:DSYNC_TASK_PW</S></Objs>'
        )
        cleaned = _clean_ps_stderr(blob)
        assert "DSYNC_" not in cleaned
        assert "$env" not in cleaned

    def test_canonical_marker_still_matches_after_clean(self):
        """register_task de-CLIXMLs before the marker match — Access is denied survives.

        Pinned to the already-elevated DIRECT path (``is_elevated -> True``) so the
        password call exercises the ``subprocess.run`` CLIXML branch under test rather
        than the D5 self-elevation path (covered in test_scheduler_elevation.py).
        """
        from pathlib import Path

        from src.scheduler.windows import register_task

        with (
            patch("src.scheduler.windows.is_elevated", return_value=True),
            patch("src.scheduler.windows.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr=_REAL_CLIXML_STDERR)
            ok, msg = register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane",
                run_as_password="s3cr3t!",
            )
        assert ok is False
        assert "Access is denied" in msg
        # The cleaned message must not leak the script body or the secret literal.
        assert "CLIXML" not in msg
        assert "Register-ScheduledTask -TaskName" not in msg
        assert "DSYNC_TASK_PW" not in msg
        assert "s3cr3t!" not in msg


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


# -----------------------------------------------------------------------
# Windows scheduler tests — register_task (subprocess mocked)
# -----------------------------------------------------------------------


class TestWindowsRegisterTask:
    @patch("src.scheduler.windows.subprocess.run")
    def test_register_success(self, mock_run):
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is True
        mock_run.assert_called_once()
        argv = _argv(mock_run)
        # Fixed flags + an -EncodedCommand base64 blob (the script is not piped
        # via stdin — observed live on this dev box (Win11, PS 5.1), a multi-line
        # try/catch read line-by-line from stdin silently no-ops; -EncodedCommand
        # parses the script as one unit so the fail-loud try/catch works).
        assert argv[:6] == [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
        ]
        assert "input" not in mock_run.call_args[1]
        # The decoded script is the fixed PowerShell program.
        assert _ps_script(mock_run).startswith("$ErrorActionPreference")
        # Legacy schtasks registration markers must be gone.
        assert "schtasks" not in argv
        assert "/XML" not in argv
        assert "/TR" not in argv

    @patch("src.scheduler.windows.subprocess.run")
    def test_register_failure_passes_stderr_through(self, mock_run):
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access is denied.")

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is False
        assert "Access is denied" in msg

    @patch("src.scheduler.windows.subprocess.run")
    def test_register_success_requires_dsync_ok_sentinel(self, mock_run):
        """returncode 0 without the DSYNC_OK sentinel is treated as failure."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="something else", stderr="")

        ok, _ = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is False

    @patch("src.scheduler.windows.subprocess.run")
    def test_register_with_sftp_flag(self, mock_run):
        """The action arguments (in DSYNC_ARGS) carry --sftp when requested."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        assert "--sftp" in _child_env(mock_run)["DSYNC_ARGS"]

    @patch("src.scheduler.windows.subprocess.run")
    def test_registered_action_labels_runs_as_scheduled(self, mock_run):
        """The action args carry ``--source scheduled`` (D2c) — the ONLY thing that
        labels a nightly run as 'scheduled' in the run store (a scheduled task has no
        per-action env). Losing it silently relabels every nightly run as 'cli' and
        breaks Run History's manual-vs-scheduled distinction."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert "--source scheduled" in _child_env(mock_run)["DSYNC_ARGS"]

    @patch("src.scheduler.windows.is_elevated", return_value=True)
    @patch("src.scheduler.windows.subprocess.run")
    def test_registered_action_labels_runs_as_scheduled_with_password(self, mock_run, _mock_elevated):
        """The unattended (password/Highest) principal carries the same source label.

        Pinned to the already-elevated DIRECT path (``is_elevated -> True``); the
        non-elevated self-elevation path (D5) is covered in test_scheduler_elevation.py.
        """
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_password="secret-pw",
        )
        assert "--source scheduled" in _child_env(mock_run)["DSYNC_ARGS"]

    @patch("src.scheduler.windows.subprocess.run")
    def test_run_time_passed_raw_string_in_env(self, mock_run):
        """DSYNC_RUNTIME carries the raw 'HH:mm' string, not a (hour, minute) tuple."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="16:45",
        )
        assert _child_env(mock_run)["DSYNC_RUNTIME"] == "16:45"

    def test_register_rejects_invalid_sis_type(self):
        from src.scheduler.windows import register_task

        with pytest.raises(ValueError, match="Invalid SIS type"):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="bad;type",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
            )

    def test_register_rejects_invalid_task_name(self):
        from src.scheduler.windows import register_task

        with pytest.raises(ValueError, match="Invalid task name"):
            register_task(
                task_name="task/../../etc",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
            )

    def test_register_rejects_invalid_time(self):
        from src.scheduler.windows import register_task

        with pytest.raises(ValueError):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="25:99",
            )

    @patch("src.scheduler.windows.subprocess.run")
    def test_validation_runs_before_subprocess(self, mock_run):
        """Bad input must raise before any powershell call."""
        from src.scheduler.windows import register_task

        with pytest.raises(ValueError):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="bad;type",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
            )
        mock_run.assert_not_called()

    @patch("src.scheduler.windows.subprocess.run")
    def test_frozen_exe_invoked_directly(self, mock_run):
        """DistrictSync.exe is DSYNC_EXE; arguments carry no -m src.main; WD=exe parent."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        env = _child_env(mock_run)
        assert env["DSYNC_EXE"] == str(Path("C:/DistrictSync/DistrictSync.exe"))
        assert "-m src.main" not in env["DSYNC_ARGS"]
        assert env["DSYNC_WORKDIR"] == str(Path("C:/DistrictSync/DistrictSync.exe").parent)

    @patch("src.scheduler.windows.subprocess.run")
    def test_python_source_mode_uses_m_flag(self, mock_run):
        """Running from source via python.exe sets DSYNC_EXE=python, DSYNC_ARGS=-m src.main ...

        Without -m, Python treats --sis as a script path and exits with
        ERROR_FILE_NOT_FOUND (0x80070002). Working directory = project root.
        """
        from src.scheduler import windows
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/Python313/python.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        env = _child_env(mock_run)
        assert env["DSYNC_EXE"] == str(Path("C:/Python313/python.exe"))
        assert "-m src.main" in env["DSYNC_ARGS"]
        assert "--sis myedbc" in env["DSYNC_ARGS"]
        assert "--sftp" in env["DSYNC_ARGS"]
        assert "cmd /c" not in env["DSYNC_ARGS"]
        assert "cd /d" not in env["DSYNC_ARGS"]
        expected_root = Path(windows.__file__).resolve().parents[2]
        assert env["DSYNC_WORKDIR"] == str(expected_root)

    @patch("src.scheduler.windows.subprocess.run")
    def test_space_bearing_path_is_quoted_in_args(self, mock_run):
        """A space-bearing district path is wrapped in quotes inside DSYNC_ARGS."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")
        in_dir = Path("C:/A & B/in dir")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=in_dir,
            output_dir=Path("C:/out"),
            run_time="03:00",
        )
        assert f'"{in_dir}"' in _child_env(mock_run)["DSYNC_ARGS"]

    @patch("src.scheduler.windows.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_powershell_is_actionable(self, _mock_run):
        """No powershell.exe → a distinct actionable message, no crash."""
        from src.scheduler.windows import register_task

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is False
        assert "PowerShell not found" in msg

    @patch("src.scheduler.windows.subprocess.run")
    def test_missing_scheduledtasks_module_is_actionable(self, mock_run):
        """A cmdlet-not-found PS error maps to the ScheduledTasks-module message."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Register-ScheduledTask : The term 'Register-ScheduledTask' is not "
            "recognized as the name of a cmdlet, function, script file, or operable program.",
        )

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is False
        assert "ScheduledTasks module not available" in msg


class TestWindowsDeleteTask:
    @patch("src.scheduler.windows.subprocess.run")
    def test_delete_success(self, mock_run):
        from src.scheduler.windows import delete_task

        mock_run.return_value = MagicMock(returncode=0, stdout="SUCCESS", stderr="")

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is True
        args = mock_run.call_args[0][0]
        assert "/Delete" in args

    @patch("src.scheduler.windows.subprocess.run")
    def test_delete_failure(self, mock_run):
        from src.scheduler.windows import delete_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Not found")

        ok, msg = delete_task("DistrictSync_Daily")
        assert ok is False


class TestReadSchedule:
    """D4 tri-state read-back parse fixtures — found / definitively-absent / query-failed.

    The load-bearing contract: the cmdlet's task-not-found → ``found=False`` (MISSING),
    but ANY other failure (denied, timeout, PowerShell missing, non-Windows) → ``found=None``
    (UNKNOWN), never a false "absent". Datetimes ride the invariant ISO round-trip verbatim.
    """

    _FOUND_JSON = (
        'DSYNC_FOUND:{"found":true,"next_run":"2026-07-09T03:00:00.0000000",'
        '"last_run":"2026-07-08T03:00:00.0000000","last_result":0,'
        '"action_path":"C:\\\\Program Files\\\\DistrictSync\\\\DistrictSync.exe"}'
    )

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_found_task_parses_all_fields(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(returncode=0, stdout=self._FOUND_JSON, stderr="")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is True
        # Datetimes are passed through as the raw invariant ISO round-trip strings.
        assert rb.next_run == "2026-07-09T03:00:00.0000000"
        assert rb.last_run == "2026-07-08T03:00:00.0000000"
        assert rb.last_result == 0
        assert rb.action_path.endswith("DistrictSync.exe")

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_the_read_script_references_only_the_env_name(self, mock_run):
        # Same injection-free hardening as registration: no dynamic interpolation, name via env.
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_ABSENT", stderr="")
        read_schedule("DistrictSync_Daily")
        script = _ps_script(mock_run)
        assert "$env:DSYNC_TASKNAME" in script
        assert "DistrictSync_Daily" not in script  # the name is NEVER baked into the script text
        assert mock_run.call_args[1]["env"]["DSYNC_TASKNAME"] == "DistrictSync_Daily"
        # The read-back subprocess is bounded by a timeout (a hung PowerShell can't freeze the UI).
        assert mock_run.call_args[1]["timeout"] == 10

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_definitively_absent_is_found_false(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_ABSENT", stderr="")
        rb = read_schedule("NonExistent")
        assert rb.found is False  # MISSING — the only state that may claim "not scheduled"

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_access_denied_is_unknown_not_absent(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access is denied.")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None  # a failed query is NEVER reported as absent
        assert rb.error

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_timeout_is_unknown(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="powershell", timeout=10)
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert "timed out" in (rb.error or "").lower()

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_powershell_missing_is_unknown(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.side_effect = FileNotFoundError()
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None
        assert "PowerShell not found" in (rb.error or "")

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_malformed_json_degrades_to_unknown(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_FOUND:{not json", stderr="")
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is None  # can't parse a shape → UNKNOWN, never a false claim

    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.windows.subprocess.run")
    def test_never_run_task_has_null_last_run(self, mock_run):
        from src.scheduler.windows import read_schedule

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='DSYNC_FOUND:{"found":true,"next_run":"2026-07-09T03:00:00.0000000",'
            '"last_run":null,"last_result":267011,"action_path":"C:\\\\x\\\\DistrictSync.exe"}',
            stderr="",
        )
        rb = read_schedule("DistrictSync_Daily")
        assert rb.found is True
        assert rb.last_run is None  # the never-run sentinel is nulled in the script

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
