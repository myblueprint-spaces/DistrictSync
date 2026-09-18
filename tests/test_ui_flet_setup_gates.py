"""Tests for src/ui_flet/setup_gates.py — the pure Setup submit-gate predicates.

These are the single source the disabled-button state AND the Enter-to-submit
(`on_submit`) handlers both read, so Enter can never bypass a gate a disabled
button enforces (Slice 2, D-chrome / Problem #9).
"""

from __future__ import annotations

import inspect

import pytest

from src.scheduler import windows
from src.ui_flet import setup_gates
from src.ui_flet.setup_gates import (
    RegisterBlock,
    ScheduleAccountFacts,
    can_save_sftp,
    principal_key,
    window_settings_valid,
    window_valid_from_config,
)


def register_block(
    config_complete: bool,
    run_time: str,
    *,
    account: ScheduleAccountFacts,
    delivery_secret_unreadable: bool = False,
) -> RegisterBlock:
    """The ONE call shape this file uses — the next required keyword is one edit here.

    ``delivery_secret_unreadable`` has NO default in production (a permissive default on the
    parameter that decides whether a computer is provisioned with a delivery credential it
    cannot read is the banned shape); a default HERE is what keeps the existing truth tables
    from each carrying the keyword. The production signature's requiredness is asserted by
    :func:`test_the_new_keywords_are_required_in_production`, so this default cannot hide it.
    """
    return setup_gates.register_block(
        config_complete,
        run_time,
        account=account,
        delivery_secret_unreadable=delivery_secret_unreadable,
    )


