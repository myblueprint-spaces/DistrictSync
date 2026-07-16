"""Integration tests for the Students entity transformation."""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer


class TestStudentsTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_full_student_transform(self, student_demographic_df, students_mapping, global_config, raw_data):
        result = self.transformer.transform(
            student_demographic_df, students_mapping, "Students", raw_data, global_config
        )
        # Active + PreReg are both retained by default (Advanced CSV spec) — PreReg
        # Grace (S007) stays; only Inactive/other statuses are dropped.
        # See DECISIONS: "PreReg included by default".
        active_or_prereg = student_demographic_df["enrolment status"].isin(["Active", "PreReg"])
        assert len(result) == int(active_or_prereg.sum())

        # Verify expected output columns from field_map
        for field in students_mapping["field_map"]:
            assert field in result.columns, f"Missing output column: {field}"

    def test_grade_mapped_to_ceds(self, student_demographic_df, students_mapping, global_config, raw_data):
        result = self.transformer.transform(
            student_demographic_df, students_mapping, "Students", raw_data, global_config
        )
        # Grade "K" should become "KG", "3" should become "03", etc.
        grades = result["Grade"].tolist()
        assert "KG" in grades  # K → KG
        assert "03" in grades  # 3 → 03
        assert "07" in grades  # 7 → 07

    def test_inactive_students_excluded(self, students_mapping, global_config):
        """Students with Inactive status should not appear in output.

        Exercises the one-L ``"enrolment status"`` spelling, which the
        predicate's status-column alias still honors (the alias covers both
        the one-L repo/SD40 spelling and the two-L real MyEd export header).
        """
        df = pd.DataFrame(
            {
                "student number": ["S001", "S002", "S003"],
                "legal first name": ["A", "B", "C"],
                "legal surname": ["X", "Y", "Z"],
                "date of birth": ["2010-01-01", "2010-01-01", "2010-01-01"],
                "grade": ["5", "6", "7"],
                "school number": ["100", "100", "100"],
                "homeroom": ["A", "B", "C"],
                "previous school number": ["", "", ""],
                "usual first name": ["", "", ""],
                "usual surname": ["", "", ""],
                "student email address": ["", "", ""],
                "enrolment status": ["Active", "Inactive", "Active"],
            }
        )
        raw_data = {"StudentDemographicInformation.txt": df}
        result = self.transformer.transform(df, students_mapping, "Students", raw_data, global_config)
        assert len(result) == 2

    def test_withdraw_date_transform(self, student_demographic_with_withdraw_df, students_mapping, global_config):
        df = student_demographic_with_withdraw_df
        raw_data = {"StudentDemographicInformation.txt": df}
        result = self.transformer.transform(df, students_mapping, "Students", raw_data, global_config)
        # S001: no date → Active (kept)
        # S002: past date → Inactive (filtered)
        # S003: future date → Active (kept)
        # S004: past date → Inactive (filtered)
        # S005: bad date → Inactive (filtered)
        assert len(result) == 2

    def test_date_of_birth_normalized_to_iso(self, students_mapping, global_config):
        """dd-MMM-yyyy dates from MyEd BC GDE should be normalized to yyyy-mm-dd
        in the output so they match the format used by Classes.csv.
        """
        df = pd.DataFrame(
            {
                "student number": ["S001", "S002", "S003"],
                "legal first name": ["A", "B", "C"],
                "legal surname": ["X", "Y", "Z"],
                "date of birth": ["15-Sep-2010", "2011-03-04", ""],
                "grade": ["5", "6", "7"],
                "school number": ["100", "100", "100"],
                "homeroom": ["A", "B", "C"],
                "previous school number": ["", "", ""],
                "usual first name": ["", "", ""],
                "usual surname": ["", "", ""],
                "student email address": ["", "", ""],
                "enrolment status": ["Active", "Active", "Active"],
            }
        )
        raw_data = {"StudentDemographicInformation.txt": df}
        result = self.transformer.transform(df, students_mapping, "Students", raw_data, global_config)
        dobs = result["Date of Birth"].tolist()
        assert dobs[0] == "2010-09-15"  # dd-MMM-yyyy → ISO
        assert dobs[1] == "2011-03-04"  # already ISO, pass-through
        assert dobs[2] == ""  # empty stays empty

    def test_blank_required_name_coalesces_from_preferred(self, global_config):
        """When the config maps First/Last Name to sparse columns (SD74-style:
        primary ← Usual, Preferred ← Legal), a blank required name falls back to the
        preferred-name value so the Advanced-CSV-required field is never empty.
        """
        df = pd.DataFrame(
            {
                "student number": ["S001", "S002", "S003"],
                "legal first name": ["Logan", "Sophia", "Mia"],
                "legal surname": ["Thompson", "Thomas", "Wilson"],
                "usual first name": ["Lo", "", ""],  # only S001 has a preferred first name
                "usual surname": ["", "", ""],  # nobody has a preferred surname
                "date of birth": ["2010-01-01", "2010-01-01", "2010-01-01"],
                "grade": ["5", "6", "7"],
                "school number": ["100", "100", "100"],
                "homeroom": ["A", "B", "C"],
                "previous school number": ["", "", ""],
                "student email address": ["", "", ""],
                "enrolment status": ["Active", "Active", "Active"],
            }
        )
        # SD74-style swap: primary name ← Usual columns, Preferred name ← Legal columns.
        mapping = {
            "source_files": {"student_demographic": "StudentDemographicInformation.txt"},
            "field_map": {
                "User ID": "Student Number",
                "First Name": "Usual First Name",
                "Last Name": "Usual surname",
                "Grade": {"column": "Grade", "transform": "grade_to_ceds"},
                "EnrollStatus": None,
                "Preferred First Name": "Legal First Name",
                "Preferred Last Name": "Legal Surname",
            },
        }
        raw_data = {"StudentDemographicInformation.txt": df}
        result = self.transformer.transform(df, mapping, "Students", raw_data, global_config)

        # First Name: keep the preferred where present (Lo), else fall back to Legal.
        assert result["First Name"].tolist() == ["Lo", "Sophia", "Mia"]
        # Last Name: Usual surname is blank for all → fall back to Legal surname.
        assert result["Last Name"].tolist() == ["Thompson", "Thomas", "Wilson"]
        # The preferred columns themselves are untouched.
        assert result["Preferred First Name"].tolist() == ["Logan", "Sophia", "Mia"]

    def test_email_generation_when_format_configured(self, global_config):
        df = pd.DataFrame(
            {
                "student number": ["12345"],
                "legal first name": ["Alice"],
                "legal surname": ["Smith"],
                "date of birth": ["2010-01-01"],
                "grade": ["5"],
                "school number": ["100"],
                "homeroom": ["A"],
                "previous school number": [""],
                "usual first name": [""],
                "usual surname": [""],
                "student email address": [""],
                "enrolment status": ["Active"],
            }
        )
        mapping = {
            "source_files": {"student_demographic": "StudentDemographicInformation.txt"},
            "field_map": {
                "User ID": "Student Number",
                "Student Number": "Student Number",
                "First Name": "Legal First Name",
                "Last Name": "Legal Surname",
                "Date of Birth": "Date of birth",
                "Grade": {"column": "Grade", "transform": "grade_to_ceds"},
                "EnrollStatus": None,
                "SchoolCode": "School Number",
                "Homeroom": "Homeroom",
                "PreRegSchoolCode": "Previous school number",
                "Preferred First Name": "Usual First Name",
                "Preferred Last Name": "Usual surname",
                "Community Hours": {"value": ""},
                "Literacy Test Completed": {"value": ""},
                "Email Address": {"format": "{student number}@test.ca"},
            },
        }
        raw_data = {"StudentDemographicInformation.txt": df}
        result = self.transformer.transform(df, mapping, "Students", raw_data, global_config)
        assert result["Email Address"].iloc[0] == "12345@test.ca"


