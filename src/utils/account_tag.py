"""A keyed tag that tells accounts apart without naming them (plan 0055 N1, DSYNC-100; owner O5).

Districts do not want account names in logs, log file names or run records. Run History still has to say "this account"
or "another account", and two writers on a shared profile still need separate log files, so a record and a log name
carry ``account_tag(account)`` instead of the name:

    HMAC-SHA256(key, case-folded account)[:12]   (hex)

**The key** is this computer's own ``MachineGuid`` (``HKLM\\SOFTWARE\\Microsoft\\Cryptography``), which Windows creates at
install and every local account can read. Chosen over a stored salt (review of DSYNC-100): every account on the computer
computes the same tag for the same account with nothing to create, copy or migrate, so a per-user profile moved into the
shared one by machine setup keeps "this account" on its earlier runs, and an install provisioned before this version has
tags at once. It protects a COPIED log or record (off the computer the key is unknown, so the tag cannot be reversed with
a list of names), not against a person on the server, who can read Task Scheduler anyway.

Off Windows (development and CI only) the key is a random file in the profile, created exclusively once.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Final

from src.utils.accounts import UNKNOWN_ACCOUNT, process_account

logger = logging.getLogger(__name__)

MACHINE_KEY_PATH: Final = r"SOFTWARE\Microsoft\Cryptography"
MACHINE_KEY_VALUE: Final = "MachineGuid"
TAG_LENGTH: Final = 12
#: Off Windows only: the key file in the profile root.
PROFILE_KEY_FILE: Final = "account-tag.key"
PROFILE_KEY_BYTES: Final = 32
#: The log-name fragment when no key can be read at all: never a name, never a reversible hash.
NO_KEY_FRAGMENT: Final = "nokey"

ROLE_SCHEDULED: Final = "scheduled"
ROLE_INTERACTIVE: Final = "interactive"


def _normalise(account: str) -> str:
    return account.strip().casefold()


def _read_machine_guid() -> bytes | None:  # pragma: no cover - Windows-only registry read
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, MACHINE_KEY_PATH, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            value, _kind = winreg.QueryValueEx(key, MACHINE_KEY_VALUE)
    except FileNotFoundError:
        return None
    text = str(value).strip()
    return text.encode("utf-8") if text else None


def _profile_key(profile: Path) -> bytes:
    """The off-Windows key: created EXCLUSIVELY once, so two first runs cannot tag with two keys; never replaced."""
    path = profile / PROFILE_KEY_FILE
    profile.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(secrets.token_bytes(PROFILE_KEY_BYTES))
    except FileExistsError:
        pass
    data = path.read_bytes()
    if not data:
        raise OSError("the account-tag key file is empty")
    return data


def tag_key() -> bytes | None:
    """The key, or ``None`` when it cannot be read. Never raises."""
    try:
        if sys.platform == "win32":
            return _read_machine_guid()
        from src.utils import paths

        return _profile_key(paths.user_data_dir())
    except Exception:  # noqa: BLE001 - a tag must never take a log sink or a run record down
        logger.warning("The account tag key could not be read; this run is recorded without an account tag.")
        return None


def account_tag(account: str, *, key: bytes | None = None) -> str | None:
    """The keyed tag of ``account``; ``None`` with no key or no usable account. Pure when ``key`` is given."""
    normalised = _normalise(account or "")
    if not normalised or normalised == UNKNOWN_ACCOUNT:
        return None
    secret = key if key is not None else tag_key()
    if not secret:
        return None
    return hmac.new(secret, normalised.encode("utf-8"), hashlib.sha256).hexdigest()[:TAG_LENGTH]


def current_tag() -> str | None:
    """The tag of the account this process runs as."""
    return account_tag(process_account())


def log_name_tag() -> str:
    """A file-name-safe fragment for this process's log file: the tag, or :data:`NO_KEY_FRAGMENT`. Never a name."""
    return current_tag() or NO_KEY_FRAGMENT


def process_role(argv: list[str] | None = None) -> str:
    """``scheduled`` for the app's own nightly (``--source scheduled`` or ``DSYNC_SOURCE=scheduled``), else
    ``interactive``. An argv pre-scan, because the log sink opens before argparse reads ``--source``."""
    args = sys.argv[1:] if argv is None else argv
    for i, arg in enumerate(args):
        if arg == "--source" and i + 1 < len(args) and args[i + 1].strip().lower() == ROLE_SCHEDULED:
            return ROLE_SCHEDULED
        if arg.lower() == f"--source={ROLE_SCHEDULED}":
            return ROLE_SCHEDULED
    if os.environ.get("DSYNC_SOURCE", "").strip().lower() == ROLE_SCHEDULED:
        return ROLE_SCHEDULED
    return ROLE_INTERACTIVE
