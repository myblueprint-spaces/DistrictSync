"""Tests for src/ui_flet/handover_result.py — the one-shot post-handover slot (0049 S-2b.3).

Three concerns:

* :func:`~src.ui_flet.handover_result.compose` — the JOIN of what the elevated child
  reported and what the parent's own switch read says. Swept over every
  ``ProvisionOutcome`` so a new one cannot quietly land on the wrong banner, with the
  "switch committed ∧ the nightly did not" row asserted explicitly: it is the row where a
  stock red card would tell an admin nothing happened after something irreversible did.
* the ONE-SHOT contract — ``take()`` reads and clears, so re-entry renders the report once
  and a later navigation to Home never re-announces a change that happened days ago.
* that the slot is in MEMORY — nothing is written anywhere, twinned with a positive that
  proves the slot works at all.

Pure: no flet, no elevated child, no UAC prompt.
"""

from __future__ import annotations

import threading

import pytest

from src.scheduler.provision_session import HandoverOutcome, ProvisionAttempt, ProvisionOutcome
from src.ui_flet import handover_result, launcher
from src.ui_flet.handover_result import HandoverBanner, HandoverResult, compose
from src.utils.paths import MachineScopeRefusedReason

_DETAIL = "The nightly sync couldn't be created — the account name wasn't recognised."


@pytest.fixture(autouse=True)
def _drain_the_slot():
    """A leaked slot would leak BETWEEN tests — this module's whole subject is a global."""
    handover_result.reset()
    yield
    handover_result.reset()


def _attempt(outcome: ProvisionOutcome) -> ProvisionAttempt:
    return ProvisionAttempt(outcome=outcome)


class TestCompose:
    def test_a_clean_handover(self) -> None:
        result = compose(
            _attempt(ProvisionOutcome.PROVISIONED),
            HandoverOutcome(handed_over=True, renamed=("config.json",), breadcrumb=True),
            nightly_detail="",
        )
        assert result == HandoverResult(banner=HandoverBanner.HANDED_OVER)

    def test_the_switch_committed_and_the_nightly_did_not(self) -> None:
        """The spec's row: the banner LEADS with the scope change and carries the
        registration failure second. A failed register may never paint its stock red card
        over a successful, irreversible handover — the admin would retry believing nothing
        happened."""
        result = compose(
            _attempt(ProvisionOutcome.FAILED),
            HandoverOutcome(handed_over=True),
            nightly_detail=_DETAIL,
        )
        assert result is not None
        assert result.banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED
        assert result.detail == _DETAIL

    def test_a_killed_child_that_committed_is_the_same_banner(self) -> None:
        """A timeout carries no message at all, and the child may have done everything —
        so the honest banner is the same one, with a detail that says "may or may not"."""
        result = compose(
            _attempt(ProvisionOutcome.UNCONFIRMED),
            HandoverOutcome(handed_over=True),
            nightly_detail="may or may not have been scheduled",
        )
        assert result is not None
        assert result.banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED

    @pytest.mark.parametrize("outcome", [o for o in ProvisionOutcome if o is not ProvisionOutcome.PROVISIONED])
    def test_every_non_success_over_a_committed_switch_leads_with_the_scope_change(
        self, outcome: ProvisionOutcome
    ) -> None:
        result = compose(_attempt(outcome), HandoverOutcome(handed_over=True), nightly_detail=_DETAIL)
        assert result is not None
        assert result.banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED

    def test_a_refused_repin_is_its_own_terminal_banner(self) -> None:
        result = compose(
            _attempt(ProvisionOutcome.PROVISIONED),
            HandoverOutcome(handed_over=False, refused=MachineScopeRefusedReason.OPEN_ACE),
            nightly_detail="",
        )
        assert result == HandoverResult(
            banner=HandoverBanner.REFUSED, refused_reason=MachineScopeRefusedReason.OPEN_ACE
        )

    def test_the_refusal_is_checked_before_handed_over(self) -> None:
        """``refused`` is set in a state where ``handed_over`` is False, so a rule that
        tested ``handed_over`` first would drop the ONE outcome that must not be dropped —
        the session whose next click can only crash. Asserted through the (constructible)
        contradictory shape, which is the only way to see the order at all."""
        result = compose(
            _attempt(ProvisionOutcome.FAILED),
            HandoverOutcome(handed_over=True, refused=MachineScopeRefusedReason.INACCESSIBLE),
            nightly_detail=_DETAIL,
        )
        assert result is not None
        assert result.banner is HandoverBanner.REFUSED

    @pytest.mark.parametrize("reason", list(MachineScopeRefusedReason))
    def test_every_refusal_reason_already_has_plain_language_copy(self, reason) -> None:
        """The REFUSED surface invents no copy: ``launcher._MACHINE_SCOPE_CAUSES`` owns one
        cause per member, and that map's own completeness test keeps it total."""
        result = compose(
            _attempt(ProvisionOutcome.UNAVAILABLE),
            HandoverOutcome(handed_over=False, refused=reason),
            nightly_detail="",
        )
        assert result is not None and result.refused_reason is not None
        assert launcher._MACHINE_SCOPE_CAUSES[result.refused_reason]

    @pytest.mark.parametrize("outcome", list(ProvisionOutcome))
    def test_nothing_irreversible_carries_nothing_across(self, outcome: ProvisionOutcome) -> None:
        """Switch off and no refusal -> ``None``: there was no re-entry to survive, Setup is
        still mounted, and its own result slot is the right place for the failure. A banner
        here would ambush the admin on Home about a change that never happened."""
        assert compose(_attempt(outcome), HandoverOutcome(handed_over=False), nightly_detail=_DETAIL) is None

    def test_nightly_detail_is_required_and_keyword_only(self) -> None:
        # A defaulted "" would ship a banner announcing a permanent change and saying
        # nothing about whether the nightly sync exists.
        with pytest.raises(TypeError):
            compose(_attempt(ProvisionOutcome.FAILED), HandoverOutcome(handed_over=True))  # type: ignore[call-arg]


