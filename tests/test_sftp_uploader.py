"""Tests for src/sftp/uploader.py — SFTP upload with mocked paramiko/keyring."""

from unittest.mock import MagicMock, patch

import pytest

from src.sftp.uploader import KEYRING_SERVICE, SFTPUploader


class TestSFTPUploaderInit:
    def test_valid_host(self):
        uploader = SFTPUploader(
            host="sftp.ca.spacesedu.com",
            port=22,
            username="district_x",
            remote_path="/upload",
        )
        assert uploader.host == "sftp.ca.spacesedu.com"
        assert uploader.port == 22
        assert uploader.username == "district_x"

    def test_invalid_host_rejected(self):
        with pytest.raises(ValueError, match="not allowed"):
            SFTPUploader(
                host="evil.example.com",
                port=22,
                username="user",
                remote_path="/upload",
            )


class TestStorePassword:
    @patch("src.sftp.uploader.keyring", create=True)
    def test_store_calls_keyring(self, mock_keyring_module):
        # Patch the import inside the method
        with patch.dict("sys.modules", {"keyring": mock_keyring_module}):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            uploader.store_password("secret123")
            mock_keyring_module.set_password.assert_called_once_with(KEYRING_SERVICE, "user", "secret123")

    @patch.dict("sys.modules", {"keyring": MagicMock(set_password=MagicMock(side_effect=Exception("keyring error")))})
    def test_store_raises_on_keyring_error(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with pytest.raises(Exception, match="keyring error"):
            uploader.store_password("secret123")


class TestGetPassword:
    def test_get_password_returns_stored_value(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "my_secret"
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            result = uploader._get_password()
            assert result == "my_secret"

    def test_get_password_returns_none_on_error(self):
        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = Exception("no keyring backend")
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            result = uploader._get_password()
            assert result is None


class TestConnect:
    def test_connect_raises_without_password(self):
        mock_paramiko = MagicMock()
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with (
            patch.dict("sys.modules", {"paramiko": mock_paramiko}),
            patch.object(uploader, "_get_password", return_value=None),
            pytest.raises(RuntimeError, match="No SFTP password found"),
        ):
            uploader._connect()

    def test_connect_raises_without_paramiko(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with patch.object(uploader, "_get_password", return_value="secret"):
            # Simulate paramiko not installed
            import builtins

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "paramiko":
                    raise ImportError("No module named 'paramiko'")
                return real_import(name, *args, **kwargs)

            with (
                patch("builtins.__import__", side_effect=fake_import),
                pytest.raises(RuntimeError, match="paramiko is not installed"),
            ):
                uploader._connect()


class TestTestConnection:
    def test_successful_connection(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            ok, msg = uploader.test_connection()
            assert ok is True
            assert "successful" in msg.lower()
            mock_sftp.listdir.assert_called_once_with("/upload")
            mock_sftp.close.assert_called_once()
            mock_client.close.assert_called_once()

    def test_failed_connection(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with patch.object(uploader, "_connect", side_effect=RuntimeError("No password")):
            ok, msg = uploader.test_connection()
            assert ok is False
            assert "No password" in msg

    def test_connection_exception(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with patch.object(uploader, "_connect", side_effect=Exception("timeout")):
            ok, msg = uploader.test_connection()
            assert ok is False
            assert "timeout" in msg


class TestUploadCsvs:
    def test_upload_zips_all_csvs(self, tmp_path):
        """upload_csvs should zip all CSVs into a single dated file and upload it."""
        from datetime import date

        (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("id,name\n1,Harper\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path)

        # Returns the list of CSV filenames inside the ZIP
        assert len(uploaded) == 2
        assert "Staff.csv" in uploaded
        assert "Students.csv" in uploaded
        # Only one sftp.put call (the dated ZIP file)
        assert mock_sftp.put.call_count == 1
        remote_path = mock_sftp.put.call_args[0][1]
        expected_name = f"gde2acsv_{date.today().isoformat()}.zip"
        assert remote_path == f"/upload/{expected_name}"
        mock_client.close.assert_called_once()

    def test_upload_custom_zip_name(self, tmp_path):
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path, zip_name="custom.zip")

        assert len(uploaded) == 1
        remote_path = mock_sftp.put.call_args[0][1]
        assert remote_path == "/upload/custom.zip"

    def test_upload_no_csv_files(self, tmp_path):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        uploaded = uploader.upload_csvs(tmp_path)
        assert uploaded == []

    def test_upload_raises_on_sftp_error(self, tmp_path):
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        mock_sftp.put.side_effect = Exception("disk full")
        with (
            patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)),
            pytest.raises(Exception, match="disk full"),
        ):
            uploader.upload_csvs(tmp_path)
        mock_client.close.assert_called_once()

    def test_uploaded_zip_contains_all_csvs(self, tmp_path):
        """Verify the ZIP actually contains the expected CSV files."""
        import zipfile

        (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("id,role\n1,teacher\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/files")
        mock_sftp = MagicMock()
        mock_client = MagicMock()

        # Capture the local ZIP path passed to sftp.put
        captured_local = []
        def capture_put(local, remote):
            # Read the ZIP before the temp dir is cleaned up
            with zipfile.ZipFile(local) as zf:
                captured_local.extend(zf.namelist())
        mock_sftp.put.side_effect = capture_put

        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploader.upload_csvs(tmp_path)

        assert sorted(captured_local) == ["Staff.csv", "Students.csv"]
