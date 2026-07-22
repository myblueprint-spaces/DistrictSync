"""The roster-shrink fail-safe on the UNATTENDED (CLI / scheduled / cron) path.

Owner decision 2026-07-21 (``docs/claugentic-DECISIONS.md``): a *partial* roster
shrink — the :data:`~src.etl.pipeline.ROSTER_ANCHOR_ENTITY` (``Students``) present
with rows, but sharply smaller than the previous run — must **refuse to deliver,
keep the last-good output, and exit non-zero** on the unattended path, instead of
the old warn-and-deliver. A broken MyEd export silently deactivating a subset of a
district's students in SpacesEDU is the product's highest-stakes failure, and a
blocked delivery is recoverable where a wrong mass-deactivation is a support
incident.

This closes the gap the *total*-wipe / missing-anchor case already covers
(:func:`~src.etl.pipeline.check_delivery_integrity` → ``INCOMPLETE_ROSTER`` /
``NO_OUTPUT``, see ``tests/test_pipeline_delivery_integrity.py``): the roster anchor
is PRESENT, just far smaller.

Reproduce-first (``bug`` discipline, matching the delivery-integrity slice): the
core refusal — a >20% ``Students`` drop through ``run_pipeline`` — is RED against
the pre-fix pipeline, which logged the drop as an ANOMALY and delivered anyway.

**Scope decisions pinned here (see the module tests for each):**

* Trigger is the roster anchor (``Students``) ONLY — a Family/Staff drop stays a
  warning, never a block.
* First run / no baseline / an unreadable baseline is NOT a shrink — deliver.
* Tier configs with no anchor (``mbponly`` / ``sd51attendance``) are inert.
* The escape hatch is a per-invocation ``--acknowledge-shrink`` CLI flag threaded
  into ``run_pipeline`` (``acknowledge_shrink``); it is NEVER persisted.
* dry-run surfaces the would-be refusal (mirrors ``check_delivery_integrity``), and
  never delivers.
* Self-healing: once an acknowledged run re-seeds the smaller roster to
  ``Students.csv``, the next unattended run compares new-vs-new and does not fire.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.etl import pipeline
from src.etl.pipeline import ROSTER_ANCHOR_ENTITY, RunErrorCategory, run_pipeline
from src.history.store import read_run_records

_ANCHOR = ROSTER_ANCHOR_ENTITY


# --------------------------------------------------------------------------- #
# Input builders — an anchor-only myedbc demographic of a chosen size.          #
# A demographic-only input builds ONLY Students (no schedule → no Classes/      #
# Enrollments, no staff/family files → those skip), so the roster size is the   #
# single knob and no other entity muddies the shrink comparison. No real PII.   #
# --------------------------------------------------------------------------- #
def _demographic(n: int, *, status: str = "Active") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Student Number": [f"S{i:04d}" for i in range(n)],
            "Legal First Name": [f"First{i}" for i in range(n)],
            "Legal Surname": [f"Last{i}" for i in range(n)],
            "Date of birth": ["2010-01-15"] * n,
            "Grade": ["10"] * n,
            "School Number": ["100"] * n,
            "Homeroom": ["A1"] * n,
            "Previous school number": [""] * n,
            "Usual First Name": [""] * n,
            "Usual surname": [""] * n,
            "Student email address": [f"s{i}@test.ca" for i in range(n)],
            "Enrolment Status": [status] * n,
            "Teacher Name": ["Ms. Harper"] * n,
            "Teacher ID": ["T001"] * n,
        }
    )


def _write_demographic(input_dir: Path, n: int) -> None:
    """(Re)write the demographic export so the next run's roster is exactly ``n``."""
    _demographic(n).to_csv(input_dir / "StudentDemographicInformation.txt", index=False)


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def _snapshot(output_dir: Path) -> dict[str, bytes]:
    """Every top-level CSV's exact bytes — the "output dir untouched" oracle."""
    return {p.name: p.read_bytes() for p in sorted(output_dir.glob("*.csv"))}


def _archive_dirs(output_dir: Path) -> list[Path]:
    return [p for p in output_dir.iterdir() if p.is_dir() and p.name.startswith("archive_")]


def _students_row_count(output_dir: Path) -> int:
    with open(output_dir / f"{_ANCHOR}.csv", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def _last_run_log(caplog: pytest.LogCaptureFixture) -> dict:
    lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
    assert lines, "expected a structured __DISTRICTSYNC_RUN__ line"
    return json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])


