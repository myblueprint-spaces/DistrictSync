"""CLI ↔ UI output-parity lock.

The Convert screen (`convert_job`) and the CLI/wizard (`run_pipeline`) must produce
**byte-for-byte identical** CSVs for identical inputs. This test runs the SAME
synthetic GDE bytes through both REAL paths — neither side is re-implemented here
(plan 0051) — and asserts, per entity:

  * the transformed frames are equal, AND
  * the on-disk CSV **bytes** are equal — including the per-entity BOM rule
    (no BOM for `StudentAttendance`, BOM for the rostering CSVs).

This is the regression that would have caught the original StudentAttendance-BOM
bug: the encoding decision now lives in exactly one place (`DataLoader.csv_encoding`)
and both write paths route through it. The config used (`sd51myedbc`) enables
four rostering entities (Family has been off for SD51 since 2026-09-25) **plus**
`StudentAttendance`, so the run exercises a no-BOM entity and several with-BOM
entities in a single pass — mirroring the
two-entity assertion in the 2026-06-19 BOM regression test (`test_loader.py`).

All data is synthetic — no real student PII.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.etl.loader import DataLoader
from src.etl.pipeline import run_pipeline
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens.convert import convert_job

CONFIG = "sd51myedbc"  # 4 rostering entities + StudentAttendance (no-BOM)


# ---------------------------------------------------------------------------
# Synthetic GDE bytes — keyed by the filenames sd51myedbc resolves.
# ---------------------------------------------------------------------------


def _csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to UTF-8 CSV bytes (what a GDE export looks like)."""
    return df.to_csv(index=False).encode("utf-8")


def _headerless_bytes(rows: list[list[str]], sep: str = ",") -> bytes:
    """Serialize headerless rows (absence GDEs) — column names come from config."""
    return ("\n".join(sep.join(r) for r in rows) + "\n").encode("utf-8")


@pytest.fixture
def gde_sources() -> dict[str, bytes]:
    """The seven GDE files sd51myedbc requires, plus the contacts export, as in-memory bytes.

    Produces non-empty output for every enabled entity (4 rostering + the two
    StudentAttendance bands). The contacts file is still supplied — a real SD51
    drop carries one — but the config no longer reads it (Family is off). The two absence files are HEADERLESS — the config
    injects the 18-/17-column header lists at extract time.
    """
    demographic = pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Legal First Name": ["Alice", "Bob", "Charlie"],
            "Legal Surname": ["Smith", "Jones", "Brown"],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10"],
            "Grade": ["3", "10", "12"],
            "School Number": ["100", "200", "200"],
            "Homeroom": ["A1", "C3", "C4"],
            "Next school code": ["", "", ""],
            "Usual First Name": ["Ali", "", "Chuck"],
            "Usual surname": ["", "", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca", "charlie@test.ca"],
            "Enrolment Status": ["Active", "Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    )
    schedule = pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Teacher ID": ["T001", "T003", "T004"],
            "Section Letter": ["A", "A", "B"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    )
    staff = pd.DataFrame(
        {
            "Teacher Id": ["T001", "T003", "T004"],
            "First Name": ["Jane", "Linda", "Raj"],
            "Last Name": ["Harper", "Liu", "Singh"],
            "Email Address": ["harper@school.ca", "liu@school.ca", "singh@school.ca"],
            "Teaching Staff": ["Y", "Y", "Y"],
            "School Number": ["100", "200", "200"],
        }
    )
    emergency = pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "First Name": ["John", "Robert"],
            "Last Name": ["Smith", "Jones"],
            "Email Address": ["john@mail.com", "robert@mail.com"],
        }
    )
    course = pd.DataFrame(
        {
            "School Number": ["200", "200"],
            "Course Code": ["MAT10", "ENG12"],
            "Title": ["Math 10", "English 12"],
        }
    )
    class_info = pd.DataFrame(
        {
            "School Number": [],
            "Teacher ID": [],
            "Master Timetable ID": [],
            "Term": [],
            "Semester": [],
            "Day": [],
            "Period": [],
        }
    )

    # Headerless Student Daily Absences (K-7) — 18 columns in MyEd order. Only
    # school/student/date/codes/portion are functionally used (config-driven).
    # Row layout matches the base `StudentDailyAbsences.txt` headers block.
    # cols: School Number, Student Number, Last, First, Grade, Homeroom,
    # Teacher Name, Absence Date, Reason Code AM, Sub Alloc AM, Authorized AM,
    # Reason Code PM, Sub Alloc PM, Authorized PM, Absent Code AM, Absent Code PM,
    # Teacher ID, Portion Absent
    daily = _headerless_bytes(
        [
            [
                "100",
                "S001",
                "Smith",
                "Alice",
                "3",
                "A1",
                "Harper",
                "18-Sep-2024",
                "",
                "",
                "N",
                "",
                "",
                "",
                "A",
                "",
                "T001",
                "1.0",
            ],
            [
                "100",
                "S001",
                "Smith",
                "Alice",
                "3",
                "A1",
                "Harper",
                "19-Sep-2024",
                "",
                "",
                "Y",
                "",
                "",
                "",
                "T",
                "",
                "T001",
                "0.5",
            ],
        ]
    )

    # SD51's Student Period Absences (8-12) is the HEADERFUL Enhanced export — 19
    # columns carrying their OWN header row, so no headers are injected (DECISIONS
    # 2026-09-12). Only school/student/date/category are functionally used; the
    # category is passed through as-is.
    period = _headerless_bytes(
        [
            [
                "School Number",
                "Student Number",
                "Student Legal Last Name",
                "Student Legal First Name",
                "Grade",
                "Homeroom",
                "Teacher Name",
                "Absence Date",
                "Course Code",
                "Absence Category",
                "Absence Sub Allocation Code",
                "Authorized Absence Code",
                "Office Reason",
                "Section Letter",
                "Period Id",
                "Teacher ID",
                "School Course Code",
                "Flavour",
                "Schedule Term",
            ],
            [
                "200",
                "S002",
                "Jones",
                "Bob",
                "10",
                "C3",
                "Liu",
                "2024-09-18",
                "MAT10",
                "A",
                "",
                "N",
                "",
                "A",
                "1",
                "T003",
                "MAT10",
                "",
                "S1",
            ],
            [
                "200",
                "S003",
                "Brown",
                "Charlie",
                "12",
                "C4",
                "Singh",
                "19-Sep-2024",
                "ENG12",
                "L",
                "",
                "N",
                "",
                "B",
                "2",
                "T004",
                "ENG12",
                "",
                "S1",
            ],
        ]
    )

    return {
        "StudentDemographicEnhanced.txt": _csv_bytes(demographic),
        "StudentSchedule.txt": _csv_bytes(schedule),
        "StaffInformation.txt": _csv_bytes(staff),
        "EmergencyContactInformation.txt": _csv_bytes(emergency),
        "CourseInformation.txt": _csv_bytes(course),
        "ClassInformationEnh.txt": _csv_bytes(class_info),
        "StudentDailyAbsences.txt": daily,
        "StudentPeriodAbsencesEnhanced.txt": period,
    }


