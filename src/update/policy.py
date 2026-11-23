"""The update mode's vocabulary (plan 0055 U1/M2; owner decision O1, 2026-11-23).

Three modes:

* ``off``: no update connection at all.
* ``notify`` (the default): fetch the signed manifest only and say when a newer version exists; download nothing.
* ``on``: as ``notify``, and the SYSTEM updater installs signed updates on a machine-wide install.

This module holds the vocabulary and the ONE parser. The machine setting is written only by the elevated provisioning op
(``provisioning._commit_machine_switch``, the only registry writer) as ``UpdateMode`` under ``HKLM\\SOFTWARE\\DistrictSync``;
reading it, and the Group Policy key that overrides it, is slice U1.
"""

from __future__ import annotations

from typing import Final

OFF: Final = "off"
NOTIFY: Final = "notify"
ON: Final = "on"
UPDATE_MODES: Final = (OFF, NOTIFY, ON)
DEFAULT_UPDATE_MODE: Final = NOTIFY

#: The REG_SZ value name of the machine setting, beside ``MachineScope`` in ``HKLM\\SOFTWARE\\DistrictSync``.
MACHINE_SETTING_VALUE_NAME: Final = "UpdateMode"


def parse_update_mode(value: object) -> str:
    """The update mode ``value`` names, case- and space-insensitive. Raises ``ValueError`` for anything else."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    if text not in UPDATE_MODES:
        raise ValueError("not an update mode")
    return text
