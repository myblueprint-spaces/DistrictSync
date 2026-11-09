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
from pathlib import Path
from unittest.mock import patch

import pytest

pytest_sftpserver = pytest.importorskip("pytest_sftpserver.plugin", reason="pytest-sftpserver not installed")

from src.sftp.uploader import INTERACTIVE_RETRY, SFTPUploader  # noqa: E402
from tests._output_tree import build_output_tree, zip_name  # noqa: E402

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


def _capturing_connect(uploader: SFTPUploader, puts: list[tuple[str, str, list[str]]]):
    """Wrap the REAL ``_connect`` so each ``put`` records (local name, remote path, zip members).

    The connection is genuine (pinned host key, real transport); only ``put`` is swapped so
    the test can read the zip the uploader nominated before the call returns.
    """
    original_connect = uploader._connect

    def _capture_put(local_path: str, remote_path: str) -> None:
        members: list[str] = []
        if local_path.endswith(".zip"):
            with zipfile.ZipFile(local_path) as zf:
                members = sorted(zf.namelist())
        puts.append((Path(local_path).name, remote_path, members))

    def patched_connect():
        client, sftp = original_connect()
        sftp.put = _capture_put  # type: ignore[method-assign]
        return client, sftp

    return patched_connect


@pytest.mark.integration
class TestSFTPRealUpload:
    def test_upload_creates_zip_on_server(self, sftpserver, tmp_path, pinned_local_server):
        """Full round-trip: the on-disk ACSV zip → real SFTP put → names reported delivered."""
        build_output_tree(tmp_path, ["Students", "Staff"])

        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
        ):
            uploaded = uploader.upload_csvs(tmp_path, manifest={"Students.csv", "Staff.csv"}, retry=INTERACTIVE_RETRY)

        assert sorted(uploaded) == ["Staff.csv", "Students.csv"]

    def test_upload_zip_contains_all_csvs(self, sftpserver, tmp_path, pinned_local_server):
        """The uploaded ZIP file must contain all rostering CSVs by name, at the zip root."""
        build_output_tree(tmp_path, ["Students", "Staff", "Family"])

        puts: list[tuple[str, str, list[str]]] = []
        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
            patch.object(uploader, "_connect", side_effect=_capturing_connect(uploader, puts)),
        ):
            uploader.upload_csvs(
                tmp_path,
                manifest={"Students.csv", "Staff.csv", "Family.csv"},
                retry=INTERACTIVE_RETRY,
            )

        assert len(puts) == 1
        assert puts[0][2] == ["Family.csv", "Staff.csv", "Students.csv"]

    def test_foreign_csv_never_reaches_the_server(self, sftpserver, tmp_path, pinned_local_server):
        """Over the REAL transport: an admin's stray CSV in the output folder is not delivered.

        Only the on-disk ACSV zip (carrying only the manifested roster) is put — a stray CSV
        at the top level or beside the rostering CSVs in ``csv/`` never egresses.
        """
        build_output_tree(tmp_path, ["Students"])
        (tmp_path / "old_roster.csv").write_text("User ID\nEX1\n", encoding="utf-8")
        (tmp_path / "csv" / "students_backup.csv").write_text("User ID\nEX2\n", encoding="utf-8")

        puts: list[tuple[str, str, list[str]]] = []
        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
            patch.object(uploader, "_connect", side_effect=_capturing_connect(uploader, puts)),
        ):
            uploaded = uploader.upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)

        assert [local for local, _, _ in puts] == [zip_name()]
        assert puts[0][2] == ["Students.csv"]
        assert uploaded == ["Students.csv"]

    def test_upload_empty_dir_raises_before_connecting(self, sftpserver, tmp_path):
        """If there is no ACSV zip to deliver, upload_csvs fails loud (no silent [] → false
        'delivered') and never connects (the raise fires before ``_connect``)."""
        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
            patch.object(uploader, "_connect") as connect,
            pytest.raises(RuntimeError, match=r"exactly one Advanced CSV zip \(found 0\)"),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        connect.assert_not_called()

    def test_upload_zip_name_includes_today(self, sftpserver, tmp_path, pinned_local_server):
        """The remote name is the on-disk ``acsv_<district>_<YYYY-MM-DD>.zip`` (built today)."""
        from datetime import date

        build_output_tree(tmp_path, ["Students"], for_date=date.today())

        puts: list[tuple[str, str, list[str]]] = []
        uploader = _make_uploader(sftpserver.port)
        with (
            patch("src.utils.validators.ALLOWED_SFTP_HOSTS", _PATCHED_HOSTS),
            patch.object(uploader, "_get_password", return_value="pass"),
            sftpserver.serve_content({"upload": {}}),
            patch.object(uploader, "_connect", side_effect=_capturing_connect(uploader, puts)),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)

        assert len(puts) == 1
        expected = f"/upload/acsv_sd40_{date.today().isoformat()}.zip"
        assert puts[0][1] == expected
        assert puts[0][1] == f"/upload/{zip_name(for_date=date.today())}"
