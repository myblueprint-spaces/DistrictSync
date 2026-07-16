"""Tests for student email generation from templates."""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer


class TestGenerateStudentEmail:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_basic_template(self):
        row = pd.Series({"student number": "12345", "first name": "Alice"})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == "12345@school.ca"

    def test_multi_field_template(self):
        """String row values are lowercased so emails are deliverable."""
        row = pd.Series(
            {
                "first name": "Alice",
                "last name": "Smith",
                "student number": "12345",
            }
        )
        result = self.transformer.generate_student_email(row, "{first name}.{last name}@school.ca")
        assert result == "alice.smith@school.ca"

    def test_collapses_internal_spaces_in_values(self):
        """Double-barrelled names ('Goodrick Hill') produce a single-token local part."""
        row = pd.Series({"legal surname": "Goodrick Hill", "usual first name": "Skyler"})
        result = self.transformer.generate_student_email(row, "{legal surname}.{usual first name}@sd54.bc.ca")
        assert result == "goodrickhill.skyler@sd54.bc.ca"

    def test_nan_value_becomes_empty_string(self):
        """A missing field renders as '' rather than the literal 'nan'."""
        row = pd.Series({"legal surname": "Doe", "usual first name": float("nan")})
        result = self.transformer.generate_student_email(row, "{legal surname}.{usual first name}@sd54.bc.ca")
        assert result == "doe.@sd54.bc.ca"

    def test_numeric_value_unchanged(self):
        """Numeric values (e.g. Student Number stored as int) pass through unchanged."""
        row = pd.Series({"student number": 12345})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == "12345@school.ca"

    def test_case_insensitive_keys(self):
        """Template keys are lowercased, row keys are lowercased."""
        row = pd.Series({"Student Number": "12345"})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == "12345@school.ca"

    def test_missing_key_raises_key_error(self):
        """Fail-loud: a template key absent from the row is a config/column
        mismatch and raises. StudentTransformer._generate_emails is the
        resilient caller — it blanks only that cell and records a data error
        (pinned in test_transform_students.py)."""
        row = pd.Series({"first name": "Alice"})
        with pytest.raises(KeyError):
            self.transformer.generate_student_email(row, "{student number}@school.ca")

    def test_empty_format_string(self):
        row = pd.Series({"student number": "12345"})
        result = self.transformer.generate_student_email(row, "")
        assert result == ""