class TestResultShapes:
    def test_the_three_factories_pair_a_banner_with_its_payload(self) -> None:
        assert HandoverResult.handed_over() == HandoverResult(banner=HandoverBanner.HANDED_OVER)
        assert HandoverResult.nightly_unconfirmed(_DETAIL).detail == _DETAIL
        assert HandoverResult.after_refusal(MachineScopeRefusedReason.MISSING).refused_reason is (
            MachineScopeRefusedReason.MISSING
        )

    def test_a_clean_handover_carries_no_failure_detail(self) -> None:
        clean = HandoverResult.handed_over()
        assert clean.detail == ""
        assert clean.refused_reason is None

    def test_no_banner_is_spelled_twice(self) -> None:
        values = [member.value for member in HandoverBanner]
        assert len(values) == len(set(values))


class TestOneShot:
    def test_remember_then_take(self) -> None:
        handover_result.remember(HandoverResult.handed_over())
        assert handover_result.take() == HandoverResult.handed_over()

    def test_take_clears_so_a_later_navigation_never_re_announces_it(self) -> None:
        handover_result.remember(HandoverResult.handed_over())
        assert handover_result.take() is not None
        assert handover_result.take() is None
        assert handover_result.take() is None

    def test_an_empty_slot_is_none_not_a_blank_banner(self) -> None:
        assert handover_result.take() is None

    def test_the_newer_report_wins(self) -> None:
        handover_result.remember(HandoverResult.handed_over())
        handover_result.remember(HandoverResult.nightly_unconfirmed(_DETAIL))
        taken = handover_result.take()
        assert taken is not None and taken.banner is HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED

    def test_reset_drops_a_pending_result(self) -> None:
        handover_result.remember(HandoverResult.handed_over())
        handover_result.reset()
        assert handover_result.take() is None

    def test_a_worker_thread_can_hand_a_result_to_the_ui_thread(self) -> None:
        """Setup dispatches the register through ``page.run_thread``, so ``remember`` runs
        off the UI thread while ``take`` runs on it — the reason the slot is locked."""
        worker = threading.Thread(target=lambda: handover_result.remember(HandoverResult.handed_over()))
        worker.start()
        worker.join()
        assert handover_result.take() == HandoverResult.handed_over()

    def test_exactly_one_of_many_takers_gets_the_result(self) -> None:
        """Read-and-clear is ONE locked step, so a rebuild racing a second dispatch cannot
        hand the same result to two surfaces."""
        handover_result.remember(HandoverResult.handed_over())
        taken: list[HandoverResult | None] = []
        lock = threading.Lock()

        def _take() -> None:
            got = handover_result.take()
            with lock:
                taken.append(got)

        threads = [threading.Thread(target=_take) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len([got for got in taken if got is not None]) == 1

    def test_the_slot_writes_nothing_to_the_profile(self, isolated_user_profile) -> None:
        """It is a transient "what just happened", NOT state: a relaunch tomorrow must not
        re-announce an irreversible change. Twinned with the positive below, so "no file
        appeared" cannot pass by the slot simply not working."""
        isolated_user_profile.mkdir(parents=True, exist_ok=True)
        before = sorted(path.name for path in isolated_user_profile.iterdir())

        handover_result.remember(HandoverResult.nightly_unconfirmed(_DETAIL))

        assert sorted(path.name for path in isolated_user_profile.iterdir()) == before
        # The positive twin: the mechanism really did carry the result.
        assert handover_result.take() == HandoverResult.nightly_unconfirmed(_DETAIL)
