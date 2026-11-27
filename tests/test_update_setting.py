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
            installed=False, kind=InstallKind.PORTABLE, effective=_eff(policy.NOTIFY, policy.SOURCE_DEFAULT)
        )
        assert view.shown is False

    @pytest.mark.parametrize("kind", [InstallKind.MACHINE, InstallKind.PER_USER])
    def test_group_policy_locks_it_to_its_own_mode(self, kind):
        view = setting.update_setting_view(installed=True, kind=kind, effective=_eff(policy.ON, policy.SOURCE_POLICY))
        assert view.shown and view.locked and view.choices == (policy.ON,) and view.note == setting.POLICY_NOTE

    def test_a_machine_wide_install_offers_all_three_through_the_administrator(self):
        view = setting.update_setting_view(
            installed=True, kind=InstallKind.MACHINE, effective=_eff(policy.OFF, policy.SOURCE_MACHINE)
        )
        assert view.choices == (policy.NOTIFY, policy.ON, policy.OFF) and view.mode == policy.OFF
        assert view.machine_wide and not view.locked and view.note == setting.MACHINE_NOTE

    def test_a_copy_for_one_account_offers_notify_or_off_never_on(self):
        view = setting.update_setting_view(
            installed=True, kind=InstallKind.PER_USER, effective=_eff(policy.NOTIFY, policy.SOURCE_DEFAULT)
        )
        assert view.choices == (policy.NOTIFY, policy.OFF) and not view.machine_wide

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
        assert status.newer_version_available() is None
        assert setting.home_line(None) is None

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
        assert [c.content for c in _walk(card) if isinstance(c, ft.OutlinedButton)] == [setting.APPLY_LABEL]
        assert not [c for c in _walk(card) if isinstance(c, ft.FilledButton)]  # no second filled primary


def _walk(control):  # noqa: ANN001, ANN202
    import flet as ft

    yield control
    for attr in ("controls", "content"):
        child = getattr(control, attr, None)
        for item in child if isinstance(child, list) else [child] if child is not None else []:
            if isinstance(item, ft.Control):
                yield from _walk(item)
