"""The Windows scheduler's credential handling — COM boundary (plan 0041 S1b).

The unattended (stored-password) path's hygiene contract, re-pinned against the
in-process transport that replaced ``powershell.exe -EncodedCommand``:

  - the password reaches Windows ONLY as the in-process argument to
    ``Folder.RegisterTaskDefinition`` (via ``task_com.RegisterParams``) — there is no
    child process, so "never on argv, never in a child env" is now STRUCTURAL; what
    these tests pin is the remaining escape routes: ``os.environ`` stays untouched,
    ``repr(params)`` hides the password (``repr=False``), and no log record on either
    the success or failure path carries the value;
  - password path → explicit ``TASK_LOGON_PASSWORD`` + Highest/Limited by
    ``run_highest``; no-password path → ``TASK_LOGON_INTERACTIVE_TOKEN`` + Limited
    (``run_highest`` ignored) — and the S4U logon value (2) is never passed anywhere;
  - ``current_run_as_user()`` resolution + fallback and ``validate_run_as_user()``
    are transport-independent and survive verbatim below.

The COM seam is ``task_com.apply_definition``'s (service, folder) pair — faked with
MagicMocks, so these run identically on Windows dev hosts and Linux CI (no pywin32).
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler import task_com
from src.scheduler.task_com import RegisterParams, apply_definition


@pytest.fixture(autouse=True)
def _already_elevated():
    """Pin these tests to the DIRECT registration path (already-elevated), on any host."""
    with patch("src.scheduler.windows.is_elevated", return_value=True):
        yield


def _fake_com():
    """A (service, folder) MagicMock pair shaped like the live Task Scheduler objects."""
    service = MagicMock(name="Schedule.Service")
    folder = MagicMock(name="RootFolder")
    return service, folder


def _params(password="s3cret!", run_highest=True, user="CORP\\jane"):
    return RegisterParams(
        task_name="DistrictSync_Daily",
        exe=r"C:\DistrictSync\DistrictSync.exe",
        arguments="--sis myedbc --source scheduled",
        working_dir=r"C:\DistrictSync",
        run_time="03:00",
        user=user,
        password=password,
        run_highest=run_highest,
    )


def _register_call(folder):
    assert folder.RegisterTaskDefinition.call_count == 1
    return folder.RegisterTaskDefinition.call_args[0]


class TestPasswordReachesOnlyTheComArgument:
    def test_password_is_the_fifth_register_argument(self):
        service, folder = _fake_com()
        apply_definition(service, folder, _params())
        name, _definition, flags, user, password, logon = _register_call(folder)
        assert name == "DistrictSync_Daily"
        assert flags == task_com.TASK_CREATE_OR_UPDATE
        assert user == "CORP\\jane"
        assert password == "s3cret!"
        assert logon == task_com.TASK_LOGON_PASSWORD

    def test_os_environ_is_never_touched(self):
        """The retired transport built a child env; nothing may mutate the parent's now."""
        service, folder = _fake_com()
        before = dict(os.environ)
        apply_definition(service, folder, _params())
        assert dict(os.environ) == before
        assert not any("DSYNC" in k for k in os.environ)

    def test_params_repr_hides_the_password(self):
        """``repr=False`` is load-bearing: a default dataclass repr would hand the
        password to any log/f-string that formats the params object."""
        rendered = repr(_params(password="uniq-XYZZY-pw"))
        assert "uniq-XYZZY-pw" not in rendered
        assert "RegisterParams" in rendered

    def test_no_password_registers_with_none_credential(self):
        service, folder = _fake_com()
        apply_definition(service, folder, _params(password=None))
        _name, _d, _flags, _user, password, logon = _register_call(folder)
        assert password is None
        assert logon == task_com.TASK_LOGON_INTERACTIVE_TOKEN


