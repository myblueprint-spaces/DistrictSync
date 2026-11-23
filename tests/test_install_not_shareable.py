"""Plan 0055 G0: a per-user installed copy can never be scheduled to run as another account.

The copy lives in the installing user's own ``%LOCALAPPDATA%``, which a service account or managed
service account cannot read, so such a nightly registers fine and then never starts. Three layers
refuse it, each pinned here or beside its neighbours:

* the Setup gate (``RegisterBlock.INSTALL_NOT_SHAREABLE``, ``tests/test_ui_flet_setup_gates.py``),
* the engine floor on the direct register path (``windows._MSG_INSTALL_NOT_SHAREABLE``,
  ``tests/test_scheduler_runas.py``),
* the pre-prompt provisioning refusal (``ProvisionOutcome.INSTALL_NOT_SHAREABLE``, below).

This file pins the view's input, the copy, the classifier branch and the provisioning refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.scheduler import elevated_apply, provision_session, task_com, windows
from src.scheduler.provision_session import ProvisionOutcome, request_provision
from src.scheduler.task_com import Principal, PrincipalKind
from src.ui_flet import setup_errors
from src.ui_flet.screens import setup as setup_mod
from src.ui_flet.setup_flow import ReconcileOutcome, folders_save_note, sftp_reconcile_suffix
from src.ui_flet.setup_gates import RegisterBlock, ScheduleAccountFacts

_SERVICE = "CORP\\svc_districtsync"
_GMSA = "CORP\\svc_districtsync$"


@pytest.fixture(autouse=True)
def _profile_is_tmp_path(tmp_path, monkeypatch):
    """``tmp_path`` stands in for this account's profile on every OS (Linux CI has no LOCALAPPDATA)."""
    monkeypatch.setattr(task_com, "_user_profile_roots", lambda: [tmp_path])


def _installed_layout(tmp_path: Path) -> Path:
    root = tmp_path / "myBlueprint.DistrictSync"
    (root / "current").mkdir(parents=True)
    (root / "Update.exe").write_bytes(b"")
    exe = root / "current" / "DistrictSync.exe"
    exe.write_bytes(b"")
    return exe


def _portable(tmp_path: Path) -> Path:
    exe = tmp_path / "DistrictSync" / "DistrictSync-windows.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


class TestTheViewInput:
    def test_true_for_a_per_user_installed_copy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_mod.sys, "executable", str(_installed_layout(tmp_path)))
        assert setup_mod.install_not_shareable() is True

    def test_positive_twin_false_for_the_single_file_exe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup_mod.sys, "executable", str(_portable(tmp_path)))
        assert setup_mod.install_not_shareable() is False

    def test_positive_twin_false_for_the_same_layout_outside_this_accounts_profile(self, tmp_path, monkeypatch):
        """The machine-wide MSI's Program Files copy has the same layout and is readable by every account."""
        monkeypatch.setattr(setup_mod.sys, "executable", str(_installed_layout(tmp_path)))
        monkeypatch.setattr(task_com, "_user_profile_roots", lambda: [tmp_path / "somebody-else"])
        assert setup_mod.install_not_shareable() is False

    def test_a_failure_reads_as_shareable_and_never_raises_into_a_paint_path(self, monkeypatch):
        def boom(_exe: str) -> bool:
            raise OSError("unreadable")

        monkeypatch.setattr(setup_mod, "runs_from_per_user_install", boom)
        assert setup_mod.install_not_shareable() is False


