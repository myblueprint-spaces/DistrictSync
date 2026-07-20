"""Pure schedule-error classifier for the Setup surface — the single source.

NO ``flet`` import, no UI-framework import — this is presentation-neutral,
cheaply-tested logic that maps a schedule-registration failure into a calm,
actionable, plain-language message. It is pure logic the Flet Setup surface
(``screens/setup.py``) consumes; it was extracted here so the classification
lives in one tested place, independent of any view layer.

``classify_schedule_error(msg, elevated)`` is keyed off the canonical substrings
``register_task`` publishes (``"PowerShell not found"`` /
``"ScheduledTasks module not available"`` / Windows' own ``"Access is denied"``)
plus whether the process is elevated (:func:`src.scheduler.windows.is_elevated`).

**Self-elevation outcomes (Plan 0029, D5):** on the self-elevated register/unregister
path, ``register_task`` returns one of a small set of canonical, secret-free elevation
markers (imported here as exact constants so the copy can never drift from the producer).
Each maps to a calm, bounded category — the un-elevated "run as administrator" branch is
**superseded on the register path** (elevation replaces that dead-end) but is kept for any
residual raw access-denied message so the classifier stays total.

**Security (I2):** ``msg`` is the ALREADY-sanitized ``register_task`` return —
``_clean_ps_stderr`` has de-CLIXML'd it and stripped any ``DSYNC_*`` secret. The
classifier does NOT re-sanitize; it only maps substrings + appends FIXED copy.
The ``else`` branch surfaces ``msg`` verbatim, which is safe because the core
already guarantees no ``DSYNC_TASK_PW`` literal/value reaches the message. The
returned copy is **plain prose** (no ``**markdown**``) because the Flet
``HealthVerdictBanner``/``ErrorCard`` render ``detail`` as a plain ``ft.Text``,
which would show literal asterisks.
"""

from __future__ import annotations

from src.scheduler.windows import (
    _MSG_DIFFERENT_ACCOUNT,
    _MSG_ELEVATION_LAUNCH_FAILED,
    _MSG_ELEVATION_NO_RESULT,
    _MSG_ELEVATION_TIMEOUT,
    _MSG_UAC_DECLINED,
)


def classify_schedule_error(msg: str, elevated: bool) -> str:
    """Map a (now clean) registration error into a calm, actionable message.

    Args:
        msg: The de-CLIXML'd, secret-stripped ``register_task`` failure message.
            Safe to surface verbatim in the ``else`` branch — the core owns
            having sanitized it (this function does not re-sanitize).
        elevated: Whether the current process runs with administrator rights
            (:func:`src.scheduler.windows.is_elevated`). Distinguishes an
            un-elevated access-denied (→ run as administrator) from an elevated
            one (→ batch-logon-right / wrong-password), so an already-elevated
            admin is not sent in circles.

    Returns:
        A plain-language, actionable message (plain prose — no markdown, so a
        Flet verdict banner renders it cleanly).
    """
    # Self-elevation outcomes (D5) — exact canonical markers from register_task's
    # elevated path. Checked first (and by exact equality) so a bounded category always
    # wins over the generic access-denied / else copy; the copy is single-sourced here.
    if msg == _MSG_UAC_DECLINED:
        return "You declined the Windows permission prompt — nothing was changed."
    if msg == _MSG_ELEVATION_TIMEOUT:
        # HEDGED (honesty): a timeout is only reachable AFTER the prompt was accepted, and the
        # terminated child may already have created the schedule — never claim "before it was
        # answered" / "nothing was changed". The register flow already tried a read-back.
        return (
            "DistrictSync stopped waiting for the elevated request to finish — the nightly sync "
            "may or may not have been scheduled. Check the schedule status below, then schedule "
            "it again if needed."
        )
    if msg == _MSG_ELEVATION_NO_RESULT:
        return (
            "The permission prompt was accepted but we couldn't confirm the schedule change — "
            "check the schedule status below."
        )
    if msg == _MSG_DIFFERENT_ACCOUNT:
        return (
            "The permission prompt ran as a different account — log in as an administrator, or "
            "schedule the nightly sync without the Windows password (runs only while you're logged in)."
        )
    if msg == _MSG_ELEVATION_LAUNCH_FAILED:
        return (
            "Windows couldn't show the permission prompt. Try again, or run DistrictSync as an "
            "administrator to schedule the nightly sync."
        )

    access_denied = "Access is denied" in msg or "access denied" in msg.lower()

    if "PowerShell not found" in msg:
        return (
            "Windows PowerShell wasn't found on this machine, so the schedule can't be created. "
            "PowerShell ships with Windows 8 / Server 2012 and newer — if it's missing, this "
            "server is unsupported for automated scheduling. You can still run conversions manually."
        )
    if "ScheduledTasks module not available" in msg:
        return (
            "This Windows version is too old to schedule tasks this way (it needs Windows 8 / "
            "Server 2012 or newer). You can still run conversions manually from the Convert page."
        )
    if access_denied and not elevated:
        return (
            "Permission denied — right-click the application and choose Run as administrator, "
            "then try again. (Creating an unattended task needs administrator rights.)"
        )
    if access_denied and elevated:
        return (
            "Windows refused the schedule change even though you're running as administrator. The account "
            "likely can't be used for an unattended task: make sure you entered your Windows "
            "account password (not your Windows Hello PIN — for a Microsoft Account, your "
            "microsoft.com password), and that the account is allowed to "
            "'Log on as a batch job'."
        )
    # Unclassified (0035 W3b, T1 #2): lead with calm FIXED copy + a support path, and demote
    # the raw (already-sanitized) message to a trailing details clause — the admin reads a
    # next step first, never a wall of PowerShell. Phrased "schedule change" (not "register")
    # because Setup routes remove failures through this same classifier. ``msg`` still passes
    # through VERBATIM inside the parenthetical (the core owns having sanitized it — the
    # non-leak contract in the module docstring is unchanged).
    return (
        "The schedule change didn't go through. Try again in a moment — if it keeps failing, "
        f"the Help page has our support contact. (Details: {msg})"
    )
