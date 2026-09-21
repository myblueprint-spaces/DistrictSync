"""Tests for the Setup schedule-error classifier (IA-4a; rewritten at plan 0047 Slice A2).

``src.ui_flet.setup_errors.classify_schedule_error`` is the single source for mapping a
``register_task`` / ``delete_task`` failure message + the process elevation state into a
calm, actionable message. It is pure (no flet import) so it is unit-testable headless.

Four concerns are covered:

1. **Behaviour** — the five HRESULT-keyed exact branches plan 0047 added, the five
   elevation markers, the two defensive access-denied substring branches, and the
   unclassified fallback. Assertions are on PLAIN substrings (the copy carries no
   ``**markdown**``: a Flet verdict banner renders ``detail`` as a plain ``ft.Text``).
2. **[SECURITY — I2] Non-leak proof** — the proof is MIXED by construction: a SUBSTRING
   branch returns FIXED copy independent of ``msg`` (so a secret riding in ``msg`` cannot
   ride along), and an EXACT-equality branch is unreachable unless ``msg`` IS the constant.
   The fallback passes ``msg`` through verbatim (the core owns having sanitized it).
3. **Produced-vs-classified sweep** — the producible message set is DERIVED by reflection
   over ``task_com`` / ``windows`` / ``elevated_apply``, so a new canonical is red by
   construction until it is either classified or declared unclassified with a reason.
4. **Vocabulary + first-sentence rules** — every branch's first sentence names the CAUSE
   (all five render under the same red "Couldn't schedule the nightly sync" headline), and
   no classifier string says "below" (the readout renders ABOVE the result slot) or
   "logged in" (the schedule section's vocabulary is "signed in / signed out").

All 25 production call sites route through the module-level :func:`_classify` helper: the
production signature's ``account_is_current`` is a REQUIRED keyword (so plan 0046-B cannot
forget it), and one test-file default keeps the next keyword a one-line edit here.
"""

from __future__ import annotations

import pytest

from src.scheduler import elevated_apply, task_com, windows
from src.scheduler.provisioning import ProvisionRefused, ProvisionStep
from src.ui_flet import setup_errors
from src.ui_flet.setup_errors import _unclassified_copy, classify_provision_step, classify_schedule_error
from src.ui_flet.setup_flow import SCHEDULE_ACCOUNT_FIELD_LABEL

# A fake secret + path smuggled inside ``msg`` — used to prove that a classified
# (known-substring) branch returns FIXED copy that does NOT echo it.
_SECRET_MSG = "Access is denied. DSYNC_TASK_PW=hunter2 C:\\Users\\x\\secret"


def _classify(msg: str, elevated: bool, *, account_is_current: bool = True) -> str:
    """The ONE call shape this file uses — the next required keyword is one edit here.

    ``account_is_current`` has no default in production (a permissive default on the
    parameter that selects personal-credential coaching is the banned shape); a default
    HERE is fine and is what keeps the 25 call sites from each carrying the keyword.
    """
    return classify_schedule_error(msg, elevated, account_is_current=account_is_current)


def _first_sentence(text: str) -> str:
    return text.split(". ")[0]