class TestLogonTypeMatrix:
    def test_password_highest(self):
        service, folder = _fake_com()
        apply_definition(service, folder, _params(run_highest=True))
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        assert definition.Principal.RunLevel == task_com.TASK_RUNLEVEL_HIGHEST

    def test_password_limited(self):
        service, folder = _fake_com()
        apply_definition(service, folder, _params(run_highest=False))
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        assert definition.Principal.RunLevel == task_com.TASK_RUNLEVEL_LUA

    def test_run_highest_ignored_without_password(self):
        """run_highest=True + no password must still register Limited (today's semantics)."""
        service, folder = _fake_com()
        apply_definition(service, folder, _params(password=None, run_highest=True))
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        assert definition.Principal.RunLevel == task_com.TASK_RUNLEVEL_LUA
        assert folder.RegisterTaskDefinition.call_args[0][5] == task_com.TASK_LOGON_INTERACTIVE_TOKEN

    def test_s4u_is_unrepresentable(self):
        """The S4U logon type (2) is not defined in task_com and never passed: it runs
        logged-off with NO network token, silently breaking the SFTP egress — the exact
        2026-06-25 regression class. Both halves pinned: no constant, no call value."""
        assert not hasattr(task_com, "TASK_LOGON_S4U")
        for pw, highest in ((None, True), ("pw", True), ("pw", False)):
            service, folder = _fake_com()
            apply_definition(service, folder, _params(password=pw, run_highest=highest))
            assert folder.RegisterTaskDefinition.call_args[0][5] != 2


class TestSettingsQuintetAndShape:
    """Rows 1, 4, 11: COM defaults differ on ALL FIVE settings — each must be explicit."""

    def test_all_five_settings_are_explicit(self):
        service, folder = _fake_com()
        apply_definition(service, folder, _params())
        settings = folder.RegisterTaskDefinition.call_args[0][1].Settings
        assert settings.StartWhenAvailable is False  # no catch-up run (2026-06-15)
        assert settings.MultipleInstances == task_com.TASK_INSTANCES_IGNORE_NEW
        assert settings.ExecutionTimeLimit == "PT2H"  # COM default is PT72H
        assert settings.DisallowStartIfOnBatteries is False  # battery operation enabled
        assert settings.StopIfGoingOnBatteries is False

    def test_trigger_is_daily_at_the_fixed_past_boundary(self):
        """Row 4: an invariant ISO boundary WE compose — deterministic + catch-up-inert."""
        service, folder = _fake_com()
        apply_definition(service, folder, _params())
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        definition.Triggers.Create.assert_called_once_with(task_com.TASK_TRIGGER_DAILY)
        trigger = definition.Triggers.Create.return_value
        assert trigger.StartBoundary == "2024-01-01T03:00:00"
        assert trigger.DaysInterval == 1

    def test_action_carries_exe_args_workdir(self):
        """Row 11: Execute/Arguments/WorkingDirectory — the read-back's action_path source."""
        service, folder = _fake_com()
        apply_definition(service, folder, _params())
        definition = folder.RegisterTaskDefinition.call_args[0][1]
        definition.Actions.Create.assert_called_once_with(task_com.TASK_ACTION_EXEC)
        action = definition.Actions.Create.return_value
        assert action.Path == r"C:\DistrictSync\DistrictSync.exe"
        assert action.Arguments == "--sis myedbc --source scheduled"
        assert action.WorkingDirectory == r"C:\DistrictSync"


