"""Plan 0046 Slice B — the service-account principal, at the VIEW seam.

``src/ui_flet/screens/setup.py`` is coverage-omitted glue, so what is pinned here is the
WIRING, not the rendering: that the typed account reaches ``register_task`` unchanged, that the
record's three facets move together, that the gate's reason is always visible, that the banner
and the classifier report the principal that was actually registered, and that the pre-B world
(a district that never touches the field) is byte-identical.

The helpers are imported from ``tests.test_ui_flet_render_smoke`` — the house pattern for
driving a real Flet tree headlessly on the pinned 0.85.3 — so this file adds no second way to
find a control.
"""

from __future__ import annotations

import contextlib
import sys
from unittest.mock import MagicMock

import pytest

import src.ui_flet.screens.setup as setup_mod
from src.config.app_config import AppConfig
from src.scheduler import task_com, windows
from src.scheduler.provision_session import HandoverOutcome, ProvisionAttempt, ProvisionOutcome
from src.scheduler.task_com import PrincipalKind
from src.sftp.uploader import SFTPUploader
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.setup_flow import (
    GMSA_IT_DOC_TITLE,
    GMSA_PREREQUISITES,
    SCHEDULE_ACCOUNT_FIELD_LABEL,
    SCHEDULE_GMSA_TOGGLE_LABEL,
    ReconcileOutcome,
    TaskArgs,
    task_args_to_persisted,
)
from src.ui_flet.setup_gates import principal_key
from tests.test_ui_flet_render_smoke import (
    _benign_probe,
    _button_by_content,
    _checkbox_by_label,
    _has_text,
    _has_text_containing,
    _pick_event,
    _settings_dirs,
    _textfield_by_label,
)

_SIGNED_IN = "PC\\ted"
_SERVICE = "CORP\\svc_districtsync"

#: Both service-account delivery notes, named explicitly (plan 0049 S-2a.3). The copy rules that
#: apply to the pair — the guide/Help pointers, and the address ban — are parametrized over this
#: rather than asserted on the original alone, so a sibling added later cannot slip past them.
_DELIVERY_NOTE_ATTRS = (
    "_SERVICE_ACCOUNT_DELIVERY_NOTE",
    "_SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE",
)


@pytest.fixture
def stub_page() -> MagicMock:
    """A permissive stub Page — any attr/method access returns a child mock no-op."""
    return MagicMock()


# --------------------------------------------------------------------------- #
# Harness                                                                      #
# --------------------------------------------------------------------------- #
def _settings(tmp_path, monkeypatch, **over) -> AppConfig:
    """A completed-install Settings config on a Windows-shaped scheduler.

    The ``sys.platform`` pin is LOAD-BEARING, not decoration: the run-as account field renders
    and wires ONLY on win32 (``tests/test_ui_flet_render_smoke.py`` says so at its own account
    note), so without it every control lookup here returns ``None`` on the Linux and macOS CI
    legs while passing on a Windows developer box. That is the exact three-OS divergence the
    land gate exists to catch — and it caught this one (PR #124, `test fail`). The house pattern
    is to pin the platform rather than skip the file, so the WIRING stays covered on every leg.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    in_dir, out_dir = _settings_dirs(tmp_path)
    fields: dict = {
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "sis_type": "myedbc",
        "setup_completed": True,
        "schedule_time": "03:00",
    }
    fields.update(over)
    cfg = AppConfig(**fields)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    _benign_probe(monkeypatch)
    monkeypatch.setattr("src.scheduler.windows.current_run_as_user", lambda: _SIGNED_IN)
    return cfg


def _registered_args(cfg: AppConfig) -> dict:
    return task_args_to_persisted(
        TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=cfg.schedule_time,
        )
    )


def _flatten_principal(seen: dict) -> None:
    """Project the DECLARED principal back into the two facts the rows below assert on.

    Since plan 0049 S-3 the engine takes ONE ``task_com.Principal`` instead of a
    ``run_as_user`` / ``run_as_password`` pair, because the pair's *combination* used to
    imply the logon type. What each row here is actually about — "the typed account reaches
    the engine unchanged", "a blank field means the signed-in account" — did not change, so
    the object is unpacked here rather than at thirty assertion sites. ``principal`` is kept
    alongside it, and ``TestTheUiDeclaresOnlyTwoKinds`` asserts the kind directly.
    """
    principal = seen.get("principal")
    if principal is None:
        return
    seen["run_as_user"] = principal.user or None
    seen["run_as_password"] = principal.password


def _capture_register(monkeypatch, *, result=(True, "ok")) -> dict:
    """Record what actually reaches ``register_task`` — the REGISTERED params, not the return."""
    seen: dict = {"calls": 0}

    def _fake(**kwargs):
        seen["calls"] += 1
        seen.update(kwargs)
        _flatten_principal(seen)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr("src.scheduler.windows.register_task", _fake)
    return seen


def _capture_classifier(monkeypatch) -> dict:
    """Capture the value the view passes for ``account_is_current`` (never re-derived here)."""
    seen: dict = {}
    real = setup_mod.classify_schedule_error

    def _spy(msg, elevated, *, account_is_current, kind):
        seen["msg"] = msg
        seen["account_is_current"] = account_is_current
        seen["kind"] = kind
        return real(msg, elevated, account_is_current=account_is_current, kind=kind)

    monkeypatch.setattr(setup_mod, "classify_schedule_error", _spy)
    return seen


def _schedule_section(cfg, stub_page):
    """The WIZARD mount's shape — ``allow_gmsa`` left at its default (plan 0049 S-4)."""
    card, handle = setup_mod._build_schedule_section(stub_page, cfg)
    return card, handle


def _settings_schedule_section(cfg, stub_page):
    """The SETTINGS mount's shape — the only one that offers the gMSA disclosure (S-4).

    ``allow_gmsa=True`` is exactly what ``_mount_settings`` passes; it is spelled here rather
    than driving the whole Settings scroll so the fork itself is what the rows below vary.
    """
    card, handle = setup_mod._build_schedule_section(stub_page, cfg, allow_gmsa=True)
    return card, handle


def _gmsa_box(tree):
    return _checkbox_by_label(tree, SCHEDULE_GMSA_TOGGLE_LABEL)


def _password_slot(tree):
    """The Column holding the Windows-password field + its caption (S-4 hides it as a unit)."""
    from tests.test_ui_flet_render_smoke import _iter_controls

    for control in _iter_controls(tree):
        children = getattr(control, "controls", None)
        if not isinstance(children, list):
            continue
        if any(getattr(c, "label", None) == "Windows account password" for c in children):
            return control
    return None


def _toggle_gmsa(tree, on: bool) -> None:
    """Move the tick box the way flet does — ``check_row`` reads ``e.control.value``."""
    box = _gmsa_box(tree)
    assert box is not None, "the gMSA disclosure is not on this mount"
    box.value = on
    box.on_change(_pick_event(on))


def _account_field(tree):
    return _textfield_by_label(tree, SCHEDULE_ACCOUNT_FIELD_LABEL)


def _press_register(tree) -> None:
    _button_by_content(tree, "Schedule nightly sync").on_click(None)


