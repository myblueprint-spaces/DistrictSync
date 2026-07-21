"""Real tests for the DistrictSync CLI entry point (``src.main.cli``).

**Why this file exists.** The whole CLI — argument parsing, SFTP subcommand
routing, and the exit-code decisions — used to live inline inside
``if __name__ == "__main__":`` in ``src/main.py``. Nothing could import it, so
the "exit-code contract" tests hand-copied the condition out of ``main.py`` and
asserted that a ``sys.exit(3)`` they raised themselves exited with 3. Those tests
were tautologies: they would have stayed green if ``main.py`` had exited 0.

Every test below drives the REAL entry point — ``cli(argv) -> int`` — and asserts
the returned process exit code. If the routing, the exception handling, or the
exit-code decision regresses, these turn red.

Exit-code contract under test (unchanged by this file — only made verifiable):

  0 — success (ETL complete; SFTP succeeded or not requested)
  1 — ETL / validation error (run did not complete)
  2 — argparse usage error, mutually-exclusive SFTP flags, or empty password stdin
  3 — SFTP delivery failed (ETL output present on disk)

The console-attach tests at the bottom cover the Windows-only affordance that
makes CLI output visible from a GUI-subsystem exe. Its OS syscall and its
stream-open are injected seams, so the *logic* (never clobber a live stream,
no-op without a parent console, never fire on the double-click path) is proven
on POSIX CI too; only a thin real-syscall smoke is ``win32``-gated.
"""

from __future__ import annotations

import inspect
import io
import sys
from pathlib import Path

import pytest
import tomllib

import src.main as main_mod
from src.main import cli
from tests.test_pipeline_required_input import _write_full_rostering_input

_EXPECTED_ENTITIES = ["Students", "Staff", "Family", "Classes", "Enrollments"]

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    """A minimal-but-complete myedbc rostering input set."""
    d = tmp_path / "input"
    d.mkdir()
    _write_full_rostering_input(d)
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def _etl_argv(gde_input: Path, gde_output: Path, *extra: str) -> list[str]:
    return ["--sis", "myedbc", "--input", str(gde_input), "--output", str(gde_output), *extra]


# ===========================================================================
#  Exit code 0 — success
# ===========================================================================


