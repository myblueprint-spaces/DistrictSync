"""The delivery-integrity gate on BOTH write-and-deliver paths — silent success made loud.

Reproduce-first (``bug`` discipline) for the two Tier-1 findings in the UNATTENDED
nightly path. Both let a broken night report success: Task Scheduler shows green,
a ``status="success"`` record lands in the run store, and Home/Run History say the
roster synced.

* **Finding 1 — zero output files.** The write/deliver block was gated on
  ``if not dry_run and outputs:``. An empty ``outputs`` set silently skipped save,
  archive AND upload, then logged "ETL process completed successfully" with every
  count at zero. The mirror case on the way IN (no usable required input) has been
  guarded fail-loud since Plan 0008 — only the way OUT was silent.
* **Finding 2 — the roster anchor vanished while dependents shipped.** With an
  empty/absent student export but a healthy timetable: ``Students`` is skipped →
  ``context.active_student_ids`` is never published → ``filter_to_active``
  deliberately no-ops (correct in isolation — see
  ``tests/test_zero_orphan_enrollments.py::TestEmptyRosterGuard``) → the previous
  ``Students.csv`` is ARCHIVED out of the SFTP glob → the run writes, delivers and
  exits 0. SpacesEDU receives enrolments referencing students it has never heard of
  — precisely the orphan class the zero-orphan invariant exists to prevent.

The gate refuses BEFORE the write, so the output directory keeps its last-good
(self-consistent) state and nothing is delivered. Both faults exit **1** via the
existing "``run_pipeline`` raises → ``main`` exits 1" wiring — no new exit code.

**FIX-2 — the second write-and-deliver path.** The gate first landed wired into
``run_pipeline`` only, so the desktop "Convert now" flow (``ui_flet.screens.convert.
convert_job``) still committed, archived the previous ``Students.csv`` out of the SFTP
glob, delivered, and recorded ``status="success"`` on exactly the payload the CLI refused
— a false green on the screen an admin opens *because* the nightly run looked wrong. The
Convert classes below drive the SAME pure gate through that path: same placement (before
``save_all``), same bounded categories, and ahead of the anomaly gate so an
acknowledgement can never buy delivery of an anchor-less roster.

**Owner decision 2026-09-28 moved both faults EARLIER.** "We don't have optional files":
a missing or row-less file a required output lists now stops the night at the INPUT gate
(``pipeline.check_required_inputs`` — ``incomplete_input``), and a required output that
comes out EMPTY stops it inside ``run_transform`` (``empty_required_output``) — so an empty
or absent student export no longer reaches this gate at all. The gate stays the FLOOR
beneath them (its pure tests below are unchanged), ``no_output`` is still reachable (a
standalone attendance config on a night with no absences — unchanged, see the class), and
every scenario below still ends the same way: nothing written, nothing sent, the last good
output untouched, exit 1 — only the category names the earlier, more precise stop.

**What must NOT regress:** the one legitimate partial run — Family's own export missing or
empty, left out with a warning — and a district config that does not enable ``Students``
at all stay exit 0, on BOTH paths.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl import pipeline
from src.etl.errors import EmptyRequiredOutputError, IncompleteInputError, NoUsableInputError, RunErrorCategory
from src.etl.outcomes import EntityOutcome, OutcomeReason
from src.etl.pipeline import run_pipeline
from src.history.store import read_run_records
from src.ui_flet.convert_output import run_identity
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens import convert as convert_screen
from src.ui_flet.screens.convert import convert_job
from tests.test_pipeline_required_input import _class_info_for_schedule

# --------------------------------------------------------------------------- #
# Input builders — minimal myedbc rostering frames (no real student data).      #
# --------------------------------------------------------------------------- #

_ANCHOR = "Students"


def _demographic(*, status: str = "Active", grades: tuple[str, str] = ("10", "12")) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Legal First Name": ["Alice", "Bob"],
            "Legal Surname": ["Smith", "Jones"],
            "Date of birth": ["2010-01-15", "2009-06-20"],
            "Grade": list(grades),
            "School Number": ["100", "100"],
            "Homeroom": ["A1", "A1"],
            "Previous school number": ["", ""],
            "Usual First Name": ["", ""],
            "Usual surname": ["", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca"],
            "Enrolment Status": [status, status],
            "Teacher Name": ["Ms. Harper", "Ms. Harper"],
            "Teacher ID": ["T001", "T001"],
        }
    )


def _schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Student ID": ["S001", "S002"],
            "School Number": ["100", "100"],
            "School Year": ["2025/2026", "2025/2026"],
            "Grade": ["10", "12"],
            "Master Timetable ID": ["MT001", "MT002"],
            "Teacher ID": ["T001", "T001"],
            "Section Letter": ["A", "A"],
            "District Course Code": ["MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y"],
            "Teacher Name": ["Harper", "Harper"],
        }
    )


def _staff() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Teacher ID": ["T001"],
            "First Name": ["Jane"],
            "Last Name": ["Harper"],
            "Email Address": ["harper@school.ca"],
            "Teaching Staff": ["Y"],
            "School Number": ["100"],
        }
    )


def _course_info() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Course Code": ["MAT10", "ENG12"],
            "Title": ["Math 10", "English 12"],
        }
    )


def _family() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    )


def _write_attendance_only_input(d: Path) -> None:
    """The ``sd51attendance`` source — a tier config with NO roster anchor.

    HEADERFUL, and named for the district's **Enhanced** export: SD51 overrides the
    base's standard headerless ``StudentPeriodAbsences.txt`` (DECISIONS 2026-09-12),
    so the base's filename-keyed ``headers:`` block does not apply to this tier and
    the file must carry its own header row. Writing the standard headerless shape
    here kept this test green against a file the district never sends.

    The DAILY band is present with no rows — a night without K-7 absences. It must be
    present: since owner decision 2026-09-28 a MISSING absence file stops the night, while a
    present one with no rows is a normal night (``outcomes.MAY_BE_EMPTY``).
    """
    (d / "StudentDailyAbsences.txt").write_bytes(b"")
    (d / "StudentPeriodAbsencesEnhanced.txt").write_text(
        "School Number,Student Number,Student Legal Last Name,Student Legal First Name,Grade,Homeroom,Teacher Name,Absence Date,Course Code,Absence Category,Absence Sub Allocation Code,Authorized Absence Code,Office Reason,Section Letter,Period Id,Teacher ID,School Course Code,Flavour,Schedule Term\n"
        "100,P1,Last,First,10,A1,Teacher,2024-09-18,MAT10,A,,,,A,1,T001,SCC,FL,S1\n"
        "100,P2,Last,First,11,A1,Teacher,19-Sep-2024,ENG11,L,,,,B,2,T002,SCC,FL,S1",
        encoding="utf-8",
    )


def _write_full_input(d: Path) -> None:
    """A complete myedbc rostering input set — the HEALTHY-run baseline."""
    _demographic().to_csv(d / "StudentDemographicInformation.txt", index=False)
    _schedule().to_csv(d / "StudentSchedule.txt", index=False)
    _staff().to_csv(d / "StaffInformationEnhanced.txt", index=False)
    _course_info().to_csv(d / "CourseInformation.txt", index=False)
    _family().to_csv(d / "EmergencyContactInformation.txt", index=False)
    _class_info_for_schedule(_schedule()).to_csv(d / "ClassInformationEnh.txt", index=False)


def _write_family_only_config() -> str:
    """A user-dir ``_base: myedbc`` district whose ONE output is Family — the only shape in which
    every configured output may be left out, so the out-gate's ``no_output`` leg is reachable."""
    import yaml as _yaml

    from src.utils.paths import user_mappings_dir

    sis = "familyonly"
    (user_mappings_dir() / f"{sis}_mapping.yaml").write_text(
        _yaml.safe_dump(
            {
                "_base": "myedbc",
                "version": "1.9",
                "sis": "MyEducationBC",
                "district_name": "Family-only test district",
                "global_config": {"enabled_entities": ["Family"]},
            }
        ),
        encoding="utf-8",
    )
    return sis


