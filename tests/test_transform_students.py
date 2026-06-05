"""Integration tests for the Students entity transformation."""

import pandas as pd

from src.etl.transformer import DataTransformer


class TestStudentsTransform:
    def setup_method(self):
        self.transformer = DataTransformer()
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_full_student_transform(self, student_demographic_df, students_mapping, global_config, raw_data):
        result = self.transformer.transform(
            student_demographic_df, students_mapping, "Students", raw_data, global_config
        )
        # PreReg Grace (S007) is EXCLUDED by default (faq.md) — only the 6 Active
        # rows are retained. PreReg is not in the default active_values.
        # See DECISIONS: "PreReg excluded by default".
        active_only = student_demographic_df["enrolment status"] == "Active"
        assert len(result) == int(active_only.sum())

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
