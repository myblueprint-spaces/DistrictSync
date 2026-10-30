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

import copy
import logging

import pandas as pd
import pytest

from src.config.loader import load_config
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.transformer import DataTransformer
from src.etl.transformers.student_attendance import StudentAttendanceTransformer
from tests.contract_schema import OUTPUT_SCHEMA

# The 4 required SpacesEDU columns in exact case-sensitive order. The contract
# permits dropping every optional field after Student Number, so these are the
# entire output — no trailing blank columns.
#
# LAYER NOTE — the VALUE is shared, the LAYER is not. This module asserts the
# shape of the transformer's IN-MEMORY frame (pre-loader); tests/test_contract.py
# asserts the ON-DISK header of the written CSV. Two genuinely different layers
# that must agree, so they now read the same published contract
# (tests/contract_schema.py -> docs/developer/output-contract.md) instead of
# each restating it. Asserting here against a local copy would let the frame and
# the file drift apart while both stayed green.
EXPECTED_COLUMNS = OUTPUT_SCHEMA["StudentAttendance"]


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
        assert result.iloc[0][EXPECTED_COLUMNS].equals(result.iloc[1][EXPECTED_COLUMNS])

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
        assert s1["Absence Date"] == "2024-09-18"

    def test_absence_date_formatted_iso(self, student_attendance_mapping, attendance_global_config):
        # A dd-MMM-yyyy GDE date must be reformatted to ISO yyyy-MM-dd by the transform.
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
        assert list(result["Absence Date"]) == ["2024-09-18"]

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
        with pytest.raises(ValueError, match="no category mapping") as raised:
            _run(df, student_attendance_mapping, attendance_global_config)
        # A code-shaped value IS echoed, with the key to add — that is what makes it actionable.
        assert "Absent Code='Z'" in str(raised.value) and "Add 'Z|N'" in str(raised.value)

    def test_a_value_that_is_not_code_shaped_is_never_echoed(
        self, student_attendance_mapping, attendance_global_config
    ):
        """Plan 0053 S4 (§8): a daily file whose columns do not line up puts ANY cell where the
        code belongs — a pupil's name included — and this message reaches the log (the
        ``ENTITY NOT BUILT`` traceback). A non-code value is described by its length only."""
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": ["Pupilsurname"],  # a shifted column: a NAME where the code belongs
                "authorized am": ["Firstname"],
                "portion absent": [0.5],
            }
        )
        with pytest.raises(ValueError, match="no category mapping") as raised:
            _run(df, student_attendance_mapping, attendance_global_config)
        message = str(raised.value)
        assert "Pupilsurname" not in message and "Firstname" not in message
        assert "PUPILSURNAME" not in message, "nor its upper-cased category-map key"
        assert "<a 12-character value that is not a code — not shown>" in message
        assert "columns are not in the order" in message

    @pytest.mark.parametrize(
        ("code", "flag", "hidden"),
        [
            ("Li", "Kai", ("Li", "Kai", "LI", "KAI")),
            ("Wong", "Y", ("Wong", "WONG")),
            ("Z", "Ava", ("Ava", "AVA")),
        ],
        ids=["two-short-names", "short-surname-with-a-real-flag", "real-code-with-a-short-name"],
    )
    def test_a_SHORT_name_is_never_echoed_either(
        self, student_attendance_mapping, attendance_global_config, code, flag, hidden
    ):
        """A 1-4 letter mixed-case name is as short as a code — the vocabulary is closed
        (upper-case codes, Y/N/blank flags), so a name cannot pass as one (PRIV-1)."""
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": [code],
                "authorized am": [flag],
                "portion absent": [0.5],
            }
        )
        with pytest.raises(ValueError, match="no category mapping") as raised:
            _run(df, student_attendance_mapping, attendance_global_config)
        message = str(raised.value)
        for value in hidden:
            assert f"'{value}'" not in message and f"{value}|" not in message and f"|{value}" not in message
        assert "not a code — not shown" in message
        assert "Add '" not in message, "a pair holding a non-code must not be offered as a key to add"

    @staticmethod
    def _daily_without_authorized(code: str = "A") -> pd.DataFrame:
        return pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["18-Sep-2024"],
                "absent code am": [code],
                "portion absent": [0.5],
            }
        )

    def test_an_absent_authorized_column_is_a_typed_join_key_error_naming_it(
        self, student_attendance_mapping, attendance_global_config
    ):
        """§5 #33, owner ruling 2026-09-26 (plan 0053 S13b): the authorized flag is half of every
        category-map key, so its COLUMN is required once the daily band has rows — a typed error
        naming it in config spelling (the entity then reads FAILED/missing_source_column), never
        #23's untyped category-map miss."""
        with pytest.raises(SourceSchemaError) as raised:
            _run(self._daily_without_authorized(), student_attendance_mapping, attendance_global_config)
        err = raised.value
        configured = attendance_global_config["attendance"]["daily"]["daily_authorized_col"]
        assert (err.entity, err.columns, err.guard) == ("StudentAttendance", (configured,), GuardKind.JOIN_KEY)
        assert "S1" not in str(err) and "the source has 5 columns" in str(err)  # a count, never a value

    def test_the_error_names_the_column_in_the_configs_own_spelling(
        self, student_attendance_mapping, attendance_global_config
    ):
        """Config spelling, trimmed, never lower-cased — what S7's label vocabulary declares."""
        gc = copy.deepcopy(attendance_global_config)
        gc["attendance"]["daily"]["daily_authorized_col"] = "  Authorized AM "
        with pytest.raises(SourceSchemaError) as raised:
            _run(self._daily_without_authorized(), student_attendance_mapping, gc)
        assert raised.value.columns == ("Authorized AM",)

    def test_twin_the_column_present_or_the_band_empty_requires_nothing(
        self, student_attendance_mapping, attendance_global_config
    ):
        present = self._daily_without_authorized().assign(**{"authorized am": ["N"]})
        assert len(_run(present, student_attendance_mapping, attendance_global_config)) == 1
        empty = self._daily_without_authorized().iloc[0:0]
        assert _run(empty, student_attendance_mapping, attendance_global_config).empty

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


