"""Tests for src/ui_flet/setup_gates.py — the pure Setup submit-gate predicates.

These are the single source the disabled-button state AND the Enter-to-submit
(`on_submit`) handlers both read, so Enter can never bypass a gate a disabled
button enforces (Slice 2, D-chrome / Problem #9).
"""

from __future__ import annotations

import pytest

from src.scheduler import windows
from src.ui_flet.setup_gates import (
    RegisterBlock,
    ScheduleAccountFacts,
    can_register_schedule,
    can_save_sftp,
    principal_key,
    register_block,
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
        assert can_register_schedule(config_complete, run_time, account=_prefill()) is expected

    def test_none_run_time_is_closed(self):
        # Defensive: a None value (uninitialised TextField) must not raise.
        assert can_register_schedule(True, None, account=_prefill()) is False  # type: ignore[arg-type]

    def test_the_account_facts_are_required(self):
        # No permissive default on a safety-relevant parameter (CLAUDE.md): a forgotten call
        # site must not silently skip the principal gate.
        with pytest.raises(TypeError):
            can_register_schedule(True, "03:00")  # type: ignore[call-arg]


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


# ---------------------------------------------------------------------------
# Plan 0046 B — the service-account principal gate
# ---------------------------------------------------------------------------

_CURRENT = "PC\\ted"


def _prefill(**over: object) -> ScheduleAccountFacts:
    """The UNTOUCHED-PREFILL facts — today's world, and the G5 baseline."""
    kwargs: dict[str, object] = {
        "typed": _CURRENT,
        "current": _CURRENT,
        "password_supplied": False,
        "recorded": "",
        "schedule_registered": False,
    }
    kwargs.update(over)
    return ScheduleAccountFacts(**kwargs)  # type: ignore[arg-type]


class TestPrincipalKey:
    """The ONE reduction every principal comparison in the app goes through."""

    @pytest.mark.parametrize(
        ("account", "current", "expected"),
        [
            ("", _CURRENT, ""),  # blank == the signed-in account
            (None, _CURRENT, ""),  # defensive: an uninitialised field
            ("   ", _CURRENT, ""),  # whitespace-only
            (_CURRENT, _CURRENT, ""),  # the prefill, untouched
            ("pc\\TED", _CURRENT, ""),  # case-insensitive — NOT a principal change
            ("  PC\\ted  ", _CURRENT, ""),  # surrounding whitespace
            ("CORP\\svc_x", _CURRENT, "corp\\svc_x"),  # foreign → the case-folded name
            ("svc_x", _CURRENT, "svc_x"),
            ("CORP\\svc_x", "", "corp\\svc_x"),  # no current account known → still foreign
        ],
    )
    def test_reduction(self, account, current, expected):
        assert principal_key(account, current) == expected

    @pytest.mark.parametrize(
        "spelling",
        ["svc_x", "SVC_X", "  svc_x  ", "CORP\\svc_x", "corp\\SVC_X", "", "   ", _CURRENT, "pc\\TED"],
    )
    def test_parity_with_the_engine_refusal(self, spelling):
        """Two-implementation parity over one table: ``principal_key(...) != ""`` must equal
        the verdict ``register_task`` itself applies (``requested.casefold() !=
        current.casefold()`` over the stripped value), restated here from the engine's own
        shape. The engine's refusal stays the structural floor either way — but a drifted view
        reduction would gate on the wrong thing."""
        requested = (spelling or "").strip()
        engine_says_foreign = bool(requested) and requested.casefold() != _CURRENT.casefold()
        assert (principal_key(spelling, _CURRENT) != "") is engine_says_foreign


class TestRegisterBlock:
    def test_the_untouched_prefill_is_open(self):
        assert register_block(True, "03:00", account=_prefill()) is RegisterBlock.NONE
        assert can_register_schedule(True, "03:00", account=_prefill()) is True

    def test_a_spaced_local_prefill_stays_open(self):
        """G5: ``current_run_as_user()`` legitimately returns ``PC\\John Smith``, which
        ``_RUN_AS_USER_RE`` rejects. Those districts register logged-on-only fine today, so
        shape is checked ONLY for a foreign principal."""
        spaced = "PC\\John Smith"
        facts = _prefill(typed=spaced, current=spaced)
        assert register_block(True, "03:00", account=facts) is RegisterBlock.NONE

    def test_a_spaced_foreign_account_is_shape_blocked(self):
        facts = _prefill(typed="CORP\\svc account", password_supplied=True)
        assert register_block(True, "03:00", account=facts) is RegisterBlock.ACCOUNT_SHAPE

    def test_a_foreign_account_without_a_password_needs_one(self):
        facts = _prefill(typed="CORP\\svc_x", password_supplied=False)
        assert register_block(True, "03:00", account=facts) is RegisterBlock.ACCOUNT_NEEDS_PASSWORD

    def test_a_foreign_account_with_a_password_is_open(self):
        facts = _prefill(typed="CORP\\svc_x", password_supplied=True)
        assert register_block(True, "03:00", account=facts) is RegisterBlock.NONE

    @pytest.mark.parametrize(
        ("complete", "run_time", "expected"),
        [
            (False, "03:00", RegisterBlock.INCOMPLETE),
            (True, "", RegisterBlock.RUN_TIME),
            (True, "   ", RegisterBlock.RUN_TIME),
        ],
    )
    def test_the_pre_existing_conditions_keep_their_own_reasons(self, complete, run_time, expected):
        assert register_block(complete, run_time, account=_prefill()) is expected

    def test_ordering_is_config_then_run_time_then_principal(self):
        """An incomplete config with a malformed account reports INCOMPLETE — the admin is
        told the first thing that is wrong, not the last."""
        facts = _prefill(typed="CORP\\svc account", password_supplied=True)
        assert register_block(False, "", account=facts) is RegisterBlock.INCOMPLETE
        assert register_block(True, "", account=facts) is RegisterBlock.RUN_TIME

    def test_every_member_is_reachable(self):
        produced = {
            register_block(False, "03:00", account=_prefill()),
            register_block(True, "", account=_prefill()),
            register_block(True, "03:00", account=_prefill()),
            register_block(True, "03:00", account=_prefill(typed="CORP\\svc account", password_supplied=True)),
            register_block(True, "03:00", account=_prefill(typed="CORP\\svc_x")),
            register_block(
                True,
                "03:00",
                account=_prefill(typed="CORP\\svc_x", password_supplied=True, schedule_registered=True),
            ),
        }
        assert produced == set(RegisterBlock)


class TestAccountSwitchNeedsRemove:
    """Owner decision 2026-09-16: the app REFUSES an in-place principal switch and routes the
    admin to Remove → Schedule. The gate keys on "not PROVABLY the same principal"."""

    _SWITCH = RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE

    @pytest.mark.parametrize(
        ("recorded", "typed"),
        [
            ("", "CORP\\svc_x"),  # (a) signed-in → service account
            ("CORP\\svc_x", ""),  # (b) service account → signed-in
            ("CORP\\svc_a", "CORP\\svc_b"),  # (c) one service account → another
            (None, "CORP\\svc_x"),  # (d) NO record — we cannot prove it is already that account
        ],
    )
    def test_a_switch_on_a_live_task_is_refused(self, recorded, typed):
        facts = _prefill(typed=typed, recorded=recorded, password_supplied=True, schedule_registered=True)
        assert register_block(True, "03:00", account=facts) is self._SWITCH

    @pytest.mark.parametrize(
        ("recorded", "typed", "password"),
        [
            ("", "", False),  # (e) today's world
            ("", "  pc\\TED  ", False),  # (f) the prefill in any casing/whitespace
            ("CORP\\svc_x", "corp\\SVC_X", True),  # (g) re-registering the SAME service account
            (None, "", False),  # (h) the recordless ordinary re-register today's installs need
        ],
    )
    def test_a_non_switch_on_a_live_task_is_not_refused(self, recorded, typed, password):
        facts = _prefill(typed=typed, recorded=recorded, password_supplied=password, schedule_registered=True)
        assert register_block(True, "03:00", account=facts) is not self._SWITCH

    @pytest.mark.parametrize("recorded", ["", "CORP\\svc_a", None])
    def test_no_live_task_is_never_a_switch(self, recorded):
        """(i) With nothing registered there is no task to re-point."""
        facts = _prefill(typed="CORP\\svc_b", recorded=recorded, password_supplied=True, schedule_registered=False)
        assert register_block(True, "03:00", account=facts) is not self._SWITCH


def test_the_engine_refusal_is_still_the_structural_floor():
    """ACCOUNT_NEEDS_PASSWORD MIRRORS an engine refusal that must keep existing: the gate makes
    it legible BEFORE a UAC prompt, it never replaces the floor that closes all three
    blank-password paths."""
    assert isinstance(windows._MSG_ACCOUNT_NEEDS_PASSWORD, str)
    assert windows._MSG_ACCOUNT_NEEDS_PASSWORD