class TestRegisterTaskOrchestration:
    """register_task's direct path over a mocked task_com boundary."""

    @patch("src.scheduler.windows.task_com.bounded")
    def test_success_returns_registered(self, mock_bounded):
        from src.scheduler.windows import register_task

        mock_bounded.return_value = None
        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path(r"C:\DistrictSync\DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path(r"C:\data\in"),
            output_dir=Path(r"C:\data\out"),
            run_time="03:00",
            run_as_password="pw",
        )
        assert ok is True
        assert "registered" in msg.lower()
        assert mock_bounded.call_args[1]["timeout_s"] == task_com.REGISTER_TIMEOUT_S

    @patch("src.scheduler.windows._confirm_registration")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_timeout_resolves_through_read_back(self, mock_bounded, mock_confirm):
        """Row 14: the worker may still complete — never a bare 'failed' over a task
        that may now exist; the hedged copy rides the same classifier branch."""
        from src.scheduler.windows import register_task

        mock_bounded.side_effect = task_com.BoundedTimeout("register")
        mock_confirm.return_value = (True, "Schedule registered and confirmed.")
        ok, _ = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
        )
        assert ok is True
        mock_confirm.assert_called_once()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_credential_failure_surfaces_the_canonical_message(self, mock_bounded):
        """Live-confirmed 2026-08-05: a wrong password fails with exactly this text."""
        from src.scheduler.windows import register_task

        mock_bounded.side_effect = task_com.TaskComError(
            task_com.HR_LOGON_FAILURE, "The user name or password is incorrect."
        )
        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
            run_as_password="wrong",
        )
        assert ok is False
        assert msg == "The user name or password is incorrect."

    @patch("src.scheduler.windows.task_com.bounded")
    def test_pywin32_missing_is_the_canonical_unavailable_message(self, mock_bounded):
        from src.scheduler.windows import register_task

        mock_bounded.side_effect = ImportError("no win32com")
        ok, msg = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
        )
        assert ok is False
        assert msg == task_com.MSG_COM_UNAVAILABLE

    def test_invalid_run_as_user_raises_before_any_com_call(self):
        from src.scheduler.windows import register_task

        with patch("src.scheduler.windows.task_com.bounded") as mock_bounded:
            with pytest.raises(ValueError):
                register_task(
                    task_name="DistrictSync_Daily",
                    exe_path=Path("x.exe"),
                    sis_type="myedbc",
                    input_dir=Path("i"),
                    output_dir=Path("o"),
                    run_time="03:00",
                    run_as_user="jane && calc",
                    run_as_password="pw",
                )
            mock_bounded.assert_not_called()

    @patch("src.scheduler.windows._register_elevated")
    @patch("src.scheduler.windows.is_elevated", return_value=False)
    @patch("src.scheduler.windows.sys.platform", "win32")
    def test_password_and_not_elevated_dispatches_to_the_elevated_path(self, _elev, mock_elevated):
        from src.scheduler.windows import register_task

        mock_elevated.return_value = (True, "Schedule registered and confirmed.")
        ok, _ = register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
            run_as_password="pw",
        )
        assert ok is True
        mock_elevated.assert_called_once()
        # The password rode the keyword call — and never any process environment.
        assert mock_elevated.call_args[1]["run_as_password"] == "pw"
        assert not any("DSYNC" in k for k in os.environ)


class TestPasswordLeakClosure:
    """The value must appear in NO log record on either path (caplog sweeps the root)."""

    SECRET = "uniq-Vq7x-secret"  # noqa: S105 - a test marker, not a credential

    @patch("src.scheduler.windows.task_com.bounded")
    def test_success_path_never_logs_the_password(self, mock_bounded, caplog):
        from src.scheduler.windows import register_task

        mock_bounded.return_value = None
        with caplog.at_level(logging.DEBUG):
            ok, msg = register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("x.exe"),
                sis_type="myedbc",
                input_dir=Path("i"),
                output_dir=Path("o"),
                run_time="03:00",
                run_as_password=self.SECRET,
            )
        assert ok is True
        assert self.SECRET not in caplog.text
        assert self.SECRET not in msg

    @patch("src.scheduler.windows.task_com.bounded")
    def test_failure_path_never_logs_the_password(self, mock_bounded, caplog):
        from src.scheduler.windows import register_task

        mock_bounded.side_effect = task_com.TaskComError(task_com.HR_ACCESS_DENIED, "Access is denied.")
        with caplog.at_level(logging.DEBUG):
            ok, msg = register_task(
                task_name="DistrictSync_Daily",
                exe_path=Path("x.exe"),
                sis_type="myedbc",
                input_dir=Path("i"),
                output_dir=Path("o"),
                run_time="03:00",
                run_as_password=self.SECRET,
            )
        assert ok is False
        assert self.SECRET not in caplog.text
        assert self.SECRET not in msg


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


# -----------------------------------------------------------------------
# The PRINCIPAL contract (plan 0046, A1)
#
# register_task used to resolve the run-as account like this::
#
#     has_password = bool(run_as_password)
#     if has_password:
#         user = validate_run_as_user(run_as_user or current_run_as_user())
#     else:
#         user = current_run_as_user()          # <- run_as_user DISCARDED, silently
#
# so an explicit account with no password registered the task to the INTERACTIVE user
# and returned (True, "Schedule registered."): the wrong identity behind a green banner.
# ``""`` compounded it — ``windows`` read it as "no password" while
# ``task_com.apply_definition`` reads ``password is not None`` as "unattended", so a blank
# string could register TASK_LOGON_PASSWORD + Highest with a blank credential.
#
# These tests assert the REGISTERED PRINCIPAL (the RegisterParams that reach the COM
# boundary), never the return tuple alone — the old defect returned success.
# -----------------------------------------------------------------------

