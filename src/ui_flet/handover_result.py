"""The one-shot "what just happened" slot for a machine-scope handover (plan 0049 S-2b.3).

COUNTED and pure apart from one module-level cell: no ``flet`` import, no I/O.

**Why this module exists at all.** Completing a handover ends in ``reenter`` — the shell
rebuilds the whole app body and lands on Home (``nav.initial_destination_id`` is Home in
every state). So a banner painted on Setup is destroyed by the very call that finishes the
flow, and the admin's last sight of an IRREVERSIBLE, machine-wide change is a screen that
blinked. Nothing in this repo carries state across that rebuild — ``page.session`` has zero
hits — so the result rides a process-scoped slot that the rebuilt surface drains.

**One-shot, by construction.** :func:`take` reads AND clears. A result that survived would
reappear the next time the admin navigated to Home, days later, announcing a change that
happened once. And it is deliberately in MEMORY: this is a transient "what just happened",
not state — a relaunch tomorrow must not re-announce it, which is exactly what a file or a
``config.json`` field would do.

**Composition is here, not in the view** (:func:`compose`), because the banner an admin
sees is decided by TWO facts that come from different places: what the elevated child
reported (:class:`~src.scheduler.provision_session.ProvisionAttempt`) and whether the
parent's own switch read says this session handed over
(:class:`~src.scheduler.provision_session.HandoverOutcome`). Putting that join in a screen
would put it somewhere coverage omits.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from src.scheduler.provision_session import HandoverOutcome, ProvisionAttempt, ProvisionOutcome
from src.utils.paths import MachineScopeRefusedReason


class HandoverBanner(StrEnum):
    """Which of the three post-handover surfaces to paint. The view branches on the MEMBER."""

    #: Everything worked: this computer now keeps DistrictSync's settings in one shared
    #: place and the nightly is registered against it.
    HANDED_OVER = "handed_over"
    #: The scope change LANDED and the nightly did not, or could not be confirmed. The
    #: banner must LEAD with the scope change: a failed register painting its stock red
    #: card over a successful, irreversible handover tells the admin nothing happened, and
    #: they retry a move that cannot be repeated.
    HANDED_OVER_NIGHTLY_UNCONFIRMED = "handed_over_nightly_unconfirmed"
    #: The child committed the switch and the parent's re-pin then REFUSED the folder. The
    #: pin is left unset, so every later ``user_data_dir()`` in this session raises — the
    #: admin is in a live window whose next click can only crash. This one is terminal: the
    #: surface says the shared folder exists but cannot be used, that nothing else on this
    #: computer changed, and it DISABLES further Setup actions rather than let them keep
    #: pressing.
    REFUSED = "refused"


@dataclass(frozen=True)
class HandoverResult:
    """What the rebuilt surface renders. Build it through the three classmethods.

    They exist so a shape cannot be assembled that contradicts itself — a
    :attr:`HandoverBanner.REFUSED` with no reason to show, or a plain
    :attr:`~HandoverBanner.HANDED_OVER` carrying a failure detail. They do not RAISE on a
    bad combination on purpose: this is built in the dispatch handler right after an
    irreversible change, and a constructor that threw there would lose the only report of
    it. Review-upheld, like ``AppConfig.identity_save``.

    Attributes:
        banner: which surface to paint.
        detail: the already-classified sentence about the nightly, for
            :attr:`~HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED` only. Classified by the
            CALLER (``setup_errors``), because only the view knows the ``elevated`` and
            ``account_is_current`` facts that classifier requires.
        refused_reason: the bounded refusal, for :attr:`~HandoverBanner.REFUSED` only —
            ``launcher._MACHINE_SCOPE_CAUSES`` already owns one plain-language cause per
            member, so the surface has copy for it without inventing any.
    """

    banner: HandoverBanner
    detail: str = ""
    refused_reason: MachineScopeRefusedReason | None = None

    @classmethod
    def handed_over(cls) -> HandoverResult:
        """The clean case: the scope changed and the nightly is registered."""
        return cls(banner=HandoverBanner.HANDED_OVER)

    @classmethod
    def nightly_unconfirmed(cls, detail: str) -> HandoverResult:
        """The scope changed; the nightly did not, or we cannot say that it did.

        ``detail`` carries WHICH — a reported failure reads as one, and a timeout reads as
        "may or may not have been scheduled" — so the member does not have to promise a
        certainty the two arms do not share.
        """
        return cls(banner=HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED, detail=detail)

    @classmethod
    def after_refusal(cls, reason: MachineScopeRefusedReason) -> HandoverResult:
        """The switch was committed against a folder this app will not use. Terminal."""
        return cls(banner=HandoverBanner.REFUSED, refused_reason=reason)


def compose(attempt: ProvisionAttempt, handover: HandoverOutcome, *, nightly_detail: str) -> HandoverResult | None:
    """Join the child's report and the parent's own switch read into ONE banner, or ``None``.

    ``None`` means *nothing irreversible happened* — the switch is off, this session did
    not hand over, and there was no re-entry to survive. Setup is still mounted and its
    ordinary result slot is the right place for the failure; a one-shot banner would then
    ambush the admin on Home for a change that never took place.

    Order is load-bearing:

    1. ``handover.refused`` FIRST. It is set only when the switch reads ON but the shared
       profile is unusable, and in that state ``handed_over`` is False — so a rule that
       tested ``handed_over`` first would drop the one outcome that must not be dropped.
    2. handed over ∧ the child said it finished → :attr:`HandoverBanner.HANDED_OVER`.
    3. handed over ∧ anything else → :attr:`~HandoverBanner.HANDED_OVER_NIGHTLY_UNCONFIRMED`.
       This is where "the switch committed but the registration failed" is modelled, and
       modelling it as a JOIN rather than as a child outcome is deliberate: the child's
       message cannot tell a post-commit registration failure from a refusal before it
       started, and on a timeout the child is killed with no message at all. The parent's
       own switch read is the only honest authority on which side of the commit we are.

    ``nightly_detail`` is required and keyword-only: it is the sentence that tells an admin
    whether their nightly sync exists, and a defaulted ``""`` would ship a banner that
    announces a permanent change and says nothing about what it left behind.
    """
    if handover.refused is not None:
        return HandoverResult.after_refusal(handover.refused)
    if not handover.handed_over:
        return None
    if attempt.outcome is ProvisionOutcome.PROVISIONED:
        return HandoverResult.handed_over()
    return HandoverResult.nightly_unconfirmed(nightly_detail)


# The slot. A plain module global — process-scoped, never persisted (see the module
# docstring) — behind a lock because the two ends run on different threads: Setup dispatches
# the register through ``page.run_thread``, so ``remember`` is called from a worker while
# ``take`` is called from the UI thread rebuilding Home.
_lock = threading.Lock()
_pending: HandoverResult | None = None


def remember(result: HandoverResult) -> None:
    """Park ``result`` for the surface that re-entry is about to build.

    Overwrites whatever was there. That is the right rule rather than a hazard: two
    handovers in one process means the first was already rendered or already superseded,
    and the NEWER report is the true one.
    """
    global _pending
    with _lock:
        _pending = result


def take() -> HandoverResult | None:
    """Read the pending result AND clear it — the one-shot half of the contract.

    Read-and-clear in one locked step, so a rebuild racing a second dispatch cannot hand
    the same result to two surfaces.
    """
    global _pending
    with _lock:
        result, _pending = _pending, None
    return result


def reset() -> None:
    """Drop any pending result. For tests — a leaked slot would leak between them."""
    global _pending
    with _lock:
        _pending = None