def _drain(stub_page) -> None:
    """Run whatever the register flow handed to ``page.run_thread`` / ``page.run_task``."""
    for call in list(stub_page.run_thread.call_args_list):
        call.args[0]()
    stub_page.run_thread.reset_mock()
    for call in list(stub_page.run_task.call_args_list):
        coro = call.args[0](*call.args[1:])
        with contextlib.suppress(StopIteration):
            coro.send(None)
    stub_page.run_task.reset_mock()


# --------------------------------------------------------------------------- #
# Provisioning harness (plan 0049 S-2b) — shared with the handover test file    #
# --------------------------------------------------------------------------- #
# Since S-2b a FOREIGN principal no longer reaches ``register_task`` from the Register button:
# it reaches ``request_provision``, behind the machine-scope confirm. Every seam below is
# monkeypatched, never real — ``request_provision`` raises a UAC prompt and ``complete_handover``
# re-pins ``paths``, renames the live profile and re-points the log sink.
def _capture_provision(monkeypatch, *, results=None) -> dict:
    """Record what reaches ``request_provision``, and queue its outcomes.

    The queue exists for the two-attempt sequences in
    ``tests/test_ui_flet_schedule_refusal_feedback.py``; exhausting it falls through to
    ``PROVISIONED``, so a test can never pass by silently re-reading a failure.
    """
    seen: dict = {"calls": 0}
    queue = list(results or [])

    def _fake(task_name, exe_path, sis_type, input_dir, output_dir, run_time, sftp=False, **kwargs):
        seen["calls"] += 1
        seen.update(kwargs)
        _flatten_principal(seen)
        seen["task_name"] = task_name
        seen["sis_type"] = sis_type
        seen["run_time"] = run_time
        seen["sftp"] = sftp
        return queue.pop(0) if queue else ProvisionAttempt(outcome=ProvisionOutcome.PROVISIONED)

    monkeypatch.setattr(setup_mod, "request_provision", _fake)
    return seen


def _stub_handover(monkeypatch, *, handed_over=True, refused=None, persist_raises=None) -> dict:
    """Drive ``complete_handover``'s CONTRACT without its side effects.

    It models the real ordering exactly, because the assertions depend on it: a refusal returns
    BEFORE ``persist`` (there is nowhere to persist to), and every other outcome calls
    ``persist`` and then — only when the parent's switch read says this session handed over —
    ``reenter``. ``persist_called`` is recorded so "the record was not written" can be proven to
    be the VIEW's guard rather than a callback that never fired.
    """
    seen: dict = {"calls": 0, "persist_called": False, "reenter_called": False}

    def _fake(*, persist, reenter):
        seen["calls"] += 1
        if refused is not None:
            return HandoverOutcome(handed_over=False, refused=refused)
        if persist_raises is not None:
            seen["persist_called"] = True
            raise persist_raises
        persist()
        seen["persist_called"] = True
        if handed_over:
            reenter()
            seen["reenter_called"] = True
        return HandoverOutcome(handed_over=handed_over)

    monkeypatch.setattr(setup_mod, "complete_handover", _fake)
    return seen


def _scope_dialog(stub_page):
    """The machine-scope confirm this press showed, or ``None``."""
    for call in reversed(list(stub_page.show_dialog.call_args_list)):
        dialog = call.args[0]
        if getattr(getattr(dialog, "title", None), "value", None) == setup_mod.SCOPE_CONFIRM_TITLE:
            return dialog
    return None


def _confirm_scope(stub_page) -> None:
    """Press Continue on the confirm — the ONLY door to a provisioning dispatch."""
    dialog = _scope_dialog(stub_page)
    assert dialog is not None, "the machine-scope confirm was never shown"
    action = next(b for b in dialog.actions if getattr(b, "content", None) == setup_mod.SCOPE_CONFIRM_CONTINUE_LABEL)
    action.on_click(None)


def _register_service_account(tree, stub_page, *, account=_SERVICE, password="pw") -> None:
    """Type a service account, press Schedule, confirm, and drain the worker."""
    _account_field(tree).value = account
    _textfield_by_label(tree, "Windows account password").value = password
    _press_register(tree)
    _confirm_scope(stub_page)
    _drain(stub_page)


# --------------------------------------------------------------------------- #
# G5 — the untouched prefill is byte-identical to the pre-B world               #
# --------------------------------------------------------------------------- #
class TestG5PrefillUnchanged:
    @pytest.mark.parametrize("password", ["", "x"])
    def test_the_registered_principal_is_unchanged_for_both_password_states(
        self, tmp_path, stub_page, monkeypatch, password
    ):
        """Asserted on the REGISTERED params, not the return tuple: the pre-B build sent
        ``run_as_user=None`` and the engine used the current account. An untouched prefill must
        reduce to exactly that."""
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = password
        _press_register(tree)
        _drain(stub_page)

        assert seen["calls"] == 1
        # The prefilled value IS the signed-in account, so the engine's own comparison reduces
        # it to the interactive path exactly as `run_as_user=None` did.
        assert principal_key(seen["run_as_user"], _SIGNED_IN) == ""
        assert seen["run_as_password"] == (password or None)

    def test_a_spaced_local_account_still_registers(self, tmp_path, stub_page, monkeypatch):
        """``current_run_as_user()`` legitimately returns ``PC\\John Smith``, which the run-as
        regex rejects. Those districts register logged-on-only fine today; the gate must not
        close on mount for them."""
        spaced = "PC\\John Smith"
        cfg = _settings(tmp_path, monkeypatch)
        monkeypatch.setattr("src.scheduler.windows.current_run_as_user", lambda: spaced)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)

        assert _account_field(tree).value == spaced
        assert _button_by_content(tree, "Schedule nightly sync").disabled is False
        _press_register(tree)
        _drain(stub_page)
        assert seen["calls"] == 1
        assert seen["run_as_password"] is None


# --------------------------------------------------------------------------- #
# The typed value is sent unchanged                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "typed",
    ["svc_x", "SVC_X", "  svc_x  ", "CORP\\svc_x", "corp\\SVC_X"],
)
def test_the_typed_account_is_sent_verbatim(tmp_path, stub_page, monkeypatch, typed):
    """Stripped only. A sanitised or re-cased value would bypass the engine's case-insensitive
    comparison — the very thing that makes the prefill safe.

    Asserted on ``request_provision`` since plan 0049 S-2b: a FOREIGN principal reaches the
    provisioning round trip instead of ``register_task``, and that is the call which now carries
    the account name to the elevated child. The invariant and the parametrization are unchanged;
    what moved is which engine entry point has to honour them.
    """
    cfg = _settings(tmp_path, monkeypatch)
    ordinary = _capture_register(monkeypatch)
    seen = _capture_provision(monkeypatch)
    _stub_handover(monkeypatch, handed_over=False)
    tree, _ = _schedule_section(cfg, stub_page)
    _register_service_account(tree, stub_page, account=typed)

    assert seen["calls"] == 1
    assert seen["run_as_user"] == typed.strip()
    assert ordinary["calls"] == 0, "a foreign principal must not take the un-provisioned path"


