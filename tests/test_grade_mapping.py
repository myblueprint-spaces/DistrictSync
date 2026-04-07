"""Tests for CEDS grade code mapping."""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer


class TestGradeToCeds:
    """Parametrized tests covering the full CEDS_MAPPING table plus edge cases."""

    @pytest.mark.parametrize(
        "input_grade, expected",
        [
            # Early childhood
            ("INFANT/TODDLER", "IT"),
            ("PRESCHOOL", "PR"),
            ("PRE-K", "PK"),
            ("PREKINDERGARTEN", "PK"),
            ("TK", "TK"),
            ("TRANSITIONAL KINDERGARTEN", "TK"),
            # Kindergarten variants
            ("KINDERGARTEN", "KG"),
            ("K", "KG"),
            ("KF", "KG"),
            ("EL", "KG"),
            # Numeric grades — single digit
            ("1", "01"),
            ("2", "02"),
            ("3", "03"),
            ("4", "04"),
            ("5", "05"),
            ("6", "06"),
            ("7", "07"),
            ("8", "08"),
            ("9", "09"),
            # Numeric grades — zero-padded
            ("01", "01"),
            ("02", "02"),
            ("03", "03"),
            ("04", "04"),
            ("05", "05"),
            ("06", "06"),
            ("07", "07"),
            ("08", "08"),
            ("09", "09"),
            # Secondary
            ("10", "10"),
            ("11", "11"),
            ("12", "12"),
            ("13", "13"),
            # Post-secondary and special
            ("POSTSECONDARY", "PS"),
            ("UNGRADED", "UG"),
            ("UGRADED", "UG"),  # typo variant in mapping
            ("UG", "UG"),
            ("OTHER", "Other"),
        ],
    )
    def test_known_mappings(self, input_grade, expected):
        assert DataTransformer.grade_to_ceds(input_grade) == expected

    @pytest.mark.parametrize(
        "input_grade",
        [
            "k",
            "kindergarten",
            "pre-k",  # Case insensitive
        ],
    )
    def test_case_insensitive(self, input_grade):
        """Mapping should be case-insensitive (input is uppercased before lookup)."""
        result = DataTransformer.grade_to_ceds(input_grade)
        assert result != "UG"  # Should find a match, not default

    def test_mixed_case_kg_unmapped(self):
        """'Kg' uppercases to 'KG' which IS a CEDS code, but isn't a key in the mapping.
        The mapping has 'K' and 'KINDERGARTEN' but not 'KG' as an input key."""
        # 'KG' is not in the CEDS_MAPPING as a key, so it falls to default
        assert DataTransformer.grade_to_ceds("Kg") == "UG"

    @pytest.mark.parametrize(
        "input_grade, expected",
        [
            ("  K  ", "KG"),  # Whitespace stripped
            (" 10 ", "10"),
            ("  1  ", "01"),
        ],
    )
    def test_whitespace_stripped(self, input_grade, expected):
        assert DataTransformer.grade_to_ceds(input_grade) == expected

    @pytest.mark.parametrize(
        "input_grade",
        [
            "UNKNOWN",
            "14",
            "Grade5",
            "-1",
            "N/A",
            "XYZ",
        ],
    )
    def test_unmapped_defaults_to_ug(self, input_grade):
        """Any unmapped grade should default to 'UG'."""
        assert DataTransformer.grade_to_ceds(input_grade) == "UG"

    def test_none_input(self):
        assert DataTransformer.grade_to_ceds(None) == "UG"

    def test_nan_input(self):
        assert DataTransformer.grade_to_ceds(float("nan")) == "UG"

    def test_pd_na_input(self):
        assert DataTransformer.grade_to_ceds(pd.NA) == "UG"

    def test_empty_string(self):
        assert DataTransformer.grade_to_ceds("") == "UG"

    def test_numeric_int_input(self):
        """Integer input should still work (converted to str internally)."""
        assert DataTransformer.grade_to_ceds(7) == "07"
        assert DataTransformer.grade_to_ceds(10) == "10"
