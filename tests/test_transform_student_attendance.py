"""Integration tests for the StudentAttendance entity transformation.

Covers both bands that union into the single 4-column output:
  - K-7 Student Daily Absences (DERIVED category + row multiplicity).
  - 8-12 Student Period Absences (PER-PERIOD PASS-THROUGH: one output row per
    input row, category passed through as-is, no dedup).

StudentAttendance is opt-in — its template lives in the base myedbc config but
is only enabled where `enabled_entities` lists it (SD51). Tests use the
DataTransformer facade (like the other transformer tests) so they exercise the
same path the pipeline uses, including the runtime `global_config.attendance`
config injection. Synthetic data only — NO real PII.
"""

import pandas as pd
import pytest

from src.config.loader import load_config
from src.etl.transformer import DataTransformer

# The 4 required SpacesEDU columns in exact case-sensitive order. The contract
# permits dropping every optional field after Student Number, so these are the
# entire output — no trailing blank columns.
EXPECTED_COLUMNS = [
    "School Number",
    "Absence Date",
    "Absence Category",
    "Student Number",
]
POPULATED = ("School Number", "Absence Date", "Absence Category", "Student Number")


def _run(df, mapping, global_config, period_df=None):
    """Run the StudentAttendance transform via the facade.

    Both bands are resolved BY ROLE from raw_data (order-independent), not from
    the positional primary ``df``. ``df`` is registered under the daily band's
    filename (``StudentDailyAbsences.txt``); ``period_df``, when supplied, under
    the period band's filename. The positional primary passed to the facade
    mirrors what the pipeline would pass (the first declared source_files role's
    frame), but the transformer ignores it and resolves each band by role.
    """
    transformer = DataTransformer()
    transformer.set_school_year(2025, "08-25", "07-25")
    raw_data = {"StudentDailyAbsences.txt": df}
    if period_df is not None:
        raw_data["StudentPeriodAbsences.txt"] = period_df
    # The pipeline passes the frame of the FIRST declared source_files role as
    # the positional primary. Mirror that so this path matches the pipeline.
    roles = list(mapping.get("source_files", {}).keys())
    primary = df
    if roles and roles[0] == "period_absences" and period_df is not None:
        primary = period_df
    return transformer.transform(primary, mapping, "StudentAttendance", raw_data, global_config)


def _empty_daily():
    """An empty K-7 Daily frame with the normalized source columns present."""
    return pd.DataFrame(
        columns=[
            "school number",
            "student number",
            "absence date",
            "absent code am",
            "authorized am",
            "portion absent",
        ]
    )


