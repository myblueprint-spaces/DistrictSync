"""Plan 0055 U1b (DSYNC-107): the update mode as a Settings section, and Home's "newer version" line.

The decision is pure (``update.setting``): locked under Group Policy; off/notify/on on a machine-wide install (applied by
the machine setup ladder, one UAC prompt); notify/off on a copy installed for one account; nothing on the portable build.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config.app_config import AppConfig
from src.update import policy, setting, status
from src.utils.install_kind import InstallKind


def _eff(mode: str, source: str) -> policy.EffectiveMode:
    return policy.EffectiveMode(mode, source)


class TestTheView:
    def test_a_build_without_the_updater_shows_nothing(self):
        view = setting.update_setting_view(
            installed=False,
            kind=InstallKind.PORTABLE,
            effective=_eff(policy.NOTIFY, policy.SOURCE_DEFAULT),
            shared_profile=False,
        )
        assert view.shown is False

    @pytest.mark.parametrize("kind", [InstallKind.MACHINE, InstallKind.PER_USER])
    def test_group_policy_locks_it_to_its_own_mode(self, kind):
        view = setting.update_setting_view(
            installed=True, kind=kind, effective=_eff(policy.ON, policy.SOURCE_POLICY), shared_profile=False
        )
        assert view.shown and view.locked and view.choices == (policy.ON,) and view.note == setting.POLICY_NOTE

    def test_a_machine_wide_install_offers_all_three_through_the_administrator(self):
        view = setting.update_setting_view(
            installed=True,
            kind=InstallKind.MACHINE,
            effective=_eff(policy.OFF, policy.SOURCE_MACHINE),
            shared_profile=True,
        )
        assert view.choices == (policy.NOTIFY, policy.ON, policy.OFF) and view.mode == policy.OFF
        assert view.machine_wide and not view.locked and view.note == setting.MACHINE_NOTE

    def test_a_copy_for_one_account_offers_notify_or_off_never_on(self):
        view = setting.update_setting_view(
            installed=True,
            kind=InstallKind.PER_USER,
            effective=_eff(policy.NOTIFY, policy.SOURCE_DEFAULT),
            shared_profile=False,
        )
        assert view.choices == (policy.NOTIFY, policy.OFF) and not view.machine_wide

    @pytest.mark.parametrize(
        ("source", "shared"),
        [(policy.SOURCE_MACHINE, False), (policy.SOURCE_DEFAULT, True), (policy.SOURCE_SETTING, True)],
    )
    def test_a_per_user_copy_never_saves_what_the_machine_or_the_shared_profile_decides(self, source, shared):
        view = setting.update_setting_view(
            installed=True, kind=InstallKind.PER_USER, effective=_eff(policy.ON, source), shared_profile=shared
        )
        assert view.locked and view.choices == (policy.ON,) and view.note == setting.SHARED_NOTE

    def test_every_mode_has_one_label_shared_with_the_first_run_window(self):
        from src.ui_flet import machine_setup_window

        assert set(setting.MODE_LABELS) == set(policy.UPDATE_MODES)
        assert dict(machine_setup_window.UPDATE_CHOICES) == setting.MODE_LABELS


class TestThePerUserSetting:
    @pytest.mark.parametrize(("mode", "enabled"), [(policy.NOTIFY, True), (policy.OFF, False)])
    def test_it_maps_to_update_enabled(self, mode, enabled):
        assert setting.user_setting_for(mode) is enabled

    def test_on_is_refused(self):
        with pytest.raises(ValueError):
            setting.user_setting_for(policy.ON)


class TestHomesLine:
    def test_a_newer_version_found_by_notify_is_said(self, monkeypatch):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "app_version", lambda: "3.31.0")
        monkeypatch.setattr(
            status, "last_check_summary", lambda state_dir=None: {"status": "available", "version": "3.32.0"}
        )
        assert status.newer_version_available() == "3.32.0"
        assert "3.32.0" in setting.home_line("3.32.0")

    @pytest.mark.parametrize(
        "last",
        [None, {"status": "none"}, {"status": "available", "version": "3.31.0"}, {"status": "available"}],
    )
    def test_otherwise_nothing(self, monkeypatch, last):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "app_version", lambda: "3.31.0")
        monkeypatch.setattr(status, "last_check_summary", lambda state_dir=None: last)
        monkeypatch.setattr(status, "skip_reason", lambda **kw: None)
        assert status.newer_version_available() is None

    def test_no_line_while_updates_are_off_even_with_a_recorded_newer_version(self, monkeypatch):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "app_version", lambda: "3.31.0")
        monkeypatch.setattr(
            status, "last_check_summary", lambda state_dir=None: {"status": "available", "version": "3.32.0"}
        )
        monkeypatch.setattr(status, "skip_reason", lambda **kw: "off_in_settings")
        assert status.newer_version_available() is None
        monkeypatch.setattr(status, "skip_reason", lambda **kw: None)
        assert status.newer_version_available() == "3.32.0"  # positive twin

    def test_a_raising_check_never_takes_home_down(self, monkeypatch):
        monkeypatch.setattr(status, "is_installed_build", lambda: True)

        def boom(state_dir=None):
            raise OSError("locked")

        monkeypatch.setattr(status, "last_check_summary", boom)
        assert status.newer_version_available() is None


class TestTheSection:
    """The view glue, driven on a stub page: shown only for the installed build, locked under policy."""

    def _section(self, monkeypatch, *, kind, effective, installed=True):
        from src.ui_flet.screens import setup

        monkeypatch.setattr("src.utils.build_flavor.is_installed_build", lambda: installed)
        monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: kind)
        monkeypatch.setattr(policy, "effective_update_mode", lambda **kw: effective)
        monkeypatch.setattr("src.utils.paths.is_machine_scope", lambda: False)
        return setup._build_updates_section(MagicMock(), AppConfig())

    def test_none_on_the_portable_build(self, monkeypatch):
        assert (
            self._section(monkeypatch, kind=InstallKind.PORTABLE, effective=_eff("notify", "default"), installed=False)
            is None
        )

    def test_a_per_user_choice_saves_update_enabled(self, monkeypatch):
        import flet as ft

        saved = {}
        monkeypatch.setattr(AppConfig, "save", lambda self: saved.update(enabled=self.update_enabled))
        card = self._section(monkeypatch, kind=InstallKind.PER_USER, effective=_eff("notify", "default"))
        group = next(c for c in _walk(card) if isinstance(c, ft.RadioGroup))
        event = MagicMock()
        event.control.value = policy.OFF
        group.on_change(event)
        assert saved == {"enabled": False}

    def test_policy_disables_every_choice_and_offers_no_apply(self, monkeypatch):
        import flet as ft

        card = self._section(monkeypatch, kind=InstallKind.MACHINE, effective=_eff("off", "policy"))
        radios = [c for c in _walk(card) if isinstance(c, ft.Radio)]
        assert radios and all(r.disabled for r in radios)
        assert not [c for c in _walk(card) if isinstance(c, ft.OutlinedButton)]

    def test_a_machine_wide_install_applies_with_a_button_never_a_click(self, monkeypatch):
        import flet as ft

        monkeypatch.setattr(AppConfig, "save", lambda self: pytest.fail("a machine setting is never saved per user"))
        card = self._section(monkeypatch, kind=InstallKind.MACHINE, effective=_eff("notify", "machine"))
        group = next(c for c in _walk(card) if isinstance(c, ft.RadioGroup))
        event = MagicMock()
        event.control.value = policy.ON
        group.on_change(event)
        (apply,) = [c for c in _walk(card) if isinstance(c, ft.OutlinedButton)]
        assert apply.content == setting.APPLY_LABEL and apply.disabled is False  # a real change enables it
        assert not [c for c in _walk(card) if isinstance(c, ft.FilledButton)]  # no second filled primary
        event.control.value = policy.NOTIFY
        group.on_change(event)
        assert apply.disabled is True  # back to the mode in force: nothing to apply

    @pytest.mark.parametrize(("outcome", "changed"), [("provisioned", True), ("different_account", False)])
    def test_apply_changes_only_the_mode_and_tells_the_truth(self, monkeypatch, outcome, changed):
        import asyncio

        import flet as ft

        from src.scheduler import provision_session

        asked = []

        def request(mode):
            asked.append(mode)
            return provision_session.ProvisionAttempt(outcome=provision_session.ProvisionOutcome(outcome))

        monkeypatch.setattr(provision_session, "request_update_mode", request)
        card = self._section(monkeypatch, kind=InstallKind.MACHINE, effective=_eff("notify", "machine"))
        group = next(c for c in _walk(card) if isinstance(c, ft.RadioGroup))
        event = MagicMock()
        event.control.value = policy.ON
        group.on_change(event)
        (apply,) = [c for c in _walk(card) if isinstance(c, ft.OutlinedButton)]
        asyncio.run(apply.on_click(None))
        assert asked == [policy.ON]
        text = "\n".join(str(getattr(c, "value", "")) for c in _walk(card))
        assert (setting.APPLIED_NOTE in text) is changed
        assert group.value == (policy.ON if changed else policy.NOTIFY)  # a failure shows the mode in force


class TestTheModeOnlyOp:
    """U1b review F2: Settings' Apply writes ONLY the mode: no profile copy, no delivery password, no handover."""

    def test_the_request_carries_only_the_mode(self):
        from src.scheduler import provisioning

        assert provisioning.build_update_mode_payload("on") == {"op": "set_update_mode", "update_mode": "on"}

    def test_the_request_refuses_under_the_test_profile(self, monkeypatch, tmp_path):
        from src.scheduler import provisioning

        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path))
        with pytest.raises(provisioning.ProvisionRefused):
            provisioning.build_update_mode_payload("on")

    def test_the_child_writes_the_mode_only_on_a_set_up_computer(self, monkeypatch):
        from src.scheduler import provisioning
        from src.utils import paths

        written = []
        monkeypatch.setattr(provisioning, "_write_update_mode", written.append)
        monkeypatch.setattr(provisioning, "ensure_updater_dir", lambda: None)  # never the real %ProgramData%
        monkeypatch.setattr(paths, "machine_switch_on", lambda: False)
        with pytest.raises(ValueError):
            provisioning.apply_update_mode({"op": "set_update_mode", "update_mode": "on"})
        assert written == []
        monkeypatch.setattr(paths, "machine_switch_on", lambda: True)
        provisioning.apply_update_mode({"op": "set_update_mode", "update_mode": "on"})
        assert written == ["on"]

    def test_the_elevated_op_refuses_any_extra_field(self, monkeypatch):
        from src.scheduler import elevated_apply, provisioning

        monkeypatch.setattr(provisioning, "apply_update_mode", lambda payload: pytest.fail("ran with extra fields"))
        with pytest.raises(ValueError):
            elevated_apply._do_set_update_mode({"op": "set_update_mode", "update_mode": "on", "sftp_password": "x"})

    def test_the_session_never_hands_over_or_seeds(self, monkeypatch):
        from src.scheduler import provision_session

        seen = {}

        def elevate(payload, *, wants_check):
            seen.update(payload)
            return provision_session.ProvisionAttempt(outcome=provision_session.ProvisionOutcome.PROVISIONED)

        monkeypatch.setattr(provision_session, "_elevate_and_read", elevate)
        monkeypatch.setattr(provision_session, "complete_handover", lambda **kw: pytest.fail("handed over"))
        assert provision_session.request_update_mode("off").outcome.value == "provisioned"
        assert seen == {"op": "set_update_mode", "update_mode": "off"}

    @pytest.mark.parametrize("outcome", ["different_account", "declined", "launch_failed", "failed", "unconfirmed"])
    def test_only_a_confirmed_change_says_changed(self, outcome):
        changed, text = setting.apply_message(outcome)
        assert changed is False and text != setting.APPLIED_NOTE


def _walk(control):  # noqa: ANN001, ANN202
    import flet as ft

    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        for item in child if isinstance(child, list) else [child] if child is not None else []:
            if isinstance(item, ft.Control):
                yield from _walk(item)
