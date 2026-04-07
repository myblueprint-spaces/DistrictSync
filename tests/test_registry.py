"""Tests for the transformer registry — known entities, DefaultTransformer fallback."""

import pandas as pd
import pytest

from src.etl.transformers.context import TransformContext
from src.etl.transformers.registry import (
    TRANSFORMER_REGISTRY,
    DefaultTransformer,
    get_transformer,
)
from src.etl.transformers.students import StudentTransformer


class TestGetTransformer:

    def test_known_entity_returns_registered_transformer(self):
        transformer = get_transformer("Students")
        assert isinstance(transformer, StudentTransformer)

    def test_all_five_standard_entities_registered(self):
        for name in ("Students", "Staff", "Family", "Classes", "Enrollments"):
            assert name in TRANSFORMER_REGISTRY

    def test_unknown_entity_returns_default_transformer(self):
        transformer = get_transformer("CourseInfo")
        assert isinstance(transformer, DefaultTransformer)

    def test_unknown_entity_does_not_raise(self):
        # Previously raised ValueError; now returns DefaultTransformer
        transformer = get_transformer("SomeNewEntity")
        assert transformer is not None


class TestDefaultTransformer:

    @pytest.fixture()
    def context(self):
        ctx = TransformContext()
        ctx.set_school_year(2025)
        return ctx

    def test_applies_field_map_string_columns(self, context):
        df = pd.DataFrame({
            "Course Code": ["MATH10", "ENG11"],
            "Title": ["Mathematics 10", "English 11"],
            "School Number": ["100", "100"],
        })
        mapping = {
            "field_map": {
                "Course Code": "course code",
                "Course Name": "title",
                "School ID": "school number",
            }
        }
        transformer = DefaultTransformer("CourseInfo")
        result = transformer.transform(df, mapping, context)

        assert list(result.columns) == ["Course Code", "Course Name", "School ID"]
        assert result["Course Code"].tolist() == ["MATH10", "ENG11"]
        assert result["Course Name"].tolist() == ["Mathematics 10", "English 11"]

    def test_applies_fixed_value(self, context):
        df = pd.DataFrame({"Name": ["Alice"]})
        mapping = {
            "field_map": {
                "Name": "name",
                "Status": {"value": "Active"},
            }
        }
        transformer = DefaultTransformer("Test")
        result = transformer.transform(df, mapping, context)

        assert result["Status"].iloc[0] == "Active"

    def test_applies_transform_function(self, context):
        df = pd.DataFrame({"Grade Level": ["K", "1", "10"]})
        mapping = {
            "field_map": {
                "Grade": {"column": "grade level", "transform": "grade_to_ceds"},
            }
        }
        transformer = DefaultTransformer("Test")
        result = transformer.transform(df, mapping, context)

        assert result["Grade"].tolist() == ["KG", "01", "10"]

    def test_missing_column_yields_na(self, context):
        df = pd.DataFrame({"Name": ["Alice"]})
        mapping = {
            "field_map": {
                "Name": "name",
                "NonExistent": "does_not_exist",
            }
        }
        transformer = DefaultTransformer("Test")
        result = transformer.transform(df, mapping, context)

        assert result["Name"].iloc[0] == "Alice"
        assert pd.isna(result["NonExistent"].iloc[0])

    def test_entity_name_stored(self):
        t = DefaultTransformer("StudentCourses")
        assert t._entity_name == "StudentCourses"