def _file_headers(config_name: str) -> dict[str, list[str]]:
    """Collect the headerless-file header lists from the validated config."""
    raw = load_config(config_name).to_raw_dict()
    headers: dict[str, list[str]] = {}
    for entity_cfg in raw["mappings"].values():
        for filename, header_list in entity_cfg.get("headers", {}).items():
            headers[filename] = header_list
    return headers


# ---------------------------------------------------------------------------
# Path drivers
# ---------------------------------------------------------------------------


def _run_cli_path(gde_sources: dict[str, bytes], tmp_path: Path) -> Path:
    """CLI path: write GDE bytes to disk, run run_pipeline → output dir."""
    input_dir = tmp_path / "cli_input"
    output_dir = tmp_path / "cli_output"
    input_dir.mkdir()
    output_dir.mkdir()
    for name, data in gde_sources.items():
        (input_dir / name).write_bytes(data)
    run_pipeline(CONFIG, str(input_dir), str(output_dir))
    return output_dir


def _run_ui_path(gde_sources: dict[str, bytes], tmp_path: Path) -> Path:
    """UI path: the REAL ``convert_job``, not a hand-rolled copy of it.

    This used to re-implement the adapter inline (``load_from_bytes → run_transform →
    save_all``), which made the lock weaker than it looked in two ways: it could not see a
    divergence that lived in ``convert_job`` itself, and — until plan 0051 — the two sides
    were handed DIFFERENT file sets, agreeing only because this fixture folder happens to
    hold exactly the config's files and nothing else. A parity test that mirrors the thing
    it guards is the very "two implementations of one job" fault 0051 exists to delete.

    Calling the production function means this now pins what it claims: identical inputs
    through both real paths produce byte-identical CSVs, including the per-entity BOM split
    and field order.
    """
    input_dir = tmp_path / "ui_input"
    output_dir = tmp_path / "ui_output"
    input_dir.mkdir()
    output_dir.mkdir()
    for name, data in gde_sources.items():
        (input_dir / name).write_bytes(data)
    AppConfig(input_dir=str(input_dir), output_dir=str(output_dir), sis_type=CONFIG).save()
    result = convert_job(CONFIG, str(input_dir))
    assert result.status is ConvertStatus.DELIVERED, f"UI path did not build: {result.status}"
    return output_dir


