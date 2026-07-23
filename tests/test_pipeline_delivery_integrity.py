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

**What must NOT regress:** per-entity skip-on-empty stays legitimate (CLAUDE.md →
Exit codes). A partial run — a NON-anchor entity empty/vanished, a district config
that does not enable ``Students`` at all — stays exit 0 with a warning, on BOTH paths.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl import pipeline
from src.etl.pipeline import RunErrorCategory, run_pipeline
from src.history.store import read_run_records
from src.ui_flet.convert_output import run_identity
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens import convert as convert_screen
from src.ui_flet.screens.convert import convert_job

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
    """The headerless ``sd51attendance`` source — a tier config with NO roster anchor."""
    (d / "StudentPeriodAbsences.txt").write_text(
        "100,P1,Last,First,10,A1,Teacher,2024-09-18,MAT10,A,,,MT001,A,T001,SCC,FL\n"
        "100,P2,Last,First,11,A1,Teacher,19-Sep-2024,ENG11,L,,,MT002,B,T002,SCC,FL",
        encoding="utf-8",
    )


def _write_full_input(d: Path) -> None:
    """A complete myedbc rostering input set — the HEALTHY-run baseline."""
    _demographic().to_csv(d / "StudentDemographicInformation.txt", index=False)
    _schedule().to_csv(d / "StudentSchedule.txt", index=False)
    _staff().to_csv(d / "StaffInformationEnhanced.txt", index=False)
    _course_info().to_csv(d / "CourseInformation.txt", index=False)
    _family().to_csv(d / "EmergencyContactInformation.txt", index=False)
    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(d / "ClassInformationEnh.txt", index=False)


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_full_input(d)
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
    """``check_delivery_integrity`` is a pure predicate over (outputs, configured entities)."""

    _ROSTERING = ("Students", "Staff", "Family", "Classes", "Enrollments")

    @staticmethod
    def _frame() -> pd.DataFrame:
        return pd.DataFrame({"User ID": ["S001"]})

    def _outputs(self, *names: str) -> dict[str, pd.DataFrame]:
        return {name: self._frame() for name in names}

    def test_no_outputs_at_all_is_a_fault(self) -> None:
        fault = pipeline.check_delivery_integrity({}, self._ROSTERING)
        assert fault is not None
        assert fault.category == RunErrorCategory.NO_OUTPUT.value

    def test_missing_anchor_with_dependents_is_a_fault(self) -> None:
        fault = pipeline.check_delivery_integrity(self._outputs("Classes", "Enrollments"), self._ROSTERING)
        assert fault is not None
        assert fault.category == RunErrorCategory.INCOMPLETE_ROSTER.value

    def test_healthy_full_run_is_clean(self) -> None:
        assert pipeline.check_delivery_integrity(self._outputs(*self._ROSTERING), self._ROSTERING) is None

    def test_non_anchor_entity_missing_stays_clean(self) -> None:
        """Per-entity skip-on-empty is legitimate BY DESIGN — only the anchor gates."""
        outputs = self._outputs("Students", "Staff", "Classes", "Enrollments")  # Family vanished
        assert pipeline.check_delivery_integrity(outputs, self._ROSTERING) is None

    def test_anchor_alone_is_clean(self) -> None:
        assert pipeline.check_delivery_integrity(self._outputs("Students"), self._ROSTERING) is None

    def test_anchor_not_configured_is_never_gated(self) -> None:
        """A config that does not enable Students (mbponly, sd51attendance) must not fire."""
        outputs = self._outputs("CourseInfo", "StudentCourses")
        assert pipeline.check_delivery_integrity(outputs, ("CourseInfo", "StudentCourses")) is None

    def test_fault_is_raisable_and_carries_a_bounded_category(self) -> None:
        fault = pipeline.check_delivery_integrity({}, self._ROSTERING)
        assert isinstance(fault, pipeline.DeliveryIntegrityError)
        assert isinstance(fault, RuntimeError)  # rides the existing main.py exit-1 wiring
        assert fault.category in {c.value for c in RunErrorCategory}

    def test_fault_messages_carry_no_pii_and_no_paths(self) -> None:
        """Messages reach the console + the diagnostic log: entity names/counts ONLY."""
        for fault in (
            pipeline.check_delivery_integrity({}, self._ROSTERING),
            pipeline.check_delivery_integrity(self._outputs("Classes", "Enrollments"), self._ROSTERING),
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
    """Input loads, but every entity is empty or skipped → no CSVs at all.

    Built from a demographic-only input whose students are all Inactive in
    NON-homeroom grades: the no-usable-input guard does not fire (the frame is
    non-empty), Students transforms to zero rows, and every other entity's source
    is absent — so ``outputs`` is ``{}``.
    """

    @staticmethod
    def _zero_output_input(tmp_path: Path) -> Path:
        d = tmp_path / "input"
        d.mkdir()
        _demographic(status="Inactive").to_csv(d / "StudentDemographicInformation.txt", index=False)
        return d

    def test_run_raises_instead_of_returning_success(self, tmp_path: Path, gde_output: Path) -> None:
        """FAILS on the unfixed code: run_pipeline returned a PipelineResult with
        empty counts and logged 'ETL process completed successfully'."""
        with pytest.raises(RuntimeError, match="produced no output files"):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))

    def test_main_wiring_exits_1(self, tmp_path: Path, gde_output: Path) -> None:
        """Exit 1 — the contract's existing 'ETL error' meaning; no new code invented."""
        assert _exit_code_via_main_wiring("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output)) == 1

    def test_nothing_is_written(self, tmp_path: Path, gde_output: Path) -> None:
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))
        assert list(gde_output.glob("*.csv")) == []
        assert _archive_dirs(gde_output) == []

    def test_run_record_says_failed_with_the_no_output_category(
        self, tmp_path: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Home / Run History read the store — it must show the truth, not a green run."""
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.NO_OUTPUT.value

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.NO_OUTPUT.value

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
        with pytest.raises(RuntimeError, match="produced no output files"):
            run_pipeline("myedbc", str(self._zero_output_input(tmp_path)), str(gde_output), dry_run=True)


# --------------------------------------------------------------------------- #
# Finding 2 — the roster anchor vanished, dependents must NOT ship              #
# --------------------------------------------------------------------------- #
class TestRosterAnchorVanishedFailsLoud:
    """Healthy timetable, missing student export → Classes/Enrollments would ship
    referencing students SpacesEDU has never heard of, while the previous
    ``Students.csv`` is archived out of the delivery glob.
    """

    @staticmethod
    def _baseline_then_drop_students(gde_input: Path, gde_output: Path) -> dict[str, bytes]:
        """Run a healthy night, then remove the student export. Returns the good bytes."""
        first = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert first.entity_counts.get(_ANCHOR, 0) > 0  # guard: the baseline really has a roster
        assert first.entity_counts.get("Enrollments", 0) > 0
        good = _snapshot(gde_output)
        (gde_input / "StudentDemographicInformation.txt").unlink()
        return good

    def test_run_raises_instead_of_delivering_orphans(self, gde_input: Path, gde_output: Path) -> None:
        """FAILS on the unfixed code: the second run returned exit-0 success with
        Classes + Enrollments written and Students.csv archived away."""
        self._baseline_then_drop_students(gde_input, gde_output)
        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

    def test_main_wiring_exits_1(self, gde_input: Path, gde_output: Path) -> None:
        self._baseline_then_drop_students(gde_input, gde_output)
        assert _exit_code_via_main_wiring("myedbc", str(gde_input), str(gde_output)) == 1

    def test_previous_good_output_is_byte_identical_afterwards(self, gde_input: Path, gde_output: Path) -> None:
        """The refusal happens BEFORE the write — the last-good, self-consistent
        output set survives untouched (Students.csv is NOT archived away)."""
        good = self._baseline_then_drop_students(gde_input, gde_output)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        assert _snapshot(gde_output) == good
        assert (gde_output / f"{_ANCHOR}.csv").exists()
        assert _archive_dirs(gde_output) == []

    def test_no_orphan_enrollment_survives_on_disk(self, gde_input: Path, gde_output: Path) -> None:
        """The zero-orphan invariant, restated at the DELIVERY boundary: every
        student-role Enrollments row on disk resolves to a row in Students.csv."""
        self._baseline_then_drop_students(gde_input, gde_output)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        students = pd.read_csv(gde_output / f"{_ANCHOR}.csv", dtype=str)
        enrollments = pd.read_csv(gde_output / "Enrollments.csv", dtype=str)
        roster = set(students["User ID"].dropna())
        student_rows = enrollments[enrollments["Role"] == "student"]
        orphans = set(student_rows["User ID"].dropna()) - roster
        assert not orphans

    def test_sftp_is_never_attempted(self, gde_input: Path, gde_output: Path, monkeypatch) -> None:
        """Must not deliver a payload it cannot vouch for."""
        self._baseline_then_drop_students(gde_input, gde_output)
        calls: list[str] = []
        monkeypatch.setattr(pipeline, "_sftp_upload", lambda *a, **k: calls.append("called") or True)
        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output), sftp=True)
        assert calls == []

    def test_run_record_says_failed_with_the_incomplete_roster_category(
        self, gde_input: Path, gde_output: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._baseline_then_drop_students(gde_input, gde_output)
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(gde_input), str(gde_output))

        payload = _last_run_log(caplog)
        assert payload["status"] == "failed"
        assert payload["error_category"] == RunErrorCategory.INCOMPLETE_ROSTER.value
        # The counts stay honest: what WAS built is reported, the anchor is 0.
        assert payload[_ANCHOR] == 0
        assert payload["Enrollments"] > 0

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.INCOMPLETE_ROSTER.value

    def test_first_ever_run_without_a_student_export_also_fails(self, tmp_path: Path, gde_output: Path) -> None:
        """No baseline on disk at all — still a refusal (the fault is the payload,
        not the comparison against a previous run)."""
        d = tmp_path / "input"
        d.mkdir()
        _schedule().to_csv(d / "StudentSchedule.txt", index=False)
        _staff().to_csv(d / "StaffInformationEnhanced.txt", index=False)
        _course_info().to_csv(d / "CourseInformation.txt", index=False)

        with pytest.raises(RuntimeError, match=_ANCHOR):
            run_pipeline("myedbc", str(d), str(gde_output))
        assert list(gde_output.glob("*.csv")) == []


# --------------------------------------------------------------------------- #
# Regression fence — legitimate partial runs MUST stay exit 0                   #
# --------------------------------------------------------------------------- #
class TestLegitimatePartialRunsStayGreen:
    def test_healthy_full_run_is_unchanged(self, gde_input: Path, gde_output: Path) -> None:
        result = run_pipeline("myedbc", str(gde_input), str(gde_output))
        assert result.entity_counts.get(_ANCHOR, 0) > 0
        assert (gde_output / f"{_ANCHOR}.csv").exists()

    def test_non_anchor_entity_vanishing_stays_a_warning(self, gde_input: Path, gde_output: Path) -> None:
        """CLAUDE.md: per-entity skip-on-empty is legitimate. A vanished Family is
        an ANOMALY warning + a stale-CSV archive, NOT a failure."""
        run_pipeline("myedbc", str(gde_input), str(gde_output))
        (gde_input / "EmergencyContactInformation.txt").unlink()

        result = run_pipeline("myedbc", str(gde_input), str(gde_output))  # must not raise

        assert any("Family produced no output this run" in a for a in result.anomalies)
        assert result.entity_counts.get(_ANCHOR, 0) > 0

    def test_anchor_only_run_stays_green(self, tmp_path: Path, gde_output: Path) -> None:
        """Only the demographic export arrived — Students ships, the rest skip. Exit 0."""
        d = tmp_path / "input"
        d.mkdir()
        _demographic().to_csv(d / "StudentDemographicInformation.txt", index=False)

        result = run_pipeline("myedbc", str(d), str(gde_output))

        assert result.entity_counts.get(_ANCHOR, 0) > 0
        assert (gde_output / f"{_ANCHOR}.csv").exists()

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
        """The exact reproduction: a FIRST run into a clean folder raises no anomaly,
        so the anomaly write-gate never fires — only the integrity gate stands between
        a zero-student payload and SpacesEDU."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)

        result = convert_job("myedbc", str(anchorless))

        # Nothing committed — the refusal precedes save_all/archive/upload.
        assert list(gde_output.glob("*.csv")) == []
        assert _archive_dirs(gde_output) == []
        # The ledger must not claim a healthy night.
        assert self._success_records() == []
        # And the admin is told which fault it was, by category.
        assert result.status is ConvertStatus.INCOMPLETE_ROSTER
        assert result.entity_counts.get("Enrollments", 0) > 0  # honest: what WAS built is reported
        assert result.entity_counts.get(_ANCHOR, 0) == 0

    def test_previous_good_output_survives_byte_identical(self, gde_input: Path, gde_output: Path) -> None:
        """The recoverability argument for refusing BEFORE the write: the last-good,
        self-consistent output set is left untouched — ``Students.csv`` is NOT moved
        into ``archive_<ts>/`` where a delivery could never pick it up again."""
        self._configure(gde_input, gde_output)
        first = convert_job("myedbc", str(gde_input))
        assert first.entity_counts.get(_ANCHOR, 0) > 0
        good = _snapshot(gde_output)

        (gde_input / "StudentDemographicInformation.txt").unlink()
        result = convert_job("myedbc", str(gde_input))

        assert _snapshot(gde_output) == good
        assert (gde_output / f"{_ANCHOR}.csv").exists()
        assert _archive_dirs(gde_output) == []
        assert result.status is ConvertStatus.INCOMPLETE_ROSTER

    def test_an_anomaly_acknowledgement_cannot_buy_delivery(self, gde_input: Path, gde_output: Path) -> None:
        """The integrity gate outranks the anomaly ack: 'I've reviewed the smaller
        files' is consent about SIZE, never consent to ship enrolments for students
        that will not be delivered."""
        self._configure(gde_input, gde_output)
        convert_job("myedbc", str(gde_input))
        good = _snapshot(gde_output)
        (gde_input / "StudentDemographicInformation.txt").unlink()

        result = convert_job("myedbc", str(gde_input), anomaly_ack=run_identity("myedbc", str(gde_input)))

        assert _snapshot(gde_output) == good
        assert _archive_dirs(gde_output) == []
        assert result.status is ConvertStatus.INCOMPLETE_ROSTER

    def test_sftp_is_never_attempted(self, tmp_path: Path, gde_output: Path, monkeypatch) -> None:
        """A payload the gate cannot vouch for must not reach the network."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)
        monkeypatch.setattr(
            convert_screen,
            "SFTPUploader",
            lambda **_kw: pytest.fail("SFTPUploader must not be constructed for a refused payload"),
        )

        result = convert_job("myedbc", str(anchorless), sftp_requested=True)

        assert result.status is ConvertStatus.INCOMPLETE_ROSTER
        assert result.sftp_attempted is False

    def test_run_record_says_failed_with_the_incomplete_roster_category(self, tmp_path: Path, gde_output: Path) -> None:
        """Run History + Home read the store — a refusal must read as a failure there,
        carrying the BOUNDED category only (the privacy split is unchanged)."""
        anchorless = self._anchorless_input(tmp_path)
        self._configure(anchorless, gde_output)

        convert_job("myedbc", str(anchorless))

        records = read_run_records()
        assert records is not None and records
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == RunErrorCategory.INCOMPLETE_ROSTER.value
        assert records[0]["source"] == "manual"
        assert records[0][_ANCHOR] == 0
        assert records[0]["Enrollments"] > 0
        assert not records[0].get("error")  # no free-text error in the store

    def test_no_output_at_all_keeps_its_own_category(self, tmp_path: Path, gde_output: Path) -> None:
        """The gate's other leg still maps to the existing NO_OUTPUT status/copy —
        one gate, two bounded faults, no re-implementation on either path."""
        d = tmp_path / "zero"
        d.mkdir()
        _demographic(status="Inactive").to_csv(d / "StudentDemographicInformation.txt", index=False)
        self._configure(d, gde_output)

        result = convert_job("myedbc", str(d))

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
