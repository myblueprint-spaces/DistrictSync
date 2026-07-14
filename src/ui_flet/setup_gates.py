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
