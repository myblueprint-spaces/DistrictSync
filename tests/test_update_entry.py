"""DSYNC-66 (plan 0054 S5 follow-up): DistrictSync's wiring of the shared start guard (stand-in guard).

The real guard and package are in ``tests/test_update_entry_real.py``; the real crash-and-recover run is
``scripts/updater_e2e.py`` (a crash ends the process with ``os._exit``, which cannot be driven inside pytest).
"""

from __future__ import annotations

import re
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.update import entry
from tests.test_installed_flavour import _stamp, _unstamped

_ROOT = Path(__file__).resolve().parent.parent


class FakeGuard(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("myb_start_guard")
        self.events: list[tuple] = []
        self.run_kwargs: dict = {}
        outer = self

        self.build_fingerprint = lambda: "fp-1"
        self.start_child_job = lambda: outer.events.append(("job",)) or True
        self.adopt_children = lambda: outer.events.append(("adopt",)) or 1
        self.confirm_start = lambda state_dir: outer.events.append(("confirm", Path(state_dir)))

        def run_guarded(main, **kwargs):
            outer.events.append(("run",))
            outer.run_kwargs = kwargs
            return main()

        self.run_guarded = run_guarded


@pytest.fixture(autouse=True)
def _fresh():
    entry.forget_guard()
    yield
    entry.forget_guard()


@pytest.fixture
def guarded(eligible, monkeypatch):
    """An installed build under a Velopack install with the stand-in guard importable."""
    fake = FakeGuard()
    monkeypatch.setitem(sys.modules, "myb_start_guard", fake)
    monkeypatch.setattr(entry, "guard_state_dir", lambda: eligible.state)
    return fake


class TestWhichStartsCount:
    @pytest.mark.parametrize("args", [[], ["--sis", "myedbc", "--input", "x", "--output", "y"]])
    def test_the_window_and_a_conversion_count(self, args):
        assert entry.start_counts(args) is True

    @pytest.mark.parametrize(
        "args",
        [
            ["--version"],
            ["--diagnose"],
            ["--schedule-check", "tok"],
            ["--elevated-apply", "req", "res"],
            ["--sftp-test"],
            ["--sftp-configure"],
            ["--help"],
            ["--veloapp-install", "3.30.0"],
            ["--veloapp-updated", "3.30.0"],
            ["--veloapp-obsolete", "3.29.0"],
            ["--sis", "myedbc", "--veloapp-install", "3.30.0"],  # a hook never counts, whatever else is on the line
        ],
    )
    def test_everything_else_opens_no_window_or_is_a_hook_and_must_not_count(self, args):
        assert entry.start_counts(args) is False

    def test_only_no_arguments_is_the_window(self):
        assert entry.is_window_start([]) and not entry.is_window_start(["--sis", "x"])


class TestWhereItApplies:
    def test_portable_build_returns_none_and_imports_nothing(self, monkeypatch):
        _unstamped(monkeypatch)
        monkeypatch.setitem(sys.modules, "myb_start_guard", None)  # importing it would raise
        assert entry.run_entry([]) is None

    def test_installed_build_not_under_an_install_returns_none(self, eligible, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.executable", str(tmp_path / "elsewhere" / "current" / "DistrictSync.exe"))
        assert entry.run_entry([]) is None

    def test_machine_scope_returns_none(self, eligible, monkeypatch):
        monkeypatch.setattr("src.utils.paths.is_machine_scope", lambda: True)
        assert entry.run_entry([]) is None

    def test_a_refused_machine_profile_returns_none(self, eligible, monkeypatch):
        from src.utils import paths

        def refuse():
            raise paths.MachineScopeRefused(paths.MachineScopeRefusedReason.MISSING, Path("x"))

        monkeypatch.setattr(paths, "is_machine_scope", refuse)
        assert entry.run_entry([]) is None

    def test_the_data_dir_override_returns_none_except_in_the_e2e_build(self, eligible, monkeypatch):
        monkeypatch.setattr("src.utils.paths.data_dir_override_active", lambda: True)
        assert entry.run_entry([]) is None
        _stamp(monkeypatch, "e2e")
        assert entry._applicable() is True

    def test_a_guard_that_cannot_be_imported_never_stops_the_app(self, eligible, monkeypatch):
        monkeypatch.setitem(sys.modules, "myb_start_guard", None)
        assert entry.run_entry([]) is None


class TestRunEntry:
    def test_a_conversion_runs_under_the_guard_counted_with_the_cli_code(self, guarded, eligible, monkeypatch):
        monkeypatch.setattr("src.main.cli", lambda argv: 1)  # a failed nightly's proper exit code
        code = entry.run_entry(["--sis", "myedbc", "--input", "x", "--output", "y", "--source", "scheduled"])
        assert code == 1
        assert guarded.run_kwargs["counted"] is True
        assert guarded.run_kwargs["state_dir"] == eligible.state
        assert guarded.run_kwargs["build"] == "fp-1"
        assert ("job",) not in guarded.events  # only the window gets a child job

    def test_the_cli_receives_exactly_the_arguments(self, guarded, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr("src.main.cli", lambda argv: seen.append(list(argv)) or 0)
        entry.run_entry(["--version"])
        assert seen == [["--version"]]

    def test_a_non_window_command_runs_in_the_guard_uncounted(self, guarded, monkeypatch):
        monkeypatch.setattr("src.main.cli", lambda argv: 0)
        assert entry.run_entry(["--diagnose"]) == 0
        assert guarded.run_kwargs["counted"] is False

    def test_a_velopack_hook_is_uncounted(self, guarded, monkeypatch):
        monkeypatch.setattr("src.main.cli", lambda argv: 0)
        entry.run_entry(["--veloapp-updated", "3.31.0"])
        assert guarded.run_kwargs["counted"] is False

    def test_the_window_starts_the_child_job_before_the_guarded_run(self, guarded, monkeypatch):
        monkeypatch.setattr("src.main.cli", lambda argv: 0)
        entry.run_entry([])
        assert [e[0] for e in guarded.events][:2] == ["job", "run"]
        assert guarded.run_kwargs["counted"] is True

    def test_the_guard_is_held_for_confirming_and_adopting(self, guarded, eligible, monkeypatch):
        monkeypatch.setattr("src.main.cli", lambda argv: 0)
        entry.run_entry([])
        assert entry.confirm_start() is True and ("confirm", eligible.state) in guarded.events
        assert entry.adopt_window_children() == 1

    def test_confirming_and_adopting_are_no_ops_without_a_guard(self):
        assert entry.confirm_start() is False and entry.adopt_window_children() == 0


class TestRecovery:
    """The package does the whole recovery (restore AND record the version bad); this module only calls it."""

    @pytest.fixture
    def world(self, eligible, monkeypatch):
        calls: list[tuple] = []
        updater = types.ModuleType("myb_desktop_updater")
        updater.AppIdentity = lambda *a: SimpleNamespace(name=a[0])
        updater.HandoffRefused = type(
            "HandoffRefused", (), {"__init__": lambda s, reason: setattr(s, "reason", reason)}
        )
        recovery = types.ModuleType("myb_desktop_updater.recovery")
        outcome = {"refuse": False}

        def recover_broken_start(identity, state_dir, exe_names, **_k):
            calls.append((identity.name, Path(state_dir), list(exe_names)))
            if outcome["refuse"]:
                return updater.HandoffRefused("app_running")
            return SimpleNamespace(version="3.29.0", restore=True)

        recovery.recover_broken_start = recover_broken_start
        monkeypatch.setitem(sys.modules, "myb_desktop_updater", updater)
        monkeypatch.setitem(sys.modules, "myb_desktop_updater.recovery", recovery)
        return SimpleNamespace(calls=calls, state=eligible.state, outcome=outcome)

    def test_the_kept_copy_is_restored_and_the_start_stops(self, world):
        assert entry._restore_kept_copy(world.state) == 1
        assert world.calls == [("DistrictSync", world.state, [Path(sys.executable).name])]

    def test_a_refused_restore_lets_the_app_try_again(self, world):
        world.outcome["refuse"] = True
        assert entry._restore_kept_copy(world.state) is None

    def test_this_module_keeps_no_crash_count_of_its_own(self):
        """DSYNC-66: the package's guard is the only counter; no begin_start loop, no record-bad workaround here."""
        text = Path(entry.__file__).read_text(encoding="utf8")
        assert ".begin_start(" not in text and "HealthStore(" not in text


class TestSourceOrder:
    def test_the_guard_is_the_first_thing_main_does_before_any_heavy_import(self):
        text = (_ROOT / "src" / "main.py").read_text(encoding="utf8")
        guard = text.index("run_entry")
        for heavy in ("from src.config.app_config", "from src.etl.pipeline", "from src.sftp.uploader", "import pandas"):
            if heavy in text:
                assert guard < text.index(heavy), heavy
        assert re.search(r'if __name__ == "__main__":\s+from src\.update\.entry import run_entry', text)

    def test_the_entry_module_imports_nothing_heavy(self):
        """It must be importable before pandas/yaml/flet: stdlib plus two light DistrictSync modules."""
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import src.update.entry; print(sorted(m for m in ('pandas','yaml','flet','pydantic','paramiko') if m in sys.modules))",
            ],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        assert out.stdout.strip() == "[]"

    def test_a_portable_run_as_a_script_is_unchanged(self, tmp_path):
        """``python src/main.py`` (the frozen shape: run as ``__main__``) still works and the guard stays out of it."""
        import os

        env = {**os.environ, "DISTRICTSYNC_DATA_DIR": str(tmp_path / "profile")}
        out = subprocess.run(
            [sys.executable, str(_ROOT / "src" / "main.py"), "--version"],
            cwd=_ROOT,
            env={**env, "PYTHONPATH": str(_ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert out.returncode == 0 and out.stdout.strip(), out.stderr
