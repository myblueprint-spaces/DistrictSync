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
from src.ui_flet.convert_output import run_identity
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
        #
        # DELIBERATELY STRENGTHENED (FIX-2): this line used to read ``anomaly_ack=True``.
        # A bare boolean is exactly the permissive contract the ack-identity defect rested
        # on — any later run could spend it. The ack is now a ``RunIdentity`` token that
        # authorizes only the run it names; passing the identity of THIS run keeps the
        # original assertion (an acknowledged vanish writes + archives) intact while
        # closing the "which run did I approve?" hole. See
        # ``test_ack_from_one_run_never_authorizes_a_different_run`` for the inverse.
        acked = convert_job("myedbc", str(gde_input), anomaly_ack=run_identity("myedbc", str(gde_input)))
        assert acked.status is not ConvertStatus.NEEDS_ANOMALY_ACK
        assert acked.entity_counts  # a committed build
        assert not (gde_output / "Family.csv").exists()
        archives = _archive_dirs(gde_output)
        assert len(archives) == 1
        assert (archives[0] / "Family.csv").read_bytes() == family_before
        # The SFTP ship set (top-level *.csv) no longer contains the stale file.
        assert "Family.csv" not in {p.name for p in gde_output.glob("*.csv")}

    def test_ack_from_one_run_never_authorizes_a_different_run(
        self, gde_input: Path, gde_output: Path, tmp_path: Path
    ) -> None:
        """FIX-2: the acknowledgement is bound to the RUN IDENTITY it reviewed.

        Reproduce-first. The ack card used to take a snapshotted ``(district, input_dir)``
        and never use it: ``_on_ack`` called ``_start_convert(anomaly_ack=True)``, which
        re-read the district dropdown and the input picker FRESH — and those controls were
        re-enabled by ``_set_running(False)`` while the card was still on screen. So the
        admin could review folder A's "some files look much smaller than usual", repoint
        the picker at folder B, click "I've reviewed this — convert anyway", and B's write
        proceeded on A's review. The anomaly gate is the last safety net between a
        truncated export and a collapsed roster reaching SpacesEDU; an ack that can approve
        a run nobody looked at is not a gate.

        The write-gate now takes a ``RunIdentity`` token, not a bool, and honours it only
        when it matches the run actually executing — so an unreviewed run is refused at the
        gate itself, not merely hidden by the view.
        """
        self._configure(gde_input, gde_output)
        convert_job("myedbc", str(gde_input))  # baseline in the shared output folder

        # A SECOND input folder the admin could repoint the picker at mid-review.
        other_input = tmp_path / "input_b"
        other_input.mkdir()
        _write_myedbc_input(other_input)
        (other_input / "EmergencyContactInformation.txt").unlink()  # B is anomalous too — never reviewed

        # Folder A raises the ack card.
        (gde_input / "EmergencyContactInformation.txt").unlink()
        gated = convert_job("myedbc", str(gde_input))
        assert gated.status is ConvertStatus.NEEDS_ANOMALY_ACK
        reviewed = run_identity("myedbc", str(gde_input))
        family_before = (gde_output / "Family.csv").read_bytes()

        # The admin repoints to B, then clicks the ack: A's approval, B's run.
        acked_wrong_run = convert_job("myedbc", str(other_input), anomaly_ack=reviewed)

        assert acked_wrong_run.status is ConvertStatus.NEEDS_ANOMALY_ACK  # B was never reviewed
        assert (gde_output / "Family.csv").read_bytes() == family_before  # nothing written under A's ack
        assert _archive_dirs(gde_output) == []

        # The SAME token does authorize the run it actually reviewed.
        acked_right_run = convert_job("myedbc", str(gde_input), anomaly_ack=reviewed)
        assert acked_right_run.status is not ConvertStatus.NEEDS_ANOMALY_ACK
        assert acked_right_run.entity_counts

    def test_ack_for_a_different_district_never_authorizes_this_run(self, gde_input: Path, gde_output: Path) -> None:
        """The district axis of the same binding — the dropdown was equally editable."""
        self._configure(gde_input, gde_output)
        convert_job("myedbc", str(gde_input))
        (gde_input / "EmergencyContactInformation.txt").unlink()
        assert convert_job("myedbc", str(gde_input)).status is ConvertStatus.NEEDS_ANOMALY_ACK
        family_before = (gde_output / "Family.csv").read_bytes()

        # Reviewed under sd48myedbc, clicked while the dropdown reads myedbc.
        stale = run_identity("sd48myedbc", str(gde_input))
        result = convert_job("myedbc", str(gde_input), anomaly_ack=stale)

        assert result.status is ConvertStatus.NEEDS_ANOMALY_ACK
        assert (gde_output / "Family.csv").read_bytes() == family_before

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


