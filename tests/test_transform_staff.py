"""Integration tests for the Staff entity transformation."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestStaffTransform:

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025)

    def test_basic_staff_transform(
        self, staff_info_df, staff_mapping, global_config, raw_data
    ):
        result = self.transformer.transform(
            staff_info_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert len(result) == len(staff_info_df)
        for field in staff_mapping["field_map"]:
            assert field in result.columns

    def test_role_mapping(self, staff_info_df, staff_mapping, global_config, raw_data):
        result = self.transformer.transform(
            staff_info_df, staff_mapping, "Staff", raw_data, global_config
        )
        roles = result["Role"].tolist()
        # T005 has "N" → administrator, rest have "Y" → teacher
        assert roles.count("teacher") == 4
        assert roles.count("administrator") == 1

    def test_staff_with_roster_merge(self, staff_info_df, global_config):
        """When a roster file with 'staff sourceid' exists, it should be merged."""
        roster_df = pd.DataFrame({
            "teacher id": ["T001", "T002", "T003"],
            "staff sourceid": ["SRC001", "SRC002", "SRC003"],
            "other_col": ["x", "y", "z"],
        })
        raw_data = {
            "StaffInformationEnhanced.txt": staff_info_df,
            "StudentSchedule.txt": roster_df,
        }
        mapping = {
            "source_files": {
                "staff_info": "StaffInformationEnhanced.txt",
                "roster": "StudentSchedule.txt",
            },
            "field_map": {
                "User ID": "Teacher Id",
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Email": "Email Address",
                "Role": {"column": "Teaching Staff", "transform": "map_role"},
                "School ID": "School Number",
            },
        }
        result = self.transformer.transform(
            staff_info_df, mapping, "Staff", raw_data, global_config
        )
        assert len(result) == len(staff_info_df)

    def test_staff_empty_input_returns_empty(self, staff_mapping, global_config):
        """An empty DataFrame input should return an empty result without error."""
        empty_df = pd.DataFrame(columns=["teacher id", "first name", "last name",
                                          "email address", "teaching staff", "school number"])
        raw_data = {"StaffInformationEnhanced.txt": empty_df}
        result = self.transformer.transform(
            empty_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert result.empty

    def test_staff_all_administrators(self, staff_mapping, global_config):
        """All staff with Teaching Staff = 'N' should have role 'administrator'."""
        admin_df = pd.DataFrame({
            "teacher id": ["T001", "T002"],
            "first name": ["Alice", "Bob"],
            "last name": ["Smith", "Jones"],
            "email address": ["alice@school.ca", "bob@school.ca"],
            "teaching staff": ["N", "N"],
            "school number": ["100", "100"],
        })
        raw_data = {"StaffInformationEnhanced.txt": admin_df}
        result = self.transformer.transform(
            admin_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert all(r == "administrator" for r in result["Role"].tolist())

    def test_staff_mixed_case_teaching_flag(self, staff_mapping, global_config):
        """Teaching Staff flag should be case-insensitive (Y/y/N/n)."""
        mixed_df = pd.DataFrame({
            "teacher id": ["T001", "T002", "T003", "T004"],
            "first name": ["A", "B", "C", "D"],
            "last name": ["A", "B", "C", "D"],
            "email address": ["a@s.ca", "b@s.ca", "c@s.ca", "d@s.ca"],
            "teaching staff": ["Y", "y", "N", "n"],
            "school number": ["100", "100", "100", "100"],
        })
        raw_data = {"StaffInformationEnhanced.txt": mixed_df}
        result = self.transformer.transform(
            mixed_df, staff_mapping, "Staff", raw_data, global_config
        )
        roles = result["Role"].tolist()
        assert roles.count("teacher") == 2
        assert roles.count("administrator") == 2

    def test_staff_roster_merge_no_overlap(self, staff_info_df, global_config):
        """Roster with no matching teacher IDs — staff sourceid should be NaN."""
        roster_df = pd.DataFrame({
            "teacher id": ["T999", "T998"],
            "staff sourceid": ["SRC999", "SRC998"],
        })
        raw_data = {
            "StaffInformationEnhanced.txt": staff_info_df,
            "Roster.txt": roster_df,
        }
        mapping = {
            "source_files": {
                "staff_info": "StaffInformationEnhanced.txt",
                "roster": "Roster.txt",
            },
            "field_map": {
                "User ID": "Teacher Id",
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Email": "Email Address",
                "Role": {"column": "Teaching Staff", "transform": "map_role"},
                "School ID": "School Number",
            },
        }
        result = self.transformer.transform(
            staff_info_df, mapping, "Staff", raw_data, global_config
        )
        # All rows retained (left join), no match = no sourceid enrichment
        assert len(result) == len(staff_info_df)

    def test_staff_deduplication(self, staff_mapping, global_config):
        """Duplicate teacher IDs in source should each produce a row (no silent dedup)."""
        dup_df = pd.DataFrame({
            "teacher id": ["T001", "T001", "T002"],
            "first name": ["Jane", "Jane", "Mark"],
            "last name": ["Harper", "Harper", "Reed"],
            "email address": ["harper@school.ca", "harper@school.ca", "reed@school.ca"],
            "teaching staff": ["Y", "Y", "Y"],
            "school number": ["100", "200", "100"],  # same teacher, two schools
        })
        raw_data = {"StaffInformationEnhanced.txt": dup_df}
        result = self.transformer.transform(
            dup_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert len(result) == 3  # Both rows preserved

    def test_staff_school_id_mapped(self, staff_info_df, staff_mapping, global_config, raw_data):
        """School ID output column should contain the school number values."""
        result = self.transformer.transform(
            staff_info_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert "School ID" in result.columns
        school_ids = set(result["School ID"].astype(str).tolist())
        assert "100" in school_ids
        assert "200" in school_ids

    def test_staff_missing_email_does_not_crash(self, staff_mapping, global_config):
        """Staff with missing email column should produce NaN (not crash)."""
        no_email_df = pd.DataFrame({
            "teacher id": ["T001"],
            "first name": ["Jane"],
            "last name": ["Harper"],
            # no 'email address' column
            "teaching staff": ["Y"],
            "school number": ["100"],
        })
        raw_data = {"StaffInformationEnhanced.txt": no_email_df}
        result = self.transformer.transform(
            no_email_df, staff_mapping, "Staff", raw_data, global_config
        )
        assert len(result) == 1
        # Email should be missing / NA — not crash
        assert "Email" in result.columns
