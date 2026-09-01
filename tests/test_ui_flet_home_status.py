"""Unit tests for the pure ``home_status`` derivation (IA-3a, COUNTED — the trust core).

Every rule + first-match precedence exercised on SYNTHETIC records (no filesystem);
``is_stale`` boundaries; the ``None``/``[]`` degradation sentinels; partial-record
totality (missing keys → no ``KeyError``); and the load-bearing PRIVACY invariant — a
record whose free-text ``error`` carries a filesystem path must never leak that path into
the admin-facing ``headline``/``detail``. A parametrized sweep asserts every fixture
returns a valid ``HomeStatus`` with no exception.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet import home_status as home_status_mod
from src.ui_flet.home_status import (
    MISSED_RUN_AFTER_HOURS,
    STALE_AFTER_HOURS,
    WELCOME_FRESH,
    WELCOME_RESUME_PLAIN,
    WELCOME_RESUME_SETTINGS_ONLY,
    WELCOME_RESUME_WITH_HISTORY,
    HomeStatus,
    LatestReason,
    classify_latest_reason,
    derive_home_status,
    has_prior_runs,
    is_delivery_only,
    is_stale,
    verdict_for_reason,
    welcome_band,
    welcome_band_line,
)
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus, derive_schedule_status
from src.ui_flet.verdict import Verdict


def _live_schedule(next_run_display: str = "3:00 AM") -> ScheduleStatus:
    """A LIVE ScheduleStatus with a known next-run time (the injected read-back)."""
    return ScheduleStatus(
        state=ScheduleState.LIVE,
        headline="Nightly sync is scheduled",
        detail="registered",
        next_run_display=next_run_display,
    )


# A fixed reference "now" so relative timestamps are deterministic.
_NOW = datetime(2026, 7, 4, 8, 0, 0)
_RECENT = (_NOW - timedelta(hours=5)).isoformat(timespec="seconds")  # within the stale window
_OLD = (_NOW - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")  # past it

_CONFIGURED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)


def _record(**overrides: object) -> dict:
    """A clean, recent, delivered-success record; overrides tweak one axis per test."""
    base: dict = {
        "timestamp": _RECENT,
        "status": "success",
        "duration_s": 3.1,
        "Students": 100,
        "Staff": 12,
        "Family": 80,
        "Classes": 40,
        "Enrollments": 300,
        "CourseInfo": 0,
        "StudentCourses": 0,
        "StudentAttendance": 0,
        "sftp_attempted": True,
        "sftp_ok": True,
        "error": "",
        "anomalies": [],
        "data_errors": {},
    }
    base.update(overrides)
    return base


def _derive(record: dict) -> HomeStatus:
    return derive_home_status([record], _CONFIGURED, now=_NOW)


class TestIsStale:
    def test_within_window_not_stale(self) -> None:
        assert is_stale(_RECENT, _NOW) is False

    def test_past_window_is_stale(self) -> None:
        assert is_stale(_OLD, _NOW) is True

    def test_unparseable_ts_is_not_stale(self) -> None:
        # Can't determine → don't cry wolf.
        assert is_stale("not-a-timestamp", _NOW) is False

    def test_empty_ts_is_not_stale(self) -> None:
        assert is_stale("", _NOW) is False

    def test_boundary_exactly_at_window_is_not_stale(self) -> None:
        exactly = (_NOW - timedelta(hours=STALE_AFTER_HOURS)).isoformat(timespec="seconds")
        assert is_stale(exactly, _NOW) is False  # strictly greater-than is stale

    def test_naive_aware_mismatch_is_not_stale(self) -> None:
        # An aware `now` vs a naive parsed `last_ts` → TypeError on subtraction → total (False).
        aware_now = datetime(2026, 7, 4, 8, 0, 0, tzinfo=timezone.utc)
        assert is_stale(_OLD, aware_now) is False  # _OLD is naive → can't determine

    def test_custom_stale_after_hours(self) -> None:
        two_hours_old = (_NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        assert is_stale(two_hours_old, _NOW, stale_after_hours=1) is True
        assert is_stale(two_hours_old, _NOW, stale_after_hours=3) is False


class TestUnavailableSentinel:
    def test_records_none_is_calm_warning_no_raise(self) -> None:
        status = derive_home_status(None, _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Sync status unavailable"
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.metrics is None


class TestEmptyState:
    """0038 S7 part (i) re-based the fresh-start discriminator on ``store_created_at`` ALONE.

    ``has_completed_setup()`` used to be an OR-disjunct, and the wizard flips it the instant
    it saves — so from S6 (Home HOSTS the wizard) a brand-new install landed, at its peak
    moment, on "if you used an earlier version, its run history isn't carried over". These
    rows changed EXPECTATION, not strictness: each pair below asserts BOTH directions of the
    new rule (a store stamp still reads as an upgrade; setup-completed alone no longer does),
    so a regression to the old disjunct fails on the twin rather than passing quietly.
    """

    # A RECENT birth stamp: the store exists (so this is an upgrader) but is younger than
    # MISSED_RUN_AFTER_HOURS, which keeps the missed-run rule — it outranks the empty-state
    # copy on a LIVE schedule — out of these rows. Its own coverage lives in TestMissedRun.
    _STORE_STAMP = _RECENT

    def test_empty_with_a_store_stamp_is_fresh_start(self) -> None:
        # The UPGRADER: a store that already exists is evidence a run was once recorded.
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP)
        assert status.verdict is Verdict.WARNING  # amber-toned, never red
        assert status.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert status.fix is None  # nothing to fix — just wait
        # Honesty C: the hidden-history claim is CONDITIONED (which earlier build wrote the
        # store is unknowable), never a flat assertion that earlier runs exist.
        assert "If you used an earlier version" in status.detail

    def test_empty_with_NO_store_is_never_told_about_an_earlier_version(self) -> None:
        # The twin, and the whole point of part (i): the same completed-setup config with no
        # store must NOT inherit the upgrader's conditional past-version sentence.
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=None)
        assert status.verdict is Verdict.WARNING
        assert status.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert "earlier version" not in status.detail
        assert status.detail == "Your nightly sync will appear here."
        # …and it does not call that sync the FIRST one either. ``_CONFIGURED`` carries
        # ``schedule_registered=True``, which is exactly what a <= v3.4.0 upgrader has — an
        # install with months of nightly syncs behind it and no ``history.db``, because the
        # store did not exist before v3.5.0. "Your first nightly sync" is the same
        # ledger-vs-world falsehood the headline was rewritten to avoid.
        assert "first" not in status.detail

    def test_empty_upgrader_with_live_schedule_shows_next_run(self) -> None:
        # The next-run reassurance derives from the LIVE read-back (D4), not the config flag.
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP, schedule_status=_live_schedule("3:00 AM")
        )
        assert status.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert "3:00 AM" in status.detail

    def test_empty_upgrader_without_schedule_status_omits_next_run(self) -> None:
        # No injected read-back → NO schedule assertion (never claim a time we didn't confirm).
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=self._STORE_STAMP)
        assert "scheduled for" not in status.detail

    def test_empty_first_run_with_live_schedule_names_the_scheduled_time(self) -> None:
        # S7: the never-run install now gets the confirmed time too — AC-home-2's "with the
        # scheduled nightly time if one is registered" was only ever honoured on the OTHER
        # branch. Its own sentence, not the upgrader's ("first"/"next" would argue).
        #
        # Stage-7 BLOCK-2: "first" is GONE. An empty store is not proof of a first sync — an
        # install upgrading from <= v3.4.0 (history.db shipped in 3.5.0, no backfill) has synced
        # nightly for months and still lands here. The scheduled time is true either way.
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=None, schedule_status=_live_schedule("3:00 AM")
        )
        assert status.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert status.detail == "Your nightly sync is scheduled for 3:00 AM."
        assert "first" not in status.detail

    def test_empty_genuine_first_run_unscheduled_says_no_sync_yet(self) -> None:
        # Not established (never completed setup, no store yet) → the calm genuine-first-run copy.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=None)
        assert status.verdict is Verdict.WARNING
        assert status.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE
        assert "scheduled for" not in status.detail
        # Stage-7 BLOCK-2: and it names NO nightly at all. Nothing here has confirmed or even
        # recorded a schedule, so "your first nightly sync will appear here" would be naming
        # automation this install does not have.
        assert "nightly sync" not in status.detail

    def test_empty_completed_manual_only_install_is_NOT_an_upgrader(self) -> None:
        # Part (i), stated as the case it fixes: finishing setup is evidence about the SETTINGS,
        # never about a run. (Before S7 this row asserted the opposite, via ``setup_completed``.)
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=None)
        assert status.headline == home_status_mod.EMPTY_NO_RUNS_HEADLINE

    def test_empty_store_created_at_signals_an_upgrade_even_if_unscheduled(self) -> None:
        # A store that already exists (created_at present) is the established signal, on its own.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=self._STORE_STAMP)
        assert status.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE

    def test_the_discriminator_ignores_a_blank_stamp(self) -> None:
        # Totality: a whitespace-only stamp is not evidence of anything.
        assert home_status_mod.has_earlier_run_history(store_created_at="   ") is False
        assert home_status_mod.has_earlier_run_history(store_created_at=None) is False
        assert home_status_mod.has_earlier_run_history(store_created_at=self._STORE_STAMP) is True

    def test_empty_completed_but_confirmed_unscheduled_says_no_auto_sync(self) -> None:
        # #1b: a completed install whose read-back CONFIRMS no schedule (MISSING) must be told
        # plainly that nothing syncs on its own — NOT the "new syncs will appear" copy that implies
        # automation. Calm WARNING, NO fix CTA/badge (a manual-only district must not be nagged).
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([], cfg, now=_NOW, schedule_status=missing)
        assert status.verdict is Verdict.WARNING
        assert status.fix is None
        assert "won't sync automatically" in status.detail
        assert "New syncs will appear" not in status.detail

    def test_empty_upgrader_confirmed_unscheduled_still_says_no_auto_sync(self) -> None:
        # The no-automation nudge is NOT scoped to newcomers: an upgrader whose task is
        # confirmed gone needs it just as much. (Its headline stays the upgrader's.)
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=self._STORE_STAMP, schedule_status=missing)
        assert status.headline == home_status_mod.EMPTY_FRESH_START_HEADLINE
        assert "won't sync automatically" in status.detail

    def test_empty_completed_unconfirmed_schedule_keeps_the_neutral_copy(self) -> None:
        # Honesty inverse: an UNKNOWN/None read-back must NOT assert "won't sync automatically"
        # (we can't see the schedule) — each branch keeps its own neutral copy.
        #
        # Stage-7 BLOCK-2: this config never registered a schedule, so the neutral copy may not
        # name a nightly either. This is the state Home paints on EVERY mount before the
        # off-thread probe returns, and permanently whenever the read-back is UNKNOWN.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        status = derive_home_status([], cfg, now=_NOW, schedule_status=None)
        assert "won't sync automatically" not in status.detail
        assert status.detail == home_status_mod._NO_RUNS_YET_LEAD
        assert "nightly sync" not in status.detail

        upgrader = derive_home_status([], cfg, now=_NOW, store_created_at=self._STORE_STAMP, schedule_status=None)
        assert "won't sync automatically" not in upgrader.detail
        assert "New syncs will appear" in upgrader.detail

    # ------------------------------------------------------------------ #
    # Stage-7 BLOCK-2 — no empty-state sentence may NAME an absent nightly #
    # ------------------------------------------------------------------ #
    def test_the_first_sync_lead_needs_a_POSITIVE_schedule_signal(self) -> None:
        """The gate, both directions — an unprobed install with a registered task keeps it."""
        registered = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)
        unregistered = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)

        # (a) the app's OWN record that it registered one — the only signal available on the
        #     initial paint (the probe is off-thread), so it is admitted.
        assert derive_home_status([], registered, now=_NOW).detail == home_status_mod._FIRST_SYNC_LEAD
        # (b) nothing registered AND nothing confirmed → no nightly may be named.
        assert derive_home_status([], unregistered, now=_NOW).detail == home_status_mod._NO_RUNS_YET_LEAD
        # (c) a CONFIRMED-LIVE read-back with no next-run time to quote still counts as a signal
        #     (the assertion is that a schedule EXISTS, not a promise to name its hour).
        live_no_time = ScheduleStatus(state=ScheduleState.LIVE, headline="", detail="", next_run_display="")
        assert (
            derive_home_status([], unregistered, now=_NOW, schedule_status=live_no_time).detail
            == home_status_mod._FIRST_SYNC_LEAD
        )

    def test_the_neutral_lead_names_no_automation_and_no_earlier_version(self) -> None:
        """The replacement sentence is true for BOTH readings of an empty store."""
        lead = home_status_mod._NO_RUNS_YET_LEAD
        assert "nightly sync" not in lead  # names no automation nobody set up …
        assert "earlier version" not in lead  # … and makes no claim about a past install
        assert "Convert" in lead  # but still says how a run can reach the ledger

    def test_NO_empty_state_lead_calls_the_next_sync_the_FIRST_one(self) -> None:
        """Discharge-round BLOCK-2: the gate admits ``schedule_registered``, so a <= v3.4.0
        upgrader (months of nightly syncs, no ``history.db`` because it shipped in v3.5.0)
        reaches ``_FIRST_SYNC_LEAD``. Calling its next sync "the first" is the same
        ledger-vs-world falsehood ``EMPTY_NO_RUNS_HEADLINE`` was rewritten to avoid, and the
        module docstring three lines above the branch already says "first" is deliberately
        absent — so the two must not disagree. Swept over EVERY lead this branch can emit.
        """
        for name in ("_FIRST_SYNC_LEAD", "_NO_RUNS_YET_LEAD", "_FRESH_START_LEAD"):
            lead = getattr(home_status_mod, name)
            assert "first" not in lead.lower(), f"{name} calls the next sync the first one: {lead!r}"

    def test_the_registered_upgrader_is_never_told_this_is_its_first_sync(self) -> None:
        """The reproduction, on the real derivation, in BOTH states that reach it.

        ``schedule_status=None`` is Home's INITIAL paint on every mount (the probe is
        off-thread) and ``UNKNOWN`` is its PERMANENT state wherever the OS read-back fails —
        so this is not a corner, it is what a registered upgrader sees.
        """
        upgrader = AppConfig(
            input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True, schedule_registered=True
        )
        unknown = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
        for schedule in (None, unknown):
            status = derive_home_status([], upgrader, now=_NOW, store_created_at=None, schedule_status=schedule)
            assert status.detail == home_status_mod._FIRST_SYNC_LEAD  # the gate still admits it …
            assert "first" not in status.detail  # … but the sentence no longer over-claims

    def test_the_no_runs_headline_speaks_about_the_LEDGER_not_the_world(self) -> None:
        """BLOCK-2(a): the <= v3.4.0 upgrader has synced for months and still has no store.

        ``store_created_at`` cannot separate "never ran" from "ran before the store existed",
        so the headline may only claim what the ledger knows. The old wording ("No sync has run
        yet") was a flat assertion about the world, and false for that whole population.
        """
        assert home_status_mod.EMPTY_NO_RUNS_HEADLINE == "No runs recorded yet"
        assert "has run" not in home_status_mod.EMPTY_NO_RUNS_HEADLINE


class TestScheduleAttention:
    """D4: a schedule the config expected but the OS no longer has (or one that fired without
    completing) is the dominant fault — WARNING routed to Setup, never onboarding."""

    def test_expected_missing_routes_to_setup(self) -> None:
        sched = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"
        # #2b: the CTA names the ACTION, not the destination (Firefighter landing precision).
        assert status.fix.label == "Fix the nightly schedule"
        assert status.metrics is None

    def test_contradiction_routes_to_setup(self) -> None:
        # A record-gap contradiction: the task fired more recently than the newest record.
        sched = derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_unknown_schedule_never_overrides_a_healthy_run(self) -> None:
        # A failed query must not manufacture a fault — Home falls through to the record rules.
        sched = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.HEALTHY

    def test_clean_live_schedule_does_not_override_a_healthy_run(self) -> None:
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=_live_schedule())
        assert status.verdict is Verdict.HEALTHY

    def test_unexpected_missing_does_not_warn(self) -> None:
        # A configured manual-only install that never scheduled → not a fault on Home.
        sched = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=sched)
        assert status.verdict is Verdict.HEALTHY


class TestFailureBeatsScheduleAttention:
    """W3-B: **a failed sync is never masked by a schedule warning** (the module's own documented
    precedence — "failures above warnings"). The schedule-attention rule used to return BEFORE the
    two FAILED rules, so a failed run under an expected-MISSING / fired-but-no-record schedule
    rendered as an amber *schedule* warning and the failure went unmentioned — and, because
    ``screens/home.py`` paints the record-derived verdict first and re-derives when the async probe
    lands, the admin watched a red "sync failed" band downgrade itself to amber a second later.

    The restored rule: the two FAILED reasons outrank schedule attention; attention still outranks
    every WARNING-tier reason, the empty state and HEALTHY. Because the failure's fix CTA can only
    point one way, the *confirmed-gone* schedule is surfaced as a bounded secondary CLAUSE on the
    failure's detail (never a second CTA, never a new verdict tier).
    """

    _FAILED_ETL_DETAIL = "The sync that ran 5 hours ago hit a problem and didn't finish."
    _FAILED_DELIVERY_DETAIL = "The data was built but the upload failed."
    _SCHEDULE_GONE_CLAUSE = (
        "Your nightly schedule is also no longer registered with Windows — "
        "re-register it in Settings so the sync can run again."
    )

    @staticmethod
    def _expected_missing() -> ScheduleStatus:
        """The Event-141 shape: the config expected a schedule, the OS definitively has none."""
        return derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)

    @staticmethod
    def _contradiction() -> ScheduleStatus:
        """The LIVE fired-but-no-record shape: the task fired newer than the newest record."""
        return derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )

    @staticmethod
    def _failed_etl() -> dict:
        return _record(status="failed")

    @staticmethod
    def _failed_delivery() -> dict:
        return _record(sftp_attempted=True, sftp_ok=False)

    # -- the masking defect itself ------------------------------------------------------- #

    def test_failed_etl_is_not_masked_by_an_expected_missing_schedule(self) -> None:
        status = derive_home_status(
            [self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"
        # The fix stays on the DOMINANT fault — the failure is investigated in Run History.
        assert status.fix is not None and status.fix.dest_id == "run_history"

    def test_failed_etl_is_not_masked_by_a_fired_but_no_record_contradiction(self) -> None:
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction())
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_is_not_masked_by_an_expected_missing_schedule(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_failed_delivery_is_not_masked_by_a_contradiction(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction()
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    # -- the secondary fact: surfaced, but only when it is NOT already the failure's story - #

    def test_confirmed_gone_schedule_is_named_as_a_secondary_clause_on_a_failed_etl(self) -> None:
        # Both faults are real; only one CTA can exist. The failure keeps the band + button, and the
        # schedule fact rides along as a bounded clause so the admin doesn't fix the run and walk
        # away believing tonight's sync will resume (it positively won't — the task is gone).
        status = derive_home_status(
            [self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.detail == f"{self._FAILED_ETL_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    def test_confirmed_gone_schedule_is_named_as_a_secondary_clause_on_a_failed_delivery(self) -> None:
        status = derive_home_status(
            [self._failed_delivery()], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.detail == f"{self._FAILED_DELIVERY_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    def test_a_contradiction_adds_no_clause_because_the_failure_already_tells_that_story(self) -> None:
        # The LIVE contradiction's own copy is "your last scheduled run reported a problem" — the
        # SAME category the FAILED band already names, with less precision, and the schedule itself
        # is still registered. Restating it would duplicate, not inform (category-only faults).
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=self._contradiction())
        assert status.detail == self._FAILED_ETL_DETAIL

    @pytest.mark.parametrize(
        "schedule",
        [
            None,  # not probed yet (the first paint)
            _live_schedule(),  # a clean LIVE schedule
            derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None),
            derive_schedule_status(
                ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
            ),
        ],
        ids=["unprobed", "live", "unexpected-missing", "unknown"],
    )
    def test_no_clause_when_the_schedule_is_not_confirmed_gone(self, schedule: ScheduleStatus | None) -> None:
        # D4 honesty, inverted: an UNKNOWN/unprobed read-back must never be spoken of as a fault,
        # and an unexpected MISSING (a manual-only district) is not a broken promise.
        status = derive_home_status([self._failed_etl()], _CONFIGURED, now=_NOW, schedule_status=schedule)
        assert status.detail == self._FAILED_ETL_DETAIL

    def test_secondary_clause_is_fixed_copy_and_leaks_no_record_free_text(self) -> None:
        # Privacy (LIVE/top): the clause is authored copy, not a field lifted off the record — a
        # poisoned free-text `error` cannot ride into it.
        secret = r"C:\Users\x\secret\roster.csv"
        status = derive_home_status(
            [_record(status="failed", error=f"FileNotFoundError: {secret}")],
            _CONFIGURED,
            now=_NOW,
            schedule_status=self._expected_missing(),
        )
        assert secret not in status.detail
        assert "secret" not in status.detail
        assert status.detail == f"{self._FAILED_ETL_DETAIL} {self._SCHEDULE_GONE_CLAUSE}"

    # -- attention keeps outranking everything BELOW the failures ------------------------- #

    @pytest.mark.parametrize(
        "override",
        [
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 3}},
            {"timestamp": _OLD},
            {},  # a clean, recent, HEALTHY latest
        ],
        ids=["anomaly", "data-warnings", "stale", "healthy"],
    )
    def test_schedule_attention_still_outranks_every_non_failed_latest(self, override: dict) -> None:
        # The demotion is surgical: attention now loses ONLY to the FAILED tier. A nightly that
        # won't run again still dominates an amber record fault and a green one.
        status = derive_home_status(
            [_record(**override)], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Your schedule isn't registered anymore"
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_schedule_attention_still_wins_over_an_empty_store(self) -> None:
        # No record exists → nothing to mask; the schedule fault remains the whole story.
        status = derive_home_status([], _CONFIGURED, now=_NOW, schedule_status=self._expected_missing())
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Your schedule isn't registered anymore"

    # -- the no-flip pin (the observable symptom in screens/home.py) --------------------- #

    @pytest.mark.parametrize("shape", ["failed_etl", "failed_delivery"])
    @pytest.mark.parametrize("flavor", ["expected_missing", "contradiction"])
    def test_verdict_never_downgrades_when_the_async_schedule_probe_lands(self, shape: str, flavor: str) -> None:
        # ``screens/home.py`` paints ``_render(None)`` from the store, then re-renders in place when
        # the off-thread probe returns. A trust instrument must not downgrade its own alarm, so the
        # two paints must agree on verdict AND headline; only the detail may GROW (the secondary
        # clause). This is the pin for the flip the admin actually watched.
        record = self._failed_etl() if shape == "failed_etl" else self._failed_delivery()
        schedule = self._expected_missing() if flavor == "expected_missing" else self._contradiction()

        first_paint = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=None)
        second_paint = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)

        assert second_paint.verdict is first_paint.verdict
        assert second_paint.headline == first_paint.headline
        assert second_paint.detail.startswith(first_paint.detail)
        assert second_paint.fix == first_paint.fix

    @pytest.mark.parametrize("flavor", ["expected_missing", "contradiction"])
    def test_probe_never_lowers_the_severity_of_any_latest_record(self, flavor: str) -> None:
        # The general invariant behind the pin above: across every fault shape, learning the
        # schedule may ESCALATE the verdict (a clean record under a dead schedule) but must never
        # de-escalate it. ``Verdict`` is declared in escalating-attention order.
        severity = list(Verdict)
        schedule = self._expected_missing() if flavor == "expected_missing" else self._contradiction()
        for override in (
            {"status": "failed"},
            {"sftp_attempted": True, "sftp_ok": False},
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 2}},
            {"timestamp": _OLD},
            {},
        ):
            record = _record(**override)
            first = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=None)
            second = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
            assert severity.index(second.verdict) >= severity.index(first.verdict), override


class TestMissedRun:
    """The owner rule (2026-07-15): a CONFIRMED-LIVE schedule + no run record in the last 26h +
    an established store → the missed-run WARNING. Every guard failing → stay silent (a false
    "missed run" on day one costs more trust than a one-day-late first warning)."""

    _MISSED_HEADLINE = "We expected a nightly sync that didn't arrive"
    # The store's birth stamp, comfortably older than the missed-run window.
    _ESTABLISHED = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
    # A clean record just past the window (but well inside STALE_AFTER_HOURS).
    _PAST_WINDOW = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS + 1)).isoformat(timespec="seconds")

    def test_live_empty_established_store_warns_missed(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == self._MISSED_HEADLINE
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.metrics is None

    def test_live_record_past_window_warns_missed(self) -> None:
        status = derive_home_status(
            [_record(timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == self._MISSED_HEADLINE

    def test_live_recent_record_stays_healthy(self) -> None:
        status = derive_home_status(
            [_record()], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.HEALTHY

    def test_fresh_store_guard_stays_silent(self) -> None:
        # A store younger than the window (day-one install) has not missed anything yet.
        fresh = (_NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=fresh, schedule_status=_live_schedule())
        assert status.headline == "Run history starts fresh here"

    def test_no_store_meta_stays_silent(self) -> None:
        status = derive_home_status([], _CONFIGURED, now=_NOW, store_created_at=None, schedule_status=_live_schedule())
        assert status.headline != self._MISSED_HEADLINE

    def test_unparseable_created_at_stays_silent(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at="garbage", schedule_status=_live_schedule()
        )
        assert status.headline == "Run history starts fresh here"

    def test_unconfirmed_schedule_never_fires_missed(self) -> None:
        # D4 honesty: None (not probed) and UNKNOWN (query failed) never assert a miss — the
        # schedule-unaware staleness proxy remains the honest fallback for an old clean record.
        unknown = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        for sched in (None, unknown):
            status = derive_home_status(
                [_record(timestamp=_OLD)],
                _CONFIGURED,
                now=_NOW,
                store_created_at=self._ESTABLISHED,
                schedule_status=sched,
            )
            assert status.headline == "No recent sync"

    def test_confirmed_missing_schedule_does_not_fire_missed(self) -> None:
        # An unexpected MISSING (manual-only install) is not a missed run — nothing was promised.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status([], cfg, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=missing)
        assert status.headline != self._MISSED_HEADLINE

    def test_failed_latest_is_never_masked_by_missed_run(self) -> None:
        # Failures above warnings: an old FAILED record keeps the red verdict, not this amber.
        status = derive_home_status(
            [_record(status="failed", timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_latest_is_never_masked_by_missed_run(self) -> None:
        status = derive_home_status(
            [_record(sftp_attempted=True, sftp_ok=False, timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    def test_missed_run_precedes_old_anomaly_warning(self) -> None:
        # Among WARNINGs the missed run is the fresher fact — the anomaly copy would describe
        # a run that is over a day old.
        status = derive_home_status(
            [_record(anomalies=["ANOMALY: x"], timestamp=self._PAST_WINDOW)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.headline == self._MISSED_HEADLINE

    def test_boundary_exactly_at_window_stays_silent(self) -> None:
        exactly = (_NOW - timedelta(hours=MISSED_RUN_AFTER_HOURS)).isoformat(timespec="seconds")
        status = derive_home_status(
            [_record(timestamp=exactly)],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY  # strictly-greater-than, mirrors is_stale

    def test_unparseable_newest_timestamp_stays_silent(self) -> None:
        # Can't establish the gap → don't cry wolf (the record still classifies normally).
        status = derive_home_status(
            [_record(timestamp="garbage")],
            _CONFIGURED,
            now=_NOW,
            store_created_at=self._ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.headline != self._MISSED_HEADLINE

    def test_detail_is_plain_language_no_raw_values(self) -> None:
        status = derive_home_status(
            [], _CONFIGURED, now=_NOW, store_created_at=self._ESTABLISHED, schedule_status=_live_schedule()
        )
        assert "26" not in status.detail  # the window is copy-free plain language ("the last day")
        assert self._ESTABLISHED not in status.detail  # never the raw ISO


def _windowed(**over: object) -> AppConfig:
    """A configured install WITH the seasonal window enabled (Aug 11 -> Jul 6, wrap-around)."""
    base: dict = dict(
        input_dir="/in",
        output_dir="/out",
        sis_type="myedbc",
        schedule_registered=True,
        sync_window_enabled=True,
        sync_window_start="08-11",
        sync_window_end="07-06",
    )
    base.update(over)
    return AppConfig(**base)


# A "now" comfortably OUTSIDE the Aug 11 -> Jul 6 window (mid-July summer break) and one INSIDE it.
_SUMMER = datetime(2026, 7, 20, 8, 0, 0)  # 07-20: > Jul 6 and < Aug 11 -> paused
_SUMMER_ESTABLISHED = (_SUMMER - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
_PAUSED_HEADLINE = "Paused for the summer"
_MISSED_HEADLINE = "We expected a nightly sync that didn't arrive"


class TestSeasonalPause:
    """B: while an ENABLED seasonal window is OUTSIDE its active season, no nightly sync arrives by
    design — the missed-run and stale warnings must be SUPPRESSED and a calm HEALTHY-toned
    "Paused for the summer — resumes <date>" state shown instead. A genuinely FAILED latest record
    still surfaces (a real failure isn't hidden by summer); missed-run + stale never fire in a pause.
    """

    def test_reproduce_missed_run_is_suppressed_and_paused_shows(self) -> None:
        # Reproduce-first (RED on the pre-slice base, which ignores the window): a LIVE schedule +
        # an established store + no records is a textbook missed-run — but we are OUTSIDE an enabled
        # window, so the expected nightly is a summer no-op. Home must show the calm paused state.
        status = derive_home_status(
            [],
            _windowed(),
            now=_SUMMER,
            store_created_at=_SUMMER_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY  # an intentional pause is healthy, never amber/red
        assert status.headline == _PAUSED_HEADLINE
        assert _MISSED_HEADLINE not in status.headline
        assert "Aug 11" in status.detail  # friendly resume date, PII-free (no raw ISO)
        assert _SUMMER_ESTABLISHED not in status.detail
        assert status.fix is None

    def test_missed_run_with_a_record_is_also_suppressed_in_a_pause(self) -> None:
        # The record-present missed-run path (newest record past the window) is suppressed too.
        old = (_SUMMER - timedelta(hours=MISSED_RUN_AFTER_HOURS + 1)).isoformat(timespec="seconds")
        status = derive_home_status(
            [_record(timestamp=old)],
            _windowed(),
            now=_SUMMER,
            store_created_at=_SUMMER_ESTABLISHED,
            schedule_status=_live_schedule(),
        )
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE

    def test_stale_clean_success_is_suppressed_in_a_pause(self) -> None:
        # A clean-but-old success in summer is expected (nothing runs) — no "No recent sync" warning.
        old = (_SUMMER - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")
        status = derive_home_status([_record(timestamp=old)], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE
        assert "No recent sync" not in status.headline

    def test_failed_latest_still_surfaces_in_a_pause(self) -> None:
        # Non-negotiable: a REAL failure is never hidden by summer — FAILED outranks the pause.
        status = derive_home_status([_record(status="failed")], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_failed_delivery_latest_still_surfaces_in_a_pause(self) -> None:
        status = derive_home_status([_record(sftp_attempted=True, sftp_ok=False)], _windowed(), now=_SUMMER)
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"

    def test_expected_missing_schedule_still_surfaces_in_a_pause(self) -> None:
        # A genuinely gone task makes "resumes <date>" a lie — the MISSING attention still surfaces
        # (only the by-design LIVE fired-but-no-record contradiction is suppressed during a pause).
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
        status = derive_home_status([_record()], _windowed(), now=_SUMMER, schedule_status=missing)
        assert status.verdict is Verdict.WARNING
        assert status.fix is not None and status.fix.dest_id == "setup"

    def test_confirmed_missing_schedule_outranks_pause_empty_store(self) -> None:
        # FIX 2: the hint_registered=False twin of the test above. window enabled -> registered ->
        # "Remove nightly sync" (schedule_registered=False, window left ON) -> summer. The MISSING
        # read-back is now expected=False -> attention=False, so the schedule-attention rule never
        # fires and the empty-store paused branch used to mask a schedule that is CONFIRMED gone
        # (it will never resume, so "resumes <date>" is a lie). The honest "add a nightly schedule"
        # WARNING must surface instead of the green paused headline.
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        status = derive_home_status(
            [],
            _windowed(setup_completed=True, schedule_registered=False),
            now=_SUMMER,
            store_created_at=_SUMMER_ESTABLISHED,
            schedule_status=missing,
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline != _PAUSED_HEADLINE
        assert "add a nightly schedule" in status.detail

    def test_confirmed_missing_schedule_outranks_pause_populated_store(self) -> None:
        # The populated twin ("Remove nightly sync" leaves past runs in the store). A confirmed-gone
        # schedule must not read as a calm summer pause -> the normal record rules apply (here: an
        # old newest record -> the honest "No recent sync" WARNING), never the HEALTHY pause.
        missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None)
        old = (_SUMMER - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")
        status = derive_home_status(
            [_record(timestamp=old)],
            _windowed(setup_completed=True, schedule_registered=False),
            now=_SUMMER,
            schedule_status=missing,
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline != _PAUSED_HEADLINE

    def test_live_fired_but_no_record_contradiction_is_suppressed_in_a_pause(self) -> None:
        # The paused nightly fires (LastRunTime advances) but writes NO record by design — the
        # resulting fired-but-no-record contradiction is a false alarm in summer and is suppressed.
        contradiction = derive_schedule_status(
            ScheduleReadback(found=True, last_run="2026-07-19T04:00:00"),
            hint_registered=True,
            latest_record_ts=_RECENT,
        )
        status = derive_home_status([_record()], _windowed(), now=_SUMMER, schedule_status=contradiction)
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == _PAUSED_HEADLINE

    def test_boundary_last_active_day_is_not_paused(self) -> None:
        # Jul 6 is the INCLUSIVE last active day -> normal behavior (not paused).
        on_boundary = datetime(2026, 7, 6, 8, 0, 0)
        status = derive_home_status(
            [_record(timestamp=(on_boundary - timedelta(hours=5)).isoformat())], _windowed(), now=on_boundary
        )
        assert status.headline != _PAUSED_HEADLINE

    def test_boundary_first_paused_day(self) -> None:
        # Jul 7 is the first day OUTSIDE the window -> paused.
        first_paused = datetime(2026, 7, 7, 8, 0, 0)
        status = derive_home_status([_record(timestamp=_OLD)], _windowed(), now=first_paused)
        assert status.headline == _PAUSED_HEADLINE

    def test_inside_window_missed_run_still_fires(self) -> None:
        # Window ENABLED but today INSIDE it (mid-June) -> normal cadence rules, missed-run fires.
        june = datetime(2026, 6, 15, 8, 0, 0)
        established = (june - timedelta(hours=MISSED_RUN_AFTER_HOURS + 48)).isoformat(timespec="seconds")
        status = derive_home_status(
            [], _windowed(), now=june, store_created_at=established, schedule_status=_live_schedule()
        )
        assert status.verdict is Verdict.WARNING
        assert status.headline == _MISSED_HEADLINE

    def test_disabled_window_is_year_round_unchanged(self) -> None:
        # The opt-in default: disabled window -> byte-identical to today (no paused state ever).
        cfg = _windowed(sync_window_enabled=False)
        status = derive_home_status(
            [], cfg, now=_SUMMER, store_created_at=_SUMMER_ESTABLISHED, schedule_status=_live_schedule()
        )
        assert status.headline == _MISSED_HEADLINE  # the missed-run warning fires as before

    def test_blank_bounds_never_pause(self) -> None:
        # Enabled but with unset bounds (never configured) -> treated as year-round, never paused.
        cfg = _windowed(sync_window_start="", sync_window_end="")
        status = derive_home_status([_record(timestamp=_OLD)], cfg, now=_SUMMER)
        assert status.headline != _PAUSED_HEADLINE

    def test_malformed_bounds_never_pause(self) -> None:
        # A malformed window (should be gated at save, but be TOTAL) -> year-round, never crash.
        cfg = _windowed(sync_window_start="13-40", sync_window_end="xx-yy")
        status = derive_home_status([_record(timestamp=_OLD)], cfg, now=_SUMMER)
        assert isinstance(status, HomeStatus)
        assert status.headline != _PAUSED_HEADLINE

    def test_paused_detail_is_plain_language_and_pii_free(self) -> None:
        status = derive_home_status([], _windowed(), now=_SUMMER, store_created_at=_SUMMER_ESTABLISHED)
        assert "08-11" not in status.detail  # never the raw MM-DD / ISO
        assert "Nothing is wrong" in status.detail


class TestFailedRules:
    def test_failed_etl_is_failed_verdict(self) -> None:
        status = _derive(_record(status="failed"))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"
        # 0032 T2 #4: an ETL failure is investigated in Run History — the label says where it goes.
        assert status.fix is not None and status.fix.dest_id == "run_history"
        assert status.fix.label == "Check Run History"

    def test_failed_etl_detail_derives_from_the_records_timestamp(self) -> None:
        # 0032 T1 #1b: never the hard-coded "Last night's…" — a failed latest can be any age,
        # so the copy dates the failed run from its own timestamp via friendly_timestamp.
        status = _derive(_record(status="failed"))
        assert status.detail == "The sync that ran 5 hours ago hit a problem and didn't finish."

    def test_failed_etl_detail_missing_timestamp_reads_recently(self) -> None:
        # Totality: no timestamp → friendly_timestamp's safe fallback, never a raw/blank slot.
        rec = _record(status="failed")
        del rec["timestamp"]
        status = _derive(rec)
        assert status.detail == "The sync that ran recently hit a problem and didn't finish."

    def test_failed_etl_precedes_sftp_and_data_errors(self) -> None:
        # A failed ETL is the dominant fault even with an SFTP failure + data errors also set.
        status = _derive(_record(status="failed", sftp_attempted=True, sftp_ok=False, data_errors={"total": 9}))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Last sync failed"

    def test_sftp_delivery_failed_is_failed_verdict(self) -> None:
        status = _derive(_record(sftp_attempted=True, sftp_ok=False))
        assert status.verdict is Verdict.FAILED
        assert status.headline == "Your roster didn't reach SpacesEDU"
        # 0032 T2 #4: the delivery fix (host/credentials) lives in Settings' delivery section,
        # not the read-only run ledger — and the label names the destination.
        assert status.fix is not None and status.fix.dest_id == "setup"
        assert status.fix.label == "Open Settings"

    def test_sftp_failed_precedes_data_errors(self) -> None:
        status = _derive(_record(sftp_attempted=True, sftp_ok=False, data_errors={"total": 3}))
        assert status.headline == "Your roster didn't reach SpacesEDU"


class TestWarningRules:
    def test_anomaly_is_warning(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: Students dropped from 200 to 100 rows (50% decrease)"]))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Something looked off in the last sync"
        # Never surface the raw ANOMALY:-prefixed string.
        assert "ANOMALY:" not in status.detail
        assert "One roster file" in status.detail

    def test_multiple_anomalies_plural_detail(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: a", "ANOMALY: b"]))
        assert "2 roster files" in status.detail

    def test_anomaly_precedes_data_errors(self) -> None:
        status = _derive(_record(anomalies=["ANOMALY: x"], data_errors={"total": 4}))
        assert status.headline == "Something looked off in the last sync"

    def test_data_errors_is_warning(self) -> None:
        status = _derive(_record(data_errors={"total": 3, "by_field": {"Students.email": 3}}))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Completed with 3 data warnings"
        assert status.fix is not None and status.fix.dest_id == "run_history"
        # The fixture's record IS delivered (sftp_ok=True) — the delivery claim is earned here.
        assert status.detail == "Some records had field problems and were skipped — the sync still delivered."

    def test_data_errors_no_sftp_never_claims_delivery(self) -> None:
        # 2026-08-31 live-install mislabel: a data-warnings run with NO SFTP attempt read "the
        # sync still delivered" while nothing was uploaded. The detail must consult the record's
        # SFTP axis (``sftp_delivered``) exactly like the healthy branch always has.
        status = _derive(_record(data_errors={"total": 3}, sftp_attempted=False, sftp_ok=False))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "Completed with 3 data warnings"
        assert status.detail == (
            "Some records had field problems and were skipped — the files were still written to your output folder."
        )
        assert "delivered" not in status.detail

    def test_single_data_error_singular_headline(self) -> None:
        status = _derive(_record(data_errors={"total": 1}))
        assert status.headline == "Completed with 1 data warning"

    def test_stale_clean_success_is_warning(self) -> None:
        status = _derive(_record(timestamp=_OLD))
        assert status.verdict is Verdict.WARNING
        assert status.headline == "No recent sync"
        assert _OLD not in status.detail  # plain relative phrase, not the raw ISO
        assert status.fix is not None and status.fix.dest_id == "run_history"


class TestHealthy:
    def test_recent_clean_delivered_success_is_healthy_with_metrics(self) -> None:
        status = _derive(_record())
        assert status.verdict is Verdict.HEALTHY
        # 0032 T1 #1c: no schedule read-back injected → never assert ongoing automation.
        assert status.headline == "Your roster is up to date"
        assert status.fix is None
        assert status.metrics is not None
        assert status.metrics.sftp_delivered is True
        # Raw ISO never leaks into the healthy detail.
        assert _RECENT not in status.detail

    def test_healthy_headline_asserts_syncing_only_on_live_readback(self) -> None:
        # 0032 T1 #1c: "syncing" claims ongoing automation → demands a CONFIRMED-LIVE read-back.
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=_live_schedule())
        assert status.verdict is Verdict.HEALTHY
        assert status.headline == "Your roster is syncing"

    def test_healthy_headline_stays_neutral_on_unconfirmed_readback(self) -> None:
        # An UNKNOWN read-back (query failed) must not upgrade the claim — honesty inverse of D4.
        unknown = derive_schedule_status(
            ScheduleReadback(found=None, error="denied"), hint_registered=True, latest_record_ts=None
        )
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, schedule_status=unknown)
        assert status.headline == "Your roster is up to date"

    def test_healthy_delivered_detail_names_spacesedu(self) -> None:
        # 0032 T1 #1a: "delivered" claims branch on the record's SFTP axis — sftp_ok names the
        # actual destination, never the old axis-blind "delivered cleanly".
        status = _derive(_record())
        assert status.detail == "Last sync delivered to SpacesEDU 5 hours ago."

    def test_healthy_no_sftp_detail_says_completed_to_output_folder(self) -> None:
        # A run that never attempted SFTP must NEVER claim a delivery that didn't happen.
        status = _derive(_record(sftp_attempted=False, sftp_ok=False))
        assert status.verdict is Verdict.HEALTHY
        assert status.detail == "Last sync completed 5 hours ago — files were written to your output folder."
        assert "delivered" not in status.detail
        assert status.metrics is not None
        assert status.metrics.sftp_delivered is False

    def test_metrics_show_5_rostering_tiles_not_7_with_zeros(self) -> None:
        # A SpacesEDU district run (myBlueprint+ counts 0) shows exactly the 5 rostering tiles.
        status = _derive(_record())
        assert status.metrics is not None
        assert set(status.metrics.entity_counts) == {"Students", "Staff", "Family", "Classes", "Enrollments"}
        assert "CourseInfo" not in status.metrics.entity_counts
        assert "StudentAttendance" not in status.metrics.entity_counts

    def test_metrics_include_myblueprint_tiles_when_non_zero(self) -> None:
        status = _derive(_record(CourseInfo=15, StudentCourses=200))
        assert status.metrics is not None
        assert status.metrics.entity_counts["CourseInfo"] == 15
        assert status.metrics.entity_counts["StudentCourses"] == 200


# --------------------------------------------------------------------------- #
# The healthy line's roster-size clause (0038 S7) — the truth table            #
# --------------------------------------------------------------------------- #
class TestSizeClause:
    """Slim Home drops the tile row, so the healthy line carries ONE size number.

    The hazard the table exists for: the run record writes EVERY entity key with a defaulted
    ``0`` (``pipeline.build_run_record`` over ``_RECORD_ENTITY_KEYS``), so the record alone
    cannot tell "this config doesn't emit Students" from "the roster collapsed to zero". The
    clause therefore takes the config's OWN produced entities as data. A non-zero filter would
    have conflated the two and hidden exactly the alarm this feature exists to raise.
    """

    _ROSTERING = ("Students", "Staff", "Family", "Classes", "Enrollments")
    _ATTENDANCE_ONLY = ("StudentAttendance",)
    _MYB_ONLY = ("CourseInfo", "StudentCourses")
    _MBP_CORE = ("Students", "CourseInfo", "StudentCourses")

    def test_a_rostering_config_counts_students(self) -> None:
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert status.detail == "Last sync delivered to SpacesEDU 5 hours ago. It included 100 students."

    def test_an_attendance_only_config_counts_attendance_rows_not_zero_students(self) -> None:
        # THE headline hazard: this config's record carries Students=0 by shape.
        record = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, StudentAttendance=8140)
        status = derive_home_status([record], _CONFIGURED, now=_NOW, output_entities=self._ATTENDANCE_ONLY)
        assert "It included 8,140 attendance rows." in status.detail
        assert "student" not in status.detail

    def test_a_myblueprint_only_config_counts_courses_not_zero_students(self) -> None:
        record = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, CourseInfo=1204, StudentCourses=9)
        status = derive_home_status([record], _CONFIGURED, now=_NOW, output_entities=self._MYB_ONLY)
        assert "It included 1,204 courses." in status.detail
        assert "0 students" not in status.detail

    def test_a_mixed_config_leads_with_the_first_produced_entity(self) -> None:
        record = _record(CourseInfo=1204, StudentCourses=9)
        status = derive_home_status([record], _CONFIGURED, now=_NOW, output_entities=self._MBP_CORE)
        assert "It included 100 students." in status.detail

    def test_a_genuine_zero_on_a_rostering_config_IS_printed(self) -> None:
        # The alarm, not a case to hide: this district DOES emit Students and shipped none.
        status = derive_home_status([_record(Students=0)], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 0 students." in status.detail

    def test_one_row_is_singular(self) -> None:
        status = derive_home_status([_record(Students=1)], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 1 student." in status.detail

    def test_classes_pluralise_irregularly(self) -> None:
        # ``humanize.pluralize`` is a naive ``+ "s"`` and would render "1 classs"/"40 classs";
        # both forms are authored in SIZE_NOUNS for exactly this reason.
        assert (
            home_status_mod.size_clause({"Classes": 40}, ("Classes",), expected_sis_type="myedbc")
            == "It included 40 classes."
        )
        assert (
            home_status_mod.size_clause({"Classes": 1}, ("Classes",), expected_sis_type="myedbc")
            == "It included 1 class."
        )

    def test_no_counts_record_means_no_clause(self) -> None:
        assert home_status_mod.size_clause(None, self._ROSTERING, expected_sis_type="myedbc") == ""

    def test_unknown_output_entities_means_no_clause(self) -> None:
        # A degraded/unreadable config resolves to () — the clause vanishes, never guesses.
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, output_entities=())
        assert status.detail == "Last sync delivered to SpacesEDU 5 hours ago."
        assert "It included" not in status.detail

    def test_a_partner_entity_with_no_authored_noun_means_no_clause(self) -> None:
        # The table is the allowlist: a key out of a hand-dropped YAML is never echoed into
        # admin-facing copy, and never falls back to the raw key.
        assert home_status_mod.size_clause({"SomethingElse": 42}, ("SomethingElse",), expected_sis_type="myedbc") == ""

    def test_the_clause_vanishes_when_the_delivery_has_no_build_behind_it(self) -> None:
        # ``_counts_source`` → None (a delivery-only latest with no successful build): the
        # tiles were already suppressed here, and the clause must be too.
        delivery = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, delivery_only=True, source="manual")
        status = derive_home_status([delivery], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is None
        assert "It included" not in status.detail

    def test_a_delivery_takes_its_size_from_the_build_it_shipped(self) -> None:
        # The positive twin of the row above: with a successful build behind it, the delivery
        # names THAT build's roster rather than its own zero-shaped counts.
        delivery = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, delivery_only=True, source="manual")
        build = _record(Students=4812)
        status = derive_home_status([delivery, build], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 4,812 students." in status.detail

    def test_the_clause_rides_the_no_sftp_phrasing_too(self) -> None:
        status = derive_home_status(
            [_record(sftp_attempted=False, sftp_ok=False)], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING
        )
        assert status.detail == (
            "Last sync completed 5 hours ago — files were written to your output folder. It included 100 students."
        )

    def test_the_clause_only_rides_the_HEALTHY_line(self) -> None:
        # Every fault branch names a category and offers a fix; a roster size there would be a
        # number attached to a run that did not deliver.
        for extra in ({"status": "failed"}, {"sftp_attempted": True, "sftp_ok": False}, {"anomalies": ["ANOMALY: x"]}):
            status = derive_home_status([_record(**extra)], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
            assert "It included" not in status.detail, extra

    def test_garbage_counts_do_not_crash_the_clause(self) -> None:
        assert (
            home_status_mod.size_clause({"Students": "not-a-number"}, ("Students",), expected_sis_type="myedbc")
            == "It included 0 students."
        )

    # ------------------------------------------------------------------ #
    # Stage-7 BLOCK-1 — the entity list and the counts must be the SAME    #
    # district. Both divergence routes below are shipped design:           #
    #   * Mapping's Apply writes ``sis_type`` and does NOT re-register the #
    #     nightly task, so later SCHEDULED records carry the OLD district; #
    #   * Convert records the district picked in its dropdown without      #
    #     saving it (the S5 "This run: <district>" pill exists for this).  #
    # Before the guard, an ``sd51attendance`` record under a saved         #
    # ``sd48myedbc`` rendered "It included 0 students." under a GREEN band.#
    # ------------------------------------------------------------------ #
    def test_a_record_from_a_DIFFERENT_district_prints_no_number(self) -> None:
        # The attendance record's rostering keys are zero BY SHAPE; the saved district's entity
        # list says Students, so the clause would have read "0 students" — a false alarm number
        # under a healthy verdict.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="sd48myedbc", schedule_registered=True)
        record = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, StudentAttendance=8140)
        record["sis_type"] = "sd51attendance"
        status = derive_home_status([record], cfg, now=_NOW, output_entities=self._ROSTERING)
        assert status.verdict is Verdict.HEALTHY  # the verdict itself is untouched …
        assert "It included" not in status.detail  # … only the number drops out
        assert "0 students" not in status.detail

    def test_the_OTHER_direction_too_a_rostering_record_under_a_courses_config(self) -> None:
        # The mirror case the panel reproduced: an sd48 record (4,812 students, no course keys)
        # under a saved ``mbponly`` read "It included 0 courses."
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="mbponly", schedule_registered=True)
        record = _record(Students=4812)
        record["sis_type"] = "sd48myedbc"
        status = derive_home_status([record], cfg, now=_NOW, output_entities=self._MYB_ONLY)
        assert "It included" not in status.detail
        assert "0 courses" not in status.detail

    def test_the_positive_twin_an_AGREEING_district_still_prints(self) -> None:
        # Without this the guard could be satisfied by suppressing the clause outright.
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="sd48myedbc", schedule_registered=True)
        record = _record(Students=4812)
        record["sis_type"] = "sd48myedbc"
        status = derive_home_status([record], cfg, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 4,812 students." in status.detail

    def test_a_record_with_no_district_at_all_still_prints(self) -> None:
        # Totality + back-compat: pre-enrichment records carry no ``sis_type``, and an absent
        # value is NOT a disagreement. Mirrors ``run_history._district_note``: BOTH sides must
        # be known non-empty to establish a difference.
        assert "sis_type" not in _record()
        status = derive_home_status([_record()], _CONFIGURED, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 100 students." in status.detail
        # …and the same rule from the other side, at the function boundary.
        assert (
            home_status_mod.size_clause({"Students": 7, "sis_type": "sd40myedbc"}, ("Students",), expected_sis_type="")
            == "It included 7 students."
        )

    def test_the_guard_is_applied_to_the_BUILD_a_delivery_shipped(self) -> None:
        """The walk-back is where ``records[0]`` would have been the wrong anchor.

        A delivery-only latest carries no counts, so ``_counts_source`` walks back to the newest
        successful BUILD — which can be a different district again. Guarding ``records[0]`` would
        have compared the DELIVERY's district and then printed the older build's numbers.
        """
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="sd48myedbc", schedule_registered=True)
        delivery = _record(Students=0, Staff=0, Family=0, Classes=0, Enrollments=0, delivery_only=True)
        delivery["sis_type"] = "sd48myedbc"  # the delivery AGREES — only the build behind it does not
        foreign_build = _record(Students=99)
        foreign_build["sis_type"] = "sd74myedbc"
        status = derive_home_status([delivery, foreign_build], cfg, now=_NOW, output_entities=self._ROSTERING)
        assert "It included" not in status.detail, "the guard must follow the counts, not the latest record"

        # The positive twin: the same shape with an agreeing build behind it DOES print.
        own_build = _record(Students=99)
        own_build["sis_type"] = "sd48myedbc"
        agreeing = derive_home_status([delivery, own_build], cfg, now=_NOW, output_entities=self._ROSTERING)
        assert "It included 99 students." in agreeing.detail

    def test_the_injected_entity_ORDER_does_not_decide_the_lead(self) -> None:
        """Pins the fact ``mapping_catalog._ordered_entities``'s docstring now states.

        That docstring used to guarantee "a picker's label order and Home's 'which entity
        leads' rule can never diverge". They are not coupled at all: ``size_clause`` collects
        ``output_entities`` into a SET and walks ``SIZE_NOUNS``, so THAT dict's order is the
        lead rule and the injected order is discarded. Asserted here so the corrected wording
        cannot quietly rot back into the false one.
        """
        counts = {"Students": 10, "CourseInfo": 20, "sis_type": "myedbc"}
        forward = home_status_mod.size_clause(counts, ("Students", "CourseInfo"), expected_sis_type="myedbc")
        backward = home_status_mod.size_clause(counts, ("CourseInfo", "Students"), expected_sis_type="myedbc")
        assert forward == backward == "It included 10 students."

    def test_every_entity_key_home_can_see_has_an_authored_noun(self) -> None:
        """Totality against the record's own key set — a new output entity cannot ship mute.

        Read off ``pipeline._RECORD_ENTITY_KEYS`` rather than re-typed, so adding an entity to
        the pipeline without a noun here is RED instead of a silently-absent clause.
        """
        from src.etl.pipeline import _RECORD_ENTITY_KEYS

        assert set(_RECORD_ENTITY_KEYS) == set(home_status_mod.SIZE_NOUNS), (
            "every entity the run record can count needs a singular/plural noun for the size clause"
        )
        for key, forms in home_status_mod.SIZE_NOUNS.items():
            assert len(forms) == 2 and all(forms), key
            assert forms[0] != forms[1], f"{key} has no distinct singular/plural"


# --------------------------------------------------------------------------- #
# The quick-action strip (0038 S7) — tiering as a COUNTED rule                 #
# --------------------------------------------------------------------------- #
class TestQuickActions:
    _FIXES = (
        None,
        home_status_mod.FixAction("Check Run History", "run_history"),
        home_status_mod.FixAction("Open Settings", "setup"),
        home_status_mod.FixAction("Fix the nightly schedule", "setup"),
    )

    @pytest.mark.parametrize("fix", _FIXES, ids=["healthy", "fix-run-history", "fix-settings", "fix-schedule"])
    def test_exactly_one_filled_action_exists_on_the_surface(self, fix) -> None:
        """The design system's one-primary rule, as arithmetic over the WHOLE surface.

        The strip's filled count plus the verdict's fix CTA (1 when present) must be exactly
        one in every state — the property the render smokes then confirm on the built tree.
        """
        actions = home_status_mod.quick_actions(fix)
        filled_in_strip = sum(1 for action in actions if action.filled)
        assert filled_in_strip + (1 if fix is not None else 0) == 1

    @pytest.mark.parametrize("fix", _FIXES, ids=["healthy", "fix-run-history", "fix-settings", "fix-schedule"])
    def test_the_strip_never_repeats_the_fixs_destination(self, fix) -> None:
        actions = home_status_mod.quick_actions(fix)
        if fix is not None:
            assert fix.dest_id not in {action.dest_id for action in actions}
        assert len({action.dest_id for action in actions}) == len(actions)

    def test_the_healthy_strip_leads_with_convert(self) -> None:
        actions = home_status_mod.quick_actions(None)
        assert [(a.label, a.dest_id, a.filled) for a in actions] == [
            ("Convert now", "convert", True),
            ("Run History", "run_history", False),
            ("Settings", "setup", False),
        ]

    def test_every_fix_this_module_can_emit_is_covered(self) -> None:
        """Reality-read: the parametrized fixes above must be the real ``FixAction`` shapes.

        Hand-listed cases rot. Every fix destination ``derive_home_status`` can actually return
        is swept here, so a new fix route cannot quietly acquire an untested tiering.
        """
        emitted = {status.fix.dest_id for status in _every_home_status() if status.fix is not None}
        assert emitted <= {fix.dest_id for fix in TestQuickActions._FIXES if fix is not None}
        assert emitted, "no fix CTA was produced at all — this sweep would be vacuous"

    def test_the_strip_destinations_are_real_rail_destinations(self) -> None:
        from src.ui_flet.nav import DESTINATIONS

        known = {dest.id for dest in DESTINATIONS}
        for action in home_status_mod.quick_actions(None):
            assert action.dest_id in known, f"{action.dest_id} is not a rail destination"


def _every_home_status() -> list[HomeStatus]:
    """Every ``HomeStatus`` the module's rules can produce over a spread of inputs."""
    missing = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
    records = [
        None,
        [],
        [_record()],
        [_record(status="failed")],
        [_record(sftp_attempted=True, sftp_ok=False)],
        [_record(anomalies=["ANOMALY: x"])],
        [_record(data_errors={"total": 2})],
        [_record(timestamp=_OLD)],
    ]
    out: list[HomeStatus] = []
    for schedule in (None, _live_schedule(), missing):
        for recs in records:
            out.append(derive_home_status(recs, _CONFIGURED, now=_NOW, schedule_status=schedule))
    return out


class TestPrivacyNoErrorLeak:
    _SECRET = r"C:\Users\x\secret"

    def test_failed_record_error_path_never_leaks(self) -> None:
        # The free-text `error` (str(e), can carry a path/sis_type) must NEVER appear
        # in the admin-facing headline/detail. Category-only fault naming.
        status = _derive(_record(status="failed", error=f"FileNotFoundError: {self._SECRET}\\input.csv"))
        assert self._SECRET not in status.detail
        assert self._SECRET not in status.headline
        assert "secret" not in status.detail
        # The FIXED category sentence — only the record's own timestamp is rendered (plain phrase).
        assert status.detail == "The sync that ran 5 hours ago hit a problem and didn't finish."

    def test_secret_never_leaks_across_any_rule(self) -> None:
        # Even on delivered/anomaly/data-error rules where `error` may be populated, it never leaks.
        for extra in (
            {"status": "failed"},
            {"sftp_attempted": True, "sftp_ok": False},
            {"anomalies": ["ANOMALY: x"]},
            {"data_errors": {"total": 2}},
        ):
            status = _derive(_record(error=self._SECRET, **extra))
            assert self._SECRET not in status.detail
            assert self._SECRET not in status.headline


class TestTotalityPartialRecords:
    def test_missing_keys_do_not_raise(self) -> None:
        # A partial/old-schema record (only a status) classifies via .get defaults, no KeyError.
        status = derive_home_status([{"status": "success"}], _CONFIGURED, now=_NOW)
        assert isinstance(status, HomeStatus)
        assert status.verdict in Verdict

    def test_empty_record_dict_does_not_raise(self) -> None:
        status = derive_home_status([{}], _CONFIGURED, now=_NOW)
        assert isinstance(status, HomeStatus)

    def test_unparseable_timestamp_skips_staleness_still_classifies(self) -> None:
        # A clean delivered success with a garbage timestamp → staleness skipped → HEALTHY, no crash.
        status = _derive(_record(timestamp="garbage-timestamp"))
        assert status.verdict is Verdict.HEALTHY

    def test_garbage_count_values_do_not_crash_metrics(self) -> None:
        status = _derive(_record(Students="not-a-number", CourseInfo=None))
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 0


class TestClassifyLatestReason:
    """The shared single-source status→reason precedence IA-6 also consumes (staleness EXCLUDED)."""

    def test_failed_etl(self) -> None:
        assert classify_latest_reason(_record(status="failed")) is LatestReason.FAILED_ETL

    def test_missing_status_is_failed_etl(self) -> None:
        assert classify_latest_reason({}) is LatestReason.FAILED_ETL

    def test_failed_delivery(self) -> None:
        assert classify_latest_reason(_record(sftp_attempted=True, sftp_ok=False)) is LatestReason.FAILED_DELIVERY

    def test_anomaly(self) -> None:
        assert classify_latest_reason(_record(anomalies=["ANOMALY: x"])) is LatestReason.ANOMALY

    def test_data_warnings(self) -> None:
        assert classify_latest_reason(_record(data_errors={"total": 3})) is LatestReason.DATA_WARNINGS

    def test_clean(self) -> None:
        assert classify_latest_reason(_record()) is LatestReason.CLEAN

    def test_precedence_failed_over_all(self) -> None:
        rec = _record(
            status="failed", sftp_attempted=True, sftp_ok=False, anomalies=["ANOMALY: y"], data_errors={"total": 9}
        )
        assert classify_latest_reason(rec) is LatestReason.FAILED_ETL

    def test_staleness_is_not_a_reason(self) -> None:
        # A stale-but-clean record is still CLEAN — staleness is a separate axis, not a reason.
        assert classify_latest_reason(_record(timestamp=_OLD)) is LatestReason.CLEAN


class TestVerdictForReason:
    def test_total_over_every_reason(self) -> None:
        for reason in LatestReason:
            assert verdict_for_reason(reason) in Verdict

    def test_reason_verdict_mapping(self) -> None:
        assert verdict_for_reason(LatestReason.FAILED_ETL) is Verdict.FAILED
        assert verdict_for_reason(LatestReason.FAILED_DELIVERY) is Verdict.FAILED
        assert verdict_for_reason(LatestReason.ANOMALY) is Verdict.WARNING
        assert verdict_for_reason(LatestReason.DATA_WARNINGS) is Verdict.WARNING
        assert verdict_for_reason(LatestReason.CLEAN) is Verdict.HEALTHY


def _delivery_record(**overrides: object) -> dict:
    """A deliver-from-disk record (0034 Slice 2): zero count keys by shape + the rider."""
    base = _record(
        Students=0,
        Staff=0,
        Family=0,
        Classes=0,
        Enrollments=0,
        delivery_only=True,
        source="manual",
    )
    base.update(overrides)
    return base


class TestDeliveryOnlyRecord:
    """A delivery ships an EARLIER build — its record must never read as a 0-row build."""

    def test_rider_discriminates_delivery_from_build(self) -> None:
        assert is_delivery_only(_delivery_record()) is True
        assert is_delivery_only(_record()) is False  # pre-existing records classify as builds

    def test_clean_delivery_refreshes_freshness_with_the_builds_counts(self) -> None:
        # The build is past the stale window, but the delivery re-dates the sync (the roster
        # genuinely reached SpacesEDU) — and the tiles show the BUILD's counts, never zeros.
        build = _record(timestamp=_OLD)
        delivery = _delivery_record(timestamp=_RECENT)
        status = derive_home_status([delivery, build], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100
        assert status.metrics.last_run_display == "5 hours ago"  # the delivery's timestamp
        assert status.metrics.sftp_delivered is True

    def test_delivery_with_no_build_on_record_shows_no_tiles(self) -> None:
        # No build record to source counts from → no tiles at all — never a "0 Students" lie.
        status = derive_home_status([_delivery_record()], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is None

    def test_delivery_over_failed_build_uses_the_successful_builds_counts(self) -> None:
        # A failed build (zero counts — atomic save_all rolled back, nothing committed)
        # sitting between the good build and the delivery must never feed the HEALTHY
        # tiles: the delivery shipped the GOOD build's on-disk CSVs.
        good = _record(timestamp=_OLD)
        failed = _record(status="failed", timestamp=_OLD, Students=0, Staff=0, Family=0, Classes=0, Enrollments=0)
        delivery = _delivery_record(timestamp=_RECENT)
        status = derive_home_status([delivery, failed, good], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100

    def test_delivery_with_only_failed_builds_shows_no_tiles(self) -> None:
        # No SUCCESSFUL build on record → no honest count exists → no tiles, never zeros.
        failed = _record(status="failed", timestamp=_OLD, Students=0, Staff=0, Family=0, Classes=0, Enrollments=0)
        status = derive_home_status([_delivery_record(), failed], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.HEALTHY
        assert status.metrics is None

    def test_failed_delivery_only_is_the_failed_delivery_verdict(self) -> None:
        status = derive_home_status([_delivery_record(sftp_ok=False)], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.FAILED
        assert "didn't reach SpacesEDU" in status.headline
        # Delivery-only failure built nothing this run — the copy must not claim a build.
        assert status.detail == "The upload of your saved files failed."

    def test_failed_delivery_after_a_build_keeps_the_build_copy(self) -> None:
        status = derive_home_status([_record(sftp_ok=False)], _CONFIGURED, now=_NOW)
        assert status.verdict is Verdict.FAILED
        assert status.detail == "The data was built but the upload failed."

    def test_build_latest_keeps_its_own_counts(self) -> None:
        # Regression: a build latest is its own counts source (delivery records behind it).
        status = derive_home_status([_record(), _delivery_record(timestamp=_OLD)], _CONFIGURED, now=_NOW)
        assert status.metrics is not None
        assert status.metrics.entity_counts["Students"] == 100


# Every representative fixture → a valid HomeStatus, no exception (totality sweep).
_SWEEP_INPUTS = [
    None,
    [],
    [_record()],
    [_record(status="failed", error=r"boom C:\path\x")],
    [_record(sftp_attempted=True, sftp_ok=False)],
    [_record(anomalies=["ANOMALY: a"])],
    [_record(data_errors={"total": 5})],
    [_record(timestamp=_OLD)],
    [_record(timestamp="garbage")],
    [{}],
    [{"status": "success", "sftp_attempted": True}],
    [{"anomalies": "not-a-list"}],  # non-list anomalies must be tolerated
    [_delivery_record()],
    [_delivery_record(sftp_ok=False)],
]


@pytest.mark.parametrize("records", _SWEEP_INPUTS)
def test_derivation_is_total_over_all_inputs(records: list[dict] | None) -> None:
    status = derive_home_status(records, _CONFIGURED, now=_NOW)
    assert isinstance(status, HomeStatus)
    assert status.verdict in Verdict
    assert status.headline and status.detail


# --------------------------------------------------------------------------- #
# The first-run welcome band (0038 S6) — the line above the hosted wizard.     #
#                                                                             #
# Written BEFORE the implementation (the charter's recorded approach for a     #
# pure predicate whose semantics the spec DECIDES), so every judgment call —   #
# what counts as "history", which of two reassurances is honest, what an       #
# UNREADABLE store means — is stated as a decision rather than described from  #
# the code afterwards.                                                         #
# --------------------------------------------------------------------------- #
class TestPriorRunsSignal:
    """What counts as "this install has been running" — the band's one input."""

    def test_no_records_and_no_store_is_a_fresh_install(self) -> None:
        assert has_prior_runs([], store_created_at=None) is False

    def test_a_record_is_history(self) -> None:
        assert has_prior_runs([_record()], store_created_at=None) is True

    def test_a_store_that_exists_but_holds_nothing_is_still_history(self) -> None:
        """``write_run_record`` is the store's sole creator, so a birth stamp means a run
        was recorded at some point — even if the rows were later lost to a quarantine."""
        assert has_prior_runs([], store_created_at="2026-01-05T03:00:00") is True

    def test_an_UNREADABLE_store_is_treated_as_history_never_as_fresh(self) -> None:
        """``read_run_records`` returns ``None`` when a store file exists but could not be
        read. A file we failed to read is a CHECKED fact that this install is not new —
        the same honesty ``settings_unreadable`` applies to the settings file. Saying
        "Welcome" over it is the one direction that can be wrong about a year of runs."""
        assert has_prior_runs(None, store_created_at=None) is True


def _all_band_lines() -> tuple[str, ...]:
    """Every welcome-band constant the module DEFINES, read off the module itself.

    A reality-read rather than a hand-kept list: the two copy sweeps below must cover a
    variant the moment it exists, and this file has already shipped one they missed.
    """
    return tuple(
        value for name, value in vars(home_status_mod).items() if name.startswith("WELCOME_") and isinstance(value, str)
    )


class TestWelcomeBandLine:
    """Fresh installs are welcomed; running ones are never greeted as new."""

    def test_a_genuinely_fresh_install_is_welcomed(self) -> None:
        line = welcome_band_line(has_run_history=False, has_saved_choices=False, run_history_readable=True)
        assert line == WELCOME_FRESH
        assert "Welcome" in line

    def test_run_history_switches_the_band_to_finish_setting_up(self) -> None:
        line = welcome_band_line(has_run_history=True, has_saved_choices=True, run_history_readable=True)
        assert line == WELCOME_RESUME_WITH_HISTORY
        assert "Welcome" not in line, "a year of run records must never be greeted as a new install"
        assert "run history" in line

    def test_saved_choices_alone_never_claims_a_run_history_that_does_not_exist(self) -> None:
        """The half-configured install: settings on disk, nothing ever run. Reassuring it
        that "your run history is safe" would assert a thing we know is absent."""
        line = welcome_band_line(has_run_history=False, has_saved_choices=True, run_history_readable=True)
        assert line == WELCOME_RESUME_SETTINGS_ONLY
        assert "Welcome" not in line
        assert "run history" not in line

    def test_history_outranks_the_absence_of_saved_choices(self) -> None:
        """A settings file wiped clean under a populated run store still isn't new."""
        line = welcome_band_line(has_run_history=True, has_saved_choices=False, run_history_readable=True)
        assert line == WELCOME_RESUME_WITH_HISTORY

    def test_an_UNREADABLE_store_is_not_new_but_is_promised_NOTHING(self) -> None:
        """The two questions come apart here, which is why they are separate parameters.

        The install is established (a store file exists), so "Welcome" would be false — but
        we could not OPEN that store, so "your run history is safe" is a promise about a
        thing we cannot see. And nothing was entered either — so the settings-only line
        would name the entered settings on an install this call has POSITIVELY CHECKED has
        none, which is the same over-claim pointed at the other artefact. This arm gets the
        line that reassures about nothing at all.
        """
        line = welcome_band_line(has_run_history=True, has_saved_choices=False, run_history_readable=False)
        assert line == WELCOME_RESUME_PLAIN
        assert "Welcome" not in line
        assert "run history" not in line
        assert "entered" not in line, "the band named settings it just checked were absent"

    def test_an_UNREADABLE_store_WITH_saved_choices_keeps_the_settings_only_line(self) -> None:
        """The twin of the row above: same unreadable store, but there IS something on disk
        to point at, so the settings-only line is honest here and must not be lost."""
        line = welcome_band_line(has_run_history=True, has_saved_choices=True, run_history_readable=False)
        assert line == WELCOME_RESUME_SETTINGS_ONLY
        assert "run history" not in line

    def test_no_band_line_carries_a_step_count(self) -> None:
        """The band sits one line above the wizard's own "Step 1 of 5" indicator.

        Two counts on one screen contradict each other the moment either moves — and the
        5→4 "Finish unnumbered" question is open on the ROADMAP, so one of them will. The
        indicator owns the count; the band owns the reassurance.
        """
        for line in _all_band_lines():
            lowered = line.lower()
            for count in ("four", "five", "step", "4 ", "5 "):
                assert count not in lowered, f"the band names a step count: {line!r}"

    def test_every_line_is_one_calm_sentence_with_no_banned_vocabulary(self) -> None:
        for line in _all_band_lines():
            assert line == line.strip() and line.endswith(".")
            lowered = line.lower()
            for banned in ("sign in", "log in", "verify", "unlock", "authorized", "account", "credentials"):
                assert banned not in lowered

    def test_the_sweeps_above_see_every_band_constant_the_module_defines(self) -> None:
        """The sweeps read the MODULE, not a hand-kept list — pinned in both directions.

        A hand-listed loop is how a fifth variant ships unswept (this file already shipped
        a fourth that the two loops above did not cover). ``_all_band_lines`` derives from
        ``vars(home_status)``, so a new constant is swept the moment it is defined; this row
        is the other direction — the four we know about must still be in there, so a rename
        or a deletion cannot quietly shrink the sweep to nothing.
        """
        swept = _all_band_lines()
        assert set(swept) >= {
            WELCOME_FRESH,
            WELCOME_RESUME_WITH_HISTORY,
            WELCOME_RESUME_SETTINGS_ONLY,
            WELCOME_RESUME_PLAIN,
        }
        assert len(swept) == len(set(swept)), "two band constants hold the same string"


class TestWelcomeBandOverAConfig:
    """The AppConfig-facing wrapper — the ONE call Home makes."""

    def test_a_blank_profile_with_an_empty_store_is_welcomed(self) -> None:
        assert welcome_band(AppConfig(), records=[], store_created_at=None) == WELCOME_FRESH

    def test_a_manual_only_upgrader_is_told_to_finish_setting_up(self) -> None:
        """Upgrade shape 2: complete + never scheduled, with real runs behind it."""
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=False)
        assert cfg.has_completed_setup() is False  # it genuinely lands in the wizard host
        assert welcome_band(cfg, records=[_record()], store_created_at=None) == WELCOME_RESUME_WITH_HISTORY

    def test_a_half_finished_wizard_with_no_runs_is_not_welcomed_either(self) -> None:
        cfg = AppConfig(sis_type="myedbc")
        assert welcome_band(cfg, records=[], store_created_at=None) == WELCOME_RESUME_SETTINGS_ONLY

    def test_an_input_folder_ALONE_is_a_saved_choice(self) -> None:
        """A wizard abandoned on the Folders step, before a district was picked.

        Written after a falsification probe: narrowing ``_has_saved_choices`` to
        ``sis_type`` alone left the whole suite green, because every existing row happened
        to carry a district. Each arm of an OR needs its own row.
        """
        assert welcome_band(AppConfig(input_dir="/in"), records=[], store_created_at=None) == (
            WELCOME_RESUME_SETTINGS_ONLY
        )

    def test_an_output_folder_ALONE_is_a_saved_choice(self) -> None:
        assert welcome_band(AppConfig(output_dir="/out"), records=[], store_created_at=None) == (
            WELCOME_RESUME_SETTINGS_ONLY
        )

    def test_a_district_ALONE_is_a_saved_choice(self) -> None:
        assert welcome_band(AppConfig(sis_type="myedbc"), records=[], store_created_at=None) == (
            WELCOME_RESUME_SETTINGS_ONLY
        )

    def test_advisory_state_alone_does_not_count_as_a_saved_choice(self) -> None:
        """Window geometry and the identity answer are not setup progress — an install
        that only answered the launch page is still a fresh install to the wizard."""
        cfg = AppConfig(identity_email="admin@sd48.bc.ca", window_width=1200.0)
        assert welcome_band(cfg, records=[], store_created_at=None) == WELCOME_FRESH

    def test_a_store_stamp_with_no_rows_still_reads_as_history(self) -> None:
        """The quarantine-recreated store: `write_run_record` made it, so runs HAPPENED,
        but the rows were lost. ``store_created_at`` is the only surviving evidence — and
        it is a SUPPLY the view must actually pass (see the wizard-host test of the same
        name, added after dropping it here left 364 tests green)."""
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc")
        stamped = welcome_band(cfg, records=[], store_created_at="2026-01-05T03:00:00")
        assert stamped == WELCOME_RESUME_WITH_HISTORY

    def test_an_unreadable_store_gets_the_settings_only_line(self) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc")
        assert welcome_band(cfg, records=None, store_created_at=None) == WELCOME_RESUME_SETTINGS_ONLY

    def test_an_unreadable_store_over_a_BLANK_profile_names_nothing_at_all(self) -> None:
        """The real config that reaches the plain line, not just the parameter triple.

        A readable-but-blank ``config.json`` — exactly what the launch page's identity-only
        save leaves behind — beside a ``history.db`` that exists and will not open. It is
        established (the file is a checked fact) but there is nothing on disk we can name,
        so the band must promise neither the run history nor the settings.
        """
        blank = AppConfig(identity_email="admin@sd48.bc.ca")
        assert home_status_mod._has_saved_choices(blank) is False, "the fixture is not the blank profile"
        assert welcome_band(blank, records=None, store_created_at=None) == WELCOME_RESUME_PLAIN
