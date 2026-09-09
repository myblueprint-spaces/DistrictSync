"""Integration tests for the Staff entity transformation."""

import logging

import pandas as pd

from src.etl.transformer import DataTransformer


class TestStaffTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_basic_staff_transform(self, staff_info_df, staff_mapping, global_config, raw_data):
        result = self.transformer.transform(staff_info_df, staff_mapping, "Staff", raw_data, global_config)
        assert len(result) == len(staff_info_df)
        for field in staff_mapping["field_map"]:
            assert field in result.columns

    def test_role_mapping(self, staff_info_df, staff_mapping, global_config, raw_data):
        result = self.transformer.transform(staff_info_df, staff_mapping, "Staff", raw_data, global_config)
        roles = result["Role"].tolist()
        # T005 has "N" → administrator, rest have "Y" → teacher
        assert roles.count("teacher") == 4
        assert roles.count("administrator") == 1

    def test_staff_with_roster_merge(self, staff_info_df, global_config):
        """When a roster file with 'staff sourceid' exists, it should be merged."""
        roster_df = pd.DataFrame(
            {
                "teacher id": ["T001", "T002", "T003"],
                "staff sourceid": ["SRC001", "SRC002", "SRC003"],
                "other_col": ["x", "y", "z"],
            }
        )
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
        result = self.transformer.transform(staff_info_df, mapping, "Staff", raw_data, global_config)
        assert len(result) == len(staff_info_df)

    def test_staff_empty_input_returns_empty(self, staff_mapping, global_config):
        """An empty DataFrame input should return an empty result without error."""
        empty_df = pd.DataFrame(
            columns=["teacher id", "first name", "last name", "email address", "teaching staff", "school number"]
        )
        raw_data = {"StaffInformationEnhanced.txt": empty_df}
        result = self.transformer.transform(empty_df, staff_mapping, "Staff", raw_data, global_config)
        assert result.empty

    def test_staff_all_administrators(self, staff_mapping, global_config):
        """All staff with Teaching Staff = 'N' should have role 'administrator'."""
        admin_df = pd.DataFrame(
            {
                "teacher id": ["T001", "T002"],
                "first name": ["Alice", "Bob"],
                "last name": ["Smith", "Jones"],
                "email address": ["alice@school.ca", "bob@school.ca"],
                "teaching staff": ["N", "N"],
                "school number": ["100", "100"],
            }
        )
        raw_data = {"StaffInformationEnhanced.txt": admin_df}
        result = self.transformer.transform(admin_df, staff_mapping, "Staff", raw_data, global_config)
        assert all(r == "administrator" for r in result["Role"].tolist())

    def test_staff_mixed_case_teaching_flag(self, staff_mapping, global_config):
        """Teaching Staff flag should be case-insensitive (Y/y/N/n)."""
        mixed_df = pd.DataFrame(
            {
                "teacher id": ["T001", "T002", "T003", "T004"],
                "first name": ["A", "B", "C", "D"],
                "last name": ["A", "B", "C", "D"],
                "email address": ["a@s.ca", "b@s.ca", "c@s.ca", "d@s.ca"],
                "teaching staff": ["Y", "y", "N", "n"],
                "school number": ["100", "100", "100", "100"],
            }
        )
        raw_data = {"StaffInformationEnhanced.txt": mixed_df}
        result = self.transformer.transform(mixed_df, staff_mapping, "Staff", raw_data, global_config)
        roles = result["Role"].tolist()
        assert roles.count("teacher") == 2
        assert roles.count("administrator") == 2

    def test_staff_roster_merge_no_overlap(self, staff_info_df, global_config):
        """Roster with no matching teacher IDs — staff sourceid should be NaN."""
        roster_df = pd.DataFrame(
            {
                "teacher id": ["T999", "T998"],
                "staff sourceid": ["SRC999", "SRC998"],
            }
        )
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
        result = self.transformer.transform(staff_info_df, mapping, "Staff", raw_data, global_config)
        # All rows retained (left join), no match = no sourceid enrichment
        assert len(result) == len(staff_info_df)

    def test_staff_deduplication(self, staff_mapping, global_config):
        """Duplicate teacher IDs in source should each produce a row (no silent dedup)."""
        dup_df = pd.DataFrame(
            {
                "teacher id": ["T001", "T001", "T002"],
                "first name": ["Jane", "Jane", "Mark"],
                "last name": ["Harper", "Harper", "Reed"],
                "email address": ["harper@school.ca", "harper@school.ca", "reed@school.ca"],
                "teaching staff": ["Y", "Y", "Y"],
                "school number": ["100", "200", "100"],  # same teacher, two schools
            }
        )
        raw_data = {"StaffInformationEnhanced.txt": dup_df}
        result = self.transformer.transform(dup_df, staff_mapping, "Staff", raw_data, global_config)
        assert len(result) == 3  # Both rows preserved

    def test_staff_school_id_mapped(self, staff_info_df, staff_mapping, global_config, raw_data):
        """School ID output column should contain the school number values."""
        result = self.transformer.transform(staff_info_df, staff_mapping, "Staff", raw_data, global_config)
        assert "School ID" in result.columns
        school_ids = set(result["School ID"].astype(str).tolist())
        assert "100" in school_ids
        assert "200" in school_ids

    def test_staff_missing_email_does_not_crash(self, staff_mapping, global_config):
        """Staff with missing email column should produce NaN (not crash)."""
        no_email_df = pd.DataFrame(
            {
                "teacher id": ["T001"],
                "first name": ["Jane"],
                "last name": ["Harper"],
                # no 'email address' column
                "teaching staff": ["Y"],
                "school number": ["100"],
            }
        )
        raw_data = {"StaffInformationEnhanced.txt": no_email_df}
        result = self.transformer.transform(no_email_df, staff_mapping, "Staff", raw_data, global_config)
        assert len(result) == 1
        # Email should be missing / NA — not crash
        assert "Email" in result.columns


