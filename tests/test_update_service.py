"""Plan 0055 U2a (DSYNC-98): the SYSTEM updater's entry, its trusted state folder, and the machine-wide nightly notice.

U2a installs nothing: ``--update-service`` decides a refusal from admin-writable inputs only, logs one line into a
folder only SYSTEM/Administrators can change, and exits 0. The download and apply (U2 steps 3-6) and the health probe
wait on the update feed and on U3.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from src.scheduler import provisioning
from src.update import check, policy, service
from src.utils.install_kind import InstallKind

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def eligible(monkeypatch, tmp_path):
    """Every rung passes up to the apply, which U2a does not build."""
    folder = tmp_path / "DistrictSync-Updater"
    folder.mkdir()
    monkeypatch.setattr(service, "state_dir", lambda: folder)
    monkeypatch.setattr(service, "state_dir_trusted", lambda path: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: InstallKind.MACHINE)
    monkeypatch.setattr(policy, "effective_update_mode", lambda **kw: policy.EffectiveMode(policy.ON, "machine"))
    monkeypatch.setattr("src.update.feed.production_target", lambda: object())
    return folder


class TestTheRefusals:
    def test_with_everything_in_place_it_still_installs_nothing(self, eligible):
        assert service.refusal() == service.APPLY_NOT_BUILT

    def test_the_order(self, eligible, monkeypatch):
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: False)
        assert service.refusal() == service.STATE_UNTRUSTED
        monkeypatch.setattr("src.update.feed.production_target", lambda: None)
        assert service.refusal() == service.NOT_CONFIGURED
        monkeypatch.setattr(
            policy, "effective_update_mode", lambda **kw: policy.EffectiveMode(policy.NOTIFY, "default")
        )
        assert service.refusal() == service.MODE_NOT_ON
        monkeypatch.setattr("src.utils.install_kind.install_kind", lambda: InstallKind.PER_USER)
        assert service.refusal() == service.NOT_MACHINE_WIDE
        monkeypatch.setattr(sys, "platform", "linux")
        assert service.refusal() == service.NOT_WINDOWS

    def test_the_mode_is_read_from_hklm_only(self, eligible, monkeypatch):
        seen = {}

        def effective(**kw):
            seen.update(kw)
            return policy.EffectiveMode(policy.ON, "machine")

        monkeypatch.setattr(policy, "effective_update_mode", effective)
        service.refusal()
        assert seen == {"setting_enabled": True}  # config.json (writable by the setup user) is never an input

    def test_it_never_touches_the_profile_or_the_settings(self, eligible, monkeypatch):
        from src.config.app_config import AppConfig
        from src.utils import paths

        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: pytest.fail("read config.json")))
        monkeypatch.setattr(paths, "user_data_dir", lambda: pytest.fail("resolved a profile"))
        monkeypatch.setattr(paths, "pin_data_dir", lambda: pytest.fail("pinned a profile"))
        assert service.run_update_service() == 0

    def test_a_surprise_installs_nothing(self, eligible, monkeypatch):
        def boom():
            raise RuntimeError("registry")

        monkeypatch.setattr("src.utils.install_kind.install_kind", boom)
        assert service.refusal() == service.STATE_UNTRUSTED


class TestTheLog:
    def test_one_line_per_run_with_a_code_and_no_path(self, eligible):
        assert service.run_update_service() == 0
        line = (eligible / service.LOG_FILE).read_text(encoding="utf-8").strip()
        assert (
            line.endswith(f"reason={service.APPLY_NOT_BUILT}") and "\\" not in line and "/" not in line.split()[0][:4]
        )

    def test_never_into_an_untrusted_folder(self, eligible, monkeypatch):
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: False)
        service.run_update_service()
        assert not (eligible / service.LOG_FILE).exists()


class TestTheStateFolder:
    def test_it_is_untrusted_when_missing_or_writable_by_others(self, tmp_path, monkeypatch):
        assert service.state_dir_trusted(tmp_path / "missing") is False
        monkeypatch.setattr("src.utils.install_kind.writable_by_non_admin", lambda path, **kw: True)
        assert service.state_dir_trusted(tmp_path) is False
        monkeypatch.setattr("src.utils.install_kind.writable_by_non_admin", lambda path, **kw: False)
        assert service.state_dir_trusted(tmp_path) is True  # positive twin

    def test_the_descriptor_is_protected_and_grants_users_read_only(self):
        sddl = provisioning.updater_dir_sddl()
        assert "D:P" in sddl and "(A;OICI;FA;;;SY)" in sddl and "(A;OICI;FA;;;BA)" in sddl
        assert f"(A;OICI;{provisioning.READ_EXECUTE};;;BU)" in sddl
        assert ";;;AU)" not in sddl and "WD)" not in sddl

    def test_a_planted_folder_is_never_adopted(self, monkeypatch, tmp_path):
        def exists(path, sddl):
            raise FileExistsError(path)

        monkeypatch.setattr(provisioning, "_create_directory_with_sddl", exists)
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: False)
        with pytest.raises(provisioning.ProvisionRefused) as info:
            provisioning.ensure_updater_dir()
        assert info.value.step is provisioning.ProvisionStep.PRE_EXISTING
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: True)
        provisioning.ensure_updater_dir()  # positive twin: an admin-made folder is accepted

    def test_machine_setup_makes_it_only_when_the_mode_is_on(self, monkeypatch):
        made, provisioned = [], []
        monkeypatch.setattr(provisioning, "ensure_updater_dir", lambda: made.append(1))
        monkeypatch.setattr(
            provisioning, "apply_provision", lambda payload, **kw: provisioned.append(kw["update_mode"])
        )
        provisioning.apply_machine_setup({"user": "", "update_mode": "notify"})
        assert made == [] and provisioned == ["notify"]
        provisioning.apply_machine_setup({"user": "", "update_mode": "on"})
        assert made == [1] and provisioned == ["notify", "on"]


class TestTheEntry:
    def test_it_is_dispatched_before_the_start_guard(self):
        source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
        assert source.index('["--update-service"]') < source.index("from src.update.entry import run_entry")
        tree = ast.parse(source)
        first_import = next(
            n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "src.config.app_config"
        )
        assert source.index("run_update_service") < source.index("from src.config.app_config", first_import.col_offset)


class TestTheMachineWideNightlyNotice:
    @pytest.fixture()
    def machine(self, monkeypatch):
        monkeypatch.setattr(check, "is_installed_build", lambda: True)
        monkeypatch.setattr(check, "install_root", lambda: Path("C:/Program Files/myBlueprint.DistrictSync"))
        monkeypatch.setattr(check, "install_kind", lambda: InstallKind.MACHINE)
        monkeypatch.setattr(check, "_in_machine_scope", lambda: True)

    def test_only_the_nightly_may_check_never_the_window(self, machine, monkeypatch):
        assert check.skip_reason(trigger=check.TRIGGER_WINDOW) == "machine_wide"
        assert check.skip_reason() == "machine_wide"
        reason = check.skip_reason(trigger=check.TRIGGER_AFTER_RUN, ignore_interval=True)
        assert reason not in ("machine_wide", "machine_scope")  # the nightly goes on to the ordinary guards

    def test_the_notice_lives_in_runs(self, machine, isolated_user_profile):
        assert check.updater_state_dir() == isolated_user_profile / "runs" / "updater"

    def test_the_mode_ignores_the_shared_config(self, machine, monkeypatch):
        seen = {}

        def effective(**kw):
            seen.update(kw)
            return policy.EffectiveMode(policy.ON, "machine")

        monkeypatch.setattr(policy, "effective_update_mode", effective)
        check._effective_mode(None)
        assert seen == {"setting_enabled": True}

    def test_on_a_machine_wide_install_the_nightly_only_notifies_even_in_on(self):
        source = (ROOT / "src" / "update" / "check.py").read_text(encoding="utf-8")
        assert "if _effective_mode(config).mode == policy.NOTIFY or _machine_wide():" in source
