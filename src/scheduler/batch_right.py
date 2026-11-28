"""'Log on as a batch job' after an unattended registration (DSYNC-111) — pure apart from one read seam.

**Why.** Registering a nightly as a password account or a managed service account succeeds even when the account lacks
the 'Log on as a batch job' right (``SeBatchLogonRight``): Windows reports that only at run time. In the lab the
headless ``--schedule-register`` printed "Scheduled" and exited 0, and the nightly then never ran (Last Run Result
267011, event 4625 ``0xC000015B``). This module is what the command line and the window both ask afterwards.

**What it can know depends on the account.**

* A **local account** (its SID is in this computer's own account domain, and this computer is not a domain
  controller): its whole batch-logon token is knowable here — the account, its local groups and the implicit SIDs every
  batch token carries (:func:`token_sids`). :func:`_local_facts` reads every holder of ``SeBatchLogonRight`` and of
  ``SeDenyBatchLogonRight`` (``LsaEnumerateAccountsWithUserRight``) and the account's groups (Net API), and
  :func:`local_token_state` decides: a deny-holder in the minimal token → DENIED; the account holds it → GRANTED; another SID in
  the token holds it → GRANTED_VIA_GROUP; NOTHING in the token holds it → NOT_HELD_LOCAL, a POSITIVE "not held".
* A **domain account or a gMSA**: nested domain groups cannot be expanded here, so the read is the DSYNC-33 one
  (:func:`schedule_check.read_batch_right`, the account's OWN rights). A right granted through a group reads
  ``NOT_DIRECT`` exactly like a right nobody granted, so that is INCONCLUSIVE.

So NOT HELD (exit 4 on the command line) is said only on a positive determination: a deny that reaches the account, a
local account whose token holds nothing that grants the right, or Windows itself refusing the logon type when it started
the gMSA check run (``CheckVerdict.BATCH_LOGON``). HELD is a grant that reaches the account or a check run DistrictSync
started. Everything else — not granted directly to a domain account, a policy this process could not read (the LSA
refuses an unelevated caller) — is INCONCLUSIVE: an advisory, never a finding. Every read is total: any error falls
back to the next weaker read, and finally to UNKNOWN; never to a guessed "not held".

**Two token sets, each erring toward exit 0 (review F1).** GRANTS are judged against a GENEROUS token
(:func:`token_sids`): the certain implicit SIDs plus three a batch token MAY carry (This Organization, LOCAL, NTLM
Authentication) and every local group holding any of them — an extra SID there can only turn a "not held" into a
"held". DENIES are judged against a MINIMAL token (:func:`deny_token_sids`): only the SIDs a batch token CERTAINLY
carries and the groups they reach — so a deny on a SID the token may not carry can never produce a false exit 4.

**What it never does:** grant the right. Granting user rights is district IT's job (DSYNC-111 decision 4, pinned by a
source scan in ``tests/test_batch_right.py``).

The prerequisite lines live here (moved from the window's setup flow, which re-exports them) because the command line
prints them and the scheduler layer may not import the UI.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.scheduler import schedule_check, task_com
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
    NOT_HELD = "not_held"  # positively: denied, a local token holding nothing, or Windows refused the logon type
    INCONCLUSIVE = "inconclusive"  # not granted directly to a domain account, or unreadable


def assess(state: RightState, *, check: ScheduleCheck | None = None) -> BatchRight:
    """The decision (see the module docstring). Pure and TOTAL.

    Windows' own answer from the check run outranks the policy read, because it covers groups and policy.
    """
    if check is not None and check.verdict is CheckVerdict.BATCH_LOGON:
        return BatchRight.NOT_HELD
    if check is not None and check.passed:
        return BatchRight.HELD
    if state in (RightState.DENIED, RightState.NOT_HELD_LOCAL):
        return BatchRight.NOT_HELD
    if state in (RightState.GRANTED, RightState.GRANTED_VIA_GROUP):
        return BatchRight.HELD
    return BatchRight.INCONCLUSIVE


# --------------------------------------------------------------------------------------------------------------------
# The local-account token (pure)
# --------------------------------------------------------------------------------------------------------------------

SID_EVERYONE = "S-1-1-0"
SID_ADMINISTRATORS = "S-1-5-32-544"
#: NT AUTHORITY\Local account and member of Administrators group — in the token only for a member of Administrators.
SID_LOCAL_ACCOUNT_AND_ADMIN = "S-1-5-114"

#: The SIDs Windows CERTAINLY adds to a LOCAL account's token for a BATCH logon (Task Scheduler with a stored password,
#: LogonUser LOGON32_LOGON_BATCH via MSV1_0), beside the account SID, its SAM groups and the local groups they reach:
#:
#: * ``S-1-1-0`` Everyone and ``S-1-5-11`` Authenticated Users — every authenticated logon;
#: * ``S-1-5-3`` BATCH — the logon-type SID of a batch logon;
#: * ``S-1-5-113`` Local account — every local-account logon (plus ``S-1-5-114`` for a member of Administrators,
#:   added by the expansion).
#:
#: Not included because no right is ever assigned to them: the per-logon ``S-1-5-5-x-y`` session SID and the
#: integrity labels. ``BUILTIN\\Users`` (``S-1-5-32-545``) arrives through the alias expansion: by default it holds
#: Authenticated Users and INTERACTIVE, and every local account is in it directly too.
BATCH_TOKEN_CERTAIN_SIDS: frozenset[str] = frozenset({"S-1-1-0", "S-1-5-3", "S-1-5-11", "S-1-5-113"})

#: SIDs a batch token MAY carry (``S-1-5-15`` This Organization, ``S-1-2-0`` LOCAL, ``S-1-5-64-10`` NTLM
#: Authentication). Counted for GRANTS only: a grant to one of them can then never yield a false "not held".
BATCH_TOKEN_POSSIBLE_SIDS: frozenset[str] = frozenset({"S-1-5-15", "S-1-2-0", "S-1-5-64-10"})

#: The generous grant-side set (review F1 keeps the name the earlier slice and its docs use).
BATCH_TOKEN_IMPLICIT_SIDS: frozenset[str] = BATCH_TOKEN_CERTAIN_SIDS | BATCH_TOKEN_POSSIBLE_SIDS


@dataclass(frozen=True)
class LocalFacts:
    """What :func:`_local_facts` read for a LOCAL account. SIDs as strings; never an account name.

    ``user_group_sids``: the SAM global groups (``NetUserGetGroups``, e.g. the computer's "None") and the local groups
    with ``LG_INCLUDE_INDIRECT`` (``NetUserGetLocalGroups``). ``alias_members``: every local group's member SIDs
    (``NetLocalGroupGetMembers``), so a group holding an IMPLICIT SID (Users holds Authenticated Users) joins the token.
    """

    account_sid: str
    user_group_sids: frozenset[str]
    alias_members: Mapping[str, frozenset[str]]
    batch_holders: frozenset[str]
    deny_holders: frozenset[str]


def is_local_account(sid: str, *, account_domain_sid: str, primary_domain_sid: str | None) -> bool:
    """Is ``sid`` an account of THIS computer's own SAM? Pure and TOTAL.

    Its domain part (the SID minus its RID) must equal the computer's account-domain SID. On a domain controller the
    account domain IS the domain (``primary_domain_sid`` equals it), so nothing there counts as local.
    """
    if not sid.startswith("S-1-5-21-"):
        return False
    domain, _, rid = sid.rpartition("-")
    if not rid.isdigit() or domain != account_domain_sid:
        return False
    return not (primary_domain_sid and primary_domain_sid == account_domain_sid)


def _expand(facts: LocalFacts, implicit: frozenset[str]) -> frozenset[str]:
    """The account, its groups and ``implicit``, plus every local group holding any of them, to a fixed point."""
    sids = {facts.account_sid, *facts.user_group_sids, *implicit}
    changed = True
    while changed:
        before = len(sids)
        sids.update(alias for alias, members in facts.alias_members.items() if members & sids)
        if SID_ADMINISTRATORS in sids:
            sids.add(SID_LOCAL_ACCOUNT_AND_ADMIN)
        changed = len(sids) != before
    return frozenset(sids)


def token_sids(facts: LocalFacts) -> frozenset[str]:
    """The GENEROUS token, for grants: every SID the batch token may carry (:data:`BATCH_TOKEN_IMPLICIT_SIDS`). Pure."""
    return _expand(facts, BATCH_TOKEN_IMPLICIT_SIDS)


def deny_token_sids(facts: LocalFacts) -> frozenset[str]:
    """The MINIMAL token, for denies: only SIDs the batch token certainly carries (:data:`BATCH_TOKEN_CERTAIN_SIDS`).

    Review F1: a deny judged against the generous set would turn a deny on a SID the token may NOT carry (LOCAL, NTLM
    Authentication, or a group reached only through one) into a false exit 4. Always a subset of :func:`token_sids`.
    """
    return _expand(facts, BATCH_TOKEN_CERTAIN_SIDS)


def local_token_state(facts: LocalFacts) -> RightState:
    """The local account's batch right from its token. Pure and TOTAL.

    A deny-holder in the MINIMAL token wins; then the account, then any SID of the GENEROUS token; else not held. Each
    side errs toward exit 0: a deny must certainly reach the account, a grant need only possibly reach it.
    """
    if deny_token_sids(facts) & facts.deny_holders:
        return RightState.DENIED
    if facts.account_sid in facts.batch_holders:
        return RightState.GRANTED
    if token_sids(facts) & facts.batch_holders:
        return RightState.GRANTED_VIA_GROUP
    return RightState.NOT_HELD_LOCAL


# --------------------------------------------------------------------------------------------------------------------
# The live reads (Windows-only) and the ONE seam over them
# --------------------------------------------------------------------------------------------------------------------

_SE_BATCH_LOGON = "SeBatchLogonRight"
_SE_DENY_BATCH_LOGON = "SeDenyBatchLogonRight"
#: ``LsaEnumerateAccountsWithUserRight``'s "nobody holds it" is STATUS_NO_MORE_ENTRIES; pywin32 reports it as
#: ERROR_NO_MORE_ITEMS (259) through LsaNtStatusToWinError. Both spellings accepted (which one surfaces is unmeasured).
_NO_HOLDERS = frozenset({259, 0x8000001A, -2147483622})
_LG_INCLUDE_INDIRECT = 1
_POLICY_VIEW_LOCAL_INFORMATION = 0x00000001
_POLICY_LOOKUP_NAMES = 0x00000800


class LocalApi(Protocol):
    """The Net/LSA reads :func:`_assemble_local_facts` needs, each answering in SIDs (review F4: the seam)."""

    def account_sid(self, name: str) -> str: ...
    def domain_sids(self) -> tuple[str, str | None]: ...  # (account domain, primary domain or None)
    def holders(self, right: str) -> frozenset[str]: ...
    def local_groups(self, user: str) -> frozenset[str]: ...
    def sam_groups(self, prefix: str, user: str) -> frozenset[str]: ...
    def aliases(self) -> Mapping[str, frozenset[str]]: ...


def _assemble_local_facts(account: str, api: LocalApi) -> LocalFacts | None:
    """Assemble a LOCAL account's facts from ``api``; ``None`` (with NO rights or group read) when it is not local."""
    sid = api.account_sid(account)
    account_domain, primary_domain = api.domain_sids()
    if not is_local_account(sid, account_domain_sid=account_domain, primary_domain_sid=primary_domain):
        return None
    prefix, _, user = account.rpartition("\\")
    return LocalFacts(
        account_sid=sid,
        user_group_sids=api.local_groups(user) | api.sam_groups(prefix, user),
        alias_members=dict(api.aliases()),
        batch_holders=api.holders(_SE_BATCH_LOGON),
        deny_holders=api.holders(_SE_DENY_BATCH_LOGON),
    )


class _LiveLocalApi:  # pragma: no cover - Windows-only LSA + Net API; read-only
    """The real reads. Needs an elevated token (the LSA refuses ``LsaEnumerateAccountsWithUserRight`` otherwise —
    measured), so on the provisioning path it runs in the elevated child. Nothing here changes the policy."""

    def __init__(self) -> None:
        import win32security  # noqa: PLC0415 - lazy: Windows-only

        self._sec = win32security

    def _text(self, sid: object) -> str:
        return str(self._sec.ConvertSidToStringSid(sid))

    def _sid_of(self, name: str) -> str:
        return self._text(self._sec.LookupAccountName(None, name)[0])

    def account_sid(self, name: str) -> str:
        from src.scheduler.provisioning import account_sid  # noqa: PLC0415 - provisioning imports windows

        return account_sid(name)

    def _policy(self):  # noqa: ANN202 - a PyLSA_HANDLE
        return self._sec.LsaOpenPolicy(None, _POLICY_LOOKUP_NAMES | _POLICY_VIEW_LOCAL_INFORMATION)

    def domain_sids(self) -> tuple[str, str | None]:
        policy = self._policy()
        try:
            _n, account = self._sec.LsaQueryInformationPolicy(policy, self._sec.PolicyAccountDomainInformation)
            _p, primary = self._sec.LsaQueryInformationPolicy(policy, self._sec.PolicyPrimaryDomainInformation)
        finally:
            self._sec.LsaClose(policy)
        return self._text(account), (self._text(primary) if primary is not None else None)

    def holders(self, right: str) -> frozenset[str]:
        import pywintypes  # noqa: PLC0415 - lazy: Windows-only

        policy = self._policy()
        try:
            return frozenset(self._text(s) for s in self._sec.LsaEnumerateAccountsWithUserRight(policy, right))
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) in _NO_HOLDERS:
                return frozenset()
            raise
        finally:
            self._sec.LsaClose(policy)

    def local_groups(self, user: str) -> frozenset[str]:
        import win32net  # noqa: PLC0415 - lazy: Windows-only

        return frozenset(self._sid_of(n) for n in win32net.NetUserGetLocalGroups(None, user, _LG_INCLUDE_INDIRECT))

    def sam_groups(self, prefix: str, user: str) -> frozenset[str]:
        import win32net  # noqa: PLC0415 - lazy: Windows-only

        return frozenset(
            self._sid_of(f"{prefix}\\{entry[0]}" if prefix else entry[0])
            for entry in win32net.NetUserGetGroups(None, user)
        )

    def aliases(self) -> Mapping[str, frozenset[str]]:
        import win32net  # noqa: PLC0415 - lazy: Windows-only

        found: dict[str, frozenset[str]] = {}
        resume = 0
        while True:
            entries, _total, resume = win32net.NetLocalGroupEnum(None, 0, resume)
            for entry in entries:
                members: set[str] = set()
                member_resume = 0
                while True:
                    rows, _t, member_resume = win32net.NetLocalGroupGetMembers(None, entry["name"], 0, member_resume)
                    members.update(self._text(row["sid"]) for row in rows)
                    if not member_resume:
                        break
                found[self._sid_of(entry["name"])] = frozenset(members)
            if not resume:
                break
        return found


