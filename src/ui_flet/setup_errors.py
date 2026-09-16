"""Pure schedule-error classifier for the Setup surface — the single source.

NO ``flet`` import, no UI-framework import — this is presentation-neutral, cheaply-tested
logic that maps a schedule-registration (or removal) failure into a calm, actionable,
plain-language message. The Flet Setup surface (``screens/setup.py``) consumes it; it was
extracted here so the classification lives in one tested place, independent of any view.

**Keying (plan 0047).** ``classify_schedule_error`` keys by EXACT equality on constants it
IMPORTS from their producers — ``task_com.MSG_*`` (the HRESULT-keyed, injective,
marker-guarded canonicals) and ``windows._MSG_*`` (the bounded elevation categories) — so the
copy can never drift from the message that reaches it, and a re-worded canonical moves its
branch here rather than silently changing what an admin reads. The ONE remaining substring
branch is the defensive access-denied fallback, which keys on
``messages.ACCESS_DENIED_MARKERS`` and deliberately shows NO Windows code: a fuzzy match must
not assert a status it may not have. Each HRESULT-keyed branch shows its code, recovered
through ``task_com.hresult_for`` — the ONE place the message↔code pairing is spelled.

**Copy rules.** Every branch's first sentence names the CAUSE, not the outcome: all of them
render under the same red "Couldn't schedule the nightly sync" headline, so an outcome-first
sentence repeats the headline and tells the admin nothing. Plain prose only (no
``**markdown**`` — the Flet ``HealthVerdictBanner``/``ErrorCard`` render ``detail`` as a plain
``ft.Text``, which would show literal asterisks); ``\\n\\n`` is allowed. The vocabulary is the
schedule section's own: *signed in / signed out*, controls named ("Schedule nightly sync",
the Convert page), never a direction ("below" is wrong — the schedule readout renders ABOVE
the result slot). No branch promises a fix the app cannot make: where a district's security
policy blocks the registration, no app-side change makes the nightly sync run, and the copy
says so.

**Security (I2).** The non-leak proof is MIXED, by construction:

* a SUBSTRING branch returns FIXED copy independent of ``msg``, so a secret riding in ``msg``
  cannot ride along;
* an EXACT-equality branch is unreachable unless ``msg`` IS the constant, so a secret cannot
  reach one at all;
* the unclassified fallback surfaces ``msg`` verbatim in a trailing details clause, which is
  safe because the producer owns having sanitized it — ``windows._sanitize_child_message``
  collapses anything carrying ``messages.SECRET_SENTINEL_PREFIX``, and ``task_com``'s boundary
  guard drops any Windows description carrying a foreign marker. This module does NOT
  re-sanitize and introduces no credential text of its own.

(The retired sanitization contract this docstring used to name — ``_clean_ps_stderr`` and
CLIXML de-wrapping — went with the PowerShell transport at plan 0041 S1b.)
"""

from __future__ import annotations

from src.scheduler.messages import ACCESS_DENIED_MARKERS
from src.scheduler.task_com import (
    MSG_ACCESS_DENIED,
    MSG_ACCOUNT_INFO_NOT_SET,
    MSG_COM_UNAVAILABLE,
    MSG_LOGON_FAILURE,
    MSG_NO_LOGON_SESSION,
    MSG_OPERATION_FAILED,
    format_hresult,
    hresult_for,
)
from src.scheduler.windows import (
    _MSG_CHILD_DETAIL_UNAVAILABLE,
    _MSG_CHILD_NO_DETAIL,
    _MSG_DIFFERENT_ACCOUNT,
    _MSG_ELEVATED_ACCESS_DENIED,
    _MSG_ELEVATION_LAUNCH_FAILED,
    _MSG_ELEVATION_NO_RESULT,
    _MSG_ELEVATION_TIMEOUT,
    _MSG_UAC_DECLINED,
)


def _code(canonical: str) -> str:
    """ " (Windows code 0x…)" for a canonical — the ONE pairing, via the table's inverse.

    Never a hand-typed ``HR_*``: the code an admin quotes to their IT team has to be the one
    the log line carries, and ``hresult_for`` is the inverse of the single producer table.
    """
    return f" (Windows code {format_hresult(hresult_for(canonical))})"


def _carries_access_denied(msg: str) -> bool:
    """The defensive substring test — the marker list, imported from its owner."""
    lowered = msg.lower()
    return any(marker in lowered for marker in ACCESS_DENIED_MARKERS)


