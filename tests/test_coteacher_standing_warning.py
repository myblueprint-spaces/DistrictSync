"""Co-teachers left out: a standing WARNING, never silent, never a failure (plan 0053 S10).

Owner ruling 2026-09-25 (``docs/claugentic-DECISIONS.md``; ``failure-policy.md`` §5 #15, §7):
when a present, non-empty ClassInformation lacks a column the co-teacher rows are linked by,
Enrollments ships WITHOUT those rows and the run carries ``OutcomeNote.COTEACHER_SOURCE_UNUSABLE``
on the Enrollments outcome — which Home and Run History show as a standing amber every night it
persists. What is pinned here, each with the twin that proves the mechanism fires:

* **The tier** — ``failure_copy.NOTE_TIER`` is TOTAL over ``OutcomeNote``; ``outcome_tier`` raises
  a BUILT outcome carrying a WARNING note to WARNING (a note never lowers a tier), and
  ``warning_outcomes`` — still the ONE PARTIAL predicate — selects it.
* **The copy** — a single noted outcome words its own headline (never "left out" for a file that
  was delivered), the note's sentence and next step; beside a file that WAS left out, that file's
  copy leads and the note follows; Run History's suffix never counts the built file as skipped.
  Every note has copy (TOTAL), and no copy carries a slot.
* **End to end, two nights** — an SD60-shaped drop (ClassInformation with no primary-teacher
  column) and an SD40-shaped one (ClassInformation with no Master Timetable ID while blended
  classes exist, through the bundled ``sd40myedbc`` and its declared session components): each
  BUILDS Enrollments without the co-teacher rows, records the note, and is PARTIAL / WARNING on
  night 1 AND night 2 with the note's headline and Run History label. Twins: the same SD60 drop
  WITH the columns ships the co-teacher rows and is CLEAN with no note; the bundled SD60 fixture,
  whose ClassInformation is EMPTY, records no note (nothing to use).

All data is synthetic (the ``tests/test_contract.py`` builders plus the frames written here).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl.outcomes import (
    EntityOutcome,
    OutcomeKind,
    OutcomeNote,
    OutcomeReason,
    outcomes_from_record,
    outcomes_to_record,
)
from src.etl.pipeline import run_pipeline
from src.history.store import read_run_records
from src.ui_flet import failure_copy
from src.ui_flet.convert_result import ConvertResult, ConvertStatus, summarize
from src.ui_flet.failure_copy import (
    NOTE_TIER,
    OUTCOME_TIER,
    note_sentence,
    outcome_tier,
    partial_copy,
    partial_label,
    warning_outcomes,
)
from src.ui_flet.home_status import LatestReason, build_record_for, classify_latest_reason, derive_home_status
from src.ui_flet.run_history import derive_history_banner, to_run_row
from src.ui_flet.verdict import Verdict
from tests.test_contract import _create_sd40_inputs, _create_sd60_inputs

_NOTE = OutcomeNote.COTEACHER_SOURCE_UNUSABLE
_HEADLINE_NOT_DELIVERED = "Your sync completed without some co-teachers"
_HEADLINE_DELIVERED = "Your roster synced without some co-teachers"
_LABEL = "co-teachers left out"
#: A co-teacher only ClassInformation names — the schedule never does, so a row for it can only
#: have come from the co-teacher path.
_COTEACHER_ID = "T777"


def _built(entity: str = "Enrollments", *, notes: tuple = ()) -> EntityOutcome:
    return EntityOutcome.built(entity, 7, notes=notes)


# --------------------------------------------------------------------------- #
# The tier                                                                      #
# --------------------------------------------------------------------------- #
class TestTheNoteTier:
    def test_note_tier_is_total_over_the_enum(self) -> None:
        assert set(NOTE_TIER) == set(OutcomeNote)

    def test_the_coteacher_note_is_a_warning(self) -> None:
        assert NOTE_TIER[_NOTE] is Verdict.WARNING

    def test_a_built_outcome_with_the_note_warns_and_its_twin_without_is_healthy(self) -> None:
        assert outcome_tier(_built(notes=((_NOTE, 3),))) is Verdict.WARNING
        assert outcome_tier(_built()) is Verdict.HEALTHY

    def test_a_note_never_lowers_a_tier(self) -> None:
        """NOT_RUN is FAILED and carries no note; an EMPTY warning stays a warning with one."""
        empty = EntityOutcome.empty("Enrollments", OutcomeReason.NO_ROWS_AFTER_TRANSFORM, notes=((_NOTE, 1),))
        assert outcome_tier(empty) is Verdict.WARNING
        assert outcome_tier(EntityOutcome.not_run("Enrollments")) is Verdict.FAILED

    def test_warning_outcomes_is_still_the_one_predicate_and_selects_the_noted_build(self) -> None:
        noted = _built(notes=((_NOTE, 3),))
        assert warning_outcomes([_built("Students"), noted, _built("Classes")]) == (noted,)
        assert warning_outcomes([_built("Students"), _built()]) == ()

    def test_the_kind_reason_tier_is_untouched(self) -> None:
        """OUTCOME_TIER keeps its (entity, kind, reason) signature and its BUILT → HEALTHY row."""
        assert OUTCOME_TIER("Enrollments", OutcomeKind.BUILT, OutcomeReason.NONE) is Verdict.HEALTHY


# --------------------------------------------------------------------------- #
# The copy                                                                      #
# --------------------------------------------------------------------------- #
class TestTheCopy:
    def test_every_note_has_copy_and_no_copy_carries_a_slot(self) -> None:
        assert set(failure_copy._NOTE_COPY) == set(OutcomeNote)
        for note, copy in failure_copy._NOTE_COPY.items():
            assert set(copy) == {"phrase", "label", "sentence", "next_step"}, note
            assert all("{" not in text and "}" not in text for text in copy.values()), note
            assert note_sentence(note) == copy["sentence"]

    def test_a_single_noted_outcome_words_its_own_headline_never_left_out(self) -> None:
        for delivered, headline in ((False, _HEADLINE_NOT_DELIVERED), (True, _HEADLINE_DELIVERED)):
            got_headline, detail = partial_copy([_built(notes=((_NOTE, 3),))], delivered=delivered)
            assert got_headline == headline
            assert detail.startswith(note_sentence(_NOTE))
            assert ("Everything else was delivered." if delivered else "Everything else completed.") in detail
            assert detail.endswith(failure_copy._NOTE_COPY[_NOTE]["next_step"])
            assert "left out of this sync" not in detail and "enrollments were not built" not in detail.lower()

    def test_beside_a_left_out_file_that_file_leads_and_the_note_follows(self) -> None:
        family = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        without_note = partial_copy([family], delivered=False)
        headline, detail = partial_copy([family, _built(notes=((_NOTE, 3),))], delivered=False)
        assert headline == without_note[0] == "Your sync completed without family contacts"
        assert detail.startswith(without_note[1])
        assert note_sentence(_NOTE) in detail

    def test_the_run_history_suffix_never_counts_the_built_file_as_skipped(self) -> None:
        family = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        noted = _built(notes=((_NOTE, 3),))
        assert partial_label([noted]) == _LABEL
        assert partial_label([family]) == "1 file skipped"
        assert partial_label([family, noted]) == f"1 file skipped · {_LABEL}"
        with pytest.raises(ValueError):
            partial_label([])

    def test_a_built_outcome_without_a_warning_note_is_never_worded_as_partial(self) -> None:
        """A caller bug (the selection did not come from ``warning_outcomes``) raises rather
        than wording a green outcome as a warning — its twin, the noted build, words above."""
        with pytest.raises(ValueError, match="no outcome here warns"):
            partial_copy([_built()], delivered=False)
        with pytest.raises(ValueError, match="no outcome here warns"):
            partial_label([_built()])

    def test_convert_words_it_exactly_as_home_does(self) -> None:
        noted = _built(notes=((_NOTE, 3),))
        result = ConvertResult(
            status=ConvertStatus.DELIVERED,
            entity_counts={"Enrollments": 7},
            entity_outcomes=(_built("Students"), noted),
            sftp_attempted=False,
            sftp_ok=False,
            delivery_requested=False,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert (headline, detail) == partial_copy([noted], delivered=False)


# --------------------------------------------------------------------------- #
# A note never hides an anomaly (review finding B1)                             #
# --------------------------------------------------------------------------- #
_ANOMALIES = ["Staff dropped 35% compared to the previous run"]


def _record(*outcomes: EntityOutcome, anomalies: list[str] | None = None) -> dict:
    return {
        "status": "success",
        "sftp_attempted": False,
        "sftp_ok": False,
        "anomalies": list(anomalies or []),
        "entity_outcomes": outcomes_to_record(outcomes),
    }


class TestTheNoteNeverHidesAnAnomaly:
    """A note-only PARTIAL (every warning outcome BUILT) ranks BELOW an anomaly — its file was
    delivered, so on SD40/SD60, where the note stands every night, it would otherwise hide every
    one-night drop for good. A FAILED or EMPTY file keeps PARTIAL above the anomaly (P7)."""

    def test_a_noted_build_with_an_anomaly_classifies_anomaly(self) -> None:
        record = _record(_built("Students"), _built(notes=((_NOTE, 3),)), anomalies=_ANOMALIES)
        assert classify_latest_reason(record, prior_build=None) is LatestReason.ANOMALY

    def test_twin_the_same_anomaly_beside_a_failed_file_stays_partial(self) -> None:
        failed = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        record = _record(_built("Students"), _built(notes=((_NOTE, 3),)), failed, anomalies=_ANOMALIES)
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL

    def test_twin_the_same_anomaly_beside_a_warning_empty_file_stays_partial(self) -> None:
        empty = EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM)
        record = _record(_built("Students"), _built(notes=((_NOTE, 3),)), empty, anomalies=_ANOMALIES)
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL

    def test_twin_the_noted_build_without_an_anomaly_is_still_partial(self) -> None:
        record = _record(_built("Students"), _built(notes=((_NOTE, 3),)))
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL

    def test_home_names_the_anomaly_not_the_note_on_that_night(self) -> None:
        noted = _record(_built("Students"), _built(notes=((_NOTE, 3),)), anomalies=_ANOMALIES)
        plain = _record(_built("Students"), _built(), anomalies=_ANOMALIES)
        assert classify_latest_reason(noted, prior_build=None) is classify_latest_reason(plain, prior_build=None)


# --------------------------------------------------------------------------- #
# End to end — two nights                                                       #
# --------------------------------------------------------------------------- #
def _latest() -> tuple[dict, dict | None, list[dict]]:
    records = read_run_records()
    assert records
    return records[0], build_record_for(records, 0), records


def _enrollments_entry(record: dict) -> dict:
    return record["entity_outcomes"]["Enrollments"]


def _assert_partial_with_the_note(record: dict, prior: dict | None, records: list[dict], sis: str) -> None:
    entry = _enrollments_entry(record)
    assert (entry["kind"], entry["reason"]) == ("built", "none")
    assert set(entry["notes"]) == {_NOTE.value} and entry["notes"][_NOTE.value] >= 1
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.PARTIAL
    cfg = AppConfig(sis_type=sis, setup_completed=True)
    home = derive_home_status(records, cfg, store_created_at=record["timestamp"])
    assert home.verdict is Verdict.WARNING
    assert home.headline == _HEADLINE_NOT_DELIVERED
    banner = derive_history_banner(records, cfg, store_created_at=record["timestamp"])
    assert (banner.verdict, banner.headline) == (Verdict.WARNING, _HEADLINE_NOT_DELIVERED)
    assert to_run_row(record, prior_build=prior).status_label == f"Completed · {_LABEL}"
    assert [o.entity for o in warning_outcomes(outcomes_from_record(record))] == ["Enrollments"]


def _sd60_class_info(d: Path, *, with_coteacher_columns: bool) -> None:
    """SD60's REAL ClassInfo shape has no primary-teacher flag; the twin adds it (and the section)."""
    frame = {
        "School Number": ["100"],
        "Teacher ID": [_COTEACHER_ID],
        "Master Timetable ID": ["MT001"],
        "Term": ["T1"],
        "Semester": ["S1"],
        "Day": ["1"],
        "Period": ["1"],
    }
    if with_coteacher_columns:
        frame["Primary Teacher"] = ["Y"]
        frame["Section Letter"] = ["A1"]  # school 100's homeroom A1 (S001) — Path 1 links it
    pd.DataFrame(frame).to_csv(d / "Spaces_ClassInfo.txt", index=False)


