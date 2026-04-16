"""Tests for src/scheduler/windows.py and src/scheduler/linux.py.

All subprocess calls are mocked — no OS scheduler interaction needed.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# -----------------------------------------------------------------------
# Windows scheduler tests
# -----------------------------------------------------------------------


class TestWindowsRegisterTask:
    @patch("src.scheduler.windows.subprocess.run")
    def test_register_success(self, mock_run):
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="SUCCESS", stderr="")

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is True
        assert "SUCCESS" in msg
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "schtasks"
        assert "/Create" in args
        assert "DistrictSync_Daily" in args

    @patch("src.scheduler.windows.subprocess.run")
    def test_register_failure(self, mock_run):
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access denied")

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert ok is False
        assert "Access denied" in msg

    @patch("src.scheduler.windows.subprocess.run")
    def test_register_with_sftp_flag(self, mock_run):
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        # The /TR argument should contain --sftp
        args = mock_run.call_args[0][0]
        tr_idx = args.index("/TR")
        task_run = args[tr_idx + 1]
        assert "--sftp" in task_run

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
    def test_frozen_exe_invoked_directly(self, mock_run):
        """DistrictSync.exe must be invoked without 'cmd /c' wrapping."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        args = mock_run.call_args[0][0]
        tr = args[args.index("/TR") + 1]
        assert not tr.startswith("cmd /c")
        assert tr.startswith('"C:\\DistrictSync\\DistrictSync.exe"') or tr.startswith(
            '"C:/DistrictSync/DistrictSync.exe"'
        )

    @patch("src.scheduler.windows.subprocess.run")
    def test_python_source_mode_wraps_with_cmd_and_m_flag(self, mock_run):
        """Running from source via python.exe must use 'cmd /c cd ... && python -m src.main ...'.

        Without -m, Python treats --sis as a script path and exits with
        ERROR_FILE_NOT_FOUND (0x80070002) — this is the bug that broke
        the scheduled task in dev.
        """
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/Python313/python.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        args = mock_run.call_args[0][0]
        tr = args[args.index("/TR") + 1]
        assert tr.startswith("cmd /c")
        assert "cd /d" in tr
        assert "-m src.main" in tr
        assert "--sis myedbc" in tr
        assert "--sftp" in tr


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


class TestWindowsQueryTask:
    @patch("src.scheduler.windows.subprocess.run")
    def test_query_existing_task(self, mock_run):
        from src.scheduler.windows import query_task

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Task Name: DistrictSync_Daily\nStatus: Ready\nNext Run Time: 03:00\nLast Result: 0\n",
            stderr="",
        )

        info = query_task("DistrictSync_Daily")
        assert info["exists"] is True
        assert "status" in info

    @patch("src.scheduler.windows.subprocess.run")
    def test_query_nonexistent_task(self, mock_run):
        from src.scheduler.windows import query_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="ERROR: The system cannot find the file")

        info = query_task("NonExistent")
        assert info["exists"] is False
        assert info["status"] == "Not Found"


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
