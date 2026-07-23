"""Tests for src/ui_flet/setup_gates.py — the pure Setup submit-gate predicates.

These are the single source the disabled-button state AND the Enter-to-submit
(`on_submit`) handlers both read, so Enter can never bypass a gate a disabled
button enforces (Slice 2, D-chrome / Problem #9).
"""

from __future__ import annotations

import pytest

from src.ui_flet.setup_gates import (
    can_register_schedule,
    can_save_sftp,
    window_settings_valid,
    window_valid_from_config,
)


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


class TestWindowValidFromConfig:
    """FIX 3: the Schedule section rebuilds (Back->Forward) from PERSISTED config — the last VALID
    bounds, since an enabled+invalid edit persists nothing — with an empty error slot, yet the live
    on-change handler that sets the wizard's ``window_valid`` flag never re-fires on a rebuild.
    ``window_valid_from_config`` re-derives the advance gate on every (re)build (persisted bounds,
    or the district pre-fill when unset) so a stale ``False`` can't strand the Schedule step's
    Continue / "Set up later" (both gate on the flag). It single-sources the pre-fill fallback over
    the existing ``window_settings_valid`` engine gate."""

    def test_enabled_valid_saved_bounds_regate_true(self) -> None:
        # cfg holds the last VALID bounds (the invalid end never persisted) -> the gate re-opens.
        assert (
            window_valid_from_config(
                enabled=True, start_md="08-11", end_md="07-06", prefill_start="08-11", prefill_end="07-06"
            )
            is True
        )

    def test_unset_bounds_fall_back_to_prefill(self) -> None:
        # Enabled but no saved bounds yet -> the district pre-fill (valid) fills the gap, gate open.
        assert (
            window_valid_from_config(
                enabled=True, start_md=None, end_md=None, prefill_start="08-11", prefill_end="07-06"
            )
            is True
        )
        assert (
            window_valid_from_config(enabled=True, start_md="", end_md="", prefill_start="08-11", prefill_end="07-06")
            is True
        )

    def test_disabled_is_always_valid(self) -> None:
        assert (
            window_valid_from_config(
                enabled=False, start_md="99-99", end_md="nope", prefill_start="08-11", prefill_end="07-06"
            )
            is True
        )

    def test_enabled_invalid_saved_is_false(self) -> None:
        # Defensive/total: a persisted-but-malformed bound (hand-edited config.json) still closes it.
        assert (
            window_valid_from_config(
                enabled=True, start_md="13-99", end_md="07-06", prefill_start="08-11", prefill_end="07-06"
            )
            is False
        )

    def test_matches_window_settings_valid_over_bound_or_prefill(self) -> None:
        # Single-sources the fallback: it is exactly window_settings_valid over (bound or prefill).
        assert window_valid_from_config(
            enabled=True, start_md=None, end_md="07-06", prefill_start="08-11", prefill_end="07-06"
        ) is window_settings_valid(True, "08-11", "07-06")


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
