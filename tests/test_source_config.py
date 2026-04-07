"""Tests for source config normalization and file retrieval."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestNormalizeSourceConfig:

    def setup_method(self):
        self.transformer = DataTransformer()

    def test_dict_passthrough(self):
        config = {"student_schedule": "StudentSchedule.txt", "course_info": "Course.txt"}
        result = self.transformer.normalize_source_config(config)
        assert result == config

    def test_list_of_dicts(self):
        config = [
            {"role": "student_schedule", "file": "StudentSchedule.txt"},
            {"role": "course_info", "file": "Course.txt"},
        ]
        result = self.transformer.normalize_source_config(config)
        assert result == {
            "student_schedule": "StudentSchedule.txt",
            "course_info": "Course.txt",
        }

    def test_list_of_strings(self):
        config = ["Schedule.txt", "Course.txt", "Staff.txt", "Demo.txt"]
        result = self.transformer.normalize_source_config(config)
        assert result == {
            "student_schedule": "Schedule.txt",
            "course_info": "Course.txt",
            "staff_info": "Staff.txt",
            "student_demographic": "Demo.txt",
        }

    def test_list_of_strings_fewer_than_roles(self):
        config = ["Schedule.txt", "Course.txt"]
        result = self.transformer.normalize_source_config(config)
        assert len(result) == 2
        assert result["student_schedule"] == "Schedule.txt"
        assert result["course_info"] == "Course.txt"

    def test_empty_list(self):
        assert self.transformer.normalize_source_config([]) == {}

    def test_non_dict_non_list(self):
        assert self.transformer.normalize_source_config("not_valid") == {}


class TestGetSourceFile:

    def setup_method(self):
        self.transformer = DataTransformer()

    def test_retrieves_existing_file(self):
        df = pd.DataFrame({"col": [1, 2, 3]})
        raw_data = {"File.txt": df}
        source_config = {"role1": "File.txt"}
        result = self.transformer.get_source_file(raw_data, source_config, "role1")
        assert len(result) == 3

    def test_returns_copy_not_original(self):
        df = pd.DataFrame({"col": [1, 2, 3]})
        raw_data = {"File.txt": df}
        source_config = {"role1": "File.txt"}
        result = self.transformer.get_source_file(raw_data, source_config, "role1")
        result["col"] = 99
        assert df["col"].tolist() == [1, 2, 3]  # Original unchanged

    def test_missing_role_returns_empty(self):
        raw_data = {"File.txt": pd.DataFrame({"col": [1]})}
        source_config = {"role1": "File.txt"}
        result = self.transformer.get_source_file(raw_data, source_config, "nonexistent")
        assert result.empty

    def test_missing_file_returns_empty(self):
        raw_data = {}
        source_config = {"role1": "Missing.txt"}
        result = self.transformer.get_source_file(raw_data, source_config, "role1")
        assert result.empty
