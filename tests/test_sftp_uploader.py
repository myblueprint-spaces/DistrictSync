"""Tests for src/sftp/uploader.py — SFTP upload with mocked paramiko/keyring."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.sftp.uploader import (
    KEYRING_SERVICE,
    LISTING_DENIED_NOTE,
    SFTPUploader,
    build_zip_name,
    district_slug,
)


class TestDistrictSlug:
    def test_strips_myedbc_suffix(self):
        assert district_slug("sd40myedbc") == "sd40"
        assert district_slug("sd48myedbc") == "sd48"
        assert district_slug("sd51myedbc") == "sd51"
        assert district_slug("sd74myedbc") == "sd74"

    def test_base_myedbc_unchanged(self):
        assert district_slug("myedbc") == "myedbc"

    def test_sanitizes_special_characters(self):
        assert district_slug("myBlueprint+") == "myBlueprint"
        assert district_slug("sis with spaces") == "sis_with_spaces"
        assert district_slug("sis/with\\slashes") == "sis_with_slashes"

    def test_fallback_when_all_stripped(self):
        assert district_slug("+++") == "district"
        assert district_slug("   ") == "district"


class TestBuildZipName:
    def test_with_district(self):
        result = build_zip_name("sd40myedbc", for_date=date(2026, 4, 10))
        assert result == "districtsync_sd40_2026-04-10.zip"

    def test_with_base_district(self):
        result = build_zip_name("myedbc", for_date=date(2026, 4, 10))
        assert result == "districtsync_myedbc_2026-04-10.zip"

    def test_without_district_falls_back_to_date_only(self):
        """Legacy callers that don't know the district get the old format."""
        result = build_zip_name(for_date=date(2026, 4, 10))
        assert result == "districtsync_2026-04-10.zip"

    def test_none_district_matches_default(self):
        result = build_zip_name(sis_type=None, for_date=date(2026, 4, 10))
        assert result == "districtsync_2026-04-10.zip"

    def test_uses_today_when_no_date_provided(self):
        result = build_zip_name("sd40myedbc")
        today = date.today().isoformat()
        assert result == f"districtsync_sd40_{today}.zip"


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
    def test_store_calls_keyring(self):
        with patch("src.sftp.uploader.keyring.set_password") as mock_set:
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            uploader.store_password("secret123")
            mock_set.assert_called_once_with(KEYRING_SERVICE, "user", "secret123")

    def test_store_raises_on_keyring_error(self):
        with patch("src.sftp.uploader.keyring.set_password", side_effect=Exception("keyring error")):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            with pytest.raises(Exception, match="keyring error"):
                uploader.store_password("secret123")


class TestGetPassword:
    def test_get_password_returns_stored_value(self):
        with patch("src.sftp.uploader.keyring.get_password", return_value="my_secret"):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            assert uploader._get_password() == "my_secret"

    def test_get_password_returns_none_on_error(self):
        with patch("src.sftp.uploader.keyring.get_password", side_effect=Exception("no keyring backend")):
            uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
            assert uploader._get_password() is None