class TestClassifyScheduleError:
    def test_not_elevated_access_denied_says_run_as_admin(self) -> None:
        msg = _classify("Access is denied.", elevated=False)
        assert "Run as administrator" in msg
        assert "administrator rights" in msg

    def test_elevated_access_denied_is_provenance_worded_not_credential_coaching(self) -> None:
        # Plan 0046 A7: the PIN / microsoft.com / batch-logon coaching was the PowerShell-era
        # diagnosis and is retired — a wrong password was live-observed (2026-08-05) to fail
        # with 0x8007052E, which now has its own branch.
        msg = _classify(task_com.MSG_ACCESS_DENIED, elevated=True)
        assert "Run as administrator" not in msg
        assert "Log on as a batch job" not in msg
        assert "PIN" not in msg
        assert "microsoft.com" not in msg
        assert "batch" not in msg.lower()
        assert "in our testing a wrong password reports a different message" in msg
        assert "IT team" in msg
        assert "0x80070005" in msg

    def test_elevated_child_access_denied_names_the_approved_prompt(self) -> None:
        out = _classify(windows._MSG_ELEVATED_ACCESS_DENIED, elevated=False)
        # Provenance, not the parent's elevation bit: a child refusal arrives as its own
        # canonical, so the copy can say the prompt WAS approved.
        assert "even after the permission prompt was approved" in out
        assert "0x80070005" in out

    def test_no_logon_session_names_the_policy_and_the_options(self) -> None:
        for elevated in (True, False):
            out = _classify(task_com.MSG_NO_LOGON_SESSION, elevated=elevated)
            assert "Network access: Do not allow storage of passwords and credentials for network authentication" in out
            assert "Microsoft documents this when" in out
            assert "Convert page" in out
            assert "Windows account password" in out
            assert "Schedule nightly sync" in out
            assert "will not run after a reboot with no one signed in" in out
            assert "0x80070520" in out
            # No promise a retry helps, no frequency word, no markdown, no wrong direction.
            assert "try again" not in out.lower()
            assert "usually" not in out.lower()
            assert "(Details:" not in out
            assert "**" not in out
            assert "below" not in out
            assert "logged in" not in out

    def test_account_info_not_set_is_not_about_the_typed_password(self) -> None:
        out = _classify(task_com.MSG_ACCOUNT_INFO_NOT_SET, elevated=False)
        assert "isn't about the password you typed" in out
        assert "Remove nightly sync" in out
        assert "Schedule nightly sync" in out
        assert "0x8004130F" in out

    def test_credential_copy_for_the_current_account(self) -> None:
        out = _classify(task_com.MSG_LOGON_FAILURE, elevated=False, account_is_current=True)
        assert "Windows rejected the user name or password" in out
        assert "PIN" in out
        assert "microsoft.com" in out
        assert "the one you use to sign in to this computer" in out
        assert "lock the account" in out
        assert "0x8007052E" in out

    def test_credential_copy_for_another_account_drops_the_personal_coaching(self) -> None:
        out = _classify(task_com.MSG_LOGON_FAILURE, elevated=False, account_is_current=False)
        # A service account has no Windows Hello PIN and no microsoft.com password — coaching
        # a personal cloud credential into a service-account field is plan 0046's A7.
        assert "PIN" not in out
        assert "microsoft.com" not in out
        assert "the account you entered" in out
        assert "lock the account" in out
        assert "0x8007052E" in out

    def test_account_is_current_is_a_required_keyword(self) -> None:
        # G5: 0046-B cannot forget the switch — a missing argument is a TypeError (and a
        # mypy error), never a silently-personal default.
        with pytest.raises(TypeError):
            classify_schedule_error(task_com.MSG_LOGON_FAILURE, False)  # type: ignore[call-arg]

    def test_com_unavailable_offers_convert_and_carries_no_code(self) -> None:
        out = _classify(task_com.MSG_COM_UNAVAILABLE, elevated=False)
        assert "can't reach Windows Task Scheduler" in out
        assert "Convert page" in out
        assert "Help page" in out
        assert "0x" not in out  # this one has no HRESULT to show
        assert "try again" not in out.lower()

    def test_unknown_message_leads_calm_and_demotes_details(self) -> None:
        # The else branch LEADS with fixed calm copy + a support path; the raw
        # (core-sanitized) message is demoted to a trailing "(Details: …)" clause.
        # NOTE the probe: "The user name or password is incorrect." is now
        # task_com.MSG_LOGON_FAILURE, an exact-equality branch — a genuinely unclassified
        # string is needed here or this row would test the credential branch instead.
        raw = "CIM exception 0x80041318 at Microsoft.Management.Infrastructure"
        msg = _classify(raw, elevated=True)
        assert msg == (
            "The schedule change didn't go through. You can try once more; if it fails again, "
            "the Help page has our support contact — include the detail shown here. "
            f"(Details: {raw})"
        )

    def test_lowercase_access_denied_classified(self) -> None:
        # Defensive: a lowercase "access denied" phrasing still classifies.
        msg = _classify("access denied while registering", elevated=False)
        assert "Run as administrator" in msg
        assert "0x" not in msg  # a FUZZY match must not assert a status it may not have

    def test_no_markdown_asterisks_in_any_branch(self) -> None:
        # The copy carries no ``**bold**`` so a Flet verdict banner (plain ft.Text) never
        # shows literal asterisks. Seeded with the LIVE branch messages (the two retired
        # PowerShell strings this loop used to carry classified nothing after 0041 S1b —
        # it kept passing while testing only the fallback).
        for elevated in (True, False):
            for src_msg in (
                task_com.MSG_ACCESS_DENIED,
                task_com.MSG_NO_LOGON_SESSION,
                task_com.MSG_ACCOUNT_INFO_NOT_SET,
                task_com.MSG_LOGON_FAILURE,
                task_com.MSG_COM_UNAVAILABLE,
                windows._MSG_ELEVATED_ACCESS_DENIED,
            ):
                assert "**" not in _classify(src_msg, elevated=elevated)


