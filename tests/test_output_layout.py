"""Tests for src/etl/output_layout.py: the output folder's one layout rule (DSYNC-57)."""

import zipfile
from datetime import date
from pathlib import Path

import pytest

from src.etl.output_layout import (
    ROSTERING_SUBDIR,
    STANDALONE_CSV_FILENAMES,
    acsv_zip_name,
    acsv_zips,
    csv_path,
    csv_relpath,
    district_slug,
    write_acsv_zip,
)


class TestDistrictSlug:
    def test_strips_myedbc_suffix(self):
        assert district_slug("sd40myedbc") == "sd40"
        assert district_slug("sd74myedbc") == "sd74"

    def test_base_myedbc_unchanged(self):
        assert district_slug("myedbc") == "myedbc"

    def test_sanitizes_special_characters(self):
        assert district_slug("myBlueprint+") == "myBlueprint"
        assert district_slug("sis with spaces") == "sis_with_spaces"
        assert district_slug("sis/with\\slashes") == "sis_with_slashes"

    def test_fallback_when_all_stripped(self):
        assert district_slug("+++") == "district"


class TestAcsvZipName:
    def test_names_the_district_then_the_date(self):
        assert acsv_zip_name("sd40myedbc", date(2026, 11, 6)) == "acsv_sd40_2026-11-06.zip"

    def test_base_district(self):
        assert acsv_zip_name("myedbc", date(2026, 11, 6)) == "acsv_myedbc_2026-11-06.zip"

    def test_defaults_to_today(self):
        assert acsv_zip_name("sd40myedbc") == f"acsv_sd40_{date.today().isoformat()}.zip"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_district_raises(self, blank):
        """No silent district (D9): an unattributable bundle name is a caller bug."""
        with pytest.raises(ValueError, match="needs a district"):
            acsv_zip_name(blank)


class TestStandaloneFeedNames:
    def test_names_match_the_loader_entity_filename_rule(self):
        from src.etl.loader import DataLoader

        assert (
            DataLoader.output_filenames({"StudentAttendance", "CourseInfo", "StudentCourses"})
            == STANDALONE_CSV_FILENAMES
        )

    def test_every_standalone_name_is_a_real_entity_file(self):
        """A typo ('StudentCourse.csv') would otherwise sit in the set silently and the feed
        would land in the zip, where it imports nothing (ACSV-026)."""
        from src.etl.loader import DataLoader
        from src.etl.transformers.registry import TRANSFORMER_REGISTRY

        assert DataLoader.output_filenames(TRANSFORMER_REGISTRY) >= STANDALONE_CSV_FILENAMES


class TestCsvPaths:
    def test_rostering_csvs_live_in_the_subfolder(self):
        assert csv_relpath("Students.csv") == Path(ROSTERING_SUBDIR) / "Students.csv"

    @pytest.mark.parametrize("name", sorted(STANDALONE_CSV_FILENAMES))
    def test_standalone_feeds_live_at_the_top_level(self, name, tmp_path):
        assert csv_relpath(name) == Path(name)
        assert csv_path(tmp_path, name) == tmp_path / name


class TestWriteAcsvZip:
    def test_members_sit_at_the_zip_root(self, tmp_path):
        """ACSV-025: no enclosing folder, so the importer reads them together."""
        sub = tmp_path / "csv"
        sub.mkdir()
        for name in ("Students.csv", "Classes.csv"):
            (sub / name).write_text("a\n1\n", encoding="utf-8")
        target = tmp_path / "acsv_sd40_2026-11-06.zip"

        write_acsv_zip(target, [sub / "Students.csv", sub / "Classes.csv"])

        with zipfile.ZipFile(target) as zf:
            assert zf.namelist() == ["Classes.csv", "Students.csv"]

    def test_a_standalone_feed_is_refused(self, tmp_path):
        (tmp_path / "StudentAttendance.csv").write_text("a\n", encoding="utf-8")
        with pytest.raises(ValueError, match="never go inside"):
            write_acsv_zip(tmp_path / "x.zip", [tmp_path / "StudentAttendance.csv"])
        assert not (tmp_path / "x.zip").exists()

    def test_an_empty_set_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="at least one"):
            write_acsv_zip(tmp_path / "x.zip", [])


class TestAcsvZips:
    def test_lists_only_top_level_acsv_zips(self, tmp_path):
        (tmp_path / "acsv_sd40_2026-11-06.zip").write_bytes(b"")
        (tmp_path / "other.zip").write_bytes(b"")
        (tmp_path / "archive_1").mkdir()
        (tmp_path / "archive_1" / "acsv_sd40_2026-11-05.zip").write_bytes(b"")

        assert [p.name for p in acsv_zips(tmp_path)] == ["acsv_sd40_2026-11-06.zip"]

    def test_a_missing_folder_is_empty(self, tmp_path):
        assert acsv_zips(tmp_path / "nope") == []
