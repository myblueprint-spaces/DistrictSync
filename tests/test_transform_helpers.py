"""Unit tests for the extracted transformer helper modules (0035 W4c debloat).

Covers the NEW seams introduced by the BaseTransformer decomposition:

- `ids.normalize_id_series` / `ids.clean_invalid_ids` — the single shared
  ID/join-key normalization (T2.4 DRY).
- `grades.split_by_homeroom_grades` — the hoisted grade→CEDS→homeroom split
  (T3.4; previously 4 duplicated sites in Classes/Enrollments).
- `BaseTransformer.resolve_column` — the shared field_map
  resolve-with-default idiom (T3.8).
- `dates` — the SINGLE input-format grid (withdraw + general dates merged)
  and the pure school-year determination.
- `BlendedClassDetector` — now a plain service class (LSP fix), with the
  one-pass teacher-position index proven equivalent to the legacy per-blend
  `isin` scan.
- `MappingConfig.check_required_entities` — now actually logs (was dead code).

The legacy call sites (`BaseTransformer.<helper>` wrappers) stay pinned by the
existing test files; these tests target the modules directly.
"""

import logging
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.config.models import MappingConfig
from src.etl.transformers import dates, grades, ids
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.blended import BlendedClassDetector

# ---------------------------------------------------------------------------
# ids.py
# ---------------------------------------------------------------------------


class TestNormalizeIdSeries:
    def test_strips_whitespace_and_stringifies(self):
        s = pd.Series([" S001 ", 1001, "T9\t"])
        assert list(ids.normalize_id_series(s)) == ["S001", "1001", "T9"]

    def test_nan_becomes_nan_string(self):
        # astype(str) semantics preserved — callers filter 'nan' explicitly.
        s = pd.Series([np.nan, "S1"])
        assert list(ids.normalize_id_series(s)) == ["nan", "S1"]

    def test_returns_new_series_input_untouched(self):
        s = pd.Series([" S001 "])
        out = ids.normalize_id_series(s)
        assert s.iloc[0] == " S001 "
        assert out is not s

    def test_wrapper_delegates(self):
        df = pd.DataFrame({"id": ["T1", "", "nan", None, "T2"]})
        out = BaseTransformer.clean_invalid_ids(df, "id")
        assert list(out["id"]) == ["T1", "T2"]


# ---------------------------------------------------------------------------
# grades.py — split_by_homeroom_grades (T3.4)
# ---------------------------------------------------------------------------


class TestSplitByHomeroomGrades:
    def _demo(self):
        return pd.DataFrame({"grade": ["K", "1", "8", "12"], "student": ["A", "B", "C", "D"]})

    def test_homeroom_flavor_converts_in_place_and_keeps_members(self):
        df = self._demo()
        out = grades.split_by_homeroom_grades(df, "grade", ["KG", "01"], keep="homeroom")
        # The source column is CEDS-converted IN PLACE (downstream reads it).
        assert list(df["grade"]) == ["KG", "01", "08", "12"]
        assert list(out["student"]) == ["A", "B"]

    def test_subject_flavor_adds_ceds_column_and_inverts(self):
        df = self._demo()
        out = grades.split_by_homeroom_grades(df, "grade", ["KG", "01"], keep="subject")
        # Raw grade preserved; CEDS lands in a NEW column (Classes re-derives Grade from raw).
        assert list(out["grade"]) == ["8", "12"]
        assert list(out["grade_ceds"]) == ["08", "12"]

    def test_subject_flavor_returns_copy(self):
        df = self._demo()
        out = grades.split_by_homeroom_grades(df, "grade", [], keep="subject")
        out.loc[out.index[0], "student"] = "MUTATED"
        assert "MUTATED" not in df["student"].values

    def test_missing_grade_column_fails_loud(self):
        df = pd.DataFrame({"other": ["x"]})
        with pytest.raises(KeyError):
            grades.split_by_homeroom_grades(df, "grade", ["KG"], keep="homeroom")


# ---------------------------------------------------------------------------
# BaseTransformer.resolve_column (T3.8)
# ---------------------------------------------------------------------------


class TestResolveColumn:
    def test_dict_with_column_lowercased(self):
        fm = {"Grade": {"column": "Grade Level", "transform": "grade_to_ceds"}}
        assert BaseTransformer.resolve_column(fm, "Grade", "grade") == "grade level"

    def test_dict_without_column_falls_back(self):
        fm = {"Grade": {"value": "01"}}
        assert BaseTransformer.resolve_column(fm, "Grade", "grade") == "grade"

    def test_missing_key_falls_back(self):
        assert BaseTransformer.resolve_column({}, "Grade", "grade") == "grade"

    def test_bare_string_yields_default_matching_legacy_sites(self):
        # Documented: the legacy inline sites (Class ID / Grade / School ID)
        # ignored a bare-string config — the shared helper preserves that.
        fm = {"Grade": "grade level"}
        assert BaseTransformer.resolve_column(fm, "Grade", "grade") == "grade"

    def test_none_sentinel_yields_default(self):
        assert BaseTransformer.resolve_column({"Grade": None}, "Grade", "grade") == "grade"


