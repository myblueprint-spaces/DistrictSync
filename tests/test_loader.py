"""Tests for the DataLoader — CSV output with field ordering."""

import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.etl.loader import DataLoader


def _replace_side_effect(*, fail_role, fail_entity):
    """Build an ``os.replace`` side-effect that delegates to the real ``os.replace``
    for every move except a single chosen ``(role, entity)`` commit operation,
    which it raises on — driving the real ``_commit_staged`` rollback path
    end-to-end (true integration, not a mock of rollback).

    Each call is classified **by path role**, never call-count (with backup-aside
    the call sequence is now interleaved backup/promote/…, so counting would fail
    the wrong operation):
      - ``promote``  — staged file → output dir (``src`` parent is ``.tmp_*``)
      - ``backup``   — existing target → backup dir (``dst`` parent is ``.bak_*``)
      - ``restore``  — backup → output dir (``src`` parent is ``.bak_*``)

    ``state["fired"]`` records that the injected failure actually triggered, so a
    test can assert it fired (guarding against a green that proves nothing).
    """
    real = os.replace
    state = {"fired": False}

    def side_effect(src, dst):
        src, dst = Path(src), Path(dst)
        if src.parent.name.startswith(".tmp_"):
            role = "promote"
        elif dst.parent.name.startswith(".bak_"):
            role = "backup"
        elif src.parent.name.startswith(".bak_"):
            role = "restore"
        else:
            role = "other"
        target = src.name if role == "promote" else dst.name if role == "backup" else None
        if role == fail_role and target == f"{fail_entity}.csv":
            state["fired"] = True
            raise OSError(f"Simulated failure on {fail_role} of {fail_entity}")
        return real(src, dst)

    return side_effect, state


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

    def test_csv_encoding_rule(self):
        # Single source of truth for per-entity encoding (used by the loader AND
        # the Streamlit ad-hoc page so both write byte-identical files).
        assert DataLoader.csv_encoding("StudentAttendance") == "utf-8"
        assert DataLoader.csv_encoding("Students") == "utf-8-sig"
        assert DataLoader.csv_encoding("Enrollments") == "utf-8-sig"

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

    def test_select_ordered_returns_contract_columns_in_order(self):
        """``select_ordered`` is the single source of column selection shared by
        the disk/SFTP write (``_write_csv``) and the UI download/zip path."""
        df = pd.DataFrame({"B": [1], "A": [2], "C": [3], "Extra": [9]})
        result = DataLoader.select_ordered(df, ["A", "B", "C"], "Test")
        assert list(result.columns) == ["A", "B", "C"]  # ordered + extras dropped

    def test_select_ordered_raises_value_error_not_key_error(self):
        """A missing column raises the SAME clean ``ValueError`` everywhere (never a
        raw ``KeyError`` from ``df[field_order]``) — so the download handler's
        ``except ValueError`` guard catches it on every write path."""
        df = pd.DataFrame({"Name": ["Alice"], "Grade": ["05"]})
        with pytest.raises(ValueError, match="columns missing.*Email"):
            DataLoader.select_ordered(df, ["Name", "Grade", "Email"], "Students")

    def test_utf8_bom_written(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"Name": ["René"], "Grade": ["05"]})
        loader.save_to_csv(df, "Test", ["Name", "Grade"])

        raw_bytes = (tmp_path / "Test.csv").read_bytes()
        assert raw_bytes.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM


