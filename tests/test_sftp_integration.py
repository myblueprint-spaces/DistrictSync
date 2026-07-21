"""SFTP integration tests using a real in-process SFTP server.

Uses pytest-sftpserver to spin up a live paramiko-backed SFTP server, so
these tests exercise the actual SSH transport layer — not just mocked calls.

Skipped automatically if pytest-sftpserver is not installed:
    pip install pytest-sftpserver

The SFTP host allowlist is temporarily extended to include 127.0.0.1 for
these tests via patching — the production allowlist is not modified.

Since W1-A the host-key pinning is FAIL-CLOSED (an unpinned server is refused, not
accepted with a warning), so every test that really connects must pin the test
server's key first — see the ``pinned_local_server`` fixture. That makes this file a
genuine end-to-end proof that a *correctly pinned* server still delivers.
"""

import zipfile
from unittest.mock import patch

import pytest

pytest_sftpserver = pytest.importorskip("pytest_sftpserver.plugin", reason="pytest-sftpserver not installed")

from src.sftp.uploader import SFTPUploader  # noqa: E402

_PATCHED_HOSTS = frozenset({"127.0.0.1", "sftp.ca.spacesedu.com", "sftp.app.spacesedu.com", "sftp.myblueprint.ca"})


def _make_uploader(port: int) -> SFTPUploader:
    """Create an SFTPUploader pointed at the local test server."""
    with patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS):
        return SFTPUploader(host="127.0.0.1", port=port, username="user", remote_path="/upload")


@pytest.fixture
def pinned_local_server(sftpserver, tmp_path, monkeypatch):
    """Pin the local test server's host key, the way a district pins SpacesEDU.

    The key is derived from pytest-sftpserver's own bundled server key (deterministic —
    no probe connection), and written under the BRACKETED ``[host]:port`` name paramiko
    looks a non-22 port up by. Both known_hosts seams point at it, so the real
    ``PinnedHostKeyPolicy`` runs unmodified: these tests exercise the accepting branch
    of the fail-closed policy rather than patching the policy out.
    """
    from paramiko.rsakey import RSAKey
    from pytest_sftpserver.consts import SERVER_KEY_PRIVATE

    key = RSAKey.from_private_key_file(SERVER_KEY_PRIVATE)
    pins_dir = tmp_path / "pins"
    pins_dir.mkdir()
    known_hosts = pins_dir / "known_hosts"
    known_hosts.write_text(
        f"[127.0.0.1]:{sftpserver.port} {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.sftp.uploader.user_known_hosts_file", lambda: known_hosts)
    monkeypatch.setattr("src.sftp.uploader.bundle_known_hosts_file", lambda: known_hosts)
    return known_hosts


@pytest.mark.integration
class TestSFTPRealUpload:
    def test_upload_creates_zip_on_server(self, sftpserver, tmp_path, pinned_local_server):
        """Full round-trip: CSV → ZIP creation → SFTP put → file on server."""
        (tmp_path / "Students.csv").write_text("User ID\n1\n", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("User ID\n2\n", encoding="utf-8")

        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
        ):
            uploaded = uploader.upload_csvs(tmp_path)

        assert sorted(uploaded) == ["Staff.csv", "Students.csv"]

    def test_upload_zip_contains_all_csvs(self, sftpserver, tmp_path, pinned_local_server):
        """The uploaded ZIP file must contain all CSV files by name."""
        (tmp_path / "Students.csv").write_text("User ID\nS1\n", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("User ID\nT1\n", encoding="utf-8")
        (tmp_path / "Family.csv").write_text("Email\nparent@test.ca\n", encoding="utf-8")

        captured_zip_names: list[str] = []

        def _capture_put(local_path: str, remote_path: str) -> None:
            with zipfile.ZipFile(local_path) as zf:
                captured_zip_names.extend(zf.namelist())

        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
        ):
            # Intercept the sftp.put call to inspect the ZIP before it's cleaned up
            original_connect = uploader._connect

            def patched_connect():
                client, sftp = original_connect()
                sftp.put = _capture_put  # type: ignore[method-assign]
                return client, sftp

            with patch.object(uploader, "_connect", side_effect=patched_connect):
                uploader.upload_csvs(tmp_path, zip_name="test.zip")

        assert sorted(captured_zip_names) == ["Family.csv", "Staff.csv", "Students.csv"]

    def test_upload_empty_dir_raises_before_connecting(self, sftpserver, tmp_path):
        """If no CSV files exist, upload_csvs fails loud (no silent [] → false 'delivered')
        and never connects (the raise fires before ``_connect``)."""
        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
            pytest.raises(RuntimeError, match="No CSV files found to upload"),
        ):
            uploader.upload_csvs(tmp_path)

    def test_upload_zip_name_includes_today(self, sftpserver, tmp_path, pinned_local_server):
        """Default ZIP name must match districtsync_YYYY-MM-DD.zip."""
        from datetime import date

        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        remote_paths: list[str] = []

        def _capture(local: str, remote: str) -> None:
            remote_paths.append(remote)

        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
        ):
            original_connect = uploader._connect

            def patched_connect():
                client, sftp = original_connect()
                sftp.put = _capture  # type: ignore[method-assign]
                return client, sftp

            with patch.object(uploader, "_connect", side_effect=patched_connect):
                uploader.upload_csvs(tmp_path)

        assert len(remote_paths) == 1
        expected = f"/upload/districtsync_{date.today().isoformat()}.zip"
        assert remote_paths[0] == expected
