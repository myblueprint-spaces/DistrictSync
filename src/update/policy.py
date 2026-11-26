"""The update mode's vocabulary (plan 0055 U1/M2; owner decision O1, 2026-11-23).

Three modes:

* ``off``: no update connection at all.
* ``notify`` (the default): fetch the signed manifest only and say when a newer version exists; download nothing.
* ``on``: as ``notify``, and the SYSTEM updater installs signed updates on a machine-wide install.

This module holds the vocabulary, the ONE parser and the ONE reader (:func:`effective_update_mode`, plan 0055 U1). Sources,
first match wins:

1. **Group Policy:** ``UpdateMode`` (REG_SZ) under ``HKLM\\SOFTWARE\\Policies\\myBlueprint\\DistrictSync``. Written by Group
   Policy or an administrator (templates in ``deploy/group-policy/``); the app NEVER writes it and nothing can override it.
   The policy's own state is ``UpdatePolicyState`` beside it: ``disabled`` (the policy set to Disabled) is ``off``, whatever
   ``UpdateMode`` says; Group Policy deletes ``UpdateMode`` on Disabled, so the state cannot live in that value.
2. **The machine setting:** ``UpdateMode`` under ``HKLM\\SOFTWARE\\DistrictSync``, written only by the elevated provisioning op
   (``provisioning._commit_machine_switch``, the only registry writer).
3. **The per-user switch:** ``AppConfig.update_enabled`` off means ``off`` (a per-user copy has no machine setting).
4. The default, ``notify``.

A value that cannot be read or parsed is ``off`` and logged, never ``on``: the strict answer is the safe one. Both keys are read
with ``KEY_READ | KEY_WOW64_64KEY`` (the plan 0049 lesson: a 32-bit-view read answers about a different key).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

OFF: Final = "off"
NOTIFY: Final = "notify"
ON: Final = "on"
UPDATE_MODES: Final = (OFF, NOTIFY, ON)
DEFAULT_UPDATE_MODE: Final = NOTIFY

#: The REG_SZ value name of the machine setting, beside ``MachineScope`` in ``HKLM\\SOFTWARE\\DistrictSync``.
MACHINE_SETTING_VALUE_NAME: Final = "UpdateMode"
#: The Group Policy key and value (pinned by the ADMX template in deploy/group-policy/).
POLICY_KEY_PATH: Final = r"SOFTWARE\Policies\myBlueprint\DistrictSync"
POLICY_VALUE_NAME: Final = "UpdateMode"
#: The policy's own Enabled/Disabled marker (the ADMX policy-level valueName); "disabled" means off.
POLICY_STATE_VALUE_NAME: Final = "UpdatePolicyState"
POLICY_STATE_ENABLED: Final = "enabled"
POLICY_STATE_DISABLED: Final = "disabled"

SOURCE_POLICY: Final = "policy"
SOURCE_MACHINE: Final = "machine"
SOURCE_SETTING: Final = "setting"
SOURCE_DEFAULT: Final = "default"


@dataclass(frozen=True)
class EffectiveMode:
    mode: str  # one of UPDATE_MODES
    source: str  # policy | machine | setting | default


def parse_update_mode(value: object) -> str:
    """The update mode ``value`` names, case- and space-insensitive. Raises ``ValueError`` for anything else."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    if text not in UPDATE_MODES:
        raise ValueError("not an update mode")
    return text


def _read_hklm_string(key_path: str, name: str) -> str | None:  # pragma: no cover - Windows-only registry read
    """``HKLM\\<key_path>\\<name>`` as text, or ``None`` when the key or value is absent. Other failures raise ``OSError``."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return str(value)


def _from_registry(key_path: str, name: str, source: str) -> EffectiveMode | None:
    """The mode a registry value sets, ``None`` when it is absent; unreadable or malformed is ``off``, logged."""
    try:
        raw = _read_hklm_string(key_path, name)
    except OSError:
        logger.warning("The %s update setting could not be read; updates are treated as off.", source)
        return EffectiveMode(OFF, source)
    if raw is None:
        return None
    try:
        return EffectiveMode(parse_update_mode(raw), source)
    except ValueError:
        logger.warning("The %s update setting is not off, notify or on; updates are treated as off.", source)
        return EffectiveMode(OFF, source)


def _policy_disabled() -> bool:
    """Whether the Group Policy is set to Disabled. Unreadable or not ``enabled``/``disabled`` counts as Disabled (strict)."""
    try:
        raw = _read_hklm_string(POLICY_KEY_PATH, POLICY_STATE_VALUE_NAME)
    except OSError:
        logger.warning("The policy update state could not be read; updates are treated as off.")
        return True
    if raw is None:
        return False
    state = raw.strip().lower()
    if state not in (POLICY_STATE_ENABLED, POLICY_STATE_DISABLED):
        logger.warning("The policy update state is not enabled or disabled; updates are treated as off.")
        return True
    return state == POLICY_STATE_DISABLED


def effective_update_mode(*, setting_enabled: bool) -> EffectiveMode:
    """The update mode in force and where it came from. ``setting_enabled`` is ``AppConfig.update_enabled``. Never raises."""
    from src.utils.paths import MACHINE_SCOPE_KEY_PATH

    if _policy_disabled():
        return EffectiveMode(OFF, SOURCE_POLICY)

    for key_path, name, source in (
        (POLICY_KEY_PATH, POLICY_VALUE_NAME, SOURCE_POLICY),
        (MACHINE_SCOPE_KEY_PATH, MACHINE_SETTING_VALUE_NAME, SOURCE_MACHINE),
    ):
        found = _from_registry(key_path, name, source)
        if found is not None:
            return found
    if not setting_enabled:
        return EffectiveMode(OFF, SOURCE_SETTING)
    return EffectiveMode(DEFAULT_UPDATE_MODE, SOURCE_DEFAULT)
