"""DSYNC-117: a log file that cannot be opened never kills DistrictSync.

Lab repro (MSI 0.57.1): after a root-only grant, the second administrator's relaunched app could not
create ``runs\\etl_tool-interactive-<tag>.log`` and PyInstaller showed "Unhandled exception in script"
with a full traceback (``get_logger`` -> ``_build_file_handler`` -> ``[Errno 13]``).

Pinned here:

* the sink falls back to a log in the per-user HANDSHAKE folder (the declared per-user exception), or
  to stderr when there is none, and the app carries on — both for the CLI and for the window boot;
* a positive twin: a normal open still writes ``runs\\``;
* the window boot routes a session that cannot write ``runs\\`` to the grant window (a typed refusal),
  never to a traceback;
* the fallback line names no account and no path;
* the relaunch after a successful grant quotes its own path (``os.execv`` on Windows does not, so a
  ``C:\\Program Files`` install re-opened as the CLI with a split path — the route the lab's traceback
  took).

Never the real profile or ``C:\\ProgramData``: the machine root is redirected into tmp and the
handshake folder is a separate tmp folder, so a fall-through into the per-user PROFILE is visible.
"""

from __future__ import annotations

import logging
import logging.handlers
import subprocess
from pathlib import Path

import pytest

from src.utils import logger as logger_mod
from src.utils import paths as paths_module

_ACCOUNT = "CORP\\Zebediah"
# Captured at import: several tests replace the stdlib class with a denying factory.
_ROTATING = logging.handlers.RotatingFileHandler


@pytest.fixture
def isolated_loggers():
    root = logging.getLogger()
    etl = logging.getLogger("etl")
    before = (root.handlers[:], root.level, etl.handlers[:], etl.level, etl.propagate)
    try:
        yield
    finally:
        for lg in (root, etl):
            for h in lg.handlers:
                if h not in before[0] + before[2]:
                    h.close()
        root.handlers, root.level = before[0], before[1]
        etl.handlers, etl.level, etl.propagate = before[2], before[3], before[4]


def _flush_all() -> None:
    for lg in (logging.getLogger(), logging.getLogger("etl")):
        for h in lg.handlers:
            h.flush()


def _file_handlers() -> list[logging.handlers.RotatingFileHandler]:
    found = [h for h in logging.getLogger().handlers if isinstance(h, _ROTATING)]
    found += [h for h in logging.getLogger("etl").handlers if isinstance(h, _ROTATING)]
    return found


@pytest.fixture
def handshake(tmp_path, monkeypatch):
    """The per-user handshake folder — DISTINCT from the profile, so a fall-through is visible."""
    folder = tmp_path / "handshake"
    monkeypatch.setattr(paths_module, "handshake_dir", lambda: folder)
    # The launcher binds the name at import; patch its copy too, or test order decides the answer.
    from src.ui_flet import launcher

    monkeypatch.setattr(launcher, "handshake_dir", lambda: folder)
    return folder


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine-scoped pin into tmp: trusted root with ``runs``, this account's tag fixed."""
    from src.utils import account_tag

    root = tmp_path / "ProgramData" / "DistrictSync"
    (root / paths_module.MACHINE_RUNS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: root)
    monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: True)
    monkeypatch.setattr(paths_module, "_assert_machine_dir_trusted", lambda path: None)
    monkeypatch.setattr(account_tag, "process_account", lambda: _ACCOUNT)
    monkeypatch.setattr(account_tag, "tag_key", lambda **kw: bytes(32))
    monkeypatch.setattr(account_tag, "process_role", lambda: "interactive")
    paths_module.reset_data_dir_pin()
    yield root
    paths_module.reset_data_dir_pin()


def _deny(denied: Path):
    """A ``RotatingFileHandler`` that raises ``PermissionError`` for one folder, as Windows did."""
    real = _ROTATING

    def _handler(path, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors the stdlib signature
        if Path(path).parent == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)

    return _handler


# --------------------------------------------------------------------------- #
# The sink itself.                                                             #
# --------------------------------------------------------------------------- #


class TestTheSinkFallsBack:
    def test_a_permission_denied_open_falls_back_to_the_handshake_folder(
        self, machine, handshake, isolated_loggers, monkeypatch
    ):
        runs = machine / paths_module.MACHINE_RUNS_SUBDIR
        monkeypatch.setattr(logger_mod.logging.handlers, "RotatingFileHandler", _deny(runs))

        log = logger_mod.get_logger("dsync117")  # must not raise
        log.info("still-running-117")
        _flush_all()

        fallback = handshake / "etl_tool.log"
        assert fallback.is_file()
        assert "still-running-117" in fallback.read_text(encoding="utf-8")
        assert list(runs.iterdir()) == []

    def test_the_fallback_says_so_without_a_path_or_an_account(self, machine, handshake, isolated_loggers, monkeypatch):
        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        logger_mod.get_logger("dsync117")
        _flush_all()
        text = (handshake / "etl_tool.log").read_text(encoding="utf-8")
        assert "could not be opened" in text
        assert "Zebediah" not in text and "zebediah" not in text.lower()
        assert str(machine) not in text and "ProgramData" not in text

    def test_no_handshake_folder_falls_back_to_stderr_and_still_continues(
        self, machine, tmp_path, isolated_loggers, monkeypatch, capsys
    ):
        """``handshake_dir()`` itself unusable: the line goes to stderr, nothing is written anywhere."""

        def _no_handshake() -> Path:
            raise OSError("no per-user folder")

        monkeypatch.setattr(paths_module, "handshake_dir", _no_handshake)
        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )

        log = logger_mod.get_logger("dsync117")
        log.warning("to-stderr-117")
        assert "to-stderr-117" in capsys.readouterr().err
        assert _file_handlers() == []

    def test_the_fallback_never_writes_the_per_user_profile(
        self, machine, handshake, isolated_loggers, monkeypatch, isolated_user_profile
    ):
        """A MachineScopeRefused never falls through to the per-user profile; nor does a denied log."""
        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        logger_mod.get_logger("dsync117")
        _flush_all()
        assert not (isolated_user_profile / "etl_tool.log").exists()


