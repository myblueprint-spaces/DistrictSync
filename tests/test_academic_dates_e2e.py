"""End-to-end coverage of the DERIVED academic Start Date / End Date values.

Every live district config resolves ``Classes."Start Date"`` / ``"End Date"``
to ``{use_academic_year: true}`` — the pipeline DERIVES the literal dates from
``global_config``'s academic month-days plus the determined school year. That
derive path had no value-level coverage:

* ``tests/test_contract.py`` asserts the two columns EXIST, never their values.
* ``tests/test_regression_sd74.py`` runs against the FROZEN config copy in
  ``tests/snapshots/config/``, which still pins ``use_academic_year: false`` +
  literal ``value:`` dates. That pin is deliberate — it keeps the golden
  time-independent — but it means the golden covers a config shape NO live
  district uses.

This module closes that gap at two altitudes:

1. every live config that emits Classes still DERIVES (no re-pinned literal) —
   which is what lets the single end-to-end run below generalise to the fleet,
   since districts inherit the base entity definition rather than redefining it;
2. the derived VALUES survive the whole pipeline into ``Classes.csv``, for both
   school-year determination paths — a source ``School Year`` column, and the
   calendar fallback around the configured rollover.

The clock is frozen at the established seam (``src.etl.transformers.base.datetime``
— see ``tests/test_school_year.py``) so the run date can never leak into an
assertion. Expected values are derived from the CONFIG's month-days, never
spelled inline: a test that hardcoded ``08-25`` would just mirror a config typo
back as "correct".
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.config.loader import load_config
from src.config.models import FieldAcademicYear, GlobalConfig
from src.main import main
from src.utils.paths import bundle_mappings_dir
from tests.test_pipeline_run_store import _write_myedbc_input

# The two Classes fields that resolve from the computed academic-year bounds.
ACADEMIC_DATE_FIELDS = ("Start Date", "End Date")

# The config exercised end-to-end. Districts inherit the base Classes entity
# (they select entities via `enabled_entities`, they do not redefine them), and
# `TestLiveConfigsDeriveAcademicDates` pins that none of them re-pins a literal
# — so one run over the base config covers the shared derive path.
E2E_SIS_TYPE = "myedbc"

# The shared `_write_myedbc_input` builder's StudentSchedule 'School Year' cell,
# and the end year it means under the MyEd BC convention ("2025/2026" is the
# academic period ENDING in 2026). Guarded at runtime by
# `_assert_fixture_school_year` so a change to the shared builder fails loudly
# here instead of silently weakening the assertion.
FIXTURE_SCHOOL_YEAR = "2025/2026"
FIXTURE_SCHOOL_YEAR_END = 2026

# Calendar year the frozen clock lives in for the fallback cases. Deliberately
# unrelated to both the fixture's school year and any plausible run date, so a
# passing assertion cannot be a coincidence of "today".
FROZEN_CLOCK_YEAR = 2031

ISO_DATE = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_sis_types() -> list[str]:
    """Every shipped mapping config, by its `--sis` name."""
    suffix = "_mapping.yaml"
    return sorted(p.name[: -len(suffix)] for p in bundle_mappings_dir().glob(f"*{suffix}"))


@contextmanager
def _frozen_clock(today: date):
    """Freeze "today" at the established seam (``src.etl.transformers.base``)."""
    with patch("src.etl.transformers.base.datetime") as mock_datetime:
        mock_datetime.now.return_value.date.return_value = today
        yield


def _global_config(sis_type: str = E2E_SIS_TYPE) -> GlobalConfig:
    return load_config(sis_type).global_config


def _rollover_date(year: int, global_config: GlobalConfig) -> date:
    """The configured school-year rollover, as a concrete date in *year*.

    Mirrors the pipeline's config resolution (``academic_year_rollover_month_day``
    falling back to ``academic_end_month_day``) so the oracle reads from config,
    not from a date literal.
    """
    month_day = global_config.academic_year_rollover_month_day or global_config.academic_end_month_day
    assert month_day, f"{E2E_SIS_TYPE} config has no academic rollover / end month-day"
    month, day = (int(part) for part in month_day.split("-"))
    return date(year, month, day)


def _read_classes(output_dir: Path) -> pd.DataFrame:
    return pd.read_csv(output_dir / "Classes.csv", encoding="utf-8-sig", dtype=str).fillna("")


def _sole_value(classes: pd.DataFrame, column: str) -> str:
    """The one non-blank value *column* carries across every Classes row."""
    values = set(classes[column])
    assert values, f"Classes.csv has no rows — nothing to assert about '{column}'"
    assert "" not in values, f"Classes '{column}' is blank on at least one row"
    assert len(values) == 1, f"Classes '{column}' differs across rows: {sorted(values)}"
    return values.pop()


def _iso_date(raw: str, column: str) -> date:
    """Parse an output date, failing with the SpacesEDU-facing reason."""
    try:
        return datetime.strptime(raw, ISO_DATE).date()
    except ValueError:
        pytest.fail(f"Classes '{column}' is not ISO yyyy-mm-dd (SpacesEDU import shape): {raw!r}")


def _assert_academic_bounds(classes: pd.DataFrame, end_year: int, global_config: GlobalConfig) -> None:
    """Assert the produced dates are the config month-days on the right years.

    *end_year* is the MyEd BC end-year convention ("2026" = the 2025-2026
    academic period), so Start Date must land in ``end_year - 1``.
    """
    start = _iso_date(_sole_value(classes, "Start Date"), "Start Date")
    end = _iso_date(_sole_value(classes, "End Date"), "End Date")

    assert f"{start.month:02d}-{start.day:02d}" == global_config.academic_start_month_day
    assert f"{end.month:02d}-{end.day:02d}" == global_config.academic_end_month_day
    assert start.year == end_year - 1, f"Start Date year {start.year} is not the year before end year {end_year}"
    assert end.year == end_year, f"End Date year {end.year} is not the determined end year {end_year}"
    assert start < end


def _assert_fixture_school_year(input_dir: Path, global_config: GlobalConfig) -> None:
    """Guard: the shared builder still supplies the school year we expect."""
    for filename in global_config.school_year_sources.values():
        frame = pd.read_csv(input_dir / filename, dtype=str)
        values = sorted(set(frame["School Year"]))
        assert values == [FIXTURE_SCHOOL_YEAR], f"{filename} school years changed: {values}"


def _drop_school_year_column(input_dir: Path, global_config: GlobalConfig) -> None:
    """Strip 'School Year' from every configured school-year SOURCE file.

    With no parseable school year in any configured source, the pipeline takes
    the rollover-aware calendar fallback — the ONLY path where the (frozen)
    clock decides the academic dates.
    """
    dropped = False
    for filename in global_config.school_year_sources.values():
        path = input_dir / filename
        frame = pd.read_csv(path, dtype=str)
        columns = [c for c in frame.columns if c.strip().lower() == "school year"]
        if columns:
            frame.drop(columns=columns).to_csv(path, index=False)
            dropped = True
    assert dropped, "fixture no longer carries a 'School Year' source column — fallback would not be exercised"


@pytest.fixture
def myedbc_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Synthetic myedbc GDE input + an empty output dir (both under tmp_path)."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    _write_myedbc_input(input_dir)
    return input_dir, output_dir


# ---------------------------------------------------------------------------
# 1. Config shape — the derive path is what every live district actually takes
# ---------------------------------------------------------------------------


class TestLiveConfigsDeriveAcademicDates:
    """No live config may re-pin Classes Start/End Date to a literal date.

    A re-pinned district silently ships one academic year's dates forever
    (the frozen snapshot config in ``tests/snapshots/config/`` is exactly that
    shape, kept deliberately). This also underwrites the end-to-end runs below
    generalising from the base config to the whole fleet.
    """

    @pytest.mark.parametrize("sis_type", _live_sis_types())
    @pytest.mark.parametrize("field_name", ACADEMIC_DATE_FIELDS)
    def test_classes_date_field_is_derived(self, sis_type: str, field_name: str) -> None:
        config = load_config(sis_type)
        if "Classes" not in config.active_entities():
            pytest.skip(f"{sis_type} does not emit Classes")

        field = config.mappings["Classes"].field_map[field_name]
        assert isinstance(field, FieldAcademicYear), (
            f"{sis_type}: Classes '{field_name}' is not an academic-year field ({type(field).__name__})"
        )
        assert field.use_academic_year is True, f"{sis_type}: Classes '{field_name}' disables use_academic_year"
        assert field.value is None, f"{sis_type}: Classes '{field_name}' re-pins a literal date ({field.value!r})"


# ---------------------------------------------------------------------------
# 2. End-to-end — the derived values reach Classes.csv
# ---------------------------------------------------------------------------


class TestDerivedAcademicDatesEndToEnd:
    """Full pipeline, frozen clock, real (non-frozen) config."""

    def test_dates_follow_the_source_school_year_not_the_clock(self, myedbc_dirs: tuple[Path, Path]) -> None:
        """A 'School Year' source column wins over "today".

        Catches: the academic bounds silently drifting to the run date on
        districts whose GDE does carry the year (i.e. all of them today).
        """
        input_dir, output_dir = myedbc_dirs
        global_config = _global_config()
        _assert_fixture_school_year(input_dir, global_config)

        with _frozen_clock(date(FROZEN_CLOCK_YEAR, 3, 3)):
            main(E2E_SIS_TYPE, str(input_dir), str(output_dir))

        _assert_academic_bounds(_read_classes(output_dir), FIXTURE_SCHOOL_YEAR_END, global_config)

    @pytest.mark.parametrize(
        ("offset_days", "end_year_offset"),
        [(-1, 0), (0, 1), (1, 1)],
        ids=["day-before-rollover", "on-rollover", "day-after-rollover"],
    )
    def test_dates_follow_the_clock_across_the_configured_rollover(
        self, myedbc_dirs: tuple[Path, Path], offset_days: int, end_year_offset: int
    ) -> None:
        """With no school-year source, the configured rollover picks the year.

        Catches: an off-by-one at the rollover boundary (``<`` vs ``<=``), and
        the end-year convention inverting so Start Date lands in the wrong
        calendar year.
        """
        input_dir, output_dir = myedbc_dirs
        global_config = _global_config()
        _drop_school_year_column(input_dir, global_config)

        today = _rollover_date(FROZEN_CLOCK_YEAR, global_config) + timedelta(days=offset_days)
        with _frozen_clock(today):
            main(E2E_SIS_TYPE, str(input_dir), str(output_dir))

        _assert_academic_bounds(_read_classes(output_dir), today.year + end_year_offset, global_config)
