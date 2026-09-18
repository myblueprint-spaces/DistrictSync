"""Plan 0049 S-2b — the machine-scope handover at the VIEW seam.

``src/ui_flet/screens/setup.py``, ``screens/home.py`` and ``shell.py`` are coverage-omitted
glue, so what is pinned here is the WIRING, not the rendering: that the new gate is reachable
with its note, that a provisioning dispatch is IMPOSSIBLE without the confirm, that
``complete_handover`` is called on every outcome the child can report, that ``persist`` is a
no-op on every non-success path (with its positive twin), that each ``HandoverOutcome`` shape
has a surface — including the refusal, which must also freeze the rest of Setup — and that the
one-shot result survives the re-entry that destroys the surface which produced it.

**No test here may raise a real UAC prompt.** Every elevated seam (``request_provision``,
``complete_handover``) is monkeypatched, following ``tests/test_elevated_apply.py``'s rule; the
harness helpers that do it are shared from ``tests.test_ui_flet_service_account`` so this file
adds no second way to drive the flow.

**Machine scope is forced through ALL THREE of the trust predicate's raw reads.** Seeding fewer
reaches the real Win32 API — green on a Windows developer box, red on CI's Linux leg, which
cost two PRs on this plan (#132, #133). ``_force_machine_scope`` is the one place that does it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

import src.ui_flet.handover_result as handover_result
import src.ui_flet.screens.home as home_mod
import src.ui_flet.screens.setup as setup_mod
from src.config.app_config import AppConfig
from src.scheduler import task_com
from src.scheduler.provision_session import ProvisionAttempt, ProvisionOutcome
from src.scheduler.provisioning import ProvisionStep
from src.ui_flet import tokens
from src.ui_flet.handover_result import HandoverBanner, HandoverResult
from src.ui_flet.setup_errors import _unclassified_copy, classify_provision_step
from src.ui_flet.setup_gates import RegisterBlock, ScheduleAccountFacts, register_block
from src.utils import paths
from src.utils.paths import MachineScopeRefusedReason
from tests.test_ui_flet_render_smoke import (
    _button_by_content,
    _has_text,
    _has_text_containing,
    _iter_controls,
    _textfield_by_label,
)
from tests.test_ui_flet_service_account import (
    _SERVICE,
    _SIGNED_IN,
    _account_field,
    _capture_provision,
    _capture_register,
    _confirm_scope,
    _drain,
    _press_register,
    _register_service_account,
    _registered_args,
    _schedule_section,
    _scope_dialog,
    _settings,
    _stub_handover,
)

_PASSWORD_FIELD = "Windows account password"

#: Folders the reach heuristic has nothing against, spelled as literals. ``tmp_path`` cannot be
#: used: on Windows it sits under ``C:\Users\<name>\AppData\Local\Temp``, so the
#: under-a-user-profile rule fires there and not on Linux — a three-OS divergence in a test
#: about something else. ``is_complete()`` only checks these are non-empty, so they need not
#: exist (the folders card's own Save gate is what checks that, and it is not under test here).
_PLAIN_FOLDERS = {"input_dir": "D:\\GDE", "output_dir": "D:\\Exports"}

#: The DACL a provisioned profile actually gets, as ``(ace type, SID)`` pairs — the shape
#: ``paths._read_dacl_aces`` returns. Same shape as ``tests/test_paths.py::_TRUSTED_ACES``.
_TRUSTED_ACES = (
    (0, "S-1-5-18"),
    (0, "S-1-5-32-544"),
    (0, "S-1-5-21-1-2-3-1001"),
)


@pytest.fixture(autouse=True)
def _clean_slot():
    """The one-shot slot is a module global; a leak would leak between tests."""
    handover_result.reset()
    yield
    handover_result.reset()


@pytest.fixture
def stub_page() -> MagicMock:
    return MagicMock()


def _force_machine_scope(monkeypatch, tmp_path) -> None:
    """Make ``paths.is_machine_scope()`` answer True, through all three raw reads.

    ``_assert_machine_dir_trusted`` makes THREE reads (the HKLM switch, the owner SID + DACL
    control word, and the DACL ace walk). A driver that seeds only two leaves the third hitting
    the real Win32 API: an ``OSError`` → ``INACCESSIBLE`` on Linux, and a real ``%TEMP%`` DACL
    on Windows. Never the real ``C:\\ProgramData\\DistrictSync`` — nothing outside tmp is read,
    created or trusted here.
    """
    target = tmp_path / "ProgramData" / "DistrictSync"
    (target / "runs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "machine_data_dir", lambda: target)
    monkeypatch.setattr(paths, "_machine_switch_on", lambda: True)
    monkeypatch.setattr(paths, "_read_dir_security", lambda path: ("S-1-5-32-544", paths._SE_DACL_PROTECTED))
    monkeypatch.setattr(paths, "_read_dacl_aces", lambda path: _TRUSTED_ACES)
    monkeypatch.delenv("DISTRICTSYNC_DATA_DIR", raising=False)
    paths.reset_data_dir_pin()
    monkeypatch.setattr(paths, "is_machine_scope", lambda: paths._pinned()[1])


def _stub_secret_store(monkeypatch, *, has_secret: bool) -> None:
    """Drive the delivery-secret gate through its REAL seam (``secret_store.select_store``).

    Patching ``delivery_secret_unreadable`` itself would prove only that the view calls
    something; this proves the whole chain — the select rule, ``has_secret`` and the view's
    once-per-paint read — resolves to a gate an admin can see.
    """
    from src.sftp import secret_store

    store = MagicMock()
    store.has_secret.return_value = has_secret
    monkeypatch.setattr(secret_store, "select_store", lambda: store)


def _delivery_settings(tmp_path, monkeypatch, **over) -> AppConfig:
    """A completed install with delivery ON and a host/username, so a secret CAN exist."""
    fields = {"sftp_enabled": True, "sftp_host": "sftp.example.com", "sftp_username": "sd48"}
    fields.update(over)
    return _settings(tmp_path, monkeypatch, **fields)


# --------------------------------------------------------------------------- #
# S-2b.1 — the new gate, reachable at the view, with its note                  #
# --------------------------------------------------------------------------- #
class TestTheDeliverySecretGate:
    def test_an_unreadable_delivery_secret_closes_the_gate_and_shows_its_note(self, tmp_path, stub_page, monkeypatch):
        cfg = _delivery_settings(tmp_path, monkeypatch)
        _stub_secret_store(monkeypatch, has_secret=False)
        tree, _ = _schedule_section(cfg, stub_page)

        field = _account_field(tree)
        field.value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        field.on_change(None)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text(tree, setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE)

    def test_the_positive_twin_a_readable_secret_leaves_the_gate_open(self, tmp_path, stub_page, monkeypatch):
        """Without this, the row above would pass on a section that can never register."""
        cfg = _delivery_settings(tmp_path, monkeypatch)
        _stub_secret_store(monkeypatch, has_secret=True)
        tree, _ = _schedule_section(cfg, stub_page)

        field = _account_field(tree)
        field.value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        field.on_change(None)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is False
        assert not _has_text(tree, setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE)

    def test_it_never_fires_for_the_signed_in_account(self, tmp_path, stub_page, monkeypatch):
        """The gate fires only when the register would PROVISION. An unreadable delivery
        password on a per-user install is the Delivery section's problem, not this gate's."""
        cfg = _delivery_settings(tmp_path, monkeypatch)
        _stub_secret_store(monkeypatch, has_secret=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        _account_field(tree).on_change(None)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is False
        assert not _has_text(tree, setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE)

    def test_the_note_offers_BOTH_ways_through(self):
        """The escape must be OFFERED, not discovered. The password may have been saved by a
        different Windows account, whose Credential Manager this one can never reach — so a
        note that named only "save it again" would strand exactly that admin."""
        note = setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE
        assert "Delivery to SpacesEDU" in note, "it must name the section that fixes it"
        assert "save the password again" in note
        assert "turn delivery off" in note, "the second way through must be named, not implied"
        # …and what turning it off COSTS, in plain words: the CSVs are still written.
        assert "still write your CSV files" in note
        assert "won't send them to SpacesEDU" in note
        assert "@" not in note  # scripts/check_no_emails.py scans every tracked file

    def test_the_reconcile_blocks_on_the_delivery_password_not_the_account_or_the_run_time(
        self, tmp_path, stub_page, monkeypatch
    ):
        """``BLOCKED`` hardcodes "fix the run time" and ``BLOCKED_ACCOUNT`` says "check the
        Windows account and its password" — both misdirects here, one field apart. The new member
        is the FIRST to make the reconcile's totality arm a live path, and it carries its own
        outcome so the Save banner names the delivery password instead."""
        from src.ui_flet.setup_flow import ReconcileOutcome

        cfg = _delivery_settings(tmp_path, monkeypatch)
        _stub_secret_store(monkeypatch, has_secret=False)
        tree, handle = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"

        assert handle.trigger_register() is ReconcileOutcome.BLOCKED_DELIVERY_SECRET
        assert _has_text(tree, setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE)

    def test_the_gate_reads_the_store_ONCE_per_paint_pass(self, tmp_path, stub_page, monkeypatch):
        """It is an I/O read feeding two consumers (the button gate and the field note), so one
        keystroke must not cost two. Counted on the view's own seam."""
        cfg = _delivery_settings(tmp_path, monkeypatch)
        _stub_secret_store(monkeypatch, has_secret=False)
        calls = {"n": 0}
        real = setup_mod.delivery_secret_unreadable

        def _counting(**kwargs):
            calls["n"] += 1
            return real(**kwargs)

        monkeypatch.setattr(setup_mod, "delivery_secret_unreadable", _counting)
        tree, _ = _schedule_section(cfg, stub_page)
        calls["n"] = 0  # discard the mount's own gate read
        _account_field(tree).on_change(None)

        assert calls["n"] == 1

    def test_the_gate_re_opens_once_the_password_is_saved_again(self, tmp_path, stub_page, monkeypatch):
        """The note tells the admin to save the password in the card below, so the read must NOT
        be memoised for the section's lifetime — a cached True would keep the gate shut after
        they did exactly what it asked."""
        cfg = _delivery_settings(tmp_path, monkeypatch)
        readable = {"yes": False}
        from src.sftp import secret_store

        store = MagicMock()
        store.has_secret.side_effect = lambda *_a: readable["yes"]
        monkeypatch.setattr(secret_store, "select_store", lambda: store)

        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True

        readable["yes"] = True
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is False

    def test_every_register_block_member_has_a_note_or_is_deliberately_silent(self):
        """The view owns the copy for this enum, and ``_account_block_note`` returns ``""`` for
        anything it does not handle — so a new member with no branch paints a dead control. The
        two silent members are named here explicitly rather than derived, so adding a third
        silent one is a decision somebody makes on purpose."""
        silent = {RegisterBlock.NONE, RegisterBlock.INCOMPLETE, RegisterBlock.RUN_TIME}
        facts = ScheduleAccountFacts(
            typed=_SERVICE, current=_SIGNED_IN, password_supplied=False, recorded=None, schedule_registered=True
        )
        for block in RegisterBlock:
            note = setup_mod.account_block_note(block, facts)
            if block in silent:
                assert note == "", block
            else:
                assert note, f"{block} has no note — its disabled button would have no cause"