class TestTheCopy:
    def test_the_gate_note_is_the_one_copy(self):
        facts = ScheduleAccountFacts(
            typed=_SERVICE,
            current="PC\\admin",
            password_supplied=True,
            recorded=None,
            schedule_registered=False,
            kind=PrincipalKind.PASSWORD,
        )
        assert setup_mod.account_block_note(RegisterBlock.INSTALL_NOT_SHAREABLE, facts) == (
            setup_errors.INSTALL_NOT_SHAREABLE_COPY
        )

    def test_it_names_the_cause_and_the_fix_and_no_account(self):
        copy = setup_errors.INSTALL_NOT_SHAREABLE_COPY
        assert "installed for your Windows account only" in copy
        assert "DistrictSync-windows.exe" in copy
        assert "svc_" not in copy
        assert "password" not in copy  # no password can fix it, so the copy never asks for one

    def test_the_refusal_says_nothing_changed(self):
        refusal = setup_errors.INSTALL_NOT_SHAREABLE_REFUSAL_COPY
        assert refusal.startswith(setup_errors.INSTALL_NOT_SHAREABLE_COPY)
        assert "Nothing was changed" in refusal

    def test_the_engine_message_classifies_to_the_refusal_with_no_code(self):
        out = setup_errors.classify_schedule_error(
            windows._MSG_INSTALL_NOT_SHAREABLE,
            False,
            account_is_current=False,
            kind=PrincipalKind.PASSWORD,
        )
        assert out == setup_errors.INSTALL_NOT_SHAREABLE_REFUSAL_COPY
        assert "0x" not in out

    def test_the_reconcile_notes_name_the_install_not_the_password_or_run_time(self):
        for note in (
            folders_save_note(ReconcileOutcome.BLOCKED_INSTALL),
            sftp_reconcile_suffix(ReconcileOutcome.BLOCKED_INSTALL),
        ):
            assert "installed for your Windows account only" in note
            assert "password" not in note
            assert "run time" not in note
        # Positive twin: a different cause keeps its own sentence.
        assert "installed for your Windows account only" not in folders_save_note(ReconcileOutcome.BLOCKED_ACCOUNT)


class TestThePrePromptProvisioningRefusal:
    def _provision(self, exe: Path, principal: Principal):
        return request_provision(
            "DistrictSync_Daily",
            exe,
            "myedbc",
            Path("in"),
            Path("out"),
            "03:00",
            sftp_host="",
            sftp_username="",
            principal=principal,
            wake_to_run=True,
        )

    @pytest.mark.parametrize(
        "principal",
        [
            Principal(kind=PrincipalKind.PASSWORD, user=_SERVICE, password="pw"),
            Principal(kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT, user=_GMSA, password=None),
        ],
        ids=["stored-password", "managed-service-account"],
    )
    def test_refused_before_the_account_lookup_and_before_any_prompt(self, tmp_path, principal):
        with (
            patch.object(provision_session, "_resolve_before_prompt") as lookup,
            patch.object(provision_session.elevation, "run_elevated") as prompt,
        ):
            attempt = self._provision(_installed_layout(tmp_path), principal)
        assert attempt.outcome is ProvisionOutcome.INSTALL_NOT_SHAREABLE
        lookup.assert_not_called()
        prompt.assert_not_called()

    def test_positive_twin_a_single_file_exe_is_not_refused_for_the_install(self, tmp_path):
        """The request goes on to the account lookup (stubbed to refuse there, so nothing runs)."""
        with patch.object(
            provision_session, "_resolve_before_prompt", return_value=ProvisionOutcome.ACCOUNT_NOT_FOUND
        ) as lookup:
            attempt = self._provision(
                _portable(tmp_path), Principal(kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT, user=_GMSA, password=None)
            )
        assert attempt.outcome is ProvisionOutcome.ACCOUNT_NOT_FOUND
        lookup.assert_called_once()

    def test_the_view_treats_it_as_a_pre_prompt_refusal(self):
        assert ProvisionOutcome.INSTALL_NOT_SHAREABLE in setup_mod._PRE_PROMPT_REFUSALS


class TestThePrivilegedHalfRefusesToo:
    """The elevated child re-validates every field; it refuses this too, as an invalid request."""

    def _payload(self, exe: Path, *, kind: PrincipalKind, user: str, password: str | None) -> dict[str, object]:
        return {
            "op": "register",
            "task_name": "DistrictSync_Daily",
            "exe": str(exe),
            "arguments": "--sis myedbc",
            "working_dir": str(exe.parent),
            "run_time": "03:00",
            "user": user,
            "password": password,
            "kind": kind.value,
            "run_highest": False,
            "wake_to_run": True,
        }

    def test_a_foreign_principal_against_a_per_user_copy_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(windows, "current_run_as_user", lambda: "PC\\admin")
        payload = self._payload(_installed_layout(tmp_path), kind=PrincipalKind.PASSWORD, user=_SERVICE, password="pw")
        with (
            patch.object(task_com, "register_task_definition") as reg,
            pytest.raises(ValueError, match="per-user installed copy"),
        ):
            elevated_apply._do_register(payload)
        reg.assert_not_called()

    def test_positive_twin_the_signed_in_account_and_the_single_file_exe_still_register(self, tmp_path, monkeypatch):
        monkeypatch.setattr(windows, "current_run_as_user", lambda: _SERVICE)
        own = self._payload(_installed_layout(tmp_path), kind=PrincipalKind.PASSWORD, user=_SERVICE, password="pw")
        with patch.object(task_com, "register_task_definition") as reg:
            elevated_apply._do_register(own)
        assert reg.call_count == 1
        monkeypatch.setattr(windows, "current_run_as_user", lambda: "PC\\admin")
        loose = self._payload(_portable(tmp_path), kind=PrincipalKind.PASSWORD, user=_SERVICE, password="pw")
        with patch.object(task_com, "register_task_definition") as reg:
            elevated_apply._do_register(loose)
        assert reg.call_count == 1