class TestConfigurableDateFormat:
    """`global_config.attendance.date_format` controls the Absence Date output shape.

    Default (base config) is ISO `yyyy-MM-dd`; a district may override. Input GDE
    dates in any recognized format are still parsed regardless of the setting.
    """

    @staticmethod
    def _gc_with_format(attendance_global_config, date_format):
        gc = copy.deepcopy(attendance_global_config)
        gc["attendance"]["date_format"] = date_format
        return gc

    def test_default_is_iso(self, student_attendance_mapping, attendance_global_config):
        # Base config carries no override -> ISO yyyy-MM-dd.
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
        assert list(result["Absence Date"]) == ["2024-09-18"]

    def test_daily_override_changes_output(self, student_attendance_mapping, attendance_global_config):
        gc = self._gc_with_format(attendance_global_config, "dd-MMM-yyyy")
        df = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["2024-09-18"],  # ISO input -> reformatted to the override
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": [0.5],
            }
        )
        result = _run(df, student_attendance_mapping, gc)
        assert list(result["Absence Date"]) == ["18-Sep-2024"]

    def test_period_override_changes_output(self, student_attendance_mapping, attendance_global_config):
        gc = self._gc_with_format(attendance_global_config, "dd-MMM-yyyy")
        period = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["P1"],
                "absence date": ["2024-09-18"],
                "absence category": ["A"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, gc, period)
        assert list(result["Absence Date"]) == ["18-Sep-2024"]

    def test_unsupported_format_raises(self, student_attendance_mapping, attendance_global_config):
        # A lowercase `mm` typo fails loud before any rows are built.
        gc = self._gc_with_format(attendance_global_config, "yyyy-mm-dd")
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
        with pytest.raises(ValueError, match="Unsupported date_format"):
            _run(df, student_attendance_mapping, gc)


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

    def test_base_carries_attendance_date_format_default(self):
        cfg = load_config("myedbc")
        assert cfg.global_config.attendance["date_format"] == "yyyy-MM-dd"

    def test_sd51_inherits_attendance_date_format_default(self):
        # SD51 uses the default ISO shape (no per-district override).
        cfg = load_config("sd51myedbc")
        assert cfg.global_config.attendance["date_format"] == "yyyy-MM-dd"

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
        # Deep-merge replaces lists, so the rostering entities SD51 emits must all still be
        # there. Family is deliberately absent (off for SD51 since 2026-09-25 — its contacts
        # export has no email column; pinned in test_config.py::test_sd51_does_not_enable_family).
        assert set(enabled) == {
            "Students",
            "Staff",
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

    def test_sd51_period_source_is_the_enhanced_export(self):
        # SD51's MyEd job emits `StudentPeriodAbsencesEnhanced.txt`, NOT the base's
        # standard headerless `StudentPeriodAbsences.txt`. This is pinned because the
        # override reads like a typo against DECISIONS 2026-06-19 (which chose the
        # standard file) and reverting it is SILENT: a missing file resolves to an
        # empty frame, so every 8-12 absence would simply vanish from the delivery
        # with no error. Confirmed against the district's 2026-09-11 drop.
        cfg = load_config("sd51myedbc")
        assert cfg.mappings["StudentAttendance"].source_files["period_absences"] == "StudentPeriodAbsencesEnhanced.txt"
        assert cfg.global_config.attendance["period"]["period_category_col"] == "absence category"

    def test_sd51_period_file_is_read_headerful(self):
        # The base's `headers:` block is keyed by FILENAME, so naming the Enhanced
        # export must leave it WITHOUT injected headers — it is headerful (19 cols vs
        # the standard's 17, diverging from position 13), so injecting the standard's
        # positional names would mislabel every column past 12.
        cfg = load_config("sd51myedbc")
        entity = cfg.mappings["StudentAttendance"]
        period_file = entity.source_files["period_absences"]
        assert period_file not in (entity.headers or {})
        # Positive twin: the daily band IS headerless and DOES still get its headers,
        # so the assertion above is proving a real distinction, not an empty dict.
        assert entity.source_files["daily_absences"] in (entity.headers or {})

    def test_sd51_attendance_tier_inherits_the_enhanced_period_source(self):
        # The attendance-only tier inherits via `_base: sd51myedbc`; it must not be
        # left pointing at a file the district does not send.
        cfg = load_config("sd51attendance")
        assert cfg.mappings["StudentAttendance"].source_files["period_absences"] == "StudentPeriodAbsencesEnhanced.txt"

    def test_base_still_declares_the_standard_period_headers(self):
        # SD51's override is district-scoped: the base keeps the standard headerless
        # file and its 17 injected columns for every district that sends that shape.
        cfg = load_config("myedbc")
        assert "StudentPeriodAbsences.txt" in (cfg.mappings["StudentAttendance"].headers or {})


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

    def test_date_formatted_iso(self, student_attendance_mapping, attendance_global_config):
        period = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["P1"],
                "absence date": ["18-Sep-2024"],  # dd-MMM-yyyy input
                "absence category": ["A"],
            }
        )
        result = _run(_empty_daily(), student_attendance_mapping, attendance_global_config, period)
        assert list(result["Absence Date"]) == ["2024-09-18"]

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
        assert row["Absence Date"] == "2024-09-18"

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


