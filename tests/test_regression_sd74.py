"""Regression / golden-file snapshot tests for the SD74 district pipeline.

The snapshot input files live in tests/snapshots/input/ and represent a
known-good SD74 GDE extract. The golden output files in
tests/snapshots/output/ were generated from a verified pipeline run and
lock the expected output shape, columns, and values.

The pipeline runs against a FROZEN copy of the SD74 mapping config stored
in tests/snapshots/config/ — so future edits to config/mappings/ files
will NOT break this test. To intentionally update the golden files, re-run
the pipeline against the new live config and replace tests/snapshots/output/.
"""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.main import main

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
INPUT_DIR = SNAPSHOT_DIR / "input"
GOLDEN_DIR = SNAPSHOT_DIR / "output"
FROZEN_CONFIG_DIR = SNAPSHOT_DIR / "config"

ENTITIES = ["Students", "Staff", "Family", "Classes", "Enrollments"]


def _read(path: Path) -> pd.DataFrame:
    """Read a CSV with UTF-8 BOM, treating all values as strings."""
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")


@pytest.mark.regression
class TestSD74Regression:
    @pytest.fixture(scope="class")
    def sd74_output(self, tmp_path_factory):
        """Run SD74 pipeline against snapshot inputs using the frozen config.

        Patching CONFIG_DIR ensures the live config/mappings/ files are not
        consulted — only the frozen copies in tests/snapshots/config/ are used.
        """
        out = tmp_path_factory.mktemp("sd74_regression")
        with patch("src.config.loader.CONFIG_DIR", FROZEN_CONFIG_DIR):
            main("sd74myedbc", str(INPUT_DIR), str(out))
        return out

    # ------------------------------------------------------------------
    # Structural checks
    # ------------------------------------------------------------------

    def test_all_five_output_files_exist(self, sd74_output):
        for entity in ENTITIES:
            assert (sd74_output / f"{entity}.csv").exists(), f"Missing {entity}.csv"

    def test_output_row_counts_match_golden(self, sd74_output):
        """Quick count check — easier to read failure message than full diff."""
        for entity in ENTITIES:
            actual = _read(sd74_output / f"{entity}.csv")
            golden = _read(GOLDEN_DIR / f"{entity}.csv")
            assert len(actual) == len(golden), (
                f"{entity}: expected {len(golden)} rows, got {len(actual)}"
            )

    def test_output_column_names_match_golden(self, sd74_output):
        for entity in ENTITIES:
            actual_cols = list(_read(sd74_output / f"{entity}.csv").columns)
            golden_cols = list(_read(GOLDEN_DIR / f"{entity}.csv").columns)
            assert actual_cols == golden_cols, (
                f"{entity} column mismatch.\n"
                f"  Expected: {golden_cols}\n"
                f"  Got:      {actual_cols}"
            )

    # ------------------------------------------------------------------
    # Full content checks (fail = a field value changed)
    # ------------------------------------------------------------------

    def test_students_matches_golden(self, sd74_output):
        actual = _read(sd74_output / "Students.csv").reset_index(drop=True)
        golden = _read(GOLDEN_DIR / "Students.csv").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, golden, check_dtype=False)

    def test_staff_matches_golden(self, sd74_output):
        actual = _read(sd74_output / "Staff.csv").reset_index(drop=True)
        golden = _read(GOLDEN_DIR / "Staff.csv").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, golden, check_dtype=False)

    def test_family_matches_golden(self, sd74_output):
        actual = _read(sd74_output / "Family.csv").reset_index(drop=True)
        golden = _read(GOLDEN_DIR / "Family.csv").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, golden, check_dtype=False)

    def test_classes_matches_golden(self, sd74_output):
        actual = _read(sd74_output / "Classes.csv").reset_index(drop=True)
        golden = _read(GOLDEN_DIR / "Classes.csv").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, golden, check_dtype=False)

    def test_enrollments_matches_golden(self, sd74_output):
        actual = _read(sd74_output / "Enrollments.csv").reset_index(drop=True)
        golden = _read(GOLDEN_DIR / "Enrollments.csv").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, golden, check_dtype=False)

    # ------------------------------------------------------------------
    # SD74-specific value checks
    # ------------------------------------------------------------------

    def test_student_emails_use_sd74_domain(self, sd74_output):
        students = _read(sd74_output / "Students.csv")
        emails = students["Email Address"].dropna()
        emails = emails[emails != ""]
        assert all(e.endswith("@sd74.bc.ca") for e in emails), (
            "All student emails must use @sd74.bc.ca domain"
        )

    def test_classes_have_fixed_dates(self, sd74_output):
        classes = _read(sd74_output / "Classes.csv")
        assert all(classes["Start Date"] == "2025-08-25"), "SD74 start date must be fixed at 2025-08-25"
        assert all(classes["End Date"] == "2026-07-25"), "SD74 end date must be fixed at 2026-07-25"

    def test_output_files_have_utf8_bom(self, sd74_output):
        for entity in ENTITIES:
            raw = (sd74_output / f"{entity}.csv").read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf"), f"{entity}.csv is missing UTF-8 BOM"