# ---------------------------------------------------------------------------
# The parity lock
# ---------------------------------------------------------------------------


class TestCLIvsUIParity:
    def test_both_paths_emit_the_same_entities(self, gde_sources, tmp_path):
        cli_dir = _run_cli_path(gde_sources, tmp_path)
        ui_dir = _run_ui_path(gde_sources, tmp_path)

        cli_files = {p.name for p in cli_dir.glob("*.csv")}
        ui_files = {p.name for p in ui_dir.glob("*.csv")}
        assert cli_files == ui_files
        # The run must include both a no-BOM and a with-BOM entity, or the
        # byte-parity assertion below would not actually exercise the BOM split.
        assert "StudentAttendance.csv" in cli_files
        assert "Students.csv" in cli_files

    def test_outputs_are_byte_identical_per_entity(self, gde_sources, tmp_path):
        cli_dir = _run_cli_path(gde_sources, tmp_path)
        ui_dir = _run_ui_path(gde_sources, tmp_path)

        for cli_csv in sorted(cli_dir.glob("*.csv")):
            ui_csv = ui_dir / cli_csv.name
            assert ui_csv.exists(), f"UI path missing {cli_csv.name}"
            # Frame-equal: identical data + column order.
            cli_df = pd.read_csv(cli_csv, dtype=str)
            ui_df = pd.read_csv(ui_csv, dtype=str)
            pd.testing.assert_frame_equal(cli_df, ui_df)
            # Column-set parity: extras dropped identically on both paths.
            assert list(cli_df.columns) == list(ui_df.columns)
            # Byte-equal: identical encoding (incl. BOM rule) + bytes on disk.
            assert cli_csv.read_bytes() == ui_csv.read_bytes(), f"{cli_csv.name} bytes differ between CLI and UI"

    def test_studentattendance_has_no_bom_on_both_paths(self, gde_sources, tmp_path):
        """The no-BOM entity: StudentAttendance.csv must start with a clean header
        (no BOM glued on) on BOTH the CLI and the UI write path — the direct fix
        for 'Browser Convert writes StudentAttendance.csv with a BOM'."""
        cli_dir = _run_cli_path(gde_sources, tmp_path)
        ui_dir = _run_ui_path(gde_sources, tmp_path)

        for out_dir in (cli_dir, ui_dir):
            data = (out_dir / "StudentAttendance.csv").read_bytes()
            assert not data.startswith(b"\xef\xbb\xbf"), f"{out_dir.name}/StudentAttendance.csv must have no BOM"
            assert data.startswith(b"School Number"), "first header must be clean (no BOM glued on)"

    def test_both_paths_report_the_same_entity_outcomes(self, gde_sources, tmp_path):
        """Plan 0053 S2: the two entry points build their outcome ledger at the same point,
        so the per-entity outcomes — in the returned result AND in the stored record — are
        identical, one per configured entity (five here, attendance included)."""
        from src.history.store import read_run_records

        cli_input, cli_output = tmp_path / "cli_in", tmp_path / "cli_out"
        cli_input.mkdir()
        cli_output.mkdir()
        for name, data in gde_sources.items():
            (cli_input / name).write_bytes(data)
        cli_result = run_pipeline(CONFIG, str(cli_input), str(cli_output))
        _run_ui_path(gde_sources, tmp_path)
        ui_result = convert_job(CONFIG, str(tmp_path / "ui_input"))

        assert ui_result.entity_outcomes == cli_result.entity_outcomes
        assert [o.entity for o in cli_result.entity_outcomes] == [
            "Students",
            "Staff",
            "Classes",
            "Enrollments",
            "StudentAttendance",
        ]
        records = read_run_records()
        assert records is not None and len(records) >= 2
        by_source = {r["source"]: r["entity_outcomes"] for r in reversed(records)}
        assert by_source["manual"] == by_source["cli"]

    def test_rostering_keeps_bom_on_both_paths(self, gde_sources, tmp_path):
        """The with-BOM entity: Students.csv keeps the utf-8-sig BOM for Excel on
        BOTH paths."""
        cli_dir = _run_cli_path(gde_sources, tmp_path)
        ui_dir = _run_ui_path(gde_sources, tmp_path)

        for out_dir in (cli_dir, ui_dir):
            data = (out_dir / "Students.csv").read_bytes()
            assert data.startswith(b"\xef\xbb\xbf"), f"{out_dir.name}/Students.csv must keep the BOM"