class TestCrossBandStudentDaySuppression:
    """A student-day recorded in BOTH bands is reported ONCE, by the daily band.

    WHY: the two GDE files are two SOURCES, not two independent facts. A
    district taking attendance in a homeroom/attendance-only section (MyEd BC's
    ``ATT--*``) emits the same absence twice — once daily, once as a period row
    against that section. SpacesEDU cannot tell them apart (the output carries
    no band marker), so it weighs each row by the student's GRADE and adds them
    up: a half-day absence becomes a full day.

    Found live on SD60's 2026-09-09 drop, where all 73 period rows were
    ``ATT--AM`` for grades 1-6 and 44 duplicated a daily row — 34 student-days
    came out over a full day. Every "is suppressed" case below is twinned with
    one proving a period-only student-day still survives, so a rule that
    suppressed everything could not pass both.
    """

    _DAILY = [
        {"School Number": "100", "Absence Date": "2025-10-01", "Absence Category": "A", "Student Number": "S1"},
        {"School Number": "100", "Absence Date": "2025-10-01", "Absence Category": "A", "Student Number": "S1"},
    ]

    @staticmethod
    def _period(student="S1", date="2025-10-01", school="100", category="A-E"):
        return {
            "School Number": school,
            "Absence Date": date,
            "Absence Category": category,
            "Student Number": student,
        }

    # -- the rule fires ---------------------------------------------------

    def test_overlapping_student_day_is_suppressed(self):
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, [self._period()])
        assert kept == [] and n == 1

    def test_suppression_ignores_a_differing_category(self):
        """SD60's real case: daily derived "A", period passed through "AD-E".

        Same student, same school, same day — still one absence. Keying on the
        category would have let the duplicate through.
        """
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(
            self._DAILY, [self._period(category="AD-E")]
        )
        assert kept == [] and n == 1

    def test_every_period_row_for_an_overlapping_day_is_suppressed(self):
        rows = [self._period(), self._period(category="L"), self._period(category="A")]
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, rows)
        assert kept == [] and n == 3

    # -- the rule stands down (twins for each case above) ------------------

    def test_period_only_student_day_survives(self):
        rows = [self._period(student="S2")]
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, rows)
        assert kept == rows and n == 0

    def test_same_student_different_date_survives(self):
        rows = [self._period(date="2025-10-02")]
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, rows)
        assert kept == rows and n == 0

    def test_same_student_and_date_at_a_DIFFERENT_school_survives(self):
        """School is part of the identity — a cross-enrolled pupil has two days."""
        rows = [self._period(school="200")]
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, rows)
        assert kept == rows and n == 0

    def test_genuine_per_period_multiplicity_is_untouched(self):
        """An 8-12 district with no daily band keeps every per-period row.

        Four period entries = one day at the 0.25 weighting; suppressing any of
        them would under-report the absence.
        """
        rows = [self._period(student="S9") for _ in range(4)]
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows([], rows)
        assert kept == rows and n == 0

    def test_no_period_rows_is_a_no_op(self):
        kept, n = StudentAttendanceTransformer.suppress_overlapping_period_rows(self._DAILY, [])
        assert kept == [] and n == 0

    # -- end to end -------------------------------------------------------

    def test_end_to_end_overlap_is_reported_once(self, student_attendance_mapping, attendance_global_config):
        """S1 is absent all day in BOTH files: 2 rows (one day), not 3."""
        daily = pd.DataFrame(
            {
                "school number": ["100"],
                "student number": ["S1"],
                "absence date": ["2025-10-01"],
                "absent code am": ["A"],
                "authorized am": ["N"],
                "portion absent": ["1.0"],
            }
        )
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["S1", "S2"],
                "absence date": ["2025-10-01", "2025-10-01"],
                "absence category": ["A-E", "A"],
            }
        )
        result = _run(daily, student_attendance_mapping, attendance_global_config, period)
        assert len(result) == 3  # S1 full day (2 daily rows) + S2 period-only (1)
        s1 = result[result["Student Number"] == "S1"]
        assert len(s1) == 2, "S1's day must come from the daily band alone"
        assert set(s1["Absence Category"]) == {"A"}, "the derived category wins, not the passed-through one"
        assert len(result[result["Student Number"] == "S2"]) == 1


