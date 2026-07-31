"""Home and Run History can never classify the SAME run differently (0038 S7).

The slice's headline promise, made programmatic. ``derive_home_status`` (Home),
``derive_history_banner`` (the Run History banner) and ``to_run_row`` (its per-run row) all
answer "did this run work?" over the same record, through the same
``classify_latest_reason`` → ``verdict_for_reason`` spine. Two narrower agreement tests
already existed (``test_ui_flet_run_history.py``: banner↔row within Run History, and
Home↔banner on the two FAILED reasons × three schedule flavours); **both are kept** — the
schedule-flavour axis and the row axis live there. This file adds what neither had: a sweep
driven off the ``LatestReason`` ENUM ITSELF, so a sixth reason cannot ship unswept.

**What a violation looks like** — the real W3-B incident: Home's schedule-attention rule
returned ABOVE its two FAILED rules, so a failed latest under an expected-MISSING schedule
read amber-and-schedule on Home while Run History read red-and-failed. Same record, two
answers, and the surface an admin trusts most was the one that downplayed it.

**The two legal escapes, ENCODED rather than papered over.**

1. *Staleness is a separate axis layered on CLEAN.* A clean run that is merely OLD makes both
   BANNERS amber while the ROW stays HEALTHY — already documented and allowed. The strict
   sweep therefore uses recent timestamps, and ``TestTheStalenessEscape`` pins the escape
   itself in both directions (the two banners still agree with each other).
2. *Home has rules Run History does not* — schedule-attention and missed-run, both
   WARNING-tier and both gated BELOW a FAILED latest. So on a CLEAN latest Home may legally
   read amber where the banner reads green. ``TestStrictAgreement`` holds those quiet by
   construction (the ``_QUIET_SCHEDULES`` flavours plus a window-free config — none of them
   trips either rule) and asserts EQUALITY; ``TestNoSeverityInversion`` then re-runs the whole
   cross-product WITH the two attention flavours added and asserts the weaker property that
   survives them: never a green-vs-red inversion, and FAILED on one surface iff FAILED on the
   other. Choosing only the weaker one everywhere would leave the quiet flavours unswept;
   choosing only the stricter one would have required pretending Home's extra rules do not
   exist.

**What this file protects, stated exactly: VERDICTS, not copy.** On the RECORD axis the two
surfaces deliberately word the same classification differently ("Last sync failed" vs "Your
last sync failed"), so headline equality is not merely unasserted — it is false by design, and
the strict sweep asserts the strongest property that is actually true there. Only the EMPTY
state single-sources its headlines, which is why ``TestTheEmptyStoreAgreement`` *can* and does
assert copy equality. An earlier version of this docstring claimed the strict sweep caught
"copy-level divergence"; it never reached copy level on the record axis, and a detail-level
falsehood shipped past it green. The honest split is recorded here rather than papered over.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet import home_status as home_status_mod
from src.ui_flet.home_status import (
    STALE_AFTER_HOURS,
    LatestReason,
    classify_latest_reason,
    derive_home_status,
    verdict_for_reason,
)
from src.ui_flet.run_history import derive_history_banner, to_run_row
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus, derive_schedule_status
from src.ui_flet.verdict import Verdict

_NOW = datetime(2026, 7, 4, 8, 0, 0)
_RECENT = (_NOW - timedelta(hours=5)).isoformat(timespec="seconds")
_OLD = (_NOW - timedelta(hours=STALE_AFTER_HOURS + 5)).isoformat(timespec="seconds")

# No seasonal window (the pause rule is a THIRD shared branch with its own pinned tests) and
# no store stamp, so the empty-state and missed-run rules stay out of the record sweep.
_CONFIGURED = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)

_LIVE = ScheduleStatus(
    state=ScheduleState.LIVE,
    headline="Nightly sync is scheduled",
    detail="registered",
    next_run_display="3:00 AM",
)
_EXPECTED_MISSING = derive_schedule_status(ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None)
_CONTRADICTION = derive_schedule_status(
    ScheduleReadback(found=True, last_run="2026-07-04T04:00:00"),
    hint_registered=True,
    latest_record_ts=_RECENT,
)
# MISSING but NOT ``attention``: the task is confirmed absent and the config never promised
# one (a manual-only district). This is the read-back the honest "won't sync automatically"
# copy keys on — and, unlike the two above, it does NOT trip Home's schedule-attention rule.
_UNEXPECTED_MISSING = derive_schedule_status(
    ScheduleReadback(found=False), hint_registered=False, latest_record_ts=None
)

# Flavours that leave Home's two extra rules silent — the ones the STRICT sweeps may use.
_QUIET_SCHEDULES: dict[str, ScheduleStatus | None] = {
    "unprobed": None,
    "live": _LIVE,
    "unexpected-missing": _UNEXPECTED_MISSING,
}
# Every flavour, including the two that DO trip Home's schedule-attention rule.
_SCHEDULES: dict[str, ScheduleStatus | None] = {
    **_QUIET_SCHEDULES,
    "expected-missing": _EXPECTED_MISSING,
    "contradiction": _CONTRADICTION,
}


def _base_record(**overrides: object) -> dict:
    record: dict = {
        "timestamp": _RECENT,
        "status": "success",
        "duration_s": 3.1,
        "Students": 100,
        "Staff": 12,
        "Family": 80,
        "Classes": 40,
        "Enrollments": 300,
        "sftp_attempted": True,
        "sftp_ok": True,
        "error": "",
        "anomalies": [],
        "data_errors": {},
    }
    record.update(overrides)
    return record


# One record PER REASON, keyed by the enum member it is meant to classify to. The keying is
# checked below — a record filed under the wrong reason would make every row that uses it
# assert agreement about a state it never reached.
_RECORD_FOR_REASON: dict[LatestReason, dict] = {
    LatestReason.FAILED_ETL: _base_record(status="failed", error="FileNotFoundError: /x/y.csv"),
    LatestReason.FAILED_DELIVERY: _base_record(sftp_attempted=True, sftp_ok=False),
    LatestReason.ANOMALY: _base_record(anomalies=["ANOMALY: Students dropped 42%"]),
    LatestReason.DATA_WARNINGS: _base_record(data_errors={"total": 3, "by_field": {"Grade": 3}}),
    LatestReason.CLEAN: _base_record(),
}

_REASONS = list(LatestReason)


class TestTheSweepIsNotVacuous:
    def test_every_reason_in_the_enum_has_a_record(self) -> None:
        """Driven off the ENUM, not a hand-listed set — a sixth reason fails here first."""
        assert set(_RECORD_FOR_REASON) == set(LatestReason)

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    def test_each_record_actually_classifies_to_the_reason_it_is_filed_under(self, reason: LatestReason) -> None:
        assert classify_latest_reason(_RECORD_FOR_REASON[reason]) is reason

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    def test_each_record_is_recent_so_the_staleness_axis_stays_out(self, reason: LatestReason) -> None:
        """The strict sweep's precondition, asserted rather than assumed.

        A record that had drifted stale would make Home and the banner BOTH read "No recent
        sync" — agreement, but about the wrong rule, and the reason under test would never be
        exercised at all.
        """
        assert home_status_mod.is_stale(str(_RECORD_FOR_REASON[reason]["timestamp"]), _NOW) is False


class TestStrictAgreement:
    """Same record, same inputs, Home's extra rules held quiet → the three verdicts are EQUAL."""

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    @pytest.mark.parametrize("schedule_id", sorted(_QUIET_SCHEDULES), ids=sorted(_QUIET_SCHEDULES))
    def test_home_banner_and_row_all_carry_the_reasons_verdict(self, reason: LatestReason, schedule_id: str) -> None:
        """Every QUIET flavour, not just ``None`` (Stage-7 SHOULD 1).

        ``_QUIET_SCHEDULES`` was defined here and consumed ONLY by the empty-store sweep below
        — a defined-but-unused constant, and the tell that the strict sweep was hard-coding one
        of its three members. The gap was live: perturbing ``run_history``'s CLEAN branch to
        branch on a confirmed-MISSING read-back produced a real Home-HEALTHY-vs-banner-WARNING
        divergence under ``unexpected-missing`` with the whole suite green. All three flavours
        are quiet BY CONSTRUCTION (none carries ``attention``, and the missed-run rule needs a
        LIVE read-back over an established store, which ``_CONFIGURED`` has no stamp for), so
        EQUALITY remains the right assertion across the widened axis.
        """
        record = _RECORD_FOR_REASON[reason]
        expected = verdict_for_reason(reason)
        schedule = _QUIET_SCHEDULES[schedule_id]

        home = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        row = to_run_row(record, now=_NOW)

        assert home.verdict is expected, f"Home disagreed with {reason} under a {schedule_id} schedule"
        assert banner.verdict is expected, f"the banner disagreed with {reason} under a {schedule_id} schedule"
        assert row.status_verdict is expected, f"the row disagreed with {reason}"

    def test_the_quiet_flavours_really_are_quiet(self) -> None:
        """Non-vacuity guard for the widened axis (the twin of ``_the_schedule_axis_actually_moves_home``).

        The equality above is only meaningful while every ``_QUIET_SCHEDULES`` member leaves
        Home's two extra rules silent. If one ever starts tripping ``attention``, this row says
        so directly instead of the whole sweep turning red for an unclear reason.
        """
        for schedule_id, schedule in _QUIET_SCHEDULES.items():
            assert schedule is None or not schedule.attention, f"{schedule_id} is no longer a quiet flavour"

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    def test_neither_surface_leaks_the_free_text_error(self, reason: LatestReason) -> None:
        """Agreement is worthless if the two surfaces agree on leaking a path (privacy, LIVE/top)."""
        record = dict(_RECORD_FOR_REASON[reason], error=r"C:\Users\x\secret\input.csv")
        home = derive_home_status([record], _CONFIGURED, now=_NOW)
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW)
        for text in (home.headline, home.detail, banner.headline, banner.detail):
            assert "secret" not in text