def classify_schedule_error(msg: str, elevated: bool, *, account_is_current: bool) -> str:
    """Map a schedule failure message into a calm, actionable, cause-first message.

    Args:
        msg: The failure message ``register_task`` / ``delete_task`` returned — a
            ``task_com`` canonical, a ``windows._MSG_*`` elevation category, or an
            unmapped message carrying its own hex status. Already sanitized by the
            producer; safe to surface verbatim in the fallback's details clause.
        elevated: Whether the PARENT process runs with administrator rights
            (:func:`src.scheduler.windows.is_elevated`). It distinguishes an un-elevated
            access-denied (→ run as administrator) from an elevated one. It is NOT how a
            UAC-approved CHILD refusal is recognised: that arrives as its own canonical
            (``_MSG_ELEVATED_ACCESS_DENIED``), so provenance rides the message rather than
            the parent's token — otherwise an admin who already answered the prompt is told
            to answer it again, the loop SD60 ran on 2026-09-14.
        account_is_current: Whether the task's principal is the signed-in account. REQUIRED
            keyword, deliberately undefaulted: it selects personal-credential coaching (a
            Windows Hello PIN, a microsoft.com password) that is wrong — and misleading —
            for a service account. Today every call site passes ``True``; plan 0046-B passes
            the recorded principal and re-reads the ``False`` copy against the real field.

    Returns:
        A plain-language, cause-first message (plain prose — no markdown, so a Flet verdict
        banner renders it cleanly).
    """
    # 1. Self-elevation outcomes (D5) — exact canonical markers from the elevated path.
    # Checked first so a bounded category always wins over the generic copy below.
    if msg == _MSG_UAC_DECLINED:
        return "You declined the Windows permission prompt — nothing was changed."
    if msg == _MSG_ELEVATION_TIMEOUT:
        # HEDGED (honesty): a timeout is only reachable AFTER the prompt was accepted, and the
        # terminated child may already have created the schedule — never claim "before it was
        # answered" / "nothing was changed". The register flow already tried a read-back.
        return (
            "DistrictSync stopped waiting for the elevated request to finish — the nightly sync "
            "may or may not have been scheduled. Check the schedule status above, then schedule "
            "it again if needed."
        )
    if msg == _MSG_ELEVATION_NO_RESULT:
        return (
            "The permission prompt was accepted but we couldn't confirm the schedule change — "
            "check the schedule status above."
        )
    if msg == _MSG_DIFFERENT_ACCOUNT:
        # A8 (SD51, 2026-09-16): the OBSERVED fact leads and the cross-account mechanism is
        # named as the known trigger, not the only cause — the PermissionError rung this
        # message comes from also fires on a non-cross-account read refusal, and a flat causal
        # claim would send those admins hunting Windows accounts. A code reading, not a
        # reproduction (plan 0047's own grounding says so).
        return (
            "The elevated step couldn't read the request DistrictSync prepared under your account — "
            "that can happen when the administrator credentials typed at the prompt belong to a "
            "different account. Sign in to this computer with an administrator account and run "
            "DistrictSync from there, or schedule the nightly sync without the Windows password "
            "(it then runs only while you're signed in)."
        )
    if msg == _MSG_ELEVATION_LAUNCH_FAILED:
        return (
            "Windows couldn't show the permission prompt. Try again, or run DistrictSync as an "
            "administrator to schedule the nightly sync."
        )

    # 2. HRESULT-keyed, exact equality — the cause Windows named, plus its code.
    if msg == MSG_NO_LOGON_SESSION:
        # "Microsoft documents this when …", never "usually": the policy link is documented for
        # Task Scheduler's own UI (an archived 2012 page) and for another product, not for this
        # code path — a frequency word would hedge a provenance gap with N=0 observations. And
        # no promise of a retry: if the policy IS the cause, no app-side change makes it work.
        return (
            "Windows would not save the password for the nightly task, so it can't be scheduled to "
            "run while no one is signed in. Microsoft documents this when the security setting "
            "'Network access: Do not allow storage of passwords and credentials for network "
            "authentication' is switched on — a hardening setting your IT team controls; "
            "DistrictSync can't change it."
            "\n\n"
            "Send your IT team that setting's name and the code shown here; until it's resolved you "
            "can run the sync by hand from the Convert page. If someone stays signed in overnight, "
            "you can instead clear the Windows account password field above and choose Schedule "
            "nightly sync again — it will not run after a reboot with no one signed in." + _code(msg)
        )
    if msg == MSG_ACCOUNT_INFO_NOT_SET:
        # States the code's DOCUMENTED meaning and claims no cause — 0x8004130F's cause is not
        # documented anywhere, and it is emphatically not the password that was just typed.
        return (
            "Windows is missing the nightly task's own saved account details — this isn't about the "
            "password you typed. If a nightly schedule is listed above, choose Remove nightly sync, "
            "then Schedule nightly sync again; if it keeps failing, the Help page has our support "
            "contact." + _code(msg)
        )
    if msg == MSG_LOGON_FAILURE:
        lead = (
            "Windows rejected the user name or password. Enter your Windows account password — the "
            "one you use to sign in to this computer, not a Windows Hello PIN; for a Microsoft "
            "Account it's your microsoft.com password."
            if account_is_current
            else (
                "Windows rejected the user name or password for the account you entered. Check the "
                "account name and re-enter that account's password."
            )
        )
        return lead + (
            " If it's rejected again, stop rather than retry (repeated attempts can lock the account) "
            "and check with your IT team, quoting the code shown here." + _code(msg)
        )
    if msg == MSG_COM_UNAVAILABLE:
        # A frozen build that failed to bundle pywin32 — PERMANENT, so no retry is offered and
        # there is no HRESULT to show (nothing reached Windows).
        return (
            "This copy of DistrictSync can't reach Windows Task Scheduler, so the nightly sync can't "
            "be scheduled from here — you can still run conversions from the Convert page. The Help "
            "page has our support contact."
        )
    if msg == _MSG_ELEVATED_ACCESS_DENIED or (elevated and _carries_access_denied(msg)):
        # PROVENANCE wording, not a cause: there is no source for what causes an elevated
        # 0x80070005. All we can honestly say is what our own testing shows.
        opener = (
            "even after the permission prompt was approved"
            if msg == _MSG_ELEVATED_ACCESS_DENIED
            else "even though you're running as administrator"
        )
        # The code rides only the two EXACT identities: this branch is also the (defensive)
        # substring floor for an elevated caller, and a fuzzy match must not assert a status.
        exact = msg in (_MSG_ELEVATED_ACCESS_DENIED, MSG_ACCESS_DENIED)
        quoting = "the code shown here and the ERROR line" if exact else "the ERROR line"
        return (
            f"Something on this computer is likely blocking the schedule change {opener} — in our "
            "testing a wrong password reports a different message, not this one. Check with your IT "
            f"team, quoting {quoting} in DistrictSync's log file." + (_code(MSG_ACCESS_DENIED) if exact else "")
        )

    # 3. Substring, defensive, NO code — the un-elevated access-denied remedy. Byte-identical
    # to the copy this branch has always returned; MSG_ACCESS_DENIED with elevated=False also
    # lands here, since the remedy is the same whether the status was mapped or matched.
    if _carries_access_denied(msg):
        return (
            "Permission denied — right-click the application and choose Run as administrator, "
            "then try again. (Creating an unattended task needs administrator rights.)"
        )

    # 4. Everything else.
    return _unclassified_copy(msg)