class TestStaffDepartedExclusion:
    """Departed staff must not ship as active SpacesEDU users.

    WHY: a MyEd BC staff GDE is unfiltered — it carries former employees marked
    "Inactive" alongside current ones. SD74 reported a departed teacher showing
    as active; their 2026-09-08 drop carried 13 Inactive of 163 (SD40 32 of
    1129, SD60 2 of 82, Unity Christian 206 of 306).

    The rule engages on the DATA, not on per-district config, so a district that
    has never been looked at is covered too — but only when it speaks a status
    vocabulary we recognise. Every "does not filter" case below therefore has a
    positive twin proving the filter fires at all on the same shape.
    """

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    @staticmethod
    def _staff_df(statuses, *, status_col="staff status"):
        """A staff frame of len(statuses) rows, one per status value."""
        n = len(statuses)
        return pd.DataFrame(
            {
                "teacher id": [f"T{i:03d}" for i in range(1, n + 1)],
                "first name": [f"First{i}" for i in range(1, n + 1)],
                "last name": [f"Last{i}" for i in range(1, n + 1)],
                "email address": [f"user{i}@school.ca" for i in range(1, n + 1)],
                "teaching staff": ["Y"] * n,
                "school number": ["100"] * n,
                status_col: list(statuses),
            }
        )

    def _run(self, df, staff_mapping, global_config, mapping=None):
        raw_data = {"StaffInformationEnhanced.txt": df}
        return self.transformer.transform(df, mapping or staff_mapping, "Staff", raw_data, global_config)

    # -- the filter fires -------------------------------------------------

    def test_inactive_staff_excluded(self, staff_mapping, global_config):
        df = self._staff_df(["Active", "Inactive", "Active"])
        result = self._run(df, staff_mapping, global_config)
        assert list(result["User ID"]) == ["T001", "T003"]

    def test_blank_status_excluded(self, staff_mapping, global_config):
        """A blank status is not a positive signal of employment (owner decision).

        Guards the pandas trap too: a CSV blank arrives as NaN, which
        ``normalize_id_series`` stringifies to the literal "nan" — a filter
        comparing against "" alone would silently KEEP these rows.
        """
        df = self._staff_df(["Active", "", None, "Inactive"])
        result = self._run(df, staff_mapping, global_config)
        assert list(result["User ID"]) == ["T001"]

    def test_status_matching_ignores_case_and_whitespace(self, staff_mapping, global_config):
        df = self._staff_df(["  ACTIVE  ", "inactive", "Active"])
        result = self._run(df, staff_mapping, global_config)
        assert list(result["User ID"]) == ["T001", "T003"]

    def test_filter_applies_after_roster_merge(self, global_config):
        """The ordering invariant: _merge_roster REPLACES the working frame.

        Filtering before it would be silently discarded on the roster path, so
        this asserts the exclusion survives a two-source-file config.
        """
        staff_df = self._staff_df(["Active", "Inactive"])
        roster_df = pd.DataFrame({"teacher id": ["T001", "T002"], "staff sourceid": ["SRC001", "SRC002"]})
        mapping = {
            "source_files": {
                "staff_info": "StaffInformationEnhanced.txt",
                "roster": "StudentSchedule.txt",
            },
            "field_map": {
                "User ID": "Teacher Id",
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Role": {"transform": "map_role", "column": "Teaching Staff"},
            },
        }
        raw_data = {"StaffInformationEnhanced.txt": staff_df, "StudentSchedule.txt": roster_df}
        result = self.transformer.transform(staff_df, mapping, "Staff", raw_data, global_config)
        assert list(result["User ID"]) == ["T001"]

    def test_configured_status_column_is_honoured(self, staff_mapping, global_config):
        """Configurable Columns: a district may rename the status column."""
        df = self._staff_df(["Active", "Inactive"], status_col="employment state")
        mapping = {**staff_mapping, "source_columns": {"staff_status": "Employment State"}}
        result = self._run(df, staff_mapping, global_config, mapping=mapping)
        assert list(result["User ID"]) == ["T001"]

    # -- the filter stands down (each twinned with a firing case above) ----

    def test_no_status_column_keeps_every_row(self, staff_info_df, staff_mapping, global_config, raw_data):
        """A district whose export has no status column has told us nothing."""
        result = self.transformer.transform(staff_info_df, staff_mapping, "Staff", raw_data, global_config)
        assert len(result) == len(staff_info_df)

    def test_unrecognised_vocabulary_keeps_every_row_and_warns(self, staff_mapping, global_config, caplog):
        """Fail OPEN: a blind == "active" would empty Staff.csv for this district."""
        df = self._staff_df(["Employed", "Terminated", "Employed"])
        with caplog.at_level(logging.WARNING):
            result = self._run(df, staff_mapping, global_config)
        assert len(result) == 3
        assert any("unrecognised value(s)" in r.message and "[Staff]" in r.message for r in caplog.records)

    def test_all_inactive_keeps_every_row_and_warns(self, staff_mapping, global_config, caplog):
        """The floor: never deliver an empty Staff.csv."""
        df = self._staff_df(["Inactive", "Inactive"])
        with caplog.at_level(logging.WARNING):
            result = self._run(df, staff_mapping, global_config)
        assert len(result) == 2
        assert any("marks none of" in r.message for r in caplog.records)

    def test_all_blank_status_keeps_every_row(self, staff_mapping, global_config, caplog):
        """An all-blank column has an empty vocabulary — it reaches the same floor."""
        df = self._staff_df(["", "", ""])
        with caplog.at_level(logging.WARNING):
            result = self._run(df, staff_mapping, global_config)
        assert len(result) == 3
        assert any("marks none of" in r.message for r in caplog.records)

    # -- privacy ----------------------------------------------------------

    def test_exclusion_log_carries_no_pii(self, staff_mapping, global_config, caplog):
        """Counts and vocabulary only — never a staff name or email."""
        df = self._staff_df(["Active", "Inactive"])
        with caplog.at_level(logging.INFO):
            self._run(df, staff_mapping, global_config)
        logged = " ".join(r.message for r in caplog.records)
        assert "Excluded 1 of 2" in logged
        for leaked in ("Last1", "Last2", "user1@school.ca", "user2@school.ca", "T002"):
            assert leaked not in logged
