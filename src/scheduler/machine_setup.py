"""Machine setup, shared by ``--machine-setup`` and the window's first-run step (plan 0055 M2a/M2b).

ONE decision ladder (:func:`run`) and ONE copy table for both callers, and the ONE predicate that says a machine-wide
install has not been set up yet (:func:`needs_machine_setup`).

**A machine-wide install refuses a per-user profile (M2b).** On the MSI install (``install_kind() is MACHINE``) the
program is meant for every account on the computer, and settings kept in one person's profile are exactly what other
accounts (a service account, a gMSA) cannot read. So until machine setup has switched the computer to the shared
profile, the window shows only the setup step and the command line refuses everything except ``--machine-setup``.
``DISTRICTSYNC_DATA_DIR`` (a test/support profile) is exempt.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from src.update import policy

logger = logging.getLogger(__name__)

#: What machine setup says for each bounded outcome. Plain words for a district IT person; no account, no path from
#: this computer except the shared folder's fixed location.
MACHINE_SETUP_DONE = "This computer now keeps one shared DistrictSync profile in C:\\ProgramData\\DistrictSync."
MACHINE_SETUP_COPY: dict[str, str] = {
    "declined": "The Windows permission prompt was declined, so nothing was changed.",
    "launch_failed": "Windows did not show the permission prompt, so nothing was changed.",
    "unconfirmed": "The elevated step did not report back in time; see the line below for what is now true.",
    "different_account": (
        "The permission prompt was answered by a different administrator, so nothing was changed. "
        "Run this as the administrator who will look after DistrictSync."
    ),
    "unavailable": "The machine setup could not be started on this computer (see the log), so nothing was changed.",
    "failed": "The machine setup failed (see the log).",
}
#: What a committed but unusable shared folder means (the child committed against a folder the app refuses).
MACHINE_SETUP_REFUSED_FOLDER = (
    "The shared profile is switched on, but DistrictSync cannot use the folder C:\\ProgramData\\DistrictSync "
    "(reason: {reason}). Run DistrictSync.exe --diagnose and send the report to your support contact."
)
#: Outcomes in which the elevated step never ran, so nothing was changed.
MACHINE_SETUP_NOT_RUN = frozenset({"declined", "launch_failed", "different_account", "unavailable"})
PER_USER_COPY_REFUSED = (
    "Error: this copy is installed for one Windows account only, so it cannot be set up for the whole computer. "
    "Install the machine-wide DistrictSync, then run --machine-setup from it."
)
#: The command line's "nothing switched" tail. The window shows :data:`WINDOW_NOT_SET_UP` instead: on a machine-wide
#: install per-account settings are refused, so this sentence would describe a state the window cannot use.
NOT_HANDED_OVER = "This computer still keeps DistrictSync settings per Windows account."
WINDOW_NOT_SET_UP = (
    "DistrictSync is not set up on this computer yet. Try again, or an administrator can run "
    "DistrictSync.exe --machine-setup --updates notify from a command prompt."
)
#: The command line on a machine-wide install that has not been set up (M2b).
NOT_SET_UP_CLI = (
    "DistrictSync is installed for the whole computer but has not been set up yet. An administrator runs "
    "DistrictSync.exe --machine-setup --updates notify (or opens DistrictSync once), then this command works."
)


@dataclass(frozen=True)
class MachineSetupResult:
    """``code`` 0 only when the shared profile is in use and this run is known to have set it; ``lines`` to show."""

    code: int
    lines: tuple[str, ...]
    handed_over: bool


def needs_machine_setup() -> bool:
    """A machine-wide install whose computer is provably NOT set up for the shared profile. Never raises.

    ``False`` for every other install kind, under ``DISTRICTSYNC_DATA_DIR``, and when the switch cannot be read (the
    existing machine-scope refusal handles an unreadable switch; this gate claims only what it can prove).
    """
    try:
        from src.utils import paths
        from src.utils.install_kind import InstallKind, install_kind

        if paths.data_dir_override_active() or install_kind() is not InstallKind.MACHINE:
            return False
        from src.scheduler import provision_session

        return provision_session.switch_state() is False
    except Exception:  # noqa: BLE001 - a gate input never breaks a start; the per-user ladder is the old behaviour
        logger.warning("Could not tell whether this computer needs machine setup; continuing as before.")
        return False


def delivery_seed() -> tuple[bool, str, str]:
    """``(sftp, host, username)`` from the PER-USER settings, read without pinning a profile (the setup seeds them).

    Parsed by AppConfig's OWN reader (``_config_from_bytes``: a wrong-typed value makes the document unreadable), never a
    second parser; no settings, or unreadable ones, seed no delivery.
    """
    try:
        from src.config.app_config import CONFIG_FILENAME, _config_from_bytes
        from src.utils import paths

        cfg = _config_from_bytes((paths.per_user_data_dir() / CONFIG_FILENAME).read_bytes())
    except OSError:
        return (False, "", "")
    if cfg is None:
        return (False, "", "")
    return (bool(cfg.sftp_enabled), cfg.sftp_host or "", cfg.sftp_username or "")


def run(update_mode: str) -> MachineSetupResult:
    """Provision the shared profile behind one UAC prompt, then hand this process over to it.

    The elevated half is the existing provisioning child; the parent half (``complete_handover``: re-pin, fence,
    ``MOVED.txt``) runs here. No nightly is registered.
    """
    from src.scheduler import provision_session
    from src.scheduler.task_com import runs_from_per_user_install

    if runs_from_per_user_install(sys.executable):
        return MachineSetupResult(1, (PER_USER_COPY_REFUSED,), False)
    mode = policy.parse_update_mode(update_mode)
    before = provision_session.switch_state()  # True, False, or None when it cannot be read
    sftp, host, username = delivery_seed()
    attempt = provision_session.request_machine_setup(
        update_mode=mode, sftp=sftp, sftp_host=host, sftp_username=username
    )
    outcome = attempt.outcome.value
    lines: list[str] = []
    if outcome == "refused":
        step = attempt.step.value if attempt.step is not None else "unknown"
        code = f", Windows permissions code {attempt.icacls_exit}" if attempt.icacls_exit is not None else ""
        lines.append(
            f"The machine setup stopped at step '{step}'{code}; see the log. Nothing after that step was changed."
        )
    elif outcome != "provisioned":
        lines.append(MACHINE_SETUP_COPY.get(outcome, MACHINE_SETUP_COPY["failed"]))
    handover = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)
    if handover.refused is not None:
        return MachineSetupResult(
            1, (*lines, MACHINE_SETUP_REFUSED_FOLDER.format(reason=handover.refused.value)), False
        )
    if not handover.handed_over:
        return MachineSetupResult(1, (*lines, NOT_HANDED_OVER), False)
    # The switch is on. Whether THIS run did anything is the child's answer, never the switch's: on a computer that was
    # already set up (a re-run to change the update mode) a declined prompt still leaves the switch on.
    if outcome == "provisioned" or (outcome == "unconfirmed" and before is False):
        # (unconfirmed + provably off before and on now: the mode is written before the switch)
        return MachineSetupResult(0, (*lines, MACHINE_SETUP_DONE, f"Automatic updates: {mode}."), True)
    if outcome in MACHINE_SETUP_NOT_RUN:
        tail = "This computer already keeps one shared DistrictSync profile; the update setting was not changed."
    else:
        tail = (
            "This computer keeps one shared DistrictSync profile, but this run may not have finished; "
            "run DistrictSync.exe --diagnose to see the update setting."
        )
    return MachineSetupResult(1, (*lines, tail), True)