# --------------------------------------------------------------------------- #
# S-2b.2 — the foreshadow                                                      #
# --------------------------------------------------------------------------- #
class TestTheForeshadow:
    def test_it_appears_the_moment_the_typed_name_goes_foreign(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        tree, _ = _schedule_section(cfg, stub_page)
        assert not _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE)

        field = _account_field(tree)
        field.value = _SERVICE
        field.on_change(None)
        assert _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE)

        field.value = _SIGNED_IN
        field.on_change(None)
        assert not _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE)

    def test_it_shows_even_while_the_gate_is_closed(self, tmp_path, stub_page, monkeypatch):
        """A permanent machine-wide relocation must not first be mentioned at the point of no
        return — so it is keyed on the TYPED account, never on the gate. The pairing with the
        password note is exactly what explains why that note matters."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = _SERVICE  # no password → ACCOUNT_NEEDS_PASSWORD
        field.on_change(None)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text(tree, setup_mod._ACCOUNT_PASSWORD_NOTE)
        assert _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE)

    def test_it_says_a_confirm_is_coming_and_what_moves(self):
        note = setup_mod._SCOPE_FORESHADOW_NOTE
        assert "moves this computer's DistrictSync settings" in note
        assert setup_mod._SCOPE_CONTENTS in note
        assert "asked to confirm" in note

    def test_an_already_shared_computer_is_not_told_its_settings_will_move(self, tmp_path, stub_page, monkeypatch):
        """ "Remove nightly sync" deliberately does NOT un-provision, so an already-shared
        computer really does reach this field again — and telling that admin their settings are
        about to move would be false."""
        _force_machine_scope(monkeypatch, tmp_path)
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = _SERVICE
        field.on_change(None)

        assert _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE_SHARED)
        assert not _has_text(tree, setup_mod._SCOPE_FORESHADOW_NOTE)


# --------------------------------------------------------------------------- #
# S-2b.2 — the confirm gates the dispatch                                      #
# --------------------------------------------------------------------------- #
class TestTheConfirm:
    def _armed(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        seen = _capture_provision(monkeypatch)
        handover = _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        return cfg, tree, seen, handover

    def test_a_press_alone_dispatches_NOTHING(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, seen, handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        _drain(stub_page)

        assert seen["calls"] == 0, "a provisioning dispatch happened without the confirm"
        assert handover["calls"] == 0
        assert cfg.schedule_registered is False
        assert _scope_dialog(stub_page) is not None

    def test_cancel_dispatches_nothing_and_changes_nothing(self, tmp_path, stub_page, monkeypatch):
        cfg, tree, seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        dialog = _scope_dialog(stub_page)
        cancel = next(b for b in dialog.actions if getattr(b, "content", None) == setup_mod.SCOPE_CONFIRM_CANCEL_LABEL)
        cancel.on_click(None)
        _drain(stub_page)

        assert seen["calls"] == 0
        assert cfg.schedule_registered is False
        # The typed answers survive a cancel — nothing was attempted, so nothing is cleared.
        assert _account_field(tree).value == _SERVICE
        assert _textfield_by_label(tree, _PASSWORD_FIELD).value == "pw"

    def test_the_positive_twin_continue_dispatches_exactly_once(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree, seen, handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        _confirm_scope(stub_page)
        _drain(stub_page)

        assert seen["calls"] == 1
        assert handover["calls"] == 1

    def test_enter_on_the_password_field_cannot_bypass_the_confirm(self, tmp_path, stub_page, monkeypatch):
        """``on_submit`` is the path that still reaches ``_register`` while the primary is gated,
        so it is the one a bypass would come through."""
        _cfg, tree, seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        password = _textfield_by_label(tree, _PASSWORD_FIELD)
        password.on_change(None)
        password.on_submit(None)
        _drain(stub_page)

        assert seen["calls"] == 0
        assert _scope_dialog(stub_page) is not None

    def test_the_confirm_names_the_folder_what_moves_and_that_it_is_one_way(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree, _seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        dialog = _scope_dialog(stub_page)
        body = " ".join(v for c in _iter_controls(dialog.content) if isinstance(v := getattr(c, "value", None), str))

        # 1. what moves, and to where.
        assert setup_mod.MACHINE_SCOPE_FOLDER in body
        assert setup_mod._SCOPE_CONTENTS in body
        # 2. who can reach it afterwards — and who cannot, at all.
        assert "Every administrator of this computer" in body
        assert "not an administrator" in body
        assert "can't take it away again" in body
        # 3. one-way, INCLUDING the one thing an admin would try.
        assert "can't move the settings back" in body
        assert "Remove nightly sync won't undo it" in body
        assert "still have to build" in body, "it must say un-provisioning does not exist yet"

    def test_the_confirm_has_no_filled_default(self, tmp_path, stub_page, monkeypatch):
        """Deliberate, and the same rule ``_show_downgrade_dialog`` follows: an irreversible,
        machine-wide change must be CHOSEN, never defaulted into by pressing the button the
        dialog visually pre-selects."""
        _cfg, tree, _seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        dialog = _scope_dialog(stub_page)

        assert not [b for b in dialog.actions if isinstance(b, ft.FilledButton)]
        assert [b for b in dialog.actions if isinstance(b, ft.OutlinedButton)]
        assert [b for b in dialog.actions if isinstance(b, ft.TextButton)]

    def test_an_already_shared_computer_gets_the_access_wording(self, tmp_path, stub_page, monkeypatch):
        _force_machine_scope(monkeypatch, tmp_path)
        _cfg, tree, _seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        _press_register(tree)
        dialog = _scope_dialog(stub_page)
        body = " ".join(v for c in _iter_controls(dialog.content) if isinstance(v := getattr(c, "value", None), str))

        assert setup_mod._SCOPE_CONFIRM_ALREADY in body
        assert setup_mod._SCOPE_CONFIRM_MOVE not in body

    def test_a_malformed_run_time_is_refused_BEFORE_the_confirm(self, tmp_path, stub_page, monkeypatch):
        """``register_block`` only refuses a BLANK run time, so a malformed one would otherwise
        ask an admin to approve an irreversible, machine-wide change and only then tell them the
        time was wrong."""
        _cfg, tree, seen, _handover = self._armed(tmp_path, stub_page, monkeypatch)
        _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)").value = "99:99"
        _press_register(tree)
        _drain(stub_page)

        assert _scope_dialog(stub_page) is None, "the confirm was raised for a press that cannot run"
        assert seen["calls"] == 0
        assert _has_text(tree, setup_mod._RUN_TIME_ERROR_HEADLINE)

    def test_the_signed_in_account_never_sees_the_confirm(self, tmp_path, stub_page, monkeypatch):
        """The pre-S-2b world, unchanged: no foreign principal, no provision, no modal."""
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        _press_register(tree)
        _drain(stub_page)

        assert _scope_dialog(stub_page) is None
        assert seen["calls"] == 1


# --------------------------------------------------------------------------- #
# S-2b.1 — the folder-reach warning INSIDE the confirm                         #
# --------------------------------------------------------------------------- #
class TestTheFolderReachWarning:
    def _confirm_body(self, tmp_path, stub_page, monkeypatch, *, unc="", on_remount=None, **folders):
        over = dict(_PLAIN_FOLDERS)
        over.update(folders)
        cfg = _settings(tmp_path, monkeypatch, **over)
        # The ONE syscall seam (``src/utils/unc.py``'s own convention), so the pure rule above
        # it is exercised identically on Windows, Linux and macOS.
        monkeypatch.setattr("src.utils.unc.unc_target", lambda path: unc)
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = setup_mod._build_schedule_section(stub_page, cfg, on_remount=on_remount)
        _account_field(tree).value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        _press_register(tree)
        return tree, _scope_dialog(stub_page)

    def test_a_mapped_drive_earns_the_warning_and_its_note(self, tmp_path, stub_page, monkeypatch):
        _tree, dialog = self._confirm_body(
            tmp_path, stub_page, monkeypatch, output_dir="Z:\\Exports", unc="\\\\server\\share\\Exports"
        )
        assert _has_text_containing(dialog.content, setup_mod.SCOPE_FOLDER_WARNING_HEADLINE)
        assert _has_text_containing(dialog.content, setup_mod._SCOPE_FOLDER_WARNING_DETAIL)
        assert _has_text_containing(dialog.content, "Z:\\Exports")

    def test_it_is_a_warning_and_never_a_gate(self, tmp_path, stub_page, monkeypatch):
        """``reach_unconfirmed`` is wrong in BOTH directions, which is why it was demoted out of
        ``RegisterBlock``. It may not disable the confirm, and the dispatch must still happen."""
        cfg = _settings(tmp_path, monkeypatch, **{**_PLAIN_FOLDERS, "output_dir": "Z:\\Exports"})
        monkeypatch.setattr("src.utils.unc.unc_target", lambda path: "\\\\server\\share\\Exports")
        seen = _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
        _press_register(tree)
        dialog = _scope_dialog(stub_page)
        action = next(
            b for b in dialog.actions if getattr(b, "content", None) == setup_mod.SCOPE_CONFIRM_CONTINUE_LABEL
        )

        assert action.disabled is False
        action.on_click(None)
        _drain(stub_page)
        assert seen["calls"] == 1

    def test_the_replacement_is_offered_when_windows_gave_us_one(self, tmp_path, stub_page, monkeypatch):
        _tree, dialog = self._confirm_body(
            tmp_path,
            stub_page,
            monkeypatch,
            output_dir="Z:\\Exports",
            unc="\\\\server\\share\\Exports",
            on_remount=lambda: None,
        )
        label = setup_mod.SCOPE_FOLDER_REPLACE_LABEL.format(
            path="\\\\server\\share\\Exports", folder=setup_mod.SCOPE_FOLDER_OUTPUT_LABEL
        )
        assert any(getattr(c, "content", None) == label for c in _iter_controls(dialog.content))

    def test_no_replacement_is_offered_when_there_is_nothing_to_offer(self, tmp_path, stub_page, monkeypatch):
        """``unc_replacement`` is ``""`` when the resolution FAILED as well as when the path is
        not a mapped drive, so a button on a non-empty string can never offer a path we did not
        get from Windows. Here the warning still fires (the folder is under a user profile)."""
        _tree, dialog = self._confirm_body(
            tmp_path,
            stub_page,
            monkeypatch,
            output_dir="C:\\Users\\ted\\Exports",
            unc="",
            on_remount=lambda: None,
        )
        assert _has_text_containing(dialog.content, setup_mod.SCOPE_FOLDER_WARNING_HEADLINE)
        assert not [
            c
            for c in _iter_controls(dialog.content)
            if isinstance(getattr(c, "content", None), str) and "instead" in str(c.content)
        ]

    def test_public_is_exempt(self, tmp_path, stub_page, monkeypatch):
        """``C:\\Users\\Public`` is readable by every account on the computer by design — the
        wrong end of the heuristic, and the helper handles it."""
        _tree, dialog = self._confirm_body(
            tmp_path, stub_page, monkeypatch, output_dir="C:\\Users\\Public\\Exports", unc=""
        )
        assert not _has_text_containing(dialog.content, setup_mod.SCOPE_FOLDER_WARNING_HEADLINE)

    def test_the_replacement_writes_the_path_and_re_renders_the_host(self, tmp_path, stub_page, monkeypatch):
        """The write has to be paired with a re-render: the folders card seeds its fields at
        BUILD time, so a write without one would leave a live control contradicting the config
        and its next Save would put the stale value straight back."""
        cfg = _settings(tmp_path, monkeypatch, **{**_PLAIN_FOLDERS, "output_dir": "Z:\\Exports"})
        monkeypatch.setattr("src.utils.unc.unc_target", lambda path: "\\\\server\\share\\Exports")
        remounts = {"n": 0}
        card, _handle = setup_mod._build_schedule_section(
            stub_page, cfg, on_remount=lambda: remounts.__setitem__("n", remounts["n"] + 1)
        )
        _account_field(card).value = _SERVICE
        _textfield_by_label(card, _PASSWORD_FIELD).value = "pw"
        _press_register(card)
        dialog = _scope_dialog(stub_page)
        label = setup_mod.SCOPE_FOLDER_REPLACE_LABEL.format(
            path="\\\\server\\share\\Exports", folder=setup_mod.SCOPE_FOLDER_OUTPUT_LABEL
        )
        next(c for c in _iter_controls(dialog.content) if getattr(c, "content", None) == label).on_click(None)

        assert cfg.output_dir == "\\\\server\\share\\Exports"
        assert remounts["n"] == 1

    def test_the_replacement_is_not_offered_without_a_host_that_can_re_render(self, tmp_path, stub_page, monkeypatch):
        """Absent ⇒ no affordance, never a dead one — this shell's established rule. The warning
        still names the network path in its own copy, so the admin is not left guessing."""
        _tree, dialog = self._confirm_body(
            tmp_path, stub_page, monkeypatch, output_dir="Z:\\Exports", unc="\\\\server\\share\\Exports"
        )
        assert _has_text_containing(dialog.content, setup_mod.SCOPE_FOLDER_WARNING_HEADLINE)
        assert not [c for c in _iter_controls(dialog.content) if isinstance(c, ft.TextButton)]


# --------------------------------------------------------------------------- #
# S-2b.3 — complete_handover runs on EVERY outcome                             #
# --------------------------------------------------------------------------- #
_ALL_OUTCOMES = list(ProvisionOutcome)


def _drive_outcome(tmp_path, stub_page, monkeypatch, attempt, **handover):
    cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
    seen = _capture_provision(monkeypatch, results=[attempt])
    handover_seen = _stub_handover(monkeypatch, **handover)
    tree, _ = _schedule_section(cfg, stub_page)
    _register_service_account(tree, stub_page)
    return cfg, tree, seen, handover_seen


@pytest.mark.parametrize("outcome", _ALL_OUTCOMES, ids=[o.value for o in _ALL_OUTCOMES])
def test_complete_handover_is_called_on_every_outcome(tmp_path, stub_page, monkeypatch, outcome):
    """Including ``UNCONFIRMED`` (the bounded-wait timeout) and ``DECLINED``.

    The gate is the PARENT's own re-read of the HKLM switch, and the child's result file is
    absent on exactly the outcomes where it may nonetheless have committed — so skipping the
    call on any of them would leave the session writing through a stale per-user pin, with
    every later edit vanishing at the next launch.
    """
    attempt = ProvisionAttempt(
        outcome=outcome,
        step=ProvisionStep.CREATE if outcome is ProvisionOutcome.REFUSED else None,
        message=task_com.MSG_ACCESS_DENIED if outcome is ProvisionOutcome.FAILED else "",
    )
    _cfg, _tree, seen, handover = _drive_outcome(tmp_path, stub_page, monkeypatch, attempt, handed_over=False)

    assert seen["calls"] == 1
    assert handover["calls"] == 1, f"complete_handover was skipped for {outcome.value}"


_NON_SUCCESS = [o for o in ProvisionOutcome if o is not ProvisionOutcome.PROVISIONED]


@pytest.mark.parametrize("outcome", _NON_SUCCESS, ids=[o.value for o in _NON_SUCCESS])
def test_persist_is_a_no_op_on_every_non_success_path(tmp_path, stub_page, monkeypatch, outcome):
    """``complete_handover`` calls ``persist`` on outcomes the child never completed, because its
    gate is the switch read. Writing the record there would make Home report a healthy nightly
    over a task that does not exist. ``persist_called`` proves the guard is the VIEW's, not a
    callback that simply never fired."""
    attempt = ProvisionAttempt(
        outcome=outcome,
        step=ProvisionStep.CREATE if outcome is ProvisionOutcome.REFUSED else None,
        message=task_com.MSG_ACCESS_DENIED if outcome is ProvisionOutcome.FAILED else "",
    )
    cfg, _tree, _seen, handover = _drive_outcome(tmp_path, stub_page, monkeypatch, attempt, handed_over=True)

    assert handover["persist_called"] is True, "the engine's callback must still run"
    assert cfg.schedule_registered is False
    assert cfg.schedule_run_as_user == ""
    assert cfg.schedule_task_args is None


def test_the_positive_twin_persist_writes_the_record_on_success(tmp_path, stub_page, monkeypatch):
    cfg, _tree, _seen, handover = _drive_outcome(
        tmp_path,
        stub_page,
        monkeypatch,
        ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED),
        handed_over=True,
    )

    assert handover["persist_called"] is True
    assert cfg.schedule_registered is True
    assert cfg.schedule_run_as_user == _SERVICE
    assert cfg.schedule_task_args is not None


# --------------------------------------------------------------------------- #
# S-2b.3 — every HandoverOutcome shape has a surface                           #
# --------------------------------------------------------------------------- #
class TestEveryOutcomeHasASurface:
    def test_handed_over_and_provisioned_paints_the_done_banner(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED),
            handed_over=True,
        )
        assert _has_text(tree, setup_mod.HANDOVER_DONE_HEADLINE)

    def test_switch_committed_and_register_failed_LEADS_with_the_scope_change(self, tmp_path, stub_page, monkeypatch):
        """A failed register may never paint its stock red card over a successful, irreversible
        handover — the admin would retry a move that cannot be repeated."""
        _cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.FAILED, message=task_com.MSG_ACCESS_DENIED),
            handed_over=True,
        )
        assert _has_text(tree, setup_mod.HANDOVER_UNCONFIRMED_HEADLINE)
        detail = next(
            v
            for c in _iter_controls(tree)
            if isinstance(v := getattr(c, "value", None), str) and v.startswith(setup_mod.HANDOVER_UNCONFIRMED_LEAD)
        )
        assert detail.startswith(setup_mod.HANDOVER_UNCONFIRMED_LEAD), "the scope change must come first"
        assert len(detail) > len(setup_mod.HANDOVER_UNCONFIRMED_LEAD), "the failure must still be carried"
        # …and the stock red card is NOT what was painted.
        assert not _has_text(tree, setup_mod.SCOPE_ATTEMPT_FAILED_HEADLINE)

    def test_a_timeout_after_a_commit_also_leads_with_the_scope_change(self, tmp_path, stub_page, monkeypatch):
        """The child is KILLED on the bounded wait, so it reports nothing — and the parent's
        switch read is the only honest authority on which side of the commit we are."""
        _cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.UNCONFIRMED),
            handed_over=True,
        )
        assert _has_text(tree, setup_mod.HANDOVER_UNCONFIRMED_HEADLINE)

    def test_a_refusal_paints_the_terminal_card(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, refused=MachineScopeRefusedReason.OPEN_ACE)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)

        assert _has_text(tree, setup_mod.SCOPE_REFUSED_HEADLINE)
        from src.ui_flet.launcher import _MACHINE_SCOPE_CAUSES

        assert _has_text_containing(tree, _MACHINE_SCOPE_CAUSES[MachineScopeRefusedReason.OPEN_ACE])
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _button_by_content(tree, "Remove nightly sync").disabled is True

    def test_a_refusal_writes_no_record(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        _capture_provision(monkeypatch)
        handover = _stub_handover(monkeypatch, refused=MachineScopeRefusedReason.MISSING)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)

        assert handover["persist_called"] is False, "the real function returns before persist here"
        assert cfg.schedule_registered is False

    def test_a_refusal_freezes_the_rest_of_the_setup_surface(self, tmp_path, stub_page, monkeypatch):
        """The pin is left UNSET, so every later ``user_data_dir()`` in this session raises — a
        live window whose next click can only crash. The card explaining it stays live so its
        log-folder affordance still works; everything else goes dead."""
        _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, refused=MachineScopeRefusedReason.FOREIGN_OWNER)
        tree = setup_mod.build_setup(stub_page)

        folders_save = _button_by_content(tree, "Save folders & district")
        _register_service_account(tree, stub_page)

        assert folders_save.disabled is True, "an action the admin can still press can only crash"
        assert _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)").disabled is True
        # The one thing still worth offering.
        log_buttons = [
            c for c in _iter_controls(tree) if getattr(c, "content", None) == "Open log folder" and not c.disabled
        ]
        assert log_buttons, "the terminal card's log-folder affordance must stay live"

    def test_not_handed_over_paints_the_attempts_own_card(self, tmp_path, stub_page, monkeypatch):
        """Nothing irreversible happened, Setup is still mounted, and its ordinary result slot is
        the right place — a one-shot banner would ambush the admin on Home for a change that
        never took place."""
        _cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.DECLINED),
            handed_over=False,
        )
        assert _has_text(tree, setup_mod.SCOPE_ATTEMPT_FAILED_HEADLINE)
        assert handover_result.take() is None, "nothing may be parked when nothing was handed over"

    def test_provisioned_but_the_switch_reads_off_says_exactly_that(self, tmp_path, stub_page, monkeypatch):
        """The child reported the whole sequence done and the parent's switch read disagrees. The
        nightly EXISTS (the record was written); the scope change is what cannot be confirmed."""
        cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED),
            handed_over=False,
        )
        assert _has_text(tree, setup_mod.SCOPE_SWITCH_UNCONFIRMED_HEADLINE)
        assert cfg.schedule_registered is True

    def test_a_raise_out_of_the_handover_still_reports_something(self, tmp_path, stub_page, monkeypatch):
        """A raise out of ``persist`` propagates BY DESIGN (a facet save that did not happen must
        not look like one that did). The surface must not be left with a spinner on it."""
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, persist_raises=OSError("disk"))
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)

        assert _has_text(tree, setup_mod.SCOPE_UNCONFIRMED_HEADLINE)
        assert _button_by_content(tree, "Remove nightly sync").disabled is False


# --------------------------------------------------------------------------- #
# S-2b.3 — the refusal copy routes through classify_provision_step             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("step", list(ProvisionStep), ids=[s.value for s in ProvisionStep])
def test_a_refused_step_renders_its_own_classified_copy(tmp_path, stub_page, monkeypatch, step):
    """Never through ``classify_schedule_error`` (which keys on canonical messages by exact
    equality, so an interpolated refusal could not match a branch) and never through the
    interpolated message itself."""
    _cfg, tree, _seen, _handover = _drive_outcome(
        tmp_path,
        stub_page,
        monkeypatch,
        ProvisionAttempt(outcome=ProvisionOutcome.REFUSED, step=step),
        handed_over=False,
    )
    expected = classify_provision_step(step)
    assert _has_text_containing(tree, expected)
    assert expected != _unclassified_copy(step.value), "a step id must never land on the generic branch"


def test_a_refusal_carries_its_icacls_exit_code(tmp_path, stub_page, monkeypatch):
    """The one extra diagnostic support can quote. It is appended by the VIEW because the copy
    table deliberately carries no code."""
    _cfg, tree, _seen, _handover = _drive_outcome(
        tmp_path,
        stub_page,
        monkeypatch,
        ProvisionAttempt(outcome=ProvisionOutcome.REFUSED, step=ProvisionStep.GRANT, icacls_exit=5),
        handed_over=False,
    )
    assert _has_text_containing(tree, "5")
    assert _has_text_containing(tree, classify_provision_step(ProvisionStep.GRANT))


# --------------------------------------------------------------------------- #
# S-2b.3 — the one-shot result survives re-entry                               #
# --------------------------------------------------------------------------- #
class TestTheOneShotSurvivesReentry:
    def _with_reentry(self, tmp_path, stub_page, monkeypatch, attempt):
        cfg = _settings(tmp_path, monkeypatch, **_PLAIN_FOLDERS)
        _capture_provision(monkeypatch, results=[attempt])
        _stub_handover(monkeypatch, handed_over=True)
        parked: list = []
        card, _handle = setup_mod._build_schedule_section(
            stub_page, cfg, on_reenter=lambda: parked.append(handover_result.take())
        )
        _register_service_account(card, stub_page)
        return card, parked

    def test_the_result_is_remembered_BEFORE_the_rebuild(self, tmp_path, stub_page, monkeypatch):
        """The rebuild destroys the surface that produced the banner, so a ``remember`` after it
        would park a result for a surface that has already been built."""
        _card, parked = self._with_reentry(
            tmp_path, stub_page, monkeypatch, ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED)
        )
        assert len(parked) == 1
        assert parked[0] is not None, "re-entry found an empty slot — the banner was lost"
        assert parked[0].banner is HandoverBanner.HANDED_OVER

    def test_a_failed_nightly_after_a_commit_is_parked_as_the_scope_first_banner(
        self, tmp_path, stub_page, monkeypatch
    ):
        _card, parked = self._with_reentry(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.FAILED, message=task_com.MSG_ACCESS_DENIED),
        )
        assert parked[0].banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED
        assert parked[0].detail, "the nightly sentence must be classified before it is parked"

    def test_nothing_is_painted_in_place_once_re_entry_took_it(self, tmp_path, stub_page, monkeypatch):
        """Two reports of one change is a bug of its own; the surface defers to the rebuild."""
        card, _parked = self._with_reentry(
            tmp_path, stub_page, monkeypatch, ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED)
        )
        assert not _has_text(card, setup_mod.HANDOVER_DONE_HEADLINE)

    def test_with_no_re_entry_wired_the_banner_is_painted_in_place(self, tmp_path, stub_page, monkeypatch):
        """Absent ⇒ no affordance, never a dead one: nothing will rebuild, so the report stays
        here rather than being parked for a surface that never comes."""
        _cfg, tree, _seen, _handover = _drive_outcome(
            tmp_path,
            stub_page,
            monkeypatch,
            ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED),
            handed_over=True,
        )
        assert _has_text(tree, setup_mod.HANDOVER_DONE_HEADLINE)
        assert handover_result.take() is None, "it was painted here, so nothing may be left parked"


# --------------------------------------------------------------------------- #
# Home renders the one-shot, once per mount, above the verdict band            #
# --------------------------------------------------------------------------- #
class TestHomeRendersTheOneShot:
    @staticmethod
    def _home(stub_page, monkeypatch, *, cfg=None, probe=None):
        cfg = cfg or AppConfig(
            input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True, identity_email=""
        )
        monkeypatch.setattr(home_mod, "read_run_records", lambda: [])
        monkeypatch.setattr(home_mod, "_store_created_at", lambda: "2026-07-04T03:00:00")
        monkeypatch.setattr(home_mod, "get_scheduler", lambda: MagicMock(supports_read_schedule=bool(probe)))
        if probe is not None:
            monkeypatch.setattr(home_mod, "_probe_schedule_async", lambda _p, _c, _t, render: render(probe))
        return home_mod.build_home(stub_page, app_config=cfg, on_navigate=lambda _d: None)

    def test_the_banner_is_the_first_content_element_above_the_verdict(self, stub_page, monkeypatch):
        """A deliberate one-paint exception to verdict-first: the admin has just pressed a button
        that permanently moved this computer's settings, and that outranks "did last night's
        roster sync?" exactly once."""
        handover_result.remember(HandoverResult.handed_over())
        tree = self._home(stub_page, monkeypatch)

        assert _has_text(tree, "Home"), "the page header still leads"
        assert _has_text_containing(tree.controls[1], setup_mod.HANDOVER_DONE_HEADLINE)
        assert getattr(tree.controls[2], "bgcolor", None) in {
            tokens.color_status_healthy_tint,
            tokens.color_status_warning_tint,
            tokens.color_status_failed_tint,
        }, "the verdict band must sit directly beneath it"

    def test_it_is_taken_ONCE_per_mount_and_survives_the_schedule_probe(self, stub_page, monkeypatch):
        """``_render`` runs again when the probe lands, and ``take()`` CLEARS — so a drain inside
        it would show the banner for one frame and then lose it."""
        from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

        handover_result.remember(HandoverResult.handed_over())
        probe = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
        tree = self._home(stub_page, monkeypatch, probe=probe)

        assert _has_text_containing(tree, setup_mod.HANDOVER_DONE_HEADLINE)

    def test_a_second_mount_does_not_re_announce_it(self, stub_page, monkeypatch):
        """One-shot by construction: a result that survived would reappear days later announcing
        a change that happened once."""
        handover_result.remember(HandoverResult.handed_over())
        self._home(stub_page, monkeypatch)
        again = self._home(stub_page, monkeypatch)

        assert not _has_text_containing(again, setup_mod.HANDOVER_DONE_HEADLINE)

    def test_the_wizard_hosting_branch_takes_it_too(self, stub_page, monkeypatch):
        """A first-run admin dispatches the handover from the HOSTED wizard's Schedule step, and
        re-entry lands them back on this branch while ``setup_completed`` is still false."""
        handover_result.remember(HandoverResult.handed_over())
        fresh = AppConfig(identity_email="")
        tree = self._home(stub_page, monkeypatch, cfg=fresh)

        assert _has_text_containing(tree, setup_mod.HANDOVER_DONE_HEADLINE)

    def test_a_refusal_that_reached_the_slot_is_still_shown(self, stub_page, monkeypatch):
        """Normally painted on Setup (``complete_handover`` returns before re-entry there), but
        the renderer is TOTAL over the enum: a leaked result must be shown, never dropped."""
        handover_result.remember(HandoverResult.after_refusal(MachineScopeRefusedReason.REPARSE))
        tree = self._home(stub_page, monkeypatch)

        assert _has_text_containing(tree, setup_mod.SCOPE_REFUSED_HEADLINE)

    def test_the_floor_still_carries_the_report(self, stub_page, monkeypatch):
        """Home's floor says "Your nightly sync keeps running in the background", which is not
        about this computer's settings — and the wizard floor claims nothing was changed, which
        a handover makes false. Neither may swallow the report."""
        handover_result.remember(HandoverResult.handed_over())
        monkeypatch.setattr(home_mod, "_dashboard", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True, identity_email="")
        monkeypatch.setattr(home_mod, "read_run_records", lambda: [])
        tree = home_mod.build_home(stub_page, app_config=cfg, on_navigate=lambda _d: None)

        assert _has_text_containing(tree, setup_mod.HANDOVER_DONE_HEADLINE)
        assert _has_text_containing(tree, "We couldn't show your sync status")