class TestStudentsCrossEnrollmentCollapse:
    """Opt-in cross-enrollment collapse (SD60): dedupe Students rows sharing a
    User ID to one row, keeping the home-school row. Off by default."""

    _MAPPING = {
        "source_files": {"student_demographic": "Demo.txt"},
        "field_map": {
            "User ID": "Student Number",
            "First Name": "Legal First Name",
            "Last Name": "Legal Surname",
            "SchoolCode": "School Number",
            "EnrollStatus": None,
        },
    }

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _cross_df(self):
        # S001 is Active at two schools (200 and its home school 100); S002 single.
        return pd.DataFrame(
            {
                "student number": ["S001", "S001", "S002"],
                "legal first name": ["Alice", "Alice", "Bob"],
                "legal surname": ["Smith", "Smith", "Jones"],
                "school number": ["200", "100", "100"],
                "home school number": ["100", "100", "100"],
                "enrolment status": ["Active", "Active", "Active"],
            }
        )

    def _gc(self, collapse=True, home="home school number"):
        gc = {"academic_start_month_day": "08-25", "academic_end_month_day": "07-25"}
        if collapse is not None:
            gc["cross_enrollment"] = {"collapse": collapse, "home_school_column": home}
        return gc

    def test_collapse_keeps_home_school_row(self):
        df = self._cross_df()
        result = self.transformer.transform(df, self._MAPPING, "Students", {"Demo.txt": df}, self._gc())
        assert set(result["User ID"]) == {"S001", "S002"}
        assert len(result) == 2
        s001 = result[result["User ID"] == "S001"]
        # The home-school row (School == Home School == 100) is the one retained.
        assert s001["SchoolCode"].iloc[0] == "100"

    def test_single_row_student_untouched(self):
        df = pd.DataFrame(
            {
                "student number": ["S001"],
                "legal first name": ["Alice"],
                "legal surname": ["Smith"],
                "school number": ["200"],  # not the home school, but the only row
                "home school number": ["100"],
                "enrolment status": ["Active"],
            }
        )
        result = self.transformer.transform(df, self._MAPPING, "Students", {"Demo.txt": df}, self._gc())
        assert len(result) == 1
        assert result["SchoolCode"].iloc[0] == "200"

    def test_collapse_false_keeps_both_rows(self):
        df = self._cross_df()
        result = self.transformer.transform(df, self._MAPPING, "Students", {"Demo.txt": df}, self._gc(collapse=False))
        assert len(result) == 3

    def test_collapse_config_absent_keeps_both_rows(self):
        df = self._cross_df()
        result = self.transformer.transform(df, self._MAPPING, "Students", {"Demo.txt": df}, self._gc(collapse=None))
        assert len(result) == 3

    def test_missing_home_school_column_raises(self):
        df = self._cross_df().drop(columns="home school number")
        with pytest.raises(ValueError, match="home_school_column"):
            self.transformer.transform(df, self._MAPPING, "Students", {"Demo.txt": df}, self._gc())


