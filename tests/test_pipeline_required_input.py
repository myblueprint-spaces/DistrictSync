"""The two input boundaries of a run: no usable input at all, then every REQUIRED file.

A scheduled, unattended run that received no usable required input (wrong
folder, truncated export, locked file) must fail loudly — not masquerade as a
clean run. The first guard keys off INPUT presence (``raw_data`` right after
``load_data``); the second (owner decision 2026-09-28, ``pipeline.check_required_inputs``)
requires every file a CRITICAL entity's mapping lists — "we don't have optional files" —
so:

  (a) every required file missing/empty  → ``NoUsableInputError`` (``no_input``) + a
      failed run-log → main wiring exits 1 — except an attendance-only config whose files are
      all PRESENT with no rows, a night with nothing to send (owner ruling 2026-09-30; pinned in
      ``tests/test_pipeline_delivery_integrity.py::TestANightWithNothingToSend``);
  (b) SOME required file missing, or present with no data rows → ``IncompleteInputError``
      (``incomplete_input``, exit 1), naming the files, the stopped entities recorded
      FAILED/``missing_source_file`` and nothing written — on BOTH entry points (AST-pinned);
  (c) the exceptions come from the declarations: Family's own contacts file may be missing
      or empty (Family is only left out); an attendance file may be PRESENT with no rows (a
      night without absences) but never missing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.etl.errors import EmptyRequiredOutputError, IncompleteInputError, NoUsableInputError, RunErrorCategory
from src.etl.outcomes import EntityOutcome, OutcomeKind, OutcomeLedger, OutcomeReason
from src.etl.pipeline import check_required_inputs, configured_entity_order, run_pipeline, run_transform
from src.history.store import read_run_records

# ---------------------------------------------------------------------------
# Required-input GDE columns (minimal, mirrors test_sftp_exit.py)
# ---------------------------------------------------------------------------


def _class_info_for_schedule(schedule: pd.DataFrame) -> pd.DataFrame:
    """A COMPLETE ClassInformation export for ``schedule``: one row per section (owner ruling 2026-09-30).

    The input gate stops a night whose ClassInformation is present with no data rows (owner
    decision 2026-09-28), so these synthetic inputs ship what a real export carries, consistent
    with the schedule: each section's school, course code, primary teacher (flag ``Y``), Master
    Timetable ID, section letter and the four time-slot columns the base mapping's blended
    detection keys on (every co-teacher column too, so no co-teacher note is recorded). Each
    section gets its OWN period, so no two share a teacher + time slot and no blend forms — these
    fixtures put one teacher across grades 10/12, which would otherwise blend. The outputs are
    therefore exactly what the schedule alone decides. ``tests/test_contract.py::_write_class_info``
    is the bundled-config fixtures' counterpart.
    """
    sections = schedule[
        ["School Number", "District Course Code", "Teacher ID", "Master Timetable ID", "Section Letter"]
    ].drop_duplicates()
    return pd.DataFrame(
        {
            "School Number": list(sections["School Number"]),
            "Course Code": list(sections["District Course Code"]),
            "Teacher ID": list(sections["Teacher ID"]),
            "Master Timetable ID": list(sections["Master Timetable ID"]),
            "Section Letter": list(sections["Section Letter"]),
            "Primary Teacher": ["Y"] * len(sections),
            "Term": ["T1"] * len(sections),
            "Semester": ["S1"] * len(sections),
            "Day": ["1"] * len(sections),
            "Period": [str(slot) for slot in range(1, len(sections) + 1)],
        }
    )


def _write_class_info_rows(d: Path) -> None:
    """Write ``d``'s ClassInformation from the ``StudentSchedule.txt`` already written there."""
    schedule = pd.read_csv(d / "StudentSchedule.txt", dtype=str, keep_default_na=False)
    _class_info_for_schedule(schedule).to_csv(d / "ClassInformationEnh.txt", index=False)