# ---------------------------------------------------------------------------
# dates.py — single format grid + pure school-year determination
# ---------------------------------------------------------------------------


class TestDatesModule:
    def test_single_shared_input_format_grid(self):
        # T3.8: the withdraw grid and the general grid are ONE constant now.
        assert dates.INPUT_DATE_FORMATS == ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")

    @pytest.mark.parametrize("value", ["15-Jan-2020", "2020-06-15", "06/15/2020", "15/06/2020"])
    def test_classify_withdraw_past_all_formats(self, value):
        assert dates.classify_withdraw(value, date(2025, 6, 1)) == (True, False)

    def test_classify_withdraw_blank_future_unparseable(self):
        today = date(2025, 6, 1)
        assert dates.classify_withdraw("", today) == (False, False)
        assert dates.classify_withdraw("2099-12-31", today) == (False, False)
        assert dates.classify_withdraw("garbage", today) == (True, True)

    def test_determine_school_year_prefers_sources_over_today(self):
        all_data = {"a.txt": pd.DataFrame({"school year": ["2025/2026"]})}
        year = dates.determine_school_year(all_data, {"r": "a.txt"}, "07-25", date(2030, 1, 1))
        assert year == 2026

    def test_determine_school_year_fallback_uses_rollover(self):
        year = dates.determine_school_year({}, {}, "07-25", date(2025, 8, 1))
        assert year == 2026  # past rollover → next academic year


# ---------------------------------------------------------------------------
# BlendedClassDetector — plain service class + one-pass teacher index (T3.2)
# ---------------------------------------------------------------------------


class TestBlendedServiceClass:
    def test_not_a_transformer_subclass(self):
        # LSP fix: no more BaseTransformer inheritance with a raising transform().
        assert not isinstance(BlendedClassDetector(), BaseTransformer)
        assert not hasattr(BlendedClassDetector, "transform")

    def _working(self):
        # Interleaved MT IDs so frame-order dedup differs from per-MT
        # concatenation order — the case that would expose a naive rewrite.
        return pd.DataFrame(
            {
                "master timetable id": ["MT2", "MT1", "MT2", "MT1", "MT3"],
                "teacher id": ["TB", "TA", "TB", "TB", "TZ"],
            }
        )

    def test_teacher_index_matches_legacy_isin_scan(self):
        working = self._working()
        positions = BlendedClassDetector._teacher_positions(working, "teacher id")
        for mt_ids in (["MT1", "MT2"], ["MT2"], ["MT1", "MT3"], ["MT1", "MT2", "MT3"]):
            legacy = working[working["master timetable id"].isin(mt_ids)]["teacher id"].unique().tolist()
            assert BlendedClassDetector._collect_teachers(positions, mt_ids) == legacy

    def test_unknown_mt_id_contributes_nothing(self):
        positions = BlendedClassDetector._teacher_positions(self._working(), "teacher id")
        assert BlendedClassDetector._collect_teachers(positions, ["NOPE"]) == []


# ---------------------------------------------------------------------------
# models.py — check_required_entities now logs (T2.4b)
# ---------------------------------------------------------------------------


def _entity(source: str) -> dict:
    return {"source_files": {"student_demographic": source}, "field_map": {"User ID": "student number"}}


class TestCheckRequiredEntitiesLogging:
    def _config(self, mappings: dict) -> dict:
        # Academic dates required whenever Classes is defined+enabled
        # (check_dates_required_for_classes) — supply them unconditionally.
        return {
            "version": "1.0",
            "sis": "testsis",
            "global_config": {"academic_start_month_day": "08-25", "academic_end_month_day": "07-25"},
            "mappings": mappings,
        }

    def test_missing_standard_entities_warn_at_load(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.config.models"):
            MappingConfig(**self._config({"Students": _entity("d.csv")}))
        warned = [r.message for r in caplog.records if "standard rostering entities" in r.message]
        assert len(warned) == 1
        assert "Classes" in warned[0] and "Enrollments" in warned[0]

    def test_extra_entities_log_debug_only(self, caplog):
        mappings = {
            name: _entity("d.csv") for name in ("Students", "Staff", "Family", "Classes", "Enrollments", "CourseInfo")
        }
        with caplog.at_level(logging.DEBUG, logger="src.config.models"):
            MappingConfig(**self._config(mappings))
        assert not any("standard rostering entities" in r.message for r in caplog.records)
        assert any("non-standard entities" in r.message and "CourseInfo" in r.message for r in caplog.records)

    def test_all_standard_present_stays_silent_at_warning(self, caplog):
        mappings = {name: _entity("d.csv") for name in ("Students", "Staff", "Family", "Classes", "Enrollments")}
        with caplog.at_level(logging.WARNING, logger="src.config.models"):
            MappingConfig(**self._config(mappings))
        assert not any("standard rostering entities" in r.message for r in caplog.records)
