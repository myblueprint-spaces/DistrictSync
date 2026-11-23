"""Plan 0055 M1 (DSYNC-94): one install-kind reader, and Velopack's hooks run before anything touches a profile.

The kind decides three safety properties for the machine-wide MSI: the console stays clean (S0b #3), no non-SYSTEM process
asks Velopack to apply (S0b #4), and a Highest task may run the program (only admins can change it). Every doubt answers
PER_USER, the safer kind.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.scheduler import task_com
from src.update import check, entry, health, startup
from src.utils import install_kind as ik
from src.utils.install_kind import InstallKind
from tests.test_installed_flavour import _install_layout, _stamp

SYSTEM, ADMINS = "S-1-5-18", "S-1-5-32-544"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
USERS, AUTH_USERS, CREATOR_OWNER, ALL_APP_PACKAGES = "S-1-5-32-545", "S-1-5-11", "S-1-3-0", "S-1-15-2-1"
FULL, MODIFY, READ_EXECUTE, GENERIC_ALL = 0x1F01FF, 0x1301BF, 0x1200A9, 0x10000000
ALLOW, DENY, INHERIT_ONLY = 0, 1, 0x08

#: What Program Files gives a folder created in it (S0b #2: icacls of the MSI's install root).
PROGRAM_FILES = (
    (ALLOW, 0, FULL, TRUSTED_INSTALLER),
    (ALLOW, INHERIT_ONLY, GENERIC_ALL, TRUSTED_INSTALLER),
    (ALLOW, 0, FULL, SYSTEM),
    (ALLOW, INHERIT_ONLY, GENERIC_ALL, SYSTEM),
    (ALLOW, 0, FULL, ADMINS),
    (ALLOW, INHERIT_ONLY, GENERIC_ALL, ADMINS),
    (ALLOW, 0, READ_EXECUTE, USERS),
    (ALLOW, INHERIT_ONLY, 0xA0000000, USERS),
    (ALLOW, INHERIT_ONLY, GENERIC_ALL, CREATOR_OWNER),
    (ALLOW, 0, READ_EXECUTE, ALL_APP_PACKAGES),
)
#: What a data drive's root gives a new folder by default: Authenticated Users can Modify.
DATA_DRIVE_DEFAULT = (
    (ALLOW, 0, FULL, SYSTEM),
    (ALLOW, 0, FULL, ADMINS),
    (ALLOW, 0, MODIFY, AUTH_USERS),
    (ALLOW, 0, READ_EXECUTE, USERS),
)


def _msi_layout(tmp_path: Path) -> Path:
    exe = Path(_install_layout(tmp_path))
    (exe.parent.parent / ik.MSI_MARKER).write_bytes(b"")
    return exe


@pytest.fixture
def installed(monkeypatch):
    _stamp(monkeypatch, "installed")


def _dacl(monkeypatch, entries, owner=TRUSTED_INSTALLER):
    monkeypatch.setattr(ik, "_dacl_entries", lambda _path: entries)
    monkeypatch.setattr(ik, "_owner_sid", lambda _path: owner)


class TestTheKinds:
    def test_a_portable_process_is_portable_even_inside_an_install_layout(self, tmp_path, monkeypatch):
        _stamp(monkeypatch, "portable")
        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        assert ik.install_kind() is InstallKind.PORTABLE

    def test_no_layout_is_portable(self, installed, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.executable", str(tmp_path / "DistrictSync.exe"))
        assert ik.install_kind() is InstallKind.PORTABLE

    def test_the_setup_exe_install_without_the_msi_marker_is_per_user(self, installed, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.executable", _install_layout(tmp_path))
        _dacl(monkeypatch, PROGRAM_FILES)  # even with an admin-only ACL: no marker, no machine-wide
        assert ik.install_kind() is InstallKind.PER_USER

    def test_the_msi_in_program_files_is_machine_wide(self, installed, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        _dacl(monkeypatch, PROGRAM_FILES)
        assert ik.install_kind() is InstallKind.MACHINE

    def test_the_msi_in_an_unhardened_data_folder_is_per_user(self, installed, tmp_path, monkeypatch):
        """``VELOPACK_INSTALLDIR`` on a data drive inherits Authenticated Users: Modify, so non-admins can change it."""
        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        _dacl(monkeypatch, DATA_DRIVE_DEFAULT)
        assert ik.install_kind() is InstallKind.PER_USER

    def test_an_unreadable_dacl_is_never_machine_wide(self, installed, tmp_path, monkeypatch):
        def unreadable(_path):
            raise OSError("denied")

        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        monkeypatch.setattr(ik, "_dacl_entries", unreadable)
        assert ik.install_kind() is InstallKind.PER_USER


class TestTheAclRule:
    def test_an_inherit_only_ace_does_not_make_the_folder_writable(self, monkeypatch):
        _dacl(monkeypatch, ((ALLOW, INHERIT_ONLY, GENERIC_ALL, CREATOR_OWNER),))
        assert ik.writable_by_non_admin(Path(".")) is False

    def test_a_deny_ace_never_makes_a_folder_writable(self, monkeypatch):
        _dacl(monkeypatch, ((DENY, 0, FULL, USERS),))
        assert ik.writable_by_non_admin(Path(".")) is False

    def test_read_and_execute_for_users_is_not_writing(self, monkeypatch):
        _dacl(monkeypatch, ((ALLOW, 0, READ_EXECUTE, USERS),))
        assert ik.writable_by_non_admin(Path(".")) is False

    @pytest.mark.parametrize(
        "mask", [0x2, 0x4, 0x10, 0x40, 0x100, 0x10000, 0x40000, 0x80000, 0x40000000, GENERIC_ALL, MODIFY]
    )
    def test_any_write_class_right_for_another_account_is_writing(self, monkeypatch, mask):
        _dacl(monkeypatch, ((ALLOW, 0, mask, "S-1-5-21-1-2-3-1001"),))
        assert ik.writable_by_non_admin(Path(".")) is True

    def test_positive_twin_the_trusted_writers_may_write(self, monkeypatch):
        _dacl(monkeypatch, tuple((ALLOW, 0, FULL, sid) for sid in (SYSTEM, ADMINS, TRUSTED_INSTALLER)))
        assert ik.writable_by_non_admin(Path(".")) is False

    def test_an_untrusted_owner_is_writing_whatever_the_dacl_says(self, monkeypatch):
        """The owner can always rewrite the DACL: a user who created the folder first, then let an admin install into it."""
        _dacl(monkeypatch, PROGRAM_FILES, owner="S-1-5-21-1-2-3-1001")
        assert ik.writable_by_non_admin(Path(".")) is True

    @pytest.mark.parametrize("ace_type", [5, 9, 0xB])  # object, callback, callback-object
    def test_an_ace_this_reader_cannot_evaluate_is_doubt_and_doubt_is_writing(self, monkeypatch, ace_type):
        _dacl(monkeypatch, ((ace_type, 0, READ_EXECUTE, USERS),))
        assert ik.writable_by_non_admin(Path(".")) is True

    def test_a_parent_that_lets_others_delete_the_root_is_not_machine_wide(self, tmp_path, monkeypatch):
        root = Path(_msi_layout(tmp_path)).parent.parent
        clean, swappable = PROGRAM_FILES, (*PROGRAM_FILES, (ALLOW, 0, 0x40, USERS))
        monkeypatch.setattr(ik, "_owner_sid", lambda _path: TRUSTED_INSTALLER)
        monkeypatch.setattr(ik, "_dacl_entries", lambda path: swappable if path == root.parent else clean)
        assert ik.is_machine_wide_root(root) is False
        monkeypatch.setattr(ik, "_dacl_entries", lambda path: clean)
        assert ik.is_machine_wide_root(root) is True  # positive twin

    @pytest.mark.skipif(sys.platform != "win32", reason="reads a real DACL")
    def test_the_real_reader_sees_this_accounts_own_write_right_on_a_folder_it_created(self, tmp_path):
        """A folder in the user's own temp folder is owned and writable by that (non-trusted) account."""
        assert ik.writable_by_non_admin(tmp_path) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="reads a real DACL")
    def test_the_real_reader_finds_program_files_closed_to_non_admins(self):
        """The negative twin only correct flag, mask and owner parsing passes: Program Files is admin-only."""
        assert ik.writable_by_non_admin(Path(os.environ["PROGRAMFILES"])) is False


