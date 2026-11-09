"""Tests for src/sftp/uploader.py — SFTP upload with mocked paramiko/keyring."""

import inspect
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.sftp.uploader import (
    INTERACTIVE_RETRY,
    KEYRING_SERVICE,
    LISTING_DENIED_NOTE,
    SFTPUploader,
)
from tests._output_tree import build_output_tree, zip_name


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
            patch("src.sftp.uploader.paramiko.SFTPClient"),
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
            patch("src.sftp.uploader.paramiko.SFTPClient"),
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


ROSTERING = ["Students", "Staff", "Family", "Classes", "Enrollments"]


def _deliver(uploader, output_dir, manifest):
    """Run one delivery with a mocked transport; return (uploaded, zip members, remote paths, local paths)."""
    zipped: list[str] = []
    locals_: list[Path] = []

    def capture_put(local, remote):
        locals_.append(Path(local))
        if str(local).endswith(".zip"):
            with zipfile.ZipFile(local) as zf:
                zipped.extend(zf.namelist())

    mock_sftp = MagicMock()
    mock_sftp.put.side_effect = capture_put
    mock_client = MagicMock()
    with patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)):
        uploaded = uploader.upload_csvs(output_dir, manifest=manifest, retry=INTERACTIVE_RETRY)
    mock_client.close.assert_called_once()
    return uploaded, zipped, [call.args[1] for call in mock_sftp.put.call_args_list], locals_


def _uploader(remote="/upload"):
    return SFTPUploader("sftp.ca.spacesedu.com", 22, "user", remote)


