"""SFTP uploader — uploads generated CSV files to SpacesEDU's SFTP server.

Credentials are stored securely in the OS credential store via the
``keyring`` library (Windows Credential Manager / macOS Keychain /
Linux Secret Service).  Only non-sensitive settings (host, port, paths)
are stored in the plain ``AppConfig`` JSON file.

Connections are restricted to the SpacesEDU SFTP host allowlist
(see ``src.utils.validators.ALLOWED_SFTP_HOSTS``).

Usage::

    from src.sftp.uploader import SFTPUploader
    uploader = SFTPUploader(host="sftp.ca.spacesedu.com", port=22,
                            username="district_x", remote_path="/upload")
    uploader.store_password("secret")          # called once from setup wizard
    ok, msg = uploader.test_connection()
    if ok:
        uploaded = uploader.upload_csvs(Path("data/output"))
"""

from __future__ import annotations

import logging
from pathlib import Path

import keyring
import paramiko

from src.utils.validators import validate_sftp_host

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "DistrictSync_SFTP"

# Canonical success-with-note returned by ``test_connection`` when auth succeeded but the
# account cannot LIST the remote folder (upload-only delivery accounts, e.g. SpacesEDU-style).
# Delivery uses ``sftp.put``, never ``listdir`` — so a listing denial is NOT a delivery
# failure. FIXED string (no host/port/path interpolation): it crosses to the UI copy layer by
# EQUALITY (``screens/setup._show_result`` compares ``msg == LISTING_DENIED_NOTE``).
LISTING_DENIED_NOTE = (
    "Connected and signed in. This account can't list the remote folder — "
    "that's normal for upload-only delivery accounts."
)


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
        """
        password = password_override or self._get_password()
        if not password:
            raise RuntimeError("No SFTP password found. Run the setup wizard to enter credentials.")

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        # Host already restricted to ALLOWED_SFTP_HOSTS via validate_sftp_host() in __init__.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507
        client.connect(
            self.host,
            port=self.port,
            username=self.username,
            password=password,
            timeout=30,
        )
        sftp = client.open_sftp()
        return client, sftp

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
    ) -> list[str]:
        """Zip the rostering CSVs in *output_dir* and upload via SFTP.

        ``StudentAttendance.csv``, when present, is SpacesEDU's attendance feed
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

        Returns:
            List of CSV filenames delivered — the zipped rostering CSVs plus any
            standalone ``StudentAttendance.csv``. Always non-empty on return (an empty
            *output_dir* raises rather than returning ``[]``).

        Raises:
            RuntimeError: If *output_dir* contains no CSV files (fail-loud — a silent
                ``[]`` let callers report a false "delivered"), or if the connection /
                upload could not be established.
        """
        import tempfile
        import zipfile

        from src.utils.helpers import build_zip_name

        if zip_name is None:
            zip_name = build_zip_name(sis_type)

        csv_files = sorted(output_dir.glob("*.csv"))
        if not csv_files:
            # Fail loud: a silent `[]` return let callers mark the delivery "ok" (a false
            # "delivered"). Raise so run_pipeline exits 3 / Convert shows BUILT_NOT_DELIVERED.
            # Only the directory NAME (never the full path) is in the message — no PII leak.
            logger.error(f"No CSV files found to upload in {output_dir.name}")
            raise RuntimeError(f"No CSV files found to upload in {output_dir.name}")

        # SpacesEDU's attendance feed ships standalone, outside the rostering zip.
        attendance_files = [f for f in csv_files if f.name == "StudentAttendance.csv"]
        zip_files = [f for f in csv_files if f.name != "StudentAttendance.csv"]

        delivered: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            client, sftp = self._connect()
            try:
                # Build + upload the rostering zip only when there are rostering
                # CSVs — skip it on the attendance-only edge so we never deliver
                # an empty archive (the attendance file is still sent below).
                if zip_files:
                    zip_path = Path(tmpdir) / zip_name
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for csv_file in zip_files:
                            zf.write(csv_file, csv_file.name)
                    zip_size = zip_path.stat().st_size
                    logger.info(f"Created ZIP: {zip_name} with {len(zip_files)} file(s) ({zip_size:,} bytes)")

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
            except Exception as exc:
                logger.error(f"Failed to upload to {self.host}: {exc}")
                raise
            finally:
                client.close()