def test_a_blank_account_field_sends_none(tmp_path, stub_page, monkeypatch):
    cfg = _settings(tmp_path, monkeypatch)
    seen = _capture_register(monkeypatch)
    tree, _ = _schedule_section(cfg, stub_page)
    _account_field(tree).value = "   "
    _press_register(tree)
    _drain(stub_page)
    assert seen["run_as_user"] is None


# --------------------------------------------------------------------------- #
# The record — three facets, one save                                          #
# --------------------------------------------------------------------------- #
class TestTheRecord:
    def test_a_confirmed_register_writes_all_three_facets_in_one_save(self, tmp_path, stub_page, monkeypatch):
        """Unchanged invariant, new route (0049 S-2b): the facets are written by the SAME
        ``_persist_registered_record``, now handed to ``complete_handover`` as ``persist``."""
        cfg = _settings(tmp_path, monkeypatch)
        saves = {"n": 0}
        monkeypatch.setattr(AppConfig, "save", lambda self: saves.__setitem__("n", saves["n"] + 1))
        _capture_provision(monkeypatch)
        handover = _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)

        assert handover["persist_called"] is True
        assert cfg.schedule_run_as_user == _SERVICE
        assert cfg.schedule_unattended is True
        assert cfg.schedule_task_args is not None
        assert saves["n"] == 1

    def test_a_signed_in_register_still_writes_the_record_on_the_ordinary_path(self, tmp_path, stub_page, monkeypatch):
        """The pre-S-2b path, unchanged: the signed-in account provisions nothing, so it never
        reaches the confirm and the record is written by ``_on_register_success``."""
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)

        assert _scope_dialog(stub_page) is None
        assert seen["calls"] == 1
        assert cfg.schedule_registered is True
        assert cfg.schedule_unattended is True

    def test_a_confirmed_unregister_clears_all_four(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
            schedule_run_as_kind=PrincipalKind.PASSWORD.value,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        monkeypatch.setattr("src.scheduler.windows.delete_task", lambda name: (True, "Schedule removed."))
        tree, _ = _schedule_section(cfg, stub_page)
        _button_by_content(tree, "Remove nightly sync").on_click(None)
        _drain(stub_page)

        assert cfg.schedule_registered is False
        assert cfg.schedule_unattended is False
        assert cfg.schedule_task_args is None
        assert cfg.schedule_run_as_user == ""
        # 0049 S-4: the FOURTH facet goes with the other three, in the same save.
        assert cfg.schedule_run_as_kind == ""

    def test_remove_then_schedule_keeps_the_typed_account_in_the_box(self, tmp_path, stub_page, monkeypatch):
        """The refusal copy promises "what you've typed here stays in the box" — so nothing on
        the unregister path may clear the field."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user="",
        )
        cfg.schedule_task_args = _registered_args(cfg)
        monkeypatch.setattr("src.scheduler.windows.delete_task", lambda name: (True, "Schedule removed."))
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _button_by_content(tree, "Remove nightly sync").on_click(None)
        _drain(stub_page)

        assert _account_field(tree).value == _SERVICE
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True  # still needs the password


# --------------------------------------------------------------------------- #
# The inline reason is always visible                                          #
# --------------------------------------------------------------------------- #
class TestTheInlineReason:
    def _mounted(self, tmp_path, stub_page, monkeypatch, **over):
        cfg = _settings(tmp_path, monkeypatch, **over)
        if cfg.schedule_registered:
            cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _schedule_section(cfg, stub_page)
        return cfg, tree

    def test_no_reason_and_an_enabled_button_when_the_gate_is_open(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._mounted(tmp_path, stub_page, monkeypatch)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is False
        assert not _has_text_containing(tree, "isn't valid")
        assert not _has_text_containing(tree, "already scheduled")

    def test_a_malformed_foreign_account_shows_its_reason(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._mounted(tmp_path, stub_page, monkeypatch)
        field = _account_field(tree)
        field.value = "CORP\\svc account"
        _textfield_by_label(tree, "Windows account password").value = "pw"
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, "That account name isn't valid")

    def test_a_foreign_account_with_no_password_shows_its_reason(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._mounted(tmp_path, stub_page, monkeypatch)
        field = _account_field(tree)
        field.value = _SERVICE
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, "Enter the Windows password for this account")

    def test_the_field_prefills_from_the_RECORD_not_the_signed_in_account(self, tmp_path, stub_page, monkeypatch):
        """A live task on a service account mounts showing THAT account — otherwise every
        Settings visit would look like a switch the admin never asked for."""
        _cfg, tree = self._mounted(
            tmp_path,
            stub_page,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        assert _account_field(tree).value == _SERVICE
        assert not _has_text_containing(tree, "already scheduled to run as")

    def test_a_switch_on_a_live_task_names_the_recorded_account(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._mounted(
            tmp_path,
            stub_page,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        field = _account_field(tree)
        field.value = "CORP\\svc_other"
        _textfield_by_label(tree, "Windows account password").value = "pw"
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, f"already scheduled to run as {_SERVICE}")
        assert _has_text_containing(tree, "Remove nightly sync")

    def test_going_back_to_the_signed_in_account_is_also_a_switch(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._mounted(
            tmp_path,
            stub_page,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        field = _account_field(tree)
        field.value = ""
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, f"already scheduled to run as {_SERVICE}")

    def test_an_unknown_record_uses_the_unknown_wording_and_names_no_account(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, schedule_registered=True)
        cfg.schedule_task_args = None  # pre-v3.7.0 / hand-edited: no usable record
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = _SERVICE
        _textfield_by_label(tree, "Windows account password").value = "pw"
        field.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, "has no record of which account it runs as")
        assert not _has_text_containing(tree, f"run as {_SERVICE}")

    def test_the_run_time_reason_stays_where_it_always_was(self, tmp_path, stub_page, monkeypatch):
        """INCOMPLETE / RUN_TIME paint nothing in the account slot — today's behaviour."""
        _cfg, tree = self._mounted(tmp_path, stub_page, monkeypatch)
        run_time = _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)")
        run_time.value = ""
        run_time.on_change(None)
        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert not _has_text_containing(tree, "That account name isn't valid")
        assert not _has_text_containing(tree, "Enter the Windows password for this account")


