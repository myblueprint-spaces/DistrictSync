"""Tests for src/scheduler/windows.py and src/scheduler/linux.py.

All subprocess calls are mocked — no OS scheduler interaction needed.

Windows registration uses Task Scheduler XML (``schtasks /Create /XML``),
so these tests assert against the generated XML document and the
``/XML`` command form rather than a legacy inline ``/TR`` string.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest

# Task Scheduler XML namespace used by all generated documents.
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml_arg(args: list[str]) -> str:
    """Return the temp-XML path that follows ``/XML`` in a schtasks arg list."""
    return args[args.index("/XML") + 1]


# -----------------------------------------------------------------------
# Windows scheduler tests — _build_task_xml
# -----------------------------------------------------------------------


class TestBuildTaskXML:
    def _parse(self, xml: str) -> ET.Element:
        return ET.fromstring(xml)  # nosec B314 — parsing our own generated XML

    def test_well_formed_and_namespaced(self):
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
        )
        root = self._parse(xml)
        assert root.tag == "{http://schemas.microsoft.com/windows/2004/02/mit/task}Task"
        assert root.attrib["version"] == "1.2"
        assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')

    def test_daily_trigger_with_run_time(self):
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "16:45",
        )
        root = self._parse(xml)
        start = root.find(".//t:CalendarTrigger/t:StartBoundary", _NS)
        assert start is not None
        assert start.text is not None and start.text.endswith("T16:45:00")
        days = root.find(".//t:ScheduleByDay/t:DaysInterval", _NS)
        assert days is not None and days.text == "1"

    def test_python_source_mode(self):
        """python.exe → Command=python, Arguments has -m src.main, WD=project root, no cmd /c."""
        from src.scheduler import windows
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/Python313/python.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            True,
            "03:00",
        )
        root = self._parse(xml)
        command = root.find(".//t:Exec/t:Command", _NS)
        arguments = root.find(".//t:Exec/t:Arguments", _NS)
        working_dir = root.find(".//t:Exec/t:WorkingDirectory", _NS)
        assert command is not None and command.text == str(Path("C:/Python313/python.exe"))
        assert arguments is not None and arguments.text is not None
        assert "-m src.main" in arguments.text
        assert "--sis myedbc" in arguments.text
        assert "--sftp" in arguments.text
        assert "cmd /c" not in arguments.text
        assert "cd /d" not in arguments.text
        expected_root = Path(windows.__file__).resolve().parents[2]
        assert working_dir is not None and working_dir.text == str(expected_root)

    def test_frozen_mode(self):
        """Frozen exe → Command=exe, Arguments has no -m, WD=exe parent."""
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
        )
        root = self._parse(xml)
        command = root.find(".//t:Exec/t:Command", _NS)
        arguments = root.find(".//t:Exec/t:Arguments", _NS)
        working_dir = root.find(".//t:Exec/t:WorkingDirectory", _NS)
        assert command is not None and command.text == str(Path("C:/DistrictSync/DistrictSync.exe"))
        assert arguments is not None and arguments.text is not None
        assert "-m" not in arguments.text.split()
        assert "src.main" not in arguments.text
        assert "--sftp" not in arguments.text
        assert working_dir is not None and working_dir.text == str(Path("C:/DistrictSync/DistrictSync.exe").parent)

    def test_sftp_absent_when_flag_false(self):
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
        )
        arguments = self._parse(xml).find(".//t:Exec/t:Arguments", _NS)
        assert arguments is not None and arguments.text is not None
        assert "--sftp" not in arguments.text

    def test_password_mode_highest(self):
        """Password + run_highest=True → LogonType Password, RunLevel HighestAvailable."""
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
            run_as_user="CORP\\jane",
            run_as_password="s3cr3t!",
            run_highest=True,
        )
        root = self._parse(xml)
        assert root.find(".//t:Principal/t:UserId", _NS).text == "CORP\\jane"
        assert root.find(".//t:Principal/t:LogonType", _NS).text == "Password"
        assert root.find(".//t:Principal/t:RunLevel", _NS).text == "HighestAvailable"
        # The password is never written into the XML.
        assert "s3cr3t!" not in xml

    def test_password_mode_least_privilege(self):
        """Password + run_highest=False → RunLevel LeastPrivilege."""
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
            run_as_user="jane",
            run_as_password="pw123",
            run_highest=False,
        )
        root = ET.fromstring(xml)  # nosec B314
        assert root.find(".//t:Principal/t:LogonType", _NS).text == "Password"
        assert root.find(".//t:Principal/t:RunLevel", _NS).text == "LeastPrivilege"

    @patch("src.scheduler.windows.current_run_as_user", return_value="WORKGROUP\\bob")
    def test_no_password_mode(self, _mock_user):
        """No password → InteractiveToken, LeastPrivilege, current user."""
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
        )
        root = ET.fromstring(xml)  # nosec B314
        assert root.find(".//t:Principal/t:UserId", _NS).text == "WORKGROUP\\bob"
        assert root.find(".//t:Principal/t:LogonType", _NS).text == "InteractiveToken"
        assert root.find(".//t:Principal/t:RunLevel", _NS).text == "LeastPrivilege"

    @patch("src.scheduler.windows.current_run_as_user", return_value="HOST\\setupuser")
    def test_password_without_user_resolves_current_user(self, _mock_user):
        from src.scheduler.windows import _build_task_xml

        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            Path("C:/input"),
            Path("C:/output"),
            False,
            "03:00",
            run_as_password="pw123",
        )
        root = ET.fromstring(xml)  # nosec B314
        assert root.find(".//t:Principal/t:UserId", _NS).text == "HOST\\setupuser"

    def test_xml_escapes_special_chars_in_path(self):
        """A path with '&' and a space is escaped/quoted and the doc still parses."""
        from src.scheduler.windows import _build_task_xml

        in_dir = Path("C:/A & B/in dir")
        xml = _build_task_xml(
            Path("C:/DistrictSync/DistrictSync.exe"),
            "myedbc",
            in_dir,
            Path("C:/out"),
            False,
            "03:00",
        )
        # The raw '&' must be escaped in the serialized document.
        assert "&amp;" in xml
        assert "A & B" not in xml  # unescaped ampersand must not appear
        # And it still parses; the parser un-escapes back to the real path.
        root = ET.fromstring(xml)  # nosec B314
        arguments = root.find(".//t:Exec/t:Arguments", _NS)
        assert arguments is not None and arguments.text is not None
        # The space-bearing path is wrapped in quotes inside the single command line.
        assert f'"{in_dir}"' in arguments.text


# -----------------------------------------------------------------------
# Windows scheduler tests — register_task (subprocess mocked)
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
        # XML registration — never an inline /TR command.
        assert "/XML" in args
        assert "/TR" not in args
        assert "/SC" not in args
        assert "/ST" not in args

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
        """The generated XML (passed via /XML) carries --sftp; temp file is removed after."""
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            # Read the temp XML while it still exists (before the finally removes it).
            with open(_xml_arg(args), encoding="utf-16") as fh:
                captured["xml"] = fh.read()
            captured["path"] = _xml_arg(args)
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = _capture

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        assert "--sftp" in captured["xml"]
        # Temp XML is cleaned up after the call.
        assert not os.path.exists(captured["path"])

    @patch("src.scheduler.windows.subprocess.run")
    def test_temp_xml_removed_on_failure(self, mock_run):
        """Even when schtasks fails, the temp XML is removed."""
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            captured["path"] = _xml_arg(args)
            return MagicMock(returncode=1, stdout="", stderr="boom")

        mock_run.side_effect = _capture

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        assert not os.path.exists(captured["path"])

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
        """Bad input must raise before any schtasks call."""
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
        """DistrictSync.exe is the <Command>; arguments carry no -m src.main."""
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            with open(_xml_arg(args), encoding="utf-16") as fh:
                captured["xml"] = fh.read()
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = _capture
        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )
        root = ET.fromstring(captured["xml"])  # nosec B314
        command = root.find(".//t:Exec/t:Command", _NS)
        arguments = root.find(".//t:Exec/t:Arguments", _NS)
        assert command is not None and command.text == str(Path("C:/DistrictSync/DistrictSync.exe"))
        assert arguments is not None and arguments.text is not None
        assert "-m src.main" not in arguments.text

    @patch("src.scheduler.windows.subprocess.run")
    def test_python_source_mode_uses_m_flag(self, mock_run):
        """Running from source via python.exe sets Command=python and Arguments=-m src.main ...

        Without -m, Python treats --sis as a script path and exits with
        ERROR_FILE_NOT_FOUND (0x80070002) — the original dev bug. With XML the
        working directory is set natively (no cmd /c "cd /d ...").
        """
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            with open(_xml_arg(args), encoding="utf-16") as fh:
                captured["xml"] = fh.read()
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = _capture
        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/Python313/python.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            sftp=True,
        )
        root = ET.fromstring(captured["xml"])  # nosec B314
        command = root.find(".//t:Exec/t:Command", _NS)
        arguments = root.find(".//t:Exec/t:Arguments", _NS)
        assert command is not None and command.text == str(Path("C:/Python313/python.exe"))
        assert arguments is not None and arguments.text is not None
        assert "-m src.main" in arguments.text
        assert "--sis myedbc" in arguments.text
        assert "--sftp" in arguments.text
        assert "cmd /c" not in arguments.text
        assert "cd /d" not in arguments.text


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