class TestPositiveTwin:
    def test_a_normal_open_still_writes_runs(self, machine, handshake, isolated_loggers):
        log = logger_mod.get_logger("dsync117")
        log.info("in-runs-117")
        _flush_all()
        written = list((machine / paths_module.MACHINE_RUNS_SUBDIR).glob("etl_tool-interactive-*.log"))
        assert len(written) == 1
        assert "in-runs-117" in written[0].read_text(encoding="utf-8")
        assert not (handshake / "etl_tool.log").exists(), "no fallback when the real sink opened"


class TestTheCliContinues:
    def test_cli_logging_survives_a_denied_log(self, machine, handshake, isolated_loggers, monkeypatch):
        from src import main as main_mod

        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        logger = main_mod._configure_cli_logging()  # the lab's crash site
        assert isinstance(logger, logging.Logger)

    def test_a_cli_command_runs_to_its_own_exit_code(self, machine, handshake, isolated_loggers, monkeypatch, capsys):
        """End to end through ``cli``: the lab's traceback came from here (``_cli`` -> logging)."""
        from src import main as main_mod

        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        monkeypatch.setattr("src.update.startup.run_velopack_startup", lambda: None)
        monkeypatch.setattr("src.scheduler.machine_setup.needs_machine_setup", lambda: False)
        assert main_mod.cli(["--no-such-flag-117"]) == 2, "argparse's usage error, not a PermissionError"


# --------------------------------------------------------------------------- #
# The window boot.                                                             #
# --------------------------------------------------------------------------- #


class TestTheWindowBoot:
    def test_a_session_that_cannot_write_runs_is_offered_the_grant(
        self, machine, handshake, isolated_loggers, monkeypatch
    ):
        from src.ui_flet import launcher

        def _denied(folder: Path) -> None:
            raise PermissionError(13, "Permission denied", str(folder))

        monkeypatch.setattr(paths_module, "_probe_write", _denied)
        with pytest.raises(paths_module.MachineScopeRefused) as exc:
            launcher.boot_profile()
        assert exc.value.reason is paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE
        assert launcher.offers_grant_window(exc.value) is True

    def test_positive_twin_a_writable_runs_boots(self, machine, handshake, isolated_loggers):
        from src.ui_flet import launcher

        launcher.boot_profile()  # no refusal
        assert list((machine / paths_module.MACHINE_RUNS_SUBDIR).glob(".write-probe*")) == [], "probe cleaned up"
        assert list((machine / paths_module.MACHINE_RUNS_SUBDIR).glob("etl_tool-interactive-*.log"))

    def test_the_boot_logging_itself_never_raises_on_a_denied_log(
        self, machine, handshake, isolated_loggers, monkeypatch
    ):
        """No traceback dialog path: ``boot_logging`` is inside the launcher's net and does not throw."""
        from src.ui_flet import launcher

        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        launcher.boot_logging()
        _flush_all()
        assert (handshake / "etl_tool.log").is_file()

    def test_per_user_scope_never_probes(self, monkeypatch, isolated_loggers):
        from src.ui_flet import launcher

        def _boom(folder: Path) -> None:
            raise AssertionError("a per-user install has no runs folder to probe")

        monkeypatch.setattr(paths_module, "_probe_write", _boom)
        launcher.boot_profile()

    def test_the_refusal_has_its_own_plain_language_cause(self):
        from src.ui_flet import launcher

        cause = launcher._MACHINE_SCOPE_CAUSES[paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE]
        assert "Traceback" not in cause and "Errno" not in cause

    def test_the_traceback_of_a_runs_refusal_lands_in_the_handshake_folder(self, machine, handshake, monkeypatch):
        """``_write_traceback`` cannot open ``runs`` either; it must not lose the record."""
        from src.ui_flet import launcher

        real_open = open

        def _open(file, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors builtins.open
            if Path(file).parent == machine / paths_module.MACHINE_RUNS_SUBDIR:
                raise PermissionError(13, "Permission denied", str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _open)
        launcher._write_traceback(
            paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE, machine)
        )
        monkeypatch.undo()
        assert "failed to launch" in (handshake / "etl_tool.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The relaunch after a grant.                                                  #
# --------------------------------------------------------------------------- #


class TestTheRelaunchQuotesItsPath:
    def test_relaunch_spawns_with_an_argv_list_and_exits(self, monkeypatch):
        from src.ui_flet import grant_window

        spawned: list[list[str]] = []
        exited: list[int] = []
        monkeypatch.setattr(grant_window.subprocess, "Popen", lambda argv, **kw: spawned.append(list(argv)))
        monkeypatch.setattr(grant_window.os, "_exit", lambda code: exited.append(code))
        monkeypatch.setattr(
            grant_window.grant_access, "reexec_argv", lambda: ["C:\\Program Files\\DistrictSync\\DistrictSync.exe"]
        )

        grant_window.relaunch()

        assert spawned == [["C:\\Program Files\\DistrictSync\\DistrictSync.exe"]]
        assert exited == [0]
        assert subprocess.list2cmdline(spawned[0]).startswith('"C:\\Program Files'), "the path survives as ONE arg"

    def test_neither_window_uses_os_execv(self):
        import inspect

        from src.ui_flet import grant_window, machine_setup_window

        for module in (grant_window, machine_setup_window):
            assert "os.execv(" not in inspect.getsource(module), module.__name__