# --------------------------------------------------------------------------- #
# The delivery note (owner decision 2)                                         #
# --------------------------------------------------------------------------- #
class TestTheDeliveryNote:
    """WHETHER a delivery note renders at all — delivery on, and a service account in play.

    Every case below leaves the password field BLANK, which since plan 0049 S-2a.3 is what
    selects the MANUAL form: the gate is closed, so pressing Schedule would provision nothing and
    the "sign in as it once" instruction is still the truth. WHICH form renders is
    ``TestTheDeliveryNoteIsKeyedOnWillProvision``'s subject; leave the password out of here so the
    two classes keep testing two different things.
    """

    _MARK = "--sftp-configure"

    def test_it_renders_for_a_typed_service_account_with_delivery_on(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, sftp_enabled=True)
        tree, _ = _schedule_section(cfg, stub_page)
        assert not _has_text_containing(tree, self._MARK)  # nothing typed yet
        field = _account_field(tree)
        field.value = _SERVICE
        field.on_change(None)
        assert _has_text_containing(tree, self._MARK)
        # …and it disappears again when the account goes back to the signed-in one.
        field.value = _SIGNED_IN
        field.on_change(None)
        assert not _has_text_containing(tree, self._MARK)

    def test_it_renders_for_a_RECORDED_service_account_too(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(
            tmp_path,
            monkeypatch,
            sftp_enabled=True,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _schedule_section(cfg, stub_page)
        assert _has_text_containing(tree, self._MARK)

    def test_it_is_silent_when_delivery_is_off(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch, sftp_enabled=False)
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = _SERVICE
        field.on_change(None)
        assert not _has_text_containing(tree, self._MARK)

    @pytest.mark.parametrize("attr", _DELIVERY_NOTE_ATTRS, ids=_DELIVERY_NOTE_ATTRS)
    def test_it_points_at_the_guide_and_the_help_page_and_carries_no_address(self, attr):
        """Extended to bind BOTH forms (plan 0049 S-2a.3). The address ban is the reason: a
        sibling note that grew one would be invisible to a test pinned at the manual form, and
        ``scripts/check_no_emails.py`` would only catch it if the literal were an address rather
        than a "write to us at" sentence. The guide and Help pointers are shared deliberately —
        both forms leave the admin somewhere they can act."""
        note = getattr(setup_mod, attr)
        assert "setup guide" in note
        assert "Help page" in note
        assert "@" not in note

    def test_only_the_manual_form_carries_the_marker_the_render_tests_key_on(self):
        """``_MARK`` is how every rendering assertion above tells the two forms apart, so it has
        to be a real discriminator: a provision note that also mentioned ``--sftp-configure``
        would make those tests pass on either string."""
        assert self._MARK in setup_mod._SERVICE_ACCOUNT_DELIVERY_NOTE
        assert self._MARK not in setup_mod._SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE
        # The two say opposite things about who does the work; neither may drift into the other.
        assert "DistrictSync can't do this for you" in setup_mod._SERVICE_ACCOUNT_DELIVERY_NOTE
        assert "DistrictSync will save" in setup_mod._SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE


# --------------------------------------------------------------------------- #
# The delivery note is keyed on WILL-PROVISION, never on today's scope (0049)   #
# --------------------------------------------------------------------------- #
class TestTheDeliveryNoteIsKeyedOnWillProvision:
    """Plan 0049 S-2a.3, the sharpest finding of the S-2 review.

    Provisioning fires at the Schedule PRESS, so the install is still per-user at the moment this
    note is painted — the admin is mid-keystroke in the account field. Keyed on today's scope the
    note would say "DistrictSync can't do this for you, run ``--sftp-configure``" seconds before
    the app does exactly that, and the admin would go and do the manual work anyway.

    So the key is "would pressing Schedule provision?" — a foreign TYPED principal with every gate
    open — and the tests below drive that distinction from both ends: the same install, the same
    typed account, with and without the one field that opens the gate.
    """

    _MANUAL = "--sftp-configure"
    _PROVISION = "DistrictSync will save"

    @pytest.mark.parametrize("delivery", [False, True], ids=["delivery-off", "delivery-on"])
    @pytest.mark.parametrize("provision", [False, True], ids=["manual", "will-provision"])
    def test_no_foreign_principal_earns_no_note_at_all(self, delivery, provision):
        """Both forms are ABOUT a second Windows account. With the nightly running as the admin's
        own account there is no per-account credential problem to explain and nothing to promise
        to fix."""
        assert (
            setup_mod.service_account_delivery_note(delivery_enabled=delivery, foreign=False, will_provision=provision)
            == ""
        )

    @pytest.mark.parametrize("provision", [False, True], ids=["manual", "will-provision"])
    def test_delivery_off_earns_no_note_at_all(self, provision):
        """Owner decision 2: named only where it is TRUE and actionable. With no delivery
        configured there is no password to move and no nightly upload to fail."""
        assert (
            setup_mod.service_account_delivery_note(delivery_enabled=False, foreign=True, will_provision=provision)
            == ""
        )

    def test_a_provisioning_press_earns_the_promise(self):
        assert (
            setup_mod.service_account_delivery_note(delivery_enabled=True, foreign=True, will_provision=True)
            == setup_mod._SERVICE_ACCOUNT_DELIVERY_PROVISION_NOTE
        )

    def test_a_non_provisioning_press_keeps_todays_note_byte_for_byte(self):
        """AC1's per-user promise at this seam: an install that will NOT provision reads exactly
        what it read before the slice, because the manual instruction is still the truth there."""
        assert (
            setup_mod.service_account_delivery_note(delivery_enabled=True, foreign=True, will_provision=False)
            == setup_mod._SERVICE_ACCOUNT_DELIVERY_NOTE
        )

    def test_typing_a_service_account_on_a_per_user_install_promises_the_fix(self, tmp_path, stub_page, monkeypatch):
        """THE headline case. A per-user install, delivery on, the admin types the service account
        and its password: every gate is open, so pressing Schedule would provision — and the note
        under the field must say so rather than send them off to a command line."""
        cfg = _settings(tmp_path, monkeypatch, sftp_enabled=True)
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _account_field(tree).on_change(None)

        assert _has_text_containing(tree, self._PROVISION)
        assert not _has_text_containing(tree, self._MANUAL)

    def test_the_same_account_with_the_gate_closed_keeps_the_manual_note(self, tmp_path, stub_page, monkeypatch):
        """The positive twin, and the proof the key is the GATE and not merely "a foreign account
        was typed": same install, same account, no password — the press would be refused, nothing
        would be provisioned, and the manual instruction is the only true one."""
        cfg = _settings(tmp_path, monkeypatch, sftp_enabled=True)
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _SERVICE
        _account_field(tree).on_change(None)

        assert _has_text_containing(tree, self._MANUAL)
        assert not _has_text_containing(tree, self._PROVISION)

    @pytest.mark.parametrize("scope", [False, True], ids=["per-user", "machine-scoped"])
    def test_the_rendered_note_does_not_move_with_the_scope(self, tmp_path, stub_page, monkeypatch, scope):
        """The regression this class exists for. A future edit keying the note on
        ``paths.is_machine_scope()`` would flip BOTH cases below; keyed on the gate, the scope is
        not consulted at all and the same two inputs give the same two answers on either
        install."""
        cfg = _settings(tmp_path, monkeypatch, sftp_enabled=True)
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: scope)
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)

        field.value = _SERVICE
        field.on_change(None)
        assert _has_text_containing(tree, self._MANUAL)  # gate closed → the manual truth

        _textfield_by_label(tree, "Windows account password").value = "pw"
        field.on_change(None)
        assert _has_text_containing(tree, self._PROVISION)  # gate open → the promise
        assert not _has_text_containing(tree, self._MANUAL)

    def test_a_recorded_service_account_with_nothing_typed_is_not_a_provisioning_press(
        self, tmp_path, stub_page, monkeypatch
    ):
        """``will_provision`` reads the TYPED principal, not the recorded one — the note is about
        what THIS press would do. Here the switch gate refuses the press outright (a live task on
        one account, a blank field meaning another), so nothing would be provisioned and the
        manual note stands."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            sftp_enabled=True,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _schedule_section(cfg, stub_page)
        field = _account_field(tree)
        field.value = ""
        field.on_change(None)

        assert _has_text_containing(tree, self._MANUAL)
        assert not _has_text_containing(tree, self._PROVISION)


# --------------------------------------------------------------------------- #
# The honest banner + the classifier wiring (A7)                               #
# --------------------------------------------------------------------------- #
class TestHonestReporting:
    def _register_as(self, tmp_path, stub_page, monkeypatch, account, *, result=(True, "ok")):
        cfg = _settings(tmp_path, monkeypatch)
        _capture_register(monkeypatch, result=result)
        tree, _ = _schedule_section(cfg, stub_page)
        if account is not None:
            _account_field(tree).value = account
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)
        return tree

    def _provision_as(self, tmp_path, stub_page, monkeypatch, account, *, attempt=None, **handover):
        cfg = _settings(tmp_path, monkeypatch)
        _capture_provision(monkeypatch, results=[attempt] if attempt is not None else None)
        _stub_handover(monkeypatch, **handover)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page, account=account)
        return tree

    def test_the_provisioned_banner_never_names_the_signed_in_account(self, tmp_path, stub_page, monkeypatch):
        """G3, carried onto the provisioning path. The banner names NO account — it says "the
        account you entered" — which is what makes naming the wrong one impossible. The assertion
        is on the signed-in name because that is the failure G3 exists to stop: a task on a
        service account reporting somebody else's name back."""
        tree = self._provision_as(tmp_path, stub_page, monkeypatch, _SERVICE, handed_over=True)
        assert _has_text_containing(tree, setup_mod.HANDOVER_DONE_HEADLINE)
        assert not _has_text_containing(tree, _SIGNED_IN)

    def test_the_banner_names_the_signed_in_account_on_an_untouched_prefill(self, tmp_path, stub_page, monkeypatch):
        tree = self._register_as(tmp_path, stub_page, monkeypatch, None)
        assert _has_text_containing(tree, f"Runs as {_SIGNED_IN}")

    def test_a_foreign_account_routes_account_is_current_False(self, tmp_path, stub_page, monkeypatch):
        """0047 G4 on the provisioning path: a FAILED attempt's message goes through the SAME
        classifier, and the personal-credential coaching (a Windows Hello PIN, a microsoft.com
        password) must not be offered for a service account."""
        seen = _capture_classifier(monkeypatch)
        self._provision_as(
            tmp_path,
            stub_page,
            monkeypatch,
            _SERVICE,
            attempt=ProvisionAttempt(outcome=ProvisionOutcome.FAILED, message=task_com.MSG_LOGON_FAILURE),
            handed_over=False,
        )
        assert seen["msg"] == task_com.MSG_LOGON_FAILURE
        assert seen["account_is_current"] is False
        out = setup_mod.classify_schedule_error(
            task_com.MSG_LOGON_FAILURE, False, account_is_current=seen["account_is_current"], kind=seen["kind"]
        )
        assert "PIN" not in out
        assert "microsoft.com" not in out

    def test_the_untouched_prefill_keeps_the_personal_coaching(self, tmp_path, stub_page, monkeypatch):
        seen = _capture_classifier(monkeypatch)
        self._register_as(tmp_path, stub_page, monkeypatch, None, result=(False, task_com.MSG_LOGON_FAILURE))
        assert seen["account_is_current"] is True
        out = setup_mod.classify_schedule_error(
            task_com.MSG_LOGON_FAILURE, False, account_is_current=seen["account_is_current"], kind=seen["kind"]
        )
        assert "PIN" in out
        assert "microsoft.com" in out