def _coteacher_rows(out: Path) -> pd.DataFrame:
    enrollments = pd.read_csv(out / "Enrollments.csv", dtype=str, encoding="utf-8-sig")
    return enrollments[enrollments["User ID"] == _COTEACHER_ID]


@pytest.mark.integration
def test_an_sd60_shaped_drop_without_the_primary_teacher_flag_is_partial_two_nights_running(tmp_path: Path) -> None:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd60_inputs(inp)
    _sd60_class_info(inp, with_coteacher_columns=False)

    for _night in (1, 2):
        run_pipeline("sd60myedbc", str(inp), str(out))
        record, prior, records = _latest()
        assert record["status"] == "success"
        assert _enrollments_entry(record)["notes"] == {_NOTE.value: 1}  # the one ClassInfo row
        _assert_partial_with_the_note(record, prior, records, "sd60myedbc")
        assert _coteacher_rows(out).empty, "a co-teacher row shipped from an unusable source"
    # StudentAttendance still built beside it — the note never stops the run.
    assert record["entity_outcomes"]["StudentAttendance"]["kind"] == "built"


@pytest.mark.integration
def test_twin_the_same_sd60_drop_with_the_columns_ships_the_coteacher_and_is_clean(tmp_path: Path) -> None:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd60_inputs(inp)
    _sd60_class_info(inp, with_coteacher_columns=True)
    run_pipeline("sd60myedbc", str(inp), str(out))
    record, prior, _ = _latest()
    assert "notes" not in _enrollments_entry(record)
    assert set(_coteacher_rows(out)["Class ID"]) == {"100_A1_2026"}
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.CLEAN