class TestDailyCategoryDerivation:
    """(Absent Code, Authorized) -> Absence Category, per the configured map."""

    @pytest.mark.parametrize(
        "code,auth,expected",
        [
            ("A", "N", "A"),
            ("A", "Y", "A-E"),
            ("T", "N", "L"),
            ("T", "Y", "L-E"),
        ],
    )
    def test_category_table(self, code, auth, expected, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": [code],
                "authorized am": [auth],
                # 0.5 keeps row count at 1 except where the code is tardy; both
                # are fine for a category-only assertion.
                "portion absent": [0.5],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert set(result["Absence Category"]) == {expected}

    def test_case_insensitive_code_and_authorized(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["a"],
                "authorized am": ["y"],
                "portion absent": [0.5],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert list(result["Absence Category"]) == ["A-E"]


class TestPortionRowCount:
    """Portion Absent -> output row count (each row = one half-day)."""

    def test_full_day_produces_two_identical_rows(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [1.0],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 2
        # Two IDENTICAL rows (no AM/PM column — multiplicity carries the half-days).
        cols = list(POPULATED)
        assert result.iloc[0][cols].equals(result.iloc[1][cols])

    def test_half_day_produces_one_row(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [0.5],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 1

    def test_tardy_produces_one_row_even_at_full_portion(self, student_attendance_mapping, attendance_global_config):
        # A tardy is always a single half-day regardless of the portion value.
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["T"],
                "authorized am": ["N"],
                "portion absent": [1.0],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 1
        assert list(result["Absence Category"]) == ["L"]

    def test_blank_code_produces_no_rows(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": [""],
                "authorized am": [""],
                "portion absent": [0.0],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 0
        # Still the exact 4-column contract on an empty frame.
        assert list(result.columns) == EXPECTED_COLUMNS

    def test_fixture_row_counts(self, student_daily_absences_df, student_attendance_mapping, attendance_global_config):
        # S1 full-day(2) + S2 half(1) + S3 tardy(1) + S4 quarter(1) + S5 blank(0) = 5
        result = _run(student_daily_absences_df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 5
        # S5 (blank code) contributes nothing.
        assert "S5" not in set(result["Student Number"])


class TestOutputShape:
    def test_exactly_four_columns_in_exact_order(
        self, student_daily_absences_df, student_attendance_mapping, attendance_global_config
    ):
        result = _run(student_daily_absences_df, student_attendance_mapping, attendance_global_config)
        # Exactly the 4 required columns, in order — no extra/blank columns.
        assert list(result.columns) == EXPECTED_COLUMNS
        assert list(result.columns) == list(POPULATED)
        assert len(result.columns) == 4

    def test_all_columns_are_populated(
        self, student_daily_absences_df, student_attendance_mapping, attendance_global_config
    ):
        # Every output column carries data — no always-blank optional columns.
        result = _run(student_daily_absences_df, student_attendance_mapping, attendance_global_config)
        for col in EXPECTED_COLUMNS:
            assert (result[col].astype(str) != "").all(), f"{col} should be populated for every row"

    def test_populated_columns_carry_values(
        self, student_daily_absences_df, student_attendance_mapping, attendance_global_config
    ):
        result = _run(student_daily_absences_df, student_attendance_mapping, attendance_global_config)
        s1 = result[result["Student Number"] == "S1"].iloc[0]
        assert s1["School Number"] == "100"
        assert s1["Absence Category"] == "A"
        assert s1["Absence Date"] == "18-Sep-2024"

    def test_absence_date_formatted_dd_mmm_yyyy(self, student_attendance_mapping, attendance_global_config):
        # ISO input must be reformatted to DD-MMM-YYYY by the transform.
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["2024-09-18"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [0.5],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert list(result["Absence Date"]) == ["18-Sep-2024"]

    def test_no_dedup_two_identical_full_day_rows_survive(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [1.0],
            }
        )
        result = _run(df, student_attendance_mapping, attendance_global_config)
        # Both identical rows must remain — drop_duplicates must NOT be applied.
        assert len(result) == 2
        assert result.duplicated().sum() == 1


class TestFailLoud:
    def test_unmapped_code_authorized_pair_raises(self, student_attendance_mapping, attendance_global_config):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["Z"],  # not in category_map
                "authorized am": ["N"],
                "portion absent": [0.5],
            }
        )
        with pytest.raises(ValueError, match="no category mapping"):
            _run(df, student_attendance_mapping, attendance_global_config)

    def test_missing_attendance_config_raises(self, student_attendance_mapping):
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [0.5],
            }
        )
        # global_config with no `attendance` block at all.
        with pytest.raises(ValueError, match="attendance.daily"):
            _run(df, student_attendance_mapping, {})


class TestEmptyInput:
    def test_empty_frame_returns_4_col_empty(self, student_attendance_mapping, attendance_global_config):
        empty = pd.DataFrame(
            columns=[
                "school number",
                "student number",
                "absence date",
                "absent code am",
                "authorized am",
                "portion absent",
            ]
        )
        result = _run(empty, student_attendance_mapping, attendance_global_config)
        assert result.empty
        assert list(result.columns) == EXPECTED_COLUMNS


class TestStudentAttendanceConfigIntegration:
    def test_base_defines_template_but_does_not_enable(self):
        cfg = load_config("myedbc")
        assert "StudentAttendance" in cfg.mappings
        assert "StudentAttendance" not in cfg.global_config.enabled_entities

    def test_base_field_order_is_4_columns(self):
        cfg = load_config("myedbc")
        assert list(cfg.mappings["StudentAttendance"].field_map.keys()) == EXPECTED_COLUMNS

    def test_base_carries_daily_attendance_config(self):
        cfg = load_config("myedbc")
        daily = cfg.global_config.attendance["daily"]
        assert daily["category_map"] == {"A|N": "A", "A|Y": "A-E", "T|N": "L", "T|Y": "L-E"}
        assert daily["portion"]["full_day_value"] == 1.0
        assert daily["portion"]["full_day_rows"] == 2

    def test_sd51_enables_student_attendance_with_full_set(self):
        cfg = load_config("sd51myedbc")
        enabled = cfg.global_config.enabled_entities
        assert "StudentAttendance" in enabled
        # Deep-merge replaces lists, so the rostering entities must all still be there.
        assert set(enabled) == {
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
            "StudentAttendance",
        }

    def test_sd51_inherits_daily_attendance_config_and_daily_source(self):
        cfg = load_config("sd51myedbc")
        assert cfg.global_config.attendance["daily"]["daily_student_col"] == "student number"
        assert cfg.mappings["StudentAttendance"].source_files["daily_absences"] == "StudentDailyAbsences.txt"

    def test_base_carries_period_attendance_config(self):
        cfg = load_config("myedbc")
        period = cfg.global_config.attendance["period"]
        assert period == {
            "period_school_col": "school number",
            "period_student_col": "student number",
            "period_date_col": "absence date",
            "period_category_col": "absence category",
        }

    def test_base_declares_no_source_files(self):
        # The base declares NO source_files for StudentAttendance — each district
        # selects the band(s) it runs by which roles it declares. (Deep-merge
        # can't remove an inherited source_files key, so forcing both bands in
        # the base would prevent single-band districts.)
        cfg = load_config("myedbc")
        assert cfg.mappings["StudentAttendance"].source_files == {}

    def test_sd51_daily_source_is_first_so_it_stays_primary(self):
        # SD51 declares both bands; the pipeline's skip-on-empty-primary guard
        # keys on the first source file, so daily must remain first to keep the
        # always-present band primary.
        cfg = load_config("sd51myedbc")
        roles = list(cfg.mappings["StudentAttendance"].source_files.keys())
        assert roles[0] == "daily_absences"

    def test_sd51_inherits_period_source_under_base_filename(self):
        cfg = load_config("sd51myedbc")
        assert cfg.mappings["StudentAttendance"].source_files["period_absences"] == "StudentPeriodAbsences.txt"
        assert cfg.global_config.attendance["period"]["period_category_col"] == "absence category"


class TestPeriodPassThrough:
    """8-12 Student Period Absences — one output row per input row, category as-is."""

    def test_n_rows_in_n_rows_out(
        self, student_attendance_mapping, attendance_global_config, student_period_absences_df
    ):
        # 7 input rows: 5 valid + 1 blank student + 1 blank category -> 5 out.
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, student_period_absences_df)
        assert len(result) == 5

    def test_category_passed_through_unchanged(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100", "100", "100"],
                "student number": ["P1", "P2", "P3"],
                "absence date": ["18-Sep-2024", "18-Sep-2024", "18-Sep-2024"],
                # Two accepted codes + one NON-ACCEPTED that must survive as-is.
                "absence category": ["A-E", "AL", "OffSite"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert list(result["Absence Category"]) == ["A-E", "AL", "OffSite"]

    def test_non_accepted_category_survives(
        self, student_attendance_mapping, attendance_global_config, student_period_absences_df
    ):
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, student_period_absences_df)
        assert "OffSite" in set(result["Absence Category"])

    def test_date_formatted_dd_mmm_yyyy(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["P1"],
                "absence date": ["2024-09-18"],  # ISO input
                "absence category": ["A"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert list(result["Absence Date"]) == ["18-Sep-2024"]

    def test_exactly_four_columns_all_populated(
        self, student_attendance_mapping, attendance_global_config, student_period_absences_df
    ):
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, student_period_absences_df)
        assert list(result.columns) == EXPECTED_COLUMNS
        for col in EXPECTED_COLUMNS:
            assert (result[col].astype(str) != "").all(), f"{col} should be populated for every period row"

    def test_populated_columns_carry_values(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["200"],
                "student number": ["P9"],
                "absence date": ["18-Sep-2024"],
                "absence category": ["AD"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        row = result.iloc[0]
        assert row["School Number"] == "200"
        assert row["Student Number"] == "P9"
        assert row["Absence Category"] == "AD"
        assert row["Absence Date"] == "18-Sep-2024"

    def test_no_dedup_two_identical_period_rows_survive(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["P1", "P1"],
                "absence date": ["18-Sep-2024", "18-Sep-2024"],
                "absence category": ["A", "A"],  # identical -> both must survive
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert len(result) == 2
        assert result.duplicated().sum() == 1

    def test_blank_category_row_dropped(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["P1", "P2"],
                "absence date": ["18-Sep-2024", "18-Sep-2024"],
                "absence category": ["A", ""],  # second row blank category -> dropped
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert len(result) == 1
        assert list(result["Student Number"]) == ["P1"]

    def test_blank_student_number_row_dropped(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["P1", ""],  # second row blank student -> dropped
                "absence date": ["18-Sep-2024", "18-Sep-2024"],
                "absence category": ["A", "A"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert len(result) == 1
        assert list(result["Student Number"]) == ["P1"]

    def test_period_only_daily_empty(
        self, student_attendance_mapping, attendance_global_config, student_period_absences_df
    ):
        # Daily empty -> output is exactly the period rows.
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, student_period_absences_df)
        assert len(result) == 5
        assert set(result["Student Number"]) == {"P1", "P2", "P3", "P4"}

    def test_period_config_missing_when_data_present_raises(
        self, student_attendance_mapping, student_period_absences_df
    ):
        # Daily config present (so the daily band's fail-loud doesn't fire on the
        # empty frame) but period block absent while period data exists -> raise.
        gc = {"attendance": {"daily": load_config("myedbc").global_config.attendance["daily"]}}
        with pytest.raises(ValueError, match="attendance.period"):
            _run(_empty_daily(), student_attendance_mapping, gc, student_period_absences_df)


class TestBandUnion:
    """Both bands present -> output is the union (daily-derived + period rows)."""

    def test_union_counts(
        self,
        student_daily_absences_df,
        student_period_absences_df,
        student_attendance_mapping,
        attendance_global_config,
    ):
        # Daily band: S1 full-day(2) + S2 half(1) + S3 tardy(1) + S4 quarter(1) + S5 blank(0) = 5
        # Period band: 5 valid rows.
        result = _run(
            student_daily_absences_df,
            student_attendance_mapping,
            attendance_global_config,
            student_period_absences_df,
        )
        assert len(result) == 10

    def test_both_bands_present_in_output(
        self,
        student_daily_absences_df,
        student_period_absences_df,
        student_attendance_mapping,
        attendance_global_config,
    ):
        result = _run(
            student_daily_absences_df,
            student_attendance_mapping,
            attendance_global_config,
            student_period_absences_df,
        )
        students = set(result["Student Number"])
        # Daily band students (S*) and period band students (P*) both present.
        assert {"S1", "S2", "S3", "S4"} <= students
        assert {"P1", "P2", "P3", "P4"} <= students
        # Non-accepted period category survives through the union.
        assert "OffSite" in set(result["Absence Category"])

    def test_daily_only_when_period_absent(
        self, student_daily_absences_df, student_attendance_mapping, attendance_global_config
    ):
        # No period frame in raw_data -> only the daily-derived rows.
        result = _run(student_daily_absences_df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 5
        assert set(result["Student Number"]) == {"S1", "S2", "S3", "S4"}

    def test_union_preserves_4_col_contract(
        self,
        student_daily_absences_df,
        student_period_absences_df,
        student_attendance_mapping,
        attendance_global_config,
    ):
        result = _run(
            student_daily_absences_df,
            student_attendance_mapping,
            attendance_global_config,
            student_period_absences_df,
        )
        assert list(result.columns) == EXPECTED_COLUMNS


class TestSingleBandSelection:
    """A district selects bands by which `source_files` roles it declares.

    Daily-only, period-only, or both — each band is resolved BY ROLE
    (order-independent) and a band's config is required ONLY when that band's
    data is present.
    """

    def test_daily_only_mapping_produces_only_daily_rows(
        self, student_daily_absences_df, student_attendance_daily_only_mapping, attendance_global_config
    ):
        # Mapping declares ONLY daily_absences; a period frame in raw_data must
        # be ignored (no period_absences role -> band not resolved).
        result = _run(
            student_daily_absences_df,
            student_attendance_daily_only_mapping,
            attendance_global_config,
            period_df=pd.DataFrame(
                {
                    "school number": ["999"],
                    "student number": ["P9"],
                    "absence date": ["18-Sep-2024"],
                    "absence category": ["A"],
                }
            ),
        )
        # Only the 5 daily-derived rows; the unreferenced period frame is ignored.
        assert len(result) == 5
        assert set(result["Student Number"]) == {"S1", "S2", "S3", "S4"}
        assert "P9" not in set(result["Student Number"])

    def test_daily_only_works_with_no_period_config_block(
        self, student_daily_absences_df, student_attendance_daily_only_mapping
    ):
        # A daily-only district need NOT configure the period block.
        daily_block = load_config("myedbc").global_config.attendance["daily"]
        gc = {"attendance": {"daily": daily_block}}  # no `period` sub-block
        result = _run(student_daily_absences_df, student_attendance_daily_only_mapping, gc)
        assert len(result) == 5
        assert list(result.columns) == EXPECTED_COLUMNS

    def test_period_only_mapping_processes_period_as_passthrough(
        self, student_period_absences_df, student_attendance_period_only_mapping, attendance_global_config
    ):
        # Period-only district: the PERIOD frame is the pipeline's primary `df`.
        # It must be processed as pass-through period rows, NOT mis-handled as
        # K-7 daily data. `_run` passes period_df as the positional primary
        # because period_absences is the first declared role.
        result = _run(
            _empty_daily(),
            student_attendance_period_only_mapping,
            attendance_global_config,
            student_period_absences_df,
        )
        # 5 valid period rows (category passed through as-is); none of the daily
        # derivation knobs applied.
        assert len(result) == 5
        assert set(result["Student Number"]) == {"P1", "P2", "P3", "P4"}
        assert "OffSite" in set(result["Absence Category"])
        assert list(result.columns) == EXPECTED_COLUMNS

    def test_period_only_does_not_misprocess_as_daily(
        self, student_attendance_period_only_mapping, attendance_global_config
    ):
        # The period frame has NO daily columns (absent code / authorized /
        # portion). If it were mis-handled as daily it would raise on the
        # missing daily columns or category map. Pass-through must succeed.
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["P1", "P2"],
                "absence date": ["18-Sep-2024", "19-Sep-2024"],
                # 'AL' is a period code with NO entry in the daily category_map;
                # pass-through must NOT consult that map.
                "absence category": ["AL", "A-E"],
            }
        )
        result = _run(_empty_daily(), student_attendance_period_only_mapping, attendance_global_config, period)
        assert list(result["Absence Category"]) == ["AL", "A-E"]

    def test_period_only_works_with_no_daily_config_block(
        self, student_period_absences_df, student_attendance_period_only_mapping
    ):
        # A period-only district need NOT configure the daily block.
        period_block = load_config("myedbc").global_config.attendance["period"]
        gc = {"attendance": {"period": period_block}}  # no `daily` sub-block
        result = _run(_empty_daily(), student_attendance_period_only_mapping, gc, student_period_absences_df)
        assert len(result) == 5
        assert list(result.columns) == EXPECTED_COLUMNS