# --------------------------------------------------------------------------- #
# The ValueError floor (carried item 3)                                        #
# --------------------------------------------------------------------------- #
class TestTheValueErrorFloor:
    def test_a_raised_validate_run_as_user_reads_as_an_account_problem(self, tmp_path, stub_page, monkeypatch):
        """A gate/engine DRIFT floor. `except Exception` already caught this and reported it as
        transient — inviting a retry that cannot work — and the exception's own message
        interpolates the typed value, so it is never echoed."""
        cfg = _settings(tmp_path, monkeypatch)
        _capture_register(monkeypatch, result=ValueError("Invalid run-as user 'x y'"))
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)

        assert _has_text_containing(tree, "Windows wouldn't accept that account name")
        assert not _has_text_containing(tree, setup_mod._WORKER_ERROR_REGISTER)
        assert not _has_text_containing(tree, "x y")

    def test_the_positive_twin_a_malformed_account_never_reaches_the_engine(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = "CORP\\svc account"
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)
        assert seen["calls"] == 0


# --------------------------------------------------------------------------- #
# The reconcile routing                                                        #
# --------------------------------------------------------------------------- #
class TestReconcileRouting:
    def _drive(self, tmp_path, stub_page, monkeypatch, *, typed=None, password="", **over):
        cfg = _settings(tmp_path, monkeypatch, **over)
        if cfg.schedule_registered and cfg.schedule_task_args is None and over.get("with_record", True):
            cfg.schedule_task_args = _registered_args(cfg)
        _capture_register(monkeypatch)
        tree, handle = _schedule_section(cfg, stub_page)
        if typed is not None:
            _account_field(tree).value = typed
        _textfield_by_label(tree, "Windows account password").value = password
        return cfg, tree, handle

    def test_a_recorded_service_account_with_no_password_interrupts(self, tmp_path, stub_page, monkeypatch):
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            typed=_SERVICE,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        assert handle.trigger_register() is ReconcileOutcome.INTERRUPTED
        assert stub_page.show_dialog.called

    def test_a_newly_typed_different_account_is_blocked_without_a_dialog(self, tmp_path, stub_page, monkeypatch):
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            typed=_SERVICE,
            password="pw",
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user="",
        )
        assert handle.trigger_register() is ReconcileOutcome.BLOCKED_ACCOUNT_SWITCH
        assert not stub_page.show_dialog.called

    def test_a_malformed_account_is_blocked_on_the_account(self, tmp_path, stub_page, monkeypatch):
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            typed="CORP\\svc account",
            password="pw",
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user="CORP\\svc account",
        )
        assert handle.trigger_register() is ReconcileOutcome.BLOCKED_ACCOUNT

    def test_a_recorded_service_account_interrupts_even_on_a_logged_on_only_record(
        self, tmp_path, stub_page, monkeypatch
    ):
        """A foreign principal IMPLIES a stored password (the engine refuses otherwise), so a
        record claiming unattended=False beside one is inconsistent — asking is the honest move,
        and it is the only way the admin hears WHICH account's password Windows wants."""
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            typed=_SERVICE,
            schedule_registered=True,
            schedule_unattended=False,
            schedule_run_as_user=_SERVICE,
        )
        assert handle.trigger_register() is ReconcileOutcome.INTERRUPTED

    def test_a_principal_refusal_never_falls_through_to_the_run_time_copy(self, tmp_path, stub_page, monkeypatch):
        """The totality floor. Every live-task shape of ACCOUNT_NEEDS_PASSWORD is absorbed by
        the interrupt today, so this drives the drift directly: with the interrupt neutralised,
        a principal refusal must still report BLOCKED_ACCOUNT and register NOTHING — falling
        through to ``_register`` would paint BLOCKED's "fix the run time" over an account
        problem, the exact misdirect carried item 5 names."""
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            typed=_SERVICE,
            schedule_registered=True,
            schedule_unattended=False,
            schedule_run_as_user=_SERVICE,
        )
        seen = _capture_register(monkeypatch)
        monkeypatch.setattr(setup_mod, "downgrade_interrupt", lambda **_kw: None)
        assert handle.trigger_register() is ReconcileOutcome.BLOCKED_ACCOUNT
        assert seen["calls"] == 0

    def test_the_pre_B_path_still_dispatches(self, tmp_path, stub_page, monkeypatch):
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            password="pw",
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user="",
        )
        assert handle.trigger_register() is ReconcileOutcome.DISPATCHED

    def test_the_pre_B_blank_password_downgrade_still_interrupts(self, tmp_path, stub_page, monkeypatch):
        _cfg, _tree, handle = self._drive(
            tmp_path,
            stub_page,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user="",
        )
        assert handle.trigger_register() is ReconcileOutcome.INTERRUPTED


