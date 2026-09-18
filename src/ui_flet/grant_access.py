"""The auto-grant window's COPY and its decision table — pure, no flet, no I/O (0049 S-1b-ii.2).

A second administrator on a machine-scoped install cannot open the shared profile until
Windows grants their account an ace on it (D5). This module owns two things:

* **which refusals offer a grant at all** (:func:`offers_grant`) — keyed on the TYPED
  :class:`~src.utils.paths.MachineScopeRefusedReason`, never on ``str(exc)``. Only an
  access denial is a grantable state; every other reason describes a folder a grant cannot
  repair, and the elevated op would refuse it anyway (``ProvisionStep.PRE_EXISTING``);
* **every string the window shows**, as constants, so the copy can be reviewed and swept as
  text rather than excavated from a render function.

**D5's non-administrator promise cannot be kept, and this copy says so — twice.** The
elevation handshake is DPAPI **CurrentUser** and ``elevated_apply`` maps a cross-SID
unprotect to ``DSYNC_DIFFERENT_ACCOUNT``, so an administrator approving the prompt *on
someone else's behalf* fails closed. That is stated up front (:data:`ASK_ADMIN_NOTE` —
before anyone walks down the hall) and again on the branch that catches it
(:attr:`~src.scheduler.provision_session.GrantOutcome.DIFFERENT_ACCOUNT`), rather than
promising something the handshake refuses.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from src.scheduler.provision_session import GrantOutcome
from src.utils.paths import MachineScopeRefusedReason

# --------------------------------------------------------------------------- #
# Copy — constants first, controls later.                                      #
# --------------------------------------------------------------------------- #

WINDOW_TITLE = "DistrictSync"

ASK_HEADLINE = "DistrictSync's settings on this computer are shared"
ASK_DETAIL = (
    "Someone set DistrictSync up for everyone who uses this computer, and Windows has not "
    "been told about your account yet. It only needs to be told once."
)
# Stated BEFORE the prompt, not after a wasted trip: Windows grants the account that ASKED,
# so an administrator approving for you does not work. See the module docstring.
ASK_ADMIN_NOTE = (
    "You need to be an administrator on this computer. Someone else approving the Windows "
    "prompt for you will not work — Windows only grants the account that asked."
)
ASK_PRIMARY_LABEL = "Ask Windows for permission"
ASK_REASSURANCE = "Nothing about your district, your folders or your nightly sync changes."

CLOSE_LABEL = "Close DistrictSync"

# Failure copy — one entry per outcome, cause-first, and never a republished sentinel.
DECLINED_HEADLINE = "The Windows prompt was not approved"
DECLINED_DETAIL = "Nothing on this computer was changed. You can try again whenever you are ready."

DIFFERENT_ACCOUNT_HEADLINE = "A different administrator approved that prompt"
DIFFERENT_ACCOUNT_DETAIL = (
    "Windows only grants the account that asked, so approving on someone else's behalf does "
    "not work here. Either use this computer signed in to Windows as an administrator, or ask "
    "an administrator to open DistrictSync once themselves — that gives their own account "
    "access, and yours still needs this step."
)

REFUSED_HEADLINE = "Windows would not change the shared folder"
REFUSED_DETAIL = (
    "Nothing was changed. The shared folder may have been altered since DistrictSync was set "
    "up on this computer. Send the log to support."
)

UNCONFIRMED_HEADLINE = "That did not finish"
UNCONFIRMED_DETAIL = (
    "Windows did not report back in time, so we cannot say whether your account was granted. "
    "Close DistrictSync and open it again — if you land back here, try once more."
)

LAUNCH_FAILED_HEADLINE = "Windows would not show the permission prompt"
LAUNCH_FAILED_DETAIL = (
    "This usually means permission prompts are switched off on this computer. An "
    "administrator will need to open DistrictSync once themselves."
)

UNAVAILABLE_HEADLINE = "DistrictSync could not ask for permission"
UNAVAILABLE_DETAIL = "Nothing was changed. The details are in the log — send it to support."

RETRY_LABEL = "Try again"


@dataclass(frozen=True)
class GrantCopy:
    """One state of the window: a headline, a detail, and the ONE filled primary's label."""

    headline: str
    detail: str
    primary_label: str


