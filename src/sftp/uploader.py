"""SFTP uploader — uploads generated CSV files to SpacesEDU's SFTP server.

Credentials are stored securely in the OS credential store via the
``keyring`` library (Windows Credential Manager / macOS Keychain /
Linux Secret Service).  Only non-sensitive settings (host, port, paths)
are stored in the plain ``AppConfig`` JSON file.

Connections are restricted to the SpacesEDU SFTP host allowlist
(see ``src.utils.validators.ALLOWED_SFTP_HOSTS``), and the server's
*identity* is verified against pinned SSH host keys resolved from the
user app-data ``known_hosts`` override + the bundled ``config/known_hosts``
(see :class:`PinnedHostKeyPolicy` — the pinning is **fail-closed**: a
mismatching key AND a missing/unloadable pin both hard-fail, and the
system ``~/.ssh/known_hosts`` is deliberately never consulted).

Usage::

    from src.sftp.uploader import SFTPUploader
    uploader = SFTPUploader(host="sftp.ca.spacesedu.com", port=22,
                            username="district_x", remote_path="/upload")
    uploader.store_password("secret")          # called once from setup wizard
    ok, msg = uploader.test_connection()
    if ok:
        # Only the files THIS run produced ship — never the folder's *.csv glob.
        # ``manifest`` is required; build it from the entities the run wrote:
        #     from src.etl.loader import DataLoader
        #     manifest = DataLoader.output_filenames(outputs)   # e.g. {"Students.csv", ...}
        uploaded = uploader.upload_csvs(Path("data/output"), manifest=manifest)
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Collection
from datetime import date
from pathlib import Path
from typing import TypeVar

import keyring
import paramiko

from src.utils.paths import bundle_known_hosts_file, user_known_hosts_file
from src.utils.validators import validate_sftp_host

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

KEYRING_SERVICE = "DistrictSync_SFTP"

# Bounded retry for the transient-failure surface of connect+upload (delivery only —
# ``test_connection`` never retries: a Setup "Test" click must answer fast). 3 attempts
# total; the wait doubles after each failed attempt: 2s, then 4s.
SFTP_RETRY_ATTEMPTS = 3
SFTP_RETRY_BACKOFF_SECONDS = 2.0

# Network-ish failures worth a bounded retry. ``socket.error`` is an alias of OSError.
_TRANSIENT_EXCEPTIONS = (OSError, paramiko.SSHException)

# Canonical success-with-note returned by ``test_connection`` when auth succeeded but the
# account cannot LIST the remote folder (upload-only delivery accounts, e.g. SpacesEDU-style).
# Delivery uses ``sftp.put``, never ``listdir`` — so a listing denial is NOT a delivery
# failure. FIXED string (no host/port/path interpolation): it crosses to the UI copy layer by
# EQUALITY (``screens/setup._show_result`` compares ``msg == LISTING_DENIED_NOTE``).
LISTING_DENIED_NOTE = (
    "Connected and signed in. This account can't list the remote folder — "
    "that's normal for upload-only delivery accounts."
)


# ---------------------------------------------------------------------------
# Zip naming (lives here — the uploader is its only production consumer)
# ---------------------------------------------------------------------------


def district_slug(sis_type: str) -> str:
    """Short user-facing identifier for a district, derived from its sis_type.

    - sd40myedbc  -> sd40
    - sd74myedbc  -> sd74
    - myedbc      -> myedbc   (base config, keep as-is)
    - myBlueprint+ -> myBlueprint  (sanitized for filenames)
    """
    stem = sis_type
    if stem != "myedbc" and stem.endswith("myedbc"):
        stem = stem[: -len("myedbc")]
    # Sanitize for filesystem + zip filename use
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "district"


def build_zip_name(sis_type: str | None = None, for_date: date | None = None) -> str:
    """Build the canonical output zip filename.

    Pattern: ``districtsync_<district>_<YYYY-MM-DD>.zip`` when sis_type is known,
    falling back to ``districtsync_<YYYY-MM-DD>.zip`` for legacy callers that
    don't pass a district (preserves backwards compatibility with existing
    SFTP uploads that use only the date).
    """
    when = (for_date or date.today()).isoformat()
    if sis_type:
        return f"districtsync_{district_slug(sis_type)}_{when}.zip"
    return f"districtsync_{when}.zip"


# ---------------------------------------------------------------------------
# SSH host-key pinning
# ---------------------------------------------------------------------------


class HostKeyVerificationError(RuntimeError):
    """The server's SSH host key does not match a pinned known_hosts entry.

    The MITM case — delivery must hard-fail and must NEVER be retried.
    Deliberately a ``RuntimeError`` (not a ``paramiko.SSHException`` subclass):
    the retry loop treats ``SSHException`` as transient, and a changed server
    identity is the one failure that must not be re-attempted.
    """


def _host_key_mismatch_message(hostname: str) -> str:
    """Canonical, PII-free hard-reject message (host name only — never paths/credentials).

    Shared by :class:`PinnedHostKeyPolicy` (pinned-file mismatch) and ``_connect``'s
    ``BadHostKeyException`` wrapper (system known_hosts mismatch) so both MITM paths
    surface the identical, pinned copy.
    """
    return (
        f"SFTP host key verification failed for {hostname}: the server's identity has "
        "changed and no longer matches the pinned key in known_hosts. This can indicate "
        "a man-in-the-middle attack, so delivery was aborted. If the server's key was "
        "legitimately rotated, update the known_hosts file (it documents the ssh-keyscan "
        "command) and run again."
    )


def _host_key_unpinned_message(hostname: str) -> str:
    """Canonical, PII-free reject for a host we hold NO pinned key for (fail-closed).

    Deliberately DISTINCT from :func:`_host_key_mismatch_message`: a missing pin is a
    broken/incomplete install (the bundled ``config/known_hosts`` asset absent or
    unreadable), not evidence that the server's identity changed — so the copy must not
    cry man-in-the-middle, and the remedy is to restore the pins, not to rotate a key.
    Host name only — never a resolved path, credential, or key blob.
    """
    return (
        f"SFTP host key verification failed for {hostname}: no pinned key is available "
        "for this server, so its identity could not be verified and delivery was "
        "aborted. The pinned known_hosts file is missing or unreadable — reinstall "
        "DistrictSync, or place a known_hosts file pinning this server in the "
        "DistrictSync app-data folder (config/known_hosts documents the ssh-keyscan "
        "command)."
    )


def _host_key_port_unpinned_message(hostname: str, bare_host: str) -> str:
    """Canonical, PII-free reject for a non-22 port whose bracketed name isn't pinned.

    paramiko looks a non-standard port up as ``[host]:port``, which a plain-hostname
    entry does NOT cover. Saying so exactly is the difference between an owner adding
    one known_hosts line and an owner believing pinning is broken.
    """
    return (
        f"SFTP host key verification failed for {hostname}: a pinned key exists for "
        f"{bare_host}, but this connection uses a non-standard port, which the "
        "plain-hostname pin does NOT cover — so the server's identity could not be "
        f"verified and delivery was aborted. Add a bracketed '{hostname}' entry to "
        "known_hosts (scan with ssh-keyscan -p PORT) to pin it."
    )


# Never retried, even where the type would otherwise read as transient:
#   - AuthenticationException: hammering a wrong password can lock the delivery account.
#   - BadHostKeyException / HostKeyVerificationError: an unverified server identity
#     (changed key, or no pin to check it against) — retrying would just re-offer
#     credentials to a server we could not prove is SpacesEDU.
_NEVER_RETRY_EXCEPTIONS = (
    paramiko.AuthenticationException,
    paramiko.BadHostKeyException,
    HostKeyVerificationError,
)


def _is_transient_sftp_error(exc: BaseException) -> bool:
    """True when *exc* is a network-ish failure worth a bounded retry."""
    if isinstance(exc, _NEVER_RETRY_EXCEPTIONS):
        return False
    return isinstance(exc, _TRANSIENT_EXCEPTIONS)


def _known_hosts_paths() -> list[Path]:
    """Ordered known_hosts candidates — the user app-data override, then the bundled file."""
    return [user_known_hosts_file(), bundle_known_hosts_file()]


def load_pinned_host_keys() -> paramiko.HostKeys:
    """Merge the resolved known_hosts files into one ``HostKeys`` (user entries win).

    ``HostKeys.lookup`` returns the FIRST entry loaded for a given (host, key-type),
    so loading the user file before the bundled one gives user entries precedence —
    a known_hosts dropped into the app-data dir adds/rotates keys without a release
    (mirrors the mappings hotfix path). Files merge per-entry: a user file that pins
    only one host leaves the bundled pins for the other hosts in force.

    An unreadable/corrupt file is logged as ERROR and skipped rather than raised, so a
    bad user override still leaves the bundled pins in force (per-file resilience — NOT
    a bypass). It is no longer a security hole: with both files unusable the result is
    an EMPTY ``HostKeys``, and :class:`PinnedHostKeyPolicy` then refuses the connection
    (fail-closed) instead of accepting an unverified key.
    """
    pinned = paramiko.HostKeys()
    for path in _known_hosts_paths():
        if not path.is_file():
            continue
        try:
            pinned.load(str(path))
        except Exception as exc:
            logger.error(f"Ignoring unreadable pinned-host-key file {path}: {exc}")
    return pinned


class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """FAIL-CLOSED host-key decision backed by the pinned known_hosts entries.

    - pinned and MATCHES → accept (return normally). The ONLY accepting branch.
    - pinned but MISMATCH (the host has pinned keys, none matching the offered key,
      including a key-type we never pinned) → :func:`_host_key_mismatch_message`.
    - host has NO pinned key → :func:`_host_key_unpinned_message` (or the port-specific
      :func:`_host_key_port_unpinned_message` when only the bare host is pinned).

    **Why there is no warn-and-accept branch** (W1-A, 2026-07-21): it was a
    trust-on-first-use bypass of the entire feature — a missing PyInstaller
    ``--add-data config`` asset or one corrupt byte in ``config/known_hosts`` silently
    downgraded EVERY connection to "accept whatever key the server offers", which is
    exactly the state pinning exists to prevent, and it did so invisibly (a log WARNING
    no scheduled run ever reads). The original "don't break delivery before keys exist"
    rationale expired when ``config/known_hosts`` shipped populated for all three hosts
    in ``ALLOWED_SFTP_HOSTS`` (v3.7.0) — there is no longer a legitimate unpinned host.
    Host-NAME allowlisting stays at the boundary (``validate_sftp_host`` in
    :meth:`SFTPUploader.__init__`); this policy only ever answers "is this the KEY we
    pinned?", so the two concerns keep a single source of truth each.

    This policy is the SINGLE host-key decision point: ``_connect`` deliberately loads
    no host keys into the client (neither system nor pinned), because paramiko consults
    ``_system_host_keys`` FIRST and only calls a missing-host-key policy for hosts absent
    from it — so a user-writable ``~/.ssh/known_hosts`` entry would otherwise override
    the bundled pin. ``_connect`` still maps paramiko's own ``BadHostKeyException`` to
    :class:`HostKeyVerificationError` as defense in depth.
    """

    def __init__(self, pinned: paramiko.HostKeys) -> None:
        self._pinned = pinned

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        if self._pinned.lookup(hostname) is None:
            # Non-22 ports look up as "[host]:port" — a plain-host pin does NOT
            # apply to them. Say so distinctly, or the owner believes they're pinned.
            bare_host = hostname[1:].split("]")[0] if hostname.startswith("[") else None
            if bare_host and self._pinned.lookup(bare_host) is not None:
                message = _host_key_port_unpinned_message(hostname, bare_host)
            else:
                message = _host_key_unpinned_message(hostname)
        elif self._pinned.check(hostname, key):
            return
        else:
            message = _host_key_mismatch_message(hostname)
        logger.error(message)
        raise HostKeyVerificationError(message)


class SFTPUploader:
    """Upload CSV files from an output directory to an SFTP server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        remote_path: str,
    ) -> None:
        self.host = validate_sftp_host(host)
        self.port = port
        self.username = username
        self.remote_path = remote_path

    # ------------------------------------------------------------------
    # Credential management (OS keyring)
    # ------------------------------------------------------------------

    def store_password(self, password: str) -> None:
        """Store the SFTP password in the OS credential manager."""
        try:
            keyring.set_password(KEYRING_SERVICE, self.username, password)
            logger.info("SFTP credentials stored successfully")
        except Exception as exc:
            logger.error(f"Failed to store SFTP password: {exc}")
            raise

    def _get_password(self) -> str | None:
        """Retrieve the SFTP password from the OS credential manager."""
        try:
            return keyring.get_password(KEYRING_SERVICE, self.username)
        except Exception as exc:
            logger.error(f"Failed to retrieve SFTP password: {exc}")
            return None

    def get_stored_password(self) -> str | None:
        """Return the stored SFTP password, or None if not found / unreadable.

        Public wrapper around :meth:`_get_password` for use in the setup wizard
        to verify the keyring round-trip without re-implementing the storage key
        logic in the UI layer.
        """
        return self._get_password()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connect(self, password_override: str | None = None) -> tuple:
        """Create an authenticated SSHClient + SFTPClient pair.

        The server's identity is verified against the pinned known_hosts entries via
        :class:`PinnedHostKeyPolicy` (the host NAME is already restricted to
        ``ALLOWED_SFTP_HOSTS`` via ``validate_sftp_host()`` in ``__init__``; pinning
        checks the host's *key*). Verification is FAIL-CLOSED: a mismatching key, a
        missing/unloadable pin, and paramiko's own ``BadHostKeyException`` all raise
        :class:`HostKeyVerificationError`, which is never retried.

        Args:
            password_override: A transient password to authenticate with instead of
                the stored keyring credential (used by ``test_connection`` so a typed
                password can be verified WITHOUT being written to the keyring). When
                falsy, the stored credential is used (the nightly-upload path). The
                override is threaded to ``client.connect()`` ONLY — never the keyring,
                never a log.

        Returns:
            (paramiko.SSHClient, paramiko.SFTPClient)

        Raises:
            RuntimeError: If paramiko is missing or credentials are unavailable.
            HostKeyVerificationError: If the server's host key does not match a pinned
                entry (possible MITM), or if no pinned key is available to check it
                against (fail loud, never proceed on an unverified identity).
        """
        password = password_override or self._get_password()
        if not password:
            raise RuntimeError("No SFTP password found. Run the setup wizard to enter credentials.")

        client = paramiko.SSHClient()
        try:
            # NO load_system_host_keys() / load_host_keys() — deliberate. paramiko's
            # connect() consults the client's loaded host keys FIRST and invokes the
            # missing-host-key policy ONLY for a host absent from them, so any entry in
            # the user-writable ~/.ssh/known_hosts for an allowlisted host would silently
            # take precedence over the bundled pin (a full pinning bypass). Leaving the
            # client's key stores empty makes PinnedHostKeyPolicy the single decision
            # point for every connection.
            client.set_missing_host_key_policy(PinnedHostKeyPolicy(load_pinned_host_keys()))
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=password,
                timeout=30,
            )
            sftp = client.open_sftp()
        except paramiko.BadHostKeyException as exc:
            # Defense in depth: paramiko raises this itself (before the policy runs) for
            # a host present in the client's own key stores. We deliberately load none,
            # so this should be unreachable — but if a future change (or a paramiko
            # default) ever populates them, the MITM case must still fail with the same
            # canonical message rather than a raw exception carrying both key blobs.
            client.close()
            raise HostKeyVerificationError(_host_key_mismatch_message(self.host)) from exc
        except Exception:
            # Leak fix: callers only ever see (client, sftp) on success, so their
            # ``finally: client.close()`` can't reach a client whose connect or
            # open_sftp raised — close it here before re-raising.
            client.close()
            raise
        return client, sftp

    def _with_retry(self, operation: Callable[[], _T], what: str) -> _T:
        """Run *operation* with a bounded retry on transient failures.

        At most ``SFTP_RETRY_ATTEMPTS`` attempts, waiting ``SFTP_RETRY_BACKOFF_SECONDS``
        doubled after each failed attempt (2s, 4s). Only network-ish errors are retried
        (see ``_is_transient_sftp_error``); authentication failures and host-key rejects
        raise immediately, and the final failure re-raises the original exception so the
        caller's error contract (pipeline exit code 3) is untouched.
        """
        for attempt in range(1, SFTP_RETRY_ATTEMPTS + 1):
            try:
                return operation()
            except Exception as exc:
                if attempt >= SFTP_RETRY_ATTEMPTS or not _is_transient_sftp_error(exc):
                    raise
                delay = SFTP_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    f"{what} to {self.host} failed on attempt {attempt}/{SFTP_RETRY_ATTEMPTS} "
                    f"({exc}); retrying in {delay:.0f}s"
                )
                time.sleep(delay)
        raise AssertionError("unreachable: the retry loop always returns or raises")

    # ------------------------------------------------------------------
    # Connection test (called from the setup wizard UI)
    # ------------------------------------------------------------------

    def test_connection(self, password_override: str | None = None) -> tuple[bool, str]:
        """Attempt an SFTP connection; AUTH is the test (delivery uses ``put``, not ``list``).

        The connect/auth phase decides success. The remote-folder ``listdir`` is only a
        best-effort probe wrapped SEPARATELY: an upload-only account that is signed in but
        DENIED listing (``PermissionError``) is a SUCCESS-with-note — delivery (``sftp.put``)
        never lists — while a MISSING/wrong remote path (``FileNotFoundError``, or any other
        listdir error) stays a FAILURE, because a bad path breaks ``put`` too.

        Args:
            password_override: A typed password to test transiently (threaded to
                ``client.connect()`` ONLY — never stored, never logged, never in the
                returned message). When falsy, the stored keyring credential is used.
                This keeps the Test side-effect-free: a failed/typo'd Test can never
                clobber a working saved credential.

        Returns:
            (success, message) — success is True if auth worked. When auth worked but the
            account can't list the remote folder, returns ``(True, LISTING_DENIED_NOTE)``.
        """
        client = None
        try:
            client, sftp = self._connect(password_override=password_override)
            # Probe the remote listing SEPARATELY from connect/auth. paramiko maps
            # SFTP_PERMISSION_DENIED → IOError(errno.EACCES) ⇒ PermissionError (benign for
            # upload-only accounts); SFTP_NO_SUCH_FILE → FileNotFoundError (a real delivery
            # problem — a bad path breaks put too), which falls through to the outer handler.
            try:
                sftp.listdir(self.remote_path)
            except PermissionError:
                return True, LISTING_DENIED_NOTE
            finally:
                sftp.close()
            return True, f"Connection to {self.host}:{self.port} successful."
        except RuntimeError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Connection failed: {exc}"
        finally:
            if client:
                client.close()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_csvs(
        self,
        output_dir: Path,
        zip_name: str | None = None,
        sis_type: str | None = None,
        *,
        manifest: Collection[str],
    ) -> list[str]:
        """Zip the *manifested* rostering CSVs in *output_dir* and upload via SFTP.

        **The manifest is the authoritative payload — not the folder listing.**
        Callers name exactly the CSV filenames this delivery vouches for (built from
        the entity→filename rule in ``DataLoader.output_filenames``), and only those
        files ship. The folder is merely *where* they live: a spreadsheet export, a
        ``students_backup.csv``, or any other ``*.csv`` an admin drops into the output
        folder is left on disk and never egresses to SpacesEDU. ``manifest`` is
        REQUIRED (keyword-only) precisely so a future caller cannot silently fall back
        to "whatever is in the folder" — the defect this closes.

        ``StudentAttendance.csv``, when manifested, is SpacesEDU's attendance feed
        and must arrive as a **standalone file outside the rostering zip** (their
        nightly check looks for it by name, and it must not pollute the advanced
        -CSV bundle). It is therefore excluded from the zip and uploaded with its
        own ``sftp.put`` to the same remote directory. Every other district has
        no such file today, so ``zip_files == csv_files`` and behaviour is
        byte-identical to the all-csvs-in-one-zip path.

        Args:
            output_dir: Local directory containing the generated CSV files.
            zip_name: Explicit name of the ZIP file. If not provided, the name
                is derived from ``sis_type`` and today's date via
                ``build_zip_name`` — e.g. ``districtsync_sd40_2026-04-10.zip``
                when ``sis_type='sd40myedbc'``, or
                ``districtsync_2026-04-10.zip`` when no ``sis_type`` is provided.
            sis_type: District SIS identifier used to derive the default
                ``zip_name``. Ignored when ``zip_name`` is provided explicitly.
            manifest: The exact CSV filenames authorized for this delivery
                (e.g. ``DataLoader.output_filenames(outputs)``). Files in
                *output_dir* outside this set are NOT uploaded.

        Returns:
            List of CSV filenames delivered — the zipped rostering CSVs plus any
            standalone ``StudentAttendance.csv``. Always non-empty on return (an empty
            manifest / missing files raise rather than returning ``[]``).

        Raises:
            RuntimeError: If *manifest* is empty, if none of the manifested files are
                present in *output_dir* (fail-loud — a silent ``[]`` let callers report
                a false "delivered"), if a manifested file is missing (a partial
                delivery reported as success would be dishonest), or if the connection /
                upload could not be established.
        """
        import tempfile
        import zipfile

        if zip_name is None:
            zip_name = build_zip_name(sis_type)

        requested = set(manifest)
        if not requested:
            # Nothing was vouched for — refuse rather than invent a payload.
            logger.error("SFTP delivery aborted: no output files were nominated for delivery")
            raise RuntimeError("No output files were nominated for delivery")

        # Only manifested files ship. Top-level glob only (an `archive_<ts>/` subfolder
        # is already invisible here); the manifest then narrows it to this run's roster.
        csv_files = sorted(p for p in output_dir.glob("*.csv") if p.is_file() and p.name in requested)
        if not csv_files:
            # Fail loud: a silent `[]` return let callers mark the delivery "ok" (a false
            # "delivered"). Raise so run_pipeline exits 3 / Convert shows BUILT_NOT_DELIVERED.
            # Only the directory NAME (never the full path) is in the message — no PII leak.
            logger.error(f"No CSV files found to upload in {output_dir.name}")
            raise RuntimeError(f"No CSV files found to upload in {output_dir.name}")

        missing = sorted(requested - {p.name for p in csv_files})
        if missing:
            # A vouched-for file vanished between the atomic commit and delivery —
            # shipping the rest as "delivered" would be a partial roster reported as
            # whole. Entity CSV names only (no paths, no PII).
            logger.error(f"SFTP delivery aborted: manifested file(s) missing from {output_dir.name}: {missing}")
            raise RuntimeError(f"Cannot deliver — file(s) this run produced are missing from disk: {missing}")

        # SpacesEDU's attendance feed ships standalone, outside the rostering zip.
        attendance_files = [f for f in csv_files if f.name == "StudentAttendance.csv"]
        zip_files = [f for f in csv_files if f.name != "StudentAttendance.csv"]

        with tempfile.TemporaryDirectory() as tmpdir:
            # Build the rostering zip ONCE, outside the retry (local + deterministic) —
            # and only when there are rostering CSVs, so the attendance-only edge never
            # delivers an empty archive (the attendance file is still sent below).
            zip_path: Path | None = None
            if zip_files:
                zip_path = Path(tmpdir) / zip_name
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for csv_file in zip_files:
                        zf.write(csv_file, csv_file.name)
                logger.info(
                    f"Created ZIP: {zip_name} with {len(zip_files)} file(s) ({zip_path.stat().st_size:,} bytes)"
                )

            def _deliver() -> list[str]:
                """One connect+put attempt — the unit the bounded retry re-runs.

                Each attempt reconnects fresh (a dead transport can't be reused), and
                the date-stamped zip name makes a re-put idempotent: a retry overwrites
                the same remote name rather than duplicating the delivery.
                """
                delivered: list[str] = []
                client, sftp = self._connect()
                try:
                    if zip_path is not None:
                        zip_size = zip_path.stat().st_size
                        remote_file = f"{self.remote_path.rstrip('/')}/{zip_name}"
                        logger.info(f"Uploading {zip_name} -> {remote_file}")
                        sftp.put(str(zip_path), remote_file)
                        logger.info(f"Uploaded {zip_name} ({zip_size:,} bytes)")
                        delivered.extend(f.name for f in zip_files)

                    # Deliver each StudentAttendance.csv standalone (same logging
                    # style + failure semantics as the zip put, so a failed put
                    # propagates and preserves the pipeline's exit-code-3 contract).
                    for att_file in attendance_files:
                        att_size = att_file.stat().st_size
                        remote_att = f"{self.remote_path.rstrip('/')}/{att_file.name}"
                        logger.info(f"Uploading {att_file.name} -> {remote_att} ({att_size:,} bytes)")
                        sftp.put(str(att_file), remote_att)
                        logger.info(f"Uploaded {att_file.name} ({att_size:,} bytes)")
                        delivered.append(att_file.name)

                    sftp.close()
                    return delivered
                finally:
                    client.close()

            try:
                return self._with_retry(_deliver, "SFTP upload")
            except Exception as exc:
                logger.error(f"Failed to upload to {self.host}: {exc}")
                raise