class TestNoSeverityInversion:
    """The property that survives Home's schedule-attention and missed-run rules.

    Those two are WARNING-tier and Home-only, so on a non-failed latest Home may legally be
    amber where the banner is green. What may NEVER happen is an inversion — one surface green
    while the other is red — or a failure appearing on one surface and not the other. That is
    exactly the W3-B incident, and it is asserted across every reason × every schedule flavour.
    """

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    @pytest.mark.parametrize("schedule_id", sorted(_SCHEDULES), ids=sorted(_SCHEDULES))
    def test_failed_on_one_surface_is_failed_on_the_other(self, reason: LatestReason, schedule_id: str) -> None:
        record = _RECORD_FOR_REASON[reason]
        schedule = _SCHEDULES[schedule_id]

        home = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW, schedule_status=schedule)

        assert (home.verdict is Verdict.FAILED) == (banner.verdict is Verdict.FAILED), (
            f"{reason} under a {schedule_id} schedule: Home={home.verdict} banner={banner.verdict}"
        )

    @pytest.mark.parametrize("reason", _REASONS, ids=lambda r: r.value)
    @pytest.mark.parametrize("schedule_id", sorted(_SCHEDULES), ids=sorted(_SCHEDULES))
    def test_never_green_on_one_surface_and_red_on_the_other(self, reason: LatestReason, schedule_id: str) -> None:
        record = _RECORD_FOR_REASON[reason]
        schedule = _SCHEDULES[schedule_id]

        home = derive_home_status([record], _CONFIGURED, now=_NOW, schedule_status=schedule)
        banner = derive_history_banner([record], _CONFIGURED, now=_NOW, schedule_status=schedule)

        inverted = {home.verdict, banner.verdict} == {Verdict.HEALTHY, Verdict.FAILED}
        assert not inverted, f"{reason} under a {schedule_id} schedule inverted the severity"

    def test_the_schedule_axis_actually_moves_home(self) -> None:
        """Non-vacuity guard for the two rows above.

        If the schedule flavours never changed Home's verdict, the whole cross-product would
        be four copies of the strict sweep. This asserts the axis is live: a CLEAN latest under
        an expected-MISSING schedule IS the amber Home-only rule the escape is written for.
        """
        clean = _RECORD_FOR_REASON[LatestReason.CLEAN]
        quiet = derive_home_status([clean], _CONFIGURED, now=_NOW, schedule_status=None)
        attention = derive_home_status([clean], _CONFIGURED, now=_NOW, schedule_status=_EXPECTED_MISSING)
        assert quiet.verdict is Verdict.HEALTHY
        assert attention.verdict is Verdict.WARNING
        assert derive_history_banner([clean], _CONFIGURED, now=_NOW, schedule_status=_EXPECTED_MISSING).verdict is (
            Verdict.HEALTHY
        ), "the banner is supposed to have no schedule rule — the escape being encoded is real"


