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

    def test_unknown_message_passes_through_clean(self) -> None:
        msg = classify_schedule_error("The user name or password is incorrect.", elevated=True)
        assert msg == "Failed to register schedule: The user name or password is incorrect."

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
        # The ONE branch where msg surfaces — verbatim (the core sanitized it).
        # The classifier only prefixes fixed copy; it introduces no new secret.
        raw = "Some unclassified failure text"
        out = classify_schedule_error(raw, elevated=False)
        assert out == f"Failed to register schedule: {raw}"
        # Nothing beyond the fixed prefix + the (core-sanitized) msg.
        assert out.replace(raw, "") == "Failed to register schedule: "


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