def _local_facts(account: str) -> LocalFacts | None:  # pragma: no cover - Windows-only
    """Read a LOCAL account's token facts; ``None`` when the account is not local (or off Windows). Raises on error."""
    if sys.platform != "win32":
        return None
    return _assemble_local_facts(account, _LiveLocalApi())


def _read_right_live(account: str) -> RightState:
    """The local-account token read when it applies, else the DSYNC-33 direct read. TOTAL.

    The local read is bounded and any failure (not elevated, a lookup error, a timeout) falls back to the direct read,
    which is itself bounded and total — never to a guessed answer.
    """
    try:
        facts = task_com.bounded(
            lambda: _local_facts(account), timeout_s=schedule_check.ADVISORY_CALL_TIMEOUT_S, label="batch-token"
        )
    except Exception as exc:  # noqa: BLE001 - advisory: a read that could not finish is never a finding
        logger.info("The local batch-logon token could not be read (%s); using the direct read.", type(exc).__name__)
        facts = None
    if isinstance(facts, LocalFacts):
        return local_token_state(facts)
    return schedule_check.read_batch_right(account)


def read_right(account: str) -> RightState:
    """THE seam over every policy read (tests replace it; ``tests/conftest.py`` guards every test from the real LSA)."""
    return _read_right_live(account)


