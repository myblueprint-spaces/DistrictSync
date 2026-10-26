"""DSYNC-33 — the schedule check's surfaces: the copy, the bands, and the Setup wiring.

``schedule_check_copy`` is pure and COUNTED, so its rules are pinned directly; the bands are
view glue, so they get render smokes on the two surfaces that paint them (Home, which re-entry
lands on, and Setup's own result slot when nothing rebuilt), each asserting the design
system's structural promises — one filled primary, text-tier log actions, no account, no path.
The Remove wiring is pinned at the view seam with every elevated call monkeypatched: **no test
here raises a UAC prompt or touches a real scheduled task.**
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

import src.ui_flet.handover_result as handover_result
import src.ui_flet.screens.setup as setup_mod
from src.scheduler import task_com
from src.scheduler.provision_session import ProvisionAttempt, ProvisionOutcome, RemovalAttempt, RevokeOutcome
from src.scheduler.provisioning import ProvisionStep
from src.scheduler.schedule_check import (
    CHECK_EXIT_NOT_SHARED,
    CHECK_EXIT_OK,
    INCONCLUSIVE,
    CheckVerdict,
    InstallState,
    RightState,
    ScheduleCheck,
)
from src.scheduler.task_com import PrincipalKind
from src.ui_flet import schedule_check_copy as copy
from src.ui_flet.components import COPY_DETAILS_LABEL, OPEN_LOG_FOLDER_LABEL
from src.ui_flet.handover_result import HandoverResult
from src.ui_flet.setup_errors import ACCOUNT_LOOKUP_FAILED_COPY, ACCOUNT_NOT_FOUND_COPY, classify_provision_step
from src.ui_flet.setup_flow import GMSA_IT_DOC_TITLE
from src.ui_flet.verdict import Verdict
from tests.test_ui_flet_machine_scope_handover import _force_machine_scope
from tests.test_ui_flet_render_smoke import _button_by_content, _drive_home, _dsync32_status, _find, _has_text
from tests.test_ui_flet_render_smoke import _has_text_containing as _contains
from tests.test_ui_flet_service_account import (
    _capture_provision,
    _drain,
    _register_service_account,
    _registered_args,
    _schedule_section,
    _settings,
    _stub_handover,
)

_GMSA = "CORP\\svc_districtsync$"
_PASSED = ScheduleCheck(CheckVerdict.OK, CHECK_EXIT_OK, RightState.GRANTED, InstallState.INSTALLED)
_LOGON = ScheduleCheck(
    CheckVerdict.LOGON_FAILURE, task_com.RESULT_LOGON_FAILURE, RightState.UNKNOWN, InstallState.NOT_PERMITTED
)


@pytest.fixture(autouse=True)
def _clean_slot():
    handover_result.reset()
    yield
    handover_result.reset()


@pytest.fixture
def stub_page() -> MagicMock:
    return MagicMock()


# --------------------------------------------------------------------------- #
# The copy (pure)                                                             #
# --------------------------------------------------------------------------- #
class TestTheCopy:
    def test_every_non_passing_verdict_has_a_note(self):
        """Completeness: a new verdict with no sentence is RED, never a blank band."""
        assert set(copy.VERDICT_NOTES) == set(CheckVerdict) - {CheckVerdict.OK}
        for verdict, note in copy.VERDICT_NOTES.items():
            assert note.strip(), verdict

    def test_a_passing_check_is_a_calm_confirmation_with_no_actions(self):
        band = copy.check_band(_PASSED, task_name="DistrictSync_Daily")
        assert band.verdict is Verdict.HEALTHY
        assert (band.headline, band.detail) == (copy.CHECK_OK_HEADLINE, copy.CHECK_OK_DETAIL)
        assert band.offers_log_actions is False

    @pytest.mark.parametrize("verdict", sorted(set(CheckVerdict) - INCONCLUSIVE - {CheckVerdict.OK}))
    def test_a_finding_is_a_failed_band_with_its_code_and_the_way_forward(self, verdict):
        band = copy.check_band(ScheduleCheck(verdict, 0x8007052E), task_name="DistrictSync_Daily")
        assert band.verdict is Verdict.FAILED
        assert band.headline == copy.CHECK_FAILED_HEADLINE
        assert band.detail.startswith(copy.VERDICT_NOTES[verdict])
        assert "(Windows result 0x8007052E.)" in band.detail
        assert copy.CHECK_STILL_SCHEDULED_FAILED in band.detail, "the nightly was NOT rolled back"
        assert band.detail.endswith(copy.CHECK_RETRY_HINT)
        assert band.offers_log_actions is True

    @pytest.mark.parametrize("verdict", sorted(INCONCLUSIVE))
    def test_an_inconclusive_check_is_never_worded_as_a_finding(self, verdict):
        band = copy.check_band(ScheduleCheck(verdict), task_name="DistrictSync_Daily")
        assert band.verdict is Verdict.WARNING
        assert band.headline == copy.CHECK_INCONCLUSIVE_HEADLINE
        assert copy.CHECK_STILL_SCHEDULED_INCONCLUSIVE in band.detail
        assert copy.CHECK_RETRY_HINT not in band.detail
        assert copy.CHECK_PREREQUISITES_HINT not in band.detail
        assert "Windows result" not in band.detail, "no code was read"

    def test_the_prerequisites_pointer_is_only_for_windows_side_failures(self):
        windows_side = copy.check_band(_LOGON, task_name="t").detail
        app_side = copy.check_band(ScheduleCheck(CheckVerdict.NOT_SHARED, CHECK_EXIT_NOT_SHARED), task_name="t").detail
        assert copy.CHECK_PREREQUISITES_HINT in windows_side
        assert GMSA_IT_DOC_TITLE in windows_side, "the IT page is named so it can be asked for"
        assert copy.CHECK_PREREQUISITES_HINT not in app_side

    @pytest.mark.parametrize(
        ("check", "hint"),
        [
            (ScheduleCheck(CheckVerdict.LOGON_FAILURE, batch_right=RightState.DENIED), copy.BATCH_DENIED_HINT),
            (ScheduleCheck(CheckVerdict.TIMEOUT, batch_right=RightState.DENIED), copy.BATCH_DENIED_HINT),
            (ScheduleCheck(CheckVerdict.LOGON_FAILURE, installed=InstallState.NOT_PERMITTED), copy.NOT_PERMITTED_HINT),
            (ScheduleCheck(CheckVerdict.LOGON_FAILURE, installed=InstallState.NOT_INSTALLED), copy.NOT_INSTALLED_HINT),
            (ScheduleCheck(CheckVerdict.BATCH_LOGON, batch_right=RightState.NOT_DIRECT), copy.BATCH_NOT_DIRECT_HINT),
        ],
    )
    def test_a_definite_advisory_answer_earns_its_hint(self, check, hint):
        assert hint in copy.check_band(check, task_name="t").detail

    def test_not_granted_directly_is_offered_only_beside_the_failure_it_explains(self):
        """The right is routinely granted through a group — beside any OTHER failure, "not
        granted directly" would read as a cause it is not."""
        check = ScheduleCheck(CheckVerdict.LOGON_FAILURE, batch_right=RightState.NOT_DIRECT)
        assert copy.BATCH_NOT_DIRECT_HINT not in copy.check_band(check, task_name="t").detail

    def test_unknown_advisory_answers_are_never_a_hint(self):
        check = ScheduleCheck(CheckVerdict.BATCH_LOGON)  # both advisory facts UNKNOWN
        assert copy.prerequisite_hints(check) == []
        detail = copy.check_band(check, task_name="t").detail
        for hint in (copy.BATCH_DENIED_HINT, copy.NOT_PERMITTED_HINT, copy.NOT_INSTALLED_HINT):
            assert hint not in detail

    def test_copy_details_is_bounded(self):
        details = copy.check_details(_LOGON, task_name="DistrictSync_Daily")
        assert details.splitlines() == [
            "DistrictSync schedule check",
            "Task: DistrictSync_Daily",
            "Result: LOGON_FAILURE",
            "Windows result: 0x8007052E",
            "Batch logon right (direct): unknown",
            "Installed on this computer: not_permitted",
        ]

    def test_no_copy_names_an_account_a_path_or_an_address(self):
        every = [
            *copy.VERDICT_NOTES.values(),
            copy.CHECK_OK_HEADLINE,
            copy.CHECK_OK_DETAIL,
            copy.CHECK_FAILED_HEADLINE,
            copy.CHECK_INCONCLUSIVE_HEADLINE,
            copy.BATCH_DENIED_HINT,
            copy.BATCH_NOT_DIRECT_HINT,
            copy.NOT_INSTALLED_HINT,
            copy.NOT_PERMITTED_HINT,
            ACCOUNT_NOT_FOUND_COPY,
            ACCOUNT_LOOKUP_FAILED_COPY,
        ]
        for text in every:
            assert "@" not in text
            assert ":\\" not in text and "ProgramData" not in text
            assert "CORP" not in text


