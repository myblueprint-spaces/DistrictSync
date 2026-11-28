"""'Log on as a batch job' after an unattended registration (DSYNC-111) — pure apart from one read seam.

**Why.** Registering a nightly as a password account or a managed service account succeeds even when the account lacks
the 'Log on as a batch job' right (``SeBatchLogonRight``): Windows reports that only at run time. In the lab the
headless ``--schedule-register`` printed "Scheduled" and exited 0, and the nightly then never ran (Last Run Result
267011, event 4625 ``0xC000015B``). This module is what the command line and the window both ask afterwards.

**What it can know.** The read is the DSYNC-33 one (:func:`schedule_check.read_batch_right`): the account's OWN rights
in the local LSA policy. It does not expand groups, so a right granted to a group the account is in (Administrators, a
district group, a domain GPO) reads ``NOT_DIRECT`` exactly like a right nobody granted. Therefore:

* NOT HELD is said only on a POSITIVE determination: an explicit ``SeDenyBatchLogonRight`` on the account (a deny
  overrides every grant), or Windows itself refusing the logon type when it started the gMSA check run as the account
  (``CheckVerdict.BATCH_LOGON``, which Windows decides over the whole token, groups included);
* HELD is a direct grant, or a check run that DistrictSync started as the account;
* everything else — not granted directly, a policy that could not be read — is INCONCLUSIVE: an advisory, never a
  finding.

**What it never does:** grant the right. Granting user rights is district IT's job (DSYNC-111 decision 4, pinned by a
source scan in ``tests/test_batch_right.py``).

The prerequisite lines live here (moved from the window's setup flow, which re-exports them) because the command line
prints them and the scheduler layer may not import the UI.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from src.scheduler import schedule_check
from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck
from src.scheduler.task_com import PrincipalKind

logger = logging.getLogger(__name__)

#: The batch-logon prerequisite, as the window's checklist and the IT documents spell it.
BATCH_RIGHT_PREREQUISITE = "The account is granted the 'Log on as a batch job' right on this computer."

#: What a district's IT team must do BEFORE a managed service account can run the nightly task (plan 0049 S-4), as a
#: checklist. Read by the Settings disclosure, by ``setup_errors``' managed-service-account failure arm and by the
#: headless ``--schedule-register``; the hand-to-IT document states them verbatim too, tied back by
#: ``tests/test_partner_doc_schedule_copy_parity.py``. None of the three is knowable at registration — which is why the
#: window renders them as read-only glyphs, never tick boxes.
GMSA_PREREQUISITES: tuple[str, ...] = (
    "This computer is listed in the account's PrincipalsAllowedToRetrieveManagedPassword.",
    "Install-ADServiceAccount has been run for the account on this computer.",
    BATCH_RIGHT_PREREQUISITE,
)

#: A stored-password account needs only the batch right (its password is checked when the task is registered).
PASSWORD_PREREQUISITES: tuple[str, ...] = (BATCH_RIGHT_PREREQUISITE,)


def prerequisites_for(kind: PrincipalKind) -> tuple[str, ...]:
    """The lines an unattended account of ``kind`` needs in place. A signed-in task has none."""
    if kind is PrincipalKind.MANAGED_SERVICE_ACCOUNT:
        return GMSA_PREREQUISITES
    if kind is PrincipalKind.PASSWORD:
        return PASSWORD_PREREQUISITES
    return ()


class BatchRight(StrEnum):
    """What can be said about the account's batch-logon right after registration."""

    HELD = "held"
    NOT_HELD = "not_held"  # positively: denied, or Windows refused the logon type
    INCONCLUSIVE = "inconclusive"  # not granted directly (maybe via a group or a policy), or unreadable


def assess(state: RightState, *, check: ScheduleCheck | None = None) -> BatchRight:
    """The decision (see the module docstring). Pure and TOTAL.

    Windows' own answer from the check run outranks the direct-rights read, because it covers groups and policy.
    """
    if check is not None and check.verdict is CheckVerdict.BATCH_LOGON:
        return BatchRight.NOT_HELD
    if check is not None and check.passed:
        return BatchRight.HELD
    if state is RightState.DENIED:
        return BatchRight.NOT_HELD
    if state is RightState.GRANTED:
        return BatchRight.HELD
    return BatchRight.INCONCLUSIVE


def _read_right_live(account: str) -> RightState:
    return schedule_check.read_batch_right(account)


def read_right(account: str) -> RightState:
    """The seam over the DSYNC-33 read (tests replace it; ``tests/conftest.py`` guards every test from the real LSA)."""
    return _read_right_live(account)


def after_registration(kind: PrincipalKind, account: str, *, check: ScheduleCheck | None = None) -> BatchRight | None:
    """The batch right of a nightly just registered as ``account``. ``None`` = a signed-in task: nothing to check.

    The elevated check's own read (``check.batch_right``) is used when it answered — it ran with the token that
    registered the nightly — and this process reads the policy otherwise. Logged as ONE line with no account in it.
    """
    if kind is PrincipalKind.INTERACTIVE_TOKEN:
        return None
    state: RightState
    if check is not None and check.batch_right is not RightState.UNKNOWN:
        state = check.batch_right
    else:
        state = read_right(account)
    outcome = assess(state, check=check)
    logger.log(
        logging.WARNING if outcome is BatchRight.NOT_HELD else logging.INFO,
        "Batch-logon right after registration: %s (direct: %s)",
        outcome.value,
        state.value,
    )
    return outcome


# --------------------------------------------------------------------------------------------------------------------
# The words — shared by the command line and the window. Plain ASCII: a console may not be UTF-8.
# --------------------------------------------------------------------------------------------------------------------

PREREQUISITES_LEAD = "Before the nightly can run as this account, your IT team must make sure that:"
HELD_LINE = "The account has the 'Log on as a batch job' right on this computer."
INCONCLUSIVE_LINE = (
    "Not confirmed: DistrictSync couldn't confirm that the account has the 'Log on as a batch job' right on this "
    "computer. It may get it through a group or a domain policy, which this check can't see. If it doesn't, the "
    "nightly won't run: ask your IT team to confirm the right is granted."
)
NOT_HELD_HEADLINE = "The nightly sync is scheduled, but it won't run yet"
NOT_HELD_DETAIL = (
    "The account doesn't have the 'Log on as a batch job' right on this computer, so Windows won't start the nightly "
    "sync as that account. The nightly stays scheduled and runs once the right is granted; you don't need to schedule "
    "it again. To grant it, open Local Security Policy > Local Policies > User Rights Assignment > 'Log on as a batch "
    "job' and add the account; for a domain account, your IT team can grant it through Group Policy. A 'Deny log on "
    "as a batch job' entry overrides any grant, so the account and its groups must not be listed there."
)


def report_lines(kind: PrincipalKind, outcome: BatchRight | None) -> list[str]:
    """The command line's lines for ``outcome``: the prerequisites, then what the read found. ``[]`` for ``None``."""
    if outcome is None:
        return []
    lines = [PREREQUISITES_LEAD, *(f"  - {item}" for item in prerequisites_for(kind))]
    if outcome is BatchRight.NOT_HELD:
        lines.append(f"Warning: {NOT_HELD_HEADLINE}. {NOT_HELD_DETAIL}")
    elif outcome is BatchRight.HELD:
        lines.append(HELD_LINE)
    else:
        lines.append(INCONCLUSIVE_LINE)
    return lines