def _write_full_rostering_input(d: Path) -> None:
    """Write a complete myedbc rostering input set to ``d`` — every file the five rostering
    entities list, each with its rows (the input gate requires them — owner 2026-09-28)."""
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Legal First Name": ["Alice", "Bob"],
            "Legal Surname": ["Smith", "Jones"],
            "Date of birth": ["2010-01-15", "2009-06-20"],
            "Grade": ["10", "12"],
            "School Number": ["100", "100"],
            "Homeroom": ["A1", "A1"],
            "Previous school number": ["", ""],
            "Usual First Name": ["", ""],
            "Usual surname": ["", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca"],
            "Enrolment Status": ["Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Ms. Harper"],
            "Teacher ID": ["T001", "T001"],
        }
    ).to_csv(d / "StudentDemographicInformation.txt", index=False)

    pd.DataFrame(
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
    ).to_csv(d / "StudentSchedule.txt", index=False)

    pd.DataFrame(
        {
            "Teacher ID": ["T001"],
            "First Name": ["Jane"],
            "Last Name": ["Harper"],
            "Email Address": ["harper@school.ca"],
            "Teaching Staff": ["Y"],
            "School Number": ["100"],
        }
    ).to_csv(d / "StaffInformationEnhanced.txt", index=False)

    pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Course Code": ["MAT10", "ENG12"],
            "Title": ["Math 10", "English 12"],
        }
    ).to_csv(d / "CourseInformation.txt", index=False)

    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformation.txt", index=False)

    _write_class_info_rows(d)


# SD51 sends the HEADERFUL Enhanced export (19 columns), NOT the base's standard
# headerless 17-column file, so these rows carry their OWN header line and no
# headers are injected (DECISIONS 2026-09-12). Only School Number / Student Number
# / Absence Date / Absence Category are functionally used.
_PERIOD_ROWS = [
    "School Number,Student Number,Student Legal Last Name,Student Legal First Name,Grade,Homeroom,Teacher Name,Absence Date,Course Code,Absence Category,Absence Sub Allocation Code,Authorized Absence Code,Office Reason,Section Letter,Period Id,Teacher ID,School Course Code,Flavour,Schedule Term",
    "100,P1,Last,First,10,A1,Teacher,2024-09-18,MAT10,A,,,,A,1,T001,SCC,FL,S1",
    "100,P2,Last,First,11,A1,Teacher,19-Sep-2024,ENG11,L,,,,B,2,T002,SCC,FL,S1",
]


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


# ---------------------------------------------------------------------------
# (a) No usable input at all → raise + failed run-log + exit 1
# ---------------------------------------------------------------------------