_REAL_PROFILE_ROOTS = task_com._user_profile_roots  # captured before the autouse fixture replaces it


class TestTheProfileRoots:
    """The real ``_user_profile_roots`` (every other test replaces it)."""

    def test_the_account_folders_and_the_profiles_directory_are_included(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
        monkeypatch.setattr(task_com, "_profiles_directory", lambda: tmp_path / "Users")
        roots = _REAL_PROFILE_ROOTS()
        assert tmp_path / "local" in roots
        assert tmp_path / "home" in roots
        assert tmp_path / "Users" in roots

    def test_nothing_set_and_no_home_never_raises(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("USERPROFILE", raising=False)
        monkeypatch.setattr(task_com, "_profiles_directory", lambda: None)

        def no_home() -> Path:
            raise RuntimeError("no home")

        monkeypatch.setattr(task_com.Path, "home", staticmethod(no_home))
        assert _REAL_PROFILE_ROOTS() == []

    def test_no_roots_at_all_fails_closed_for_the_installer_layout(self, tmp_path, monkeypatch):
        exe = _installed_layout(tmp_path)
        monkeypatch.setattr(task_com, "_user_profile_roots", lambda: [])
        assert task_com.runs_from_per_user_install(str(exe)) is True
        # Positive twin: without the installer's layout there is nothing to refuse.
        assert task_com.runs_from_per_user_install(str(_portable(tmp_path))) is False

    def test_a_copy_in_another_accounts_profile_counts(self, tmp_path, monkeypatch):
        other = tmp_path / "Users" / "someone-else" / "AppData" / "Local"
        other.mkdir(parents=True)
        exe = _installed_layout(other)
        monkeypatch.setattr(task_com, "_user_profile_roots", lambda: [tmp_path / "me", tmp_path / "Users"])
        assert task_com.runs_from_per_user_install(str(exe)) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows paths compare case-insensitively")
    def test_case_differences_still_match_on_windows(self, tmp_path, monkeypatch):
        exe = _installed_layout(tmp_path)
        monkeypatch.setattr(task_com, "_user_profile_roots", lambda: [Path(str(tmp_path).upper())])
        assert task_com.runs_from_per_user_install(str(exe)) is True


class TestTheProvisionOpRefusesBeforeAnyChange:
    def test_a_foreign_principal_against_a_per_user_copy_never_reaches_apply_provision(self, tmp_path, monkeypatch):
        monkeypatch.setattr(windows, "current_run_as_user", lambda: r"PC\admin")
        payload = {
            "op": "provision",
            "task_name": "DistrictSync_Daily",
            "exe": str(_installed_layout(tmp_path)),
            "arguments": "--sis myedbc",
            "working_dir": str(tmp_path),
            "run_time": "03:00",
            "user": _SERVICE,
            "password": "pw",
            "kind": PrincipalKind.PASSWORD.value,
            "run_highest": False,
            "wake_to_run": True,
            "source_data_dir": str(tmp_path / "profile"),
        }
        from src.scheduler import provisioning

        with (
            patch.object(provisioning, "apply_provision") as apply,
            pytest.raises(ValueError, match="per-user installed copy"),
        ):
            elevated_apply._do_provision(payload)
        apply.assert_not_called()

        # Positive twin: the single-file exe goes on to provisioning.
        payload["exe"] = str(_portable(tmp_path))
        with patch.object(provisioning, "apply_provision") as apply:
            elevated_apply._do_provision(payload)
        apply.assert_called_once()