# --------------------------------------------------------------------------- #
# A6 — the keyring owner is never the task principal                            #
# --------------------------------------------------------------------------- #
class TestA6KeyringOwner:
    def test_the_old_name_is_gone(self):
        assert not hasattr(setup_mod, "_run_as_account")
        assert hasattr(setup_mod, "_keyring_owner_account")

    def _saved_delivery(self, tmp_path, stub_page, monkeypatch, *, readable="pw"):
        """Drive a delivery Save to completion and hand back the rendered section."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
            sftp_username="district_x",
            sftp_remote_path="/files",
            sftp_port=22,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        monkeypatch.setattr(setup_mod, "_keyring_owner_account", lambda: _SIGNED_IN)
        monkeypatch.setattr(SFTPUploader, "store_password", lambda self, pw: None)
        monkeypatch.setattr(SFTPUploader, "get_stored_password", lambda self: readable)

        tree = setup_mod._build_sftp_section(stub_page, cfg)
        _textfield_by_label(tree, "Password").value = "pw"
        _button_by_content(tree, "Save delivery settings").on_click(None)
        return tree

    def test_the_delivery_line_names_the_signed_in_account_not_the_principal(self, tmp_path, stub_page, monkeypatch):
        """The positive twin of "the rendered string is unchanged": the account it names is
        asserted NOT to be the registered principal. Credential Manager has no cross-user scope,
        so naming the principal here would print a false all-clear on the most likely real
        failure this feature creates.

        The scope stub is EXPLICIT since plan 0049 S-2a.3 (it was ambient before, riding whatever
        ``DISTRICTSYNC_DATA_DIR`` resolved to): this assertion is the slice's byte-identity pin for
        form 2 of ``delivery_password_line``, and a pin that depends on the environment pins
        nothing. The machine-scoped twin below is what proves the branch it now sits on is live."""
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: False)
        tree = self._saved_delivery(tmp_path, stub_page, monkeypatch)

        assert _has_text_containing(tree, f"Your delivery password is saved and readable by {_SIGNED_IN}.")
        assert not _has_text_containing(tree, f"readable by {_SERVICE}")

    def test_the_machine_scoped_line_names_the_principal_and_drops_the_keyring_owner(
        self, tmp_path, stub_page, monkeypatch
    ):
        """The twin, and the inversion that makes A6 a per-user rule rather than a universal one:
        once the secret lives in the machine store the keyring owner is the WRONG name — that
        keyring has been replaced — and the account the nightly runs as is the right one."""
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: True)
        tree = self._saved_delivery(tmp_path, stub_page, monkeypatch)

        assert _has_text_containing(tree, f"saved on this computer, where {_SERVICE} can read it")
        assert not _has_text_containing(tree, f"readable by {_SIGNED_IN}")

    @pytest.mark.parametrize("scope", [False, True], ids=["per-user", "machine-scoped"])
    def test_an_unreadable_credential_reports_through_the_same_one_function(
        self, tmp_path, stub_page, monkeypatch, scope
    ):
        """The failure arm routes through ``delivery_password_line`` too, which is the only reason
        a machine-scoped admin stops being told to "run the app as this account" — advice that
        cannot work once no single account owns the secret. Per-user keeps it word for word."""
        monkeypatch.setattr(setup_mod.paths, "is_machine_scope", lambda: scope)
        tree = self._saved_delivery(tmp_path, stub_page, monkeypatch, readable=None)

        assert _has_text_containing(tree, "Couldn't read")
        assert _has_text_containing(tree, "run the app as this account") is (not scope)


def test_the_engine_refusal_message_is_classified_not_swallowed():
    """The sweep's declared-but-now-classified arm forced this; keep it visible at the seam."""
    out = setup_mod.classify_schedule_error(
        windows._MSG_ACCOUNT_NEEDS_PASSWORD, False, account_is_current=False, kind=PrincipalKind.PASSWORD
    )
    assert SCHEDULE_ACCOUNT_FIELD_LABEL in out