def _exit_code_via_main_wiring(sis: str, input_path: str, output_path: str, *extra: str) -> int:
    """Drive the REAL CLI entry point (``src.main.cli``) and return the exit code."""
    from src.main import cli

    return cli(["--sis", sis, "--input", input_path, "--output", output_path, *extra])


def _seed_baseline(gde_input: Path, gde_output: Path, n: int) -> dict[str, bytes]:
    """Run one healthy unattended night with an ``n``-student roster; return its bytes."""
    _write_demographic(gde_input, n)
    result = run_pipeline("myedbc", str(gde_input), str(gde_output))
    assert result.entity_counts.get(_ANCHOR, 0) == n  # guard: the baseline really is n students
    return _snapshot(gde_output)


# --------------------------------------------------------------------------- #
# The pure gate — cheap, total, PII-safe; the "do NOT regress" cases live here. #
# --------------------------------------------------------------------------- #
class TestCheckRosterShrinkPure:
    """``check_roster_shrink(outputs, output_dir)`` is a pure predicate.

    It returns a ready-to-raise :class:`~src.etl.pipeline.DeliveryIntegrityError`
    (``ROSTER_SHRINK``) or ``None``. It reuses ``ANOMALY_THRESHOLD`` and the previous
    ``Students.csv`` row-count read — never a second threshold or a second reader.
    """

    @staticmethod
    def _anchor(n: int) -> dict[str, pd.DataFrame]:
        return {_ANCHOR: pd.DataFrame({"User ID": [f"S{i}" for i in range(n)]})}

    @staticmethod
    def _write_prev(output_dir: Path, rows: int) -> None:
        body = "User ID\n" + "\n".join(f"S{i}" for i in range(rows)) + ("\n" if rows else "")
        (output_dir / f"{_ANCHOR}.csv").write_text(body, encoding="utf-8")

    def test_no_baseline_is_not_a_shrink(self, tmp_path: Path) -> None:
        # First run: no previous Students.csv on disk → deliver, never a shrink.
        assert pipeline.check_roster_shrink(self._anchor(1), tmp_path) is None

    def test_empty_baseline_is_not_a_shrink(self, tmp_path: Path) -> None:
        # Header-only previous file (0 data rows) is a missing baseline, not a drop.
        self._write_prev(tmp_path, 0)
        assert pipeline.check_roster_shrink(self._anchor(10), tmp_path) is None

    def test_unreadable_baseline_is_not_a_shrink(self, tmp_path: Path) -> None:
        # A directory squatting on Students.csv → the count read returns None. An
        # unreadable baseline cannot prove a shrink, so we DELIVER (do not block) —
        # the separate anomaly check still surfaces it as a degradation warning.
        (tmp_path / f"{_ANCHOR}.csv").mkdir()
        assert pipeline.check_roster_shrink(self._anchor(10), tmp_path) is None

    def test_within_threshold_is_clean(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 100)  # 100 → 85 == 15% drop, under the 20% bar
        assert pipeline.check_roster_shrink(self._anchor(85), tmp_path) is None

    def test_exactly_at_threshold_is_clean(self, tmp_path: Path) -> None:
        # Mirrors compute_anomalies' strict `<` bar: 100 → 80 is exactly 20% → clean.
        self._write_prev(tmp_path, 100)
        assert pipeline.check_roster_shrink(self._anchor(80), tmp_path) is None

    def test_just_over_threshold_is_a_fault(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 100)  # 100 → 79 == just over 20%
        fault = pipeline.check_roster_shrink(self._anchor(79), tmp_path)
        assert fault is not None
        assert fault.category == RunErrorCategory.ROSTER_SHRINK.value

    def test_large_drop_is_a_fault(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 100)
        fault = pipeline.check_roster_shrink(self._anchor(10), tmp_path)
        assert fault is not None
        assert fault.category == RunErrorCategory.ROSTER_SHRINK.value

    def test_growth_is_never_a_shrink(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 50)
        assert pipeline.check_roster_shrink(self._anchor(100), tmp_path) is None

    def test_anchor_absent_from_outputs_is_never_this_gates_concern(self, tmp_path: Path) -> None:
        # A tier config that produces no Students, or the anchor vanished entirely
        # (handled by check_delivery_integrity) — this gate must not fire.
        self._write_prev(tmp_path, 100)
        outputs = {"CourseInfo": pd.DataFrame({"x": [1]}), "StudentCourses": pd.DataFrame({"x": [1]})}
        assert pipeline.check_roster_shrink(outputs, tmp_path) is None

    def test_fault_is_raisable_and_carries_a_bounded_category(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 100)
        fault = pipeline.check_roster_shrink(self._anchor(10), tmp_path)
        assert isinstance(fault, pipeline.DeliveryIntegrityError)
        assert isinstance(fault, RuntimeError)  # rides the existing main.py exit-1 wiring
        assert fault.category in {c.value for c in RunErrorCategory}

    def test_fault_message_carries_no_pii_and_no_paths(self, tmp_path: Path) -> None:
        self._write_prev(tmp_path, 100)
        fault = pipeline.check_roster_shrink(self._anchor(10), tmp_path)
        assert fault is not None
        message = str(fault)
        assert "S0" not in message and "S1" not in message  # no student identifier
        assert "/" not in message and "\\" not in message  # no filesystem path
        assert _ANCHOR in message  # entity name is safe and useful
        assert message.strip() == message and message