class TestAtomicWriteRollback:
    """Verify save_all() commit is backup-and-restore atomic — any mid-commit
    failure leaves the output directory exactly as it was before the call.

    Failure is injected by patching ``src.etl.loader.os.replace`` with a
    role-aware side-effect (see ``_replace_side_effect``): the real ``os.replace``
    runs for every move except one chosen commit operation, so the real rollback
    code path executes end-to-end. Sorted commit order is ``Family, Staff, Students``.
    """

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

    @staticmethod
    def _no_temp_or_backup_dirs(tmp_path) -> None:
        leftover = list(tmp_path.glob(".tmp_*")) + list(tmp_path.glob(".bak_*"))
        assert leftover == [], f"Staging/backup directory not cleaned up: {leftover}"

    def test_mid_commit_failure_preserves_prior_and_drops_new(self, tmp_path):
        """Mid-commit promote failure on a LATER file (the called-out gap):
        prior files are restored to their original bytes and would-be-new files
        are removed (rolled back to absent). Mixed fixture — some entities
        pre-exist, one is new."""
        loader = DataLoader(str(tmp_path))
        # Students + Staff pre-exist; Family is new.
        (tmp_path / "Students.csv").write_text("orig-students", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("orig-staff", encoding="utf-8")

        # Students sorts last → its promote is the final commit operation.
        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Students")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on promote of Students"),
        ):
            loader.save_all(self._outputs(), self._field_orders())

        assert state["fired"], "injected promote failure never triggered"
        # Prior files restored to original bytes (not the new version).
        assert (tmp_path / "Students.csv").read_text(encoding="utf-8") == "orig-students"
        assert (tmp_path / "Staff.csv").read_text(encoding="utf-8") == "orig-staff"
        # New entity rolled back to absent.
        assert not (tmp_path / "Family.csv").exists(), "new file not rolled back to absent"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_backup_aside_failure_preserves_prior_output(self, tmp_path):
        """A failure during the backup-aside step (not a promote) is the distinct
        not-yet-in-applied boundary: earlier files' backups must still restore
        while the failing file's dest is the untouched original."""
        loader = DataLoader(str(tmp_path))
        for entity, content in (
            ("Students", "orig-students"),
            ("Staff", "orig-staff"),
            ("Family", "orig-family"),
        ):
            (tmp_path / f"{entity}.csv").write_text(content, encoding="utf-8")

        # Family + Staff back-up & promote first; Students' backup-aside fails →
        # Students.csv is never moved, earlier two must roll back.
        side_effect, state = _replace_side_effect(fail_role="backup", fail_entity="Students")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on backup of Students"),
        ):
            loader.save_all(self._outputs(), self._field_orders())

        assert state["fired"], "injected backup-aside failure never triggered"
        assert (tmp_path / "Students.csv").read_text(encoding="utf-8") == "orig-students"
        assert (tmp_path / "Staff.csv").read_text(encoding="utf-8") == "orig-staff"
        assert (tmp_path / "Family.csv").read_text(encoding="utf-8") == "orig-family"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_rollback_preserves_existing_output(self, tmp_path):
        """Promote failure on the FIRST committed file leaves existing output
        untouched (the file that never committed is never reached)."""
        loader = DataLoader(str(tmp_path))
        (tmp_path / "Students.csv").write_text("original content", encoding="utf-8")

        # Family sorts first → its promote is the first commit operation.
        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Family")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on promote of Family"),
        ):
            loader.save_all(self._outputs(), self._field_orders())

        assert state["fired"], "injected promote failure never triggered"
        assert (tmp_path / "Students.csv").read_text(encoding="utf-8") == "original content"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_rollback_cleans_up_staging_and_backup_dirs(self, tmp_path):
        """After any injected failure, no .tmp_* AND no .bak_* directory remains."""
        loader = DataLoader(str(tmp_path))
        (tmp_path / "Students.csv").write_text("orig", encoding="utf-8")

        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Students")

        with patch("src.etl.loader.os.replace", side_effect=side_effect), pytest.raises(OSError):
            loader.save_all(self._outputs(), self._field_orders())

        assert state["fired"]
        self._no_temp_or_backup_dirs(tmp_path)

    def test_successful_save_all_writes_all_files(self, tmp_path):
        """Happy path: all files appear with the NEW values and no .tmp_*/.bak_*
        directory remains."""
        loader = DataLoader(str(tmp_path))
        loader.save_all(self._outputs(), self._field_orders())

        for entity in ["Students", "Staff", "Family"]:
            assert (tmp_path / f"{entity}.csv").exists(), f"Missing {entity}.csv after save_all()"
        students = pd.read_csv(tmp_path / "Students.csv")
        assert students["Name"].iloc[0] == "Alice"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_successful_save_all_overwrites_existing(self, tmp_path):
        """Happy overwrite path: pre-existing files are atomically replaced with
        the new values and no .tmp_*/.bak_* directory remains."""
        loader = DataLoader(str(tmp_path))
        for entity in ("Students", "Staff", "Family"):
            (tmp_path / f"{entity}.csv").write_text("stale", encoding="utf-8")

        loader.save_all(self._outputs(), self._field_orders())

        students = pd.read_csv(tmp_path / "Students.csv")
        assert students["Name"].iloc[0] == "Alice"
        staff = pd.read_csv(tmp_path / "Staff.csv")
        assert staff["Role"].iloc[0] == "teacher"
        family = pd.read_csv(tmp_path / "Family.csv")
        assert family["Email"].iloc[0] == "john@test.ca"
        self._no_temp_or_backup_dirs(tmp_path)


