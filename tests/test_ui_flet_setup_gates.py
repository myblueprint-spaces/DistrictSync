"""Tests for src/ui_flet/setup_gates.py — the pure Setup submit-gate predicates.

These are the single source the disabled-button state AND the Enter-to-submit
(`on_submit`) handlers both read, so Enter can never bypass a gate a disabled
button enforces (Slice 2, D-chrome / Problem #9).
"""

from __future__ import annotations

import inspect

import pytest

from src.scheduler import windows
from src.scheduler.task_com import PrincipalKind
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
        # 0049 S-4: PASSWORD is the G5 baseline kind — an admin who typed a foreign account
        # and its password. Overridable per row, which is how the gMSA rows are written.
        "kind": PrincipalKind.PASSWORD,
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


# ---------------------------------------------------------------------------
# Plan 0049 S-4 — the gMSA kind at BOTH rungs that can refuse an account
# ---------------------------------------------------------------------------
_GMSA = "CORP\\svc_districtsync$"
_MSA_KIND = PrincipalKind.MANAGED_SERVICE_ACCOUNT


def _block(**over: object) -> RegisterBlock:
    """``register_block`` over ``_prefill``, with the two undefaulted keywords supplied."""
    return register_block(
        True,
        "03:00",
        account=_prefill(**over),
        delivery_secret_unreadable=False,
    )


class TestTheGmsaPassesBothRungs:
    """D6's ``ACCOUNT_NEEDS_PASSWORD`` bullet was incomplete, and this class is why.

    The SHAPE rung runs FIRST and unconditionally. Before S-4 it called
    ``validate_run_as_user``, which rejects a trailing ``$`` — so every gMSA was refused
    there, for the wrong reason, and the password rung was never reached at all. Both rungs
    now dispatch on the declared kind, through ``task_com.validate_principal_account``.
    """

    def test_a_gmsa_with_no_password_opens_the_gate(self) -> None:
        assert _block(typed=_GMSA, kind=_MSA_KIND, password_supplied=False) is RegisterBlock.NONE

    def test_the_same_name_declared_as_a_password_logon_is_refused_on_SHAPE(self) -> None:
        """The positive twin that proves the DISPATCH is what admits it, not a widened charset.
        ``SVC$`` as a password logon is a caller mixing up two credential stories, and the
        validator for that kind still refuses the ``$``."""
        assert _block(typed=_GMSA, kind=PrincipalKind.PASSWORD, password_supplied=True) is RegisterBlock.ACCOUNT_SHAPE

    def test_the_password_rung_never_fires_for_a_managed_service_account(self) -> None:
        # The directory holds the credential; there is nothing to supply, so a rung that asks
        # for one would close the gate on a complete request.
        assert _block(typed=_GMSA, kind=_MSA_KIND, password_supplied=False) is not RegisterBlock.ACCOUNT_NEEDS_PASSWORD

    def test_its_positive_twin_still_fires_for_a_password_logon(self) -> None:
        # The SAME facts minus the ``$`` and with the password kind: the rung is alive.
        assert (
            _block(typed="CORP\\svc_districtsync", kind=PrincipalKind.PASSWORD, password_supplied=False)
            is RegisterBlock.ACCOUNT_NEEDS_PASSWORD
        )

    def test_an_unsuffixed_name_declared_as_a_managed_service_account_is_refused(self) -> None:
        # The dispatch cannot launder one kind's name into the other's — in EITHER direction.
        assert (
            _block(typed="CORP\\svc_districtsync", kind=_MSA_KIND, password_supplied=False)
            is RegisterBlock.ACCOUNT_SHAPE
        )

    @pytest.mark.parametrize("hostile", ["NT AUTHORITY\\SYSTEM$", "BUILTIN\\Administrators$", "  $", "a b$"])
    def test_a_hostile_or_malformed_gmsa_name_is_still_refused(self, hostile: str) -> None:
        """``validate_gmsa_account`` refuses a built-in authority BY NAME as well as by
        charset, and this gate inherits that rather than restating it."""
        assert _block(typed=hostile, kind=_MSA_KIND, password_supplied=False) is RegisterBlock.ACCOUNT_SHAPE

    @pytest.mark.parametrize(
        # NOT ``list(PrincipalKind)``. G5 is about a machine-derived name that legitimately
        # contains a space, and it applies to the two kinds the signed-in account can
        # actually BE. An MSA is validated even when the name is not foreign — see
        # ``TestAnMsaIsValidatedEvenWhenTheNameIsNotFOREIGN`` for the field defect that
        # this parametrization hid by asserting the buggy behaviour.
        "kind",
        [PrincipalKind.INTERACTIVE_TOKEN, PrincipalKind.PASSWORD],
    )
    def test_the_untouched_prefill_is_byte_identical_for_every_kind(self, kind: PrincipalKind) -> None:
        """G5, restated for S-4: the 20 shipped districts never touch the field, so the
        machine-derived name — which legitimately contains a space — must never be validated
        for either kind the signed-in account can be.

        The original form of this test swept ``list(PrincipalKind)`` and asserted ``NONE``
        for the MSA kind too. That was wrong, and it is why the defect shipped: skipping the
        rung did not let the prefill through, it moved the refusal into the worker, where it
        reads as "Windows wouldn't accept that account name … try again".
        """
        assert _block(kind=kind) is RegisterBlock.NONE

    def test_the_switch_refusal_still_outranks_the_kind(self) -> None:
        """Ordering is unchanged: a live task on another principal is refused before any
        credential question, gMSA included."""
        assert (
            _block(typed=_GMSA, kind=_MSA_KIND, recorded="", schedule_registered=True)
            is RegisterBlock.ACCOUNT_SWITCH_NEEDS_REMOVE
        )

    def test_the_delivery_secret_rung_still_outranks_the_kind(self) -> None:
        assert (
            register_block(
                True,
                "03:00",
                account=_prefill(typed=_GMSA, kind=_MSA_KIND, password_supplied=False),
                delivery_secret_unreadable=True,
            )
            is RegisterBlock.DELIVERY_SECRET_UNREADABLE
        )


