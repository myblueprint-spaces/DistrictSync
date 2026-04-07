"""SFTP uploader — uploads generated CSV files to SpacesEDU's SFTP server.

Credentials are stored securely in the OS credential store via the
``keyring`` library (Windows Credential Manager / macOS Keychain /
Linux Secret Service).  Only non-sensitive settings (host, port, paths)
are stored in the plain ``AppConfig`` JSON file.

Usage::

    from src.sftp.uploader import SFTPUploader
    uploader = SFTPUploader(host="sftp.example.com", port=22,
                            username="district_x", remote_path="/upload")
    uploader.store_password("secret")          # called once from setup wizard
    ok, msg = uploader.test_connection()
    if ok:
        uploaded = uploader.upload_csvs(Path("data/output"))
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "GDE2Acsv_SFTP"


class SFTPUploader:
    """Upload CSV files from an output directory to an SFTP server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        remote_path: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.remote_path = remote_path

    # ------------------------------------------------------------------
    # Credential management (OS keyring)
    # ------------------------------------------------------------------

    def store_password(self, password: str) -> None:
        """Store the SFTP password in the OS credential manager."""
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, self.username, password)
            logger.info(f"SFTP password stored for user '{self.username}'")
        except Exception as exc:
            logger.error(f"Failed to store SFTP password: {exc}")
            raise

    def _get_password(self) -> str | None:
        """Retrieve the SFTP password from the OS credential manager."""
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, self.username)
        except Exception as exc:
            logger.error(f"Failed to retrieve SFTP password: {exc}")
            return None

    # ------------------------------------------------------------------
    # Connection test (called from the setup wizard UI)
    # ------------------------------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        """Attempt an SFTP connection and list the remote path.

        Returns:
            (success, message) — success is True if the connection worked.
        """
        try:
            import paramiko
        except ImportError:
            return False, "paramiko is not installed. Run: pip install paramiko"

        password = self._get_password()
        if not password:
            return False, f"No password found for '{self.username}'. Please enter credentials in Setup."

        transport: paramiko.Transport | None = None
        try:
            transport = paramiko.Transport((self.host, self.port))
            transport.connect(username=self.username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.listdir(self.remote_path)
            sftp.close()
            return True, f"Connection to {self.host}:{self.port} successful."
        except Exception as exc:
            return False, f"Connection failed: {exc}"
        finally:
            if transport and transport.is_active():
                transport.close()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_csvs(self, output_dir: Path) -> list[str]:
        """Upload all CSV files in *output_dir* to the SFTP remote path.

        Args:
            output_dir: Local directory containing the generated CSV files.

        Returns:
            List of filenames that were uploaded.

        Raises:
            RuntimeError: If the connection could not be established.
        """
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("paramiko is not installed") from exc

        password = self._get_password()
        if not password:
            raise RuntimeError(
                f"No SFTP password found for '{self.username}'. "
                "Run the setup wizard to re-enter credentials."
            )

        csv_files = sorted(output_dir.glob("*.csv"))
        if not csv_files:
            logger.warning(f"No CSV files found in {output_dir}")
            return []

        transport = paramiko.Transport((self.host, self.port))
        try:
            transport.connect(username=self.username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)

            uploaded: list[str] = []
            for local_file in csv_files:
                remote_file = f"{self.remote_path.rstrip('/')}/{local_file.name}"
                logger.info(f"Uploading {local_file.name} → {remote_file}")
                sftp.put(str(local_file), remote_file)
                uploaded.append(local_file.name)
                logger.info(f"Uploaded {local_file.name} ({local_file.stat().st_size:,} bytes)")

            sftp.close()
            logger.info(f"SFTP upload complete: {len(uploaded)} file(s) uploaded")
            return uploaded
        finally:
            if transport.is_active():
                transport.close()
