"""A per-install keyed tag that tells accounts apart without naming them (plan 0055 N1, DSYNC-100; owner O5).

Districts do not want account names in logs, log file names or run records. Run History still has to say "this account"
or "another account", and two writers on a shared profile still need separate log files, so a record and a log name
carry ``account_tag(account)`` instead of the name:

    HMAC-SHA256(install salt, case-folded account)[:12]   (hex)

**The salt.** On a machine-wide (shared) profile: 32 random bytes in ``HKLM\\SOFTWARE\\DistrictSync\\AccountTagSalt``,
written only by the elevated provisioning commit (``provisioning._commit_machine_switch``, the only registry writer) and
readable by every account, because every account must compute its own tag. On a per-user profile: a file in that
profile, created on first use (the profile is the user's own). It protects a copied log or record, not against a person
on the server, who can read Task Scheduler anyway.

**No salt yet** (a shared profile provisioned before this version): :func:`account_tag` returns ``None``. The process
has no right to write HKLM, so it never creates one; a record then carries no tag and Run History cannot place it, until
the next provisioning op creates the salt. Only a LOG FILE NAME, which must still separate writers, falls back to an
unkeyed hash (:func:`log_name_tag`).
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

#: The REG_BINARY value beside ``MachineScope`` in ``HKLM\\SOFTWARE\\DistrictSync``.
SALT_VALUE_NAME: Final = "AccountTagSalt"
SALT_BYTES: Final = 32
TAG_LENGTH: Final = 12
#: The per-user salt file, in the profile root.
PROFILE_SALT_FILE: Final = "account-tag.salt"

ROLE_SCHEDULED: Final = "scheduled"
ROLE_INTERACTIVE: Final = "interactive"


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def _normalise(account: str) -> str:
    return account.strip().casefold()


def _read_machine_salt() -> bytes | None:  # pragma: no cover - Windows-only registry read
    """The HKLM salt, ``None`` when the key or value is absent or not 32 bytes. Other failures raise ``OSError``."""
    if sys.platform != "win32":
        return None
    import winreg

    from src.utils import paths

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, paths.MACHINE_SCOPE_KEY_ACCESS
        ) as key:
            value, _kind = winreg.QueryValueEx(key, SALT_VALUE_NAME)
    except FileNotFoundError:
        return None
    return bytes(value) if isinstance(value, bytes) and len(value) == SALT_BYTES else None


def _profile_salt(profile: Path) -> bytes:
    """The per-user salt, created on first use (owner-only where the OS supports it)."""
    path = profile / PROFILE_SALT_FILE
    try:
        data = path.read_bytes()
        if len(data) == SALT_BYTES:
            return data
    except FileNotFoundError:
        pass
    profile.mkdir(parents=True, exist_ok=True)
    data = new_salt()
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path.read_bytes()  # a racing writer may have won: everyone uses the file that stayed


def install_salt() -> bytes | None:
    """This install's salt, or ``None`` on a shared profile that has none yet. Never raises."""
    from src.utils import paths

    try:
        if paths.is_machine_scope():
            return _read_machine_salt()
        return _profile_salt(paths.user_data_dir())
    except Exception:  # noqa: BLE001 - a tag must never take a log sink or a run record down
        logger.warning("The account tag key could not be read; this run is recorded without an account tag.")
        return None


def account_tag(account: str, *, salt: bytes | None = None) -> str | None:
    """The keyed tag of ``account``, ``None`` with no salt or no usable account. Pure when ``salt`` is given."""
    normalised = _normalise(account or "")
    if not normalised or normalised == UNKNOWN_ACCOUNT:
        return None
    key = salt if salt is not None else install_salt()
    if not key:
        return None
    return hmac.new(key, normalised.encode("utf-8"), hashlib.sha256).hexdigest()[:TAG_LENGTH]


def current_tag() -> str | None:
    """The tag of the account this process runs as."""
    return account_tag(process_account())


def log_name_tag() -> str:
    """A file-name-safe tag for this process's log file. Never a name and never empty.

    The keyed tag when there is a salt; otherwise ``u`` plus an unkeyed hash, which still separates two writers (the
    reason the log name is per-writer) without printing the name.
    """
    tag = current_tag()
    if tag:
        return tag
    normalised = _normalise(process_account())
    return "u" + hashlib.sha256(f"districtsync-untagged:{normalised}".encode()).hexdigest()[: TAG_LENGTH - 1]


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
