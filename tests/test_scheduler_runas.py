"""Tests for the Windows scheduler run-as / password-redaction behaviour.

Covers the unattended-SFTP-schedule hardening, re-expressed against the
Task Scheduler XML registration model:
  - register_task passes /RU <user> /RP <pw> on the command line when a
    password is supplied (the run level now lives in the XML's <RunLevel>,
    not in a /RL flag)
  - register_task stays backward compatible without a password (no /RU /RP)
  - the run-as password is never written to logs (redacted to ***) nor to the
    generated XML
  - current_run_as_user() resolution + fallback
  - validate_run_as_user() accepts DOMAIN\\user / user, rejects bad input

All subprocess calls are mocked — no OS scheduler interaction needed.
"""

import logging
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
# register_task with a run-as password
# -----------------------------------------------------------------------


class TestRegisterTaskRunAs:
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_emits_ru_rp_and_xml_highest(self, mock_run):
        """A supplied password adds /RU <user> /RP <pw>; HighestAvailable lives in the XML."""
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            with open(_xml_arg(args), encoding="utf-16") as fh:
                captured["xml"] = fh.read()
            return MagicMock(returncode=0, stdout="SUCCESS", stderr="")

        mock_run.side_effect = _capture

        ok, _ = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="CORP\\jane",
            run_as_password="s3cr3t!",
        )

        assert ok is True
        args = mock_run.call_args[0][0]
        # XML registration; the run level is NOT a /RL command flag any more.
        assert "/XML" in args
        assert "/RL" not in args
        # /RU is immediately followed by the resolved user.
        assert args[args.index("/RU") + 1] == "CORP\\jane"
        # /RP is immediately followed by the raw password.
        assert args[args.index("/RP") + 1] == "s3cr3t!"
        # No /IT — that would force logged-on-only and defeat the purpose.
        assert "/IT" not in args
        # The XML carries Password logon + HighestAvailable; never the password.
        root = ET.fromstring(captured["xml"])  # nosec B314
        assert root.find(".//t:Principal/t:LogonType", _NS).text == "Password"
        assert root.find(".//t:Principal/t:RunLevel", _NS).text == "HighestAvailable"
        assert root.find(".//t:Principal/t:UserId", _NS).text == "CORP\\jane"
        assert "s3cr3t!" not in captured["xml"]

    @patch("src.scheduler.windows.current_run_as_user", return_value="WORKGROUP\\bob")
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_without_user_resolves_current_user(self, mock_run, _mock_user):
        """When run_as_user is omitted, the current user is resolved and used."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_password="pw123",
        )

        args = mock_run.call_args[0][0]
        assert args[args.index("/RU") + 1] == "WORKGROUP\\bob"

    @patch("src.scheduler.windows.subprocess.run")
    def test_run_highest_false_uses_least_privilege_in_xml(self, mock_run):
        """run_highest=False keeps /RU /RP and sets <RunLevel>LeastPrivilege in the XML."""
        from src.scheduler.windows import register_task

        captured: dict[str, str] = {}

        def _capture(args, **_kwargs):
            with open(_xml_arg(args), encoding="utf-16") as fh:
                captured["xml"] = fh.read()
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = _capture

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="jane",
            run_as_password="pw123",
            run_highest=False,
        )

        args = mock_run.call_args[0][0]
        assert "/RU" in args
        assert "/RP" in args
        assert "/RL" not in args
        root = ET.fromstring(captured["xml"])  # nosec B314
        assert root.find(".//t:Principal/t:RunLevel", _NS).text == "LeastPrivilege"

    @patch("src.scheduler.windows.subprocess.run")
    def test_invalid_run_as_user_raises(self, mock_run):
        """A shell-metacharacter user is rejected before any subprocess call."""
        from src.scheduler.windows import register_task

        with pytest.raises(ValueError, match="Invalid run-as user"):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane && calc",
                run_as_password="pw123",
            )
        mock_run.assert_not_called()

    @patch("src.scheduler.windows.subprocess.run")
    def test_failure_surfaces_stderr(self, mock_run):
        """A non-zero schtasks (e.g. wrong password) returns the stderr text."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="ERROR: The user name or password is incorrect."
        )

        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="jane",
            run_as_password="wrongpw",
        )
        assert ok is False
        assert "password is incorrect" in msg


# -----------------------------------------------------------------------
# Backward compatibility — no password supplied
# -----------------------------------------------------------------------