class TestTheStalenessEscape:
    """Escape 1, pinned in both directions rather than assumed away."""

    def test_a_stale_clean_run_is_amber_on_BOTH_banners_and_healthy_on_the_row(self) -> None:
        stale = _base_record(timestamp=_OLD)
        assert classify_latest_reason(stale) is LatestReason.CLEAN

        home = derive_home_status([stale], _CONFIGURED, now=_NOW, schedule_status=None)
        banner = derive_history_banner([stale], _CONFIGURED, now=_NOW, schedule_status=None)
        row = to_run_row(stale, now=_NOW)

        assert home.verdict is Verdict.WARNING
        assert banner.verdict is Verdict.WARNING
        assert home.verdict is banner.verdict, "the staleness axis must move BOTH banners together"
        assert row.status_verdict is Verdict.HEALTHY  # the documented, allowed difference

    def test_staleness_never_downgrades_a_failure_on_either_surface(self) -> None:
        stale_failure = _base_record(timestamp=_OLD, status="failed")
        home = derive_home_status([stale_failure], _CONFIGURED, now=_NOW)
        banner = derive_history_banner([stale_failure], _CONFIGURED, now=_NOW)
        assert home.verdict is Verdict.FAILED
        assert banner.verdict is Verdict.FAILED


class TestTheEmptyStoreAgreement:
    """The state 0038 S7 part (i) rewired — swept because it is where the two surfaces DID drift.

    The discriminator and both headlines were duplicated byte-for-byte across the two modules,
    with nothing asserting the pair. Correcting Home alone would have made the flagship surface
    say "no sync has run yet" while Run History, one click away, said "run history starts fresh
    here" about the same install.
    """

    _STAMP = _RECENT

    @pytest.mark.parametrize("store_created_at", [None, _STAMP], ids=["no-store", "store-exists"])
    @pytest.mark.parametrize("setup_completed", [False, True], ids=["setup-unfinished", "setup-finished"])
    @pytest.mark.parametrize("schedule_id", sorted(_QUIET_SCHEDULES), ids=sorted(_QUIET_SCHEDULES))
    def test_both_surfaces_read_the_same_empty_state(
        self, store_created_at: str | None, setup_completed: bool, schedule_id: str
    ) -> None:
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=setup_completed)
        schedule = _QUIET_SCHEDULES[schedule_id]
        home = derive_home_status([], cfg, now=_NOW, store_created_at=store_created_at, schedule_status=schedule)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=store_created_at, schedule_status=schedule)
        assert home.verdict is banner.verdict
        assert home.headline == banner.headline, (
            f"store={store_created_at!r} setup={setup_completed} schedule={schedule_id}"
        )

    @pytest.mark.parametrize("store_created_at", [None, _STAMP], ids=["no-store", "store-exists"])
    @pytest.mark.parametrize("schedule_id", ["expected-missing", "contradiction"])
    def test_an_attention_schedule_over_an_empty_store_is_the_home_only_escape(
        self, store_created_at: str | None, schedule_id: str
    ) -> None:
        """Escape 2 again, on the EMPTY state — found by this sweep, not assumed.

        Home's schedule-attention rule returns ABOVE the empty branch, so on an
        expected-MISSING / fired-but-no-record read-back Home names the schedule fault while
        Run History (which has no such rule) shows its empty-state banner. That is the
        documented asymmetry, not a drift — but it is only legal while neither surface
        inverts, so the weaker property is asserted here rather than the equality above.
        """
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        schedule = _SCHEDULES[schedule_id]
        home = derive_home_status([], cfg, now=_NOW, store_created_at=store_created_at, schedule_status=schedule)
        banner = derive_history_banner([], cfg, now=_NOW, store_created_at=store_created_at, schedule_status=schedule)
        assert home.verdict is Verdict.WARNING
        assert banner.verdict is Verdict.WARNING
        assert home.fix is not None and home.fix.dest_id == "setup", (
            "the escape is only defensible because Home ROUTES the fault it names"
        )
        assert home.headline != banner.headline, (
            "if these now match, the asymmetry closed — fold this state back into the strict sweep"
        )

    def test_the_empty_sweep_actually_reaches_both_headlines(self) -> None:
        """Non-vacuity: if every row produced one headline the equality above proves nothing."""
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        headlines = {
            derive_home_status([], cfg, now=_NOW, store_created_at=stamp, schedule_status=None).headline
            for stamp in (None, self._STAMP)
        }
        assert headlines == {
            home_status_mod.EMPTY_FRESH_START_HEADLINE,
            home_status_mod.EMPTY_NO_RUNS_HEADLINE,
        }

    def test_the_shared_no_automation_sentence_is_ONE_string(self) -> None:
        """Both surfaces render the same constant — not two hand-kept copies of one sentence."""
        cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
        home = derive_home_status([], cfg, now=_NOW, schedule_status=_UNEXPECTED_MISSING)
        banner = derive_history_banner([], cfg, now=_NOW, schedule_status=_UNEXPECTED_MISSING)
        assert home.detail == home_status_mod.EMPTY_NO_AUTO_SYNC_DETAIL
        assert banner.detail == home_status_mod.EMPTY_NO_AUTO_SYNC_DETAIL