# --------------------------------------------------------------------------- #
# The kind the UI DECLARES (plan 0049 S-3)                                     #
# --------------------------------------------------------------------------- #
class TestTheUiDeclaresOnlyTwoKinds:
    """The engine can represent three principal kinds; this surface can NAME two.

    That is a product fact, not an engine limit: there is no managed-service-account
    affordance in Settings until S-4, so a typed password is the only thing that can
    distinguish an unattended request from a logged-on-only one here. The governing rule of
    S-3 is that gMSA "becomes representable in the engine and is exposed to NOBODY", so the
    third kind being unreachable from every UI path is the thing to pin — it is what makes
    the slice safe to land ahead of the disclosure, the caption and the error branch.
    """

    def test_a_typed_password_declares_the_password_kind(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = "hunter2"
        _press_register(tree)
        _drain(stub_page)

        assert seen["calls"] == 1
        assert seen["principal"].kind is PrincipalKind.PASSWORD
        assert seen["principal"].password == "hunter2"

    def test_a_blank_password_declares_the_interactive_token_kind(self, tmp_path, stub_page, monkeypatch):
        """The NEGATIVE twin, and the G5 shape: the 20 shipped districts leave this field
        empty, and their nightly must still be declared logged-on-only."""
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = ""
        _press_register(tree)
        _drain(stub_page)

        assert seen["calls"] == 1
        assert seen["principal"].kind is PrincipalKind.INTERACTIVE_TOKEN
        assert seen["principal"].password is None

    def test_the_provisioning_route_declares_it_too(self, tmp_path, stub_page, monkeypatch):
        """A FOREIGN principal reaches ``request_provision`` rather than ``register_task``
        (S-2b), and that payload carries the kind to the elevated child — so this route
        needs its own row, not an inference from the one above."""
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _register_service_account(tree, stub_page)

        assert seen["calls"] == 1
        assert seen["principal"].kind is PrincipalKind.PASSWORD

    def test_the_WIZARD_mount_still_cannot_declare_a_managed_service_account(self, tmp_path, stub_page, monkeypatch):
        """S-3 pinned this STRUCTURALLY (the third kind was absent from the module's source);
        plan 0049 S-4 landed the caption, the disclosure and the error branch, so the pin moves
        from "absent from the file" to "absent from THIS MOUNT" — which is the property that
        actually matters and the one the S-4 spec chose: a first-run admin must not be offered a
        credential model nobody has been able to test.

        Proved by the affordance, not by the source: with no tick box there is no input that can
        produce the third kind, and the typed ``$`` account is refused by the shape rung ahead of
        any dispatch (the row below this one)."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        assert _gmsa_box(tree) is None
        # The positive twin: the SAME config on the Settings mount does offer it, so this row
        # cannot pass because the control was renamed or the tree walk broke.
        settings_tree, _ = _settings_schedule_section(cfg, stub_page)
        assert _gmsa_box(settings_tree) is not None

    def test_a_dollar_suffixed_account_never_reaches_a_declaration_at_all(self, tmp_path, stub_page, monkeypatch):
        """The one unrepresentable combination an admin could type — a ``$``-suffixed account
        with a password — is refused by ``setup_gates.register_block``'s ``ACCOUNT_SHAPE``
        rung, IN FRONT of the dispatch. So ``Principal``'s own refusal cannot fire from this
        surface, which is why ``_declared_principal`` says so instead of carrying a handler
        for an outcome it cannot produce. (The engine-side refusal is pinned in
        ``tests/test_scheduler_runas.py``, where it is reachable.)
        """
        cfg = _settings(tmp_path, monkeypatch)
        ordinary = _capture_register(monkeypatch)
        provisioned = _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = "CORP\\svc_sync$"
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)

        assert ordinary["calls"] == 0
        assert provisioned["calls"] == 0, "the gate must refuse before anything is dispatched"
        # And the refusal is VISIBLE — a disabled primary with no cause is the defect plan
        # 0046 B's `_paint_account_note` exists to prevent.
        assert _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE)


# --------------------------------------------------------------------------- #
# Plan 0049 S-4 — the gMSA disclosure (Settings mount only)                     #
# --------------------------------------------------------------------------- #
_GMSA = "CORP\\svc_districtsync$"


def _register_gmsa(tree, stub_page, *, account=_GMSA) -> None:
    """Tick the disclosure, type a gMSA, press Schedule, confirm the scope, drain the worker.

    A gMSA is always FOREIGN, so the press routes through ``request_provision`` and the
    machine-scope confirm exactly as a password service account does — that shared route is
    why this helper mirrors ``_register_service_account`` rather than replacing it.
    """
    _toggle_gmsa(tree, True)
    _account_field(tree).value = account
    _press_register(tree)
    _confirm_scope(stub_page)
    _drain(stub_page)


class TestTheDisclosureIsSettingsOnly:
    """The FIRST wizard/Settings fork in ``_build_schedule_section``. Proven on BOTH mounts, so
    "absent from the wizard" cannot pass because the control stopped existing anywhere."""

    def test_the_wizard_mount_has_no_tick_box(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _schedule_section(cfg, stub_page)
        assert _gmsa_box(tree) is None
        assert not _has_text_containing(tree, GMSA_IT_DOC_TITLE)

    def test_the_settings_mount_has_one(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        assert _gmsa_box(tree) is not None

    def test_the_REAL_settings_scroll_offers_it(self, tmp_path, stub_page, monkeypatch):
        """Mounted through ``build_setup``, deliberately, NOT through
        ``_settings_schedule_section``: that helper spells ``allow_gmsa=True`` itself, so every
        other row in this file would stay green if ``_mount_settings`` stopped passing it. This
        is the row that pins the CALL SITE."""
        _settings(tmp_path, monkeypatch)
        tree = build_setup(stub_page)
        assert _gmsa_box(tree) is not None

    def test_the_REAL_wizard_schedule_step_does_not(self, tmp_path, stub_page, monkeypatch):
        """Its twin on the other mount, through the same real entry point — so "absent from the
        wizard" is a fact about the shipped wizard rather than about a default this file chose."""
        _settings(tmp_path, monkeypatch, setup_completed=False)
        tree = build_setup(stub_page)
        _button_by_content(tree, "Set up later").on_click(None)  # defer delivery → Schedule
        assert _has_text(tree, "Step 4 of 5"), "the wizard did not reach its Schedule step"
        assert _gmsa_box(tree) is None
        assert _textfield_by_label(tree, "Windows account password") is not None  # the step IS there

    def test_the_wizard_still_refuses_a_dollar_suffixed_account(self, tmp_path, stub_page, monkeypatch):
        """With no way to declare the kind, the wizard's shape rung is still
        ``validate_run_as_user`` — so today's refusal is byte-identical there."""
        cfg = _settings(tmp_path, monkeypatch)
        ordinary = _capture_register(monkeypatch)
        provisioned = _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _schedule_section(cfg, stub_page)
        _account_field(tree).value = _GMSA
        _press_register(tree)
        _drain(stub_page)

        assert ordinary["calls"] == 0
        assert provisioned["calls"] == 0
        assert _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE)