class TestStudentEmailGeneration:
    """Opt-in email extensions (plan 0030): sanitize + derived date parts.

    Exercised through the full DataTransformer.transform() path so the
    copy-isolation of the injected pseudo-columns and the fail-loud missing
    column boundary are covered end-to-end. Both knobs default off → the
    default path is proven byte-identical by the regression test below.
    """

    _GC = {"academic_start_month_day": "08-25", "academic_end_month_day": "07-25"}

    # SD60-shape template: firstlast + 2-digit admission year, sanitized.
    _SD60_EMAIL = {
        "format": "{legal first name}{legal surname}{admission yy}@learn60.ca",
        "sanitize": True,
        "derived_dates": {"admission yy": {"column": "Admission date", "date_format": "yy"}},
    }

    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def _mapping(self, email_cfg):
        return {
            "source_files": {"student_demographic": "Demo.txt"},
            "field_map": {
                "User ID": "Student Number",
                "First Name": "Legal First Name",
                "Last Name": "Legal Surname",
                "SchoolCode": "School Number",
                "EnrollStatus": None,
                "Email Address": email_cfg,
            },
        }

    def _df(self, first, surname, admission):
        return pd.DataFrame(
            {
                "student number": ["S001"],
                "legal first name": [first],
                "legal surname": [surname],
                "school number": ["100"],
                "admission date": [admission],
                "enrolment status": ["Active"],
            }
        )

    def _run(self, email_cfg, df):
        return self.transformer.transform(df, self._mapping(email_cfg), "Students", {"Demo.txt": df}, self._GC)

    def test_derived_date_two_digit_year(self):
        # DD-MMM-YYYY admission date -> 2-digit year suffix.
        result = self._run(self._SD60_EMAIL, self._df("Alice", "Smith", "15-Sep-2018"))
        assert result["Email Address"].iloc[0] == "alicesmith18@learn60.ca"

    def test_sanitize_strips_apostrophe_hyphen_space(self):
        # Apostrophe + hyphen (surname) + internal space (first name) all removed.
        result = self._run(self._SD60_EMAIL, self._df("Mary Anne", "O'Brien-Smith", "15-Sep-2018"))
        assert result["Email Address"].iloc[0] == "maryanneobriensmith18@learn60.ca"

    def test_unparseable_admission_yields_no_suffix(self):
        # An unparseable admission date -> firstlast with NO year (never "...unknown").
        result = self._run(self._SD60_EMAIL, self._df("Alice", "Smith", "Unknown"))
        assert result["Email Address"].iloc[0] == "alicesmith@learn60.ca"

    def test_blank_admission_yields_no_suffix(self):
        result = self._run(self._SD60_EMAIL, self._df("Alice", "Smith", ""))
        assert result["Email Address"].iloc[0] == "alicesmith@learn60.ca"

    def test_missing_derived_column_raises(self):
        df = self._df("Alice", "Smith", "15-Sep-2018").drop(columns="admission date")
        with pytest.raises(ValueError, match="derived_dates"):
            self._run(self._SD60_EMAIL, df)

    def test_pseudo_column_not_leaked_into_output(self):
        # The injected pseudo-column lives only on the local copy — it must not
        # appear as an output column (proves working frame isolation).
        result = self._run(self._SD60_EMAIL, self._df("Alice", "Smith", "15-Sep-2018"))
        assert "admission yy" not in result.columns

    def test_generate_emails_does_not_mutate_source_frame(self):
        # Direct invariant behind the isolation claim: _generate_emails injects the
        # derived pseudo-column onto a LOCAL copy only, so the `working` frame that
        # transform() later hands to apply_field_map is never polluted.
        from src.etl.transformers.context import TransformContext
        from src.etl.transformers.students import StudentTransformer

        working = self._df("Alice", "Smith", "15-Sep-2018")  # columns already lower-cased
        result = pd.DataFrame()
        field_map = self._mapping(self._SD60_EMAIL)["field_map"]
        StudentTransformer()._generate_emails(working, result, field_map, TransformContext())
        assert "admission yy" not in working.columns  # source frame untouched
        assert result["Email Address"].iloc[0] == "alicesmith18@learn60.ca"

    def test_bad_template_key_blanks_cells_and_records_data_error(self, caplog):
        # A template naming a column absent from the frame must not silently
        # blank every email: each cell still becomes "" (row-resilient), but the
        # failure lands in context.data_errors + one ERROR log — surfacing as
        # "Completed with N data errors" instead of invisible blanks.
        email_cfg = {"format": "{no such column}@learn60.ca"}
        df = pd.concat(
            [self._df("Alice", "Smith", "15-Sep-2018"), self._df("Bob", "Jones", "15-Sep-2019")],
            ignore_index=True,
        )
        df["student number"] = ["S001", "S002"]
        with caplog.at_level("ERROR"):
            result = self._run(email_cfg, df)
        assert list(result["Email Address"]) == ["", ""]
        errors = self.transformer.data_errors
        assert len(errors) == 1
        assert errors[0]["entity"] == "Students"
        assert errors[0]["field"] == "Email Address"
        assert errors[0]["failed_rows"] == 2
        assert "no such column" in errors[0]["sample"]
        assert any("Students.Email Address" in r.message for r in caplog.records)

    def test_good_template_records_no_data_error(self):
        email_cfg = {"format": "{legal first name}.{legal surname}@learn60.ca"}
        result = self._run(email_cfg, self._df("Alice", "Smith", "15-Sep-2018"))
        assert result["Email Address"].iloc[0] == "alice.smith@learn60.ca"
        assert self.transformer.data_errors == []

    def test_default_path_preserves_apostrophe_regression(self):
        # SD54-shape template with NO sanitize/derived_dates -> legacy path:
        # lowercase + collapse spaces but KEEP the apostrophe (byte-identical to
        # pre-0030 behavior). This is the other-districts-unchanged guard.
        email_cfg = {"format": "{legal surname}.{legal first name}@sd54.bc.ca"}
        result = self._run(email_cfg, self._df("Mary", "O'Brien", "15-Sep-2018"))
        assert result["Email Address"].iloc[0] == "o'brien.mary@sd54.bc.ca"
