"""Tests for the DataLoader — CSV output with field ordering."""

from unittest.mock import patch

import pandas as pd
import pytest

from src.etl.loader import DataLoader


class TestDataLoader:
    def test_saves_csv(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"Name": ["Alice", "Bob"], "Grade": ["05", "06"]})
        loader.save_to_csv(df, "Students", ["Name", "Grade"])

        output_file = tmp_path / "Students.csv"
        assert output_file.exists()

        loaded = pd.read_csv(output_file)
        assert len(loaded) == 2
        assert list(loaded.columns) == ["Name", "Grade"]

    def test_field_ordering(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"B": [1], "A": [2], "C": [3]})
        loader.save_to_csv(df, "Test", ["A", "B", "C"])

        loaded = pd.read_csv(tmp_path / "Test.csv")
        assert list(loaded.columns) == ["A", "B", "C"]

    def test_studentattendance_written_without_bom(self, tmp_path):
        """StudentAttendance.csv must be plain UTF-8 (no BOM): SpacesEDU's strict
        attendance parser treats a BOM as part of the case-sensitive first header
        and rejects the file. Other entities keep the utf-8-sig BOM for Excel."""
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"School Number": ["123"]})

        loader.save_to_csv(df, "StudentAttendance", ["School Number"])
        loader.save_to_csv(df, "Students", ["School Number"])

        attendance = (tmp_path / "StudentAttendance.csv").read_bytes()
        rostering = (tmp_path / "Students.csv").read_bytes()
        assert not attendance.startswith(b"\xef\xbb\xbf"), "StudentAttendance.csv must have no BOM"
        assert attendance.startswith(b"School Number"), "first header must be clean (no BOM glued on)"
        assert rostering.startswith(b"\xef\xbb\xbf"), "rostering CSVs keep the BOM for Excel"

    def test_creates_output_directory(self, tmp_path):
        output_dir = tmp_path / "nested" / "output"
        DataLoader(str(output_dir))
        assert output_dir.exists()

    def test_overwrites_existing_file(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df1 = pd.DataFrame({"Name": ["Alice"]})
        df2 = pd.DataFrame({"Name": ["Bob", "Charlie"]})

        loader.save_to_csv(df1, "Test", ["Name"])
        loader.save_to_csv(df2, "Test", ["Name"])

        loaded = pd.read_csv(tmp_path / "Test.csv")
        assert len(loaded) == 2
        assert loaded["Name"].iloc[0] == "Bob"

    def test_missing_column_raises_value_error(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"Name": ["Alice"], "Grade": ["05"]})

        with pytest.raises(ValueError, match="columns missing.*NonExistent"):
            loader.save_to_csv(df, "Students", ["Name", "Grade", "NonExistent"])

    def test_utf8_bom_written(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"Name": ["René"], "Grade": ["05"]})
        loader.save_to_csv(df, "Test", ["Name", "Grade"])

        raw_bytes = (tmp_path / "Test.csv").read_bytes()
        assert raw_bytes.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM


class TestAtomicWriteRollback:
    """Verify save_all() atomicity — failure must leave existing output untouched."""

    def _outputs(self) -> dict[str, pd.DataFrame]:
        return {
            "Students": pd.DataFrame({"Name": ["Alice"], "Grade": ["05"]}),
            "Staff": pd.DataFrame({"Name": ["Harper"], "Role": ["teacher"]}),
            "Family": pd.DataFrame({"Name": ["John"], "Email": ["john@test.ca"]}),
        }

    def _field_orders(self) -> dict[str, list[str]]:
        return {
            "Students": ["Name", "Grade"],
            "Staff": ["Name", "Role"],
            "Family": ["Name", "Email"],
        }

    def test_rollback_preserves_existing_output(self, tmp_path):
        """If save_all() fails mid-commit, existing files are left untouched."""
        loader = DataLoader(str(tmp_path))
        # Pre-populate with known content
        (tmp_path / "Students.csv").write_text("original content", encoding="utf-8")

        call_count = 0

        def move_fail_on_first(src, dst):
            nonlocal call_count
            call_count += 1
            raise OSError("Simulated disk full")

        with (
            patch("src.etl.loader.shutil.move", side_effect=move_fail_on_first),
            pytest.raises(OSError, match="Simulated disk full"),
        ):
            loader.save_all(self._outputs(), self._field_orders())

        # Original file must be untouched
        assert (tmp_path / "Students.csv").read_text(encoding="utf-8") == "original content"

    def test_rollback_cleans_up_staging_dir(self, tmp_path):
        """After a failure, no .tmp_* staging directory must remain."""
        loader = DataLoader(str(tmp_path))

        with patch("src.etl.loader.shutil.move", side_effect=OSError("disk full")), pytest.raises(OSError):
            loader.save_all(self._outputs(), self._field_orders())

        tmp_dirs = list(tmp_path.glob(".tmp_*"))
        assert tmp_dirs == [], f"Staging directory not cleaned up: {tmp_dirs}"

    def test_partial_failure_leaves_no_tmp_dir(self, tmp_path):
        """Failure after the first successful move still cleans up staging dir."""
        loader = DataLoader(str(tmp_path))

        call_count = 0

        def move_fail_on_third(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise OSError("Simulated write error on 3rd file")
            import shutil as _shutil

            _shutil.move(src, dst)

        with patch("src.etl.loader.shutil.move", side_effect=move_fail_on_third), pytest.raises(OSError):
            loader.save_all(self._outputs(), self._field_orders())

        tmp_dirs = list(tmp_path.glob(".tmp_*"))
        assert tmp_dirs == [], f"Staging directory not cleaned up after partial failure: {tmp_dirs}"

    def test_successful_save_all_leaves_no_tmp_dir(self, tmp_path):
        """Happy path: successful save_all() leaves no staging directory behind."""
        loader = DataLoader(str(tmp_path))
        loader.save_all(self._outputs(), self._field_orders())

        tmp_dirs = list(tmp_path.glob(".tmp_*"))
        assert tmp_dirs == [], f"Staging directory not cleaned up after success: {tmp_dirs}"

    def test_successful_save_all_writes_all_files(self, tmp_path):
        """Happy path: all files appear in output directory after save_all()."""
        loader = DataLoader(str(tmp_path))
        loader.save_all(self._outputs(), self._field_orders())

        for entity in ["Students", "Staff", "Family"]:
            assert (tmp_path / f"{entity}.csv").exists(), f"Missing {entity}.csv after save_all()"