class TestTheLookupCopy:
    def test_both_say_nothing_changed_and_no_prompt_was_shown(self):
        for text in (ACCOUNT_NOT_FOUND_COPY, ACCOUNT_LOOKUP_FAILED_COPY):
            assert "Nothing was changed" in text or "nothing was changed" in text
            assert "no permission prompt was shown" in text

    def test_an_unreachable_domain_controller_is_not_called_a_typo(self):
        assert "domain controller" in ACCOUNT_LOOKUP_FAILED_COPY
        assert "Check the name" not in ACCOUNT_LOOKUP_FAILED_COPY

    def test_not_found_names_the_shape_and_still_hedges_on_the_domain_controller(self):
        assert "trailing $" in ACCOUNT_NOT_FOUND_COPY
        assert "domain controller" in ACCOUNT_NOT_FOUND_COPY


# --------------------------------------------------------------------------- #
# Home — where re-entry lands                                                 #
# --------------------------------------------------------------------------- #
def _home_with(monkeypatch, check: ScheduleCheck | None):
    handover_result.remember(HandoverResult.handed_over(check=check))
    return _drive_home(monkeypatch, _dsync32_status(0, gap=False))


def _log_action_labels(tree) -> list[str]:
    wanted = {OPEN_LOG_FOLDER_LABEL, COPY_DETAILS_LABEL}
    return sorted(c.content for c in _find(tree, ft.TextButton) if c.content in wanted)


