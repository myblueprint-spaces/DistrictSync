"""Pure submit-gate predicates for the Setup screen (COUNTED, no flet import).

VIEW glue lives in ``screens/setup.py`` (coverage-omitted). The trust-critical
*decision* — "may this action fire?" — is extracted here so it is unit-tested and
single-sourced: the button's ``disabled`` state AND the Enter-to-submit
(``on_submit``) handler read the SAME predicate, so pressing Enter can never bypass
a gate that a disabled button structurally enforces.

Mirrors the folders save-gate that already lives purely in
``filepicker.setup_state`` — these cover the *schedule* and *SFTP* sections.
"""

from __future__ import annotations

from src.utils.validators import validate_month_day


def window_settings_valid(enabled: bool, start_md: str, end_md: str) -> bool:
    """The seasonal-window save/advance gate — the "Enter can't bypass an invalid window" guarantee.

    Single-sources the gate the wizard's Continue button (via ``setup_flow.FlowInputs.window_valid``
    → ``can_advance``) AND the section's on-change persistence both read, so an invalid enabled
    window can neither be advanced past nor saved. Reuses the ENGINE validator ``validate_month_day``
    (one definition of "is this a real MM-DD boundary?") rather than re-parsing here.

    * **Disabled** → always valid: the window is off (year-round), the fields are ignored.
    * **Enabled** → both ``start_md`` and ``end_md`` must be real ``"MM-DD"`` calendar days. A blank
      / malformed either bound closes the gate (``None`` is tolerated as blank — never a raise).
    """
    if not enabled:
        return True
    try:
        validate_month_day(start_md or "")
        validate_month_day(end_md or "")
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def can_register_schedule(config_complete: bool, run_time: str) -> bool:
    """The Register-schedule gate.

    The folders/district config must be complete AND a non-blank run time entered.
    Single-sources the gate the Register button encodes so the button's ``disabled``
    state and the run-time / Windows-password ``on_submit`` handlers agree.
    """
    return bool(config_complete) and bool((run_time or "").strip())


def can_save_sftp(
    *,
    host: str,
    username: str,
    remote_path: str,
    password: str,
    already_configured: bool,
) -> bool:
    """The Save-SFTP-credentials gate.

    Host, username and remote path are always required. A password is required the
    FIRST time (no stored credential yet); on a re-save an existing stored credential
    may be kept by leaving the password blank. Single-sources the gate the Save button
    encodes so the four SFTP ``on_submit`` handlers can't bypass it.
    """
    has_required = bool((host or "").strip() and (username or "").strip() and (remote_path or "").strip())
    return has_required and (bool(password) or already_configured)
