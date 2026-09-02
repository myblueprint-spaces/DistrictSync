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

    def test_missing_email_source_column_excludes_the_row(self, family_mapping, global_config):
        """No source email column → the mapped Email is blank → the row is EXCLUDED.

        SpacesEDU does not import a family contact without an email address, so
        the mapped-but-unpopulated case lands on the same exclusion as a blank
        cell. The Email column itself is still emitted (schema unchanged) and
        nothing crashes.
        """
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
        assert len(result) == 0
        assert "Email" in result.columns

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

    def test_contacts_filtered_to_active_roster(self, emergency_contact_df, family_mapping, global_config, raw_data):
        """Zero-orphan invariant: a withdrawn (non-rostered) student's contacts are
        dropped; active students' contacts are kept.

        The fixture has contacts for S001 (x2), S002, S003, S004 — publishing a
        roster without S002 must drop exactly Robert's row.
        """
        students_mapping = global_config["mappings"]["Students"]
        # Run Students first (registry order in the real pipeline) with S002
        # withdrawn so the published roster excludes them.
        demo = pd.DataFrame(
            {
                "student number": ["S001", "S002", "S003", "S004"],
                "legal first name": ["Alice", "Bob", "Charlie", "Diana"],
                "legal surname": ["Smith", "Jones", "Brown", "White"],
                "grade": ["3", "5", "7", "10"],
                "school number": ["100", "100", "100", "200"],
                "homeroom": ["A1", "A1", "B2", "C3"],
                "enrolment status": ["Active", "Withdrawn", "Active", "Active"],
            }
        )
        self.transformer.transform(demo, students_mapping, "Students", raw_data, global_config)
        result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        assert set(result["Student User ID"]) == {"S001", "S003", "S004"}
        assert "Robert" not in set(result["First Name"])
        assert len(result[result["Student User ID"] == "S001"]) == 2  # both of S001's contacts kept

    def test_missing_roster_keeps_all_contacts_and_warns(
        self, emergency_contact_df, family_mapping, global_config, raw_data, caplog
    ):
        """Fail-safe (same convention as Enrollments): no published roster →
        loud warning, contacts pass through unchanged (never filter-to-empty)."""
        with caplog.at_level("WARNING"):
            result = self.transformer.transform(emergency_contact_df, family_mapping, "Family", raw_data, global_config)
        assert len(result) == len(emergency_contact_df)
        assert any("[Family]" in r.message and "active_student_ids empty" in r.message for r in caplog.records)

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


class TestFamilyNoEmailExclusion:
    """A contact with no email address is EXCLUDED from Family.csv and counted.

    WHY: SpacesEDU's importer will not create a family contact without an email
    address, so such a row can only be rejected on ingest. Excluding it here and
    saying so ONCE (counts only — never a name or an address) is the loud
    version of a loss that would otherwise happen silently at the partner.
    """

    _MAPPING = {
        "source_files": {"emergency_contacts": "EmergencyContactInformation.txt"},
        "field_map": {
            "First Name": "First Name",
            "Last Name": "Last Name",
            "Email": "Email Address",
            "Student User ID": "Student Number",
        },
    }
    #: Literals the log must never echo (PII rule): the one real address in the
    #: fixture plus every contact name.
    _PII = ["valid@mail.com", "Nomail", "Blankmail", "Hasmail", "Noemail", "Blank", "Valid"]

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _df(self, emails):
        return pd.DataFrame(
            {
                "student number": [f"S00{i + 1}" for i in range(len(emails))],
                "first name": ["Noemail", "Blank", "Valid"][: len(emails)],
                "last name": ["Nomail", "Blankmail", "Hasmail"][: len(emails)],
                "email address": emails,
            }
        )

    def _run(self, df, mapping=None):
        raw_data = {"EmergencyContactInformation.txt": df}
        return self.transformer.transform(df, mapping or self._MAPPING, "Family", raw_data, {})

    @staticmethod
    def _exclusion_records(caplog):
        return [r for r in caplog.records if "contact row(s) with no email address" in r.message]

    def _assert_no_pii(self, caplog):
        for record in caplog.records:
            for literal in self._PII:
                assert literal not in record.message, f"PII leaked into the log: {literal!r}"

    def test_blank_email_rows_excluded_and_counted_once(self, caplog):
        """NaN and whitespace-only both count as blank; only the valid row ships."""
        df = self._df([float("nan"), "   ", "valid@mail.com"])
        with caplog.at_level("WARNING"):
            result = self._run(df)
        assert len(result) == 1
        assert result["Email"].tolist() == ["valid@mail.com"]
        assert result["Student User ID"].tolist() == ["S003"]
        records = self._exclusion_records(caplog)
        assert len(records) == 1, [r.message for r in records]
        assert "2 of 3" in records[0].message
        assert records[0].levelname == "WARNING"
        assert "SpacesEDU does not import a family contact without one." in records[0].message
        self._assert_no_pii(caplog)

    def test_empty_string_email_excluded(self, caplog):
        """An empty string (not NaN) is blank too."""
        with caplog.at_level("WARNING"):
            result = self._run(self._df(["", "valid@mail.com"]))
        assert len(result) == 1
        assert self._exclusion_records(caplog)[0].message.endswith(
            "Excluded 1 of 2 contact row(s) with no email address — "
            "SpacesEDU does not import a family contact without one."
        )

    def test_all_contacts_with_email_kept_and_silent(self, caplog):
        """Positive twin: nothing to exclude → every row kept and NO warning."""
        df = self._df(["a@mail.com", "b@mail.com", "valid@mail.com"])
        with caplog.at_level("WARNING"):
            result = self._run(df)
        assert len(result) == 3
        assert self._exclusion_records(caplog) == []

    def test_config_without_email_column_warns_and_keeps_every_row(self, caplog):
        """A field_map with no Email cannot be filtered — surfaced, not hidden.

        The contract requires Email for Family.csv, so this is a config fault:
        ONE warning naming the missing output column, and the rows pass through
        untouched (never silently emptied).
        """
        mapping = {
            "source_files": {"emergency_contacts": "EmergencyContactInformation.txt"},
            "field_map": {
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Student User ID": "Student Number",
            },
        }
        df = self._df([float("nan"), "   ", "valid@mail.com"])
        with caplog.at_level("WARNING"):
            result = self._run(df, mapping)
        assert len(result) == 3
        assert "Email" not in result.columns
        no_column = [r for r in caplog.records if "no-email exclusion could not be" in r.message]
        assert len(no_column) == 1
        assert "[Family] No 'Email' output column" in no_column[0].message
        assert self._exclusion_records(caplog) == []
        self._assert_no_pii(caplog)
