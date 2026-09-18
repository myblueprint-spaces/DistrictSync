"""Plan 0049 S-2a — the copy machine scope changes, and the per-user promise it may not break.

Three jobs, all about what a surface is ALLOWED to say once a district's profile is shared:

1. **The two seasonal-window notes** (``setup.window_scope_note``). Per-user keeps 0046's A9
   limitation; machine scope replaces it with a POSITIVE claim, and a positive claim about "the
   nightly sync running as X" may only be made over a schedule we have confirmed LIVE.
2. **The delivery-password line's four forms** (``setup.delivery_password_line``) — where the
   secret lives, who can read it, and the one arm that may never be rendered from an UNKNOWN
   read-back.
3. **AC1's per-user byte-identity**, over an explicitly ENUMERATED, NAMED set. The enumeration is
   named, not mechanically complete: nothing here discovers the strings this slice touched, so a
   sixth constant added later is a visible test edit rather than a silent gap. It asserts only the
   PER-USER axis — each function's own behaviour is pinned in its own file (``home_status``,
   ``schedule_status``, ``run_history``); what this file adds is the single promise those files
   cannot state between them, that with the switch off every one of them answers exactly as it
   did before the slice.

Everything here is PURE: dataclasses and ``AppConfig`` built in memory, no file I/O, no flet tree.
The rendering seams are pinned in ``tests/test_ui_flet_service_account.py``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import src.ui_flet.home_status as home_status
import src.ui_flet.run_history as run_history
import src.ui_flet.schedule_status as schedule_status_mod
import src.ui_flet.screens.setup as setup_mod
from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet.run_history import RunRow
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.screens.setup import (
    SYNC_WINDOW_FOREIGN_NOTE,
    SYNC_WINDOW_SHARED_NOTE,
    delivery_password_line,
    sync_window_foreign_note,
    window_scope_note,
)
from src.ui_flet.verdict import Verdict

_FOREIGN = "CONTOSO\\svc_districtsync"
_OWNER = "PC\\ted"

#: Every tri-state the read-back can hand a note, plus "not probed yet". Named once so the two
#: surfaces that must stay silent on three of them cannot be tested over a shorter list.
_ALL_STATES: tuple[ScheduleState | None, ...] = (
    ScheduleState.LIVE,
    ScheduleState.MISSING,
    ScheduleState.UNKNOWN,
    None,
)
_STATE_IDS = ("live", "missing", "unknown", "not-probed")


def _window_cfg(*, enabled: bool = True) -> AppConfig:
    """A config with a real seasonal window configured — the only shape either note fires on."""
    return AppConfig(sync_window_enabled=enabled, sync_window_start="09-01", sync_window_end="06-30")


# --------------------------------------------------------------------------- #
# B — the two seasonal-window notes, and the one entry point that picks        #
# --------------------------------------------------------------------------- #
class TestTheSeasonalWindowNotes:
    """``window_scope_note`` is the ONE entry point, which is what makes "neither sibling can
    render on the other's install" a structural fact rather than a review promise.

    The two notes contradict each other by design — one says the pause will NOT apply, the other
    says it WILL — so a surface that could reach both would be telling a district two opposite
    things about the same summer.
    """

    def test_per_user_is_the_old_note_byte_for_byte(self) -> None:
        """AC1 at its sharpest: the 20 districts in the field must read the same sentence they
        read yesterday. Asserted against ``sync_window_foreign_note``'s own return, so a reworded
        A9 note stays legal while a ROUTING change does not."""
        cfg = _window_cfg()
        routed = window_scope_note(cfg, foreign_account=_FOREIGN, shared_records=False, state=ScheduleState.LIVE)
        assert routed == sync_window_foreign_note(cfg, foreign_account=_FOREIGN)
        assert routed == SYNC_WINDOW_FOREIGN_NOTE.format(account=_FOREIGN)

    @pytest.mark.parametrize("state", _ALL_STATES, ids=_STATE_IDS)
    def test_per_user_ignores_the_schedule_state_entirely(self, state: ScheduleState | None) -> None:
        """The old note makes a NEGATIVE claim ("your pause won't apply"), which is true whether
        or not a task exists — so threading the state must not have narrowed it. Its silence on a
        MISSING task would be a behaviour change dressed as a copy change."""
        cfg = _window_cfg()
        assert window_scope_note(cfg, foreign_account=_FOREIGN, shared_records=False, state=state) == (
            SYNC_WINDOW_FOREIGN_NOTE.format(account=_FOREIGN)
        )

    def test_machine_scope_asserts_the_pause_only_over_a_confirmed_live_task(self) -> None:
        cfg = _window_cfg()
        note = window_scope_note(cfg, foreign_account=_FOREIGN, shared_records=True, state=ScheduleState.LIVE)
        assert note == SYNC_WINDOW_SHARED_NOTE.format(account=_FOREIGN)
        assert _FOREIGN in note

    @pytest.mark.parametrize(
        "state",
        [ScheduleState.MISSING, ScheduleState.UNKNOWN, None],
        ids=["missing", "unknown", "not-probed"],
    )
    def test_machine_scope_is_silent_on_every_unconfirmed_state(self, state: ScheduleState | None) -> None:
        """A confirmed-MISSING schedule OUTRANKS the pause (CLAUDE.md's own rule): a task Windows
        says is gone will not resume in the fall, so "your pause applies to the nightly running as
        X" would name a nightly that does not exist. UNKNOWN was never seen and ``None`` was never
        probed — neither is evidence of anything. The positive twin is the LIVE case above."""
        cfg = _window_cfg()
        assert window_scope_note(cfg, foreign_account=_FOREIGN, shared_records=True, state=state) is None

    @pytest.mark.parametrize("shared", [False, True], ids=["per-user", "machine"])
    def test_a_disabled_window_is_silent_on_both_installs(self, shared: bool) -> None:
        """A limitation nobody configured into is noise, and a reassurance about a pause that
        does not exist is worse. The positive twin is every other test in this class, which uses
        the same call with the window ON."""
        cfg = _window_cfg(enabled=False)
        assert window_scope_note(cfg, foreign_account=_FOREIGN, shared_records=shared, state=ScheduleState.LIVE) is None

    @pytest.mark.parametrize("shared", [False, True], ids=["per-user", "machine"])
    def test_no_foreign_principal_is_silent_on_both_installs(self, shared: bool) -> None:
        """Both notes are ABOUT another account running the nightly. With the signed-in account
        running it there is no second party to name, and the shared note would interpolate an
        empty one."""
        cfg = _window_cfg()
        assert window_scope_note(cfg, foreign_account="", shared_records=shared, state=ScheduleState.LIVE) is None

    @pytest.mark.parametrize("state", _ALL_STATES, ids=_STATE_IDS)
    @pytest.mark.parametrize("enabled", [False, True], ids=["window-off", "window-on"])
    @pytest.mark.parametrize("account", ["", _FOREIGN], ids=["signed-in", "foreign"])
    def test_neither_sibling_can_ever_render_on_the_others_install(
        self, account: str, enabled: bool, state: ScheduleState | None
    ) -> None:
        """The whole reason both route through ONE function. Swept over every input combination:
        whatever comes back, a per-user install can only have produced the limitation and a
        machine-scoped one can only have produced the reassurance."""
        cfg = _window_cfg(enabled=enabled)
        foreign_text = SYNC_WINDOW_FOREIGN_NOTE.format(account=account)
        shared_text = SYNC_WINDOW_SHARED_NOTE.format(account=account)

        per_user = window_scope_note(cfg, foreign_account=account, shared_records=False, state=state)
        assert per_user in (None, foreign_text)
        assert per_user != shared_text

        machine = window_scope_note(cfg, foreign_account=account, shared_records=True, state=state)
        assert machine in (None, shared_text)
        assert machine != foreign_text

    def test_the_shared_note_makes_the_positive_claim_its_sibling_is_banned_from(self) -> None:
        """``TestSyncWindowForeignNote`` bans "we pause"/"automatically" from the OLD note, and
        must keep binding it alone: the limitation states that DistrictSync does NOT handle the
        pause, while this one states that it does. Asserting the opposite of the ban here is what
        stops a future sweep from "fixing" the two into one wording."""
        assert "applies" in SYNC_WINDOW_SHARED_NOTE
        assert "won't apply" not in SYNC_WINDOW_SHARED_NOTE
        assert "shared" in SYNC_WINDOW_SHARED_NOTE
        # It may not send the admin off to remove the schedule — that was the A9 remedy, and
        # there is nothing to remedy here.
        assert "remove the nightly schedule" not in SYNC_WINDOW_SHARED_NOTE
        assert "@" not in SYNC_WINDOW_SHARED_NOTE


# --------------------------------------------------------------------------- #
# C — the delivery-password line's four forms                                  #
# --------------------------------------------------------------------------- #
#: Form 2's shipped wording, transcribed from the pre-slice source rather than imported. An
#: imported constant would move WITH a reword and pin nothing; this is the byte-identity claim
#: AC1 actually makes to the 20 per-user districts.
_SHIPPED_PER_USER_LINE = "Your delivery password is saved and readable by PC\\ted."
_SHIPPED_PER_USER_UNREADABLE_LINE = (
    "Couldn't read the credential back on this account — SFTP uploads may fail. "
    "Try again, or run the app as this account."
)
_NO_SCHEDULE_SENTENCE = "No nightly sync is scheduled right now."


def _line(**over) -> str:
    """The line with everything readable, per-user and account-less unless a case says otherwise."""
    kwargs: dict = {
        "secret_readable": True,
        "machine_scope": False,
        "principal": "",
        "schedule_state": None,
        "keyring_owner": _OWNER,
    }
    kwargs.update(over)
    return delivery_password_line(**kwargs)


class TestTheDeliveryPasswordLineIsPerUserIdentical:
    """Form 2. The one string in this slice that 20 districts will read tonight."""

    @pytest.mark.parametrize("state", _ALL_STATES, ids=_STATE_IDS)
    @pytest.mark.parametrize("principal", ["", _FOREIGN], ids=["signed-in", "foreign"])
    def test_per_user_is_the_shipped_sentence_whatever_else_is_true(self, principal: str, state) -> None:
        """Swept over the two NEW inputs (the record's principal and the read-back state) because
        byte-identity is not "it matches in the happy case" — it is that neither new input can
        reach this branch at all. The keyring owner is named, never the principal (0046 A6)."""
        assert _line(machine_scope=False, principal=principal, schedule_state=state) == _SHIPPED_PER_USER_LINE

    def test_the_per_user_failure_arm_keeps_its_run_as_this_account_advice(self) -> None:
        """Byte-identical too — and the advice is CORRECT here: the secret is in this account's
        Credential Manager, so signing in as the account that owns it really is the fix."""
        assert _line(secret_readable=False, machine_scope=False) == _SHIPPED_PER_USER_UNREADABLE_LINE
        assert "run the app as this account" in _SHIPPED_PER_USER_UNREADABLE_LINE


class TestTheDeliveryPasswordLineUnderSharedRecords:
    """Forms 1, 3 and 4 — what the line may say once the secret lives in the machine store."""

    def test_form_1_names_the_recorded_principal(self) -> None:
        line = _line(machine_scope=True, principal=_FOREIGN, schedule_state=ScheduleState.LIVE)
        assert _FOREIGN in line
        assert "this computer" in line
        # The keyring owner is the WRONG name here: the machine store replaced that keyring, and
        # naming its owner would send the admin to an account that no longer holds anything.
        assert _OWNER not in line

    @pytest.mark.parametrize("principal", ["", "   "], ids=["empty", "whitespace"])
    def test_form_1s_account_less_variant_renders_no_name_at_all(self, principal: str) -> None:
        """D5 explicitly allows scheduling as the SIGNED-IN account on a machine-scoped install,
        where ``schedule_run_as_user`` is ``""`` by contract — so this variant is reachable in
        ordinary use, not a degraded corner. It must not render an empty name, and must not fall
        back to ``_keyring_owner_account()``: that names a keyring the machine store replaced."""
        line = _line(machine_scope=True, principal=principal, schedule_state=ScheduleState.LIVE)
        assert line == setup_mod._DELIVERY_LINE_SHARED_UNNAMED
        assert _OWNER not in line
        assert "  " not in line  # the gap an interpolated blank name would leave

    def test_the_positive_twin_a_named_principal_really_does_reach_the_named_form(self) -> None:
        """Without this, "the account-less variant renders no name" would pass on a function that
        never names anyone."""
        named = _line(machine_scope=True, principal=_FOREIGN, schedule_state=ScheduleState.LIVE)
        assert named != setup_mod._DELIVERY_LINE_SHARED_UNNAMED
        assert named.count(_FOREIGN) == 1

    def test_form_3_adds_the_no_schedule_sentence_on_a_confirmed_missing_task(self) -> None:
        """MISSING outranks the recorded principal: that record names a task Windows has just
        told us does not exist, so naming it would describe a nightly that is gone."""
        line = _line(machine_scope=True, principal=_FOREIGN, schedule_state=ScheduleState.MISSING)
        assert _NO_SCHEDULE_SENTENCE in line
        assert _FOREIGN not in line

    @pytest.mark.parametrize(
        "state",
        [ScheduleState.LIVE, ScheduleState.UNKNOWN, None],
        ids=["live", "unknown", "not-probed"],
    )
    def test_an_unread_schedule_is_never_rendered_as_an_absence(self, state) -> None:
        """MISSING ONLY. An UNKNOWN read-back is a probe timeout, an access denial, or a task
        registered elevated and unreadable by a filtered token — every one of which is a task that
        probably exists. Telling the admin nothing is scheduled would send them to re-register a
        live nightly. ``None`` has not even been asked. The positive twin is the MISSING case
        above, which proves the sentence can be produced at all."""
        assert _NO_SCHEDULE_SENTENCE not in _line(machine_scope=True, principal=_FOREIGN, schedule_state=state)

    @pytest.mark.parametrize("state", _ALL_STATES, ids=_STATE_IDS)
    def test_form_4_warns_that_delivery_will_not_run_and_drops_the_per_user_advice(self, state) -> None:
        """The unreadable arm OUTRANKS every other input — there is no point naming who can read
        a secret we have just failed to read. And "run the app as this account" is wrong advice
        once the secret lives in the machine store: no account can sign in and fix it that way."""
        line = _line(secret_readable=False, machine_scope=True, principal=_FOREIGN, schedule_state=state)
        assert line == setup_mod._DELIVERY_LINE_UNREADABLE_SHARED
        assert "run the app as this account" not in line
        assert "won't run" in line
        assert _FOREIGN not in line
        assert _NO_SCHEDULE_SENTENCE not in line

    def test_the_positive_twin_the_same_inputs_readable_do_name_the_account(self) -> None:
        """Proves the warning arm is a BRANCH, not the function's only answer."""
        assert _FOREIGN in _line(
            secret_readable=True, machine_scope=True, principal=_FOREIGN, schedule_state=ScheduleState.LIVE
        )

    def test_no_form_carries_an_address(self) -> None:
        """``scripts/check_no_emails.py`` scans every tracked file, and the Help page is where the
        support address lives — a sentence painted under a Save button is not."""
        for state in _ALL_STATES:
            for readable in (True, False):
                for machine in (True, False):
                    for principal in ("", _FOREIGN):
                        assert "@" not in _line(
                            secret_readable=readable,
                            machine_scope=machine,
                            principal=principal,
                            schedule_state=state,
                        )


# --------------------------------------------------------------------------- #
# D — AC1: the enumerated per-user byte-identity set                           #
# --------------------------------------------------------------------------- #
#: The strings this slice touched, each transcribed from the pre-slice source. Transcribed, not
#: imported: an imported constant moves WITH a reword and pins nothing. NAMED and explicit — this
#: is not a discovery sweep, so a sixth constant added later is a visible test edit.
_SHIPPED_STRINGS: tuple[tuple[str, str, str], ...] = (
    (
        "setup._SERVICE_ACCOUNT_DELIVERY_NOTE",
        setup_mod._SERVICE_ACCOUNT_DELIVERY_NOTE,
        "Delivery passwords are stored per Windows account, so this account needs its own copy. Sign "
        "in as it once (or use Windows' Run as different user) and run DistrictSync with "
        "--sftp-configure. Your DistrictSync setup guide has the full steps; the Help page has our "
        "support contact. DistrictSync can't do this for you, and the nightly delivery will fail "
        "until it's done.",
    ),
    (
        "setup.SYNC_WINDOW_FOREIGN_NOTE",
        SYNC_WINDOW_FOREIGN_NOTE,
        "Your summer pause won't apply while the nightly sync runs as {account}. The pause is stored "
        "with your own Windows account, and the sync reads the settings of the account it runs as — so "
        "it will keep running through the break. To pause it, remove the nightly schedule for the summer.",
    ),
    (
        "setup._DELIVERY_LINE_PER_USER",
        setup_mod._DELIVERY_LINE_PER_USER,
        "Your delivery password is saved and readable by {owner}.",
    ),
    (
        "setup._DELIVERY_LINE_UNREADABLE_PER_USER",
        setup_mod._DELIVERY_LINE_UNREADABLE_PER_USER,
        _SHIPPED_PER_USER_UNREADABLE_LINE,
    ),
    (
        "schedule_status.FOREIGN_RECORDS_NOTE",
        schedule_status_mod.FOREIGN_RECORDS_NOTE,
        "Your nightly sync runs as {account}, so its run records are saved under that account "
        "and don't appear in Run History here.",
    ),
)

_NOW = datetime(2026, 7, 15, 12, 0)
_LONG_AGO = "2026-06-01T02:00:00"
#: The newest row in the local ledger, OLDER than the task's own ``last_run`` below — the
#: fired-but-no-record gap itself. Without it ``_is_contradiction`` short-circuits on "no record
#: to compare" and would answer ``False`` on both sides of the switch, which is exactly the
#: vacuous green ``test_the_enumeration_is_not_vacuous`` exists to catch.
_NEWEST_RECORD_TS = "2026-07-10T03:00:00"


def _status(*, state: ScheduleState = ScheduleState.LIVE, shared_records: bool) -> ScheduleStatus:
    """A foreign-principal status — the ONLY shape any of the four suppressions depends on."""
    return ScheduleStatus(
        state=state,
        headline="",
        detail="",
        expected=True,
        foreign_account=_FOREIGN,
        shared_records=shared_records,
    )


def _fired_but_no_record() -> ScheduleReadback:
    """A task that ran and left no row — the record gap A5 excuses under a foreign principal."""
    return ScheduleReadback(found=True, next_run="2026-07-16T03:00:00", last_run="2026-07-15T03:00:00", last_result=0)


def _row(run_as: str) -> RunRow:
    """A minimal past-run row; only ``run_as`` matters to the summary line."""
    return RunRow(when="recently", status_label="Completed", status_verdict=Verdict.HEALTHY, run_as=run_as)


def _predicate_answers(*, shared_records: bool) -> dict[str, object]:
    """Every predicate this slice threaded ``shared_records`` through, answered under ONE install.

    Named and enumerated for the same reason the strings above are. Each function's full truth
    table lives in its own file; what is asserted here is only that the per-user column of it is
    the pre-slice column.
    """
    window = AppConfig(sync_window_enabled=True, sync_window_start="09-01", sync_window_end="06-30")
    return {
        "schedule_status._is_contradiction": schedule_status_mod._is_contradiction(
            _fired_but_no_record(),
            _NEWEST_RECORD_TS,
            foreign_account=_FOREIGN,
            shared_records=shared_records,
        ),
        "home_status._is_missed_run": home_status._is_missed_run(
            [],
            now=_NOW,
            store_created_at=_LONG_AGO,
            schedule_status=_status(shared_records=shared_records),
        ),
        "home_status._foreign_records_elsewhere": home_status._foreign_records_elsewhere(
            [],
            now=_NOW,
            schedule_status=_status(shared_records=shared_records),
        ),
        "home_status.sync_window_paused": home_status.sync_window_paused(
            window,
            now=_NOW,
            foreign_account=_FOREIGN,
            shared_records=shared_records,
        ),
        "home_status.machine_scope_line": home_status.machine_scope_line(
            machine_scope=shared_records,
            provisioned_by="CONTOSO\\admin",
            provisioned_at="2026-09-18T09:00:00",
        ),
        "run_history.run_as_summary_line": run_history.run_as_summary_line(
            [_row(run_history.RUN_AS_THIS_ACCOUNT)],
            machine_scope=shared_records,
        ),
    }


#: What each predicate answered BEFORE this slice, under a foreign principal. Written out rather
#: than computed, so a flipped default shows up as a failing row with a name on it.
_PRE_SLICE_ANSWERS: dict[str, object] = {
    "schedule_status._is_contradiction": False,  # A5 suppressed the record-gap alarm
    "home_status._is_missed_run": False,  # …and the missed-run alarm
    "home_status._foreign_records_elsewhere": True,  # …and replaced them with "records are elsewhere"
    "home_status.sync_window_paused": False,  # …and the pause, which was genuinely not in force
    "home_status.machine_scope_line": None,  # no scope line existed, and none may appear
    "run_history.run_as_summary_line": None,  # no run-as summary existed either
}


class TestPerUserByteIdentity:
    """AC1. On a per-user install every string and predicate this slice touches is unchanged.

    The enumeration is NAMED, not mechanically complete — nothing here discovers what the slice
    touched, so this class is a floor against regression in the set we listed, not proof that the
    set is the whole slice. Both halves matter: a reworded string is a district reading something
    new tonight, and a flipped predicate is an alarm appearing (or vanishing) on 20 installs that
    were never provisioned.
    """

    @pytest.mark.parametrize(
        ("name", "actual", "shipped"),
        _SHIPPED_STRINGS,
        ids=[name for name, _actual, _shipped in _SHIPPED_STRINGS],
    )
    def test_the_shipped_string_is_unchanged(self, name: str, actual: str, shipped: str) -> None:
        assert actual == shipped, f"{name} changed — 20 per-user districts read this tonight"

    @pytest.mark.parametrize("name", sorted(_PRE_SLICE_ANSWERS), ids=sorted(_PRE_SLICE_ANSWERS))
    def test_the_predicate_answers_exactly_as_it_did_before_the_slice(self, name: str) -> None:
        assert _predicate_answers(shared_records=False)[name] == _PRE_SLICE_ANSWERS[name]

    def test_the_enumeration_is_not_vacuous(self) -> None:
        """The positive twin for the whole class: flipping the switch must move EVERY predicate in
        the set. A row that answered the same either way would be a call site that silently
        dropped ``shared_records`` — and the per-user assertion above would still be green."""
        shared = _predicate_answers(shared_records=True)
        for name, before in _PRE_SLICE_ANSWERS.items():
            assert shared[name] != before, f"{name} ignores shared_records — its per-user pin proves nothing"

    def test_the_two_new_surfaces_say_nothing_at_all_per_user(self) -> None:
        """Stated separately from the table because ``None`` is not a degraded answer here: it is
        the ONLY correct one on every install in the field today. "Settings for your account only"
        answers a question no district asked, and lands on the surface S7 stripped to the verdict."""
        assert (
            home_status.machine_scope_line(
                machine_scope=False, provisioned_by="CONTOSO\\admin", provisioned_at="2026-09-18T09:00:00"
            )
            is None
        )
        rows = [_row(run_history.RUN_AS_THIS_ACCOUNT), _row(run_history.RUN_AS_ANOTHER_ACCOUNT)]
        # Even a ledger that DOES carry two accounts stays silent per-user: the summary line is a
        # machine-scope surface, not a mixed-values one.
        assert run_history.run_as_summary_line(rows, machine_scope=False) is None