class TestHomeRendersTheCheck:
    def test_a_passing_check_is_a_calm_confirmation_under_the_handover(self, monkeypatch):
        surface = _home_with(monkeypatch, _PASSED)
        assert _has_text(surface, setup_mod.HANDOVER_DONE_HEADLINE)
        assert _has_text(surface, copy.CHECK_OK_HEADLINE)
        assert _has_text(surface, copy.CHECK_OK_DETAIL)
        assert _log_action_labels(surface) == []
        assert len(_find(surface, ft.FilledButton)) == 1

    def test_a_failed_check_is_a_toned_band_with_its_code_hints_and_log_actions(self, monkeypatch):
        surface = _home_with(monkeypatch, _LOGON)
        assert _has_text(surface, copy.CHECK_FAILED_HEADLINE)
        assert _contains(surface, "0x8007052E")
        assert _contains(surface, copy.NOT_PERMITTED_HINT)
        assert _log_action_labels(surface) == [COPY_DETAILS_LABEL, OPEN_LOG_FOLDER_LABEL]
        assert len(_find(surface, ft.FilledButton)) == 1, "the log actions are text tier"
        assert not _contains(surface, _GMSA)

    def test_the_positive_twin_one_filled_primary_still_exists_on_that_surface(self, monkeypatch):
        """The ONE filled primary is Home's own fix CTA; this pins that the
        failure band ADDED no primary to the one Home already has."""
        baseline = len(_find(_home_with(monkeypatch, None), ft.FilledButton))
        handover_result.reset()
        assert len(_find(_home_with(monkeypatch, _LOGON), ft.FilledButton)) == baseline

    def test_no_check_paints_no_band(self, monkeypatch):
        surface = _home_with(monkeypatch, None)
        assert _has_text(surface, setup_mod.HANDOVER_DONE_HEADLINE), "positive evidence the handover rendered"
        assert not _has_text(surface, copy.CHECK_OK_HEADLINE)
        assert not _has_text(surface, copy.CHECK_FAILED_HEADLINE)


# --------------------------------------------------------------------------- #
# Setup — its own result slot (no rebuild) and the pre-UAC refusal            #
# --------------------------------------------------------------------------- #
class TestSetupRendersTheCheck:
    def _press(self, tmp_path, stub_page, monkeypatch, attempt, *, handed_over):
        cfg = _settings(tmp_path, monkeypatch, input_dir="D:\\GDE", output_dir="D:\\Exports")
        _capture_provision(monkeypatch, results=[attempt])
        _stub_handover(monkeypatch, handed_over=handed_over)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)
        return tree

    def test_handed_over_without_a_rebuild_paints_the_check_under_the_handover(self, tmp_path, stub_page, monkeypatch):
        attempt = ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED, check=_LOGON)
        tree = self._press(tmp_path, stub_page, monkeypatch, attempt, handed_over=True)
        assert _has_text(tree, setup_mod.HANDOVER_DONE_HEADLINE)
        assert _has_text(tree, copy.CHECK_FAILED_HEADLINE)
        assert _log_action_labels(tree) == [COPY_DETAILS_LABEL, OPEN_LOG_FOLDER_LABEL]
        assert len(_find(tree, ft.FilledButton)) == 1, "Schedule nightly sync stays the one primary"

    def test_switch_unconfirmed_but_registered_still_shows_the_check(self, tmp_path, stub_page, monkeypatch):
        attempt = ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED, check=_PASSED)
        tree = self._press(tmp_path, stub_page, monkeypatch, attempt, handed_over=False)
        assert _has_text(tree, setup_mod.SCOPE_SWITCH_UNCONFIRMED_HEADLINE)
        assert _has_text(tree, copy.CHECK_OK_HEADLINE)

    @pytest.mark.parametrize(
        ("outcome", "text"),
        [
            (ProvisionOutcome.ACCOUNT_NOT_FOUND, ACCOUNT_NOT_FOUND_COPY),
            (ProvisionOutcome.ACCOUNT_LOOKUP_FAILED, ACCOUNT_LOOKUP_FAILED_COPY),
        ],
    )
    def test_a_pre_uac_refusal_says_nothing_changed(self, tmp_path, stub_page, monkeypatch, outcome, text):
        tree = self._press(tmp_path, stub_page, monkeypatch, ProvisionAttempt(outcome=outcome), handed_over=False)
        assert _has_text(tree, setup_mod._REGISTER_REFUSED_HEADLINE)
        assert _has_text(tree, text)
        assert not _has_text(tree, setup_mod.SCOPE_ATTEMPT_FAILED_HEADLINE)