#: A details clause that only repeats the lead is no detail at all — these three literals
#: carry no cause of their own, so the fallback shows them without the parenthetical. The
#: CODED generic (``"The schedule operation failed (0x…)."``) is a DIFFERENT string and keeps
#: its clause, because the code is the whole point of it.
_NO_DETAIL = frozenset({_MSG_CHILD_DETAIL_UNAVAILABLE, _MSG_CHILD_NO_DETAIL, MSG_OPERATION_FAILED})


def _unclassified_copy(msg: str) -> str:
    """The fallback, and the produced-vs-classified sweep's oracle.

    0035 W3b (T1 #2): lead with calm FIXED copy + a support path, and demote the raw
    (producer-sanitized) message to a trailing details clause — the admin reads a next step
    first, never a wall of technical text. Phrased "schedule change" (not "register") because
    Setup routes remove failures through this same classifier.

    Plan 0047 G4: the lead no longer says "Try again in a moment". A district whose security
    policy blocks the registration would wait forever; "You can try once more" offers the same
    cheap action without promising it helps.
    """
    lead = (
        "The schedule change didn't go through. You can try once more; if it fails again, "
        "the Help page has our support contact — include the detail shown here."
    )
    return lead if msg in _NO_DETAIL else f"{lead} (Details: {msg})"