def can_register_schedule(
    config_complete: bool,
    run_time: str,
    *,
    account: ScheduleAccountFacts,
    delivery_secret_unreadable: bool = False,
) -> bool:
    """The bool form, through the same one call shape (see :func:`register_block`)."""
    return setup_gates.can_register_schedule(
        config_complete,
        run_time,
        account=account,
        delivery_secret_unreadable=delivery_secret_unreadable,
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
            setup_gates.can_register_schedule(True, "03:00")  # type: ignore[call-arg]


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
            # 0049 S-2b.1: a foreign principal whose delivery secret we cannot read. This
            # row is the point of the sweep — the set equality goes red the moment a member
            # exists with nothing producing it.
            register_block(
                True,
                "03:00",
                account=_prefill(typed="CORP\\svc_x", password_supplied=True),
                delivery_secret_unreadable=True,
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


# ---------------------------------------------------------------------------
# Plan 0049 S-2b.1 — the delivery-secret gate
# ---------------------------------------------------------------------------


class TestDeliverySecretUnreadable:
    """Scheduling as a service account PROVISIONS this computer, and step 5 of that
    provision seals the delivery password into the shared profile. With nothing readable to
    seal, the nightly comes up delivering nothing and says nothing — on a machine-scoped
    install ``sftp_is_configured()`` answers False without a secret, so no upload is even
    attempted. The gate is what turns that silence into a sentence."""

    _BLOCK = RegisterBlock.DELIVERY_SECRET_UNREADABLE

    def test_a_foreign_principal_with_an_unreadable_secret_is_blocked(self):
        facts = _prefill(typed="CORP\\svc_x", password_supplied=True)
        assert register_block(True, "03:00", account=facts, delivery_secret_unreadable=True) is self._BLOCK
        assert can_register_schedule(True, "03:00", account=facts, delivery_secret_unreadable=True) is False

    def test_the_same_facts_with_a_readable_secret_are_open(self):
        """The positive twin: nothing else in this shape is closing the gate."""
        facts = _prefill(typed="CORP\\svc_x", password_supplied=True)
        assert register_block(True, "03:00", account=facts, delivery_secret_unreadable=False) is RegisterBlock.NONE

    @pytest.mark.parametrize("password_supplied", [True, False])
    def test_the_signed_in_account_is_byte_identical_to_today(self, password_supplied):
        """A per-user install is NOT provisioning anything, so an unreadable delivery secret
        is the Delivery section's problem and not this gate's. Both arms, because the
        conjunction must not accidentally key on the password field instead."""
        facts = _prefill(password_supplied=password_supplied)
        assert register_block(True, "03:00", account=facts, delivery_secret_unreadable=True) is register_block(
            True, "03:00", account=facts, delivery_secret_unreadable=False
        )

    @pytest.mark.parametrize("typed", ["", "   ", _CURRENT, "pc\\TED", "  PC\\ted  "])
    def test_every_spelling_of_the_signed_in_account_is_exempt(self, typed):
        """Foreign-ness comes from ``principal_key`` — the ONE reduction — so a re-cased or
        whitespace-padded prefill can never be mistaken for a provisioning register."""
        facts = _prefill(typed=typed)
        assert register_block(True, "03:00", account=facts, delivery_secret_unreadable=True) is RegisterBlock.NONE

    def test_it_outranks_the_password_rung(self):
        """Check order: after the switch refusal, BEFORE the password. The cheapest rung
        (a field that is simply empty) stays last."""
        facts = _prefill(typed="CORP\\svc_x", password_supplied=False)
        assert register_block(True, "03:00", account=facts, delivery_secret_unreadable=True) is self._BLOCK
        # The twin, so the row above cannot pass for the wrong reason.
        assert (
            register_block(True, "03:00", account=facts, delivery_secret_unreadable=False)
            is RegisterBlock.ACCOUNT_NEEDS_PASSWORD
        )

    def test_the_switch_refusal_still_comes_first(self):
        """A valid, NON-SWITCHING principal has to be established before a delivery fact is
        worth raising — the admin is routed to Remove → Schedule either way."""
        facts = _prefill(typed="CORP\\svc_b", recorded="CORP\\svc_a", password_supplied=True, schedule_registered=True)
        assert (
            register_block(True, "03:00", account=facts, delivery_secret_unreadable=True)
            is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE
        )

    @pytest.mark.parametrize(
        ("complete", "run_time", "expected"),
        [(False, "03:00", RegisterBlock.INCOMPLETE), (True, "", RegisterBlock.RUN_TIME)],
    )
    def test_the_two_cheap_conditions_still_lead(self, complete, run_time, expected):
        facts = _prefill(typed="CORP\\svc_x", password_supplied=True)
        assert register_block(complete, run_time, account=facts, delivery_secret_unreadable=True) is expected

    def test_a_shape_refusal_still_leads(self):
        facts = _prefill(typed="CORP\\svc account", password_supplied=True)
        assert (
            register_block(True, "03:00", account=facts, delivery_secret_unreadable=True) is RegisterBlock.ACCOUNT_SHAPE
        )


def test_the_new_keywords_are_required_in_production():
    """``delivery_secret_unreadable`` is keyword-only and UNDEFAULTED on BOTH functions.

    This plan exists because one defaulted parameter substituted a security principal; a
    defaulted ``False`` here would let a forgotten call site provision a computer with a
    delivery credential it cannot read. The helpers at the top of this file default it for
    brevity, which is exactly why the production signature is asserted rather than assumed.
    """
    for func in (setup_gates.register_block, setup_gates.can_register_schedule):
        parameter = inspect.signature(func).parameters["delivery_secret_unreadable"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, func.__name__
        assert parameter.default is inspect.Parameter.empty, func.__name__
        with pytest.raises(TypeError):
            func(True, "03:00", account=_prefill())  # type: ignore[call-arg]


def test_declaration_order_is_not_evaluation_order():
    """A pin on the comment in ``RegisterBlock``'s docstring, so nobody "fixes" one to match
    the other: the checks run switch-then-password, the members are declared the other way
    round, and reordering the CHECKS changes which cause an admin reads first."""
    members = [member.name for member in RegisterBlock]
    assert members.index("ACCOUNT_NEEDS_PASSWORD") < members.index("ACCOUNT_SWITCH_NEEDS_REMOVE")
    switching = _prefill(typed="CORP\\svc_x", password_supplied=False, recorded="", schedule_registered=True)
    assert register_block(True, "03:00", account=switching) is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE


def test_the_engine_refusal_is_still_the_structural_floor():
    """ACCOUNT_NEEDS_PASSWORD MIRRORS an engine refusal that must keep existing: the gate makes
    it legible BEFORE a UAC prompt, it never replaces the floor that closes all three
    blank-password paths."""
    assert isinstance(windows._MSG_ACCOUNT_NEEDS_PASSWORD, str)
    assert windows._MSG_ACCOUNT_NEEDS_PASSWORD