class TestNoUsableInput:
    def test_all_required_files_missing_raises(self, tmp_path: Path, gde_output: Path) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()  # exists, but contains none of the required files

        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

    def test_failed_run_log_emitted(self, tmp_path: Path, gde_output: Path, caplog: pytest.LogCaptureFixture) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"), pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

        run_logs = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert run_logs, "expected a structured run-log line"
        payload = json.loads(run_logs[-1].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "failed"

    def test_main_wiring_exits_1(self, tmp_path: Path, gde_output: Path) -> None:
        """The REAL entry point turns this failure into exit 1.

        Previously this test raised its own ``sys.exit(1)`` inside a hand-copied
        version of main.py's except-block and asserted the code it had just chosen —
        it would have stayed green if main.py had exited 0. It now drives
        ``src.main.cli``, so the entry point's exception handling is what is under
        test. (Fuller exit-code coverage: ``tests/test_cli_entry.py``.)
        """
        from src.main import cli

        empty_input = tmp_path / "input"
        empty_input.mkdir()

        assert cli(["--sis", "myedbc", "--input", str(empty_input), "--output", str(gde_output)]) == 1

    def test_no_output_files_written(self, tmp_path: Path, gde_output: Path) -> None:
        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with pytest.raises(RuntimeError):
            run_pipeline("myedbc", str(empty_input), str(gde_output))

        assert list(gde_output.glob("*.csv")) == []


# ---------------------------------------------------------------------------
# (b) Period-only sd51attendance → guard does NOT fire (period file present)
# ---------------------------------------------------------------------------


class TestPeriodOnlyAttendanceDoesNotFire:
    def test_period_only_run_does_not_raise_from_guard(self, tmp_path: Path, gde_output: Path) -> None:
        """daily file PRESENT but empty (no K-7 absences that day), period present → the period
        file is NON-empty in ``raw_data`` → the no-usable-input guard does NOT fire, and the input
        gate lets an attendance file arrive with no rows (``outcomes.MAY_BE_EMPTY``) — the run
        completes (exit 0) and StudentAttendance builds from the period band.
        """
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "StudentPeriodAbsencesEnhanced.txt").write_text("\n".join(_PERIOD_ROWS), encoding="utf-8")
        (input_dir / "StudentDailyAbsences.txt").write_bytes(b"")  # a night with no daily absences

        result = run_pipeline("sd51attendance", str(input_dir), str(gde_output))

        assert result.entity_counts["StudentAttendance"] == 2

    def test_twin_a_MISSING_daily_file_stops_the_night(self, tmp_path: Path, gde_output: Path) -> None:
        """Owner 2026-09-28: "no absences today" is a file with no rows, never an absent file — a
        missing attendance file stops the night, typed and named, with nothing written."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "StudentPeriodAbsencesEnhanced.txt").write_text("\n".join(_PERIOD_ROWS), encoding="utf-8")

        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("sd51attendance", str(input_dir), str(gde_output))

        assert (raised.value.missing, raised.value.empty) == (("StudentDailyAbsences.txt",), ())
        assert raised.value.named == ("StudentDailyAbsences.txt",)
        assert list(gde_output.glob("*.csv")) == []
        record = read_run_records()[0]
        assert (record["status"], record["error_category"]) == ("failed", "incomplete_input")
        assert record["entity_outcomes"]["StudentAttendance"] == {
            "kind": "failed",
            "reason": "missing_source_file",
            "rows": 0,
            "file_label": "StudentDailyAbsences.txt",
        }


# ---------------------------------------------------------------------------
# (c) Partial multi-entity input → completes + writes the entity, exit 0
# ---------------------------------------------------------------------------


class TestPartialInputStillRuns:
    def test_partial_input_completes_and_writes(self, tmp_path: Path, gde_output: Path) -> None:
        """A complete myedbc input set — every required file present, each with its rows —
        completes normally and writes the rostering CSVs."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert (gde_output / "Students.csv").exists()
        assert result.entity_counts.get("Students", 0) > 0

    def test_partial_input_with_some_empty_sources_stops_the_night(self, tmp_path: Path, gde_output: Path) -> None:
        """Some required files present (Students), others absent — the no-usable-input guard
        does NOT fire (one required frame is non-empty), but since owner decision 2026-09-28
        the INPUT gate does: "we don't have optional files", so the night stops typed, naming
        every missing file a required output lists (Family's own is exempt), before anything
        is built. It used to complete, shipping Students alone."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        # Only the demographic file → Students has data; schedule/staff absent.
        pd.DataFrame(
            {
                "Student Number": ["S001", "S002"],
                "Legal First Name": ["Alice", "Bob"],
                "Legal Surname": ["Smith", "Jones"],
                "Date of birth": ["2010-01-15", "2009-06-20"],
                "Grade": ["10", "12"],
                "School Number": ["100", "100"],
                "Homeroom": ["A1", "A1"],
                "Previous school number": ["", ""],
                "Usual First Name": ["", ""],
                "Usual surname": ["", ""],
                "Student email address": ["alice@test.ca", "bob@test.ca"],
                "Enrolment Status": ["Active", "Active"],
                "Teacher Name": ["Ms. Harper", "Ms. Harper"],
                "Teacher ID": ["T001", "T001"],
            }
        ).to_csv(input_dir / "StudentDemographicInformation.txt", index=False)

        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert set(raised.value.missing) == {
            "StaffInformationEnhanced.txt",
            "StudentSchedule.txt",
            "CourseInformation.txt",
            "ClassInformationEnh.txt",
        }, "every missing file a required output lists — never the family contacts file"
        assert raised.value.category is RunErrorCategory.INCOMPLETE_INPUT
        assert list(gde_output.glob("*.csv")) == [], "nothing is written"
        record = read_run_records()[0]
        assert (record["status"], record["error_category"]) == ("failed", "incomplete_input")
        kinds = {entity: (entry["kind"], entry["reason"]) for entity, entry in record["entity_outcomes"].items()}
        assert kinds == {
            "Students": ("not_run", "run_aborted"),
            "Staff": ("failed", "missing_source_file"),
            "Family": ("not_run", "run_aborted"),
            "Classes": ("failed", "missing_source_file"),
            "Enrollments": ("failed", "missing_source_file"),
        }
        # Staff has ONE problem file, so its outcome names it; Classes has three, so it names none.
        assert record["entity_outcomes"]["Staff"]["file_label"] == "StaffInformationEnhanced.txt"
        assert "file_label" not in record["entity_outcomes"]["Classes"]
        assert raised.value.named == (), "one stopped entity could not name its file, so none is named"


# ---------------------------------------------------------------------------
# (b)/(c) The input gate itself — every required file present, with rows
# ---------------------------------------------------------------------------


class TestTheInputGate:
    """``pipeline.check_required_inputs`` (owner decision 2026-09-28), end to end and pure."""

    def test_a_complete_folder_passes_and_builds_every_output(self, tmp_path: Path, gde_output: Path) -> None:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert all(o.kind is OutcomeKind.BUILT for o in result.entity_outcomes), result.entity_outcomes
        assert {p.name for p in gde_output.glob("*.csv")} == {
            "Students.csv",
            "Staff.csv",
            "Family.csv",
            "Classes.csv",
            "Enrollments.csv",
        }

    @pytest.mark.parametrize(
        ("damage", "missing", "empty"),
        [
            ("delete", ("StudentSchedule.txt",), ()),
            ("header_only", (), ("StudentSchedule.txt",)),
            ("zero_bytes", (), ("StudentSchedule.txt",)),
        ],
    )
    def test_one_missing_or_row_less_file_stops_the_night_and_names_it(
        self, tmp_path: Path, gde_output: Path, damage: str, missing: tuple, empty: tuple
    ) -> None:
        """The schedule feeds Classes and Enrollments: both are recorded FAILED /
        ``missing_source_file`` naming it, every other entity NOT_RUN, and the last good output
        is byte-identical."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)
        run_pipeline("myedbc", str(input_dir), str(gde_output))  # the last good night
        before = {p.name: p.read_bytes() for p in gde_output.glob("*.csv")}
        schedule = input_dir / "StudentSchedule.txt"
        if damage == "delete":
            schedule.unlink()
        elif damage == "header_only":
            schedule.write_text(schedule.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        else:
            schedule.write_bytes(b"")

        with pytest.raises(IncompleteInputError) as raised:
            run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert (raised.value.missing, raised.value.empty) == (missing, empty)
        assert raised.value.named == ("StudentSchedule.txt",)
        assert {p.name: p.read_bytes() for p in gde_output.glob("*.csv")} == before
        record = read_run_records()[0]
        assert record["error_category"] == "incomplete_input"
        for entity in ("Classes", "Enrollments"):
            entry = record["entity_outcomes"][entity]
            assert (entry["kind"], entry["reason"], entry["file_label"]) == (
                "failed",
                "missing_source_file",
                "StudentSchedule.txt",
            )
        assert record["entity_outcomes"]["Students"]["kind"] == "not_run"

    @pytest.mark.parametrize("damage", ["delete", "header_only"])
    def test_the_family_contacts_file_alone_may_be_missing_or_empty(
        self, tmp_path: Path, gde_output: Path, damage: str
    ) -> None:
        """Family is the ONE entity that may be left out: its own file missing or row-less
        leaves Family EMPTY (a standing warning) and everything else ships."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)
        contacts = input_dir / "EmergencyContactInformation.txt"
        if damage == "delete":
            contacts.unlink()
        else:
            contacts.write_text(contacts.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        family = next(o for o in result.entity_outcomes if o.entity == "Family")
        assert (family.kind, family.reason) == (OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY)
        assert not (gde_output / "Family.csv").exists()
        assert (gde_output / "Enrollments.csv").exists()

    def test_the_gate_is_pure_over_the_declarations(self) -> None:
        """No file system: Family's file is never checked, a shared file is checked through the
        CRITICAL entity that lists it, an attendance file may be empty but not absent."""
        mappings = {
            "Students": {"source_files": {"demo": "demo.txt"}},
            "Family": {"source_files": {"contacts": "contacts.txt"}},
            "StudentAttendance": {"source_files": {"daily": "daily.txt", "period": "period.txt"}},
        }
        rows = pd.DataFrame({"x": [1]})
        ok = {"demo.txt": rows, "contacts.txt": pd.DataFrame(), "daily.txt": pd.DataFrame(), "period.txt": rows}
        ledger = OutcomeLedger(["Students", "Family", "StudentAttendance"])
        assert check_required_inputs(mappings, ok, absent={"contacts.txt"}, ledger=ledger, global_config={}) is None
        assert ledger.outcomes == (), "a passing gate records nothing"

        ledger = OutcomeLedger(["Students", "Family", "StudentAttendance"])
        with pytest.raises(IncompleteInputError) as raised:
            check_required_inputs(mappings, ok, absent={"contacts.txt", "daily.txt"}, ledger=ledger, global_config={})
        assert (raised.value.missing, raised.value.empty) == (("daily.txt",), ())
        assert ledger.outcomes == (EntityOutcome.failed("StudentAttendance", OutcomeReason.MISSING_SOURCE_FILE),)

        shared = {**mappings, "Students": {"source_files": {"demo": "demo.txt", "contacts": "contacts.txt"}}}
        ledger = OutcomeLedger(["Students", "Family", "StudentAttendance"])
        with pytest.raises(IncompleteInputError) as raised:
            check_required_inputs(shared, ok, absent=set(), ledger=ledger, global_config={})
        assert raised.value.empty == ("contacts.txt",), "a file Family shares is required through Students"

    def test_the_log_carries_one_grep_able_line(self, tmp_path: Path, gde_output: Path, caplog) -> None:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)
        (input_dir / "CourseInformation.txt").unlink()
        with caplog.at_level(logging.ERROR, logger="src.etl.pipeline"), pytest.raises(IncompleteInputError):
            run_pipeline("myedbc", str(input_dir), str(gde_output))
        lines = [r.getMessage() for r in caplog.records if "REQUIRED INPUT UNUSABLE" in r.getMessage()]
        assert lines == [
            "REQUIRED INPUT UNUSABLE — missing: ['CourseInformation.txt']; no data rows: none; needed by: Classes. "
            "The run stops before anything is built; only the family contacts file, and the class information file "
            "where blended-class detection is off, may be left out."
        ]

    def test_no_usable_input_at_all_is_still_its_own_narrower_answer(self, tmp_path: Path, gde_output: Path) -> None:
        """Checked FIRST: an empty folder is ``no_input``, never ``incomplete_input``."""
        empty_input = tmp_path / "input"
        empty_input.mkdir()
        with pytest.raises(NoUsableInputError):
            run_pipeline("myedbc", str(empty_input), str(gde_output))


# ---------------------------------------------------------------------------
# Item 1 (Plan 0008) — run_transform skips an entity ONLY when ALL its source
# frames are empty, not when the positional-first one is. A role-resolved
# entity (StudentAttendance) whose listed-first band (daily) is empty but whose
# second band (period) is populated must still be produced.
# ---------------------------------------------------------------------------


def _sd51attendance_raw() -> tuple[dict, dict]:
    """Return (mappings, global_config) for the `sd51attendance` tier.

    Built from the real validated config so the StudentAttendance entity carries
    its actual `source_files` (daily_absences first, period_absences second) and
    `enabled_entities = [StudentAttendance]`.
    """
    from src.config.loader import load_config

    raw = load_config("sd51attendance").to_raw_dict()
    return raw["mappings"], raw["global_config"]


def _period_frame() -> pd.DataFrame:
    """A populated 8-12 Student Period Absences frame.

    Matches the 19 columns SD51's HEADERFUL StudentPeriodAbsencesEnhanced.txt
    carries in its own header row (no injection — DECISIONS 2026-09-12); only
    School Number / Student Number / Absence Date / Absence Category are
    functionally used by the transformer.
    """
    return pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Student Number": ["S001", "S002"],
            "Student Legal Last Name": ["Last", "Last"],
            "Student Legal First Name": ["First", "First"],
            "Grade": ["10", "11"],
            "Homeroom": ["A1", "A1"],
            "Teacher Name": ["Teacher", "Teacher"],
            "Absence Date": ["2024-09-18", "2024-09-19"],
            "Course Code": ["MAT10", "ENG11"],
            "Absence Category": ["A", "L"],
            "Absence Sub Allocation Code": ["", ""],
            "Authorized Absence Code": ["", ""],
            "Office Reason": ["", ""],
            "Section Letter": ["A", "B"],
            "Period Id": ["1", "2"],
            "Teacher ID": ["T001", "T002"],
            "School Course Code": ["SCC", "SCC"],
            "Flavour": ["FL", "FL"],
            "Schedule Term": ["S1", "S1"],
        }
    )


class TestRunTransformAllSourcesEmptySkip:
    def test_period_only_attendance_is_produced(self) -> None:
        """REPRODUCE-FIRST (Item 1): daily band empty, period band populated.

        sd51attendance lists `daily_absences` (StudentDailyAbsences.txt) FIRST,
        so the positional-first source frame is the EMPTY daily file. Under the
        old positional-primary skip the entity was dropped → StudentAttendance
        absent from outputs (run exits 0, silently no file). After the fix the
        skip only fires when ALL source frames are empty, so the populated period
        band keeps the entity alive and it IS produced.

        Asserts FAIL on the unchanged code (StudentAttendance absent).
        """
        mappings, global_config = _sd51attendance_raw()
        raw_data = {
            "StudentDailyAbsences.txt": pd.DataFrame(),  # empty (absent daily band)
            "StudentPeriodAbsencesEnhanced.txt": _period_frame(),  # populated period band
        }

        result = run_transform(
            raw_data, mappings, global_config, ledger=OutcomeLedger(configured_entity_order(mappings, global_config))
        )

        assert "StudentAttendance" in result.outputs, (
            "period-only attendance must still produce StudentAttendance "
            "(skip should fire only when ALL source frames are empty)"
        )
        assert len(result.outputs["StudentAttendance"]) == 2

    def test_all_sources_empty_entity_is_skipped(self) -> None:
        """Both attendance bands empty → the entity is skipped (new all-empty
        branch), no StudentAttendance output, no crash."""
        mappings, global_config = _sd51attendance_raw()
        raw_data = {
            "StudentDailyAbsences.txt": pd.DataFrame(),
            "StudentPeriodAbsencesEnhanced.txt": pd.DataFrame(),
        }

        result = run_transform(
            raw_data, mappings, global_config, ledger=OutcomeLedger(configured_entity_order(mappings, global_config))
        )

        assert "StudentAttendance" not in result.outputs

    def test_empty_primary_non_attendance_entity_stops_the_night(self) -> None:
        """Empty-primary net (Item 1 risk): a non-attendance multi-source entity
        (Enrollments) with an empty schedule (its primary) but a populated
        demographic secondary now reaches `transform`.

        Classes must be enabled ahead of Enrollments here — since the
        2026-08-17 fix removed EnrollmentTransformer's early return on an
        empty schedule, the Classes -> Enrollments ordering assertion now
        fires unconditionally (previously it was only reached when the
        schedule was non-empty, so a Classes-less mapping with an empty
        schedule used to skip silently instead of failing loud). With Classes
        enabled, the one demographic student (grade 10, not a homeroom grade
        under the base myedbc config) means Classes itself publishes an
        all-empty artifact bundle and is skipped at the pipeline level
        (`transformed.empty`) — so Enrollments still legitimately produces
        nothing, just for the pipeline's ordinary skip-on-empty reason rather
        than the old pre-assertion short-circuit. Since owner decision 2026-09-28
        an EMPTY Classes (CRITICAL) no longer skips: the night stops, typed, at
        Classes, and Enrollments is never attempted — no Enrollments file either way.
        """
        from src.config.loader import load_config

        raw = load_config("myedbc").to_raw_dict()
        mappings, global_config = raw["mappings"], raw["global_config"]
        global_config["enabled_entities"] = ["Classes", "Enrollments"]

        raw_data = {
            "StudentSchedule.txt": pd.DataFrame(),  # empty primary (schedule)
            "StudentDemographicInformation.txt": pd.DataFrame(
                {
                    "Student Number": ["S001"],
                    "School Number": ["100"],
                    "Grade": ["10"],
                    "Homeroom": ["A1"],
                    "Teacher ID": ["T001"],
                }
            ),
        }

        ledger = OutcomeLedger(configured_entity_order(mappings, global_config))
        with pytest.raises(EmptyRequiredOutputError) as raised:
            run_transform(raw_data, mappings, global_config, ledger=ledger)

        assert raised.value.entity == "Classes"
        assert [o.kind for o in ledger.complete()] == [OutcomeKind.EMPTY, OutcomeKind.NOT_RUN]


# ---------------------------------------------------------------------------
# The run's OBSERVATION of its input headers (PipelineResult.input_columns)
# ---------------------------------------------------------------------------


class TestPipelineResultInputColumns:
    """``input_columns`` carries what the extractor SAW — raw observation, no derivation.

    Consumed by ``src.etl.preflight`` to report a mapped column that is in no file at
    all (an *intended blank*, which nothing else reports). Tested here rather than
    there because only a real run can prove the keys, the normalisation and the
    empty-tuple-for-an-absent-file contract.
    """

    def test_a_full_run_carries_every_required_file_and_its_normalised_headers(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        from src.config.loader import load_config
        from src.etl.pipeline import extract_required_files

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert set(result.input_columns) == set(extract_required_files(load_config("myedbc")))
        demographic = result.input_columns["StudentDemographicInformation.txt"]
        # Already strip+lower-cased by the extractor — no consumer re-normalises.
        assert "legal surname" in demographic
        assert "date of birth" in demographic  # written as "Date of birth"
        assert all(name == name.strip().lower() for name in demographic)
        assert all(isinstance(name, str) for name in demographic)

    def test_a_file_that_is_not_on_disk_keeps_its_key_with_an_empty_tuple(
        self, tmp_path: Path, gde_output: Path
    ) -> None:
        """The extractor yields an empty frame for an absent file and this carries that
        verbatim: dropping the key would make ``input_columns`` a second, quieter
        missing-FILE report able to disagree with the one that owns that fact. Twin: the
        file that IS present carries a non-empty tuple in the same run. The absent file is
        the family contacts export — since owner decision 2026-09-28 the only file whose
        absence still lets a run complete."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_full_rostering_input(input_dir)
        (input_dir / "EmergencyContactInformation.txt").unlink()

        result = run_pipeline("myedbc", str(input_dir), str(gde_output))

        assert result.input_columns["EmergencyContactInformation.txt"] == ()  # absent from disk
        assert result.input_columns["StudentDemographicInformation.txt"]

    def test_it_defaults_to_empty(self) -> None:
        """``input_columns`` is appended + defaulted: a construction that does not observe
        the input (the test stubs) carries an empty observation, which is exactly what
        makes ``preflight`` claim nothing. (Since plan 0053 S2 every construction must
        name ``entity_outcomes`` — pinned in ``tests/test_etl_outcomes.py``.)"""
        from src.etl.pipeline import PipelineResult

        assert PipelineResult(entity_outcomes=()).input_columns == {}
        assert PipelineResult(entity_outcomes=(), entity_counts={"Students": 3}).input_columns == {}

    def test_a_run_that_never_completes_carries_no_observation_at_all(self, tmp_path: Path, gde_output: Path) -> None:
        """The early-exit division of labour: only the success path builds a
        ``PipelineResult``, so a run with no usable input returns none — the report is
        absent exactly where the failure names the problem itself."""
        empty_input = tmp_path / "input"
        empty_input.mkdir()

        with pytest.raises(RuntimeError, match="No usable required input"):
            run_pipeline("myedbc", str(empty_input), str(gde_output))


# ---------------------------------------------------------------------------
# The AST pin: BOTH entry points call the ONE input gate, after the observation and
# before the transform — so neither can skip it or drift into its own spelling.
# ---------------------------------------------------------------------------
def _gate_order_problems(source: str, function: str) -> list[str]:
    from tests.test_pipeline_run_store import _call_lines

    calls = _call_lines(source, function)
    gate = calls.get("check_required_inputs", [])
    if len(gate) != 1:
        return [f"{function}: calls check_required_inputs {len(gate)} time(s), expected exactly once"]
    problems = []
    if not calls.get("observe_source_columns") or gate[0] < max(calls["observe_source_columns"]):
        problems.append(f"{function}: the gate runs before the source observation (no file could be named)")
    if not calls.get("run_transform") or gate[0] > min(calls["run_transform"]):
        problems.append(f"{function}: the gate runs after the transform")
    return problems


class TestBothEntryPointsCallTheInputGate:
    @pytest.mark.parametrize("function", ["run_pipeline", "convert_job"])
    def test_each_entry_point_gates_once_between_the_observation_and_the_transform(self, function: str) -> None:
        from tests.test_pipeline_run_store import _CONVERT_SRC, _PIPELINE_SRC

        path = _PIPELINE_SRC if function == "run_pipeline" else _CONVERT_SRC
        assert _gate_order_problems(path.read_text(encoding="utf-8"), function) == []

    def test_doctored_a_removed_gate_is_red(self) -> None:
        from tests.test_pipeline_run_store import _CONVERT_SRC

        source = _CONVERT_SRC.read_text(encoding="utf-8")
        doctored = source.replace("        check_required_inputs(", "        _gone(", 1)
        assert doctored != source
        assert _gate_order_problems(doctored, "convert_job") == [
            "convert_job: calls check_required_inputs 0 time(s), expected exactly once"
        ]

    def test_doctored_a_gate_after_the_transform_is_red(self) -> None:
        from tests.test_pipeline_run_store import _PIPELINE_SRC

        source = _PIPELINE_SRC.read_text(encoding="utf-8")
        call = "        check_required_inputs(mappings, raw_data, absent=absent, ledger=ledger, global_config=global_config)\n"
        assert call in source
        moved = source.replace(call, "", 1).replace(
            "        outputs = transform_outputs.outputs\n", "        outputs = transform_outputs.outputs\n" + call, 1
        )
        assert _gate_order_problems(moved, "run_pipeline") == ["run_pipeline: the gate runs after the transform"]