_SETUP_ACCOUNT = r"CORP\ted"
_SERVICE_ACCOUNT = r"CORP\SVC_DistrictSync"


@contextmanager
def _capture_registration():
    """Run the direct COM path for real, capturing the RegisterParams that reach it."""
    with (
        patch("src.scheduler.windows.task_com.bounded", side_effect=lambda fn, **kw: fn()),
        patch("src.scheduler.task_com.register_task_definition") as reg,
    ):
        yield reg


def _params_of(reg):
    assert reg.call_count == 1
    return reg.call_args[0][0]


def _register(**overrides):
    from src.scheduler.windows import register_task

    kwargs = {
        "task_name": "DistrictSync_Daily",
        "exe_path": Path("x.exe"),
        "sis_type": "myedbc",
        "input_dir": Path("i"),
        "output_dir": Path("o"),
        "run_time": "03:00",
    }
    kwargs.update(overrides)
    return register_task(**kwargs)


@pytest.fixture
def _setup_account():
    """Pin ``current_run_as_user()`` so "is this a DIFFERENT account?" is deterministic."""
    with patch("src.scheduler.windows.current_run_as_user", return_value=_SETUP_ACCOUNT):
        yield _SETUP_ACCOUNT


class TestForeignAccountRequiresItsPassword:
    @pytest.mark.parametrize("password", [None, ""])
    def test_refused_without_a_password(self, _setup_account, password):
        """The measured defect. Windows stores no credential for an interactive-token
        task, so "that other account, logged-on-only" is not a thing it can do — the
        honest answer is a refusal, never a substituted principal."""
        from src.scheduler.windows import _MSG_ACCOUNT_NEEDS_PASSWORD

        with _capture_registration() as reg:
            ok, msg = _register(run_as_user=_SERVICE_ACCOUNT, run_as_password=password)
        assert (ok, msg) == (False, _MSG_ACCOUNT_NEEDS_PASSWORD)
        reg.assert_not_called()

    def test_the_refusal_names_no_account(self, _setup_account, caplog):
        """The canonical message is keyed by setup_errors by EXACT equality, and neither
        it nor the log may echo an account name (bounded and PII-free — the same house
        rule the elevation markers follow)."""
        with caplog.at_level(logging.DEBUG), _capture_registration():
            ok, msg = _register(run_as_user=_SERVICE_ACCOUNT, run_as_password=None)
        assert ok is False  # not vacuous: without the refusal there is no message to check
        assert "SVC_DistrictSync" not in msg
        assert "SVC_DistrictSync" not in caplog.text
        assert "ted" not in msg

    @patch("src.scheduler.windows._register_elevated")
    @patch("src.scheduler.windows.is_elevated", return_value=False)
    @patch("src.scheduler.windows.sys.platform", "win32")
    def test_refused_before_any_uac_prompt(self, _elev, mock_elevated, _setup_account):
        """A refusal must cost the admin nothing — no elevation, no prompt, no child, and
        (the capture context is load-bearing) no COM registration on the direct path
        either: the refusal is the FIRST thing that happens, not a late veto."""
        with _capture_registration() as reg:
            ok, _ = _register(run_as_user=_SERVICE_ACCOUNT, run_as_password="")
        assert ok is False
        mock_elevated.assert_not_called()
        reg.assert_not_called()

    def test_with_a_password_it_registers_that_account(self, _setup_account):
        """The positive twin of the refusal: the thing the refusal protects must actually
        work, or every assertion above would pass over a function that can no longer
        register a foreign principal at all."""
        with _capture_registration() as reg:
            ok, _ = _register(run_as_user=_SERVICE_ACCOUNT, run_as_password="pw")
        assert ok is True
        params = _params_of(reg)
        assert params.user == _SERVICE_ACCOUNT
        assert params.password == "pw"

    def test_an_invalid_foreign_account_is_rejected_before_any_com_call(self, _setup_account):
        with _capture_registration() as reg, pytest.raises(ValueError):
            _register(run_as_user="jane && calc", run_as_password="pw")
        reg.assert_not_called()