class TestExitCodeZero:
    def test_successful_run_returns_0_and_writes_csvs(self, gde_input: Path, gde_output: Path) -> None:
        """A clean ETL run through the real entry point returns 0 and writes output."""
        assert cli(_etl_argv(gde_input, gde_output)) == 0
        assert (gde_output / "Students.csv").exists()

    def test_successful_sftp_returns_0(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """--sftp with a delivery that SUCCEEDS returns 0 (the exit-3 branch is not taken)."""
        monkeypatch.setattr("src.etl.pipeline._sftp_upload", lambda *a, **k: True)
        assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 0

    def test_dry_run_with_sftp_returns_0(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """--dry-run never delivers, so --dry-run --sftp can never reach exit 3."""

        def _must_not_upload(*args, **kwargs):
            raise AssertionError("a dry run must never attempt an upload")

        monkeypatch.setattr("src.etl.pipeline._sftp_upload", _must_not_upload)
        assert cli(_etl_argv(gde_input, gde_output, "--dry-run", "--sftp")) == 0
        assert list(gde_output.glob("*.csv")) == []

    def test_no_sftp_flag_returns_0(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """Without --sftp the uploader is never reached and the run exits 0."""

        def _must_not_upload(*args, **kwargs):
            raise AssertionError("upload attempted without --sftp")

        monkeypatch.setattr("src.etl.pipeline._sftp_upload", _must_not_upload)
        assert cli(_etl_argv(gde_input, gde_output)) == 0

    def test_version_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--version is argparse's SystemExit(0) — translated to a returned 0."""
        assert cli(["--version"]) == 0
        assert "DistrictSync" in capsys.readouterr().out

    def test_sftp_show_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The --sftp-show subcommand routes through cli() and returns its handler's code."""
        assert cli(["--sftp-show"]) == 0
        assert "SFTP" in capsys.readouterr().out

    def test_sftp_test_routes_to_its_handler(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sftp-test routes to its handler and its return value becomes the exit
        code. With nothing configured that is 1 — and no network is touched."""
        assert cli(["--sftp-test"]) == 1
        assert "not configured" in capsys.readouterr().out


# ===========================================================================
#  Exit code 1 — ETL / validation failure
# ===========================================================================


class TestExitCodeOne:
    def test_no_usable_input_returns_1(self, tmp_path: Path, gde_output: Path) -> None:
        """Every required file missing → run_pipeline raises → cli's except-block returns 1.

        This drives the REAL except-block in the entry point (the old test raised
        its own ``sys.exit(1)`` and asserted the code it had just chosen).
        """
        empty_input = tmp_path / "input"
        empty_input.mkdir()
        assert cli(["--sis", "myedbc", "--input", str(empty_input), "--output", str(gde_output)]) == 1

    def test_no_usable_input_prints_support_hint(
        self, tmp_path: Path, gde_output: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure path tells the operator where to look."""
        empty_input = tmp_path / "input"
        empty_input.mkdir()
        cli(["--sis", "myedbc", "--input", str(empty_input), "--output", str(gde_output)])
        assert "etl_tool.log" in capsys.readouterr().out

    def test_input_path_not_a_directory_returns_1(self, tmp_path: Path, gde_output: Path) -> None:
        """run_pipeline's own ``sys.exit(1)`` (bad input dir) surfaces as a returned 1.

        Pins the SystemExit translation: a SystemExit raised *inside* the pipeline
        must reach the process with its code intact, not be swallowed or remapped.
        """
        missing = tmp_path / "nope"
        assert cli(["--sis", "myedbc", "--input", str(missing), "--output", str(gde_output)]) == 1

    def test_invalid_sis_returns_1(self, gde_input: Path, gde_output: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An unknown --sis fails validation before any ETL work and returns 1."""
        assert cli(["--sis", "not-a-district", "--input", str(gde_input), "--output", str(gde_output)]) == 1
        assert "Error:" in capsys.readouterr().out

    def test_zero_output_returns_1_not_0(self, tmp_path: Path, gde_output: Path) -> None:
        """The delivery-integrity out-gate reaches the process as exit 1.

        ``check_delivery_integrity`` (landed 2026-07-21) raises when a run produces
        no output at all. That is only useful to an unattended scheduled task if the
        *exit code* changes — the previous behaviour was a green exit 0. Pinned here
        at the entry point, which is where Task Scheduler actually reads the verdict.
        """
        d = tmp_path / "input"
        d.mkdir()
        # Non-empty input (the way-IN guard does not fire) whose every student is
        # Inactive, so every entity transforms to nothing.
        import pandas as pd

        pd.DataFrame(
            {
                "Student Number": ["S001"],
                "Legal First Name": ["Alice"],
                "Legal Surname": ["Smith"],
                "Date of birth": ["2010-01-15"],
                "Grade": ["10"],
                "School Number": ["100"],
                "Homeroom": ["A1"],
                "Previous school number": [""],
                "Usual First Name": [""],
                "Usual surname": [""],
                "Student email address": ["alice@test.ca"],
                "Enrolment Status": ["Inactive"],
                "Teacher Name": ["Ms. Harper"],
                "Teacher ID": ["T001"],
            }
        ).to_csv(d / "StudentDemographicInformation.txt", index=False)

        assert cli(["--sis", "myedbc", "--input", str(d), "--output", str(gde_output)]) == 1

    def test_missing_roster_anchor_returns_1_not_3(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """A vanished student export must exit 1 — NOT 3, and NOT 0.

        Exit 3 would actively lie ("output is on disk, only delivery failed") when
        the integrity gate refused before any write. This pins the interaction
        between the two mechanisms: the out-gate wins over the SFTP branch because
        it raises before delivery is ever attempted.
        """
        assert cli(_etl_argv(gde_input, gde_output)) == 0  # healthy baseline night

        def _must_not_upload(*args, **kwargs):
            raise AssertionError("delivery must not be attempted once the gate refused")

        monkeypatch.setattr("src.etl.pipeline._sftp_upload", _must_not_upload)
        (gde_input / "StudentDemographicInformation.txt").unlink()
        assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 1

    def test_unexpected_pipeline_exception_returns_1(
        self, gde_input: Path, gde_output: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Any non-SystemExit escape from run_pipeline is caught and returns 1."""
        monkeypatch.setattr(main_mod, "run_pipeline", _boom)
        assert cli(_etl_argv(gde_input, gde_output)) == 1
        assert "kaboom" in capsys.readouterr().out


def _boom(*args, **kwargs):
    raise RuntimeError("kaboom")


# ===========================================================================
#  Exit code 2 — argument misuse
# ===========================================================================


class TestExitCodeTwo:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--sftp-test", "--sftp-show"],
            ["--sftp-configure", "--sftp-test"],
            ["--sftp-configure", "--sftp-test", "--sftp-show"],
        ],
    )
    def test_mutually_exclusive_sftp_flags_return_2(self, argv: list[str], capsys) -> None:
        """Choosing more than one SFTP subcommand is a usage error → 2."""
        assert cli(argv) == 2
        assert "choose only one" in capsys.readouterr().out

    def test_missing_input_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sis without --input is an argparse usage error → 2 (argparse's own code)."""
        assert cli(["--sis", "myedbc"]) == 2
        assert "required" in capsys.readouterr().err

    def test_missing_sis_returns_2(self, tmp_path: Path) -> None:
        """--input without --sis is an argparse usage error → 2."""
        assert cli(["--input", str(tmp_path)]) == 2

    def test_empty_password_stdin_returns_2(self, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
        """--sftp-password-stdin with nothing piped → 2 (the documented 'stdin empty' code).

        Drives the real ``_read_sftp_password`` → ``sys.exit(2)`` path through the
        entry point rather than asserting the constant in isolation.
        """
        monkeypatch.delenv(main_mod.SFTP_PASSWORD_ENV_VAR, raising=False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        argv = [
            "--sftp-configure",
            "--sftp-host",
            "sftp.ca.spacesedu.com",
            "--sftp-user",
            "partner",
            "--sftp-remote",
            "/files",
            "--sftp-password-stdin",
        ]
        assert cli(argv) == 2
        assert "no password was received on stdin" in capsys.readouterr().out


# ===========================================================================
#  SystemExit → exit code translation (the layer the whole contract rides on)
# ===========================================================================


class TestExitCodeTranslation:
    """``cli`` returns an int, but argparse / getpass / run_pipeline signal via
    ``SystemExit``. ``_exit_code_from`` must reproduce CPython's own rules, or
    every code raised that way would be reported wrongly by the process."""

    def test_bare_sys_exit_is_success(self) -> None:
        """``sys.exit()`` (code None) exits the process 0 — not 1."""
        assert main_mod._exit_code_from(SystemExit(None)) == 0

    @pytest.mark.parametrize("code", [0, 1, 2, 3, 42])
    def test_int_codes_pass_through_verbatim(self, code: int) -> None:
        assert main_mod._exit_code_from(SystemExit(code)) == code

    def test_string_payload_is_printed_and_becomes_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """CPython writes a non-int SystemExit payload to stderr and exits 1."""
        assert main_mod._exit_code_from(SystemExit("something went wrong")) == 1
        assert "something went wrong" in capsys.readouterr().err

    def test_argv_defaults_to_sys_argv(self, monkeypatch) -> None:
        """``cli()`` with no argument reads ``sys.argv[1:]`` — what ``__main__`` and
        the console script both rely on."""
        monkeypatch.setattr(sys, "argv", ["DistrictSync", "--sftp-test", "--sftp-show"])
        assert cli() == 2


# ===========================================================================
#  Exit code 3 — SFTP delivery failed, ETL output present
# ===========================================================================


class TestExitCodeThree:
    def test_sftp_failure_returns_3(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """A requested upload that FAILS exits 3 — the nightly scheduled task's signal.

        This is the real replacement for the tautological
        ``test_sftp_fail_produces_exit_3_via_main_logic``: nothing here re-states
        main.py's condition; the entry point decides and we assert what it returned.
        """
        monkeypatch.setattr("src.etl.pipeline._sftp_upload", lambda *a, **k: False)
        assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 3

    def test_sftp_failure_leaves_csvs_intact(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """Exit 3 means 'built but not delivered' — the CSVs must NOT be rolled back."""
        monkeypatch.setattr("src.etl.pipeline._sftp_upload", lambda *a, **k: False)
        assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 3
        present = {e for e in _EXPECTED_ENTITIES if (gde_output / f"{e}.csv").exists()}
        assert present == set(_EXPECTED_ENTITIES)

    def test_sftp_failure_logs_exit_3_summary(
        self, gde_input: Path, gde_output: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The exit-3 branch re-emits an ERROR summary so a scheduled run's operator
        can find the reason in etl_tool.log — the ONLY signal a windowed exe leaves
        behind when nobody is watching a terminal.

        ``_configure_cli_logging`` is stubbed to a plain logger because the real one
        runs ``logging.config.fileConfig``, which replaces root's handlers and so
        discards caplog's. The sink wiring itself is covered by
        ``tests/test_entry_logging.py``; the subject here is the ERROR emission.
        """
        import logging

        monkeypatch.setattr("src.etl.pipeline._sftp_upload", lambda *a, **k: False)
        monkeypatch.setattr(main_mod, "_configure_cli_logging", lambda: logging.getLogger("src.main"))
        with caplog.at_level(logging.ERROR, logger="src.main"):
            assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 3
        assert any("code 3" in r.message for r in caplog.records if r.levelno >= logging.ERROR)


# ===========================================================================
#  Packaging — the console-script entry point must actually be the CLI
# ===========================================================================


class TestConsoleScriptEntryPoint:
    def test_declared_entry_point_resolves_to_a_zero_arg_callable(self) -> None:
        """``[project.scripts]`` must name a callable a console script can actually call.

        setuptools generates ``sys.exit(<target>())`` — so the target must be
        callable with NO arguments. The previous declaration (``src.main:main``)
        pointed at a 3-positional-arg helper and would have raised TypeError on
        every invocation; this test would have caught that.
        """
        data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        target = data["project"]["scripts"]["districtsync"]
        module_name, _, attr = target.partition(":")

        import importlib

        func = getattr(importlib.import_module(module_name), attr)
        required = [
            p
            for p in inspect.signature(func).parameters.values()
            if p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
        assert required == [], f"console-script target {target} needs arguments: {[p.name for p in required]}"

    def test_declared_entry_point_is_cli(self) -> None:
        """The packaging claim and the code agree on ONE entry point."""
        data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["project"]["scripts"]["districtsync"] == "src.main:cli"


# ===========================================================================
#  No-argv dispatch — the GUI path must never touch the console
# ===========================================================================


class TestNoArgvDispatch:
    def test_no_argv_launches_ui_and_returns_0(self, monkeypatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(main_mod, "_default_ui_launcher", lambda: lambda: calls.append("launched"))
        assert cli([]) == 0
        assert calls == ["launched"]

    def test_no_argv_never_attaches_a_console(self, monkeypatch) -> None:
        """The double-click path must NOT attach/allocate a console — no flashing box.

        This is the behavioural guard on the GUI-subsystem UX: the console
        affordance is gated on the CLI branch, not on process start.
        """
        monkeypatch.setattr(main_mod, "_default_ui_launcher", lambda: lambda: None)
        attempts: list[bool] = []
        monkeypatch.setattr(main_mod, "_attach_parent_console", lambda: attempts.append(True) or False)
        cli([])
        assert attempts == []

    def test_cli_path_attaches_the_console_once(self, monkeypatch) -> None:
        attempts: list[bool] = []
        monkeypatch.setattr(main_mod, "_attach_parent_console", lambda: attempts.append(True) or False)
        cli(["--sftp-show"])
        assert attempts == [True]


# ===========================================================================
#  Console attach — Windows affordance, POSIX no-op
# ===========================================================================


def _blank_streams(monkeypatch) -> None:
    """Simulate a windowed PyInstaller build: all three std streams are None."""
    for name in ("stdout", "stderr", "stdin"):
        monkeypatch.setattr(sys, name, None)
        monkeypatch.setattr(sys, f"__{name}__", None)


class TestAttachParentConsole:
    def test_noop_on_posix(self, monkeypatch) -> None:
        """The whole affordance is a no-op off Windows — CI runs Ubuntu."""
        monkeypatch.setattr(sys, "platform", "linux")
        called: list[str] = []
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: called.append("syscall") or True)
        _blank_streams(monkeypatch)

        assert main_mod._attach_parent_console() is False
        assert called == [], "no Windows syscall may be made on POSIX"
        assert sys.stdout is None, "POSIX streams must be left exactly as they were"

    def test_noop_when_streams_already_usable(self, monkeypatch) -> None:
        """A source run, or a redirected `exe > out.txt`, already has real streams —
        do NOT attach and do NOT rebind (rebinding would break the redirect)."""
        monkeypatch.setattr(sys, "platform", "win32")
        called: list[str] = []
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: called.append("syscall") or True)

        live = io.StringIO()
        monkeypatch.setattr(sys, "stdout", live)
        monkeypatch.setattr(sys, "stderr", live)
        monkeypatch.setattr(sys, "stdin", live)

        assert main_mod._attach_parent_console() is False
        assert called == []
        assert sys.stdout is live

    def test_no_parent_console_is_a_clean_noop(self, monkeypatch) -> None:
        """Scheduled task / service / double-click: AttachConsole fails → streams stay None."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: False)
        opened: list[str] = []
        monkeypatch.setattr(main_mod, "_open_console_stream", lambda d, m: opened.append(d))
        _blank_streams(monkeypatch)

        assert main_mod._attach_parent_console() is False
        assert opened == [], "no console device may be opened when the attach failed"
        assert sys.stdout is None

    def test_syscall_failure_is_survivable(self, monkeypatch) -> None:
        """A missing/blocked kernel32 must degrade to 'no console', never crash the CLI."""
        monkeypatch.setattr(sys, "platform", "win32")

        def _explode() -> bool:
            raise OSError("kernel32 unavailable")

        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", _explode)
        _blank_streams(monkeypatch)

        assert main_mod._attach_parent_console() is False

    def test_rebinds_dead_streams_and_their_dunder_originals(self, monkeypatch) -> None:
        """On a successful attach the dead streams are rebound — including the
        ``sys.__stdin__`` original, which ``getpass`` compares against to decide
        whether it may use the no-echo console reader (a plaintext-echo risk if
        it silently falls back)."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: True)
        made: dict[str, io.StringIO] = {}

        def _fake_open(device: str, mode: str) -> io.StringIO:
            stream = io.StringIO()
            made[f"{device}:{mode}"] = stream
            return stream

        monkeypatch.setattr(main_mod, "_open_console_stream", _fake_open)
        _blank_streams(monkeypatch)

        assert main_mod._attach_parent_console() is True
        assert sys.stdout is not None and sys.stderr is not None and sys.stdin is not None
        assert sys.__stdin__ is sys.stdin
        assert sys.__stdout__ is sys.stdout
        assert set(made) == {"CONOUT$:w", "CONIN$:r"}

    def test_live_stream_is_never_clobbered_on_a_partial_attach(self, monkeypatch) -> None:
        """Redirection is per-stream: `exe --flags 2> err.txt` leaves stderr live and
        stdout dead. Only the dead one may be rebound."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: True)
        monkeypatch.setattr(main_mod, "_open_console_stream", lambda d, m: io.StringIO())

        live_err = io.StringIO()
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", live_err)
        monkeypatch.setattr(sys, "stdin", None)

        assert main_mod._attach_parent_console() is True
        assert sys.stderr is live_err, "a redirected stderr must survive the attach"
        assert sys.stdout is not None

    def test_open_failure_leaves_the_stream_dead_without_raising(self, monkeypatch) -> None:
        """CONOUT$ can fail to open (odd hosts, detached sessions) — degrade, never raise."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(main_mod, "_win32_attach_parent_console", lambda: True)

        def _fail(device: str, mode: str):
            raise OSError("cannot open console device")

        monkeypatch.setattr(main_mod, "_open_console_stream", _fail)
        _blank_streams(monkeypatch)

        assert main_mod._attach_parent_console() is False
        assert sys.stdout is None

    @pytest.mark.skipif(sys.platform != "win32", reason="real AttachConsole syscall is Windows-only")
    def test_real_syscall_returns_a_bool_without_raising(self) -> None:
        """Thin smoke over the un-injected seam: whatever the host's console state,
        the raw syscall wrapper returns a bool and never propagates an exception."""
        assert isinstance(main_mod._win32_attach_parent_console(), bool)


# ===========================================================================
#  Dead std streams — the shipped windowed exe's actual starting condition
# ===========================================================================


class TestDeadStreamsNeverBreakTheContract:
    def test_print_is_a_silent_noop_when_stdout_is_none(self, monkeypatch) -> None:
        """CPython's ``print`` returns silently when ``sys.stdout is None``.

        Pinned because every user-facing CLI message depends on it: if this ever
        raised, a windowed exe would crash instead of merely being quiet.
        """
        monkeypatch.setattr(sys, "stdout", None)
        print("this must not raise")  # noqa: T201 - the behaviour under test

    def test_exit_code_3_still_returned_with_dead_streams(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """The exit-code contract holds in the shipped windowed exe's condition
        (all std streams None, no parent console): quiet, but still exit 3."""
        monkeypatch.setattr("src.etl.pipeline._sftp_upload", lambda *a, **k: False)
        monkeypatch.setattr(main_mod, "_attach_parent_console", lambda: False)
        _blank_streams(monkeypatch)

        assert cli(_etl_argv(gde_input, gde_output, "--sftp")) == 3

    def test_exit_code_1_still_returned_with_dead_streams(self, tmp_path: Path, gde_output: Path, monkeypatch) -> None:
        """Same for the failure path — the ``print`` of the support hint must not
        turn a clean exit 1 into a crash."""
        monkeypatch.setattr(main_mod, "_attach_parent_console", lambda: False)
        _blank_streams(monkeypatch)

        empty_input = tmp_path / "input"
        empty_input.mkdir()
        assert cli(["--sis", "myedbc", "--input", str(empty_input), "--output", str(gde_output)]) == 1