class TestClassifierNeverLeaksSecret:
    """[SECURITY — I2] The classifier never surfaces a secret carried in ``msg``.

    On a SUBSTRING branch the returned copy is FIXED and independent of ``msg``, so a
    ``DSYNC_TASK_PW=...`` / path token smuggled into ``msg`` can NOT ride along. An
    EXACT-equality branch is unreachable unless ``msg`` IS the constant, so a secret
    cannot reach one at all. On the fallback ``msg`` passes through verbatim (the core
    owns having sanitized it); the classifier adds no credential text of its own.
    """

    def test_access_denied_not_elevated_branch_drops_secret(self) -> None:
        out = _classify(_SECRET_MSG, elevated=False)
        # Classified branch → FIXED copy, independent of msg.
        assert "Run as administrator" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_access_denied_elevated_branch_drops_secret(self) -> None:
        out = _classify(_SECRET_MSG, elevated=True)
        # POSITIVE ANCHOR — re-anchored on the new provenance copy when the batch-logon
        # coaching was retired. Without it the three negatives below would pass trivially
        # against a branch that returned "" (verified by perturbation).
        assert "Check with your IT team" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_else_branch_passes_msg_verbatim_and_adds_no_credential(self) -> None:
        # The ONE branch where msg surfaces — verbatim, demoted into the trailing
        # "(Details: …)" clause (the core sanitized it). The classifier only wraps
        # FIXED copy around it; it introduces no new secret.
        raw = "Some unclassified failure text"
        out = _classify(raw, elevated=False)
        assert out.endswith(f"(Details: {raw})")
        # Nothing beyond the fixed lead + the (core-sanitized) msg — the wrapper is
        # byte-identical no matter what msg carries.
        assert out.replace(raw, "") == (
            "The schedule change didn't go through. You can try once more; if it fails again, "
            "the Help page has our support contact — include the detail shown here. (Details: )"
        )


