"""Tests for the Windows scheduler run-as / credential-handling behaviour.

Covers the unattended-SFTP-schedule hardening against the PowerShell
``Register-ScheduledTask`` registration model:
  - the run-as password is passed to PowerShell ONLY via the child process
    environment (``DSYNC_TASK_PW``) — never on argv, never in the (decoded)
    script body, never logged, and never injected by ``register_task`` into the
    message returned to the caller. NOTE: PowerShell's own error text is passed
    through **unscrubbed** — the leak tests prove ``register_task`` does not
    inject the secret (it never logs the child env / script and the value is not
    in argv), NOT that PowerShell's exception text can never carry it.
  - the child env is a FRESH copy of ``os.environ`` (``os.environ`` is never
    mutated; ``DSYNC_TASK_PW`` does not leak into the parent process)
  - password path → explicit ``-LogonType Password`` principal + ``Highest`` /
    ``Limited`` run level; no-password path → ``Interactive`` / ``Limited``
    (never ``S4U``), and ``run_highest`` stays ignored without a password
  - current_run_as_user() resolution + fallback
  - validate_run_as_user() accepts DOMAIN\\user / user, rejects bad input

The script is handed to ``powershell.exe -EncodedCommand`` (UTF-16LE-base64),
so the "script body" is decoded from the argv blob rather than read from stdin.

All subprocess calls are mocked — no OS scheduler interaction needed.
"""

import base64
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _already_elevated():
    """Pin these run-as tests to the DIRECT (already-elevated) registration path.

    Plan 0029 D5 added per-operation self-elevation: an unattended (password) register
    from a NON-elevated process now runs behind a UAC prompt via ``ShellExecuteEx``
    instead of the direct ``subprocess.run``. This whole file asserts the direct
    ``Register-ScheduledTask`` mechanics (env/script/principal), which is exactly the
    already-elevated (or in-elevated-child) path — so force ``is_elevated() -> True`` so
    the branch is the one under test, on any host (Windows dev or Linux CI).
    """
    with patch("src.scheduler.windows.is_elevated", return_value=True):
        yield


def _argv(mock_run) -> list[str]:
    return mock_run.call_args[0][0]


def _ps_script(mock_run) -> str:
    """The PowerShell script, decoded from the ``-EncodedCommand`` argv value."""
    argv = _argv(mock_run)
    encoded = argv[argv.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


def _child_env(mock_run) -> dict:
    return mock_run.call_args[1]["env"]


# -----------------------------------------------------------------------
# register_task with a run-as password (unattended path)
# -----------------------------------------------------------------------


class TestRegisterTaskRunAs:
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_in_env_not_argv_or_script(self, mock_run):
        """The password reaches PowerShell only via DSYNC_TASK_PW in the child env."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")
        secret = "s3cr3t!"

        ok, _ = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="CORP\\jane",
            run_as_password=secret,
        )

        assert ok is True
        # Password is nowhere in argv (incl. the base64 -EncodedCommand blob).
        assert secret not in " ".join(_argv(mock_run))
        # The decoded script references the env var but never the literal password.
        script = _ps_script(mock_run)
        assert "$env:DSYNC_TASK_PW" in script
        assert secret not in script
        # The child env carries the password and the resolved user.
        env = _child_env(mock_run)
        assert env["DSYNC_TASK_PW"] == secret
        assert env["DSYNC_USER"] == "CORP\\jane"

    @patch("src.scheduler.windows.subprocess.run")
    def test_password_bearing_child_is_the_absolute_system32_powershell(self, mock_run, monkeypatch):
        """The process that RECEIVES the password in its env is pinned by absolute path.

        This is the whole point of the run-as hardening: ``_build_env`` hands the child
        ``DSYNC_TASK_PW``. Resolving argv[0] by bare name would let ``CreateProcess``'s
        search order (calling-exe dir and CWD before ``System32``, absent
        ``SafeProcessSearchMode``) substitute a planted ``powershell.exe`` — which would
        then be handed the district Windows account password. Keeping the password off
        argv is worth nothing if the *binary* can be swapped.
        """
        from src.scheduler.windows import register_task

        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")
        secret = "s3cr3t!"

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="CORP\\jane",
            run_as_password=secret,
        )

        argv = _argv(mock_run)
        assert argv[0] == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        assert argv[0] != "powershell"
        # ... and the password is still env-only, never on argv (the pre-existing guarantee).
        assert _child_env(mock_run)["DSYNC_TASK_PW"] == secret
        assert secret not in " ".join(argv)

    @patch("src.scheduler.windows.subprocess.run")
    def test_password_path_is_explicit_password_principal_highest(self, mock_run):
        """Password + run_highest=True → explicit -LogonType Password, RunLevel Highest."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="CORP\\jane",
            run_as_password="s3cr3t!",
            run_highest=True,
        )

        script = _ps_script(mock_run)
        assert "-LogonType Password" in script
        assert "-User $env:DSYNC_USER -Password $env:DSYNC_TASK_PW -Force" in script
        assert "S4U" not in script
        # Run level is driven by the env var, set to Highest.
        assert "-RunLevel $env:DSYNC_RUNLEVEL" in script
        assert _child_env(mock_run)["DSYNC_RUNLEVEL"] == "Highest"

    @patch("src.scheduler.windows.current_run_as_user", return_value="WORKGROUP\\bob")
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_without_user_resolves_current_user(self, mock_run, _mock_user):
        """When run_as_user is omitted, the current user is resolved into DSYNC_USER."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_password="pw123",
        )

        assert _child_env(mock_run)["DSYNC_USER"] == "WORKGROUP\\bob"

    @patch("src.scheduler.windows.subprocess.run")
    def test_run_highest_false_with_password_is_limited(self, mock_run):
        """run_highest=False (with password) → RunLevel Limited via the env var."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

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

        env = _child_env(mock_run)
        assert env["DSYNC_RUNLEVEL"] == "Limited"
        assert "-LogonType Password" in _ps_script(mock_run)

    @patch("src.scheduler.windows.subprocess.run")
    def test_child_env_is_fresh_copy_os_environ_unchanged(self, mock_run):
        """The child env is a fresh copy — os.environ never carries DSYNC_TASK_PW."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")
        assert "DSYNC_TASK_PW" not in os.environ  # pre-condition

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="jane",
            run_as_password="leaky-pw",
        )

        # The child env got the password ...
        assert _child_env(mock_run)["DSYNC_TASK_PW"] == "leaky-pw"
        # ... but os.environ is untouched (no DSYNC_* leaked into the parent).
        assert "DSYNC_TASK_PW" not in os.environ
        assert "DSYNC_TASKNAME" not in os.environ
        assert "DSYNC_USER" not in os.environ

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
    def test_failure_surfaces_stderr_verbatim(self, mock_run):
        """A non-zero PowerShell run (e.g. wrong password) returns the stderr text."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="The user name or password is incorrect.")

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
# Backward compatibility — no password supplied (logged-on-only path)
# -----------------------------------------------------------------------