class TestTheKindIsARequiredFact:
    def test_schedule_account_facts_cannot_be_built_without_it(self) -> None:
        # Every field here is required and undefaulted, and this one decides which validator a
        # typed name goes through — the two charsets are disjoint on exactly one character.
        with pytest.raises(TypeError):
            ScheduleAccountFacts(  # type: ignore[call-arg]
                typed=_CURRENT, current=_CURRENT, password_supplied=False, recorded="", schedule_registered=False
            )


class TestAnMsaIsValidatedEvenWhenTheNameIsNotFOREIGN:
    """The 2026-09-21 field defect, found on the owner's first real attempt at a gMSA.

    Every gMSA row above types a FOREIGN name, which is why they all passed while the product
    was broken. The shape rung is scoped to a foreign account by 0046's G5 rule so that a
    legitimate ``PC\\John Smith`` prefill keeps registering — and that scoping is WRONG for a
    managed service account, because the signed-in account can never BE one and **the field
    arrives prefilled with it**. Ticking the disclosure and pressing Schedule therefore sailed
    past this rung (``principal_key`` of the current account is ``""``), reached
    ``Principal.__post_init__`` in the worker, and surfaced as the generic sentence "Windows
    wouldn't accept that account name … try again" — which blames Windows for OUR refusal and
    invites a retry that cannot work.
    """

    def test_the_prefilled_signed_in_account_is_refused_under_the_msa_kind(self) -> None:
        """The exact reproduction: the tick box on, the prefill untouched."""
        assert _block(typed=_CURRENT, kind=_MSA_KIND, password_supplied=False) is RegisterBlock.ACCOUNT_SHAPE

    def test_a_blank_field_is_refused_under_the_msa_kind(self) -> None:
        """Blank means "the signed-in account" everywhere else in this gate. There is no such
        thing as a signed-in gMSA, so under this kind blank is a shape failure, not a default."""
        assert _block(typed="", kind=_MSA_KIND, password_supplied=False) is RegisterBlock.ACCOUNT_SHAPE

    def test_the_g5_prefill_rule_is_untouched_for_the_other_two_kinds(self) -> None:
        """The non-vacuous half. G5 exists so a machine-derived name with a space still
        registers; this fix narrows the MSA kind ONLY, and both shipped kinds answer exactly as
        they did before."""
        assert (
            _block(typed=_CURRENT, kind=PrincipalKind.INTERACTIVE_TOKEN, password_supplied=False) is RegisterBlock.NONE
        )
        assert _block(typed=_CURRENT, kind=PrincipalKind.PASSWORD, password_supplied=True) is RegisterBlock.NONE

    def test_a_valid_gmsa_name_still_opens_the_gate(self) -> None:
        """So the fix refuses the wrong SHAPE, never the kind."""
        assert _block(typed=_GMSA, kind=_MSA_KIND, password_supplied=False) is RegisterBlock.NONE