# --------------------------------------------------------------------------- #
# The shell's rebuild seam                                                     #
# --------------------------------------------------------------------------- #
def test_build_app_body_keeps_its_backward_compatible_signature():
    """``on_reenter`` is optional and defaults to ``None`` — every existing caller (and every
    boot test) passes neither it nor ``on_restart_identity``."""
    import inspect

    from src.ui_flet.shell import build_app_body

    params = inspect.signature(build_app_body).parameters
    assert params["on_reenter"].default is None
    assert params["on_reenter"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_rebuild_closure_ignores_the_entered_latch(monkeypatch):
    """``_enter_app`` is idempotent behind a ``nonlocal entered`` latch, so routing the handover's
    re-entry through it would be a silent no-op. The rebuild must be its own closure."""
    from src.ui_flet import shell

    builds: list = []
    captured: dict = {}

    def _fake_body(page, cfg, *, on_restart_identity=None, on_reenter=None):
        builds.append(cfg)
        captured["on_reenter"] = on_reenter
        return ft.Text("body")

    monkeypatch.setattr(shell, "build_app_body", _fake_body)
    monkeypatch.setattr(shell, "needs_identity", lambda _cfg: False)
    monkeypatch.setattr(shell, "bind_window_lifecycle", lambda _page: None)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: AppConfig()))

    page = MagicMock()
    shell.main(page)
    assert len(builds) == 1
    rebuild = captured["on_reenter"]
    assert rebuild is not None, "the shell must hand the body a way to rebuild itself"

    rebuild()
    assert len(builds) == 2, "the rebuild was swallowed by the idempotency latch"