class TestRegisterTaskBackwardCompat:
    @patch("src.scheduler.windows.subprocess.run")
    def test_no_password_is_interactive_limited_no_password_env(self, mock_run):
        """Default call (no password) → Interactive/Limited principal, no DSYNC_TASK_PW."""
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

        script = _ps_script(mock_run)
        assert "-LogonType Interactive -RunLevel Limited" in script
        assert "-Password" not in script
        assert "S4U" not in script
        # No password / run-level env vars on the no-password path.
        env = _child_env(mock_run)
        assert "DSYNC_TASK_PW" not in env
        assert "DSYNC_RUNLEVEL" not in env

    @patch("src.scheduler.windows.subprocess.run")
    def test_run_highest_true_without_password_still_limited(self, mock_run):
        """run_highest=True with NO password stays Limited (run_highest ignored)."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")

        register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_highest=True,
        )

        script = _ps_script(mock_run)
        # The fixed no-password script hardcodes Limited; run_highest does nothing.
        assert "-LogonType Interactive -RunLevel Limited" in script
        assert "Highest" not in script
        assert "DSYNC_RUNLEVEL" not in _child_env(mock_run)


# -----------------------------------------------------------------------
# Password / secret leak closure (success AND failure paths)
# -----------------------------------------------------------------------


class TestPasswordLeakClosure:
    @patch("src.scheduler.windows.subprocess.run")
    def test_password_absent_from_logs_and_message_on_success(self, mock_run, caplog):
        """On success neither the password value nor DSYNC_TASK_PW reaches logs/message."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=0, stdout="DSYNC_OK", stderr="")
        secret = "SuperSecretPw!42"

        with caplog.at_level(logging.DEBUG, logger="src.scheduler.windows"):
            ok, msg = register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane",
                run_as_password=secret,
            )

        assert ok is True
        assert secret not in caplog.text
        assert "DSYNC_TASK_PW" not in caplog.text
        assert secret not in msg
        assert "DSYNC_TASK_PW" not in msg

    @patch("src.scheduler.windows.subprocess.run")
    def test_password_absent_from_logs_and_message_on_failure(self, mock_run, caplog):
        """On a PS failure with a benign stderr, register_task adds neither the password value nor DSYNC_TASK_PW to the returned message or caplog (guards against logging child_env / the script)."""
        from src.scheduler.windows import register_task

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access is denied.")
        secret = "AnotherSecret#99"

        with caplog.at_level(logging.DEBUG, logger="src.scheduler.windows"):
            ok, msg = register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("C:/DistrictSync.exe"),
                sis_type="myedbc",
                input_dir=Path("C:/input"),
                output_dir=Path("C:/output"),
                run_time="03:00",
                run_as_user="jane",
                run_as_password=secret,
            )

        assert ok is False
        assert "Access is denied" in msg
        assert secret not in caplog.text
        assert "DSYNC_TASK_PW" not in caplog.text
        assert secret not in msg
        assert "DSYNC_TASK_PW" not in msg


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

        env = {k: v for k, v in os.environ.items() if k not in ("USERDOMAIN", "USERNAME")}
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