class TestClassifyElevationOutcomes:
    """Plan 0029 D5: the self-elevation outcome markers map to calm, bounded copy.

    The markers are single-sourced from ``register_task`` (imported constants), and the
    classify branches use exact equality so a bounded category always wins over the
    generic access-denied / fallback copy.
    """

    def test_uac_declined_says_nothing_changed(self) -> None:
        out = _classify(windows._MSG_UAC_DECLINED, elevated=False)
        assert "declined" in out.lower()
        assert "nothing was changed" in out.lower()

    def test_elevation_timeout_is_hedged_not_a_false_no_change(self) -> None:
        out = _classify(windows._MSG_ELEVATION_TIMEOUT, elevated=False)
        # HEDGED — timeout is post-consent, so it must NOT claim nothing changed / not answered.
        assert "may or may not" in out.lower()
        assert "schedule status" in out.lower()
        assert "nothing was changed" not in out.lower()
        assert "before it was answered" not in out.lower()

    def test_elevation_no_result_points_at_schedule_status(self) -> None:
        out = _classify(windows._MSG_ELEVATION_NO_RESULT, elevated=False)
        assert "couldn't confirm" in out.lower()
        assert "schedule status" in out.lower()

    def test_different_account_offers_two_fixes(self) -> None:
        out = _classify(windows._MSG_DIFFERENT_ACCOUNT, elevated=False)
        assert "different account" in out.lower()
        assert "administrator" in out.lower()
        assert "without the Windows password" in out

    def test_different_account_leads_with_the_observed_fact_not_the_inference(self) -> None:
        # A8: the PermissionError rung this message comes from also fires on a non-cross-account
        # read refusal, so the cross-account mechanism is the KNOWN TRIGGER, not a settled fact
        # (plan 0047's grounding calls it "a code reading, not a reproduction").
        out = _classify(windows._MSG_DIFFERENT_ACCOUNT, elevated=False)
        assert out.startswith("The elevated step couldn't read the request DistrictSync prepared under your account")
        # "can happen", not "happens": the same rung fires on an AV/sharing-violation or an ACL
        # refusal, so the cross-account case is the KNOWN TRIGGER, never the only one.
        assert "that can happen when" in out
        assert "run DistrictSync from there" in out

    def test_launch_failed(self) -> None:
        out = _classify(windows._MSG_ELEVATION_LAUNCH_FAILED, elevated=False)
        assert "couldn't show the permission prompt" in out.lower()

    def test_elevation_markers_ignore_elevated_flag(self) -> None:
        # Elevation copy is independent of the process elevation state (we auto-elevate now).
        for marker in (
            windows._MSG_UAC_DECLINED,
            windows._MSG_ELEVATION_TIMEOUT,
            windows._MSG_ELEVATION_NO_RESULT,
            windows._MSG_DIFFERENT_ACCOUNT,
            windows._MSG_ELEVATION_LAUNCH_FAILED,
        ):
            assert _classify(marker, elevated=True) == _classify(marker, elevated=False)

    def test_no_markdown_asterisks_in_elevation_copy(self) -> None:
        for marker in (
            windows._MSG_UAC_DECLINED,
            windows._MSG_ELEVATION_TIMEOUT,
            windows._MSG_ELEVATION_NO_RESULT,
            windows._MSG_DIFFERENT_ACCOUNT,
            windows._MSG_ELEVATION_LAUNCH_FAILED,
        ):
            assert "**" not in _classify(marker, elevated=False)


class TestElseBranchNoDeadEnd:
    """0035 W3b (T1 #2): the unclassified branch is calm-first — never a raw-text-first dead end.

    Plan 0047 G4: the fixed lead keeps a PROMISE-FREE "try once more" (the old "Try again in
    a moment" told a district whose security policy blocks the registration that waiting
    would help), keeps the support path and the details clause, and omits the clause when the
    message itself carries no detail.
    """

    def test_else_leads_with_calm_copy_not_the_raw_message(self) -> None:
        raw = "CIM exception 0x80041318 at Microsoft.Management.Infrastructure"
        out = _classify(raw, elevated=False)
        assert out.startswith("The schedule change didn't go through.")
        assert not out.startswith(raw)

    def test_else_offers_a_support_path_without_promising_a_retry_works(self) -> None:
        out = _classify("weird failure", elevated=True)
        assert "You can try once more" in out
        assert "in a moment" not in out
        assert "Help page" in out
        assert "support" in out

    def test_else_demotes_the_raw_message_to_a_trailing_details_clause(self) -> None:
        raw = "weird failure"
        out = _classify(raw, elevated=False)
        assert out.endswith(f"(Details: {raw})")
        # Demoted means AFTER the calm copy — the raw text appears exactly once, at the end.
        assert out.index(raw) > out.index("support")

    def test_else_wraps_neutrally_for_remove_failures_too(self) -> None:
        # Setup routes UNREGISTER failures through this same classifier — the fixed lead
        # must not claim a registration was attempted ("schedule change", not "register").
        out = _classify("could not delete task", elevated=False)
        assert "register" not in out.split("(Details:")[0].lower()

    def test_else_classified_branches_have_no_details_clause(self) -> None:
        # The demotion is fallback-only: classified branches keep their byte-intact fixed
        # copy. Re-seeded with the LIVE branch messages — with the two retired PowerShell
        # strings this loop was ACTIVELY wrong (they reach the fallback, which DOES append
        # a details clause).
        for known in (
            task_com.MSG_ACCESS_DENIED,
            task_com.MSG_NO_LOGON_SESSION,
            task_com.MSG_ACCOUNT_INFO_NOT_SET,
            task_com.MSG_LOGON_FAILURE,
            task_com.MSG_COM_UNAVAILABLE,
            windows._MSG_ELEVATED_ACCESS_DENIED,
        ):
            for elevated in (True, False):
                assert "(Details:" not in _classify(known, elevated=elevated)

    def test_the_no_detail_literals_get_no_details_clause(self) -> None:
        # A details clause that repeats the lead is no detail at all.
        for literal in (
            windows._MSG_CHILD_DETAIL_UNAVAILABLE,
            windows._MSG_CHILD_NO_DETAIL,
            task_com.MSG_OPERATION_FAILED,
        ):
            assert "(Details:" not in _classify(literal, elevated=False)

    def test_a_coded_generic_keeps_its_details_clause(self) -> None:
        # MSG_OPERATION_FAILED's CODED variant (the guarded-description escape) is a
        # different string and must still show the code it carries.
        coded = "The schedule operation failed (0x80070520)."
        out = _classify(coded, elevated=False)
        assert out.endswith(f"(Details: {coded})")