def after_registration(
    kind: PrincipalKind,
    account: str,
    *,
    check: ScheduleCheck | None = None,
    elevated_state: RightState | None = None,
) -> BatchRight | None:
    """The batch right of a nightly just registered as ``account``. ``None`` = a signed-in task: nothing to check.

    The ELEVATED child's answers come first — ``elevated_state`` (its :func:`read_right` for a password account,
    ``ProvisionAttempt.batch_right``) and the gMSA check's own read (``check.batch_right``) — because the LSA refuses an
    unelevated caller; this process reads the policy only when neither answered. ONE log line, no account in it.
    """
    if kind is PrincipalKind.INTERACTIVE_TOKEN:
        return None
    state: RightState
    if elevated_state is not None and elevated_state is not RightState.UNKNOWN:
        state = elevated_state
    elif check is not None and check.batch_right is not RightState.UNKNOWN:
        state = check.batch_right
    else:
        state = read_right(account)
    outcome = assess(state, check=check)
    logger.log(
        logging.WARNING if outcome is BatchRight.NOT_HELD else logging.INFO,
        "Batch-logon right after registration: %s (read: %s)",
        outcome.value,
        state.value,
    )
    return outcome


# --------------------------------------------------------------------------------------------------------------------
# The words — shared by the command line and the window. Plain ASCII: a console may not be UTF-8.
# --------------------------------------------------------------------------------------------------------------------

PREREQUISITES_LEAD = "Before the nightly can run as this account, your IT team must make sure that:"
#: The window's WARNING band headline over :data:`INCONCLUSIVE_LINE` (review F2).
INCONCLUSIVE_HEADLINE = "DistrictSync couldn't confirm the nightly sync's account can run it"
HELD_LINE = "The account has the 'Log on as a batch job' right on this computer."
INCONCLUSIVE_LINE = (
    "Not confirmed: DistrictSync couldn't confirm that the account has the 'Log on as a batch job' right on this "
    "computer. It may get it through a domain group or a domain policy, which this check can't see, or the policy "
    "couldn't be read here. If it doesn't have it, the nightly won't run: ask your IT team to confirm the right is "
    "granted."
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
