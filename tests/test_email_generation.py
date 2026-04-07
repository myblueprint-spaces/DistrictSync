"""Tests for student email generation from templates."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestGenerateStudentEmail:
    def setup_method(self):
        self.transformer = DataTransformer()

    def test_basic_template(self):
        row = pd.Series({"student number": "12345", "first name": "Alice"})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == "12345@school.ca"

    def test_multi_field_template(self):
        row = pd.Series(
            {
                "first name": "Alice",
                "last name": "Smith",
                "student number": "12345",
            }
        )
        result = self.transformer.generate_student_email(row, "{first name}.{last name}@school.ca")
        assert result == "Alice.Smith@school.ca"

    def test_case_insensitive_keys(self):
        """Template keys are lowercased, row keys are lowercased."""
        row = pd.Series({"Student Number": "12345"})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == "12345@school.ca"

    def test_missing_key_returns_empty(self):
        row = pd.Series({"first name": "Alice"})
        result = self.transformer.generate_student_email(row, "{student number}@school.ca")
        assert result == ""

    def test_empty_format_string(self):
        row = pd.Series({"student number": "12345"})
        result = self.transformer.generate_student_email(row, "")
        assert result == ""