def _family_only_folder(tmp_path: Path) -> Path:
    """A contacts export with rows, none of them usable (every email blank)."""
    d = tmp_path / "family_only"
    d.mkdir()
    no_email = _family()
    no_email["Email Address"] = [""]
    no_email.to_csv(d / "EmergencyContactInformation.txt", index=False)
    return d


def _write_complete_input(d: Path, *, status: str = "Active") -> None:
    """Every file myedbc's five rostering entities list, each with its rows."""
    _demographic(status=status).to_csv(d / "StudentDemographicInformation.txt", index=False)
    _schedule().to_csv(d / "StudentSchedule.txt", index=False)
    _staff().to_csv(d / "StaffInformationEnhanced.txt", index=False)
    _course_info().to_csv(d / "CourseInformation.txt", index=False)
    _family().to_csv(d / "EmergencyContactInformation.txt", index=False)
    _class_info_for_schedule(_schedule()).to_csv(d / "ClassInformationEnh.txt", index=False)


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_full_input(d)
    return d


@pytest.fixture()
def complete_input(tmp_path: Path) -> Path:
    d = tmp_path / "complete_input"
    d.mkdir()
    _write_complete_input(d)
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


def _last_run_log(caplog: pytest.LogCaptureFixture) -> dict:
    lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
    assert lines, "expected a structured __DISTRICTSYNC_RUN__ line"
    return json.loads(lines[-1].split("__DISTRICTSYNC_RUN__ ")[1])


def _exit_code_via_main_wiring(sis: str, input_path: str, output_path: str, *extra: str) -> int:
    """Drive the REAL CLI entry point and return the exit code it produced.

    This used to re-implement ``src/main.py``'s except-block and assert the
    ``sys.exit(1)`` it had just raised itself — a tautology that would have stayed
    green if the entry point exited 0. Since the CLI became importable
    (``src.main.cli``) it is driven directly, so a regression in the entry point's
    error handling turns these red. Full contract: ``tests/test_cli_entry.py``.
    """
    from src.main import cli

    return cli(["--sis", sis, "--input", input_path, "--output", output_path, *extra])


# --------------------------------------------------------------------------- #
# The pure gate — cheap, total, and where the "do NOT regress" cases are pinned #
# --------------------------------------------------------------------------- #
class TestCheckDeliveryIntegrityPure:
    """``check_delivery_integrity`` is a pure predicate over (outputs, configured entities, outcomes)."""

    _ROSTERING = ("Students", "Staff", "Family", "Classes", "Enrollments")

    @staticmethod
    def _frame() -> pd.DataFrame:
        return pd.DataFrame({"User ID": ["S001"]})

    def _outputs(self, *names: str) -> dict[str, pd.DataFrame]:
        return {name: self._frame() for name in names}

    def test_no_outputs_at_all_is_a_fault(self) -> None:
        fault = pipeline.check_delivery_integrity({}, self._ROSTERING, outcomes=())
        assert fault is not None
        assert fault.category == RunErrorCategory.NO_OUTPUT.value

    def test_missing_anchor_with_dependents_is_a_fault(self) -> None:
        fault = pipeline.check_delivery_integrity(self._outputs("Classes", "Enrollments"), self._ROSTERING, outcomes=())
        assert fault is not None
        assert fault.category == RunErrorCategory.INCOMPLETE_ROSTER.value

    def test_healthy_full_run_is_clean(self) -> None:
        assert pipeline.check_delivery_integrity(self._outputs(*self._ROSTERING), self._ROSTERING, outcomes=()) is None

    def test_non_anchor_entity_missing_stays_clean(self) -> None:
        """Only the anchor gates HERE. (Since 2026-09-28 a CRITICAL non-anchor entity that comes
        out empty stops the night earlier, in ``run_transform``; what reaches this gate absent
        is Family, the one output that may be left out.)"""
        outputs = self._outputs("Students", "Staff", "Classes", "Enrollments")  # Family vanished
        assert pipeline.check_delivery_integrity(outputs, self._ROSTERING, outcomes=()) is None

    def test_empty_outputs_whose_every_outcome_had_nothing_to_send_is_clean(self) -> None:
        """Owner ruling 2026-09-30: an attendance-only night without absences built nothing and
        that is its whole truth — a success, never ``no_output``."""
        nothing = (EntityOutcome.empty("StudentAttendance", OutcomeReason.SOURCE_FILES_EMPTY),)
        assert pipeline.check_delivery_integrity({}, ("StudentAttendance",), outcomes=nothing) is None

    @pytest.mark.parametrize(
        "outcome",
        [
            EntityOutcome.empty("StudentAttendance", OutcomeReason.NO_ROWS_AFTER_TRANSFORM),
            EntityOutcome.empty("Family", OutcomeReason.SOURCE_FILES_EMPTY),
        ],
        ids=["attendance_rows_all_lost", "family_nothing_to_send"],
    )
    def test_twin_empty_outputs_for_any_other_reason_is_still_no_output(self, outcome: EntityOutcome) -> None:
        """The exception is exactly "every outcome a normal night allows": rows that were lost,
        or an empty entity that may not be empty-as-normal, still refuse the empty set."""
        fault = pipeline.check_delivery_integrity({}, (outcome.entity,), outcomes=(outcome,))
        assert fault is not None and fault.category == RunErrorCategory.NO_OUTPUT.value

    def test_anchor_alone_is_clean(self) -> None:
        assert pipeline.check_delivery_integrity(self._outputs("Students"), self._ROSTERING, outcomes=()) is None

    def test_anchor_not_configured_is_never_gated(self) -> None:
        """A config that does not enable Students (mbponly, sd51attendance) must not fire."""
        outputs = self._outputs("CourseInfo", "StudentCourses")
        assert pipeline.check_delivery_integrity(outputs, ("CourseInfo", "StudentCourses"), outcomes=()) is None

    def test_fault_is_raisable_and_carries_a_bounded_category(self) -> None:
        fault = pipeline.check_delivery_integrity({}, self._ROSTERING, outcomes=())
        assert isinstance(fault, pipeline.DeliveryIntegrityError)
        assert isinstance(fault, RuntimeError)  # rides the existing main.py exit-1 wiring
        assert fault.category in {c.value for c in RunErrorCategory}

    def test_fault_messages_carry_no_pii_and_no_paths(self) -> None:
        """Messages reach the console + the diagnostic log: entity names/counts ONLY."""
        for fault in (
            pipeline.check_delivery_integrity({}, self._ROSTERING, outcomes=()),
            pipeline.check_delivery_integrity(self._outputs("Classes", "Enrollments"), self._ROSTERING, outcomes=()),
        ):
            assert fault is not None
            message = str(fault)
            assert "S001" not in message  # no student identifier
            assert "/" not in message and "\\" not in message  # no filesystem path
            assert message.strip() == message and message


