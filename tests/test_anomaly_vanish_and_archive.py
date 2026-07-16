"""Integration tests — the vanished-entity anomaly leg + stale-output archival (W1b).

Pins two trust gaps end-to-end:

* **run_pipeline**: an entity the config produces (enabled-entities-derived) whose
  source file disappears no longer slips past the anomaly check — the run stays
  exit 0 (a partial run with some empty sources is legitimate by design) but
  carries the ANOMALY warning in its result + run record, and the stale CSV is
  archived (never shipped, never deleted).
* **convert_job**: the SAME vanish fires the anomaly-ack write-gate (no write
  until acknowledged), and a manual convert archives stale entity CSVs exactly
  like the CLI path (a stale CSV must never ride a deliver-from-disk zip).

Runs under the autouse isolation fixture, so AppConfig + the run store land in a
per-test tmp profile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.etl.pipeline import run_pipeline
from src.history.store import read_run_records
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens.convert import convert_job
from tests.test_pipeline_run_store import _write_myedbc_input


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_myedbc_input(d)
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def _archive_dirs(output_dir: Path) -> list[Path]:
    return [p for p in output_dir.iterdir() if p.is_dir() and p.name.startswith("archive_")]


class TestRunPipelineVanish:
    def test_vanished_entity_fires_anomaly_and_stays_exit_0(self, gde_input: Path, gde_output: Path) -> None:
        # Run 1 (baseline): the full input produces the rostering CSVs.
        first = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert first.anomalies == []  # first run: no baseline → silent
        assert (gde_output / "Family.csv").exists()
        assert first.entity_counts.get("Family", 0) > 0  # guard: the baseline is non-empty

        # Run 2: the Family source file disappears → Family transforms to nothing
        # and never enters outputs (the old check was blind to exactly this).
        (gde_input / "EmergencyContactInformation.txt").unlink()
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))  # no SystemExit — exit 0 by design

        assert len(result.anomalies) == 1
        assert result.anomalies[0].startswith("ANOMALY: ")
        assert "Family produced no output this run" in result.anomalies[0]
        # The warning rides the SAME path into the run record as a >20% drop.
        records = read_run_records()
        assert records is not None and records
        assert records[0]["anomalies"] == result.anomalies
        # The stale Family.csv was archived (non-destructive) — not left to ship, not deleted.
        assert not (gde_output / "Family.csv").exists()
        archives = _archive_dirs(gde_output)
        assert len(archives) == 1
        assert (archives[0] / "Family.csv").exists()

    def test_first_run_into_an_empty_output_dir_is_silent(self, gde_input: Path, gde_output: Path) -> None:
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.anomalies == []


class TestConvertJobVanishGate:
    def _configure(self, gde_input: Path, gde_output: Path) -> None:
        AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()

    def test_vanish_needs_ack_then_ack_writes_and_archives(self, gde_input: Path, gde_output: Path) -> None:
        self._configure(gde_input, gde_output)
        first = convert_job("myedbc", str(gde_input))
        assert first.entity_counts.get("Family", 0) > 0

        (gde_input / "EmergencyContactInformation.txt").unlink()
        family_before = (gde_output / "Family.csv").read_bytes()
        students_before = (gde_output / "Students.csv").read_bytes()

        gated = convert_job("myedbc", str(gde_input))
        assert gated.status is ConvertStatus.NEEDS_ANOMALY_ACK
        assert any("Family produced no output this run" in a for a in gated.anomalies)
        # WITHOUT writing: every on-disk byte is exactly as the first run left it.
        assert (gde_output / "Family.csv").read_bytes() == family_before
        assert (gde_output / "Students.csv").read_bytes() == students_before
        assert _archive_dirs(gde_output) == []

        # Explicit acknowledgement: the write proceeds (a legit-empty source stays a
        # warning, never a failure) and the stale Family.csv is archived out of the
        # ship set — moved aside, never deleted.
        acked = convert_job("myedbc", str(gde_input), anomaly_ack=True)
        assert acked.status is not ConvertStatus.NEEDS_ANOMALY_ACK
        assert acked.entity_counts  # a committed build
        assert not (gde_output / "Family.csv").exists()
        archives = _archive_dirs(gde_output)
        assert len(archives) == 1
        assert (archives[0] / "Family.csv").read_bytes() == family_before
        # The SFTP ship set (top-level *.csv) no longer contains the stale file.
        assert "Family.csv" not in {p.name for p in gde_output.glob("*.csv")}

    def test_manual_convert_archives_stale_cross_config_csv_without_false_anomaly(
        self, gde_input: Path, gde_output: Path
    ) -> None:
        """Item 2's acceptance: a stale entity CSV in the output dir is archived
        (not deleted, not shipped) after a manual convert. CourseInfo is
        registry-known but NOT in myedbc's enabled entities, so it is archived
        WITHOUT firing a false vanish anomaly (the expected set derives from
        enabled_entities, never mappings.keys())."""
        self._configure(gde_input, gde_output)
        (gde_output / "CourseInfo.csv").write_text("stale cross-config file", encoding="utf-8")

        result = convert_job("myedbc", str(gde_input))
        assert result.status is not ConvertStatus.NEEDS_ANOMALY_ACK  # no false vanish for a disabled entity
        assert result.entity_counts.get("Students", 0) > 0
        # Not shipped from the top level, not deleted — moved into archive_<ts>/.
        assert not (gde_output / "CourseInfo.csv").exists()
        archives = _archive_dirs(gde_output)
        assert len(archives) == 1
        assert (archives[0] / "CourseInfo.csv").read_text(encoding="utf-8") == "stale cross-config file"
        assert "CourseInfo.csv" not in {p.name for p in gde_output.glob("*.csv")}
