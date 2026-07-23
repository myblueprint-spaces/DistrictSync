"""Engine-side tests for the seasonal sync-window GATE in ``src.main._cli``.

REPRODUCE-FIRST: the core "scheduled + outside window -> does not run, writes
nothing, exits 0" test (``test_scheduled_outside_enabled_window_does_not_run``)
was written and confirmed FAILING against the pre-slice base — no gate and no
config fields, so the automatic nightly run proceeded and wrote CSVs over the
summer — before a line of the gate was implemented.

The gate governs the app's OWN automatic nightly run only (the run that
IDENTIFIES as scheduled — resolved ``source == "scheduled"``): outside an
ENABLED window it does no ETL, no write, no delivery, logs one calm paused
line, and exits 0. A paused summer night is a healthy, intentional state, never
a failure (the scheduled task must show success, never failed). A DISABLED
window, an IN-window night, and any non-scheduled source (manual / cli) bypass
the gate and behave byte-identically to today.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.history.store import read_run_records
from src.main import _paused_by_sync_window, cli
from tests.test_pipeline_required_input import _write_full_rostering_input

_GATE_LOGGER = logging.getLogger("tests.sync_window_gate")

# A WRAP-AROUND window (Aug 11 -> Jul 6, spanning New Year) — the real school-year
# shape. A mid-July date sits in the summer gap OUTSIDE it; a September date is
# inside it.
_WRAP_START = "08-11"
_WRAP_END = "07-06"
_SUMMER = date(2026, 7, 15)  # outside the wrap window (the paused case)
_TERM = date(2026, 9, 15)  # inside the wrap window (the running case)


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


def _write_config(profile_dir: Path, **fields: object) -> None:
    """Write a raw ``config.json`` into the isolated profile dir.

    Deliberately raw JSON (not ``AppConfig.save``) so the SAME test drives the
    pre-slice base (which ignores the unknown ``sync_window_*`` keys — forward
    compat) and the implemented gate identically.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "config.json").write_text(json.dumps(fields), encoding="utf-8")


def _argv(gde_input: Path, gde_output: Path, *extra: str) -> list[str]:
    return ["--sis", "myedbc", "--input", str(gde_input), "--output", str(gde_output), *extra]


