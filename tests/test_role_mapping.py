"""Tests for staff role mapping and user role/ID generation."""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer
from src.etl.transformers.base import BaseTransformer

NO_STAFF_ROLE = BaseTransformer.NO_STAFF_ROLE


class TestMapRole:
    """The teaching FLAG yields `teacher` or NOTHING — never `administrator`.

    It used to send every non-`"y"` value to `administrator`, a real privilege
    level in SpacesEDU, which silently granted it to secretaries, education
    assistants and custodial staff at every district (plan 0051). The flag
    answers *does this person teach*; it has never said anything about who
    administers, and absence of a `"y"` is not evidence of anything.

    `NO_STAFF_ROLE` is not a third role — `StaffTransformer.resolve_staff_roles`
    removes the rows still carrying it (after rescuing anyone who demonstrably
    teaches). See :class:`TestStaffUnstatedRoles` in `test_transform_staff.py`
    for the row-level half of this contract.
    """

    @pytest.mark.parametrize(
        "flag, expected",
        [
            ("Y", "teacher"),
            ("y", "teacher"),
            (" Y ", "teacher"),
            ("N", NO_STAFF_ROLE),
            ("n", NO_STAFF_ROLE),
            ("", NO_STAFF_ROLE),
            ("No", NO_STAFF_ROLE),
            ("Yes", NO_STAFF_ROLE),  # Only exact "y" is teacher
        ],
    )
    def test_map_role(self, flag, expected):
        assert DataTransformer.map_role(flag) == expected

    def test_map_role_none(self):
        assert DataTransformer.map_role(None) == NO_STAFF_ROLE

    def test_map_role_nan(self):
        assert DataTransformer.map_role(float("nan")) == NO_STAFF_ROLE

    @pytest.mark.parametrize(
        "flag",
        ["N", "n", "", "   ", "No", "Yes", "True", "1", "Teacher", "Administrator", None, float("nan")],
    )
    def test_no_input_whatsoever_yields_administrator(self, flag):
        """The regression guard, stated as the rule rather than a value table.

        Nothing a district can put in a teaching-flag column may produce
        `administrator` — including the literal word. That value is reachable
        ONLY through `normalize_staff_role`, against a column the district
        populated to state the role outright.
        """
        assert DataTransformer.map_role(flag) != "administrator"

    def test_exactly_y_is_the_only_teacher(self):
        """Positive twin: the rule above is not vacuously true by returning
        `NO_STAFF_ROLE` for everything."""
        assert DataTransformer.map_role("Y") == "teacher"


class TestNormalizeStaffRole:
    """The Prefix-column role pass-through (SD83): the source states the ROLE.

    Sibling of :class:`TestMapRole`, which maps a teaching FLAG. This one reads
    a column whose values already ARE the contract's roles, so it normalizes
    (trim + case-fold) and passes through — and RAISES on anything else rather
    than inventing a role. The raise is what makes it fail loud: `apply_field_map`
    turns it into a blanked cell plus a recorded data error, never a silent
    "administrator".
    """

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("teacher", "teacher"),
            ("Teacher", "teacher"),
            ("TEACHER", "teacher"),
            (" Teacher ", "teacher"),
            ("administrator", "administrator"),
            ("Administrator", "administrator"),
            ("ADMINISTRATOR", "administrator"),
            (" Administrator ", "administrator"),
        ],
    )
    def test_contract_roles_pass_through_case_insensitively(self, value, expected):
        assert DataTransformer.normalize_staff_role(value) == expected

    @pytest.mark.parametrize("value", ["Mr.", "Ms", "Dr.", "admin", "Principal", "", "   ", "Y", "N"])
    def test_any_other_value_raises(self, value):
        """Including "admin" — a near-miss must not be silently widened into a role."""
        with pytest.raises(ValueError):
            DataTransformer.normalize_staff_role(value)

    @pytest.mark.parametrize("value", [None, float("nan")])
    def test_missing_values_raise(self, value):
        """A blank Prefix is an unanswered question, not an administrator."""
        with pytest.raises(ValueError):
            DataTransformer.normalize_staff_role(value)

    def test_the_message_names_the_column_and_the_accepted_values(self):
        """Actionable for a district admin reading the run log — and it must NOT
        echo a name or an email (the cell it reads is a title, but the rule that
        keeps PII out of error text is worth holding here too)."""
        with pytest.raises(ValueError) as exc:
            DataTransformer.normalize_staff_role("Principal")
        message = str(exc.value)
        assert "teacher" in message and "administrator" in message

    def test_it_is_on_the_transform_allowlist(self):
        """A transform name absent from ALLOWED_TRANSFORMS is rejected fail-fast at
        config load, so the method existing is only half of shipping it."""
        from src.config.models import ALLOWED_TRANSFORMS

        assert "normalize_staff_role" in ALLOWED_TRANSFORMS


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