class TestDetectStaleOutputs:
    """Item 3 (Plan 0008): pure, non-destructive stale-output detection.

    Lists entity CSVs in the output dir not produced this run; deletes NOTHING.
    The destructive (archive) decision lives in ``archive_stale_outputs`` —
    detection stays a pure, registry-keyed helper that is independently tested.
    """

    def test_detects_stale_entities_only_and_deletes_nothing(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        # Pre-seed: two recognized entity CSVs (one a cross-config myBlueprint+
        # file), an unrelated non-entity file.
        (tmp_path / "Classes.csv").write_text("stale-classes", encoding="utf-8")
        (tmp_path / "CourseInfo.csv").write_text("stale-courseinfo", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hand-written notes", encoding="utf-8")

        # This run emitted Students + Staff (no CSVs on disk for them — detection
        # keys off what is present + not emitted).
        stale = loader.detect_stale_outputs({"Students", "Staff"})

        # Sorted, entity-only: Classes + CourseInfo; notes.txt excluded.
        assert stale == ["Classes.csv", "CourseInfo.csv"]

        # NON-DESTRUCTIVE: every pre-seeded file still exists on disk.
        assert (tmp_path / "Classes.csv").exists()
        assert (tmp_path / "CourseInfo.csv").exists()
        assert (tmp_path / "notes.txt").exists()


class TestArchiveStaleOutputs:
    """Item 3 (Plan 0008, post-approval revision): archive stale outputs.

    Stale entity CSVs are MOVED into ``archive_<ts>/`` (non-destructive) rather
    than deleted — so the cross-config foot-gun under ``_base`` inheritance can't
    cause data loss. The archive subdir is excluded from SFTP's top-level
    ``*.csv`` glob, so a stale CSV can no longer ship.
    """

    @staticmethod
    def _archive_dirs(tmp_path):
        return [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("archive_")]

    def test_archives_stale_entities_and_excludes_from_sftp_glob(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        # Pre-seed: two recognized entity CSVs (one a cross-config myBlueprint+
        # file) + an unrelated non-entity file.
        (tmp_path / "Classes.csv").write_text("stale-classes", encoding="utf-8")
        (tmp_path / "CourseInfo.csv").write_text("stale-courseinfo", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("hand-written notes", encoding="utf-8")

        # This run emitted Students + Staff (neither on disk).
        archived = loader.archive_stale_outputs({"Students", "Staff"})

        # Returns the sorted stale entity filenames actually moved.
        assert archived == ["Classes.csv", "CourseInfo.csv"]

        # Exactly one archive_* subdir was created.
        archive_dirs = self._archive_dirs(tmp_path)
        assert len(archive_dirs) == 1, f"expected one archive_* dir, got {archive_dirs}"
        archive_dir = archive_dirs[0]

        # Both stale CSVs are GONE from the top level and PRESENT in the archive.
        assert not (tmp_path / "Classes.csv").exists()
        assert not (tmp_path / "CourseInfo.csv").exists()
        assert (archive_dir / "Classes.csv").read_text(encoding="utf-8") == "stale-classes"
        assert (archive_dir / "CourseInfo.csv").read_text(encoding="utf-8") == "stale-courseinfo"

        # The unrelated file is untouched at the top level.
        assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hand-written notes"

        # SFTP delivers `output_dir.glob("*.csv")` (top-level, non-recursive):
        # the archived names must no longer appear there.
        top_level_csvs = sorted(p.name for p in tmp_path.glob("*.csv"))
        assert "Classes.csv" not in top_level_csvs
        assert "CourseInfo.csv" not in top_level_csvs

    def test_emitted_entity_csv_is_never_archived(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        (tmp_path / "Students.csv").write_text("fresh", encoding="utf-8")
        (tmp_path / "Classes.csv").write_text("stale", encoding="utf-8")

        archived = loader.archive_stale_outputs({"Students"})

        # Students emitted → kept at top level; only Classes archived.
        assert archived == ["Classes.csv"]
        assert (tmp_path / "Students.csv").read_text(encoding="utf-8") == "fresh"
        assert not (tmp_path / "Classes.csv").exists()

    def test_clean_run_creates_no_archive_dir_and_returns_empty(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        (tmp_path / "Classes.csv").write_text("fresh", encoding="utf-8")

        archived = loader.archive_stale_outputs({"Classes"})

        assert archived == []
        # No archive dir is created when there is nothing stale.
        assert self._archive_dirs(tmp_path) == []
        # The emitted CSV stays in place.
        assert (tmp_path / "Classes.csv").read_text(encoding="utf-8") == "fresh"

    def test_archive_move_failure_is_best_effort_and_does_not_raise(self, tmp_path):
        """A per-file move failure is logged and skipped — never raised — so an
        archive hiccup can't turn an already committed + delivered run into a
        failure, and a single failure does not abort the rest of the loop.
        (Mirrors the 0007 ``os.replace``-injection discipline.)"""
        loader = DataLoader(str(tmp_path))
        (tmp_path / "Classes.csv").write_text("stale-classes", encoding="utf-8")
        (tmp_path / "CourseInfo.csv").write_text("stale-courseinfo", encoding="utf-8")

        real_replace = os.replace

        def fail_on_classes(src, dst):
            if Path(src).name == "Classes.csv":
                raise OSError("simulated archive move failure")
            real_replace(src, dst)

        with patch("src.etl.loader.os.replace", side_effect=fail_on_classes):
            archived = loader.archive_stale_outputs({"Students", "Staff"})

        # Did not raise; only the successfully-moved file is returned.
        assert archived == ["CourseInfo.csv"]

        # The failed move left Classes.csv in place (bytes not lost); the loop
        # continued and still archived CourseInfo.csv.
        assert (tmp_path / "Classes.csv").read_text(encoding="utf-8") == "stale-classes"
        archive_dirs = self._archive_dirs(tmp_path)
        assert len(archive_dirs) == 1
        assert (archive_dirs[0] / "CourseInfo.csv").read_text(encoding="utf-8") == "stale-courseinfo"
        assert not (archive_dirs[0] / "Classes.csv").exists()