def _fix_today(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    """Inject a fixed 'today' at the ``src.main._today`` seam (raising=False so the
    same test is meaningful against the pre-slice base, where the seam is absent)."""
    monkeypatch.setattr("src.main._today", lambda: today, raising=False)


class TestPausedBySyncWindowUnit:
    """Unit tests for the pure gate decision ``_paused_by_sync_window`` (with the
    calm paused / loud-error LOG line). Driven directly rather than through ``cli()``
    because ``cli()`` reconfigures logging via ``fileConfig`` (which resets root
    handlers and drops the caplog handler) — so the log line is asserted here, at the
    seam that owns it, while the ``cli()`` tests below assert the run/no-run outcome."""

    def test_outside_enabled_window_returns_true_and_logs_paused(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(sync_window_enabled=True, sync_window_start=_WRAP_START, sync_window_end=_WRAP_END)
        with caplog.at_level(logging.INFO, logger=_GATE_LOGGER.name):
            paused = _paused_by_sync_window(cfg, _SUMMER, _GATE_LOGGER)
        assert paused is True
        assert "Sync paused" in caplog.text
        assert "resumes 2026-08-11" in caplog.text  # next resume date, formatted

    def test_inside_enabled_window_returns_false_and_is_quiet(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(sync_window_enabled=True, sync_window_start=_WRAP_START, sync_window_end=_WRAP_END)
        with caplog.at_level(logging.INFO, logger=_GATE_LOGGER.name):
            paused = _paused_by_sync_window(cfg, _TERM, _GATE_LOGGER)
        assert paused is False
        assert "Sync paused" not in caplog.text

    def test_disabled_window_returns_false(self) -> None:
        cfg = AppConfig(sync_window_enabled=False, sync_window_start=_WRAP_START, sync_window_end=_WRAP_END)
        assert _paused_by_sync_window(cfg, _SUMMER, _GATE_LOGGER) is False

    def test_malformed_bounds_return_false_and_log_error(self, caplog: pytest.LogCaptureFixture) -> None:
        cfg = AppConfig(sync_window_enabled=True, sync_window_start="99-99", sync_window_end=_WRAP_END)
        with caplog.at_level(logging.ERROR, logger=_GATE_LOGGER.name):
            paused = _paused_by_sync_window(cfg, _SUMMER, _GATE_LOGGER)
        assert paused is False, "a malformed window must NOT pause — that would silently stop the sync"
        assert any("window" in r.message.lower() for r in caplog.records)


class TestScheduledOutsideWindowPauses:
    def test_scheduled_outside_enabled_window_does_not_run(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE core behaviour: a paused summer night writes nothing and exits 0.

        Scheduled source + enabled window + today outside it -> no ETL, no write,
        exit 0, and NO per-night run record. (The paused LOG line is asserted at the
        unit level above — ``cli()`` reconfigures logging out from under caplog.)
        """
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)

        code = cli(_argv(gde_input, gde_output, "--source", "scheduled"))

        assert code == 0, "a paused night is healthy, not a failure — must exit 0"
        assert list(gde_output.glob("*.csv")) == [], "a paused night must write no output"
        assert read_run_records() == [], "a paused night must not write a per-night run record"

    def test_scheduled_outside_enabled_window_does_not_deliver(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A paused night never touches SFTP — the upload path is never reached."""

        def _must_not_upload(*args: object, **kwargs: object) -> bool:
            raise AssertionError("a paused night must never attempt an upload")

        monkeypatch.setattr("src.etl.pipeline._sftp_upload", _must_not_upload)
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)

        assert cli(_argv(gde_input, gde_output, "--source", "scheduled", "--sftp")) == 0
        assert list(gde_output.glob("*.csv")) == []

    def test_the_day_after_the_end_boundary_is_paused(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Jul 7 — one day past an end of Jul 6 — is outside the wrap window."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, date(2026, 7, 7))
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert list(gde_output.glob("*.csv")) == []


class TestScheduledInsideWindowRuns:
    def test_scheduled_inside_enabled_window_runs_and_writes(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An in-window scheduled night is a normal run — writes CSVs, exits 0."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _TERM)
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert (gde_output / "Students.csv").exists()

    @pytest.mark.parametrize("boundary", [date(2026, 8, 11), date(2027, 7, 6)])
    def test_boundary_days_are_inclusive_and_run(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
        boundary: date,
    ) -> None:
        """The first (Aug 11) and last (Jul 6) day of the window both RUN (inclusive)."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, boundary)
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert (gde_output / "Students.csv").exists()


class TestGateBypass:
    @pytest.mark.parametrize("source", ["manual", "cli"])
    def test_non_scheduled_source_bypasses_the_window(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
        source: str,
    ) -> None:
        """Manual / cli runs are explicit human choices about WHEN to run — they run
        even outside the window (no override flag needed)."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)
        assert cli(_argv(gde_input, gde_output, "--source", source)) == 0
        assert (gde_output / "Students.csv").exists()

    def test_disabled_window_runs_year_round(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DISABLED window is byte-identical to today: a scheduled summer night runs."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=False,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert (gde_output / "Students.csv").exists()

    def test_no_window_config_at_all_runs_year_round(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No config.json at all -> defaults (window off) -> scheduled run proceeds."""
        _fix_today(monkeypatch, _SUMMER)
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert (gde_output / "Students.csv").exists()


class TestSourceResolution:
    def test_env_dsync_source_scheduled_is_gated(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run that identifies as scheduled via DSYNC_SOURCE (no --source flag) is
        governed by the window too — governance follows the run's resolved identity."""
        monkeypatch.setenv("DSYNC_SOURCE", "scheduled")
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start=_WRAP_START,
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)
        assert cli(_argv(gde_input, gde_output)) == 0
        assert list(gde_output.glob("*.csv")) == []


class TestMalformedWindowFailsSafe:
    def test_enabled_but_invalid_bounds_runs_normally(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A malformed window must NEVER silently stop the nightly sync (the dangerous
        failure). It runs normally, ignoring the window. (The loud error LOG is
        asserted in ``TestPausedBySyncWindowUnit`` — ``cli()`` resets caplog's sink.)"""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start="99-99",  # not a real calendar month-day
            sync_window_end=_WRAP_END,
        )
        _fix_today(monkeypatch, _SUMMER)
        code = cli(_argv(gde_input, gde_output, "--source", "scheduled"))
        assert code == 0
        assert (gde_output / "Students.csv").exists(), "an invalid window must not pause the sync"

    def test_enabled_with_empty_bounds_runs_normally(
        self,
        isolated_user_profile: Path,
        gde_input: Path,
        gde_output: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Enabled but never actually configured (blank bounds) -> runs normally."""
        _write_config(
            isolated_user_profile,
            sync_window_enabled=True,
            sync_window_start="",
            sync_window_end="",
        )
        _fix_today(monkeypatch, _SUMMER)
        assert cli(_argv(gde_input, gde_output, "--source", "scheduled")) == 0
        assert (gde_output / "Students.csv").exists()
