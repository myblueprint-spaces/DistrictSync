"""`global_config.class_id_year` — which year suffixes a generated Class ID.

Class ID is IDENTITY in SpacesEDU, so this knob is pinned three ways: the one
reduction (`TransformContext.class_id_year`), its reach (every generated-ID site
reads it, and `to_raw_dict` carries it to the transformers), and its consumers
(only sd45myedbc sets it; every other config stays byte-identical on "end").
"""

import re
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.config.loader import available_configs, load_config
from src.config.models import GlobalConfig
from src.etl.transformer import DataTransformer
from src.etl.transformers.context import TransformContext

ETL_SRC = Path(__file__).resolve().parents[1] / "src" / "etl"


def _context(year: int, **global_config) -> TransformContext:
    ctx = TransformContext(global_config=dict(global_config))
    ctx.set_school_year(year, "08-25", "07-25")
    return ctx


class TestTheReduction:
    def test_absent_is_the_end_year(self):
        assert _context(2027).class_id_year == 2027

    def test_end_is_the_end_year(self):
        assert _context(2027, class_id_year="end").class_id_year == 2027

    def test_start_is_the_start_year(self):
        assert _context(2027, class_id_year="start").class_id_year == 2026

    def test_start_moves_only_the_id_never_the_dates(self):
        """The academic bounds still come from the END year — the knob is identity only."""
        ctx = _context(2027, class_id_year="start")
        assert (ctx.academic_start, ctx.academic_end) == ("2026-08-25", "2027-07-25")


class TestTheIdSites:
    def test_the_facade_generate_class_id_reads_it(self):
        transformer = DataTransformer()
        transformer._context.global_config = {"class_id_year": "start"}
        transformer.set_school_year(2027, "08-25", "07-25")
        row = pd.Series({"master timetable id": "MT001"})
        assert transformer.generate_class_id(row, "master timetable id", append_year=True) == "MT001_2026"

    def test_no_generated_id_is_built_from_school_year_directly(self):
        """Every `<id>_<year>` f-string under src/etl must go through `class_id_year`;
        a site reading `school_year` would split Classes from Enrollments (or a
        blend from its sections) on a start-year district. Names (no leading
        underscore) legitimately keep the school year and are not matched."""
        offenders = [
            f"{path.relative_to(ETL_SRC)}:{n}"
            for path in ETL_SRC.rglob("*.py")
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"_\{(self\._)?context\.school_year\}", line)
        ]
        assert offenders == []


class TestTheConfigKey:
    def test_defaults_to_end(self):
        assert GlobalConfig().class_id_year == "end"

    def test_rejects_anything_else(self):
        with pytest.raises(ValidationError):
            GlobalConfig(class_id_year="first")

    def test_to_raw_dict_carries_it_to_the_transformers(self):
        """The positive twin of the default: without this key in `to_raw_dict`,
        the setting validates and then silently does nothing."""
        assert load_config("sd45myedbc").to_raw_dict()["global_config"]["class_id_year"] == "start"
        assert load_config("myedbc").to_raw_dict()["global_config"]["class_id_year"] == "end"

    def test_only_sd45_sets_start(self):
        """A config fact: SD45's pre-DistrictSync converter keyed classes on the
        start year. A future district that needs it edits this deliberately."""
        starters = {n for n in available_configs() if load_config(n).global_config.class_id_year == "start"}
        assert starters == {"sd45myedbc"}
