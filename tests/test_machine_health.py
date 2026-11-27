"""Plan 0055 U3 (DSYNC-99): machine-wide health decided by SYSTEM through the shared package's supervised probe.

* ``--update-health-probe`` imports what the nightly and the window need and exits 0, touching no profile;
* ``service.supervised_check`` probes ``exe --update-health-probe`` with the package's ``supervised_health`` and keeps its
  state only in the TRUSTED updater folder;
* the pin is the reviewed 0.5.0 (``myb_desktop_updater-v0.5.0``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.update import probe, service

ROOT = Path(__file__).resolve().parent.parent


class TestTheProbe:
    def test_it_imports_the_app_and_exits_0(self, capsys):
        assert probe.run_probe() == 0
        assert capsys.readouterr().out.strip() == "ok"

    def test_a_missing_module_fails_it(self, monkeypatch):
        monkeypatch.setattr(probe, "PROBE_MODULES", ("src.etl.pipeline", "no_such_module_for_the_probe"))
        with pytest.raises(ModuleNotFoundError):
            probe.run_probe()

    def test_it_covers_the_nightly_and_the_window(self):
        assert {"src.etl.pipeline", "src.sftp.uploader", "flet", "src.ui_flet.shell"} <= set(probe.PROBE_MODULES)

    def test_the_dispatch_comes_before_the_start_guard(self):
        source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
        assert source.index('["--update-health-probe"]') < source.index("from src.update.entry import run_entry")

    def test_run_as_the_real_entry_it_touches_no_profile(self, tmp_path):
        """The SYSTEM updater runs it with SYSTEM's environment: nothing may appear under any profile folder."""
        env = dict(os.environ)
        for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME"):
            env[name] = str(tmp_path / name)
            (tmp_path / name).mkdir()
        env.pop("DISTRICTSYNC_DATA_DIR", None)
        env["PYTHONPATH"] = str(ROOT)
        done = subprocess.run(
            [sys.executable, "-m", "src.main", "--update-health-probe"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        assert done.stdout.strip() == "ok"
        assert [p for p in tmp_path.rglob("*") if p.is_file()] == []  # no profile, log, pin or config written


class FakePackage:
    """The three names ``supervised_check`` uses, scripted."""

    def __init__(self, action: str) -> None:
        self.action = action
        self.probed: list[list[str]] = []
        self.stores: list[Path] = []

    def HealthStore(self, path):  # noqa: N802 - the package's own name
        self.stores.append(path)
        return object()

    def run_health_probe(self, argv, *, timeout_seconds):
        self.probed.append(list(argv))
        return object()

    def supervised_health(self, store, version, previous, probe_fn):
        probe_fn()

        class Action:
            value = self.action

        return Action()


class TestTheSupervisedCheck:
    @pytest.fixture()
    def trusted(self, monkeypatch, tmp_path):
        folder = tmp_path / "DistrictSync-Updater"
        folder.mkdir()
        monkeypatch.setattr(service, "state_dir", lambda: folder)
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: True)
        return folder

    def test_it_probes_the_new_build_and_keeps_state_in_the_trusted_folder(self, trusted):
        package = FakePackage("healthy")
        exe = Path("C:/Program Files/myBlueprint.DistrictSync/current/DistrictSync.exe")
        assert service.supervised_check("3.4.0", "3.3.0", exe, module=package) == "healthy"
        assert package.probed == [[str(exe), "--update-health-probe"]]
        assert package.stores == [trusted / service.HEALTH_FILE]

    def test_an_untrusted_folder_records_nothing(self, trusted, monkeypatch):
        monkeypatch.setattr(service, "state_dir_trusted", lambda path: False)
        package = FakePackage("healthy")
        assert service.supervised_check("3.4.0", "3.3.0", Path("x.exe"), module=package) == service.STATE_UNTRUSTED
        assert package.probed == [] and package.stores == []

    def test_a_surprise_never_raises(self, trusted):
        class Broken:
            def HealthStore(self, path):  # noqa: N802
                raise RuntimeError("boom")

        assert service.supervised_check("3.4.0", "3.3.0", Path("x.exe"), module=Broken()) == "error"

    def test_with_the_real_package(self, trusted, tmp_path):
        """The pinned 0.5.0's real API, with a probe command that is this interpreter exiting 0."""
        import myb_desktop_updater as real

        exe = Path(sys.executable)
        assert hasattr(real, "supervised_health") and hasattr(real, "run_health_probe")
        # the real probe would run "python --update-health-probe" (an error): it counts as a failed start
        assert service.supervised_check("3.4.0", "3.3.0", exe) in ("retry", "recover")


def test_the_pin_is_the_reviewed_0_5_0():
    import importlib.util

    spec = importlib.util.spec_from_file_location("updater_package", ROOT / "scripts" / "updater_package.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PINNED_VERSION == "0.5.0"
    assert module.PINNED_COMMIT == "32725226927e985255b08a4aa8ae5f1d21435341"