class TestConnect:
    def test_connect_raises_without_password(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with (
            patch.object(uploader, "_get_password", return_value=None),
            pytest.raises(RuntimeError, match="No SFTP password found"),
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

    def test_listing_denied_is_success_with_note(self):
        """Auth is the test: a signed-in but listing-denied account (upload-only, e.g.
        SpacesEDU) is SUCCESS-WITH-NOTE, because delivery uses ``put`` — never ``listdir``.

        paramiko maps SFTP_PERMISSION_DENIED → ``IOError(errno.EACCES)`` which Python
        raises as ``PermissionError``.
        """
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_sftp.listdir.side_effect = PermissionError("Permission denied")
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            ok, msg = uploader.test_connection()
        assert ok is True
        assert msg == LISTING_DENIED_NOTE
        # The connection is still torn down cleanly on the listing-denied path.
        mock_sftp.close.assert_called_once()
        mock_client.close.assert_called_once()

    def test_missing_remote_path_is_failure(self):
        """A missing/wrong remote path (``FileNotFoundError``) stays a FAILURE — a bad
        remote path breaks ``put`` too, so it is a real delivery problem (distinct from
        the benign listing-permission denial above).
        """
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_sftp.listdir.side_effect = FileNotFoundError("No such file")
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            ok, msg = uploader.test_connection()
        assert ok is False
        assert "No such file" in msg
        assert msg != LISTING_DENIED_NOTE
        mock_client.close.assert_called_once()

    def test_other_listdir_error_is_failure(self):
        """Any non-permission listdir error remains a failure (not silently a success)."""
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_sftp.listdir.side_effect = OSError("transport closed")
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            ok, msg = uploader.test_connection()
        assert ok is False
        assert "transport closed" in msg


class TestPasswordOverride:
    """Slice 7 (D6): a typed password threads transiently to ``client.connect()`` ONLY.

    It is never written to the keyring, never logged, and never appears in the
    returned message — so a failed/typo'd Test can't clobber a working credential.
    """

    def test_override_threaded_to_connect_never_keyring(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch("src.sftp.uploader.keyring.get_password") as mock_get,
        ):
            mock_client = mock_cls.return_value
            ok, msg = uploader.test_connection(password_override="typed-secret")
            assert ok is True
            # The override rode straight to client.connect(...), not the keyring.
            assert mock_client.connect.call_args.kwargs["password"] == "typed-secret"
            mock_get.assert_not_called()
            # The credential never leaks into the returned message.
            assert "typed-secret" not in msg

    def test_no_override_falls_back_to_stored_keyring_password(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with (
            patch("src.sftp.uploader.paramiko.SSHClient") as mock_cls,
            patch("src.sftp.uploader.keyring.get_password", return_value="stored-pw") as mock_get,
        ):
            mock_client = mock_cls.return_value
            ok, _msg = uploader.test_connection()
            assert ok is True
            # No override → the stored keyring credential is used (the nightly path).
            assert mock_client.connect.call_args.kwargs["password"] == "stored-pw"
            mock_get.assert_called_once()

    def test_test_path_leaves_stored_credential_intact(self):
        """The in-memory keyring backend (suite-wide) is UNTOUCHED by the test path."""
        import keyring as kr

        kr.set_password(KEYRING_SERVICE, "user", "original-stored")
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with patch("src.sftp.uploader.paramiko.SSHClient"):
            uploader.test_connection(password_override="typo-typed")
        # A typo'd Test never overwrites the working stored credential.
        assert kr.get_password(KEYRING_SERVICE, "user") == "original-stored"

    def test_test_connection_never_calls_store_password(self):
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with (
            patch("src.sftp.uploader.paramiko.SSHClient"),
            patch.object(uploader, "store_password") as mock_store,
        ):
            uploader.test_connection(password_override="typed")
            mock_store.assert_not_called()


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
            uploaded = uploader.upload_csvs(tmp_path, manifest={"Students.csv", "Staff.csv"})

        # Returns the list of CSV filenames inside the ZIP
        assert len(uploaded) == 2
        assert "Staff.csv" in uploaded
        assert "Students.csv" in uploaded
        # Only one sftp.put call (the dated ZIP file)
        assert mock_sftp.put.call_count == 1
        remote_path = mock_sftp.put.call_args[0][1]
        expected_name = f"districtsync_{date.today().isoformat()}.zip"
        assert remote_path == f"/upload/{expected_name}"
        mock_client.close.assert_called_once()

    def test_upload_custom_zip_name(self, tmp_path):
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path, zip_name="custom.zip", manifest={"Students.csv"})

        assert len(uploaded) == 1
        remote_path = mock_sftp.put.call_args[0][1]
        assert remote_path == "/upload/custom.zip"

    def test_upload_no_csv_files_raises(self, tmp_path):
        """Fail-loud on an empty output dir: a silent ``[]`` return let callers report a
        false 'delivered'. The raise makes the pipeline exit 3 / Convert BUILT_NOT_DELIVERED.
        """
        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with pytest.raises(RuntimeError, match="No CSV files found to upload"):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"})

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
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"})
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
            uploader.upload_csvs(tmp_path, manifest={"Students.csv", "Staff.csv"})

        assert sorted(captured_local) == ["Staff.csv", "Students.csv"]


class TestDeliveryManifest:
    """The payload is the caller's MANIFEST, never the output folder's ``*.csv`` glob.

    An admin who drops a spreadsheet export / backup CSV into the output folder must not
    have it uploaded to SpacesEDU — the folder is *where* the roster lives, not *what*
    the run vouched for.
    """

    @staticmethod
    def _capture(uploader, tmp_path, manifest):
        """Run an upload with a mocked transport; return (zip member names, remote names)."""
        import zipfile

        zipped: list[str] = []
        remote_names: list[str] = []
        mock_sftp = MagicMock()

        def _capture_put(local, remote):
            remote_names.append(str(remote).rsplit("/", 1)[-1])
            if str(local).endswith(".zip"):
                with zipfile.ZipFile(local) as zf:
                    zipped.extend(zf.namelist())

        mock_sftp.put.side_effect = _capture_put
        with patch.object(uploader, "_connect", return_value=(MagicMock(), mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path, manifest=manifest)
        return uploaded, zipped, remote_names

    def test_foreign_csv_in_the_folder_is_not_uploaded(self, tmp_path):
        (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
        (tmp_path / "old_roster.csv").write_text("id,name\n9,Ex Student\n", encoding="utf-8")
        (tmp_path / "students_backup.csv").write_text("id,name\n8,Backup\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        uploaded, zipped, _ = self._capture(uploader, tmp_path, {"Students.csv"})

        assert zipped == ["Students.csv"]
        assert uploaded == ["Students.csv"]
        # Not delivered — and not touched either (the uploader never deletes).
        assert (tmp_path / "old_roster.csv").exists()
        assert (tmp_path / "students_backup.csv").exists()

    def test_multi_run_folder_delivers_every_manifested_file(self, tmp_path):
        """Back-compat: a folder holding a full district's CSVs still ships all of them."""
        entities = ["Students", "Staff", "Family", "Classes", "Enrollments"]
        for entity in entities:
            (tmp_path / f"{entity}.csv").write_text("col\nv\n", encoding="utf-8")
        (tmp_path / "notes.csv").write_text("junk\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        uploaded, zipped, _ = self._capture(uploader, tmp_path, {f"{e}.csv" for e in entities})

        assert sorted(zipped) == sorted(f"{e}.csv" for e in entities)
        assert sorted(uploaded) == sorted(f"{e}.csv" for e in entities)
        assert "notes.csv" not in zipped

    def test_attendance_split_holds_inside_the_manifest(self, tmp_path):
        """The manifest narrows WHAT ships; the standalone-attendance split is unchanged —
        and an UNmanifested StudentAttendance.csv is not put standalone either."""
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")
        (tmp_path / "StudentAttendance.csv").write_text("School Number\n100\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")

        # Manifested → zip (rostering) + a standalone attendance put.
        uploaded, zipped, remote = self._capture(uploader, tmp_path, {"Students.csv", "StudentAttendance.csv"})
        assert zipped == ["Students.csv"]
        assert "StudentAttendance.csv" in remote
        assert sorted(uploaded) == ["StudentAttendance.csv", "Students.csv"]

        # Not manifested → the attendance file stays home; only the rostering zip goes.
        uploaded, zipped, remote = self._capture(uploader, tmp_path, {"Students.csv"})
        assert zipped == ["Students.csv"]
        assert "StudentAttendance.csv" not in remote
        assert uploaded == ["Students.csv"]

    def test_manifested_file_missing_from_disk_fails_loud(self, tmp_path):
        """A vouched-for file that vanished must abort — a partial roster reported as
        'delivered' is the dishonest outcome the exit-3 contract exists to prevent."""
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        with (
            patch.object(uploader, "_connect", return_value=(MagicMock(), mock_sftp)),
            pytest.raises(RuntimeError, match="Enrollments.csv"),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv", "Enrollments.csv"})
        mock_sftp.put.assert_not_called()  # nothing half-shipped

    def test_empty_manifest_refuses_to_invent_a_payload(self, tmp_path):
        (tmp_path / "Students.csv").write_text("id\n1\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        with pytest.raises(RuntimeError, match="nominated for delivery"):
            uploader.upload_csvs(tmp_path, manifest=set())

    def test_manifest_is_required(self):
        """Keyword-only and REQUIRED: no caller can silently fall back to globbing."""
        import inspect

        param = inspect.signature(SFTPUploader.upload_csvs).parameters["manifest"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


class TestUploadStudentAttendance:
    """StudentAttendance.csv ships standalone, outside the rostering zip."""

    def test_attendance_delivered_standalone_and_excluded_from_zip(self, tmp_path):
        """Rostering CSVs zip together; StudentAttendance.csv is put separately."""
        import zipfile

        (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
        (tmp_path / "Enrollments.csv").write_text("class,user\nC1,1\n", encoding="utf-8")
        (tmp_path / "StudentAttendance.csv").write_text(
            "School Number,Absence Date,Absence Category,Student Number\n,01-Sep-2025,A,1\n",
            encoding="utf-8",
        )

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()

        # Capture the namelist of any ZIP put, and all remote paths.
        zipped_names: list[str] = []

        def capture_put(local, remote):
            if str(local).endswith(".zip"):
                with zipfile.ZipFile(local) as zf:
                    zipped_names.extend(zf.namelist())

        mock_sftp.put.side_effect = capture_put

        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(
                tmp_path, manifest={"Students.csv", "Enrollments.csv", "StudentAttendance.csv"}
            )

        # The zip contains the rostering CSVs but NOT the attendance file.
        assert sorted(zipped_names) == ["Enrollments.csv", "Students.csv"]
        assert "StudentAttendance.csv" not in zipped_names

        # Two puts: the rostering zip + the standalone attendance file.
        assert mock_sftp.put.call_count == 2
        remote_paths = [call.args[1] for call in mock_sftp.put.call_args_list]
        assert any(p.endswith("/StudentAttendance.csv") for p in remote_paths)
        assert "/upload/StudentAttendance.csv" in remote_paths

        # Return value reports both the zipped CSVs and the standalone file.
        assert "StudentAttendance.csv" in uploaded
        assert "Students.csv" in uploaded
        assert "Enrollments.csv" in uploaded
        mock_client.close.assert_called_once()

    def test_no_standalone_put_when_attendance_absent(self, tmp_path):
        """Districts without an attendance file: zip-only, behaviour unchanged."""
        import zipfile

        (tmp_path / "Students.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
        (tmp_path / "Staff.csv").write_text("id,role\n1,teacher\n", encoding="utf-8")

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()

        zipped_names: list[str] = []

        def capture_put(local, remote):
            with zipfile.ZipFile(local) as zf:
                zipped_names.extend(zf.namelist())

        mock_sftp.put.side_effect = capture_put

        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path, manifest={"Students.csv", "Staff.csv"})

        # Single put (the zip), and the zip holds every rostering CSV.
        assert mock_sftp.put.call_count == 1
        assert mock_sftp.put.call_args.args[1].endswith(".zip")
        assert sorted(zipped_names) == ["Staff.csv", "Students.csv"]
        assert sorted(uploaded) == ["Staff.csv", "Students.csv"]

    def test_attendance_only_no_empty_zip(self, tmp_path):
        """Only StudentAttendance.csv present: deliver it standalone, no empty zip."""
        (tmp_path / "StudentAttendance.csv").write_text(
            "School Number,Absence Date,Absence Category,Student Number\n,01-Sep-2025,A,1\n",
            encoding="utf-8",
        )

        uploader = SFTPUploader("sftp.ca.spacesedu.com", 22, "user", "/upload")
        mock_sftp = MagicMock()
        mock_client = MagicMock()

        with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
            uploaded = uploader.upload_csvs(tmp_path, manifest={"StudentAttendance.csv"})

        # Exactly one put — the standalone attendance file, no empty zip.
        assert mock_sftp.put.call_count == 1
        remote_path = mock_sftp.put.call_args.args[1]
        assert remote_path == "/upload/StudentAttendance.csv"
        assert not remote_path.endswith(".zip")
        assert uploaded == ["StudentAttendance.csv"]
        mock_client.close.assert_called_once()