class TestTodaysBehaviourIsUnchanged:
    """G5: the 20 shipped districts all pass ``run_as_user=None``. Nothing here may move."""

    def test_the_current_account_needs_no_password(self, _setup_account):
        with _capture_registration() as reg:
            ok, _ = _register()
        assert ok is True
        params = _params_of(reg)
        assert params.user == _SETUP_ACCOUNT
        assert params.password is None

    def test_naming_the_current_account_is_not_a_foreign_account(self, _setup_account):
        """Case-insensitively the same account — a request to keep things as they are, not
        a principal change. (Slice 2 prefills this field with the current account.)"""
        with _capture_registration() as reg:
            ok, _ = _register(run_as_user=_SETUP_ACCOUNT.upper(), run_as_password=None)
        assert ok is True
        assert _params_of(reg).password is None

    def test_a_spaced_local_account_still_registers_logged_on_only(self):
        """``PC\\John Smith`` is a legitimate Windows account that ``validate_run_as_user``
        rejects (no spaces). The machine-derived fallback is therefore NEVER validated on
        this path — validating it would stop that district scheduling at all."""
        env = {"USERDOMAIN": "PC", "USERNAME": "John Smith"}
        with patch.dict("os.environ", env, clear=False), _capture_registration() as reg:
            ok, _ = _register()
        assert ok is True
        assert _params_of(reg).user == r"PC\John Smith"


class TestBlankPasswordIsNeverUnattended:
    """R2: ``windows`` (``bool``) and ``task_com`` (``is not None``) disagreed about what
    "no password" means, so ``""`` could reach RegisterTaskDefinition as TASK_LOGON_PASSWORD
    with a blank credential. Both halves are asserted from the SAME params."""

    @pytest.mark.parametrize(
        ("password", "expected_logon"),
        [
            (None, task_com.TASK_LOGON_INTERACTIVE_TOKEN),
            ("", task_com.TASK_LOGON_INTERACTIVE_TOKEN),
            ("x", task_com.TASK_LOGON_PASSWORD),
        ],
    )
    def test_both_halves_agree(self, _setup_account, password, expected_logon):
        with _capture_registration() as reg:
            ok, _ = _register(run_as_password=password)
        assert ok is True
        params = _params_of(reg)

        # Half 1 — what register_task hands the COM boundary.
        assert params.password == (password or None)

        # Half 2 — what apply_definition does with exactly those params.
        service, folder = _fake_com()
        apply_definition(service, folder, params)
        _n, definition, _f, _u, sent_password, logon = _register_call(folder)
        assert logon == expected_logon
        assert sent_password == (password or None)
        if expected_logon == task_com.TASK_LOGON_INTERACTIVE_TOKEN:
            assert definition.Principal.RunLevel == task_com.TASK_RUNLEVEL_LUA


# -----------------------------------------------------------------------
# Plan 0047 — ONE anchored failure log line per failure return (G2)
# -----------------------------------------------------------------------
#
# SD60, 2026-09-14: an admin enabled the nightly sync, got a failure, ran the app as
# administrator, got the SAME failure, and reported "I didn't see anything informative in
# the log." Several `(False, msg)` arms logged nothing at all, and none of them logged the
# HRESULT. `_fail` is the ONE funnel: it writes `_FAIL_LOG_FORMAT` — a single grep anchor
# carrying the verb, the task name, the canonical text and `[HRESULT 0x... | n/a]`.


def _fail_records(caplog):
    """Only the ANCHORED lines — the bespoke phase lines beside them are deliberately kept."""
    from src.scheduler import windows

    return [r for r in caplog.records if r.msg == windows._FAIL_LOG_FORMAT]


def _one_fail_record(caplog):
    records = _fail_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    return records[0]


