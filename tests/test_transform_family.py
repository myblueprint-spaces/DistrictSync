"""Integration tests for the Family entity transformation."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestFamilyTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_basic_family_transform(self, emergency_contact_df, family_mapping, global_config, raw_data):
        result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        assert len(result) == len(emergency_contact_df)
        for field in family_mapping["field_map"]:
            assert field in result.columns

    def test_maps_correct_fields(self, emergency_contact_df, family_mapping, global_config, raw_data):
        result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        # First row should be John Smith for student S001
        assert result["First Name"].iloc[0] == "John"
        assert result["Last Name"].iloc[0] == "Smith"
        assert result["Email"].iloc[0] == "john@mail.com"
        assert result["Student User ID"].iloc[0] == "S001"

    def test_multiple_contacts_per_student(self, emergency_contact_df, family_mapping, global_config, raw_data):
        """S001 has 2 contacts — both should appear."""
        result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        s001_contacts = result[result["Student User ID"] == "S001"]
        assert len(s001_contacts) == 2

    def test_empty_contacts_returns_empty(self, family_mapping, global_config):
        """No emergency contacts should return an empty DataFrame, not crash."""
        empty_df = pd.DataFrame(columns=["student number", "first name", "last name", "email address"])
        raw_data = {"EmergencyContactInformation.txt": empty_df}
        result = self.transformer.transform(empty_df, family_mapping, "Family", raw_data, global_config)
        assert result.empty

    def test_missing_email_produces_na(self, family_mapping, global_config):
        """Contact without an email address should produce NaN in Email, not crash."""
        no_email_df = pd.DataFrame(
            {
                "student number": ["S001"],
                "first name": ["John"],
                "last name": ["Smith"],
                # no 'email address' column
            }
        )
        raw_data = {"EmergencyContactInformation.txt": no_email_df}
        result = self.transformer.transform(no_email_df, family_mapping, "Family", raw_data, global_config)
        assert len(result) == 1
        assert "Email" in result.columns
        # Value should be NA / NaN, not crash
        assert pd.isna(result["Email"].iloc[0])

    def test_names_with_special_characters(self, family_mapping, global_config):
        """Names with accents, apostrophes, and hyphens should be preserved."""
        special_df = pd.DataFrame(
            {
                "student number": ["S001", "S002"],
                "first name": ["Zoé", "O'Brien"],
                "last name": ["Côté-Lefebvre", "MacDonald"],
                "email address": ["zoe@mail.com", "obrien@mail.com"],
            }
        )
        raw_data = {"EmergencyContactInformation.txt": special_df}
        result = self.transformer.transform(special_df, family_mapping, "Family", raw_data, global_config)
        assert result["First Name"].iloc[0] == "Zoé"
        assert result["Last Name"].iloc[0] == "Côté-Lefebvre"
        assert result["First Name"].iloc[1] == "O'Brien"

    def test_column_name_case_insensitive(self, family_mapping, global_config):
        """Uppercase source column names should normalise and map correctly."""
        upper_df = pd.DataFrame(
            {
                "STUDENT NUMBER": ["S001"],
                "FIRST NAME": ["John"],
                "LAST NAME": ["Smith"],
                "EMAIL ADDRESS": ["john@mail.com"],
            }
        )
        raw_data = {"EmergencyContactInformation.txt": upper_df}
        result = self.transformer.transform(upper_df, family_mapping, "Family", raw_data, global_config)
        assert len(result) == 1
        assert result["First Name"].iloc[0] == "John"

    def test_all_fields_present_in_output(self, family_mapping, global_config, raw_data, emergency_contact_df):
        """Every field declared in the mapping must appear as a column in the output."""
        result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        for field in family_mapping["field_map"]:
            assert field in result.columns, f"Missing expected output column: {field}"

    def test_row_filters_drop_non_matching_rows(self, global_config):
        """A config-driven row_filter (SD60 guardians-only) drops non-matching contacts."""
        df = pd.DataFrame(
            {
                "student number": ["S001", "S002", "S003"],
                "first name": ["John", "Jane", "Jake"],
                "last name": ["Smith", "Doe", "Roe"],
                "email address": ["j@x.com", "ja@x.com", "jk@x.com"],
                "parent auth / guardian": ["Y", "N", "Y"],
            }
        )
        mapping = {
            "source_files": {"emergency_contacts": "EmergencyEnhanced.txt"},
            "field_map": {
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Email": "Email Address",
                "Student User ID": "Student Number",
            },
            "row_filters": [{"column": "Parent Auth / Guardian", "include": ["Y"]}],
        }
        raw_data = {"EmergencyEnhanced.txt": df}
        result = self.transformer.transform(df, mapping, "Family", raw_data, global_config)
        # Only the two guardian rows survive; the non-guardian (S002, "N") is dropped.
        assert len(result) == 2
        assert list(result["First Name"]) == ["John", "Jake"]