class TestWhatTheDisclosureReveals:
    def _on(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        return cfg, tree

    def test_it_leads_with_what_has_to_be_in_place_first(self, tmp_path, stub_page, monkeypatch):
        # The disclosure's opening line, which the three prerequisite rows hang off. It used to
        # lead with the "not yet tested" caption (retired 2026-09-21), so this row is what keeps
        # the surviving lead pinned rather than leaving the reveal proved only by its list.
        _cfg, tree = self._on(tmp_path, stub_page, monkeypatch)
        assert _has_text_containing(tree, "your IT team needs to have done all three")

    def test_it_reveals_all_three_prerequisites(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._on(tmp_path, stub_page, monkeypatch)
        for item in GMSA_PREREQUISITES:
            assert _has_text_containing(tree, item), f"the disclosure does not show {item!r}"

    def test_it_names_the_hand_to_IT_document(self, tmp_path, stub_page, monkeypatch):
        # A document an admin cannot ask for by name is worse than no reference at all.
        _cfg, tree = self._on(tmp_path, stub_page, monkeypatch)
        assert _has_text_containing(tree, GMSA_IT_DOC_TITLE)

    def test_nothing_is_revealed_while_it_is_off(self, tmp_path, stub_page, monkeypatch):
        """The negative twin for all three rows above — and the state every configured install
        mounts in."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        assert not _has_text_containing(tree, "your IT team needs to have done all three")
        assert not _has_text_containing(tree, GMSA_IT_DOC_TITLE)
        for item in GMSA_PREREQUISITES:
            assert not _has_text_containing(tree, item)

    def test_untoggling_takes_it_all_away_again(self, tmp_path, stub_page, monkeypatch):
        _cfg, tree = self._on(tmp_path, stub_page, monkeypatch)
        _toggle_gmsa(tree, False)
        assert not _has_text_containing(tree, "your IT team needs to have done all three")
        assert not _has_text_containing(tree, GMSA_IT_DOC_TITLE)
        for item in GMSA_PREREQUISITES:
            assert not _has_text_containing(tree, item)


class TestThePasswordFieldIsHiddenNotLeftDead:
    """A managed service account has no password. A live credential field that cannot matter is
    the dead-control problem this file already names, one step worse — it invites an admin to
    type a secret into a control whose value would be refused."""

    def test_the_slot_is_visible_before_the_toggle(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        slot = _password_slot(tree)
        assert slot is not None and slot.visible is not False

    def test_toggling_on_hides_it(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        assert _password_slot(tree).visible is False

    def test_toggling_off_restores_it(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        _toggle_gmsa(tree, False)
        assert _password_slot(tree).visible is True

    def test_hiding_it_CLEARS_a_typed_credential(self, tmp_path, stub_page, monkeypatch):
        """Not cosmetic: a password left in an off-screen field would still be read by
        ``_account_facts``, producing the kind/password combination ``Principal`` refuses."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = "hunter2"
        _toggle_gmsa(tree, True)
        assert _textfield_by_label(tree, "Windows account password").value == ""


class TestTheGmsaRegistersAndIsRecorded:
    def test_it_declares_the_managed_service_account_kind_with_no_password(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        seen = _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _register_gmsa(tree, stub_page)

        assert seen["calls"] == 1
        assert seen["principal"].kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT
        assert seen["principal"].user == _GMSA
        assert seen["principal"].password is None

    def test_it_writes_the_fourth_facet_in_the_same_save(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        saves = {"n": 0}
        monkeypatch.setattr(AppConfig, "save", lambda self: saves.__setitem__("n", saves["n"] + 1))
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _register_gmsa(tree, stub_page)

        assert saves["n"] == 1, "the four facets must land in ONE save"
        assert cfg.schedule_registered is True
        assert cfg.schedule_run_as_user == _GMSA
        assert cfg.schedule_run_as_kind == PrincipalKind.MANAGED_SERVICE_ACCOUNT.value

    def test_it_records_the_task_as_UNATTENDED_despite_carrying_no_password(self, tmp_path, stub_page, monkeypatch):
        """``bool(password)`` would record an unattended task as logged-on-only, and the
        reconcile would then stop guarding it against a silent downgrade. The facet is keyed on
        the KIND instead."""
        cfg = _settings(tmp_path, monkeypatch)
        _capture_provision(monkeypatch)
        _stub_handover(monkeypatch, handed_over=False)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _register_gmsa(tree, stub_page)

        assert cfg.schedule_unattended is True

    def test_a_password_register_still_records_the_password_kind(self, tmp_path, stub_page, monkeypatch):
        """The NON-vacuous twin: the facet is not hardcoded to the new value."""
        cfg = _settings(tmp_path, monkeypatch)
        _capture_register(monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _textfield_by_label(tree, "Windows account password").value = "pw"
        _press_register(tree)
        _drain(stub_page)

        assert cfg.schedule_run_as_kind == PrincipalKind.PASSWORD.value
        assert cfg.schedule_unattended is True

    def test_a_blank_password_register_records_the_interactive_token_kind(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        _capture_register(monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _press_register(tree)
        _drain(stub_page)

        assert cfg.schedule_run_as_kind == PrincipalKind.INTERACTIVE_TOKEN.value
        assert cfg.schedule_unattended is False


class TestTheToggleIsSessionStateSeededFromTheRecord:
    def test_merely_toggling_persists_nothing(self, tmp_path, stub_page, monkeypatch):
        """The toggle is session state, not config: only a CONFIRMED register may write the
        kind, so the tick box on its own must leave ``config.json`` alone."""
        cfg = _settings(tmp_path, monkeypatch)
        saves = {"n": 0}
        monkeypatch.setattr(AppConfig, "save", lambda self: saves.__setitem__("n", saves["n"] + 1))
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        _toggle_gmsa(tree, False)

        assert cfg.schedule_run_as_kind == ""
        assert saves["n"] == 0

    def test_a_recorded_gmsa_install_mounts_with_the_disclosure_ON(self, tmp_path, stub_page, monkeypatch):
        """What makes "session state, not config" coherent: the RECORD is what survives. Without
        this seed a Settings mount over a live gMSA task would meet its own prefilled ``$``
        account with ``validate_run_as_user`` and refuse the install's own live principal."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_GMSA,
            schedule_run_as_kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT.value,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _settings_schedule_section(cfg, stub_page)

        assert _gmsa_box(tree).value is True
        assert _password_slot(tree).visible is False
        assert _has_text_containing(tree, GMSA_IT_DOC_TITLE)

    def test_a_password_install_mounts_with_it_OFF(self, tmp_path, stub_page, monkeypatch):
        """The negative twin — and the state all 20 shipped districts mount in."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
            schedule_run_as_kind=PrincipalKind.PASSWORD.value,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _settings_schedule_section(cfg, stub_page)

        assert _gmsa_box(tree).value is False
        assert _password_slot(tree).visible is not False

    def test_an_upgrader_with_no_recorded_kind_mounts_with_it_OFF(self, tmp_path, stub_page, monkeypatch):
        """The absent-value rule reaching the view: a v3.7-era record has a named principal and
        no kind, which resolves to PASSWORD — never to the untested one."""
        cfg = _settings(
            tmp_path,
            monkeypatch,
            schedule_registered=True,
            schedule_unattended=True,
            schedule_run_as_user=_SERVICE,
        )
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = _settings_schedule_section(cfg, stub_page)

        assert _gmsa_box(tree).value is False


class TestTheGateAndItsReasonFollowTheToggle:
    def test_the_gate_opens_for_a_gmsa_with_no_password(self, tmp_path, stub_page, monkeypatch):
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        _account_field(tree).value = _GMSA
        _account_field(tree).on_change(None)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is False
        assert not _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE)
        assert not _has_text_containing(tree, setup_mod._ACCOUNT_PASSWORD_NOTE)

    def test_a_malformed_gmsa_name_gets_the_MSA_shape_note_not_the_general_one(self, tmp_path, stub_page, monkeypatch):
        """The general shape note tells an admin to use letters/digits/dots/underscores/hyphens
        only — which for a gMSA means dropping the ``$`` that makes it one. Forked on the
        declared kind, so a disabled primary's visible cause is also the RIGHT cause."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        _account_field(tree).value = r"CORP\svc districtsync$"
        _account_field(tree).on_change(None)

        assert _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE_MSA)
        assert not _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE)

    def test_untoggling_closes_it_again_with_a_visible_reason(self, tmp_path, stub_page, monkeypatch):
        """The same name, now declared a password logon: the shape rung refuses the ``$`` and
        the reason is painted under the field — a disabled primary always has a visible cause."""
        cfg = _settings(tmp_path, monkeypatch)
        tree, _ = _settings_schedule_section(cfg, stub_page)
        _toggle_gmsa(tree, True)
        _account_field(tree).value = _GMSA
        _account_field(tree).on_change(None)
        _toggle_gmsa(tree, False)

        assert _button_by_content(tree, "Schedule nightly sync").disabled is True
        assert _has_text_containing(tree, setup_mod._ACCOUNT_SHAPE_NOTE)
