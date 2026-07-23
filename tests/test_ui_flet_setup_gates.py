"""Tests for src/ui_flet/setup_gates.py — the pure Setup submit-gate predicates.

These are the single source the disabled-button state AND the Enter-to-submit
(`on_submit`) handlers both read, so Enter can never bypass a gate a disabled
button enforces (Slice 2, D-chrome / Problem #9).
"""

from __future__ import annotations

import pytest

from src.ui_flet.setup_gates import can_register_schedule, can_save_sftp, window_settings_valid


class TestWindowSettingsValid:
    """B: the seasonal-window save/advance gate. Disabled → always valid (year-round, fields
    ignored); enabled → both bounds must be real ``MM-DD`` calendar days. Single-sources the
    "Enter can't bypass an invalid window" guarantee (wizard Continue + on-change persistence)."""

    def test_disabled_is_always_valid_even_with_garbage_bounds(self) -> None:
        assert window_settings_valid(False, "", "") is True
        assert window_settings_valid(False, "99-99", "not-a-date") is True

    def test_enabled_with_valid_bounds_is_valid(self) -> None:
        assert window_settings_valid(True, "08-11", "07-06") is True

    def test_enabled_accepts_leap_day_boundary(self) -> None:
        assert window_settings_valid(True, "02-29", "07-06") is True

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            ("", "07-06"),  # blank start
            ("08-11", ""),  # blank end
            ("13-01", "07-06"),  # out-of-range month
            ("08-11", "02-30"),  # non-existent day
            ("8-1", "07-06"),  # wrong shape (not zero-padded)
            ("0811", "07-06"),  # wrong shape (no separator)
        ],
    )
    def test_enabled_with_any_bad_bound_is_invalid(self, start, end) -> None:
        assert window_settings_valid(True, start, end) is False

    def test_none_bounds_do_not_raise(self) -> None:
        assert window_settings_valid(True, None, None) is False  # type: ignore[arg-type]


class TestCanRegisterSchedule:
    @pytest.mark.parametrize(
        ("config_complete", "run_time", "expected"),
        [
            (True, "03:00", True),  # complete config + a time → gate open
            (True, "3:00", True),  # non-blank time (format validated downstream, not here)
            (True, "", False),  # blank time → closed (Enter is a no-op)
            (True, "   ", False),  # whitespace-only time → closed
            (False, "03:00", False),  # incomplete config → closed even with a time
            (False, "", False),  # nothing → closed
        ],
    )
    def test_truth_table(self, config_complete, run_time, expected):
        assert can_register_schedule(config_complete, run_time) is expected

    def test_none_run_time_is_closed(self):
        # Defensive: a None value (uninitialised TextField) must not raise.
        assert can_register_schedule(True, None) is False  # type: ignore[arg-type]


class TestCanSaveSftp:
    def _call(self, *, host="h.example.com", username="u", remote_path="/files", password="", already_configured=False):
        return can_save_sftp(
            host=host,
            username=username,
            remote_path=remote_path,
            password=password,
            already_configured=already_configured,
        )

    def test_first_time_needs_all_fields_plus_password(self):
        # No stored credential yet → a password is required.
        assert self._call(password="pw", already_configured=False) is True
        assert self._call(password="", already_configured=False) is False

    def test_resave_may_keep_stored_credential_blank_password(self):
        # Already configured → the required fields alone open the gate (keep the stored pw).
        assert self._call(password="", already_configured=True) is True
        assert self._call(password="pw", already_configured=True) is True

    @pytest.mark.parametrize("missing", ["host", "username", "remote_path"])
    def test_any_missing_required_field_closes_gate(self, missing):
        kwargs = {missing: "  "}  # whitespace-only counts as missing
        assert self._call(already_configured=True, **kwargs) is False

    def test_none_values_do_not_raise(self):
        assert (
            can_save_sftp(host=None, username=None, remote_path=None, password=None, already_configured=True)  # type: ignore[arg-type]
            is False
        )