class TestRegisterTaskBackwardCompat:
    @patch("src.scheduler.windows.subprocess.run")
    def test_no_password_omits_runas_flags(self, mock_run):
        """Default call (no password) emits no /RU /RP /RL — unchanged behaviour."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
        )

        args = mock_run.call_args[0][0]
        assert "/RU" not in args
        assert "/RP" not in args
        assert "/RL" not in args
        # The base command still starts the same way; registration is via /XML.
        assert args[:3] == ["schtasks", "/Create", "/F"]
        assert "/XML" in args


# -----------------------------------------------------------------------
# Password redaction in logs
# -----------------------------------------------------------------------


class TestPasswordRedaction:
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_not_in_logs_on_success(self, mock_run, caplog):
        """The raw password must never appear in captured logs; *** must."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        secret = "SuperSecretPw!42"

        with caplog.at_level(logging.DEBUG, logger="src.scheduler.windows"):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane",
                run_as_password=secret,
            )

        assert secret not in caplog.text
        assert "***" in caplog.text

    @patch("src.scheduler.windows.subprocess.run")
    def test_password_not_in_logs_on_failure(self, mock_run, caplog):
        """Even on failure, the error log redacts the password to ***."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access denied")
        secret = "AnotherSecret#99"

        with caplog.at_level(logging.DEBUG, logger="src.scheduler.windows"):
            register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane",
                run_as_password=secret,
            )

        assert secret not in caplog.text
        assert "***" in caplog.text

    def test_redact_cmd_masks_value_after_rp(self):
        """_redact_cmd replaces only the token after /RP."""
        from src.scheduler.windows import _redact_cmd

        cmd = ["schtasks", "/RU", "jane", "/RP", "hunter2", "/RL", "HIGHEST"]
        out = _redact_cmd(cmd)
        assert "hunter2" not in out
        assert "/RP ***" in out
        # /RU value and everything else stay intact.
        assert "jane" in out
        assert "HIGHEST" in out

    def test_redact_cmd_noop_without_rp(self):
        """A command with no /RP is joined unchanged."""
        from src.scheduler.windows import _redact_cmd

        cmd = ["schtasks", "/Create", "/F", "/TN", "DistrictSync_Daily"]
        assert _redact_cmd(cmd) == "schtasks /Create /F /TN DistrictSync_Daily"


# -----------------------------------------------------------------------
# current_run_as_user resolution
# -----------------------------------------------------------------------


class TestCurrentRunAsUser:
    def test_uses_domain_and_username(self):
        from src.scheduler.windows import current_run_as_user

        with patch.dict("os.environ", {"USERDOMAIN": "CORP", "USERNAME": "jane"}, clear=False):
            assert current_run_as_user() == "CORP\\jane"

    @patch("src.scheduler.windows.getpass.getuser", return_value="fallback_user")
    def test_falls_back_to_getpass_when_vars_missing(self, _mock_getuser):
        from src.scheduler.windows import current_run_as_user

        env = {k: v for k, v in __import__("os").environ.items() if k not in ("USERDOMAIN", "USERNAME")}
        with patch.dict("os.environ", env, clear=True):
            assert current_run_as_user() == "fallback_user"

    @patch("src.scheduler.windows.getpass.getuser", return_value="fallback_user")
    def test_falls_back_when_vars_empty(self, _mock_getuser):
        from src.scheduler.windows import current_run_as_user

        with patch.dict("os.environ", {"USERDOMAIN": "", "USERNAME": ""}, clear=False):
            assert current_run_as_user() == "fallback_user"


# -----------------------------------------------------------------------
# validate_run_as_user
# -----------------------------------------------------------------------


class TestValidateRunAsUser:
    def test_accepts_domain_user(self):
        from src.utils.validators import validate_run_as_user

        assert validate_run_as_user("CORP\\jane") == "CORP\\jane"

    def test_accepts_bare_user(self):
        from src.utils.validators import validate_run_as_user

        assert validate_run_as_user("jane") == "jane"

    def test_accepts_dotted_and_hyphenated(self):
        from src.utils.validators import validate_run_as_user

        assert validate_run_as_user("nw-domain\\jane.doe_01") == "nw-domain\\jane.doe_01"

    def test_strips_whitespace(self):
        from src.utils.validators import validate_run_as_user

        assert validate_run_as_user("  CORP\\jane  ") == "CORP\\jane"

    def test_rejects_shell_metacharacters(self):
        from src.utils.validators import validate_run_as_user

        with pytest.raises(ValueError, match="Invalid run-as user"):
            validate_run_as_user("jane && calc")

    def test_rejects_internal_whitespace(self):
        from src.utils.validators import validate_run_as_user

        with pytest.raises(ValueError, match="Invalid run-as user"):
            validate_run_as_user("a b")

    def test_rejects_empty(self):
        from src.utils.validators import validate_run_as_user

        with pytest.raises(ValueError, match="must not be empty"):
            validate_run_as_user("")

    def test_rejects_double_backslash(self):
        from src.utils.validators import validate_run_as_user

        with pytest.raises(ValueError, match="Invalid run-as user"):
            validate_run_as_user("CORP\\\\jane")

    def test_rejects_too_long(self):
        from src.utils.validators import validate_run_as_user

        with pytest.raises(ValueError, match="too long"):
            validate_run_as_user("a" * 257)