class TestFailHelper:
    def test_verb_is_required(self):
        """The house rule (no permissive default on a safety-relevant parameter): a default
        would let a failed REMOVAL log "Failed to register task", which is the line the
        partner troubleshooting page tells an IT reader to grep for."""
        from src.scheduler import windows

        with pytest.raises(TypeError):
            windows._fail("DistrictSync_Daily", "boom")  # type: ignore[call-arg]

    def test_it_returns_the_message_unchanged(self, caplog):
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG):
            result = windows._fail("T", "boom", verb="remove", scode=task_com.HR_ACCESS_DENIED)
        assert result == (False, "boom")
        assert _one_fail_record(caplog).getMessage() == "Failed to remove task 'T': boom [HRESULT 0x80070005]"

    def test_an_absent_task_on_the_remove_path_is_the_one_silent_failure(self, caplog):
        """Deleting an already-absent task is the IDEMPOTENT end state, not an incident —
        logging it at ERROR every time would train a district to ignore the anchor."""
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG):
            ok, msg = windows._fail("T", task_com.MSG_NOT_FOUND, verb="remove")
        assert (ok, msg) == (False, task_com.MSG_NOT_FOUND)
        assert _fail_records(caplog) == []

    def test_a_line_without_detail_ends_at_the_code(self, caplog):
        """The NEGATIVE twin of the detail suffix: every arm that has a canonical and a code
        keeps today's exact line — `detail` is additive, never a reformat of the anchor."""
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG):
            windows._fail("T", "boom", verb="register", scode=task_com.HR_LOGON_FAILURE)
        assert _one_fail_record(caplog).getMessage().endswith("[HRESULT 0x8007052E]")

    def test_detail_rides_after_the_code_on_the_same_line(self, caplog):
        """One anchored line per failure stays ONE line: a bounded, secret-free token (today
        only an exception's class name) is appended, never logged separately."""
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG):
            windows._fail("T", "boom", verb="register", detail="TypeError")
        record = _one_fail_record(caplog)
        assert record.getMessage() == "Failed to register task 'T': boom [HRESULT n/a] (TypeError)"

    def test_the_same_message_on_the_register_path_is_still_logged(self, caplog):
        """The POSITIVE twin of the silence: the carve-out is remove-specific, not a
        blanket mute on that string."""
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG):
            windows._fail("T", task_com.MSG_NOT_FOUND, verb="register")
        assert len(_fail_records(caplog)) == 1


class TestNoUnfunnelledFailureReturn:
    """The STRUCTURAL pin for G2 — a convention a reviewer must remember is not a pin.

    Every `(False, ...)` return in `windows.py` must come from `_fail`, so a NEW failure arm
    added later cannot be silent by accident. Fourteen sites routed at plan 0047.
    """

    @staticmethod
    def _windows_source() -> str:
        import pathlib

        from src.scheduler import windows

        return pathlib.Path(windows.__file__).read_text(encoding="utf-8")

    @staticmethod
    def _offending_lines(source: str) -> list[int]:
        import ast

        tree = ast.parse(source)
        offenders: list[int] = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or func.name == "_fail":
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
                    continue
                first = node.value.elts[0] if node.value.elts else None
                if isinstance(first, ast.Constant) and first.value is False:
                    offenders.append(node.lineno)
        return offenders

    def test_no_return_false_tuple_outside_fail(self):
        assert self._offending_lines(self._windows_source()) == []

    def test_the_guard_can_see_a_planted_one(self):
        """Not vacuous — and not vacuous in the honest way: the planted return goes through
        THE SAME `_offending_lines` on THE REAL module source, so a bug in the walk itself
        fails this test too. (Re-implementing the walk inline over a synthetic snippet would
        prove only that a correct walk works, which was never the thing in doubt.)"""
        planted = self._windows_source().replace(
            "def current_run_as_user() -> str:",
            "def _planted_failure() -> tuple[bool, str]:\n    return False, 'planted'\n\n\ndef current_run_as_user() -> str:",
            1,
        )
        assert planted != self._windows_source()  # the anchor still exists
        assert len(self._offending_lines(planted)) == 1