@pytest.mark.integration
def test_twin_an_empty_class_information_records_no_note(tmp_path: Path) -> None:
    """The bundled SD60 fixture's ClassInfo is EMPTY: there is no co-teacher source to have been
    unusable, so no note and no amber."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd60_inputs(inp)
    run_pipeline("sd60myedbc", str(inp), str(out))
    record, prior, _ = _latest()
    assert "notes" not in _enrollments_entry(record)
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.CLEAN


def _sd40_with_a_blend(d: Path) -> None:
    """The SD40 fixture plus ONE blend: S003 (grade 12) in MT004, taught by T003 in MT002's slot.

    Its ClassInformation has no Master Timetable ID (the real SD40 shape), so detection runs on
    the term-less schedule — through ``sd40myedbc``'s declared session components — and the
    co-teacher Path 2 has blends to link but no column to link them by.
    """
    _create_sd40_inputs(d)
    schedule = d / "SD-40_StudentSchedule.csv"
    frame = pd.read_csv(schedule, header=None, dtype=str, keep_default_na=False)
    blend_row = frame.iloc[2].copy()  # S003's grade-12 row: school 200, T004, period 3, MT003
    blend_row[5], blend_row[7], blend_row[8] = "C4", "MAT12", "MAT12"
    blend_row[11], blend_row[15], blend_row[16] = "2", "MT004", "T003"  # MT002's slot + teacher
    blend_row[17] = "Liu"
    pd.concat([frame, blend_row.to_frame().T], ignore_index=True).to_csv(schedule, index=False, header=False)
    class_info = pd.read_csv(d / "SD-40_ClassInformation.csv", dtype=str)
    class_info["Teacher Id"] = [_COTEACHER_ID]
    class_info.to_csv(d / "SD-40_ClassInformation.csv", index=False)


@pytest.mark.integration
def test_an_sd40_shaped_drop_without_a_master_timetable_id_is_partial_two_nights_running(tmp_path: Path) -> None:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _sd40_with_a_blend(inp)

    for _night in (1, 2):
        run_pipeline("sd40myedbc", str(inp), str(out))
        record, prior, records = _latest()
        assert record["status"] == "success", "the declared session components keep SD40 building"
        classes = pd.read_csv(out / "Classes.csv", dtype=str, encoding="utf-8-sig")
        assert classes["Class ID"].str.startswith("BLENDED_").any(), "the fixture must really carry a blend"
        _assert_partial_with_the_note(record, prior, records, "sd40myedbc")
        assert _coteacher_rows(out).empty, "a blended co-teacher shipped without its linking column"
