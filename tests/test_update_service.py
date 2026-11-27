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

    def test_it_always_exits_0_even_when_the_folder_cannot_be_resolved(self, eligible, monkeypatch):
        """Review A2: ``machine_data_dir`` raises a RuntimeError (not an OSError) under a WIN_PD override."""

        def refused():
            raise RuntimeError("refused")

        monkeypatch.setattr(service, "state_dir", refused)
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

    @pytest.mark.parametrize("mode", ["off", "notify", "on"])
    def test_machine_setup_makes_it_whatever_the_mode(self, monkeypatch, mode):
        """Review A3: made by an administrator at setup, before anyone can plant it, and there when Group Policy later
        turns updates on."""
        order = []
        monkeypatch.setattr(provisioning, "ensure_updater_dir", lambda: order.append("folder"))
        monkeypatch.setattr(provisioning, "apply_provision", lambda payload, **kw: order.append("commit"))
        provisioning.apply_machine_setup({"user": "", "update_mode": mode})
        assert order == ["folder", "commit"]

    def test_inherit_only_grants_make_the_folder_untrusted(self, tmp_path, monkeypatch):
        """Review A4: an inherit-only Modify for Users would make every file the updater writes there theirs."""
        from src.utils import install_kind

        monkeypatch.setattr(install_kind, "_owner_sid", lambda path: "S-1-5-32-544")
        monkeypatch.setattr(install_kind, "_dacl_entries", lambda path: ((0, 0x08 | 0x03, 0x1301BF, "S-1-5-32-545"),))
        assert service.state_dir_trusted(tmp_path) is False
        assert install_kind.writable_by_non_admin(tmp_path) is False  # the install-root rule is unchanged

    def test_a_log_file_others_can_change_is_never_appended(self, eligible, monkeypatch):
        (eligible / service.LOG_FILE).write_text("old\n", encoding="utf-8")
        monkeypatch.setattr("src.utils.install_kind.writable_by_non_admin", lambda path, **kw: True)
        service.run_update_service()
        assert (eligible / service.LOG_FILE).read_text(encoding="utf-8") == "old\n"


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

    def test_a_kind_that_flips_mid_check_never_reaches_the_full_check(self, machine, monkeypatch):
        """Review A1: the first read says machine-wide, a later one does not (an ACL read failing while Velopack swaps
        ``current``). The check is decided once, so it notifies at most and never downloads or hands off."""
        reads = iter([InstallKind.MACHINE] + [InstallKind.PER_USER] * 20)
        monkeypatch.setattr(check, "install_kind", lambda: next(reads))
        calls = []

        class Module:
            class HealthStore:
                def __init__(self, path):
                    pass

                def last_sequence(self):
                    return 0

                def bad_versions(self):
                    return ()

            class FeedFetcher:
                def __init__(self, *a, **k):
                    pass

            UpdateRefused = type("UpdateRefused", (Exception,), {})

            @staticmethod
            def check_for_update(*a, **k):
                calls.append("full check")

            @staticmethod
            def updates_enabled(*a):
                return True

            @staticmethod
            def parse_version(v):
                return (1,)

        monkeypatch.setattr(
            check, "_notify_only", lambda *a, **k: (calls.append("notify"), check.CheckOutcome("none", "up_to_date"))[1]
        )
        monkeypatch.setattr(check, "app_identity", lambda module: None)
        monkeypatch.setattr(policy, "effective_update_mode", lambda **kw: policy.EffectiveMode(policy.ON, "machine"))
        target = (
            check.FeedTarget(host="h.example.invalid", trusted_keys=("k",))
            if hasattr(check, "FeedTarget")
            else object()
        )
        check.run_update_check(check.TRIGGER_AFTER_RUN, updater=Module, target=target, now=None)
        assert "full check" not in calls


class TestHomeShowsAParsedVersionOnly:
    """Review A5: the nightly's account can write the notice; only a parsed version is ever shown."""

    def test_trailing_text_never_reaches_the_screen(self, monkeypatch):
        from src.update import status

        monkeypatch.setattr(status, "is_installed_build", lambda: True)
        monkeypatch.setattr(status, "app_version", lambda: "3.31.0")
        monkeypatch.setattr(status, "skip_reason", lambda **kw: None)
        monkeypatch.setattr(
            status, "last_check_summary", lambda state_dir=None: {"status": "available", "version": "9.9.9\nPhish here"}
        )
        assert status.newer_version_available() == "9.9.9"
        assert "Phish" not in (status.update_status_line() or "")