class TestRegisterArmSweep:
    """Every `(False, msg)` arm of `register_task` on the DIRECT path emits exactly one
    anchored line, at the honest level, carrying the code when there is one."""

    # Arms whose failure genuinely has no HRESULT behind it, so `[HRESULT n/a]` is a decision
    # rather than an omission. Each is separately and BEHAVIOURALLY tested below; this list is a
    # plain comment, deliberately NOT a class attribute with a test over itself — that shape
    # asserted "the set has three entries, each containing a colon", which stayed green with the
    # whole `_fail` funnel removed (Verify 2026-09-16, R3).
    #   - pre-flight refusal: a different run-as account with no password (nothing was attempted)
    #   - pywin32 missing from a frozen build: an ImportError, not a COM status
    #   - bounded-worker timeout: the OUTCOME is unknown, so there is no status to report

    def test_preflight_refusal_is_logged(self, _setup_account, caplog):
        from src.scheduler import windows

        with caplog.at_level(logging.DEBUG), _capture_registration():
            ok, msg = _register(run_as_user=_SERVICE_ACCOUNT, run_as_password=None)
        assert (ok, msg) == (False, windows._MSG_ACCOUNT_NEEDS_PASSWORD)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert "Failed to register task" in record.getMessage()
        assert "[HRESULT n/a]" in record.getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_import_error_is_logged(self, mock_bounded, caplog):
        mock_bounded.side_effect = ImportError("no win32com")
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register()
        assert (ok, msg) == (False, task_com.MSG_COM_UNAVAILABLE)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert task_com.MSG_COM_UNAVAILABLE in record.getMessage()
        assert "[HRESULT n/a]" in record.getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_task_com_error_carries_its_code(self, mock_bounded, caplog):
        mock_bounded.side_effect = task_com.TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)
        with caplog.at_level(logging.DEBUG):
            ok, _ = _register()
        assert ok is False
        assert "[HRESULT 0x80070005]" in _one_fail_record(caplog).getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_the_new_logon_session_code_reaches_the_log(self, mock_bounded, caplog):
        """The code SD60's failure is believed to be — unmapped before plan 0047, so it
        could not appear in a district's log at all."""
        mock_bounded.side_effect = task_com.TaskComError(
            task_com.HR_NO_SUCH_LOGON_SESSION, task_com.MSG_NO_LOGON_SESSION
        )
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register(run_as_password="pw")
        assert (ok, msg) == (False, task_com.MSG_NO_LOGON_SESSION)
        assert "[HRESULT 0x80070520]" in _one_fail_record(caplog).getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_scodeless_task_com_error_logs_n_a(self, mock_bounded, caplog):
        """REACHABLE: `com_error_scode` returns None when `excepinfo[5]` cannot be coerced."""
        mock_bounded.side_effect = task_com.TaskComError(None, task_com.MSG_OPERATION_FAILED)
        with caplog.at_level(logging.DEBUG):
            ok, _ = _register()
        assert ok is False
        assert "[HRESULT n/a]" in _one_fail_record(caplog).getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_an_apartment_entry_com_error_is_logged_at_error_with_its_code(self, mock_bounded, caplog):
        """The generic arm used to log only the exception's CLASS NAME, at WARNING — so a
        stopped Task Scheduler service (a `com_error` raised at apartment entry, before any
        TaskComError conversion) reached the district's log as `Exception`, with no status."""

        class _ApartmentComError(Exception):
            def __init__(self):
                super().__init__()
                self.hresult = -2147352567
                self.excepinfo = (0, None, "desc", None, 0, -2147024891)

        mock_bounded.side_effect = _ApartmentComError()
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register()
        assert (ok, msg) == (False, task_com.MSG_OPERATION_FAILED)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert "[HRESULT 0x80070005]" in record.getMessage()

    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_non_com_exception_keeps_its_class_name(self, mock_bounded, caplog):
        """`com_error_scode` recovers nothing for a non-COM exception, so without `detail`
        the whole payload would be "[HRESULT n/a]" — strictly LESS than the class name the
        pre-0047 arm logged. The funnel adds context; it never net-deletes it."""
        mock_bounded.side_effect = TypeError("pywin32 changed shape")
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register()
        assert (ok, msg) == (False, task_com.MSG_OPERATION_FAILED)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert record.getMessage().endswith("[HRESULT n/a] (TypeError)")

    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_com_error_carries_both_its_code_and_its_class_name(self, mock_bounded, caplog):
        """The positive twin: `detail` is unconditional on the generic arm, so the apartment
        -entry case keeps its code AND gains the class name."""

        class _ApartmentComError(Exception):
            def __init__(self):
                super().__init__()
                self.hresult = -2147352567
                self.excepinfo = (0, None, "desc", None, 0, -2147024891)

        mock_bounded.side_effect = _ApartmentComError()
        with caplog.at_level(logging.DEBUG):
            _register()
        assert _one_fail_record(caplog).getMessage().endswith("[HRESULT 0x80070005] (_ApartmentComError)")

    @patch("src.scheduler.windows.read_schedule")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_timeout_is_a_warning_not_an_error(self, mock_bounded, mock_read, caplog):
        """The outcome is UNKNOWN, not failed — the worker may still complete. WARNING is
        the honest level, and the line still carries the anchor."""
        from src.scheduler.windows import ScheduleReadback

        mock_bounded.side_effect = task_com.BoundedTimeout("register")
        mock_read.return_value = ScheduleReadback(found=None)
        with caplog.at_level(logging.DEBUG):
            ok, _ = _register()
        assert ok is False
        assert _one_fail_record(caplog).levelno == logging.WARNING

    @patch("src.scheduler.windows.read_schedule")
    @patch("src.scheduler.windows.task_com.bounded")
    def test_a_direct_path_timeout_does_not_claim_to_be_elevated(self, mock_bounded, mock_read, caplog):
        """The bespoke phase line said "Elevated registration" on EVERY unconfirmed
        registration, including the direct one — which sends a reader hunting a UAC prompt
        that never happened."""
        from src.scheduler.windows import ScheduleReadback

        mock_bounded.side_effect = task_com.BoundedTimeout("register")
        mock_read.return_value = ScheduleReadback(found=None)
        with caplog.at_level(logging.DEBUG):
            _register()
        assert "Elevated registration" not in caplog.text
        assert "Registration of 'DistrictSync_Daily' could not be confirmed" in caplog.text