class TestCLIvsUIParityOverAPartialRun:
    """Plan 0053 S4: the entity bulkhead lives in the SHARED ``run_transform``, so a run that
    leaves an ISOLATABLE entity out (Unity's plain contact report) is the same run on both
    paths — the same files, byte for byte, the same outcomes, and no ``Family.csv`` on
    either."""

    _SIS = "unitychristianmyedbc"

    def test_both_paths_leave_family_out_and_agree_on_everything_else(self, tmp_path):
        from src.history.store import read_run_records
        from tests.test_contract import _create_unitychristian_plain_report_inputs

        input_dir, cli_out, ui_out = tmp_path / "in", tmp_path / "cli_out", tmp_path / "ui_out"
        for d in (input_dir, cli_out, ui_out):
            d.mkdir()
        _create_unitychristian_plain_report_inputs(input_dir)

        cli_result = run_pipeline(self._SIS, str(input_dir), str(cli_out))
        AppConfig(input_dir=str(input_dir), output_dir=str(ui_out), sis_type=self._SIS).save()
        ui_result = convert_job(self._SIS, str(input_dir))
        assert ui_result.status is ConvertStatus.DELIVERED

        cli_files = sorted(p.name for p in cli_out.glob("*.csv"))
        assert cli_files == sorted(p.name for p in ui_out.glob("*.csv"))
        assert "Family.csv" not in cli_files and "Students.csv" in cli_files
        for name in cli_files:
            assert (cli_out / name).read_bytes() == (ui_out / name).read_bytes(), f"{name} differs"
        assert ui_result.entity_outcomes == cli_result.entity_outcomes
        assert [(o.entity, o.kind.value) for o in cli_result.entity_outcomes if o.kind.value != "built"] == [
            ("Family", "failed")
        ]
        records = read_run_records()
        assert records is not None
        by_source = {r["source"]: r["entity_outcomes"] for r in reversed(records)}
        assert by_source["manual"] == by_source["cli"]


class TestUIWriteFailsLoud:
    """The deliberate, desirable behavior change: a missing field-map column makes
    the UI write path raise (DataLoader.save_all → _write_csv) instead of writing a
    partial file — fail loud, matching the scheduled run. The page surfaces this as
    an st.error; the underlying write still raises."""

    def test_save_all_raises_on_missing_field_order_column(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        outputs = {"Students": pd.DataFrame({"Name": ["Alice"], "Grade": ["05"]})}
        # field_order references a column the frame does not contain.
        field_orders = {"Students": ["Name", "Grade", "Email"]}

        with pytest.raises(ValueError, match="columns missing.*Email"):
            loader.save_all(outputs, field_orders)

        # No partial file committed.
        assert not (tmp_path / "Students.csv").exists()

    def test_download_path_raises_value_error_not_key_error(self):
        """The download/zip path (``create_zip`` + the per-CSV buttons) now routes
        column selection through ``DataLoader.select_ordered``, so a field_order
        referencing an absent column raises the SAME clean ``ValueError`` the
        disk/SFTP write raises — NOT the raw ``KeyError`` that ``df[field_order]``
        used to throw past the page's ``except ValueError`` guard.

        This is the asymmetry the Verify review flagged: the download handler
        catches only ``ValueError``, so a ``KeyError`` here would escape as a raw
        Streamlit traceback. ``field_orders`` come from ``field_map`` keys, which
        are not guaranteed to materialize as columns (the documented
        ``student_courses.py`` partial-transform debt), so this path is reachable.
        """
        df = pd.DataFrame({"Name": ["Alice"], "Grade": ["05"]})
        field_order = ["Name", "Grade", "Email"]  # "Email" absent from the frame

        # A raw df[field_order] would raise KeyError; select_ordered raises ValueError.
        with pytest.raises(ValueError, match="columns missing.*Email"):
            DataLoader.select_ordered(df, field_order, "Students")

        # Pin that the un-routed access really WOULD have raised KeyError — proving
        # the guard converts the failure mode the download handler can catch.
        with pytest.raises(KeyError):
            _ = df[field_order]

    def test_column_set_parity_extras_dropped(self, gde_sources, tmp_path):
        """The other deliberate change: the write path emits EXACTLY the contract
        columns (field_orders), dropping any extra column. Assert the persisted CLI
        and UI column sets are identical AND equal to the configured field order."""
        cli_dir = _run_cli_path(gde_sources, tmp_path)
        ui_dir = _run_ui_path(gde_sources, tmp_path)

        raw = load_config(CONFIG).to_raw_dict()
        for cli_csv in sorted(cli_dir.glob("*.csv")):
            entity = cli_csv.stem
            ui_csv = ui_dir / cli_csv.name
            cli_cols = list(pd.read_csv(cli_csv, dtype=str).columns)
            ui_cols = list(pd.read_csv(ui_csv, dtype=str).columns)
            assert cli_cols == ui_cols, f"{entity}: CLI/UI column sets differ"
            field_order = list(raw["mappings"][entity].get("field_map", {}).keys())
            assert cli_cols == field_order, f"{entity}: persisted columns are not exactly the contract columns"