# --------------------------------------------------------------------------- #
# THE CORE REFUSAL — a >20% Students drop must refuse + exit non-zero (RED).    #
# --------------------------------------------------------------------------- #
class TestPartialShrinkRefusesUnattendedDelivery:
    """RED against the pre-fix pipeline (it logged the drop and delivered)."""

    def test_partial_shrink_raises(self, gde_input: Path, gde_output: Path) -> None:
        """CORE RED: 10 → 2 students (80% drop) must now raise, not deliver."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

    def test_main_wiring_exits_1(self, gde_input: Path, gde_output: Path) -> None:
        """Exit 1 — the contract's existing 'ETL error' meaning; no new code invented."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        assert _exit_code_via_main_wiring("myedbc", str(gde_input), str(gde_output)) == 1

    def test_previous_good_output_is_byte_identical_afterwards(self, gde_input: Path, gde_output: Path) -> None:
        """Refusal happens BEFORE the write — the last-good roster survives untouched
        (not overwritten, not archived out of the SFTP glob)."""
        good = _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        assert _snapshot(gde_output) == good
        assert _students_row_count(gde_output) == 10  # the good baseline, not the shrunk 2
        assert _archive_dirs(gde_output) == []

    def test_sftp_is_never_attempted(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """A payload the gate cannot vouch for must not reach the network."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        calls: list[str] = []
        monkeypatch.setattr(pipeline, "_sftp_upload", lambda *a, **k: calls.append("called") or True)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)
        assert calls == []

    def test_run_record_says_failed_with_the_roster_shrink_category(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Home / Run History read the store — it must show a failure, not a green run,
        carrying the BOUNDED category only. Counts stay honest (the shrunk roster IS
        reported: what was built, then refused)."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.ROSTER_SHRINK.value
        assert payload[_ANCHOR] == 2  # honest: what WAS built (and refused) is reported

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.ROSTER_SHRINK.value
        assert not records[0].get("error")  # privacy split: no free-text error in the store

    def test_dry_run_also_surfaces_the_refusal(self, gde_input: Path, gde_output: Path) -> None:
        """A preview that hides a refusal it would hit live is misleading — dry-run
        raises too (mirrors check_delivery_integrity), and never delivers."""
        good = _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(gde_input), str(gde_output), dry_run=True)
        assert _snapshot(gde_output) == good  # a dry-run never writes


# --------------------------------------------------------------------------- #
# Legitimate cases that MUST still deliver (the regression fence).              #
# --------------------------------------------------------------------------- #
class TestLegitimateRunsStillDeliver:
    def test_first_run_delivers(self, gde_input: Path, gde_output: Path) -> None:
        _write_demographic(gde_input, 10)
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.entity_counts.get(_ANCHOR, 0) == 10
        assert (gde_output / f"{_ANCHOR}.csv").exists()

    def test_stable_rerun_is_byte_identical_and_delivers(self, gde_input: Path, gde_output: Path) -> None:
        good = _seed_baseline(gde_input, gde_output, 10)
        # Same roster again: 10 → 10, no drop → delivers, output byte-identical.
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert _snapshot(gde_output) == good
        assert _archive_dirs(gde_output) == []

    def test_within_threshold_drop_delivers(self, gde_input: Path, gde_output: Path) -> None:
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 9)  # 10% drop, under the 20% bar
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.entity_counts.get(_ANCHOR, 0) == 9
        assert _students_row_count(gde_output) == 9  # delivered the smaller roster

    def test_growth_delivers(self, gde_input: Path, gde_output: Path) -> None:
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 40)
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert _students_row_count(gde_output) == 40


# --------------------------------------------------------------------------- #
# Tier configs (no roster anchor) are inert end-to-end.                         #
# --------------------------------------------------------------------------- #
class TestTierConfigsAreInert:
    def test_mbponly_never_fires_the_shrink_gate(self, gde_output: Path) -> None:
        """mbponly produces CourseInfo + StudentCourses, never Students — the gate
        is inert by the same `anchor in outputs` guard, run twice for good measure."""
        mbp_input = Path(__file__).parent / "snapshots" / "mbp_input"
        first = run_pipeline("mbponly", str(mbp_input), str(gde_output))
        assert first.entity_counts.get("CourseInfo", 0) > 0
        assert not (gde_output / f"{_ANCHOR}.csv").exists()
        # A second run must not raise either (no anchor to compare, ever).
        second = run_pipeline("mbponly", str(mbp_input), str(gde_output))
        assert second.entity_counts.get("StudentCourses", 0) > 0


# --------------------------------------------------------------------------- #
# The headless escape hatch — per-invocation --acknowledge-shrink, never saved. #
# --------------------------------------------------------------------------- #
class TestAcknowledgeShrinkEscapeHatch:
    def test_acknowledge_downgrades_to_deliver(self, gde_input: Path, gde_output: Path) -> None:
        """A headless district with no UI re-seeds via the per-run flag: the shrink is
        downgraded to a warning and the run delivers the smaller roster + records success."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        result = run_pipeline("myedbc", str(gde_input), str(gde_output), acknowledge_shrink=True)
        assert result.entity_counts.get(_ANCHOR, 0) == 2
        assert _students_row_count(gde_output) == 2  # the acknowledged smaller roster was written

    def test_acknowledge_via_cli_flag_exits_zero(self, gde_input: Path, gde_output: Path) -> None:
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        assert _exit_code_via_main_wiring("myedbc", str(gde_input), str(gde_output), "--acknowledge-shrink") == 0

    def test_acknowledge_still_records_the_anomaly(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The downgrade delivers, but the >20% drop is not swept under the rug — it
        still rides the run record's anomalies list so the ledger stays honest."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            result = run_pipeline("myedbc", str(gde_input), str(gde_output), acknowledge_shrink=True)
        assert any("Students dropped" in a for a in result.anomalies)
        payload = _last_run_log(caplog)
        assert payload["status"] == "success"

    def test_acknowledge_is_per_run_and_not_persisted(self, gde_input: Path, gde_output: Path) -> None:
        """Acknowledging ONE run must not silently suppress a LATER unattended run. After
        an acknowledged delivery, restoring a large baseline and running WITHOUT the flag
        refuses again — proof the ack was per-invocation, never written to AppConfig."""
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        run_pipeline("myedbc", str(gde_input), str(gde_output), acknowledge_shrink=True)  # delivers 2

        # Simulate a fresh large baseline landing (a healthy night), then a shrink again
        # with NO flag — it must refuse, so the earlier ack clearly did not persist.
        _seed_baseline(gde_input, gde_output, 10)
        _write_demographic(gde_input, 2)
        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(gde_input), str(gde_output))


# --------------------------------------------------------------------------- #
# Self-healing — the re-seed IS the escape hatch (owner decision).             #
# --------------------------------------------------------------------------- #
class TestSelfHealingAfterReseed:
    def test_shrink_refused_then_reseeded_then_clean(self, gde_input: Path, gde_output: Path) -> None:
        """The full operational recovery, in one flow:

        1. Healthy night → 10-student baseline on disk.
        2. Broken export (2 students) → REFUSED; the 10-row baseline is untouched.
        3. Operator re-seeds the smaller roster with ONE acknowledged run → 2 rows
           become the new baseline in Students.csv.
        4. The NEXT unattended night (2 students, no flag) compares new-vs-new → no
           shrink fires, it delivers, exit 0. The gate self-heals without a calendar.
        """
        good = _seed_baseline(gde_input, gde_output, 10)

        # Step 2 — the broken export is refused, last-good survives.
        _write_demographic(gde_input, 2)
        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert _snapshot(gde_output) == good
        assert _students_row_count(gde_output) == 10

        # Step 3 — the acknowledged re-seed overwrites the baseline with the 2-row roster.
        reseed = run_pipeline("myedbc", str(gde_input), str(gde_output), acknowledge_shrink=True)
        assert reseed.entity_counts.get(_ANCHOR, 0) == 2
        assert _students_row_count(gde_output) == 2

        # Step 4 — the next nightly (same 2 students, NO flag) is clean and delivers.
        nightly = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert nightly.entity_counts.get(_ANCHOR, 0) == 2
        assert nightly.anomalies == []  # 2 → 2 is no drop at all
        assert _students_row_count(gde_output) == 2