class TestPreConsentHandshakeFailureIsLogged:
    """`_register_elevated`'s pre-consent `write_request` could raise `OSError` (DPAPI,
    profile dir, icacls) straight past the `(ok, message)` contract to the view's floor —
    with zero log lines. It is now caught, logged and returned as the generic canonical."""

    @patch("src.scheduler.windows.is_elevated", return_value=False)
    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.elevation.write_request", side_effect=OSError("profile dir unwritable"))
    def test_it_is_logged_and_returned_not_raised(self, _write, _elev, caplog):
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register(run_as_password="pw")
        assert (ok, msg) == (False, task_com.MSG_OPERATION_FAILED)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert "Failed to register task" in record.getMessage()

    @patch("src.scheduler.windows.is_elevated", return_value=False)
    @patch("src.scheduler.windows.sys.platform", "win32")
    @patch("src.scheduler.elevation.write_request", side_effect=RuntimeError("unusable data dir"))
    def test_a_runtime_error_from_write_request_is_also_logged_and_returned(self, _write, _elev, caplog):
        """`elevation.write_request` -> `paths.user_data_dir()` raises `RuntimeError` for an
        unusable directory (and `ValueError` for a relative `DISTRICTSYNC_DATA_DIR`) — NOT
        `OSError`. Before Stage 7 this guard caught only `OSError`, so the exact
        silent-raise-to-the-view-floor failure the slice exists to close was still open on
        this path."""
        with caplog.at_level(logging.DEBUG):
            ok, msg = _register(run_as_password="pw")
        assert (ok, msg) == (False, task_com.MSG_OPERATION_FAILED)
        record = _one_fail_record(caplog)
        assert record.levelno == logging.ERROR
        assert "Failed to register task" in record.getMessage()


class TestFailLogLineCarriesNoSecret:
    """R2 — the new lines must not become a secret or PII channel."""

    SECRET = "uniq-Vq7x-secret"  # noqa: S105 - a test marker, not a credential
    ACCOUNT = "CORP\\uniq-Kp3z-account"

    @patch("src.scheduler.windows.current_run_as_user", return_value=ACCOUNT)
    @patch("src.scheduler.windows.task_com.bounded")
    def test_the_direct_failure_line_carries_the_code_and_neither_secret(self, mock_bounded, _who, caplog):
        mock_bounded.side_effect = task_com.TaskComError(task_com.HR_ACCESS_DENIED, task_com.MSG_ACCESS_DENIED)
        with caplog.at_level(logging.DEBUG):
            ok, _ = _register(run_as_password=self.SECRET)
        assert ok is False
        # POSITIVE twin first: without it, the two absences below would pass over a line
        # that was never written at all.
        assert "0x80070005" in caplog.text
        assert self.SECRET not in caplog.text
        assert "uniq-Kp3z-account" not in caplog.text