class TestGradeWeightedRowCount:
    """The daily band's row count follows the student's grade (2026-10-30, validation rule ATT-017).

    SpacesEDU's attendance importer weighs every row by the student's grade — K-7 = 0.5 day per
    row, grade 8 and up = 0.25 day per row, capped at 1 day, tardies 1 each unweighted — so a
    grade 8+ full day needs FOUR rows where a K-7 one needs two. The K-7 path is unchanged.
    """

    @staticmethod
    def _daily(grades, codes, portions):
        n = len(grades)
        return pd.DataFrame(
            {
                "school number": ["100"] * n,
                "student number": [f"S{i}" for i in range(n)],
                "absence date": ["2025-10-01"] * n,
                "grade": grades,
                "absent code am": codes,
                "authorized am": ["N"] * n,
                "portion absent": portions,
            }
        )

    @staticmethod
    def _counts(df, mapping, global_config):
        result = _run(df, mapping, global_config)
        counts = result["Student Number"].value_counts()
        return [int(counts.get(f"S{i}", 0)) for i in range(len(df))]

    # -- K-7: byte-identical to the grade-blind rule ------------------------

    K7_PORTIONS = [1.0, 0.5, 0.25, 0.75, 0.0, "", "n/a", 1.5]

    @pytest.mark.parametrize("grade", ["KF", "K", "KG", "01", "1", "04", "07", "7"])
    def test_k7_counts_are_the_grade_blind_counts(self, grade, student_attendance_mapping, attendance_global_config):
        n = len(self.K7_PORTIONS)
        df = self._daily([grade] * n, ["A"] * n, self.K7_PORTIONS)
        assert self._counts(df, student_attendance_mapping, attendance_global_config) == [2, 1, 1, 1, 1, 1, 1, 1]

    def test_k7_frame_equals_the_frame_built_without_a_grade_column(
        self, student_attendance_mapping, attendance_global_config
    ):
        """Golden: every K-7 row ships exactly what the band shipped before it read the grade."""
        grades = ["KF", "01", "03", "05", "07", "02", "06", "04", "K", "07"]
        codes = ["A", "A", "T", "A", "A", "T", "A", "A", "A", ""]
        portions = [1.0, 0.5, 1.0, 0.25, 0.75, 0.5, "", 1.0, 0.0, 0.0]
        graded = self._daily(grades, codes, portions)
        graded["authorized am"] = ["N", "Y", "N", "Y", "N", "Y", "N", "Y", "N", ""]
        blind = graded.drop(columns=["grade"])
        expected = _run(blind, student_attendance_mapping, attendance_global_config)
        actual = _run(graded, student_attendance_mapping, attendance_global_config)
        pd.testing.assert_frame_equal(actual, expected)
        assert len(actual) == 2 + 1 + 1 + 1 + 1 + 1 + 1 + 2 + 1 + 0

    # -- grade 8+ ------------------------------------------------------------

    @pytest.mark.parametrize("grade", ["08", "8", "09", "10", "12", "13"])
    def test_upper_grade_full_day_is_four_identical_rows(
        self, grade, student_attendance_mapping, attendance_global_config
    ):
        result = _run(self._daily([grade], ["A"], [1.0]), student_attendance_mapping, attendance_global_config)
        assert len(result) == 4
        assert (result == result.iloc[0]).all().all(), "four IDENTICAL rows"

    @pytest.mark.parametrize(
        ("portion", "rows"),
        [
            (0.1, 1),  # 0.4 rows -> at least one row (a partial absence is never nothing)
            (0.125, 1),  # 0.5 rows -> rounds UP to 1
            (0.25, 1),
            (0.3333, 1),
            (0.375, 2),  # 1.5 rows -> rounds UP to 2
            (0.5, 2),
            ("0.5000", 2),  # the GDE's own spelling
            (0.6667, 3),
            (0.75, 3),
            (0.9, 3),  # 3.6 rows -> kept below a whole day
        ],
    )
    def test_upper_grade_partial_portion_scales(
        self, portion, rows, student_attendance_mapping, attendance_global_config
    ):
        df = self._daily(["10"], ["A"], [portion])
        assert self._counts(df, student_attendance_mapping, attendance_global_config) == [rows]

    @pytest.mark.parametrize("portion", [0.0, "", "n/a", 1.5, -0.5])
    def test_upper_grade_unreadable_portion_is_half_a_day(
        self, portion, student_attendance_mapping, attendance_global_config
    ):
        """Two 0.25-day rows — the same half day K-7's `default_rows` (one 0.5-day row) carries."""
        df = self._daily(["11"], ["A"], [portion])
        assert self._counts(df, student_attendance_mapping, attendance_global_config) == [2]

    @pytest.mark.parametrize("grade", ["KF", "03", "07", "08", "12", "", "UG"])
    @pytest.mark.parametrize("portion", [1.0, 0.5, 0.0])
    def test_tardy_is_one_row_at_every_grade(
        self, grade, portion, student_attendance_mapping, attendance_global_config
    ):
        df = self._daily([grade], ["T"], [portion])
        result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 1
        assert list(result["Absence Category"]) == ["L"]

    def test_mixed_grade_file(self, student_attendance_mapping, attendance_global_config):
        grades = ["KF", "07", "08", "12", "10", "05", "09", "11"]
        codes = ["A", "A", "A", "A", "A", "A", "T", ""]
        portions = [1.0, 1.0, 1.0, 0.5, 0.25, 0.5, 1.0, 1.0]
        df = self._daily(grades, codes, portions)
        assert self._counts(df, student_attendance_mapping, attendance_global_config) == [2, 2, 4, 2, 1, 1, 1, 0]

    def test_upper_grade_overlap_still_reported_once_by_the_daily_band(
        self, student_attendance_mapping, attendance_global_config
    ):
        """Daily-wins suppression is unchanged: the 4 daily rows ARE the day; its period rows go."""
        daily = self._daily(["10"], ["A"], [1.0])
        period = pd.DataFrame(
            {
                "school number": ["100", "100"],
                "student number": ["S0", "S0"],
                "absence date": ["2025-10-01", "2025-10-01"],
                "absence category": ["A", "A"],
            }
        )
        result = _run(daily, student_attendance_mapping, attendance_global_config, period)
        assert len(result) == 4

    # -- a grade the weighting cannot be read from ---------------------------

    @pytest.mark.parametrize("grade", ["", "UG", "Other", "PS", "HS", None])
    def test_unreadable_grade_counts_at_k7_weighting_and_warns(
        self, grade, student_attendance_mapping, attendance_global_config, caplog
    ):
        df = self._daily([grade, "08"], ["A", "A"], [1.0, 1.0])
        df.loc[0, "student number"] = "PUPIL-SENTINEL"
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.student_attendance"):
            result = _run(df, student_attendance_mapping, attendance_global_config)
        counts = result["Student Number"].value_counts()
        assert counts["PUPIL-SENTINEL"] == 2  # K-7 weighting: today's count, never dropped
        assert counts["S1"] == 4
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("1 daily absence row(s) carry a grade that is not K-12" in m for m in warnings)
        assert not any("PUPIL-SENTINEL" in m for m in warnings), "counts only — never a pupil number"

    def test_twin_every_readable_grade_warns_about_nothing(
        self, student_attendance_mapping, attendance_global_config, caplog
    ):
        df = self._daily(["KF", "01", "08", "12"], ["A"] * 4, [1.0] * 4)
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.student_attendance"):
            _run(df, student_attendance_mapping, attendance_global_config)
        assert not any("not K-12" in r.getMessage() for r in caplog.records)

    def test_an_absent_grade_column_counts_every_row_at_k7_weighting(
        self, student_attendance_mapping, attendance_global_config, caplog
    ):
        """Recorded once as a missing column (`_note_absent_columns`), not per row."""
        df = self._daily(["10"], ["A"], [1.0]).drop(columns=["grade"])
        with caplog.at_level(logging.WARNING, logger="src.etl.transformers.student_attendance"):
            result = _run(df, student_attendance_mapping, attendance_global_config)
        assert len(result) == 2
        messages = [r.getMessage() for r in caplog.records]
        assert any("ABSENCE COLUMNS MISSING" in m and "grade" in m for m in messages)
        assert not any("not K-12" in m for m in messages)

    # -- the knobs -----------------------------------------------------------

    def test_threshold_is_configurable(self, student_attendance_mapping, attendance_global_config):
        cfg = copy.deepcopy(attendance_global_config)
        cfg["attendance"]["daily"]["portion"]["upper_grade_from"] = "10"
        df = self._daily(["09", "10"], ["A", "A"], [1.0, 1.0])
        assert self._counts(df, student_attendance_mapping, cfg) == [2, 4]

    @pytest.mark.parametrize("value", ["KG", "UG", "", "eight"])
    def test_a_threshold_that_is_not_a_grade_fails_loud(
        self, value, student_attendance_mapping, attendance_global_config
    ):
        cfg = copy.deepcopy(attendance_global_config)
        cfg["attendance"]["daily"]["portion"]["upper_grade_from"] = value
        with pytest.raises(ValueError, match="upper_grade_from"):
            _run(self._daily(["08"], ["A"], [1.0]), student_attendance_mapping, cfg)

    @pytest.mark.parametrize("key", ["upper_grade_from", "upper_full_day_rows", "upper_default_rows"])
    def test_a_missing_upper_knob_fails_loud(self, key, student_attendance_mapping, attendance_global_config):
        cfg = copy.deepcopy(attendance_global_config)
        del cfg["attendance"]["daily"]["portion"][key]
        with pytest.raises(ValueError, match=key):
            _run(self._daily(["08"], ["A"], [1.0]), student_attendance_mapping, cfg)

    def test_base_defaults_implement_the_importer_weighting(self):
        daily = load_config("myedbc").global_config.attendance["daily"]
        portion = daily["portion"]
        assert (portion["full_day_rows"], portion["default_rows"], portion["tardy_rows"]) == (2, 1, 1)
        assert str(portion["upper_grade_from"]) == "08"
        assert (portion["upper_full_day_rows"], portion["upper_default_rows"]) == (4, 2)
        assert daily["daily_grade_col"] == "grade"

    @pytest.mark.parametrize("sis", ["sd51myedbc", "sd51attendance", "sd60myedbc", "unitychristianmyedbc"])
    def test_attendance_districts_inherit_the_weighting_unchanged(self, sis):
        base = load_config("myedbc").global_config.attendance["daily"]
        district = load_config(sis).global_config.attendance["daily"]
        assert district["portion"] == base["portion"]
        assert district["daily_grade_col"] == base["daily_grade_col"]
