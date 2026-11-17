"""DSYNC-61 (plan 0054 S2): the build flavour and the Velopack start-up call.

What is pinned here: the portable build never starts or imports Velopack; the installed build
starts it only under a real install, with apply-at-start OFF; the three dispatch-first modes
never reach it; ``requirements.txt`` pins the same ``velopack`` as ``tests/_pins.py``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from src import main as main_module
from src.update import startup
from src.utils import build_flavor as bf
from tests._pins import VELOPACK_VERSION

_ROOT = Path(__file__).resolve().parent.parent


def _stamp(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    mod = types.ModuleType("src._build_flavor")
    mod.flavor = value  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src._build_flavor", mod)


def _unstamped(monkeypatch: pytest.MonkeyPatch) -> None:
    # None in sys.modules makes `from src._build_flavor import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "src._build_flavor", None)


class _FakeVelopack(types.ModuleType):
    """Records the calls made on ``velopack.App()``."""

    def __init__(self) -> None:
        super().__init__("velopack")
        self.calls: list[str] = []
        outer = self

        class App:
            def set_auto_apply_on_startup(self, value: bool) -> None:
                outer.calls.append(f"auto_apply={value}")

            def run(self) -> None:
                outer.calls.append("run")

        self.App = App


def _install_layout(tmp_path: Path, *, with_update_exe: bool = True) -> str:
    root = tmp_path / "myBlueprint.DistrictSync"
    (root / "current").mkdir(parents=True)
    exe = root / "current" / "DistrictSync.exe"
    exe.write_text("x")
    if with_update_exe:
        (root / "Update.exe").write_text("x")
    return str(exe)


class TestBuildFlavor:
    def test_unstamped_is_portable(self, monkeypatch):
        _unstamped(monkeypatch)
        assert bf.build_flavor() == "portable"
        assert not bf.is_installed_build()

    def test_stamped_installed(self, monkeypatch):
        _stamp(monkeypatch, "installed")
        assert bf.build_flavor() == "installed"
        assert bf.is_installed_build()

    def test_stamped_portable(self, monkeypatch):
        _stamp(monkeypatch, "portable")
        assert bf.build_flavor() == "portable"

    @pytest.mark.parametrize("bad", ["", "Installed", "msi", None, 1])
    def test_unexpected_stamp_falls_to_portable(self, monkeypatch, bad):
        """The unsafe flavour (the one that starts Velopack) is never the fall-back."""
        _stamp(monkeypatch, bad)
        assert bf.build_flavor() == "portable"


class TestRunVelopackStartup:
    def test_portable_build_never_imports_velopack(self, monkeypatch, tmp_path):
        _unstamped(monkeypatch)
        monkeypatch.setattr(sys, "executable", _install_layout(tmp_path))  # even under an install
        imported: list[str] = []
        monkeypatch.setattr(startup.importlib, "import_module", lambda name: imported.append(name))
        assert startup.run_velopack_startup() is False
        assert imported == []

    def test_installed_build_outside_an_install_is_skipped(self, monkeypatch, tmp_path):
        _stamp(monkeypatch, "installed")
        monkeypatch.setattr(sys, "executable", _install_layout(tmp_path, with_update_exe=False))
        imported: list[str] = []
        monkeypatch.setattr(startup.importlib, "import_module", lambda name: imported.append(name))
        assert startup.run_velopack_startup() is False
        assert imported == []

    def test_installed_build_under_an_install_calls_the_packages_startup_hook(self, monkeypatch, tmp_path):
        """Positive twin of the two skips above. The hook (the package's ``run_startup_hook``) is what switches the
        installer's own auto-apply OFF before running; that behaviour is the package's, proven by the e2e script."""
        _stamp(monkeypatch, "installed")
        monkeypatch.setattr(sys, "executable", _install_layout(tmp_path))
        calls: list[str] = []
        hook_module = types.ModuleType("myb_desktop_updater.velopack_installer")
        hook_module.run_startup_hook = lambda: calls.append("hook")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "myb_desktop_updater.velopack_installer", hook_module)
        assert startup.run_velopack_startup() is True
        assert calls == ["hook"]

    def test_missing_runtime_is_logged_loudly_and_does_not_stop_the_run(self, monkeypatch, tmp_path, caplog):
        _stamp(monkeypatch, "installed")
        monkeypatch.setattr(sys, "executable", _install_layout(tmp_path))

        def _boom(name: str):
            raise ImportError(name)

        monkeypatch.setattr(startup.importlib, "import_module", _boom)
        with caplog.at_level(logging.ERROR, logger=startup.logger.name):
            assert startup.run_velopack_startup() is False
        assert any(r.levelno == logging.ERROR and "updater" in r.getMessage() for r in caplog.records)

    def test_install_root(self, tmp_path):
        exe = _install_layout(tmp_path)
        assert startup.install_root(exe) == tmp_path / "myBlueprint.DistrictSync"
        assert startup.install_root(_install_layout(tmp_path / "other", with_update_exe=False)) is None

    def test_module_has_no_literal_velopack_import(self):
        """PyInstaller follows a literal import into the portable build; a name in a variable it cannot."""
        text = (_ROOT / "src" / "update" / "startup.py").read_text(encoding="utf8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", '"')))
        assert not re.search(r"^\s*(import|from)\s+velopack\b", code, re.M)
        assert not re.search(r"import_module\(\s*['\"]velopack", code)


class TestDispatchOrder:
    @pytest.fixture
    def spy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
        calls: list[int] = []
        monkeypatch.setattr(startup, "run_velopack_startup", lambda: calls.append(1) or False)
        return calls

    def test_elevated_apply_never_starts_velopack(self, spy, monkeypatch):
        import src.scheduler.elevated_apply as ea

        monkeypatch.setattr(ea, "run_elevated_apply", lambda _a: 0)
        assert main_module.cli(["--elevated-apply", "x", "y"]) == 0
        assert spy == []

    def test_schedule_check_never_starts_velopack(self, spy, monkeypatch):
        import src.scheduler.schedule_check as sc

        monkeypatch.setattr(sc, "run_check_mode", lambda _a: 0)
        assert main_module.cli(["--schedule-check", "tok"]) == 0
        assert spy == []

    def test_diagnose_never_starts_velopack(self, spy):
        assert main_module.cli(["--diagnose"]) == 0
        assert spy == []

    def test_an_ordinary_start_does_start_it_once(self, spy):
        """Positive twin: the spy is wired to the real call site."""
        assert main_module.cli(["--version"]) == 0
        assert spy == [1]

    def test_call_site_is_after_all_three_dispatch_modes(self):
        src = (_ROOT / "src" / "main.py").read_text(encoding="utf8")
        call = src.index("run_velopack_startup()")
        for marker in ('"--elevated-apply"', '"--schedule-check"', '"--diagnose"'):
            assert src.index(f"args_list[0] == {marker}") < call
        assert call < src.index("if not args_list:")


def test_portable_run_never_imports_velopack_in_a_fresh_process(tmp_path):
    """The sys.modules assert, in a process nothing else has touched."""
    code = "import sys; from src.main import cli; rc = cli(['--version']); print(rc, 'velopack' in sys.modules)"
    env = {**os.environ, "DISTRICTSYNC_DATA_DIR": str(tmp_path / "profile"), "PYTHONPATH": str(_ROOT)}
    out = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.split()[-2:] == ["0", "False"], out.stderr


def test_requirements_pin_matches_the_shared_pin():
    text = (_ROOT / "requirements.txt").read_text(encoding="utf8")
    m = re.search(r"^velopack==(\S+)\s*;\s*sys_platform\s*==\s*\"win32\"", text, re.M)
    assert m, "requirements.txt must pin velopack exactly, Windows only"
    assert m.group(1) == VELOPACK_VERSION


class TestPackArgs:
    @pytest.fixture(scope="class")
    def args(self):
        spec = importlib.util.spec_from_file_location("flet_pack_args_flavour", _ROOT / "scripts" / "flet_pack_args.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_installed_flags_add_the_updater_modules(self, args):
        flags = args.as_flags("windows", installed=True)
        for m in ("velopack", "src.update.startup", "src._build_flavor"):
            assert f"--hidden-import {m}" in flags
        assert "--hidden-import velopack" not in args.as_flags("windows")

    def test_installed_modules_are_outside_the_one_file_parity_set(self, args):
        assert not set(args.INSTALLED_EXTRA) & args.all_modules()

    def test_installed_flavour_is_windows_only(self, args):
        with pytest.raises(ValueError):
            args.as_flags("linux", installed=True)


class TestWorkflowShape:
    def _text(self, name: str) -> str:
        return (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf8")

    def test_only_the_onedir_job_stamps_the_flavour(self):
        text = self._text("flet-pack.yml")
        head, job = text.split("  pack-windows-onedir:", 1)
        assert "_build_flavor" not in head  # one-file/macOS/Linux jobs
        assert "echo \"flavor = 'installed'\" > src/_build_flavor.py" in job
        assert "flet_pack_args.py windows --installed" in job

    def test_release_workflow_never_mentions_the_flavour(self):
        assert "_build_flavor" not in self._text("release.yml")


def test_diagnose_reports_the_flavour(monkeypatch):
    from src.utils.diagnostics import diagnose_lines

    _stamp(monkeypatch, "installed")
    assert any("build flavour" in line and "installed" in line for line in diagnose_lines())