# --------------------------------------------------------------------------- #
# Finding 1 — a run that produces ZERO output files must not report success     #
# --------------------------------------------------------------------------- #
class TestZeroOutputFailsLoud:
    """A run that would produce nothing must never report success.

    Since owner decision 2026-09-28 the realistic shape — a complete export whose students
    are ALL Inactive — stops earlier and more precisely: Students (CRITICAL) comes out EMPTY
    and ``run_transform`` raises ``empty_required_output`` naming it, before any other entity
    runs. The gate's own ``no_output`` leg stays reachable only where every configured entity
    may be left out: a config whose one output is Family, with no usable contact. (A
    standalone attendance config whose absence files are BOTH present with no rows still
    is a success with nothing to send since the owner's ruling of 2026-09-30 —
    :class:`TestANightWithNothingToSend`.)
    """

    @staticmethod
    def _zero_output_input(tmp_path: Path) -> Path:
        d = tmp_path / "input"
        d.mkdir()
        _write_complete_input(d, status="Inactive")
        return d

    def test_run_raises_instead_of_returning_success(self, tmp_path: Path, gde_output: Path) -> None:
        with pytest.raises(EmptyRequiredOutputError) as raised:
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))
        assert raised.value.entity == _ANCHOR

    def test_main_wiring_exits_1(self, tmp_path: Path, gde_output: Path) -> None:
        """Exit 1 — the contract's existing 'ETL error' meaning; no new code invented."""
        assert _exit_code_via_main_wiring("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output)) == 1

    def test_nothing_is_written(self, tmp_path: Path, gde_output: Path) -> None:
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))
        assert list(gde_output.glob("*.csv")) == []
        assert _archive_dirs(gde_output) == []

    def test_run_record_says_failed_with_the_empty_required_output_category(
        self, tmp_path: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Home / Run History read the store — it must show the truth, not a green run."""
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.EMPTY_REQUIRED_OUTPUT.value
        assert payload["entity_outcomes"][_ANCHOR]["kind"] == "empty"
        assert payload["entity_outcomes"]["Staff"]["kind"] == "not_run", "the night stopped AT Students"

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.EMPTY_REQUIRED_OUTPUT.value

    def test_stored_record_never_carries_the_free_text_error(self, tmp_path: Path, gde_output: Path) -> None:
        """Privacy split holds: the rich message stays in the diagnostic log."""
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))
        records = read_run_records()
        assert records is not None and records
        assert not records[0].get("error")

    def test_sftp_is_never_attempted(self, tmp_path: Path, gde_output: Path, monkeypatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(pipeline, "_sftp_upload", lambda *a, **k: calls.append("called") or True)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output), sftp=True)
        assert calls == []

    def test_dry_run_also_fails_loud(self, tmp_path: Path, gde_output: Path) -> None:
        """A preview that previews nothing is just as much a lie as a live run."""
        with pytest.raises(EmptyRequiredOutputError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output), dry_run=True)

    def test_the_gates_own_no_output_leg_is_still_reachable(self, tmp_path: Path, gde_output: Path) -> None:
        """Every configured output left out (a Family-only config, no usable contact): the
        out-gate still refuses to call a night that built nothing a success."""
        d = _family_only_folder(tmp_path)
        with pytest.raises(RuntimeError, match="produced no output files") as raised:
            run_pipeline(_write_family_only_config(), str(d), str(gde_output))
        assert raised.value.category == RunErrorCategory.NO_OUTPUT.value
        records = read_run_records()
        assert records is not None and records[0]["error_category"] == RunErrorCategory.NO_OUTPUT.value
        assert records[0]["entity_outcomes"]["Family"]["kind"] == "empty"


# --------------------------------------------------------------------------- #
# Finding 2 — the roster anchor vanished, dependents must NOT ship              #
# --------------------------------------------------------------------------- #
class TestRosterAnchorVanishedFailsLoud:
    """Healthy timetable, missing student export → Classes/Enrollments would ship
    referencing students SpacesEDU has never heard of, while the previous
    ``Students.csv`` is archived out of the delivery glob.

    Since owner decision 2026-09-28 the missing export stops the night at the INPUT gate
    (``incomplete_input``, the file named) before anything is built — earlier and more
    precise than the out-gate's ``incomplete_roster``, which stays the floor beneath it.
    """

    @staticmethod
    def _baseline_then_drop_students(complete_input: Path, gde_output: Path) -> dict[str, bytes]:
        """Run a healthy night, then remove the student export. Returns the good bytes."""
        first = run_pipeline("myedbc", str(complete_input), str(gde_output))
        assert first.entity_counts.get(_ANCHOR, 0) > 0  # guard: the baseline really has a roster
        assert first.entity_counts.get("Enrollments", 0) > 0
        good = _snapshot(gde_output)
        (complete_input / "StudentDemographicInformation.txt").unlink()
        return good

    def test_run_raises_instead_of_delivering_orphans(self, complete_input: Path, gde_output: Path) -> None:
        self._baseline_then_drop_students(complete_input, gde_output)
        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("myedbc", str(complete_input), str(gde_output))
        assert raised.value.missing == ("StudentDemographicInformation.txt",)
        assert raised.value.named == ("StudentDemographicInformation.txt",)

    def test_main_wiring_exits_1(self, complete_input: Path, gde_output: Path) -> None:
        self._baseline_then_drop_students(complete_input, gde_output)
        assert _exit_code_via_main_wiring("myedbc", str(complete_input), str(gde_output)) == 1

    def test_previous_good_output_is_byte_identical_afterwards(self, complete_input: Path, gde_output: Path) -> None:
        """The refusal happens BEFORE the write — the last-good, self-consistent
        output set survives untouched (Students.csv is NOT archived away)."""
        good = self._baseline_then_drop_students(complete_input, gde_output)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(complete_input), str(gde_output))

        assert _snapshot(gde_output) == good
        assert (gde_output / f"{_ANCHOR}.csv").exists()
        assert _archive_dirs(gde_output) == []

    def test_no_orphan_enrollment_survives_on_disk(self, complete_input: Path, gde_output: Path) -> None:
        """The zero-orphan invariant, restated at the DELIVERY boundary: every
        student-role Enrollments row on disk resolves to a row in Students.csv."""
        self._baseline_then_drop_students(complete_input, gde_output)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(complete_input), str(gde_output))

        students = pd.read_csv(gde_output / f"{_ANCHOR}.csv", dtype=str)
        enrollments = pd.read_csv(gde_output / "Enrollments.csv", dtype=str)
        roster = set(students["User ID"].dropna())
        student_rows = enrollments[enrollments["Role"] == "student"]
        orphans = set(student_rows["User ID"].dropna()) - roster
        assert not orphans

    def test_sftp_is_never_attempted(self, complete_input: Path, gde_output: Path, monkeypatch) -> None:
        """Must not deliver a payload it cannot vouch for."""
        self._baseline_then_drop_students(complete_input, gde_output)
        calls: list[str] = []
        monkeypatch.setattr(pipeline, "_sftp_upload", lambda *a, **k: calls.append("called") or True)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(complete_input), str(gde_output), sftp=True)
        assert calls == []

    def test_run_record_says_failed_with_the_incomplete_input_category(
        self, complete_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._baseline_then_drop_students(complete_input, gde_output)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(complete_input), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.INCOMPLETE_INPUT.value
        # Nothing was built: every entity the missing file feeds is FAILED, the rest NOT_RUN.
        assert payload[_ANCHOR] == 0 and payload["Enrollments"] == 0
        kinds = {entity: entry["kind"] for entity, entry in payload["entity_outcomes"].items()}
        assert kinds == {
            "Students": "failed",
            "Staff": "not_run",
            "Family": "not_run",
            "Classes": "failed",
            "Enrollments": "failed",
        }

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.INCOMPLETE_INPUT.value

    def test_an_isolated_entity_failure_never_masks_the_stop(
        self, complete_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """Plan 0053 S4's floor is still not weakened by the bulkhead: with the student export
        gone AND Family's transform planted to raise, the input gate stops the night before any
        transform runs — Family is never reached (NOT_RUN), the previous output untouched."""
        from src.etl.transformer import DataTransformer

        good = self._baseline_then_drop_students(complete_input, gde_output)
        original = DataTransformer.transform

        def _family_raises(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
            if entity == "Family":
                raise RuntimeError("planted Family fault")
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _family_raises)
        with pytest.raises(IncompleteInputError):
            run_pipeline("myedbc", str(complete_input), str(gde_output))
        assert _snapshot(gde_output) == good
        records = read_run_records()
        assert records is not None
        assert records[0]["error_category"] == RunErrorCategory.INCOMPLETE_INPUT.value
        assert records[0]["entity_outcomes"]["Family"]["kind"] == "not_run"

    def test_first_ever_run_without_a_student_export_also_fails(self, tmp_path: Path, gde_output: Path) -> None:
        """No baseline on disk at all — still a refusal (the fault is the payload,
        not the comparison against a previous run)."""
        d = tmp_path / "input"
        d.mkdir()
        _write_complete_input(d)
        (d / "StudentDemographicInformation.txt").unlink()

        with pytest.raises(IncompleteInputError):
            run_pipeline("myedbc", str(d), str(gde_output))
        assert list(gde_output.glob("*.csv")) == []


# --------------------------------------------------------------------------- #
# The same floor, reached from a CONFIG mistake: a grade scope no student holds  #
# --------------------------------------------------------------------------- #
class TestStudentRosteringGradesMatchingNobodyFailsLoud:
    """`global_config.student_rostering_grades` (plan 0042 slice 1b) narrows the
    roster. A VALID list that happens to match no student in this run's export
    empties ``Students`` — and ``filter_to_active`` then correctly no-ops on the
    empty roster, so every other entity would ship UNFILTERED. That is the
    mass-orphan delivery this floor used to catch; pinned here rather than
    trusted, because it is the whole reason slice 1b needs no floor of its own.

    Since owner decision 2026-09-28 it is caught EARLIER: Students is a required output that
    came out EMPTY, so ``run_transform`` stops the night AT Students
    (``empty_required_output``, Students EMPTY on the record, every later entity NOT_RUN) —
    the other entities are never built, so nothing unfiltered could ship even in principle.
    ``incomplete_roster`` stays the floor beneath it.
    """

    SIS = "srg_nobody"

    @staticmethod
    def _write_config() -> None:
        """A `_base: myedbc` district scoped to a grade its export does not carry.

        The demographic fixture is grades 10 + 12; the scope is K-08 (a superset
        of base `myedbc`'s homeroom_grades, so the subset chain is satisfied and
        the config is genuinely VALID — the fault has to be caught at RUN time).
        """
        import yaml as _yaml

        from src.utils.paths import user_mappings_dir

        (user_mappings_dir() / f"{TestStudentRosteringGradesMatchingNobodyFailsLoud.SIS}_mapping.yaml").write_text(
            _yaml.safe_dump(
                {
                    "_base": "myedbc",
                    "version": "1.11",
                    "sis": "MyEducationBC",
                    "district_name": "Grade-scoped test district",
                    "global_config": {
                        "student_rostering_grades": [
                            "IT",
                            "PR",
                            "PK",
                            "TK",
                            "KG",
                            "01",
                            "02",
                            "03",
                            "04",
                            "05",
                            "06",
                            "07",
                            "08",
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_the_config_itself_is_VALID_so_the_floor_is_what_catches_it(self) -> None:
        """The positive twin: load-time validation passes, which is precisely why
        a runtime floor is still required."""
        from src.config.loader import load_config

        self._write_config()
        assert load_config(self.SIS).global_config.student_rostering_grades is not None

    def test_run_stops_at_the_empty_student_list_rather_than_delivering(
        self, complete_input: Path, gde_output: Path
    ) -> None:
        self._write_config()
        with pytest.raises(EmptyRequiredOutputError) as excinfo:
            run_pipeline(self.SIS, str(complete_input), str(gde_output))
        assert excinfo.value.entity == _ANCHOR
        assert excinfo.value.category == RunErrorCategory.EMPTY_REQUIRED_OUTPUT.value

    def test_nothing_is_written_and_nothing_is_archived(self, complete_input: Path, gde_output: Path) -> None:
        """The refusal precedes BOTH ``save_all`` and ``archive_stale_outputs``,
        so a previous good delivery is neither overwritten nor moved out of the
        SFTP glob."""
        run_pipeline("myedbc", str(complete_input), str(gde_output))
        good = _snapshot(gde_output)
        assert good, "the baseline run must really have written files"

        self._write_config()
        with pytest.raises(RuntimeError):
            run_pipeline(self.SIS, str(complete_input), str(gde_output))

        assert _snapshot(gde_output) == good
        assert _archive_dirs(gde_output) == []

    def test_the_night_stops_at_students_before_anything_else_is_built(
        self, complete_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-vacuity: the stop fires because the student list came out EMPTY for a grade
        scope nobody holds — Students EMPTY/``no_rows_after_transform`` on the record — and
        every later entity is NOT_RUN, so nothing unfiltered was even built."""
        self._write_config()
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline(self.SIS, str(complete_input), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.EMPTY_REQUIRED_OUTPUT.value
        outcomes = payload["entity_outcomes"]
        # EMPTY for "the export had rows and none survived" (refined to missing_source_column by the
        # observation, which also sees an unrelated mapped column absent) — never "nothing to send"
        assert outcomes[_ANCHOR]["kind"] == "empty"
        assert outcomes[_ANCHOR]["reason"] in {"no_rows_after_transform", "missing_source_column"}
        assert {outcomes[e]["kind"] for e in ("Staff", "Family", "Classes", "Enrollments")} == {"not_run"}

    def test_a_scope_that_DOES_match_delivers_normally(self, complete_input: Path, gde_output: Path) -> None:
        """The positive twin for the whole class: the key is not simply fatal —
        a scope covering the export's grades ships, narrowed."""
        import yaml as _yaml

        from src.utils.paths import user_mappings_dir

        (user_mappings_dir() / "srg_matching_mapping.yaml").write_text(
            _yaml.safe_dump(
                {
                    "_base": "myedbc",
                    "version": "1.11",
                    "sis": "MyEducationBC",
                    "district_name": "Grade-scoped test district (matching)",
                    "global_config": {"homeroom_grades": [], "student_rostering_grades": ["10"]},
                }
            ),
            encoding="utf-8",
        )
        result = run_pipeline("srg_matching", str(complete_input), str(gde_output))

        assert result.entity_counts.get(_ANCHOR, 0) == 1  # the grade-10 student only
        students = pd.read_csv(gde_output / f"{_ANCHOR}.csv", dtype=str)
        assert set(students["Grade"]) == {"10"}


# --------------------------------------------------------------------------- #
# Regression fence — legitimate partial runs MUST stay exit 0                   #
# --------------------------------------------------------------------------- #
class TestLegitimatePartialRunsStayGreen:
    def test_healthy_full_run_is_unchanged(self, gde_input: Path, gde_output: Path) -> None:
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.entity_counts.get(_ANCHOR, 0) > 0
        assert (gde_output / f"{_ANCHOR}.csv").exists()

    def test_non_anchor_entity_vanishing_stays_a_warning(self, gde_input: Path, gde_output: Path) -> None:
        """Family is the ONE output that may be left out (owner 2026-09-28). A vanished Family
        is an ANOMALY warning + a stale-CSV archive, NOT a failure."""
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        (gde_input / "EmergencyContactInformation.txt").unlink()

        result = run_pipeline("myedbc", str(gde_input), str(gde_output))  # must not raise

        assert any("Family produced no output this run" in a for a in result.anomalies)
        assert result.entity_counts.get(_ANCHOR, 0) > 0

    def test_an_isolated_entity_failure_passes_the_gate_when_the_anchor_built(
        self, gde_input: Path, gde_output: Path, monkeypatch
    ) -> None:
        """Plan 0053 S4: a Family whose transform RAISES is left out, not fatal — the anchor
        built, so the delivery-integrity gate is clean and the run completes (exit 0)."""
        from src.etl.transformer import DataTransformer

        original = DataTransformer.transform

        def _family_raises(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
            if entity == "Family":
                raise RuntimeError("planted Family fault")
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _family_raises)
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))  # must not raise

        assert result.entity_counts.get(_ANCHOR, 0) > 0
        assert "Family" not in result.entity_counts
        assert not (gde_output / "Family.csv").exists()
        assert _exit_code_via_main_wiring("myedbc", str(gde_input), str(gde_output)) == 0

    def test_an_anchor_only_folder_now_stops_the_night(self, tmp_path: Path, gde_output: Path) -> None:
        """Only the demographic export arrived. It used to ship Students alone (exit 0); since
        owner decision 2026-09-28 every file a required output lists must be there, so the
        night stops at the input gate, naming each missing file — never the family contacts
        one — with nothing written. Its twin is the healthy full run above."""
        d = tmp_path / "input"
        d.mkdir()
        _demographic().to_csv(d / "StudentDemographicInformation.txt", index=False)

        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("myedbc", str(d), str(gde_output))

        assert "EmergencyContactInformation.txt" not in raised.value.missing
        assert "StudentSchedule.txt" in raised.value.missing
        assert list(gde_output.glob("*.csv")) == []
        assert _exit_code_via_main_wiring("myedbc", str(d), str(gde_output)) == 1

    def test_config_without_the_anchor_stays_green(self, tmp_path: Path, gde_output: Path) -> None:
        """``sd51attendance`` does not enable Students at all — the gate must not
        fire on a config whose configured entity set has no roster anchor."""
        d = tmp_path / "input"
        d.mkdir()
        _write_attendance_only_input(d)

        result = run_pipeline("sd51attendance", str(d), str(gde_output))

        assert result.entity_counts.get("StudentAttendance", 0) > 0


