"""Tests for the DataLoader — CSV output with field ordering."""

import inspect
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.etl.loader import (
    _STALE_BAK_MIN_AGE_SECONDS,
    _STALE_TMP_MAX_AGE_DAYS,
    DataLoader,
    output_target_problem,
)

ZIP = "acsv_sd40_2026-11-06.zip"


def _under(path: Path, prefix: str) -> bool:
    """Whether ``path`` sits (at any depth) inside a dir whose name starts with ``prefix``."""
    return any(parent.name.startswith(prefix) for parent in path.parents)


def _seed(path: Path, text: str | None) -> Path:
    """Create ``path``'s folder; write ``text`` when given. Returns ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


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
        if _under(src, ".tmp_"):
            role = "promote"
        elif _under(dst, ".bak_"):
            role = "backup"
        elif _under(src, ".bak_"):
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

        output_file = tmp_path / "csv" / "Students.csv"
        assert output_file.exists()

        loaded = pd.read_csv(output_file)
        assert len(loaded) == 2
        assert list(loaded.columns) == ["Name", "Grade"]

    def test_field_ordering(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        df = pd.DataFrame({"B": [1], "A": [2], "C": [3]})
        loader.save_to_csv(df, "Test", ["A", "B", "C"])

        loaded = pd.read_csv(tmp_path / "csv" / "Test.csv")
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
        rostering = (tmp_path / "csv" / "Students.csv").read_bytes()
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

        loaded = pd.read_csv(tmp_path / "csv" / "Test.csv")
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

        raw_bytes = (tmp_path / "csv" / "Test.csv").read_bytes()
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
        _seed(tmp_path / "csv" / "Students.csv", None).write_text("orig-students", encoding="utf-8")
        _seed(tmp_path / "csv" / "Staff.csv", None).write_text("orig-staff", encoding="utf-8")

        # Students sorts last → its promote is the final commit operation.
        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Students")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on promote of Students"),
        ):
            loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        assert state["fired"], "injected promote failure never triggered"
        # Prior files restored to original bytes (not the new version).
        assert (tmp_path / "csv" / "Students.csv").read_text(encoding="utf-8") == "orig-students"
        assert (tmp_path / "csv" / "Staff.csv").read_text(encoding="utf-8") == "orig-staff"
        # New entity rolled back to absent.
        assert not (tmp_path / "csv" / "Family.csv").exists(), "new file not rolled back to absent"
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
            _seed(tmp_path / "csv" / f"{entity}.csv", None).write_text(content, encoding="utf-8")

        # Family + Staff back-up & promote first; Students' backup-aside fails →
        # Students.csv is never moved, earlier two must roll back.
        side_effect, state = _replace_side_effect(fail_role="backup", fail_entity="Students")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on backup of Students"),
        ):
            loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        assert state["fired"], "injected backup-aside failure never triggered"
        assert (tmp_path / "csv" / "Students.csv").read_text(encoding="utf-8") == "orig-students"
        assert (tmp_path / "csv" / "Staff.csv").read_text(encoding="utf-8") == "orig-staff"
        assert (tmp_path / "csv" / "Family.csv").read_text(encoding="utf-8") == "orig-family"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_rollback_preserves_existing_output(self, tmp_path):
        """Promote failure on the FIRST committed file leaves existing output
        untouched (the file that never committed is never reached)."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Students.csv", None).write_text("original content", encoding="utf-8")

        # Family sorts first → its promote is the first commit operation.
        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Family")

        with (
            patch("src.etl.loader.os.replace", side_effect=side_effect),
            pytest.raises(OSError, match="Simulated failure on promote of Family"),
        ):
            loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        assert state["fired"], "injected promote failure never triggered"
        assert (tmp_path / "csv" / "Students.csv").read_text(encoding="utf-8") == "original content"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_rollback_cleans_up_staging_and_backup_dirs(self, tmp_path):
        """After any injected failure, no .tmp_* AND no .bak_* directory remains."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Students.csv", None).write_text("orig", encoding="utf-8")

        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Students")

        with patch("src.etl.loader.os.replace", side_effect=side_effect), pytest.raises(OSError):
            loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        assert state["fired"]
        self._no_temp_or_backup_dirs(tmp_path)

    def test_successful_save_all_writes_all_files(self, tmp_path):
        """Happy path: all files appear with the NEW values and no .tmp_*/.bak_*
        directory remains."""
        loader = DataLoader(str(tmp_path))
        loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        for entity in ["Students", "Staff", "Family"]:
            assert (tmp_path / "csv" / f"{entity}.csv").exists(), f"Missing {entity}.csv after save_all()"
        students = pd.read_csv(tmp_path / "csv" / "Students.csv")
        assert students["Name"].iloc[0] == "Alice"
        self._no_temp_or_backup_dirs(tmp_path)

    def test_successful_save_all_overwrites_existing(self, tmp_path):
        """Happy overwrite path: pre-existing files are atomically replaced with
        the new values and no .tmp_*/.bak_* directory remains."""
        loader = DataLoader(str(tmp_path))
        for entity in ("Students", "Staff", "Family"):
            _seed(tmp_path / "csv" / f"{entity}.csv", None).write_text("stale", encoding="utf-8")

        loader.save_all(self._outputs(), self._field_orders(), zip_name=ZIP)

        students = pd.read_csv(tmp_path / "csv" / "Students.csv")
        assert students["Name"].iloc[0] == "Alice"
        staff = pd.read_csv(tmp_path / "csv" / "Staff.csv")
        assert staff["Role"].iloc[0] == "teacher"
        family = pd.read_csv(tmp_path / "csv" / "Family.csv")
        assert family["Email"].iloc[0] == "john@test.ca"
        self._no_temp_or_backup_dirs(tmp_path)


class TestStagingBackupUniqueness:
    """W1b Item 3: two ``save_all`` calls in the same wall-clock second must never
    share — or ``rmtree`` — each other's staging/backup dirs. The dir names carry
    a per-call ``uuid4`` suffix, and creation uses ``exist_ok=False`` so any
    residual collision fails LOUD instead of interleaving commits."""

    def _outputs(self):
        return {"Students": pd.DataFrame({"Name": ["Alice"]})}

    def _orders(self):
        return {"Students": ["Name"]}

    def test_staging_and_backup_names_carry_matching_unique_suffix(self, tmp_path):
        """Observed via the commit's real ``os.replace`` calls: both dirs are
        ``.<kind>_<YYYYMMDD>_<HHMMSS>_<8-hex>`` and share the same stamp."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Students.csv", None).write_text("old", encoding="utf-8")  # forces a backup-aside

        seen: set[str] = set()
        real = os.replace

        def spy(src, dst):
            for p in (*Path(src).parents, *Path(dst).parents):
                if p.name.startswith((".tmp_", ".bak_")):
                    seen.add(p.name)
            return real(src, dst)

        with patch("src.etl.loader.os.replace", side_effect=spy):
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        tmp_names = {n for n in seen if n.startswith(".tmp_")}
        bak_names = {n for n in seen if n.startswith(".bak_")}
        assert len(tmp_names) == 1 and len(bak_names) == 1
        tmp_name, bak_name = tmp_names.pop(), bak_names.pop()
        assert re.fullmatch(r"\.tmp_\d{8}_\d{6}_[0-9a-f]{8}", tmp_name)
        assert re.fullmatch(r"\.bak_\d{8}_\d{6}_[0-9a-f]{8}", bak_name)
        # One call → one shared stamp (rollback restores from THIS run's backup only).
        assert tmp_name.removeprefix(".tmp_") == bak_name.removeprefix(".bak_")

    def test_staging_dir_collision_fails_loud(self, tmp_path):
        """A residual dir with the exact staging name (frozen clock + pinned uuid)
        raises instead of being silently shared/deleted."""
        loader = DataLoader(str(tmp_path))
        fixed_uuid = MagicMock()
        fixed_uuid.hex = "deadbeefcafe0000"
        # The colliding dir is FRESH, so the aged-.tmp_ sweep must leave it alone —
        # the collision then surfaces through exist_ok=False (fail loud).
        (tmp_path / ".tmp_20260716_030000_deadbeef").mkdir()

        with (
            patch("src.etl.loader.datetime") as dt,
            patch("src.etl.loader.uuid.uuid4", return_value=fixed_uuid),
            pytest.raises(FileExistsError),
        ):
            dt.now.return_value = datetime(2026, 7, 16, 3, 0, 0)
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        # Nothing was committed and the foreign dir was not destroyed.
        assert not (tmp_path / "csv" / "Students.csv").exists()
        assert (tmp_path / ".tmp_20260716_030000_deadbeef").exists()


