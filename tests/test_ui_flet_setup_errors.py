"""Tests for the relocated Setup schedule-error classifier (IA-4a).

``src.ui_flet.setup_errors.classify_schedule_error`` is the single source for
mapping a (de-CLIXML'd) ``register_task`` failure message + the process
elevation state into a calm, actionable message. It is pure (no flet import)
so it is unit-testable headless.

Two concerns are covered:

1. **Behaviour** — the six ported cases, asserting on the PLAIN substrings (the
   relocation stripped the ``**markdown**`` bold so a Flet verdict banner renders
   clean prose, not literal asterisks).
2. **[SECURITY — I2] Non-leak proof** — the classifier is a faithful,
   non-leaking mapper: on a KNOWN-substring branch it returns FIXED copy
   independent of ``msg`` (so a secret riding in ``msg`` can NOT ride along into
   a classified branch); on the ``else`` branch ``msg`` passes through verbatim
   (the core owns having sanitized it — this test does NOT re-test the core's
   de-CLIXML). The classifier itself introduces no credential text.
"""

from __future__ import annotations

from src.scheduler import windows
from src.ui_flet.setup_errors import classify_schedule_error

# A fake secret + path smuggled inside ``msg`` — used to prove that a classified
# (known-substring) branch returns FIXED copy that does NOT echo it.
_SECRET_MSG = "Access is denied. DSYNC_TASK_PW=hunter2 C:\\Users\\x\\secret"


class TestClassifyScheduleError:
    def test_not_elevated_access_denied_says_run_as_admin(self) -> None:
        msg = classify_schedule_error("Access is denied.", elevated=False)
        assert "Run as administrator" in msg
        assert "administrator rights" in msg

    def test_elevated_access_denied_points_at_credentials(self) -> None:
        msg = classify_schedule_error("Access is denied.", elevated=True)
        # Does NOT send an already-elevated admin in circles.
        assert "Run as administrator" not in msg
        assert "Log on as a batch job" in msg
        assert "Windows account password" in msg
        assert "PIN" in msg

    def test_powershell_not_found(self) -> None:
        # Elevation is irrelevant here — the PS message wins regardless.
        for elevated in (True, False):
            msg = classify_schedule_error("PowerShell not found", elevated=elevated)
            assert "PowerShell wasn't found" in msg
            assert "Run as administrator" not in msg

    def test_scheduledtasks_module_missing(self) -> None:
        for elevated in (True, False):
            msg = classify_schedule_error("ScheduledTasks module not available", elevated=elevated)
            assert "too old to schedule tasks" in msg
            assert "Run as administrator" not in msg

    def test_unknown_message_leads_calm_and_demotes_details(self) -> None:
        # 0035 W3b (T1 #2): the else branch LEADS with fixed calm copy + a support path;
        # the raw (core-sanitized) message is demoted to a trailing "(Details: …)" clause.
        msg = classify_schedule_error("The user name or password is incorrect.", elevated=True)
        assert msg == (
            "The schedule change didn't go through. Try again in a moment — if it keeps failing, "
            "the Help page has our support contact. "
            "(Details: The user name or password is incorrect.)"
        )

    def test_lowercase_access_denied_classified(self) -> None:
        # Defensive: a lowercase "access denied" phrasing still classifies.
        msg = classify_schedule_error("access denied while registering", elevated=False)
        assert "Run as administrator" in msg

    def test_no_markdown_asterisks_in_any_branch(self) -> None:
        # The relocation stripped ``**bold**`` so a Flet verdict banner (plain
        # ft.Text) never shows literal asterisks.
        for elevated in (True, False):
            for src_msg in (
                "Access is denied.",
                "PowerShell not found",
                "ScheduledTasks module not available",
            ):
                assert "**" not in classify_schedule_error(src_msg, elevated=elevated)


class TestClassifierNeverLeaksSecret:
    """[SECURITY — I2] The classifier never surfaces a secret carried in ``msg``.

    For each KNOWN-substring branch the returned copy is FIXED and independent of
    ``msg``, so a ``DSYNC_TASK_PW=...`` / path token smuggled into ``msg`` can NOT
    ride along. On the ``else`` branch ``msg`` passes through verbatim (the core
    owns having sanitized it); the classifier adds no credential text of its own.
    """

    def test_access_denied_not_elevated_branch_drops_secret(self) -> None:
        out = classify_schedule_error(_SECRET_MSG, elevated=False)
        # Classified branch → FIXED copy, independent of msg.
        assert "Run as administrator" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_access_denied_elevated_branch_drops_secret(self) -> None:
        out = classify_schedule_error(_SECRET_MSG, elevated=True)
        assert "Log on as a batch job" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_powershell_branch_drops_secret(self) -> None:
        # A secret smuggled alongside the PowerShell substring is dropped.
        out = classify_schedule_error("PowerShell not found DSYNC_TASK_PW=hunter2 secret", elevated=False)
        assert "PowerShell wasn't found" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_scheduledtasks_branch_drops_secret(self) -> None:
        out = classify_schedule_error("ScheduledTasks module not available DSYNC_TASK_PW=hunter2 secret", elevated=True)
        assert "too old to schedule tasks" in out
        assert "hunter2" not in out
        assert "secret" not in out
        assert "DSYNC_TASK_PW" not in out

    def test_else_branch_passes_msg_verbatim_and_adds_no_credential(self) -> None:
        # The ONE branch where msg surfaces — verbatim, demoted into the trailing
        # "(Details: …)" clause (the core sanitized it). The classifier only wraps
        # FIXED copy around it; it introduces no new secret.
        raw = "Some unclassified failure text"
        out = classify_schedule_error(raw, elevated=False)
        assert out.endswith(f"(Details: {raw})")
        # Nothing beyond the fixed lead + the (core-sanitized) msg — the wrapper is
        # byte-identical no matter what msg carries.
        assert out.replace(raw, "") == (
            "The schedule change didn't go through. Try again in a moment — if it keeps failing, "
            "the Help page has our support contact. (Details: )"
        )


