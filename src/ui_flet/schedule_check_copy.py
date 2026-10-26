"""The schedule check's words (DSYNC-33) — pure, COUNTED, no ``flet`` import.

``src/scheduler/schedule_check.py`` proves, inside the elevated operation that registered a
managed-service-account nightly, whether Windows can actually start DistrictSync as that
account, and returns a bounded :class:`~src.scheduler.schedule_check.ScheduleCheck`. This
module turns that into the band an admin reads after scheduling: a calm confirmation, or a
toned band with the verdict's note, the Windows code, the prerequisite hints the advisory facts
support, and (on the surface) the DSYNC-32 log actions.

Copy rules, the same as the rest of the schedule copy:

* the code is shown through ``task_com.format_hresult`` — the ONE hex spelling, shared with
  the log line ``provision_session`` writes for the same check, so IT reads one form;
* no account name and no path, on screen or in "Copy details" — the band names "the account
  you entered" (the managed service account is never the signed-in account, so this is the
  ``run_history.run_as_display`` "another account" form, said in Setup's own words);
* an advisory fact the API could not read (UNKNOWN) is never a hint — only a definite answer
  earns a sentence, and "not granted directly" is offered only beside the logon-type failure
  it explains, hedged, because the right is routinely granted through a group;
* an INCONCLUSIVE check (it timed out, or could not run) is never worded as a finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.scheduler.schedule_check import CheckVerdict, InstallState, RightState, ScheduleCheck
from src.scheduler.task_com import format_hresult
from src.ui_flet.setup_flow import GMSA_IT_DOC_TITLE
from src.ui_flet.verdict import Verdict

CHECK_OK_HEADLINE = "Windows started DistrictSync as the nightly sync's account"
CHECK_OK_DETAIL = "Windows ran a test as the account you entered, and DistrictSync started correctly."
CHECK_FAILED_HEADLINE = "Windows couldn't start DistrictSync as that account"
CHECK_INCONCLUSIVE_HEADLINE = "DistrictSync couldn't finish checking the nightly sync"
#: Said on every non-passing band: the nightly was NOT rolled back (DECISIONS 2026-10-23).
CHECK_STILL_SCHEDULED_FAILED = "The nightly sync is still scheduled, but it won't start until this is fixed."
CHECK_STILL_SCHEDULED_INCONCLUSIVE = (
    "The nightly sync is still scheduled; this check just couldn't tell whether it will start."
)
CHECK_RETRY_HINT = "Once it's fixed, choose Schedule nightly sync again to repeat this check."
CHECK_PREREQUISITES_HINT = f"Your IT team can check the three prerequisites in “{GMSA_IT_DOC_TITLE}”."

BATCH_DENIED_HINT = (
    "This computer's security policy denies the account 'Log on as a batch job', and a denial overrides any grant."
)
BATCH_NOT_DIRECT_HINT = (
    "The account isn't granted 'Log on as a batch job' directly on this computer. It may still "
    "get it through a group or a policy — your IT team can confirm."
)
NOT_INSTALLED_HINT = (
    "Windows reports the account isn't installed on this computer yet — Install-ADServiceAccount hasn't been run here."
)
NOT_PERMITTED_HINT = (
    "Windows reports this computer can't use the account — usually because it isn't listed in "
    "the account's PrincipalsAllowedToRetrieveManagedPassword."
)

#: One sentence per non-passing verdict. TOTAL over ``CheckVerdict`` minus OK (pinned by test).
VERDICT_NOTES: dict[CheckVerdict, str] = {
    CheckVerdict.LOGON_FAILURE: (
        "Windows couldn't sign the account in to run the test. For a managed service account that "
        "usually means this computer isn't allowed to retrieve the account's password, or the "
        "account isn't installed on this computer."
    ),
    CheckVerdict.BATCH_LOGON: (
        "Windows reported a logon-type failure. That is what it returns when the account hasn't "
        "been granted 'Log on as a batch job' on this computer."
    ),
    CheckVerdict.PASSWORD_EXPIRED: "Windows reported that the account's password has expired.",
    CheckVerdict.ACCESS_DENIED: (
        "Windows reported 'access denied' when it started the test — the account may not be able "
        "to open DistrictSync's program folder."
    ),
    CheckVerdict.PROGRAM_NOT_FOUND: (
        "Windows couldn't find DistrictSync's program when it started the test as that account — "
        "the account may not be able to see the folder DistrictSync is in."
    ),
    CheckVerdict.BAD_START_FOLDER: (
        "Windows couldn't use DistrictSync's folder as the test's start-in folder — the account "
        "may not be able to open it."
    ),
    CheckVerdict.PROFILE_REFUSED: (
        "DistrictSync started as the account, but stopped before using this computer's shared "
        "settings folder because the folder didn't pass its safety checks."
    ),
    CheckVerdict.NOT_SHARED: (
        "DistrictSync started as the account, but didn't find this computer's shared settings, so "
        "the nightly sync would run without them."
    ),
    CheckVerdict.MARKER_UNWRITABLE: (
        "DistrictSync started as the account, but couldn't write to the shared settings folder, "
        "where the nightly sync keeps its log and run history."
    ),
    CheckVerdict.MARKER_MISSING: (
        "DistrictSync reported that it started as the account, but the proof it should have left "
        "in the shared settings folder wasn't there."
    ),
    CheckVerdict.OTHER_RESULT: "Windows recorded a result for the test that DistrictSync can't name.",
    CheckVerdict.TIMEOUT: (
        "The test didn't finish within a minute, so DistrictSync can't say whether the nightly sync will start."
    ),
    CheckVerdict.CHECK_NOT_REGISTERED: "Windows wouldn't create the test task, so the check couldn't run.",
    CheckVerdict.CHECK_NOT_STARTED: "Windows wouldn't start the test task, so the check couldn't run.",
    CheckVerdict.UNAVAILABLE: "The check couldn't run on this computer.",
}

#: The verdicts where Windows itself could not start DistrictSync as the account — the ones the
#: three gMSA prerequisites explain. The DistrictSync-side verdicts (it started, then its own
#: check failed) get no prerequisite pointer: the log actions are the next step there.
_WINDOWS_START_FAILURES: frozenset[CheckVerdict] = frozenset(
    {
        CheckVerdict.LOGON_FAILURE,
        CheckVerdict.BATCH_LOGON,
        CheckVerdict.PASSWORD_EXPIRED,
        CheckVerdict.ACCESS_DENIED,
        CheckVerdict.PROGRAM_NOT_FOUND,
        CheckVerdict.BAD_START_FOLDER,
        CheckVerdict.OTHER_RESULT,
    }
)


@dataclass(frozen=True)
class CheckBand:
    """What the surface paints for one check. ``details`` is the bounded "Copy details" text."""

    verdict: Verdict
    headline: str
    detail: str
    details: str
    offers_log_actions: bool


def prerequisite_hints(check: ScheduleCheck) -> list[str]:
    """The sentences the advisory facts support — definite answers only, in a fixed order."""
    hints: list[str] = []
    if check.batch_right is RightState.DENIED:
        hints.append(BATCH_DENIED_HINT)
    elif check.batch_right is RightState.NOT_DIRECT and check.verdict is CheckVerdict.BATCH_LOGON:
        hints.append(BATCH_NOT_DIRECT_HINT)
    if check.installed is InstallState.NOT_PERMITTED:
        hints.append(NOT_PERMITTED_HINT)
    elif check.installed is InstallState.NOT_INSTALLED:
        hints.append(NOT_INSTALLED_HINT)
    return hints


def check_details(check: ScheduleCheck, *, task_name: str) -> str:
    """The "Copy details" text — task name, verdict NAME, code, the two advisory facts. TOTAL.

    Bounded by construction: the only free text is the task name (config-controlled, already in
    every scheduler log line). No account, no path, no address.
    """
    return "\n".join(
        [
            "DistrictSync schedule check",
            f"Task: {task_name or 'not set'}",
            f"Result: {check.verdict.name}",
            f"Windows result: {format_hresult(check.code)}",
            f"Batch logon right (direct): {check.batch_right.value}",
            f"Installed on this computer: {check.installed.value}",
        ]
    )


def check_band(check: ScheduleCheck, *, task_name: str) -> CheckBand:
    """The band for ``check``: HEALTHY confirmation, FAILED finding, or WARNING inconclusive."""
    details = check_details(check, task_name=task_name)
    if check.passed:
        return CheckBand(Verdict.HEALTHY, CHECK_OK_HEADLINE, CHECK_OK_DETAIL, details, offers_log_actions=False)

    note = VERDICT_NOTES.get(check.verdict, VERDICT_NOTES[CheckVerdict.UNAVAILABLE])
    parts = [note if check.code is None else f"{note} (Windows result {format_hresult(check.code)}.)"]
    if check.inconclusive:
        parts.append(CHECK_STILL_SCHEDULED_INCONCLUSIVE)
        parts += prerequisite_hints(check)
        return CheckBand(
            Verdict.WARNING, CHECK_INCONCLUSIVE_HEADLINE, " ".join(parts), details, offers_log_actions=True
        )

    parts.append(CHECK_STILL_SCHEDULED_FAILED)
    parts += prerequisite_hints(check)
    if check.verdict in _WINDOWS_START_FAILURES:
        parts.append(CHECK_PREREQUISITES_HINT)
    parts.append(CHECK_RETRY_HINT)
    return CheckBand(Verdict.FAILED, CHECK_FAILED_HEADLINE, " ".join(parts), details, offers_log_actions=True)