# --------------------------------------------------------------------------- #
# FIX-2 — the SAME gate on the desktop "Convert now" path (``convert_job``)     #
# --------------------------------------------------------------------------- #
class TestConvertPathRefusesAnOrphanRoster:
    """The manual Convert path must refuse exactly what the CLI refuses.

    Reproduce-first: ``check_delivery_integrity`` landed wired into ``run_pipeline``
    only. ``convert_job`` — the desktop "Convert now" flow — went straight from
    ``run_transform`` to its ``if not outputs`` / anomaly checks, neither of which
    fires when Classes/Enrollments/Family build and ``Students`` does not. It then
    committed, archived the previous ``Students.csv`` out of the SFTP glob, delivered,
    and wrote a ``status="success"`` run record — a FALSE GREEN on the very screen an
    admin opens *because* the nightly run looked wrong.

    Same placement as the CLI (before ``save_all``), same bounded categories, and —
    critically — the refusal sits BEFORE the anomaly gate, so an "I've reviewed this"
    acknowledgement can never buy delivery of an anchor-less payload.
    """

    @staticmethod
    def _configure(input_dir: Path, output_dir: Path, sis: str = "myedbc") -> None:
        AppConfig(input_dir=str(input_dir), output_dir=str(output_dir), sis_type=sis).save()

    @staticmethod
    def _anchorless_input(tmp_path: Path) -> Path:
        """A healthy timetable with NO student export — the orphan-payload shape."""
        d = tmp_path / "anchorless"
        d.mkdir()
        _schedule().to_csv(d / "StudentSchedule.txt", index=False)
        _staff().to_csv(d / "StaffInformationEnhanced.txt", index=False)
        _course_info().to_csv(d / "CourseInformation.txt", index=False)
        _family().to_csv(d / "EmergencyContactInformation.txt", index=False)
        return d

    @staticmethod
    def _success_records() -> list[dict]:
        return [r for r in (read_run_records() or []) if r.get("status") == "success"]

    def test_fresh_output_dir_with_no_anomaly_at_all_is_still_refused(self, tmp_path: Path, gde_output: Path) -> None:
        """The exact reproduction: a FIRST run into a clean folder raises no anomaly, so the
        anomaly write-gate never fires. Since owner decision 2026-09-28 the INPUT gate stands
        first: the missing student export stops the conversion typed, before anything is built,
        and the error reaches the screen's ``on_error`` card."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)

        with pytest.raises(IncompleteInputError) as raised:
            convert_job("myedbc", str(anchorless))

        assert "StudentDemographicInformation.txt" in raised.value.missing
        # Nothing committed — the refusal precedes save_all/archive/upload.
        assert list(gde_output.glob("*.csv")) == []
        assert _archive_dirs(gde_output) == []
        # The ledger must not claim a healthy night.
        assert self._success_records() == []

    def test_previous_good_output_survives_byte_identical(self, complete_input: Path, gde_output: Path) -> None:
        """The recoverability argument for refusing BEFORE the write: the last-good,
        self-consistent output set is left untouched — ``Students.csv`` is NOT moved
        into ``archive_<ts>/`` where a delivery could never pick it up again."""
        self._configure(complete_input, gde_output)
        first = convert_job("myedbc", str(complete_input))
        assert first.entity_counts.get(_ANCHOR, 0) > 0
        good = _snapshot(gde_output)

        (complete_input / "StudentDemographicInformation.txt").unlink()
        with pytest.raises(IncompleteInputError):
            convert_job("myedbc", str(complete_input))

        assert _snapshot(gde_output) == good
        assert (gde_output / f"{_ANCHOR}.csv").exists()
        assert _archive_dirs(gde_output) == []

    def test_an_anomaly_acknowledgement_cannot_buy_delivery(self, complete_input: Path, gde_output: Path) -> None:
        """The input gate outranks the anomaly ack: 'I've reviewed the smaller files' is
        consent about SIZE, never consent to ship a set with a required file missing."""
        self._configure(complete_input, gde_output)
        convert_job("myedbc", str(complete_input))
        good = _snapshot(gde_output)
        (complete_input / "StudentDemographicInformation.txt").unlink()

        with pytest.raises(IncompleteInputError):
            convert_job("myedbc", str(complete_input), anomaly_ack=run_identity("myedbc", str(complete_input)))

        assert _snapshot(gde_output) == good
        assert _archive_dirs(gde_output) == []

    def test_sftp_is_never_attempted(self, tmp_path: Path, gde_output: Path, monkeypatch) -> None:
        """A payload the gate cannot vouch for must not reach the network."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)
        monkeypatch.setattr(
            convert_screen,
            "SFTPUploader",
            lambda **_kw: pytest.fail("SFTPUploader must not be constructed for a refused payload"),
        )

        with pytest.raises(IncompleteInputError):
            convert_job("myedbc", str(anchorless), sftp_requested=True)

    def test_run_record_says_failed_with_the_incomplete_input_category(self, tmp_path: Path, gde_output: Path) -> None:
        """Run History + Home read the store — a refusal must read as a failure there,
        carrying the BOUNDED category only (the privacy split is unchanged)."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)

        with pytest.raises(IncompleteInputError):
            convert_job("myedbc", str(anchorless))

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.INCOMPLETE_INPUT.value
        assert records[0]["source"] == "manual"
        assert records[0][_ANCHOR] == 0
        assert records[0]["entity_outcomes"][_ANCHOR]["reason"] == "missing_source_file"
        assert not records[0].get("error")  # no free-text error in the store

    def test_an_empty_student_list_stops_convert_as_it_stops_the_cli(self, tmp_path: Path, gde_output: Path) -> None:
        """Every student Inactive: Students comes out EMPTY and the conversion stops at it —
        the same ``empty_required_output`` the CLI records for the same folder."""
        d = tmp_path / "inactive"
        d.mkdir()
        _write_complete_input(d, status="Inactive")
        self._configure(d, gde_output)

        with pytest.raises(EmptyRequiredOutputError) as raised:
            convert_job("myedbc", str(d))

        assert raised.value.entity == _ANCHOR
        assert list(gde_output.glob("*.csv")) == []
        assert read_run_records()[0]["error_category"] == RunErrorCategory.EMPTY_REQUIRED_OUTPUT.value

    def test_no_output_at_all_keeps_its_own_category(self, tmp_path: Path, gde_output: Path) -> None:
        """The gate's other leg still maps to the existing NO_OUTPUT status/copy —
        one gate, two bounded faults, no re-implementation on either path. Reached by a
        config whose one output is Family, with no usable contact."""
        d = _family_only_folder(tmp_path)
        sis = _write_family_only_config()
        self._configure(d, gde_output, sis)

        result = convert_job(sis, str(d))

        assert result.status is ConvertStatus.NO_OUTPUT
        assert list(gde_output.glob("*.csv")) == []


class TestConvertPathTierConfigsAreNeverFalsePositives:
    """A tier config that produces no ``Students`` BY DESIGN must convert normally.

    The gate keys off the CONFIGURED entity set (``configured_entity_order``, derived
    from ``enabled_entities``) — never the registry or ``mappings.keys()`` — so
    ``mbponly`` / ``sd51attendance`` never see it fire.
    """

    @staticmethod
    def _configure(input_dir: Path, output_dir: Path, sis: str) -> None:
        AppConfig(input_dir=str(input_dir), output_dir=str(output_dir), sis_type=sis).save()

    def test_mbponly_converts_without_a_roster_anchor(self, gde_output: Path) -> None:
        mbp_input = Path(__file__).parent / "snapshots" / "mbp_input"
        self._configure(mbp_input, gde_output, "mbponly")

        result = convert_job("mbponly", str(mbp_input))

        assert result.status is not ConvertStatus.INCOMPLETE_ROSTER
        assert result.entity_counts.get("CourseInfo", 0) > 0
        assert result.entity_counts.get("StudentCourses", 0) > 0
        assert (gde_output / "CourseInfo.csv").exists()
        assert (gde_output / "StudentCourses.csv").exists()
        assert not (gde_output / f"{_ANCHOR}.csv").exists()

    def test_sd51attendance_converts_without_a_roster_anchor(self, tmp_path: Path, gde_output: Path) -> None:
        d = tmp_path / "attendance"
        d.mkdir()
        _write_attendance_only_input(d)
        self._configure(d, gde_output, "sd51attendance")

        result = convert_job("sd51attendance", str(d))

        assert result.status is not ConvertStatus.INCOMPLETE_ROSTER
        assert result.entity_counts.get("StudentAttendance", 0) > 0
        assert (gde_output / "StudentAttendance.csv").exists()


# --------------------------------------------------------------------------- #
# Owner ruling 2026-09-30 — an attendance-only night with no absences          #
# --------------------------------------------------------------------------- #
#: The header row of SD51's Enhanced period-absence export (the file carries its own header).
_PERIOD_ENHANCED_HEADER = (
    "School Number,Student Number,Student Legal Last Name,Student Legal First Name,Grade,Homeroom,Teacher Name,"
    "Absence Date,Course Code,Absence Category,Absence Sub Allocation Code,Authorized Absence Code,Office Reason,"
    "Section Letter,Period Id,Teacher ID,School Course Code,Flavour,Schedule Term\n"
)


def _no_absences_folder(tmp_path: Path, name: str = "no_absences") -> Path:
    """``sd51attendance``'s two absence files PRESENT, each with no data rows — a night without
    absences (the headerless daily file empty, the Enhanced period file header-only)."""
    d = tmp_path / name
    d.mkdir()
    (d / "StudentDailyAbsences.txt").write_bytes(b"")
    (d / "StudentPeriodAbsencesEnhanced.txt").write_text(_PERIOD_ENHANCED_HEADER, encoding="utf-8")
    return d


class TestANightWithNothingToSend:
    """An attendance-only config whose absence files are all PRESENT but row-less: SUCCESS, exit 0,
    StudentAttendance EMPTY (``source_files_empty`` — a normal night), NOTHING written, archived or
    uploaded, Home green with a truthful detail (owner ruling 2026-09-30). It used to fail
    ``no_input``. The twins: a MISSING absence file still stops the night, and a rostering config
    whose files are all row-less still fails ``no_input``."""

    def _previous_night(self, tmp_path: Path, gde_output: Path) -> dict[str, bytes]:
        """A night WITH absences first, so there is a last output a quiet night must not touch."""
        d = tmp_path / "with_absences"
        d.mkdir()
        _write_attendance_only_input(d)
        run_pipeline("sd51attendance", str(d), str(gde_output))
        before = _snapshot(gde_output)
        assert "StudentAttendance.csv" in before, "guard: the previous night wrote its file"
        return before

    def test_it_is_a_success_that_writes_archives_and_sends_nothing(
        self, tmp_path: Path, gde_output: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        before = self._previous_night(tmp_path, gde_output)
        uploads: list[str] = []
        monkeypatch.setattr(pipeline, "_sftp_upload", lambda *a, **k: uploads.append("called") or True)

        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            result = run_pipeline("sd51attendance", str(_no_absences_folder(tmp_path)), str(gde_output), sftp=True)

        assert result.entity_counts == {}
        assert result.sftp_attempted is False and uploads == [], "nothing to send is never an upload"
        assert _snapshot(gde_output) == before, "the last output is untouched"
        assert _archive_dirs(gde_output) == [], "nothing archived either"
        assert any(r.getMessage() == pipeline.NOTHING_TO_SEND_LOG_LINE for r in caplog.records)
        record = (read_run_records() or [])[0]
        assert (record["status"], record["error_category"]) == ("success", RunErrorCategory.NONE.value)
        entry = record["entity_outcomes"]["StudentAttendance"]
        assert (entry["kind"], entry["reason"]) == ("empty", OutcomeReason.SOURCE_FILES_EMPTY.value)

    def test_main_wiring_exits_0(self, tmp_path: Path, gde_output: Path) -> None:
        assert _exit_code_via_main_wiring("sd51attendance", str(_no_absences_folder(tmp_path)), str(gde_output)) == 0

    def test_home_and_run_history_are_green_and_say_nothing_was_sent(self, tmp_path: Path, gde_output: Path) -> None:
        from src.ui_flet.failure_copy import nothing_to_send_detail
        from src.ui_flet.home_status import derive_home_status
        from src.ui_flet.run_history import derive_history_banner
        from src.ui_flet.verdict import Verdict

        run_pipeline("sd51attendance", str(_no_absences_folder(tmp_path)), str(gde_output))
        records = read_run_records() or []
        cfg = AppConfig(sis_type="sd51attendance", setup_completed=True)
        sentence = nothing_to_send_detail((EntityOutcome.empty("StudentAttendance", OutcomeReason.SOURCE_FILES_EMPTY),))
        assert sentence == "No absences were recorded, so there was nothing to send — nothing was written or delivered."

        home = derive_home_status(records, cfg, store_created_at=records[0]["timestamp"])
        assert home.verdict is Verdict.HEALTHY
        assert home.detail.endswith(sentence) and "written to your output folder" not in home.detail
        banner = derive_history_banner(records, cfg, store_created_at=records[0]["timestamp"])
        assert banner.verdict is Verdict.HEALTHY and banner.detail.endswith(sentence)

    def test_a_confirmed_live_schedule_says_syncing_over_the_same_nothing_to_send_detail(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        """The nothing-to-send branch keeps Home's headline rule: "syncing" on a CONFIRMED-LIVE
        schedule read-back only — and the detail is still the nothing-to-send sentence."""
        from src.ui_flet.failure_copy import nothing_to_send_detail
        from src.ui_flet.home_status import derive_home_status
        from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
        from src.ui_flet.verdict import Verdict

        run_pipeline("sd51attendance", str(_no_absences_folder(tmp_path)), str(gde_output))
        records = read_run_records() or []
        cfg = AppConfig(sis_type="sd51attendance", setup_completed=True)
        sentence = nothing_to_send_detail((EntityOutcome.empty("StudentAttendance", OutcomeReason.SOURCE_FILES_EMPTY),))
        live = ScheduleStatus(
            state=ScheduleState.LIVE, headline="Nightly sync is scheduled", detail="", next_run_display="3:00 AM"
        )

        home = derive_home_status(records, cfg, store_created_at=records[0]["timestamp"], schedule_status=live)
        assert home.verdict is Verdict.HEALTHY
        assert home.headline == "Your roster is syncing"
        assert home.detail.endswith(sentence)
        # the twin: without a confirmed-live read-back the same record keeps "up to date"
        plain = derive_home_status(records, cfg, store_created_at=records[0]["timestamp"])
        assert plain.headline == "Your roster is up to date" and plain.detail == home.detail

    def test_twin_a_night_with_absences_still_says_its_files_were_written(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        """The detail is the nothing-to-send night's alone: a night that built its file keeps the
        ordinary "files were written" line."""
        from src.ui_flet.home_status import derive_home_status

        self._previous_night(tmp_path, gde_output)
        records = read_run_records() or []
        cfg = AppConfig(sis_type="sd51attendance", setup_completed=True)
        home = derive_home_status(records, cfg, store_created_at=records[0]["timestamp"])
        assert "written to your output folder" in home.detail and "nothing to send" not in home.detail

    def test_convert_says_nothing_to_send_and_records_one_success(self, tmp_path: Path, gde_output: Path) -> None:
        from src.ui_flet.convert_result import summarize
        from src.ui_flet.verdict import Verdict

        before = self._previous_night(tmp_path, gde_output)
        d = _no_absences_folder(tmp_path)
        AppConfig(input_dir=str(d), output_dir=str(gde_output), sis_type="sd51attendance").save()
        count_before = len(read_run_records() or [])

        result = convert_job("sd51attendance", str(d))

        assert result.status is ConvertStatus.NOTHING_TO_SEND
        verdict, headline, detail = summarize(result)
        assert (verdict, headline) == (Verdict.HEALTHY, "Nothing to send")
        assert detail.startswith("No absences were recorded")
        assert _snapshot(gde_output) == before and _archive_dirs(gde_output) == []
        records = read_run_records() or []
        assert len(records) == count_before + 1, "exactly one record"
        assert (records[0]["source"], records[0]["status"]) == ("manual", "success")

    def test_twin_a_missing_absence_file_still_stops_the_night(self, tmp_path: Path, gde_output: Path) -> None:
        """The exception needs EVERY listed file on disk: with one missing and the other row-less
        nothing usable arrived (``no_input``); with the other carrying rows, the input gate names
        the missing one (``incomplete_input``). Either way the night stops, nothing written."""
        d = _no_absences_folder(tmp_path)
        (d / "StudentDailyAbsences.txt").unlink()
        with pytest.raises(NoUsableInputError):
            run_pipeline("sd51attendance", str(d), str(gde_output))

        with_rows = tmp_path / "period_rows_daily_missing"
        with_rows.mkdir()
        _write_attendance_only_input(with_rows)
        (with_rows / "StudentDailyAbsences.txt").unlink()
        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("sd51attendance", str(with_rows), str(gde_output))
        assert list(raised.value.missing) == ["StudentDailyAbsences.txt"]
        assert list(gde_output.glob("*.csv")) == []

    def test_twin_a_rostering_folder_of_row_less_files_is_still_no_input(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        """Every myedbc file present, header-only: a roster may not arrive empty, so the
        exception never reaches it — still ``no_input``."""
        d = tmp_path / "rostering_empty"
        d.mkdir()
        _write_complete_input(d)
        for path in d.iterdir():
            pd.read_csv(path, dtype=str).head(0).to_csv(path, index=False)
        with pytest.raises(NoUsableInputError):
            run_pipeline("myedbc", str(d), str(gde_output))

    def test_twin_a_rostering_night_with_an_empty_demographic_still_stops(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        d = tmp_path / "empty_demographic"
        d.mkdir()
        _write_complete_input(d)
        demographic = d / "StudentDemographicInformation.txt"
        pd.read_csv(demographic, dtype=str).head(0).to_csv(demographic, index=False)
        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("myedbc", str(d), str(gde_output))
        assert "StudentDemographicInformation.txt" in raised.value.empty
