"""Integration tests for the Staff entity transformation."""

import logging

import pandas as pd
import pytest

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


class TestStaffRowFilters:
    """``row_filters`` on the Staff entity (SD83: a repurposed ``Prefix`` that states the role).

    The hook is generic on ``BaseTransformer.apply_row_filters``; before this it
    had exactly one caller (``FamilyTransformer``), so ``EntityConfig.row_filters``
    was silently inert on Staff — the config-key-that-does-nothing shape this repo
    treats as a fault.

    Scope boundary worth keeping in view while reading these: this is a district's
    OPT-IN narrowing and it fails CLOSED (a named column missing from the export
    raises). Employment status is NOT its job — :class:`TestStaffDepartedExclusion`
    covers that, data-keyed for every district and fail-OPEN. The fixture below is
    therefore all-``Active``, so ``filter_departed_staff`` is a no-op and each
    assertion here isolates the filter it names; the one test that deliberately
    exercises both layers says so.
    """

    #: SD83's shipped filter — the district's whole `row_filters` block.
    _PREFIX_FILTER = [{"column": "Prefix", "include": ["Teacher", "Administrator"]}]

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    @staticmethod
    def _sd83_shaped_staff() -> pd.DataFrame:
        """SD83's real header shape, normalized. ``Prefix`` states the role; T005
        carries a courtesy title there instead and exists only to be excluded."""
        return pd.DataFrame(
            {
                "school number": ["100", "100", "200", "200", "100"],
                "user name": ["jharper", "bwong", "lliu", "rsingh", "mgrant"],
                "teaching staff": ["Y", "N", "Y", "Y", "Y"],
                "teacher id": ["T001", "T002", "T003", "T004", "T005"],
                "name": ["Harper, Jane", "Wong, Ben", "Liu, Linda", "Singh, Raj", "Grant, Mia"],
                "prefix": ["Teacher", "Administrator", "Teacher", "Teacher", "Mr."],
                "last name": ["Harper", "Wong", "Liu", "Singh", "Grant"],
                "first name": ["Jane", "Ben", "Linda", "Raj", "Mia"],
                "email address": ["a@s.ca", "b@s.ca", "c@s.ca", "d@s.ca", "e@s.ca"],
                "staff status": ["Active"] * 5,
            }
        )

    @staticmethod
    def _sd83_shaped_mapping(*, row_filters: list | None = None) -> dict:
        return {
            "source_files": {"staff_info": "StaffInformationEnhanced.txt"},
            "row_filters": row_filters if row_filters is not None else [],
            "field_map": {
                "User ID": "Teacher Id",
                "First Name": "First Name",
                "Last Name": "Last Name",
                "Email": "Email Address",
                "Role": {"column": "Prefix", "transform": "normalize_staff_role"},
                "School ID": "School Number",
            },
        }

    def _run(self, staff_df, mapping, global_config):
        return self.transformer.transform(
            staff_df, mapping, "Staff", {"StaffInformationEnhanced.txt": staff_df}, global_config
        )

    def test_without_filters_every_row_survives(self, global_config):
        """The positive twin: the fixture really does carry the row the filter tests
        claim to remove, and `filter_departed_staff` is not quietly removing it."""
        staff_df = self._sd83_shaped_staff()
        result = self._run(staff_df, self._sd83_shaped_mapping(), global_config)
        assert len(result) == 5

    def test_prefix_filter_excludes_non_role_values(self, global_config):
        staff_df = self._sd83_shaped_staff()
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        assert len(result) == 4
        assert "T005" not in set(result["User ID"].astype(str))

    def test_surviving_rows_take_their_role_from_prefix(self, global_config):
        staff_df = self._sd83_shaped_staff()
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        roles = dict(zip(result["User ID"].astype(str), result["Role"]))
        assert roles == {"T001": "teacher", "T002": "administrator", "T003": "teacher", "T004": "teacher"}

    def test_prefix_beats_the_teaching_staff_flag(self, global_config):
        """The differential twin: rows whose Prefix and Teaching Staff DISAGREE must
        follow Prefix. Without this, a config that silently kept reading the flag
        would still pass every assertion above — those rows happen to agree."""
        staff_df = self._sd83_shaped_staff()
        staff_df.loc[0, "teaching staff"] = "N"  # flag says administrator, Prefix says Teacher
        staff_df.loc[1, "teaching staff"] = "Y"  # flag says teacher, Prefix says Administrator
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        roles = dict(zip(result["User ID"].astype(str), result["Role"]))
        assert roles["T001"] == "teacher"
        assert roles["T002"] == "administrator"

    @pytest.mark.parametrize("prefix", ["Teacher", "teacher", "TEACHER", "tEaChEr", "  Teacher  "])
    def test_filter_matching_is_case_and_whitespace_insensitive(self, global_config, prefix):
        """Both SIDES of a `row_filter` are trimmed + case-folded before comparison.

        Pinned because nothing else asserts it and a district's export capitalises
        however MyEd BC happens to emit it — a case-sensitive regression would
        silently filter out EVERY row, which reads as "this district has no staff"
        rather than as a fault.
        """
        staff_df = self._sd83_shaped_staff()
        staff_df["prefix"] = prefix
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        assert len(result) == len(staff_df), f"{prefix!r} did not match the configured value"
        assert set(result["Role"]) == {"teacher"}

    @pytest.mark.parametrize("prefix", ["Head Teacher", "Teacher Aide", "NotAdministrator"])
    def test_case_insensitivity_does_not_widen_the_filter(self, global_config, prefix):
        """The negative twin: case-folding must not turn the filter into a substring
        or truthiness test. Each value here CONTAINS a configured one, so a sloppy
        repair (`in` instead of set membership) would publish exactly the rows the
        filter exists to exclude — and every positive case above would still pass.
        """
        staff_df = self._sd83_shaped_staff()
        staff_df["prefix"] = prefix
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        assert result.empty, f"{prefix!r} was matched against an exact-membership filter"

    def test_a_missing_filter_column_raises(self, global_config):
        """Fail-loud at the boundary, and the deliberate contrast with
        `filter_departed_staff`: an opt-in filter a district ASKED for must not
        silently keep everyone when its column is renamed away."""
        staff_df = self._sd83_shaped_staff().drop(columns=["prefix"])
        with pytest.raises(ValueError, match="Staff"):
            self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)

    def test_filters_apply_AFTER_the_roster_merge(self, global_config):
        """Ordering trap, pinned. ``_merge_roster`` REPLACES ``working`` with a frame
        rebuilt from ``context.raw_data`` when a roster file is configured, so a
        filter applied at transform entry is silently discarded and every excluded
        row comes back. Family filters at entry only because it has no such rebuild.
        """
        staff_df = self._sd83_shaped_staff()
        roster_df = pd.DataFrame(
            {
                "teacher id": ["T001", "T002", "T003", "T004", "T005"],
                "staff sourceid": ["S1", "S2", "S3", "S4", "S5"],
            }
        )
        mapping = self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER)
        mapping["source_files"] = {
            "staff_info": "StaffInformationEnhanced.txt",
            "roster": "StudentSchedule.txt",
        }
        raw_data = {"StaffInformationEnhanced.txt": staff_df, "StudentSchedule.txt": roster_df}
        result = self.transformer.transform(staff_df, mapping, "Staff", raw_data, global_config)
        assert len(result) == 4
        assert "T005" not in set(result["User ID"].astype(str))

    def test_row_filters_and_departed_exclusion_COMPOSE(self, global_config):
        """The two layers are complementary, and this is the only test that runs both.

        SD83 configures NO status filter — `filter_departed_staff` covers employment
        for every district and fails OPEN, where `row_filters` fails CLOSED. So an
        Inactive teacher must still be excluded even though the district's config
        says nothing about status, while the courtesy-title row goes to the filter.
        """
        staff_df = self._sd83_shaped_staff()
        staff_df.loc[3, "staff status"] = "Inactive"  # T004, a departed teacher
        result = self._run(staff_df, self._sd83_shaped_mapping(row_filters=self._PREFIX_FILTER), global_config)
        ids = set(result["User ID"].astype(str))
        assert "T004" not in ids, "the departed-staff rule did not run alongside row_filters"
        assert "T005" not in ids, "the Prefix filter did not run alongside the departed-staff rule"
        assert ids == {"T001", "T002", "T003"}

    def test_an_unfiltered_non_role_prefix_is_a_blanked_cell_not_a_role(self, global_config):
        """Without the Prefix filter the courtesy-title row still ships (nothing
        excludes it) — but its Role is BLANK and recorded as a data error, never
        guessed. Row-resilience contract: the valid rows keep their values."""
        staff_df = self._sd83_shaped_staff()
        result = self._run(staff_df, self._sd83_shaped_mapping(), global_config)
        roles = dict(zip(result["User ID"].astype(str), result["Role"]))
        assert pd.isna(roles["T005"])
        assert roles["T001"] == "teacher"