# ---------------------------------------------------------------------------
# The produced-vs-classified sweep (plan 0047, Approach item 6)
# ---------------------------------------------------------------------------


#: The producible message set, DERIVED rather than hand-listed: every ``MSG_``/``_MSG_``
#: string binding of the three producer modules. A new canonical is therefore red by
#: construction until it is classified or declared below with a reason.
def _producible() -> dict[str, str]:
    found: dict[str, str] = {}
    for module in (task_com, windows, elevated_apply):
        for name, value in sorted(vars(module).items()):
            if isinstance(value, str) and (name.startswith("MSG_") or name.startswith("_MSG_")):
                found[value] = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"
    return found


#: Reaches the classifier, deliberately UNCLASSIFIED — each with the reason.
_DELIBERATELY_UNCLASSIFIED: dict[str, str] = {
    # windows._MSG_ACCOUNT_NEEDS_PASSWORD left this set at plan 0046 B: the run-as field now
    # exists, so the engine refusal is reachable and has its own copy. The sweep's
    # declared-but-now-classified arm is what forced the move.
    "task_com.MSG_OPERATION_FAILED": (
        "the bare generic carries no cause to name — it is in _NO_DETAIL so the fallback does not "
        "repeat it back. Its CODED variant is a different string and keeps its details clause."
    ),
    "windows._MSG_CHILD_DETAIL_UNAVAILABLE": "a child-result floor with no cause to name; in _NO_DETAIL.",
    "windows._MSG_CHILD_NO_DETAIL": "a child-result floor with no cause to name; in _NO_DETAIL.",
    "elevated_apply._MSG_REQUEST_INVALID": (
        "an elevated-child refusal: the fallback's details clause is the whole signal."
    ),
    "elevated_apply._MSG_REQUEST_MISSING": (
        "genuinely transient — the handshake file was gone when the child ran; the cross-account "
        "case no longer reaches it (A8 split the PermissionError rung out ahead of it)."
    ),
    "elevated_apply._MSG_REQUEST_UNREADABLE": (
        "an elevated-child refusal: the fallback's details clause is the whole signal."
    ),
    "elevated_apply._MSG_CHILD_FLOOR": (
        "an elevated-child refusal: the fallback's details clause is the whole signal."
    ),
}

#: Never reaches the classifier at all — routed elsewhere, with the evidence.
_NEVER_REACHES: dict[str, str] = {
    "task_com.MSG_NOT_FOUND": (
        "its only realistic producer is the delete path, where schedule_status.interpret_unregister "
        "intercepts it via ABSENT_TASK_MARKERS ('cannot find') and renders the HEALTHY 'no schedule "
        "was registered' banner before classify_schedule_error is called."
    ),
    "windows._MSG_ELEVATION_REMOVE_UNCONFIRMED": (
        "screens/setup.py's _apply_unregister renders a fully hardcoded ErrorCard for it."
    ),
    "windows._MSG_REMOVAL_TIMED_OUT": (
        "falls through _apply_unregister's checks to interpret_unregister's fixed generic copy."
    ),
    "windows._MSG_NOT_WINDOWS": ("only populates ScheduleReadback.error, outside the classifier's three call sites."),
}