class TestClassifyElevationOutcomes:
    """Plan 0029 D5: the self-elevation outcome markers map to calm, bounded copy.

    The markers are single-sourced from ``register_task`` (imported constants), and the
    classify branches use exact equality so a bounded category always wins over the
    generic access-denied / else copy. Elevation replaces the old un-elevated "run as
    administrator" dead-end on the register path.
    """

    def test_uac_declined_says_nothing_changed(self) -> None:
        out = classify_schedule_error(windows._MSG_UAC_DECLINED, elevated=False)
        assert "declined" in out.lower()
        assert "nothing was changed" in out.lower()

    def test_elevation_timeout_is_hedged_not_a_false_no_change(self) -> None:
        out = classify_schedule_error(windows._MSG_ELEVATION_TIMEOUT, elevated=False)
        # HEDGED — timeout is post-consent, so it must NOT claim nothing changed / not answered.
        assert "may or may not" in out.lower()
        assert "schedule status" in out.lower()
        assert "nothing was changed" not in out.lower()
        assert "before it was answered" not in out.lower()

    def test_elevation_no_result_points_at_schedule_status(self) -> None:
        out = classify_schedule_error(windows._MSG_ELEVATION_NO_RESULT, elevated=False)
        assert "couldn't confirm" in out.lower()
        assert "schedule status" in out.lower()

    def test_different_account_offers_two_fixes(self) -> None:
        out = classify_schedule_error(windows._MSG_DIFFERENT_ACCOUNT, elevated=False)
        assert "different account" in out.lower()
        assert "administrator" in out.lower()
        assert "without the Windows password" in out

    def test_launch_failed(self) -> None:
        out = classify_schedule_error(windows._MSG_ELEVATION_LAUNCH_FAILED, elevated=False)
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
            assert classify_schedule_error(marker, elevated=True) == classify_schedule_error(marker, elevated=False)

    def test_no_markdown_asterisks_in_elevation_copy(self) -> None:
        for marker in (
            windows._MSG_UAC_DECLINED,
            windows._MSG_ELEVATION_TIMEOUT,
            windows._MSG_ELEVATION_NO_RESULT,
            windows._MSG_DIFFERENT_ACCOUNT,
            windows._MSG_ELEVATION_LAUNCH_FAILED,
        ):
            assert "**" not in classify_schedule_error(marker, elevated=False)


class TestElseBranchNoDeadEnd:
    """0035 W3b (T1 #2): the unclassified branch is calm-first — never a raw-text-first dead end.

    The fixed lead offers a next step (try again) and a support path (the Help page)
    BEFORE any technical text; the raw PowerShell message survives only as the trailing
    parenthetical so support can still diagnose from a screenshot.
    """

    def test_else_leads_with_calm_copy_not_the_raw_message(self) -> None:
        raw = "CIM exception 0x80041318 at Microsoft.Management.Infrastructure"
        out = classify_schedule_error(raw, elevated=False)
        assert out.startswith("The schedule change didn't go through.")
        assert not out.startswith(raw)

    def test_else_offers_a_support_path_and_a_retry(self) -> None:
        out = classify_schedule_error("weird failure", elevated=True)
        assert "Try again" in out
        assert "Help page" in out
        assert "support" in out

    def test_else_demotes_the_raw_message_to_a_trailing_details_clause(self) -> None:
        raw = "weird failure"
        out = classify_schedule_error(raw, elevated=False)
        assert out.endswith(f"(Details: {raw})")
        # Demoted means AFTER the calm copy — the raw text appears exactly once, at the end.
        assert out.index(raw) > out.index("support")

    def test_else_wraps_neutrally_for_remove_failures_too(self) -> None:
        # Setup routes UNREGISTER failures through this same classifier — the fixed lead
        # must not claim a registration was attempted ("schedule change", not "register").
        out = classify_schedule_error("could not delete task", elevated=False)
        assert "register" not in out.split("(Details:")[0].lower()

    def test_else_classified_branches_have_no_details_clause(self) -> None:
        # The demotion is else-only: classified branches keep their byte-intact fixed copy.
        for known in (
            "PowerShell not found",
            "ScheduledTasks module not available",
            "Access is denied.",
        ):
            for elevated in (True, False):
                assert "(Details:" not in classify_schedule_error(known, elevated=elevated)