# --------------------------------------------------------------------------- #
# Remove — the gMSA's folder access goes with it                               #
# --------------------------------------------------------------------------- #
class TestRemoveRevokesTheManagedServiceAccount:
    def _mount(self, tmp_path, stub_page, monkeypatch, *, kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT, shared=True):
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_GMSA if kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT else "CORP\\svc",
            schedule_run_as_kind=kind.value,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        if shared:
            _force_machine_scope(monkeypatch, tmp_path)
        revoke_calls: list[tuple] = []
        delete_calls: list[str] = []
        monkeypatch.setattr(
            "src.scheduler.windows.delete_task",
            lambda name: delete_calls.append(name) or (True, "The scheduled task was removed."),
        )
        tree, _ = _schedule_section(cfg, stub_page)
        return cfg, tree, revoke_calls, delete_calls

    def _revoke_returns(self, monkeypatch, calls: list, attempt: RemovalAttempt) -> None:
        def _fake(task_name, *, user, kind):
            calls.append((task_name, user, kind))
            return attempt

        monkeypatch.setattr(setup_mod, "request_removal_with_revoke", _fake)

    def _remove(self, tree, stub_page) -> None:
        _button_by_content(tree, "Remove nightly sync").on_click(None)
        _drain(stub_page)

    def test_the_recorded_gmsa_is_removed_with_its_access_in_one_op(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, revokes, deletes = self._mount(tmp_path, stub_page, monkeypatch)
        self._revoke_returns(
            monkeypatch, revokes, RemovalAttempt(True, "Schedule removed and confirmed.", RevokeOutcome.REVOKED)
        )
        self._remove(tree, stub_page)

        assert revokes == [(cfg.schedule_task_name, _GMSA, PrincipalKind.MANAGED_SERVICE_ACCOUNT)]
        assert deletes == [], "ONE elevated op — no second removal"
        assert cfg.schedule_registered is False
        assert _contains(tree, setup_mod.REVOKE_DONE_NOTE)

    def test_a_revoke_that_could_not_run_falls_back_to_the_ordinary_remove(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, revokes, deletes = self._mount(tmp_path, stub_page, monkeypatch)
        self._revoke_returns(monkeypatch, revokes, RemovalAttempt(False, "", RevokeOutcome.FALL_BACK))
        self._remove(tree, stub_page)

        assert deletes == [cfg.schedule_task_name]
        assert cfg.schedule_registered is False
        assert _contains(tree, classify_provision_step(ProvisionStep.PRUNE)), "says the access stayed"
        assert not _contains(tree, setup_mod.REVOKE_DONE_NOTE)

    def test_a_failed_prune_says_the_access_stayed(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, revokes, _deletes = self._mount(tmp_path, stub_page, monkeypatch)
        self._revoke_returns(
            monkeypatch, revokes, RemovalAttempt(True, "Schedule removed and confirmed.", RevokeOutcome.NOT_REVOKED)
        )
        self._remove(tree, stub_page)
        assert cfg.schedule_registered is False
        assert _contains(tree, classify_provision_step(ProvisionStep.PRUNE))

    def test_a_password_principal_is_removed_the_ordinary_way(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, revokes, deletes = self._mount(tmp_path, stub_page, monkeypatch, kind=PrincipalKind.PASSWORD)
        self._revoke_returns(monkeypatch, revokes, RemovalAttempt(True, "x", RevokeOutcome.REVOKED))
        self._remove(tree, stub_page)
        assert revokes == []
        assert deletes == [cfg.schedule_task_name]
        assert not _contains(tree, setup_mod.REVOKE_DONE_NOTE)

    def test_a_per_user_install_is_removed_the_ordinary_way(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, revokes, deletes = self._mount(tmp_path, stub_page, monkeypatch, shared=False)
        self._revoke_returns(monkeypatch, revokes, RemovalAttempt(True, "x", RevokeOutcome.REVOKED))
        self._remove(tree, stub_page)
        assert revokes == []
        assert deletes == [cfg.schedule_task_name]