class TestUploadCsvs:
    def test_ships_the_zip_already_in_the_output_folder(self, tmp_path):
        """DSYNC-57: the zip SFTP sends IS the file the conversion left in the folder."""
        zip_path = build_output_tree(tmp_path, ["Students", "Staff"])

        uploaded, zipped, remote, local = _deliver(_uploader(), tmp_path, {"Students.csv", "Staff.csv"})

        assert sorted(uploaded) == ["Staff.csv", "Students.csv"]
        assert sorted(zipped) == ["Staff.csv", "Students.csv"]
        assert local == [zip_path]
        assert remote == [f"/upload/{zip_name()}"]

    def test_the_zip_is_not_rebuilt_or_removed(self, tmp_path):
        zip_path = build_output_tree(tmp_path, ["Students"])
        before = zip_path.read_bytes()

        _deliver(_uploader(), tmp_path, {"Students.csv"})

        assert zip_path.read_bytes() == before
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["csv", zip_name()])

    def test_no_zip_in_the_folder_fails_loud(self, tmp_path):
        """Fail-loud: a silent ``[]`` let callers report a false 'delivered' (exit 3 instead)."""
        (tmp_path / "csv").mkdir()
        (tmp_path / "csv" / "Students.csv").write_text("id\n1\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="exactly one Advanced CSV zip"):
            _uploader().upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)

    def test_two_zips_in_the_folder_fail_loud(self, tmp_path):
        """An ambiguous folder never guesses which bundle is this run's."""
        build_output_tree(tmp_path, ["Students"])
        build_output_tree(tmp_path, ["Students"], for_date=date(2026, 11, 5))
        mock_sftp = MagicMock()
        with (
            patch.object(_uploader(), "_connect", return_value=(MagicMock(), mock_sftp)),
            pytest.raises(RuntimeError, match=r"found 2"),
        ):
            _uploader().upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        mock_sftp.put.assert_not_called()

    def test_an_unreadable_zip_fails_loud(self, tmp_path):
        (tmp_path / zip_name()).write_bytes(b"not a zip")
        with pytest.raises(RuntimeError, match="could not be read"):
            _uploader().upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)

    def test_upload_raises_on_sftp_error(self, tmp_path):
        build_output_tree(tmp_path, ["Students"])
        uploader = _uploader()
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        mock_sftp.put.side_effect = Exception("disk full")
        with (
            patch.object(uploader, "_connect", return_value=(mock_client, mock_sftp)),
            pytest.raises(Exception, match="disk full"),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        mock_client.close.assert_called_once()

    def test_zip_name_and_staging_are_no_longer_parameters(self):
        """Nothing is zipped at delivery time, so there is no name to choose or place to stage."""
        params = inspect.signature(SFTPUploader.upload_csvs).parameters
        assert {"zip_name", "sis_type", "staging_dir"}.isdisjoint(params)


class TestDeliveryManifest:
    """The payload is the caller's MANIFEST, never the output folder's listing.

    An admin who drops a spreadsheet export / backup CSV into the output folder must not
    have it uploaded to SpacesEDU — the folder is *where* the roster lives, not *what*
    the run vouched for.
    """

    def test_foreign_files_in_the_folder_are_not_uploaded(self, tmp_path):
        build_output_tree(tmp_path, ["Students"])
        (tmp_path / "old_roster.csv").write_text("id,name\n9,Ex Student\n", encoding="utf-8")
        (tmp_path / "students_backup.zip").write_bytes(b"x")

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"Students.csv"})

        assert zipped == ["Students.csv"]
        assert uploaded == ["Students.csv"]
        assert remote == [f"/upload/{zip_name()}"]
        # Not delivered — and not touched either (the uploader never deletes).
        assert (tmp_path / "old_roster.csv").exists()
        assert (tmp_path / "students_backup.zip").exists()

    def test_a_full_district_ships_every_rostering_file_in_one_zip(self, tmp_path):
        build_output_tree(tmp_path, ROSTERING)

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {f"{e}.csv" for e in ROSTERING})

        assert sorted(zipped) == sorted(f"{e}.csv" for e in ROSTERING)
        assert sorted(uploaded) == sorted(f"{e}.csv" for e in ROSTERING)
        assert len(remote) == 1

    def test_a_zip_that_disagrees_with_the_manifest_is_refused(self, tmp_path):
        """A zip holding MORE than this run vouches for (a hand-edited or stale bundle) never ships."""
        build_output_tree(tmp_path, ["Students", "Family"])
        mock_sftp = MagicMock()
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", return_value=(MagicMock(), mock_sftp)),
            pytest.raises(RuntimeError, match="does not match the files this run produced"),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv"}, retry=INTERACTIVE_RETRY)
        mock_sftp.put.assert_not_called()

    def test_a_zip_missing_a_manifested_file_is_refused(self, tmp_path):
        """A vouched-for roster file absent from the zip aborts: a partial roster reported as
        'delivered' is the dishonest outcome the exit-3 contract exists to prevent."""
        build_output_tree(tmp_path, ["Students"])
        with pytest.raises(RuntimeError, match="does not match"):
            _uploader().upload_csvs(tmp_path, manifest={"Students.csv", "Enrollments.csv"}, retry=INTERACTIVE_RETRY)

    def test_attendance_split_holds_inside_the_manifest(self, tmp_path):
        """The manifest narrows WHAT ships; an UNmanifested StudentAttendance.csv stays home."""
        build_output_tree(tmp_path, ["Students", "StudentAttendance"])

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"Students.csv", "StudentAttendance.csv"})
        assert zipped == ["Students.csv"]
        assert "/upload/StudentAttendance.csv" in remote
        assert sorted(uploaded) == ["StudentAttendance.csv", "Students.csv"]

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"Students.csv"})
        assert zipped == ["Students.csv"]
        assert "/upload/StudentAttendance.csv" not in remote
        assert uploaded == ["Students.csv"]

    def test_manifested_standalone_feed_missing_from_disk_fails_loud(self, tmp_path):
        build_output_tree(tmp_path, ["Students"])
        mock_sftp = MagicMock()
        uploader = _uploader()
        with (
            patch.object(uploader, "_connect", return_value=(MagicMock(), mock_sftp)),
            pytest.raises(RuntimeError, match="StudentAttendance.csv"),
        ):
            uploader.upload_csvs(tmp_path, manifest={"Students.csv", "StudentAttendance.csv"}, retry=INTERACTIVE_RETRY)
        mock_sftp.put.assert_not_called()  # nothing half-shipped

    def test_empty_manifest_refuses_to_invent_a_payload(self, tmp_path):
        build_output_tree(tmp_path, ["Students"])
        with pytest.raises(RuntimeError, match="nominated for delivery"):
            _uploader().upload_csvs(tmp_path, manifest=set(), retry=INTERACTIVE_RETRY)

    def test_manifest_is_required(self):
        """Keyword-only and REQUIRED: no caller can silently fall back to the folder listing."""
        param = inspect.signature(SFTPUploader.upload_csvs).parameters["manifest"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


class TestUploadStandaloneFeeds:
    """StudentAttendance + the two myBlueprint+ course CSVs ship outside the rostering zip."""

    def test_attendance_delivered_standalone_and_excluded_from_zip(self, tmp_path):
        build_output_tree(tmp_path, ["Students", "Enrollments", "StudentAttendance"])

        uploaded, zipped, remote, _ = _deliver(
            _uploader(), tmp_path, {"Students.csv", "Enrollments.csv", "StudentAttendance.csv"}
        )

        assert sorted(zipped) == ["Enrollments.csv", "Students.csv"]
        assert remote == [f"/upload/{zip_name()}", "/upload/StudentAttendance.csv"]
        assert sorted(uploaded) == ["Enrollments.csv", "StudentAttendance.csv", "Students.csv"]

    def test_attendance_only_ships_no_zip(self, tmp_path):
        """Only StudentAttendance.csv: delivered standalone, and no zip is needed or sent."""
        build_output_tree(tmp_path, ["StudentAttendance"])

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"StudentAttendance.csv"})

        assert remote == ["/upload/StudentAttendance.csv"]
        assert zipped == []
        assert uploaded == ["StudentAttendance.csv"]

    def test_course_only_district_ships_no_zip(self, tmp_path):
        """``mbponly`` emits only the two course CSVs: both go standalone."""
        build_output_tree(tmp_path, ["CourseInfo", "StudentCourses"])

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"CourseInfo.csv", "StudentCourses.csv"})

        assert zipped == []
        assert sorted(remote) == ["/upload/CourseInfo.csv", "/upload/StudentCourses.csv"]
        assert sorted(uploaded) == ["CourseInfo.csv", "StudentCourses.csv"]

    def test_all_eight_entities_split_into_one_zip_and_three_standalone_feeds(self, tmp_path):
        """The widest shipped shape: the split is by feed (ACSV-026)."""
        standalone = ["CourseInfo", "StudentCourses", "StudentAttendance"]
        build_output_tree(tmp_path, ROSTERING + standalone)

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {f"{e}.csv" for e in ROSTERING + standalone})

        assert sorted(zipped) == sorted(f"{e}.csv" for e in ROSTERING)
        assert len(remote) == 4
        for entity in standalone:
            assert f"/upload/{entity}.csv" in remote
        assert sorted(uploaded) == sorted(f"{e}.csv" for e in ROSTERING + standalone)

    def test_unmanifested_course_file_is_not_put_standalone(self, tmp_path):
        """The manifest still decides WHAT ships. A course CSV left by another config's run stays."""
        build_output_tree(tmp_path, ["Students", "CourseInfo"])

        uploaded, zipped, remote, _ = _deliver(_uploader(), tmp_path, {"Students.csv"})

        assert zipped == ["Students.csv"]
        assert "/upload/CourseInfo.csv" not in remote
        assert uploaded == ["Students.csv"]
        assert (tmp_path / "CourseInfo.csv").exists()
