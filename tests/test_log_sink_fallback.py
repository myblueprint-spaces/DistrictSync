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
* (the relaunch itself — ``os.execv`` split ``C:\\Program Files``, the route the lab's traceback took —
  is DSYNC-114's ``ui_flet/relaunch.py``, pinned in ``tests/test_ui_flet_relaunch.py``).

Never the real profile or ``C:\\ProgramData``: the machine root is redirected into tmp and the
handshake folder is a separate tmp folder, so a fall-through into the per-user PROFILE is visible.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
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

    def test_the_fallback_writes_only_the_handshake_log_and_no_profile_state(
        self, machine, handshake, isolated_loggers, monkeypatch
    ):
        """In production ``handshake_dir()`` IS the per-user profile root (``paths.handshake_dir``), the
        declared refusal-log exception. What must never appear there is profile STATE: the fallback
        writes the log and nothing else (review F5)."""
        monkeypatch.setattr(
            logger_mod.logging.handlers, "RotatingFileHandler", _deny(machine / paths_module.MACHINE_RUNS_SUBDIR)
        )
        logger_mod.get_logger("dsync117")
        _flush_all()
        assert sorted(p.name for p in handshake.iterdir()) == ["etl_tool.log"]
        for state in ("config.json", "mappings", "history.db"):
            assert not (handshake / state).exists(), state


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
# Review fixes (F1-F4).                                                        #
# --------------------------------------------------------------------------- #


def _open_raising(runs: Path, exc: OSError, calls: list[str]):
    """``os.open`` that raises ``exc`` for a file in ``runs`` and counts those calls."""
    real = paths_module.os.open

    def _open(path, flags, mode=0o777, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors os.open
        if Path(path).parent == runs:
            calls.append(str(path))
            if len(calls) > 50:  # the hang the review measured: stop it here rather than for hours
                raise AssertionError("the probe is retrying")
            raise exc
        return real(path, flags, mode, *args, **kwargs)

    return _open


class TestTheProbeNeverRetries:
    def test_a_denied_create_is_refused_after_one_attempt(self, machine, handshake, monkeypatch):
        """F1: ``tempfile.mkstemp`` retries a PermissionError while ``os.access`` says writable (it never
        reads the DACL) up to TMP_MAX times — the window launch hung. One exclusive create, no retry."""
        runs = machine / paths_module.MACHINE_RUNS_SUBDIR
        calls: list[str] = []
        monkeypatch.setattr(
            paths_module.os, "open", _open_raising(runs, PermissionError(13, "Permission denied"), calls)
        )
        with pytest.raises(paths_module.MachineScopeRefused) as exc:
            paths_module.assert_runs_writable()
        assert exc.value.reason is paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE
        assert len(calls) == 1

    def test_the_probe_is_an_exclusive_create(self, machine, handshake, monkeypatch):
        runs = machine / paths_module.MACHINE_RUNS_SUBDIR
        seen: list[int] = []
        real = paths_module.os.open

        def _open(path, flags, mode=0o777, *args, **kwargs):  # noqa: ANN001, ANN202 - mirrors os.open
            if Path(path).parent == runs:
                seen.append(flags)
            return real(path, flags, mode, *args, **kwargs)

        monkeypatch.setattr(paths_module.os, "open", _open)
        paths_module.assert_runs_writable()
        assert len(seen) == 1 and seen[0] & paths_module.os.O_CREAT and seen[0] & paths_module.os.O_EXCL
        assert list(runs.iterdir()) == []


class TestOnlyAccessDenialIsGrantable:
    def test_winerror_5_is_runs_unwritable(self, machine, handshake, monkeypatch):
        denied = PermissionError(13, "Access is denied")
        denied.winerror = 5  # type: ignore[attr-defined]
        monkeypatch.setattr(
            paths_module.os, "open", _open_raising(machine / paths_module.MACHINE_RUNS_SUBDIR, denied, [])
        )
        with pytest.raises(paths_module.MachineScopeRefused) as exc:
            paths_module.assert_runs_writable()
        assert exc.value.reason is paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE

    @pytest.mark.parametrize(
        ("error", "errno_"),
        [
            pytest.param(OSError(28, "No space left on device"), 28, id="disk full"),
            pytest.param(OSError(22, "Invalid argument"), 22, id="other oserror"),
        ],
    )
    def test_any_other_failure_carries_on_and_logs_type_and_errno(
        self, machine, handshake, monkeypatch, caplog, error, errno_
    ):
        """F2: a grant cannot fix a full disk or an antivirus block; the logger fallback covers the log."""
        monkeypatch.setattr(
            paths_module.os, "open", _open_raising(machine / paths_module.MACHINE_RUNS_SUBDIR, error, [])
        )
        with caplog.at_level(logging.WARNING, logger=paths_module.logger.name):
            paths_module.assert_runs_writable()  # no refusal
        line = "\n".join(r.getMessage() for r in caplog.records)
        assert type(error).__name__ in line and str(errno_) in line
        assert str(machine) not in line and "ProgramData" not in line

    def test_a_sharing_violation_carries_on(self, machine, handshake, monkeypatch):
        busy = PermissionError(13, "The process cannot access the file")
        busy.winerror = 32  # type: ignore[attr-defined]
        monkeypatch.setattr(
            paths_module.os, "open", _open_raising(machine / paths_module.MACHINE_RUNS_SUBDIR, busy, [])
        )
        paths_module.assert_runs_writable()  # no refusal


class TestTheRunsRefusalDialog:
    def test_it_points_at_the_handshake_log_and_says_the_nightly_is_unaffected(self, machine, handshake):
        """F3: the record went to the handshake log, and the nightly's principal holds M on runs."""
        from src.ui_flet import launcher

        paths_module.pin_data_dir()
        runs = machine / paths_module.MACHINE_RUNS_SUBDIR
        text = launcher.format_user_error(
            paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.RUNS_UNWRITABLE, runs)
        )
        assert str(handshake / "etl_tool.log") in text
        assert "etl_tool-interactive" not in text, "the runs log was never written"
        assert "nightly sync is not affected" in text
        assert "affected too" not in text

    def test_positive_twin_other_refusals_still_say_the_nightly_is_affected(self, machine, handshake):
        from src.ui_flet import launcher

        text = launcher.format_user_error(
            paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.FOREIGN_OWNER, machine)
        )
        assert "affected too" in text


class TestTheTracebackRecordNeverRaises:
    def test_a_failing_redact_import_writes_no_unredacted_text_and_never_raises(self, machine, handshake, monkeypatch):
        """F4: the redaction import and formatting sit inside the net; without redaction the body is
        skipped (a traceback can carry an account name) and only the exception TYPE is recorded."""
        from src.ui_flet import launcher

        monkeypatch.setattr(launcher, "resolve_log_path", lambda: handshake / "etl_tool.log")
        monkeypatch.setitem(sys.modules, "src.utils.redact", None)  # any import of it now raises ImportError
        launcher._write_traceback(RuntimeError("C:\\Users\\Zebediah\\secret detail"))
        monkeypatch.undo()
        text = (handshake / "etl_tool.log").read_text(encoding="utf-8")
        assert "failed to launch" in text and "RuntimeError" in text
        assert "Zebediah" not in text and "secret detail" not in text