def test_the_sweep_is_watching_a_non_empty_derived_set() -> None:
    """Non-vacuity: the reflection must actually find the canonicals it is sweeping."""
    produced = _producible()
    assert len(produced) >= 15, f"the derived producible set collapsed to {len(produced)} — the sweep is vacuous"
    for label in list(_DELIBERATELY_UNCLASSIFIED) + list(_NEVER_REACHES):
        assert label in produced.values(), f"{label} is declared here but no longer produced — drop or rename it"


def test_every_producible_message_is_classified_or_declared() -> None:
    """A new canonical is RED by construction until someone decides what it should say."""
    declared = set(_DELIBERATELY_UNCLASSIFIED) | set(_NEVER_REACHES)
    unclassified: set[str] = set()
    for value, label in _producible().items():
        classified = any(
            _classify(value, elevated=elevated, account_is_current=current) != _unclassified_copy(value)
            for elevated in (True, False)
            for current in (True, False)
        )
        if not classified:
            unclassified.add(label)
    assert unclassified == declared, (
        f"unclassified-but-undeclared: {sorted(unclassified - declared)}; "
        f"declared-but-now-classified: {sorted(declared - unclassified)}"
    )


def test_every_declared_reason_is_a_real_reason() -> None:
    """A bucket entry without a reason is a TODO wearing a decision's clothes."""
    for label, reason in {**_DELIBERATELY_UNCLASSIFIED, **_NEVER_REACHES}.items():
        assert len(reason) > 40, f"{label}'s reason is too thin to be a decision"


@pytest.mark.parametrize(
    ("message", "cause_phrase"),
    [
        (task_com.MSG_NO_LOGON_SESSION, "would not save the password"),
        (task_com.MSG_ACCOUNT_INFO_NOT_SET, "is missing the nightly task's own saved account details"),
        (task_com.MSG_LOGON_FAILURE, "rejected the user name or password"),
        (task_com.MSG_COM_UNAVAILABLE, "can't reach Windows Task Scheduler"),
        (windows._MSG_ELEVATED_ACCESS_DENIED, "is likely blocking the schedule change"),
        (task_com.MSG_ACCESS_DENIED, "is likely blocking the schedule change"),
        (task_com.MSG_ACCOUNT_NOT_RECOGNIZED, "doesn't recognise that account name"),
        (windows._MSG_ACCOUNT_NEEDS_PASSWORD, "needs that account's password"),
    ],
)
def test_every_new_branch_leads_with_the_cause(message: str, cause_phrase: str) -> None:
    """All five render under ONE red headline ("Couldn't schedule the nightly sync"), so a
    first sentence that names the OUTCOME repeats the headline and tells the admin nothing."""
    out = _classify(message, elevated=True)
    assert cause_phrase in _first_sentence(out), f"the first sentence of {out!r} does not name the cause"


def test_no_classifier_string_says_below_or_logged_in() -> None:
    """Vocabulary: the readout renders ABOVE the result slot, and the schedule section's
    spelling is "signed in / signed out" (the password field's caption owns it)."""
    probes = [*_producible(), "access denied while registering", "something unclassified"]
    for probe in probes:
        for elevated in (True, False):
            for current in (True, False):
                out = _classify(probe, elevated=elevated, account_is_current=current)
                assert "below" not in out, f"{probe!r} classifies with a direction word"
                # The fallback echoes msg verbatim; only the classifier's OWN copy is swept.
                assert "logged in" not in out.split("(Details:")[0]


# ---------------------------------------------------------------------------
# Plan 0046 B — the two run-as-account branches (A7 completed)
# ---------------------------------------------------------------------------


