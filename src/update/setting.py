"""The update mode as a SETTING: what Settings may show and change, and the Home line (plan 0055 U1b, DSYNC-107).

Pure over the effective mode (``policy.effective_update_mode``) and the install kind, so it is unit-tested without a
window. Three cases:

* **Group Policy decides** (``source == policy``): shown, LOCKED. The app never writes the policy key.
* **A machine-wide install**: off / notify / on. The machine setting lives in HKLM, which only the elevated provisioning
  op writes, so a change is the machine setup ladder again behind one UAC prompt (``machine_setup.run``).
* **A copy installed for one account**: notify or off (``AppConfig.update_enabled``). ``on`` is not offered: a per-user
  copy installs nothing on its own (owner decision O1).

A build without the updater (the portable exe) shows nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.update import policy
from src.utils.install_kind import InstallKind

#: The ONE wording of each mode, for Settings and the first-run window.
MODE_LABELS: dict[str, str] = {
    policy.NOTIFY: "Tell me when a newer version is available (recommended)",
    policy.ON: "Install new signed versions automatically",
    policy.OFF: "Never check for new versions",
}
SECTION_TITLE = "New versions of DistrictSync"
POLICY_NOTE = "Set by your organisation's Group Policy. Your IT team can change it there."
MACHINE_NOTE = (
    "This applies to everyone on this computer. An administrator running DistrictSync can change it; your IT team can "
    "also set it with Group Policy or DistrictSync.exe --machine-setup --updates."
)
SHARED_NOTE = "Set for everyone on this computer by its administrator."
USER_NOTE = "This applies to this copy of DistrictSync."
APPLY_LABEL = "Apply"


@dataclass(frozen=True)
class UpdateSettingView:
    shown: bool
    mode: str
    choices: tuple[str, ...]
    locked: bool
    machine_wide: bool
    note: str


def update_setting_view(
    *, installed: bool, kind: InstallKind, effective: policy.EffectiveMode, shared_profile: bool
) -> UpdateSettingView:
    """What the Settings section shows. Pure and total.

    A copy that is not the machine-wide install shows the mode LOCKED whenever the machine decides it (the HKLM setting
    wins over ``update_enabled``) or the session uses the shared profile (its ``config.json`` is everyone's): a per-user
    "Saved." there would change nothing, or change it for everyone.
    """
    if not installed:
        return UpdateSettingView(False, effective.mode, (), True, False, "")
    machine = kind is InstallKind.MACHINE
    choices = (policy.NOTIFY, policy.ON, policy.OFF) if machine else (policy.NOTIFY, policy.OFF)
    if effective.source == policy.SOURCE_POLICY:
        return UpdateSettingView(True, effective.mode, (effective.mode,), True, machine, POLICY_NOTE)
    if not machine and (effective.source == policy.SOURCE_MACHINE or shared_profile):
        return UpdateSettingView(True, effective.mode, (effective.mode,), True, False, SHARED_NOTE)
    mode = effective.mode if effective.mode in choices else policy.NOTIFY
    return UpdateSettingView(True, mode, choices, False, machine, MACHINE_NOTE if machine else USER_NOTE)


def user_setting_for(mode: str) -> bool:
    """``AppConfig.update_enabled`` for a per-user choice: only ``off`` switches it off. Raises on anything else."""
    if mode not in (policy.NOTIFY, policy.OFF):
        raise ValueError("a copy installed for one account offers notify or off")
    return mode != policy.OFF


APPLIED_NOTE = "Changed for this computer."
APPLY_COPY: dict[str, str] = {
    "different_account": (
        "Only an administrator running DistrictSync can change this, so nothing was changed. Your IT team can also set "
        "it with Group Policy or DistrictSync.exe --machine-setup --updates."
    ),
    "declined": "The Windows permission prompt was declined, so nothing was changed.",
    "launch_failed": "Windows did not show the permission prompt, so nothing was changed.",
}
APPLY_FAILED = "The setting was not changed. Details are in DistrictSync's log."


def apply_message(outcome: str) -> tuple[bool, str]:
    """``(changed, sentence)`` for a ``request_update_mode`` outcome value. Only ``provisioned`` is a change."""
    if outcome == "provisioned":
        return (True, APPLIED_NOTE)
    return (False, APPLY_COPY.get(outcome, APPLY_FAILED))


def home_line(available_version: str | None) -> str | None:
    """Home's quiet line when a newer signed version exists (notify mode), ``None`` otherwise."""
    if not available_version:
        return None
    return f"DistrictSync {available_version} is available. This copy does not update itself."