class TestConvertLeavesAFailedEntityOut:
    """Plan 0053 S4 on the Convert path: Unity's contacts export flips to the PLAIN report.

    Family is ISOLATABLE, so the conversion COMPLETES without it — and because a previous
    ``Family.csv`` sits in the output folder, the vanished-entity anomaly still fires and the
    ack gate still asks before anything is written (``compute_anomalies`` is unchanged). The
    prompt now says WHY the file is missing, so "convert anyway" is consent to a known cause.
    """

    _SIS = "unitychristianmyedbc"

    def test_the_gate_still_asks_names_the_reason_and_the_ack_writes_a_partial_run(self, tmp_path: Path) -> None:
        from src.etl.outcomes import EntityOutcome, OutcomeKind, OutcomeReason
        from src.ui_flet.convert_result import summarize
        from src.ui_flet.verdict import Verdict
        from tests.test_contract import (
            _create_unitychristian_inputs,
            _create_unitychristian_plain_report_inputs,
        )

        enhanced, plain, out = tmp_path / "enhanced", tmp_path / "plain", tmp_path / "out"
        for d in (enhanced, plain, out):
            d.mkdir()
        _create_unitychristian_inputs(enhanced)
        _create_unitychristian_plain_report_inputs(plain)
        AppConfig(input_dir=str(enhanced), output_dir=str(out), sis_type=self._SIS).save()

        first = convert_job(self._SIS, str(enhanced))
        assert first.entity_counts.get("Family", 0) > 0
        family_before = (out / "Family.csv").read_bytes()

        gated = convert_job(self._SIS, str(plain))
        assert gated.status is ConvertStatus.NEEDS_ANOMALY_ACK
        assert any("Family produced no output this run" in a for a in gated.anomalies)
        assert gated.entity_outcomes is not None
        # Plan 0053 S6: the FAILED outcome also carries what the source observation saw (the plain
        # report lacks both columns Family maps from it); kind and reason are the bulkhead's.
        assert (
            EntityOutcome(
                "Family",
                OutcomeKind.FAILED,
                OutcomeReason.MISSING_SOURCE_COLUMN,
                0,
                ("Email Address", "Parent Auth / Guardian"),
                ("Parent Auth / Guardian",),
                "EmergencyContactInformation.txt",
            )
            in gated.entity_outcomes
        )
        _verdict, _headline, prompt = summarize(gated)
        assert "Family contacts were left out of this sync" in prompt
        # Plan 0053 S7: the prompt names the file and the column, as the district's mapping spells them.
        assert (
            "their export file, EmergencyContactInformation.txt, is missing the column "
            "“Parent Auth / Guardian”, which this district's mapping needs"
        ) in prompt
        assert (out / "Family.csv").read_bytes() == family_before, "nothing is written before the ack"

        acked = convert_job(self._SIS, str(plain), anomaly_ack=run_identity(self._SIS, str(plain)))
        assert acked.status is ConvertStatus.DELIVERED
        verdict, headline, _detail = summarize(acked)
        assert verdict is Verdict.WARNING and headline == "Your sync completed without family contacts"
        assert not (out / "Family.csv").exists()
        archives = _archive_dirs(out)
        assert len(archives) == 1 and (archives[0] / "Family.csv").read_bytes() == family_before
        records = read_run_records()
        assert records is not None and records[0]["source"] == "manual"
        assert records[0]["status"] == "success"
        assert records[0]["entity_outcomes"]["Family"]["kind"] == "failed"
