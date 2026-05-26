"""Integration tests for the CourseInfo entity transformation.

CourseInfo is opt-in — its template is defined in the base myedbc config
but only activated when `enabled_entities` lists it (the myBlueprint+
configs do this). These tests use synthetic MyEd BC CourseInformation
data plus the standard MyEd BC exclusion patterns.
"""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer

MYEDBC_PATTERNS = [r"^.{5}-K", r"^.{5}0\d", r"^X", r"^ATT"]

COURSE_INFO_FIELD_MAP = {
    "Course Code": "Course Code",
    "Alternate Course Code": {"value": ""},
    "School ID": "School Number",
    "Course Name": "Title",
    "Course Description": {"value": ""},
    "Discipline": {"value": ""},
    "Department": {"value": ""},
    "Type": {"value": ""},
    "Grade": "Grade Level",
    "MaxGrade": {"value": ""},
    "Credit Value": "Credit Value",
    "IntegrationId": {"value": ""},
    "Year Offered": {"value": ""},
}


@pytest.fixture
def course_info_mapping():
    return {
        "source_files": {"course_info": "CourseInformation.txt"},
        "field_map": COURSE_INFO_FIELD_MAP,
    }


@pytest.fixture
def myedbc_global_config():
    return {
        "excluded_course_code_patterns": MYEDBC_PATTERNS,
        "excluded_course_flavors": ["HUB", "HOL", "DL", "---"],
    }


@pytest.fixture
def myedbc_course_info_df():
    """Synthetic MyEd BC CourseInformation with rows targeted by each filter pattern."""
    return pd.DataFrame(
        {
            "course code": [
                "MAT10",  # keep
                "ENG12",  # keep
                "SCI09",  # keep
                "MAT1003",  # drop: ^.{5}0\d (early grade)
                "ABCDE-KO",  # drop: ^.{5}-K (kindergarten)
                "XGEN12",  # drop: ^X
                "ATT--AM",  # drop: ^ATT
                "MAT10",  # duplicate (same school) — should dedupe
            ],
            "school number": ["6262013", "6262013", "6299043", "6262013", "6262013", "6262013", "6262013", "6262013"],
            "title": [
                "Math 10",
                "English 12",
                "Science 9",
                "Math K-3 Combo",
                "Kindergarten",
                "X-Course",
                "Attendance AM",
                "Math 10 (dup)",
            ],
            "grade level": ["10", "12", "09", "00", "K", "12", "00", "10"],
            "credit value": ["4", "4", "4", "4", "0", "0", "0", "4"],
        }
    )


class TestCourseInfoTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025)

    def test_excluded_patterns_filtered_out(self, myedbc_course_info_df, course_info_mapping, myedbc_global_config):
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config
        )
        codes = list(result["Course Code"])
        assert "MAT10" in codes
        assert "ENG12" in codes
        assert "SCI09" in codes
        # All four pattern matches must be gone
        assert "MAT1003" not in codes
        assert "ABCDE-KO" not in codes
        assert "XGEN12" not in codes
        assert "ATT--AM" not in codes

    def test_dedupes_on_course_code_plus_school(self, myedbc_course_info_df, course_info_mapping, myedbc_global_config):
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config
        )
        # Two MAT10 rows under school 6262013 should collapse to one
        mat10_rows = result[(result["Course Code"] == "MAT10") & (result["School ID"] == "6262013")]
        assert len(mat10_rows) == 1
        # First occurrence wins
        assert mat10_rows.iloc[0]["Course Name"] == "Math 10"

    def test_same_code_different_schools_not_deduped(self, course_info_mapping, myedbc_global_config):
        """MAT10 offered at two schools should produce two rows."""
        df = pd.DataFrame(
            {
                "course code": ["MAT10", "MAT10"],
                "school number": ["6262013", "6299043"],
                "title": ["Math 10 @ A", "Math 10 @ B"],
                "grade level": ["10", "10"],
                "credit value": ["4", "4"],
            }
        )
        raw_data = {"CourseInformation.txt": df}
        result = self.transformer.transform(df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config)
        assert len(result) == 2

    def test_output_columns_match_field_map_order(
        self, myedbc_course_info_df, course_info_mapping, myedbc_global_config
    ):
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config
        )
        # Every field declared in the mapping must appear as an output column
        for field in COURSE_INFO_FIELD_MAP:
            assert field in result.columns, f"Missing expected output column: {field}"

    def test_blank_fields_have_empty_value(self, myedbc_course_info_df, course_info_mapping, myedbc_global_config):
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config
        )
        # Spot-check the literal-value columns from the SD62 spec
        for col in (
            "Alternate Course Code",
            "Course Description",
            "Discipline",
            "Department",
            "Type",
            "MaxGrade",
            "IntegrationId",
            "Year Offered",
        ):
            assert (result[col].astype(str) == "").all(), f"{col} should be blank for every row"

    def test_field_values_mapped_correctly(self, myedbc_course_info_df, course_info_mapping, myedbc_global_config):
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config
        )
        mat10 = result[result["Course Code"] == "MAT10"].iloc[0]
        assert mat10["School ID"] == "6262013"
        assert mat10["Course Name"] == "Math 10"
        assert mat10["Grade"] == "10"
        assert mat10["Credit Value"] == "4"

    def test_no_patterns_configured_keeps_everything(self, myedbc_course_info_df, course_info_mapping):
        """When excluded_course_code_patterns is empty, nothing is filtered (only dedup applies)."""
        global_config = {"excluded_course_code_patterns": []}
        raw_data = {"CourseInformation.txt": myedbc_course_info_df}
        result = self.transformer.transform(
            myedbc_course_info_df, course_info_mapping, "CourseInfo", raw_data, global_config
        )
        # 8 input rows, 1 dedupe (the second MAT10) — 7 expected
        assert len(result) == 7
        # XGEN12, ATT--AM, etc. are still there
        codes = set(result["Course Code"])
        assert {"XGEN12", "ATT--AM", "MAT1003", "ABCDE-KO"} <= codes

    def test_empty_input_returns_empty(self, course_info_mapping, myedbc_global_config):
        empty = pd.DataFrame(columns=["course code", "school number", "title", "grade level", "credit value"])
        raw_data = {"CourseInformation.txt": empty}
        result = self.transformer.transform(empty, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config)
        assert result.empty

    def test_normalises_uppercase_source_columns(self, course_info_mapping, myedbc_global_config):
        """Source column names are normalised (lowercased + stripped) before mapping."""
        df = pd.DataFrame(
            {
                "COURSE CODE": ["MAT10"],
                "School Number": ["6262013"],
                "Title": ["Math 10"],
                "Grade Level": ["10"],
                "Credit Value": ["4"],
            }
        )
        raw_data = {"CourseInformation.txt": df}
        result = self.transformer.transform(df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config)
        assert len(result) == 1
        assert result.iloc[0]["Course Code"] == "MAT10"
        assert result.iloc[0]["Course Name"] == "Math 10"

    def test_missing_source_column_produces_na(self, course_info_mapping, myedbc_global_config):
        """A field whose source column is absent should produce NA, not crash."""
        df = pd.DataFrame(
            {
                "course code": ["MAT10"],
                "school number": ["6262013"],
                "title": ["Math 10"],
                # No 'grade level' or 'credit value'
            }
        )
        raw_data = {"CourseInformation.txt": df}
        result = self.transformer.transform(df, course_info_mapping, "CourseInfo", raw_data, myedbc_global_config)
        assert len(result) == 1
        assert pd.isna(result.iloc[0]["Grade"])
        assert pd.isna(result.iloc[0]["Credit Value"])


class TestCourseInfoEntityIntegration:
    """CourseInfo entity template is in the base myedbc config and enabled by myBlueprint+."""

    def test_base_config_defines_courseinfo_template(self):
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        # Template is present so child configs can opt in via enabled_entities
        assert "CourseInfo" in cfg.mappings
        # ...but the base does NOT enable it by default
        assert "CourseInfo" not in cfg.global_config.enabled_entities

    def test_courseinfo_field_order(self):
        """The 13 output columns appear in the requested order."""
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        expected_order = [
            "Course Code",
            "Alternate Course Code",
            "School ID",
            "Course Name",
            "Course Description",
            "Discipline",
            "Department",
            "Type",
            "Grade",
            "MaxGrade",
            "Credit Value",
            "IntegrationId",
            "Year Offered",
        ]
        assert list(cfg.mappings["CourseInfo"].field_map.keys()) == expected_order

    def test_myedbc_carries_patterns_and_flavors(self):
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        assert cfg.global_config.excluded_course_code_patterns == [
            r"^.{5}-K",
            r"^.{5}0\d",
            r"^X",
            r"^ATT",
        ]
        assert cfg.global_config.excluded_course_flavors == ["HUB", "HOL", "DL", "---"]

    def test_myblueprintplus_enables_courseinfo(self):
        from src.config.loader import load_config

        cfg = load_config("myBlueprint+")
        assert "CourseInfo" in cfg.global_config.enabled_entities

    def test_myblueprintplus_minimal_enables_courseinfo(self):
        from src.config.loader import load_config

        cfg = load_config("myBlueprint+_minimal")
        assert "CourseInfo" in cfg.global_config.enabled_entities
