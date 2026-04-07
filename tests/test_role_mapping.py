"""Tests for staff role mapping and user role/ID generation."""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer


class TestMapRole:

    @pytest.mark.parametrize("flag, expected", [
        ("Y", "teacher"),
        ("y", "teacher"),
        (" Y ", "teacher"),
        ("N", "administrator"),
        ("n", "administrator"),
        ("", "administrator"),
        ("No", "administrator"),
        ("Yes", "administrator"),  # Only exact "y" is teacher
    ])
    def test_map_role(self, flag, expected):
        assert DataTransformer.map_role(flag) == expected

    def test_map_role_none(self):
        assert DataTransformer.map_role(None) == "administrator"

    def test_map_role_nan(self):
        assert DataTransformer.map_role(float("nan")) == "administrator"


class TestGenerateUserRole:

    def setup_method(self):
        self.transformer = DataTransformer()

    def test_teacher_when_staff_id_present(self):
        row = pd.Series({"staff_id": "T001", "student_id": "S001"})
        assert self.transformer.generate_user_role(row, "staff_id", "student_id") == "teacher"

    def test_student_when_only_student_id(self):
        row = pd.Series({"staff_id": "", "student_id": "S001"})
        assert self.transformer.generate_user_role(row, "staff_id", "student_id") == "student"

    def test_unknown_when_neither(self):
        row = pd.Series({"staff_id": "", "student_id": ""})
        assert self.transformer.generate_user_role(row, "staff_id", "student_id") == "unknown"

    def test_teacher_priority_over_student(self):
        """Staff ID takes priority when both are present."""
        row = pd.Series({"staff_id": "T001", "student_id": "S001"})
        assert self.transformer.generate_user_role(row, "staff_id", "student_id") == "teacher"

    def test_nan_staff_id_falls_through(self):
        row = pd.Series({"staff_id": float("nan"), "student_id": "S001"})
        assert self.transformer.generate_user_role(row, "staff_id", "student_id") == "student"


class TestGenerateUserId:

    def setup_method(self):
        self.transformer = DataTransformer()

    def test_returns_staff_id_when_present(self):
        row = pd.Series({"staff_id": "T001", "student_id": "S001"})
        assert self.transformer.generate_user_id(row, "staff_id", "student_id") == "T001"

    def test_returns_student_id_when_no_staff(self):
        row = pd.Series({"staff_id": "", "student_id": "S001"})
        assert self.transformer.generate_user_id(row, "staff_id", "student_id") == "S001"

    def test_returns_unknown_when_neither(self):
        row = pd.Series({"staff_id": "", "student_id": ""})
        assert self.transformer.generate_user_id(row, "staff_id", "student_id") == "UNKNOWN_ID"

    def test_nan_staff_falls_through(self):
        row = pd.Series({"staff_id": float("nan"), "student_id": "S001"})
        assert self.transformer.generate_user_id(row, "staff_id", "student_id") == "S001"