class TestWhatTheKindChanges:
    def test_no_update_check_and_no_health_on_a_machine_wide_install(self, eligible, monkeypatch):
        monkeypatch.setattr(check, "install_kind", lambda: InstallKind.MACHINE)
        monkeypatch.setattr(health, "install_kind", lambda: InstallKind.MACHINE)
        assert check.skip_reason(config=eligible.config) == "machine_wide"
        assert health.health_skip_reason() == "machine_wide"

    def test_positive_twin_a_per_user_install_is_not_skipped_for_the_kind(self, eligible, monkeypatch):
        monkeypatch.setattr(check, "install_kind", lambda: InstallKind.PER_USER)
        assert check.skip_reason(config=eligible.config) != "machine_wide"

    def test_the_per_user_start_guard_does_not_apply_to_a_machine_wide_install(self, installed, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        _dacl(monkeypatch, PROGRAM_FILES)
        assert entry._applicable() is False

    def test_a_machine_wide_install_is_not_user_writable_for_the_run_level(self, tmp_path, monkeypatch):
        exe = str(_msi_layout(tmp_path))
        _dacl(monkeypatch, PROGRAM_FILES)
        assert task_com.runs_from_user_writable_install(exe) is False
        assert (
            task_com.run_level_for(task_com.PrincipalKind.PASSWORD, run_highest=True, exe=exe)
            == task_com.TASK_RUNLEVEL_HIGHEST
        )
        # Positive twin: the same layout writable by non-admins stays Limited (DSYNC-66).
        _dacl(monkeypatch, DATA_DRIVE_DEFAULT)
        assert task_com.runs_from_user_writable_install(exe) is True


class TestTheVelopackStartupOnAMachineWideInstall:
    @pytest.fixture
    def hook_calls(self, installed, tmp_path, monkeypatch):
        calls: list[str] = []
        fake = type("M", (), {"run_startup_hook": staticmethod(lambda: calls.append("hook"))})
        monkeypatch.setattr(startup.importlib, "import_module", lambda _name: fake)
        monkeypatch.setattr("sys.executable", str(_msi_layout(tmp_path)))
        return calls

    def test_an_ordinary_start_does_not_call_app_run_so_the_console_stays_clean(self, hook_calls, monkeypatch):
        _dacl(monkeypatch, PROGRAM_FILES)
        monkeypatch.setattr("sys.argv", ["DistrictSync.exe", "--sis", "myedbc"])
        assert startup.run_velopack_startup() is False
        assert hook_calls == []

    def test_a_velopack_hook_still_reaches_app_run(self, hook_calls, monkeypatch):
        _dacl(monkeypatch, PROGRAM_FILES)
        monkeypatch.setattr("sys.argv", ["DistrictSync.exe", "--veloapp-install", "1.0.0"])
        assert startup.run_velopack_startup() is True
        assert hook_calls == ["hook"]

    def test_positive_twin_a_per_user_install_calls_it_on_every_start_as_before(self, hook_calls, monkeypatch):
        _dacl(monkeypatch, DATA_DRIVE_DEFAULT)
        monkeypatch.setattr("sys.argv", ["DistrictSync.exe", "--sis", "myedbc"])
        assert startup.run_velopack_startup() is True
        assert hook_calls == ["hook"]


_WATCHED = ("src.update.entry", "src.utils.paths", "src.config.app_config", "pandas", "keyring")

_HOOK_PROBE = """
import runpy, sys
from pathlib import Path
import src.update.startup as s
# The REAL run_velopack_startup runs: an installed build under an install, with Velopack's hook module faked.
s.is_installed_build = lambda: True
s.install_root = lambda executable=None: Path(".")
import types
fake = types.ModuleType(s._HOOK_MODULE)
fake.run_startup_hook = lambda: print("HOOK called")
sys.modules[s._HOOK_MODULE] = fake
sys.argv = ["DistrictSync.exe", *ARGS]
try:
    runpy.run_module("src.main", run_name="__main__")
except SystemExit as e:
    print("EXIT", e.code)
for name in WATCHED:
    print(name, name in sys.modules)
"""


def _probe(args: list[str]) -> dict[str, str]:
    code = _HOOK_PROBE.replace("ARGS", repr(args)).replace("WATCHED", repr(_WATCHED))
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180, check=False, cwd=root
    )
    return dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)