class TestReconcileOutputDir:
    """W1b Item 4: ``save_all`` reconciles interrupted-run leftovers up front —
    a stranded ``.bak_*`` (DATA) warns + is MOVED into ``archive_<ts>_recovered/``
    (never deleted); aged ``.tmp_*`` staging (re-creatable scratch) is swept;
    fresh ``.tmp_*`` and every ``archive_*`` dir are left untouched."""

    def _outputs(self):
        return {"Students": pd.DataFrame({"Name": ["Alice"]})}

    def _orders(self):
        return {"Students": ["Name"]}

    @staticmethod
    def _recovered_dirs(tmp_path):
        return [
            p
            for p in tmp_path.iterdir()
            if p.is_dir() and p.name.startswith("archive_") and p.name.endswith("_recovered")
        ]

    def test_stale_backup_is_archived_with_a_loud_warning(self, tmp_path, caplog):
        loader = DataLoader(str(tmp_path))
        stale_bak = tmp_path / ".bak_20200101_000000_aaaaaaaa"
        stale_bak.mkdir()
        (stale_bak / "Students.csv").write_text("pre-crash originals", encoding="utf-8")
        aged = time.time() - (_STALE_BAK_MIN_AGE_SECONDS + 60)
        os.utime(stale_bak, (aged, aged))

        with caplog.at_level(logging.WARNING, logger="src.etl.loader"):
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        # Warned loudly that a previous run was interrupted.
        assert any("interrupted mid-commit" in r.message for r in caplog.records)
        # Moved aside — never deleted: the bytes survive inside archive_<ts>_recovered/.
        assert not stale_bak.exists()
        recovered = self._recovered_dirs(tmp_path)
        assert len(recovered) == 1
        moved = recovered[0] / stale_bak.name / "Students.csv"
        assert moved.read_text(encoding="utf-8") == "pre-crash originals"
        # And this run's write still committed normally.
        assert (tmp_path / "csv" / "Students.csv").exists()

    def test_fresh_backup_is_left_alone(self, tmp_path, caplog):
        # A young .bak_ may be a LIVE concurrent run's in-flight backup — moving
        # it would break that run's rollback (restore-before-cleanup invariant).
        loader = DataLoader(str(tmp_path))
        fresh_bak = tmp_path / ".bak_20260716_120000_bbbbbbbb"
        fresh_bak.mkdir()
        (fresh_bak / "Students.csv").write_text("live in-flight backup", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="src.etl.loader"):
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        assert fresh_bak.exists()
        assert (fresh_bak / "Students.csv").read_text(encoding="utf-8") == "live in-flight backup"
        assert not any("interrupted mid-commit" in r.message for r in caplog.records)
        assert self._recovered_dirs(tmp_path) == []

    def test_aged_tmp_swept_and_fresh_tmp_untouched(self, tmp_path, caplog):
        loader = DataLoader(str(tmp_path))
        old_tmp = tmp_path / ".tmp_old_leftover"
        old_tmp.mkdir()
        (old_tmp / "Staged.csv").write_text("abandoned staging", encoding="utf-8")
        aged = time.time() - (_STALE_TMP_MAX_AGE_DAYS + 1) * 86400
        os.utime(old_tmp, (aged, aged))
        fresh_tmp = tmp_path / ".tmp_fresh_leftover"
        fresh_tmp.mkdir()

        with caplog.at_level(logging.INFO, logger="src.etl.loader"):
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        # Aged staging is deleted (pure scratch) and the removal is logged.
        assert not old_tmp.exists()
        assert any("Removed abandoned staging directory .tmp_old_leftover" in r.message for r in caplog.records)
        # A fresh .tmp_ may belong to a live concurrent run — left alone.
        assert fresh_tmp.exists()

    def test_archive_dirs_are_never_touched(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        archive = tmp_path / "archive_20200101_000000"
        archive.mkdir()
        (archive / "Family.csv").write_text("archived data", encoding="utf-8")
        ancient = time.time() - 400 * 86400
        os.utime(archive, (ancient, ancient))

        loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        # Even an ancient archive dir is DATA — never swept, moved, or renamed.
        assert (archive / "Family.csv").read_text(encoding="utf-8") == "archived data"

    def test_reconcile_hiccup_never_blocks_the_write(self, tmp_path, caplog):
        """A failure moving the stale backup aside is logged at ERROR and skipped —
        cleaning up an OLD run must never block THIS run's commit, and the stale
        backup's data stays in place (nothing deleted)."""
        loader = DataLoader(str(tmp_path))
        stale_bak = tmp_path / ".bak_stale"
        stale_bak.mkdir()
        (stale_bak / "Students.csv").write_text("pre-crash originals", encoding="utf-8")
        aged = time.time() - (_STALE_BAK_MIN_AGE_SECONDS + 60)
        os.utime(stale_bak, (aged, aged))

        real = os.replace

        def deny_bak_move(src, dst):
            if Path(src).name.startswith(".bak_"):
                raise OSError("simulated lock on the stale backup dir")
            return real(src, dst)

        with (
            caplog.at_level(logging.ERROR, logger="src.etl.loader"),
            patch("src.etl.loader.os.replace", side_effect=deny_bak_move),
        ):
            loader.save_all(self._outputs(), self._orders(), zip_name=ZIP)

        assert any("Could not move leftover backup" in r.message for r in caplog.records)
        # The run still committed; the stale backup's bytes were never deleted.
        assert (tmp_path / "csv" / "Students.csv").exists()
        assert (stale_bak / "Students.csv").read_text(encoding="utf-8") == "pre-crash originals"


class TestEntityFilenameRule:
    """``csv_filename`` / ``output_filenames`` — the ONE entity→filename spelling.

    Shared by the write path, the stale-output detector and the SFTP delivery manifest.
    A second copy of ``f"{entity}.csv"`` would let the DELIVERED set drift from the
    WRITTEN set (the bug the manifest closes), so the rule is pinned here.
    """

    def test_csv_filename(self):
        assert DataLoader.csv_filename("Students") == "Students.csv"
        assert DataLoader.csv_filename("StudentAttendance") == "StudentAttendance.csv"

    def test_output_filenames_maps_a_whole_run(self):
        assert DataLoader.output_filenames(["Students", "Staff"]) == {"Students.csv", "Staff.csv"}
        assert DataLoader.output_filenames([]) == set()

    def test_written_file_matches_the_rule(self, tmp_path):
        """The write path uses the same rule — so a manifest built from it always matches."""
        loader = DataLoader(str(tmp_path))
        loader.save_all({"Students": pd.DataFrame({"User ID": ["1"]})}, {"Students": ["User ID"]}, zip_name=ZIP)
        written = {p.name for p in (tmp_path / "csv").glob("*.csv")}
        assert written == DataLoader.output_filenames(["Students"])


class TestDetectStaleOutputs:
    """Pure, non-destructive stale-output detection (Plan 0008, DSYNC-57 layout).

    Lists output files this run did not refresh; deletes NOTHING. The destructive (archive)
    decision lives in ``archive_stale_outputs``.
    """

    def test_detects_unemitted_entities_at_their_layout_paths(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Classes.csv", "stale-classes")
        _seed(tmp_path / "CourseInfo.csv", "stale-courseinfo")
        _seed(tmp_path / "notes.txt", "hand-written notes")

        stale = loader.detect_stale_outputs({"Students", "Staff"}, keep_zip=None)

        assert stale == ["CourseInfo.csv", "csv/Classes.csv"]
        assert (tmp_path / "csv" / "Classes.csv").exists()
        assert (tmp_path / "CourseInfo.csv").exists()
        assert (tmp_path / "notes.txt").exists()

    def test_a_top_level_rostering_csv_is_legacy_even_when_emitted(self, tmp_path):
        """Pre-DSYNC-57 a roster CSV sat beside where the zip now goes. Its fresh copy lives
        in csv/, so the top-level one is stale whether or not this run produced the entity."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "Students.csv", "pre-upgrade")
        _seed(tmp_path / "csv" / "Students.csv", "fresh")

        assert loader.detect_stale_outputs({"Students"}, keep_zip=None) == ["Students.csv"]

    def test_an_emitted_standalone_feed_is_not_stale(self, tmp_path):
        """A standalone feed's layout path IS the top level, so it is never mistaken for legacy."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "StudentAttendance.csv", "fresh")

        assert loader.detect_stale_outputs({"StudentAttendance"}, keep_zip=None) == []

    def test_earlier_zips_are_stale_and_this_runs_zip_is_kept(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / ZIP, "today")
        _seed(tmp_path / "acsv_sd40_2026-11-05.zip", "yesterday")
        _seed(tmp_path / "districtsync_sd40_2026-11-05.zip", "not ours to judge")

        assert loader.detect_stale_outputs(set(), keep_zip=ZIP) == ["acsv_sd40_2026-11-05.zip"]

    def test_a_run_that_wrote_no_zip_marks_every_zip_stale(self, tmp_path):
        """An attendance-only run leaves no roster bundle, so none may sit there looking current."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / ZIP, "earlier")

        assert loader.detect_stale_outputs({"StudentAttendance"}, keep_zip=None) == [ZIP]

    def test_keep_zip_is_required(self):
        param = inspect.signature(DataLoader.detect_stale_outputs).parameters["keep_zip"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


class TestArchiveStaleOutputs:
    """Stale outputs are MOVED into ``archive_<ts>/`` (non-destructive), never deleted, so the
    cross-config foot-gun under ``_base`` inheritance can't cause data loss, and nothing in
    the archive subdir can be delivered (delivery reads only the top-level zip and feeds).
    """

    @staticmethod
    def _archive_dirs(tmp_path):
        return [p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("archive_")]

    def test_archives_stale_files_keeping_their_relative_paths(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Classes.csv", "stale-classes")
        _seed(tmp_path / "CourseInfo.csv", "stale-courseinfo")
        _seed(tmp_path / "Students.csv", "pre-upgrade")
        _seed(tmp_path / "acsv_sd40_2026-11-05.zip", "yesterday")
        _seed(tmp_path / "notes.txt", "hand-written notes")

        archived = loader.archive_stale_outputs({"Students", "Staff"}, keep_zip=ZIP)

        assert archived == ["CourseInfo.csv", "Students.csv", "acsv_sd40_2026-11-05.zip", "csv/Classes.csv"]
        (archive_dir,) = self._archive_dirs(tmp_path)
        assert (archive_dir / "csv" / "Classes.csv").read_text(encoding="utf-8") == "stale-classes"
        assert (archive_dir / "CourseInfo.csv").read_text(encoding="utf-8") == "stale-courseinfo"
        assert (archive_dir / "Students.csv").read_text(encoding="utf-8") == "pre-upgrade"
        assert (archive_dir / "acsv_sd40_2026-11-05.zip").read_text(encoding="utf-8") == "yesterday"
        for rel in archived:
            assert not (tmp_path / rel).exists()
        assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hand-written notes"

    def test_emitted_entity_csv_and_this_runs_zip_are_never_archived(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Students.csv", "fresh")
        _seed(tmp_path / ZIP, "fresh-zip")
        _seed(tmp_path / "csv" / "Classes.csv", "stale")

        archived = loader.archive_stale_outputs({"Students"}, keep_zip=ZIP)

        assert archived == ["csv/Classes.csv"]
        assert (tmp_path / "csv" / "Students.csv").read_text(encoding="utf-8") == "fresh"
        assert (tmp_path / ZIP).read_text(encoding="utf-8") == "fresh-zip"

    def test_clean_run_creates_no_archive_dir_and_returns_empty(self, tmp_path):
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Classes.csv", "fresh")

        archived = loader.archive_stale_outputs({"Classes"}, keep_zip=None)

        assert archived == []
        assert self._archive_dirs(tmp_path) == []
        assert (tmp_path / "csv" / "Classes.csv").read_text(encoding="utf-8") == "fresh"

    def test_archive_move_failure_is_best_effort_and_does_not_raise(self, tmp_path):
        """A per-file move failure is logged and skipped, never raised, and does not abort the loop."""
        loader = DataLoader(str(tmp_path))
        _seed(tmp_path / "csv" / "Classes.csv", "stale-classes")
        _seed(tmp_path / "CourseInfo.csv", "stale-courseinfo")

        real_replace = os.replace

        def fail_on_classes(src, dst):
            if Path(src).name == "Classes.csv":
                raise OSError("simulated archive move failure")
            real_replace(src, dst)

        with patch("src.etl.loader.os.replace", side_effect=fail_on_classes):
            archived = loader.archive_stale_outputs({"Students", "Staff"}, keep_zip=None)

        assert archived == ["CourseInfo.csv"]
        assert (tmp_path / "csv" / "Classes.csv").read_text(encoding="utf-8") == "stale-classes"
        (archive_dir,) = self._archive_dirs(tmp_path)
        assert (archive_dir / "CourseInfo.csv").read_text(encoding="utf-8") == "stale-courseinfo"


class TestAdvancedCsvZip:
    """DSYNC-57: ``save_all`` writes the import-ready Advanced CSV zip in the same commit."""

    @staticmethod
    def _save(tmp_path, entities):
        loader = DataLoader(str(tmp_path))
        outputs = {e: pd.DataFrame({"ID": ["1"]}) for e in entities}
        return loader.save_all(outputs, {e: ["ID"] for e in entities}, zip_name=ZIP)

    def test_the_top_level_holds_only_the_upload_set(self, tmp_path):
        written = self._save(tmp_path, ["Students", "Staff", "StudentAttendance", "CourseInfo"])

        assert written == ZIP
        top = sorted(p.name for p in tmp_path.iterdir())
        assert top == sorted(["csv", ZIP, "StudentAttendance.csv", "CourseInfo.csv"])
        assert sorted(p.name for p in (tmp_path / "csv").iterdir()) == ["Staff.csv", "Students.csv"]

    def test_the_zip_holds_exactly_the_rostering_csvs_at_its_root(self, tmp_path):
        self._save(tmp_path, ["Students", "Enrollments", "StudentAttendance", "StudentCourses"])

        with zipfile.ZipFile(tmp_path / ZIP) as zf:
            assert sorted(zf.namelist()) == ["Enrollments.csv", "Students.csv"]
            assert zf.read("Students.csv") == (tmp_path / "csv" / "Students.csv").read_bytes()

    def test_a_standalone_only_run_writes_no_zip(self, tmp_path):
        assert self._save(tmp_path, ["StudentAttendance"]) is None
        assert sorted(p.name for p in tmp_path.iterdir()) == ["StudentAttendance.csv"]

    def test_a_failed_commit_leaves_the_previous_zip_and_csvs(self, tmp_path):
        """The zip is in the same transaction: a failure restores the prior zip too."""
        _seed(tmp_path / ZIP, "prior-zip")
        _seed(tmp_path / "csv" / "Students.csv", "prior-students")
        side_effect, state = _replace_side_effect(fail_role="promote", fail_entity="Students")

        with patch("src.etl.loader.os.replace", side_effect=side_effect), pytest.raises(OSError):
            self._save(tmp_path, ["Students", "Staff"])

        assert state["fired"]
        assert (tmp_path / ZIP).read_text(encoding="utf-8") == "prior-zip"
        assert (tmp_path / "csv" / "Students.csv").read_text(encoding="utf-8") == "prior-students"
        assert not (tmp_path / "csv" / "Staff.csv").exists()

    def test_zip_name_is_required(self):
        param = inspect.signature(DataLoader.save_all).parameters["zip_name"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


class TestOutputTargetProblem:
    """Pins ``output_target_problem``: which folders it refuses, that it really creates
    the folder, and that its ``.dsync_probe_*`` scratch dir is removed on success and
    reaped (prefix-scoped) when a previous call stranded one. The reason is LOG-ONLY, so
    the refusal rows assert a non-empty string rather than pinning wording.
    """

    def test_a_usable_directory_returns_none(self, tmp_path: Path):
        # The positive twin every refusal below pairs with: the same call shape says
        # "usable" for a folder that is.
        assert output_target_problem(str(tmp_path)) is None

    def test_a_creatable_nested_path_returns_none_and_is_created(self, tmp_path: Path):
        target = tmp_path / "rosters" / "output"
        assert output_target_problem(str(target)) is None
        assert target.is_dir(), "the pre-flight does the real mkdir the loader would do"

    @pytest.mark.parametrize("value", ["", "   "])
    def test_a_blank_folder_is_refused(self, value: str):
        problem = output_target_problem(value)
        assert isinstance(problem, str) and problem

    def test_a_leaf_that_is_a_file_is_refused(self, tmp_path: Path):
        leaf = tmp_path / "output"
        leaf.write_text("not a folder", encoding="utf-8")
        problem = output_target_problem(str(leaf))
        assert isinstance(problem, str) and problem
        assert leaf.read_text(encoding="utf-8") == "not a folder"  # refusing touches nothing

    def test_a_path_under_a_file_is_refused(self, tmp_path: Path):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("x", encoding="utf-8")
        problem = output_target_problem(str(blocker / "output"))
        assert isinstance(problem, str) and problem

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission bits don't gate directory creation on Windows"
    )
    def test_an_unwritable_directory_is_refused(self, tmp_path: Path):
        locked = tmp_path / "locked"
        locked.mkdir()
        os.chmod(locked, 0o500)  # r-x: the dir exists, but no subdir can be created in it
        try:
            problem = output_target_problem(str(locked))
            assert isinstance(problem, str) and problem
        finally:
            os.chmod(locked, 0o700)  # restore so tmp cleanup can remove it

    def test_the_probe_directory_is_removed_on_success(self, tmp_path: Path):
        assert output_target_problem(str(tmp_path)) is None
        assert list(tmp_path.iterdir()) == [], "the probe must not survive a successful check"

    def test_a_stale_probe_directory_is_reaped(self, tmp_path: Path):
        # Every other leftover class in an output folder has a reaper (.tmp_* at 7 days,
        # .bak_* at 1 hour). A probe dir stranded by a failed rmdir would have NONE at any
        # age — one per failed run, unbounded, on exactly the flaky shares this targets.
        stale = tmp_path / ".dsync_probe_leftover"
        stale.mkdir()
        assert output_target_problem(str(tmp_path)) is None
        assert not stale.exists(), "a stranded probe dir must be reaped by the next check"
        assert list(tmp_path.iterdir()) == []

    def test_the_reap_leaves_every_other_leftover_class_alone(self, tmp_path: Path):
        # The reap is keyed on the probe's own distinct prefix — it must never become a
        # second, quieter sweep competing with `_reconcile_output_dir`'s .bak_/.tmp_ rules
        # (a .bak_* is DATA and is never deleted at any age).
        for name in (".bak_20260101_000000_abcd1234", ".tmp_20260101_000000_abcd1234", "archive_20260101_000000"):
            (tmp_path / name).mkdir()
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")
        assert output_target_problem(str(tmp_path)) is None
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            ".bak_20260101_000000_abcd1234",
            ".tmp_20260101_000000_abcd1234",
            "Students.csv",
            "archive_20260101_000000",
        ]

    def test_a_failed_probe_cleanup_warns_but_still_reports_usable(self, tmp_path: Path, caplog):
        # Cleanup of our OWN leftover must never turn a usable folder into a refusal —
        # the next call's reap is what makes that self-healing.
        with (
            patch("src.etl.loader.Path.rmdir", side_effect=OSError("cannot remove")),
            caplog.at_level(logging.WARNING, logger="src.etl.loader"),
        ):
            assert output_target_problem(str(tmp_path)) is None
        assert caplog.records, "a failed probe cleanup must leave a trace"
        # And the leftover it left behind is reaped by the very next check.
        assert output_target_problem(str(tmp_path)) is None
        assert list(tmp_path.iterdir()) == []

    def test_the_reap_never_turns_a_usable_folder_into_a_refusal(self, tmp_path: Path, caplog):
        # BOTH arms of the best-effort promise, because either one raising would make
        # cleaning up an OLD leftover able to fail a folder that works.
        stale = tmp_path / ".dsync_probe_leftover"
        stale.mkdir()
        with (
            patch("src.etl.loader.shutil.rmtree", side_effect=OSError("in use")),
            caplog.at_level(logging.WARNING, logger="src.etl.loader"),
        ):
            assert output_target_problem(str(tmp_path)) is None
        assert caplog.records, "a failed reap must leave a trace"
        assert stale.exists(), "the leftover survives a failed reap (and is retried next call)"

        caplog.clear()
        with (
            patch("src.etl.loader.Path.glob", side_effect=OSError("cannot scan")),
            caplog.at_level(logging.WARNING, logger="src.etl.loader"),
        ):
            assert output_target_problem(str(tmp_path)) is None
        assert caplog.records, "a failed reap SCAN must leave a trace"

    def test_a_probe_already_swept_by_a_concurrent_run_is_not_warned_about(self, tmp_path: Path, caplog):
        # MEASURED race: `_reap_stale_probes` has no age gate, so a CONCURRENT run's LIVE
        # probe IS in glob range and IS removed. Benign — but only because the owner's
        # `rmdir` then fails FileNotFoundError, which must be treated as already-clean.
        # Warning here would send a support engineer after a non-problem in a district log.
        with (
            patch("src.etl.loader.Path.rmdir", side_effect=FileNotFoundError(2, "No such file or directory")),
            caplog.at_level(logging.WARNING, logger="src.etl.loader"),
        ):
            assert output_target_problem(str(tmp_path)) is None
        assert caplog.records == [], "a probe a concurrent run already swept is not a warning"

    def test_a_probe_dir_is_invisible_to_the_stale_output_detector(self, tmp_path: Path):
        # Verified, not assumed: every output-dir scan that could see the probe is file-name
        # keyed or branches on .bak_/.tmp_ only.
        (tmp_path / ".dsync_probe_live").mkdir()
        loader = DataLoader(str(tmp_path))
        assert loader.detect_stale_outputs({"Students"}, keep_zip=None) == []
        assert loader.archive_stale_outputs({"Students"}, keep_zip=None) == []
