"""Pure schedule-error classifier for the Setup surface — the single source.

NO ``flet`` import, NO ``streamlit`` import — this is presentation-neutral,
cheaply-tested logic that maps a schedule-registration failure into a calm,
actionable, plain-language message. It was relocated out of the Streamlit
``01_Setup_Wizard.py`` page (where it was UI-layer code trapped behind a
file-path import) so the Flet Setup surface and the (CUT-1-doomed) Streamlit
page share ONE implementation.

``classify_schedule_error(msg, elevated)`` is keyed off the canonical substrings
``register_task`` publishes (``"PowerShell not found"`` /
``"ScheduledTasks module not available"`` / Windows' own ``"Access is denied"``)
plus whether the process is elevated (:func:`src.scheduler.windows.is_elevated`).

**Security (I2):** ``msg`` is the ALREADY-sanitized ``register_task`` return —
``_clean_ps_stderr`` has de-CLIXML'd it and stripped any ``DSYNC_*`` secret. The
classifier does NOT re-sanitize; it only maps substrings + appends FIXED copy.
The ``else`` branch surfaces ``msg`` verbatim, which is safe because the core
already guarantees no ``DSYNC_TASK_PW`` literal/value reaches the message. The
returned copy is **plain prose** (no ``**markdown**``) because the Flet
``HealthVerdictBanner``/``ErrorCard`` render ``detail`` as a plain ``ft.Text``,
which would show literal asterisks; the Streamlit shim renders it via
``st.error``, where plain text is equally fine.
"""

from __future__ import annotations


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
            "Registration was denied even though you're running as administrator. The account "
            "likely can't be used for an unattended task: make sure you entered your Windows "
            "account password (not your Windows Hello PIN — for a Microsoft Account, your "
            "microsoft.com password), and that the account is allowed to "
            "'Log on as a batch job'."
        )
    return f"Failed to register schedule: {msg}"