class TestAccountNotRecognized:
    """0x80070534, MEASURED 2026-09-16 on our own COM path: a mistyped run-as ACCOUNT NAME.
    Distinct from a wrong password (0x8007052E), so it gets its own branch — dropping a name
    typo into the credential branch loops the admin on the password forever."""

    def _out(self) -> str:
        return _classify(task_com.MSG_ACCOUNT_NOT_RECOGNIZED, elevated=True)

    def test_it_is_not_the_generic_fallback(self) -> None:
        assert self._out() != _unclassified_copy(task_com.MSG_ACCOUNT_NOT_RECOGNIZED)

    def test_it_carries_the_measured_code(self) -> None:
        assert "0x80070534" in self._out()

    def test_it_is_about_the_name_not_the_password(self) -> None:
        out = self._out()
        assert "name" in out
        assert "spelling" in out
        assert "not the password" in out

    def test_it_names_the_field_by_its_single_sourced_label(self) -> None:
        # Substring against the CONSTANT, not a literal: the box on screen and the sentence
        # pointing at it can never drift apart.
        assert f"'{SCHEDULE_ACCOUNT_FIELD_LABEL}'" in self._out()


class TestAccountNeedsPassword:
    """The engine's own pre-flight refusal. B's Register gate refuses this state before
    dispatch, so this branch is a gate/engine DRIFT FLOOR — classified anyway, because if the
    two comparisons ever diverge the admin must read a next step, not a raw canonical."""

    def _out(self) -> str:
        return _classify(windows._MSG_ACCOUNT_NEEDS_PASSWORD, elevated=False)

    def test_it_is_not_the_generic_fallback(self) -> None:
        assert self._out() != _unclassified_copy(windows._MSG_ACCOUNT_NEEDS_PASSWORD)

    def test_it_shows_no_windows_code(self) -> None:
        # It is OUR refusal — nothing reached Windows, so claiming a status would be a lie.
        assert "0x" not in self._out()
        assert task_com.hresult_for(windows._MSG_ACCOUNT_NEEDS_PASSWORD) is None

    def test_it_names_the_field_and_the_clear_it_escape(self) -> None:
        out = self._out()
        assert f"'{SCHEDULE_ACCOUNT_FIELD_LABEL}'" in out
        assert "clear" in out
        assert "signed in with" in out

    def test_it_is_no_longer_declared_unclassified(self) -> None:
        # The positive twin of the sweep's declared-but-now-classified arm.
        assert "windows._MSG_ACCOUNT_NEEDS_PASSWORD" not in _DELIBERATELY_UNCLASSIFIED
        assert "windows._MSG_ACCOUNT_NEEDS_PASSWORD" not in _NEVER_REACHES


# ---------------------------------------------------------------------------
# Plan 0049 S-2b.1 — the provisioning step ids
# ---------------------------------------------------------------------------


#: Steps whose state repeats identically forever, so the copy must NOT tell an admin to
#: retry. An override still in place, a folder we may not adopt, permissions that came out
#: wrong, a leftover directory, an incomplete prune, a request from the wrong account —
#: every retry fails the same way, and "try again" is how an admin spends an afternoon.
_NO_RETRY_STEPS = frozenset(
    {
        ProvisionStep.OVERRIDE,
        ProvisionStep.SOURCE,
        ProvisionStep.PRE_EXISTING,
        ProvisionStep.VERIFY,
        ProvisionStep.ROLLBACK,
        ProvisionStep.PRUNE,
    }
)

_RETRY_PHRASES = ("try again", "try once more")