# The failure states, by outcome. A table rather than an if-chain so the completeness test
# ("every member that is not a success has copy") is one line and cannot rot.
_FAILURE_COPY: dict[GrantOutcome, GrantCopy] = {
    GrantOutcome.DECLINED: GrantCopy(DECLINED_HEADLINE, DECLINED_DETAIL, RETRY_LABEL),
    GrantOutcome.DIFFERENT_ACCOUNT: GrantCopy(DIFFERENT_ACCOUNT_HEADLINE, DIFFERENT_ACCOUNT_DETAIL, RETRY_LABEL),
    GrantOutcome.REFUSED: GrantCopy(REFUSED_HEADLINE, REFUSED_DETAIL, RETRY_LABEL),
    GrantOutcome.UNCONFIRMED: GrantCopy(UNCONFIRMED_HEADLINE, UNCONFIRMED_DETAIL, RETRY_LABEL),
    GrantOutcome.LAUNCH_FAILED: GrantCopy(LAUNCH_FAILED_HEADLINE, LAUNCH_FAILED_DETAIL, RETRY_LABEL),
    GrantOutcome.UNAVAILABLE: GrantCopy(UNAVAILABLE_HEADLINE, UNAVAILABLE_DETAIL, RETRY_LABEL),
}

# The ONE refusal an added ace can fix. Everything else describes a folder whose owner,
# link-ness, inheritance or open-group ace is wrong — a grant would not repair any of them,
# and ``provisioning.apply_grant_current_user`` refuses outright on a directory its own
# trust predicate rejects. Keeping this a frozenset (not ``reason is INACCESSIBLE``) is what
# lets the completeness test enumerate the whole enum and pin BOTH sides of the answer.
_GRANTABLE_REASONS = frozenset({MachineScopeRefusedReason.INACCESSIBLE})


def offers_grant(reason: MachineScopeRefusedReason) -> bool:
    """Whether this refusal is one a one-time grant can fix.

    Total over the enum: an unrecognised member answers ``False``, so a reason added later
    without a decision here degrades to the existing repair dialog rather than sending an
    admin through a UAC prompt that cannot help.
    """
    return reason in _GRANTABLE_REASONS


def ask_copy() -> GrantCopy:
    """The window's opening state — the ask itself."""
    return GrantCopy(ASK_HEADLINE, ASK_DETAIL, ASK_PRIMARY_LABEL)


def failure_copy(outcome: GrantOutcome) -> GrantCopy | None:
    """The copy for a grant attempt that did not succeed; ``None`` for a success.

    ``None`` is not a floor — it is the honest answer for
    :attr:`~src.scheduler.provision_session.GrantOutcome.GRANTED`, which has no copy at all
    because the window does not paint it: it re-execs.
    """
    return _FAILURE_COPY.get(outcome)


# --------------------------------------------------------------------------- #
# Re-exec (the pure half)                                                      #
# --------------------------------------------------------------------------- #

# The dev-mode entry point. A frozen exe re-launches itself; a source checkout is run as
# ``python -m src.main``, and ``sys.argv[0]`` there is a file path whose relative imports do
# not survive being re-run as a script.
_DEV_MODULE = "src.main"


def reexec_argv() -> list[str]:
    """The argv a successful grant re-launches this process with.

    **The process is re-executed rather than retried in place.** A second ``ft.run`` in one
    process is not proven on the pinned Flet, and the profile pin, the log sink and every
    module that captured a path at import time would all have to be unwound by hand — a
    boot path is the last place to guess. A fresh process re-runs the real ladder from the
    top, which is also the only honest confirmation that the grant worked.

    Pure and returned rather than executed, so the one thing that cannot be exercised in a
    test (``os.execv``) is a single line with nothing to get wrong.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, "-m", _DEV_MODULE, *sys.argv[1:]]