def test_the_gate_block_and_the_note_are_one_decision(tmp_path, stub_page, monkeypatch):
    """The button's ``disabled`` state and the inline note read the SAME ``register_block`` call,
    so a gate with no visible cause is structurally impossible."""
    cfg = _delivery_settings(tmp_path, monkeypatch, schedule_registered=True)
    cfg.schedule_task_args = _registered_args(cfg)
    _stub_secret_store(monkeypatch, has_secret=False)
    tree, _ = _schedule_section(cfg, stub_page)
    field = _account_field(tree)
    field.value = _SERVICE
    _textfield_by_label(tree, _PASSWORD_FIELD).value = "pw"
    field.on_change(None)

    facts = ScheduleAccountFacts(
        typed=_SERVICE,
        current=_SIGNED_IN,
        password_supplied=True,
        recorded="",
        schedule_registered=True,
    )
    # A live task on the signed-in account + a typed service account = the SWITCH refusal, which
    # outranks the delivery secret. The note on screen must be that one, not the cheaper rung.
    assert (
        register_block(True, "03:00", account=facts, delivery_secret_unreadable=True)
        is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE
    )
    assert _has_text_containing(tree, "already scheduled to run as")
    assert not _has_text(tree, setup_mod._ACCOUNT_DELIVERY_SECRET_NOTE)