def test_a_hook_is_handled_before_the_start_guard_the_profile_or_any_heavy_import():
    seen = _probe(["--veloapp-install", "1.0.0"])
    assert seen.get("HOOK") == "called"
    assert seen.get("EXIT") == "0"
    for name in _WATCHED:
        assert seen.get(name) == "False", name


def test_positive_twin_an_ordinary_invocation_still_goes_through_the_start_guard_and_the_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path))  # --version must not touch the real profile
    seen = _probe(["--version"])
    assert seen.get("src.update.entry") == "True"
    assert seen.get("src.config.app_config") == "True"
    assert seen.get("EXIT") == "0"


class TestTheHookEntry:
    def test_a_portable_build_given_a_hook_argument_exits_quietly(self, monkeypatch):
        _stamp(monkeypatch, "portable")
        assert startup.run_velopack_hook() == 0

    def test_a_hook_that_raises_ends_with_1_instead_of_an_error_dialog(self, monkeypatch):
        def boom() -> bool:
            raise RuntimeError("velopack failed")

        monkeypatch.setattr(startup, "run_velopack_startup", boom)
        assert startup.run_velopack_hook() == 1

    def test_one_spelling_of_the_hook_prefix(self):
        assert startup.is_velopack_hook(["--veloapp-install", "1"]) is True
        assert startup.is_velopack_hook(["--version"]) is False
        assert entry.start_counts(["--veloapp-updated", "1"]) is False