class TestClassifyProvisionStep:
    """A SEPARATE classifier, because ``ProvisionRefused.message`` is not a stable constant.

    ``classify_schedule_error`` keys by exact equality, and that message interpolates the
    step plus (optionally) an icacls exit code and a rollback sentence — so ONE
    ``ProvisionStep.CREATE`` failure produces several strings and could never match a
    branch. The bounded STEP is what is stable, so the step is what is classified.
    """

    def test_every_step_id_has_copy(self) -> None:
        """The COMPLETENESS test, shaped on ``launcher._MACHINE_SCOPE_CAUSES``.

        **This is the hole-closer.** The reflection sweep above
        (``test_every_producible_message_is_classified_or_declared``) derives its producible
        set from ``task_com`` / ``windows`` / ``elevated_apply`` ONLY, so it would never see
        a new ``ProvisionStep`` with no copy. Without this test a new step id ships silently
        and an admin reads the generic "you can try once more" for a state retrying cannot
        fix. Proven non-vacuous by deleting a branch and watching this go red.
        """
        assert set(setup_errors._PROVISION_STEP_COPY) == set(ProvisionStep)
        for step in ProvisionStep:
            assert classify_provision_step(step), step.value

    def test_no_two_steps_read_the_same(self) -> None:
        """A shared sentence is a step id that was added without a decision."""
        rendered = [classify_provision_step(step) for step in ProvisionStep]
        assert len(set(rendered)) == len(list(ProvisionStep))

    @pytest.mark.parametrize("step", list(ProvisionStep))
    def test_it_is_never_the_generic_schedule_fallback(self, step: ProvisionStep) -> None:
        assert classify_provision_step(step) != _unclassified_copy(step.value)
        assert classify_provision_step(step) != _unclassified_copy(
            ProvisionRefused(step).message,
        )

    @pytest.mark.parametrize("step", sorted(_NO_RETRY_STEPS))
    def test_an_unfixable_state_is_never_told_to_retry(self, step: ProvisionStep) -> None:
        out = classify_provision_step(step).lower()
        for phrase in _RETRY_PHRASES:
            assert phrase not in out, f"{step.value} offers a retry for a state retrying cannot fix"

    def test_a_genuinely_transient_state_does_offer_a_retry(self) -> None:
        """The positive twin: the rule above is a rule, not a blanket ban on the phrase."""
        offered = {
            step for step in ProvisionStep if any(phrase in classify_provision_step(step) for phrase in _RETRY_PHRASES)
        }
        assert offered, "no step offers a retry — the no-retry sweep is vacuous"
        assert offered.isdisjoint(_NO_RETRY_STEPS)

    @pytest.mark.parametrize("step", list(ProvisionStep))
    def test_it_carries_no_path_no_stderr_and_no_code(self, step: ProvisionStep) -> None:
        """The step vocabulary exists so a refusal cannot carry a resolved path (which
        embeds an account name) or an ``icacls`` stderr line. Re-introducing one in the COPY
        would hand that straight back; the exit code travels on ``ProvisionAttempt``."""
        out = classify_provision_step(step)
        for banned in ("\\", "/", "icacls", "stderr", "0x", "C:", "ProgramData"):
            assert banned not in out, f"{step.value} leaks {banned!r}"

    @pytest.mark.parametrize("step", list(ProvisionStep))
    def test_it_is_plain_prose_a_flet_text_can_render(self, step: ProvisionStep) -> None:
        # Same rule as every branch above: ErrorCard/HealthVerdictBanner render `detail` as
        # a plain ft.Text, so markdown would show literal asterisks.
        out = classify_provision_step(step)
        assert "**" not in out
        assert "below" not in out  # the readout renders ABOVE the result slot
        assert out.endswith(".")

    def test_the_interpolated_message_would_never_have_classified(self) -> None:
        """Why this function exists at all, asserted rather than asserted-in-prose: the SAME
        step produces different messages, and none of them matches a classifier branch."""
        plain = ProvisionRefused(ProvisionStep.CREATE).message
        with_code = ProvisionRefused(ProvisionStep.CREATE, icacls_exit=5).message
        with_rollback = ProvisionRefused(ProvisionStep.CREATE, rollback_failed=True).message
        assert len({plain, with_code, with_rollback}) == 3
        for message in (plain, with_code, with_rollback):
            assert _classify(message, elevated=True) == _unclassified_copy(message)

    def test_an_unknown_step_degrades_instead_of_raising(self) -> None:
        """Only reachable if a member is added without copy — which the completeness test
        makes red — but a paint path may never raise, so it falls back rather than KeyError."""
        assert classify_provision_step("a_step_from_the_future") == _unclassified_copy(  # type: ignore[arg-type]
            "a_step_from_the_future"
        )
