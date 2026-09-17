"""Where the SFTP delivery password lives — ONE store per install, chosen by SCOPE.

Plan 0049 S-1a-ii. Two implementations of one protocol, and :func:`select_store` picks
exactly one from :func:`src.utils.paths.is_machine_scope`:

* :class:`UserSecretStore` — the OS keyring, byte-identical to what every install in the
  field has always done. Keyed on the USERNAME alone (see the class docstring).
* :class:`MachineSecretStore` — a DPAPI **LocalMachine** blob at
  ``<machine profile>/sftp_secret.bin``, readable by every principal on THIS computer,
  which is the whole point: the nightly runs as a service account whose keyring the admin
  who typed the password can never reach.

**Never both, never a fallback chain.** A store that "tries the machine blob and falls
back to the keyring" would re-create the split-brain the plan exists to delete: the same
install would answer "delivery is configured" differently depending on who asked.
:func:`select_store` therefore returns one object with no second opinion.

**The confidentiality boundary on machine scope is the NTFS DACL**, applied by the
elevated ``provision`` op (S-1b) — LocalMachine DPAPI deliberately lets any local account
unseal the blob, so the directory permissions are what keep a standard user out.
:func:`src.utils.paths._assert_machine_dir_trusted` refuses a profile whose owner or
inheritance says nobody applied those permissions, and `is_machine_scope()` is False
unless it passed.

**Identity is bound in the DPAPI ENTROPY**, not compared after decrypt — see
:func:`identity_entropy`. A blob sealed for ``(hostA, userA)`` cannot be opened as
``(hostB, userB)`` at all, so :meth:`MachineSecretStore.has_secret` never materialises a
password to answer a boolean and a blob can never be re-paired to a different account by
editing ``config.json``. The payload carries the identity too and is re-checked after
decrypt; that check is belt-and-braces for a blob this module did not write.

**No password value reaches a log line, an exception message, a result dict or a repr.**
Every message in this module names the FAULT and the identity at most.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import keyring

from src.utils import paths
from src.utils.dpapi import CRYPTPROTECT_LOCAL_MACHINE, CRYPTPROTECT_UI_FORBIDDEN, dpapi_call

logger = logging.getLogger(__name__)

# The keyring service name every install in the field already stores under. Spelled ONCE,
# here; ``src.sftp.uploader`` re-exports it for the callers (and tests) that import it.
KEYRING_SERVICE = "DistrictSync_SFTP"

# The machine-scoped blob and the shape of its pre-promotion temporaries.
# nosec B105 - a FILE NAME, not a credential; bandit keys on the constant's name.
SECRET_FILENAME = "sftp_secret.bin"  # nosec B105
_TMP_SUFFIX = ".tmp"
_TMP_GLOB = f"{SECRET_FILENAME}.*{_TMP_SUFFIX}"

# A temporary older than this was abandoned by a crashed write and may be swept. Fresh
# ones belong to a write still in flight: deleting those would make two concurrent Saves
# fail each other for no benefit (a write completes in milliseconds). Mirrors the bounded
# age rule ``scheduler.elevation.sweep_orphans`` already uses for handshake blobs.
STALE_TMP_AGE_S = 300.0

# Namespacing + tamper-binding for the sealed blob. Deliberately DISTINCT from
# ``scheduler.elevation``'s ``DistrictSync/elevation/v1`` so neither blob can ever be
# opened by the other's code path, and versioned so a future payload change is a new
# namespace rather than a silent reinterpretation.
_ENTROPY_PREFIX = b"DistrictSync/sftp-secret/v1|"

# LOCAL_MACHINE: any account on this computer may unseal it (the point).
# UI_FORBIDDEN: never raise a modal DPAPI prompt — the nightly is non-interactive, and a
# prompt there turns a clean error into a hang. Hardcoded, not a parameter: this module
# has exactly one scope and a caller must not be able to choose another.
MACHINE_DPAPI_FLAGS = CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN  # 0x5


class SecretStoreError(RuntimeError):
    """A stored secret could not be produced or proven. Never carries the value.

    Deliberately NOT an ``OSError``: a caller distinguishing "the blob would not open"
    (an ``OSError`` from DPAPI) from "the blob opened but disagreed" needs the two to be
    separable, and the identity-binding tests assert exactly that separation.
    """


@runtime_checkable
class SecretStore(Protocol):
    """The delivery-password store. ``host`` is part of every identity (see below)."""

    def store_password(self, host: str, username: str, password: str) -> None:
        """Persist ``password`` for ``(host, username)``. Raises on failure — the admin is watching."""
        ...

    def get_password(self, host: str, username: str) -> str | None:
        """The stored password, or ``None`` when nothing is stored for this identity.

        Raises when a secret EXISTS but cannot be produced — a present-but-unreadable
        credential is a fault worth an error, never a silent "not configured".
        """
        ...

    def has_secret(self, host: str, username: str) -> bool:
        """Whether a usable secret exists. **TOTAL — never raises**, whatever is wrong."""
        ...


def identity_entropy(host: str, username: str) -> bytes:
    """The DPAPI optional-entropy bytes binding a blob to ONE ``(host, username)``.

    ``b"DistrictSync/sftp-secret/v1|" + host + b"|" + username`` over the **exact**
    stripped strings — **not** case-folded. That matches the keyring's exact-key
    semantics (``District_X`` and ``district_x`` are two different Credential Manager
    entries today), so the two stores cannot disagree about who a secret belongs to.

    Entropy is a namespacing / tamper-binding value, NOT a secret: it is derived from
    values already sitting in ``config.json`` in plaintext. Its job is to make a
    mismatched identity fail the unprotect itself.
    """
    resolved_host, resolved_username = _identity(host, username)
    return _ENTROPY_PREFIX + resolved_host.encode("utf-8") + b"|" + resolved_username.encode("utf-8")


def protect_machine_blob(data: bytes, entropy: bytes) -> bytes:
    """Seal ``data`` at LocalMachine scope. The ONE ``CryptProtectData`` call site here."""
    return dpapi_call("CryptProtectData", data, entropy, flags=MACHINE_DPAPI_FLAGS)


def unprotect_machine_blob(blob: bytes, entropy: bytes) -> bytes:
    """Open a LocalMachine blob. Raises ``OSError`` on a wrong entropy or a tampered blob."""
    return dpapi_call("CryptUnprotectData", blob, entropy, flags=MACHINE_DPAPI_FLAGS)


def _identity(host: str, username: str) -> tuple[str, str]:
    """Normalise an identity: strip surrounding whitespace, change nothing else."""
    return host.strip(), username.strip()


def _encode_payload(host: str, username: str, password: str) -> bytes:
    """The sealed body — the identity travels WITH the secret so a decrypt can re-check it."""
    return json.dumps({"host": host, "username": username, "password": password}).encode("utf-8")


def _decode_payload(raw: bytes, host: str, username: str) -> str:
    """Parse a decrypted body and return its password, or raise :class:`SecretStoreError`.

    Every failure message is fixed text: the decrypted bytes are the secret, so nothing
    derived from them (not even a parser's own complaint) may reach a caller.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SecretStoreError("The stored delivery secret is not in a readable format.") from exc
    if not isinstance(payload, dict):
        raise SecretStoreError("The stored delivery secret is not in a readable format.")
    if payload.get("host") != host or payload.get("username") != username:
        raise SecretStoreError("The stored delivery secret belongs to a different account.")
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise SecretStoreError("The stored delivery secret is empty.")
    return password


class UserSecretStore:
    """Today's OS keyring — Windows Credential Manager / Keychain / Secret Service.

    **``host`` is accepted and IGNORED.** The shipped key has always been
    ``(KEYRING_SERVICE, username)``, and 20 live installs have a password stored under it;
    adding the host to the key would silently invalidate every one of them on upgrade — a
    district's nightly would start reporting "SFTP is not configured" with nothing in the
    log to explain it. The asymmetry with :class:`MachineSecretStore` (whose blob is new,
    so its identity could be made complete from day one) is a decision, not an oversight.
    """

    def store_password(self, host: str, username: str, password: str) -> None:
        keyring.set_password(KEYRING_SERVICE, _identity(host, username)[1], password)

    def get_password(self, host: str, username: str) -> str | None:
        return keyring.get_password(KEYRING_SERVICE, _identity(host, username)[1])

    def has_secret(self, host: str, username: str) -> bool:
        try:
            return bool(self.get_password(host, username))
        except Exception as exc:  # noqa: BLE001 - total by contract; the reason is logged
            # A missing/broken backend must answer the predicate, not raise into a paint
            # path. WARNING (not ERROR): "is delivery configured?" has an honest answer.
            logger.warning(f"Could not read the stored delivery password: {type(exc).__name__}: {exc}")
            return False

    def __repr__(self) -> str:
        return "UserSecretStore()"


class MachineSecretStore:
    """A DPAPI LocalMachine blob in the shared profile — readable by every local account.

    Writes are **verify-before-promote**: the sealed bytes are written to a uniquely named
    temporary, read back from THAT temporary, unprotected and compared field-by-field, and
    only a proven-good blob is promoted onto the live name with :func:`os.replace`. The
    live blob is therefore never replaced by something unproven, and a failed write only
    ever removes a temporary that was never live — an AV file-lock cannot turn "delivery
    works" into "delivery is unconfigured" on an install whose service principal nobody
    can log in as to re-enter the password.
    """

    def secret_path(self) -> Path:
        """The live blob's path. Resolved at call time — never an import-time constant."""
        return paths.machine_data_dir() / SECRET_FILENAME

    def store_password(self, host: str, username: str, password: str) -> None:
        host, username = _identity(host, username)
        if not password:
            # Validated HERE, at the boundary, rather than discovered on the read-back:
            # ``_decode_payload`` treats an empty password as unusable, so an empty write
            # would seal a blob that can never be opened and report it as a verify
            # failure. (``UserSecretStore`` is deliberately NOT changed — the keyring has
            # always accepted this and 20 installs' behaviour is not this slice's to move.)
            raise SecretStoreError("Refusing to store an empty delivery password.")
        directory = paths.machine_data_dir()
        # The only MUTATING entry point, so the only place the sweep may live: a sealed
        # password stranded by a crashed write is reached by no other cleanup in the app,
        # and a read-only predicate must never mutate the disk to answer a question.
        self._sweep_stale_temporaries(directory)

        entropy = identity_entropy(host, username)
        sealed = protect_machine_blob(_encode_payload(host, username, password), entropy)
        # A unique name (the house pattern — see ``scheduler.elevation.new_result_path``)
        # so two concurrent writers cannot collide on one temporary.
        tmp = directory / f"{SECRET_FILENAME}.{secrets.token_hex(16)}{_TMP_SUFFIX}"
        try:
            tmp.write_bytes(sealed)
            # Read back the TEMPORARY, not the live name: proving the bytes that are about
            # to be promoted, rather than the ones already in service.
            if _decode_payload(unprotect_machine_blob(tmp.read_bytes(), entropy), host, username) != password:
                raise SecretStoreError("The delivery password did not survive its read-back check; nothing was saved.")
            os.replace(tmp, directory / SECRET_FILENAME)
        except BaseException:
            # ``tmp`` was never live, so removing it cannot destroy a working secret.
            # The unlink's own OSError is suppressed because the failure being re-raised
            # is the one worth reporting (a stranded tmp is swept on the next write).
            # ``BaseException`` deliberately: a Ctrl-C or a worker shutdown between the
            # write and the promote must not leave a sealed password on disk either —
            # and every path here re-raises, so nothing is swallowed.
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def get_password(self, host: str, username: str) -> str | None:
        host, username = _identity(host, username)
        try:
            blob = self.secret_path().read_bytes()
        except FileNotFoundError:
            return None
        return _decode_payload(unprotect_machine_blob(blob, identity_entropy(host, username)), host, username)

    def has_secret(self, host: str, username: str) -> bool:
        try:
            return self.get_password(host, username) is not None
        except Exception as exc:  # noqa: BLE001 - total by contract; the reason is logged
            # Every message this can carry is fixed text from this module or
            # ``utils.dpapi`` (call name + Windows error number) — never the value.
            logger.warning(f"Could not read the machine-scoped delivery secret: {type(exc).__name__}: {exc}")
            return False

    def _sweep_stale_temporaries(self, directory: Path) -> None:
        """Best-effort delete of abandoned ``sftp_secret.bin.*.tmp`` files. Never raises."""
        try:
            candidates = list(directory.glob(_TMP_GLOB))
        except OSError:
            return
        now = time.time()
        for candidate in candidates:
            try:
                if now - candidate.stat().st_mtime > STALE_TMP_AGE_S:
                    candidate.unlink()
                    logger.info("Removed an abandoned delivery-secret temporary file")
            except OSError:
                continue

    def __repr__(self) -> str:
        return "MachineSecretStore()"


def select_store() -> SecretStore:
    """The ONE store this install uses. Machine scope → the blob; otherwise the keyring.

    Under ``DISTRICTSYNC_DATA_DIR`` the answer is always the keyring, by construction of
    the pin: the override is never machine scope (``paths._pinned``).
    """
    if paths.is_machine_scope():
        return MachineSecretStore()
    return UserSecretStore()
