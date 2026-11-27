"""Plan 0055 N1 (DSYNC-100): no account names in logs, log file names or run records.

Districts do not run DistrictSync as a named person and do not want people's names in its logs (owner, 2026-11-23).
Pinned here:

* the keyed account tag (``account_tag``): pure, case-insensitive, never the name, ``None`` without a salt, and a shared
  profile never creates its own salt;
* the log-name role pre-scan (the sink opens before argparse reads ``--source``);
* the one choke point (``redact`` / ``RedactingFormatter``): profile paths and this process's account, tracebacks too;
* Run History tells accounts apart by tag, and still reads a record written before the tag existed;
* the schedule-check marker carries the tag;
* a scan: a scheduled run by an account with a distinctive name, its input under that account's profile folder, writes
  no file that contains the name.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.ui_flet import run_history
from src.utils import account_tag, redact
from tests.test_pipeline_run_store import _write_myedbc_input

SALT = bytes(range(32))


class TestTheAccountTag:
    def test_it_is_keyed_short_hex_and_never_the_name(self):
        tag = account_tag.account_tag("CORP\\jane.doe", salt=SALT)
        assert tag is not None and len(tag) == account_tag.TAG_LENGTH and int(tag, 16) >= 0
        assert "jane" not in tag

    def test_spelling_and_case_do_not_split_one_account(self):
        assert account_tag.account_tag("CORP\\Jane", salt=SALT) == account_tag.account_tag(" corp\\jane ", salt=SALT)

    def test_positive_twin_two_accounts_and_two_salts_differ(self):
        assert account_tag.account_tag("CORP\\jane", salt=SALT) != account_tag.account_tag("CORP\\svc", salt=SALT)
        assert account_tag.account_tag("CORP\\jane", salt=SALT) != account_tag.account_tag("CORP\\jane", salt=bytes(32))

    @pytest.mark.parametrize("account", ["", "   ", "unknown"])
    def test_no_usable_account_has_no_tag(self, account):
        assert account_tag.account_tag(account, salt=SALT) is None

    def test_a_per_user_profile_creates_its_salt_once_and_reuses_it(self, isolated_user_profile):
        first = account_tag.install_salt()
        assert first is not None and len(first) == account_tag.SALT_BYTES
        assert account_tag.install_salt() == first
        assert (isolated_user_profile / account_tag.PROFILE_SALT_FILE).read_bytes() == first

    def test_a_shared_profile_without_a_salt_has_no_tag_and_creates_nothing(self, monkeypatch, isolated_user_profile):
        from src.utils import paths

        monkeypatch.setattr(paths, "is_machine_scope", lambda: True)
        monkeypatch.setattr(account_tag, "_read_machine_salt", lambda: None)
        assert account_tag.install_salt() is None
        assert account_tag.current_tag() is None
        assert not (isolated_user_profile / account_tag.PROFILE_SALT_FILE).exists()
        # the log name still separates writers, without the name
        monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
        name_tag = account_tag.log_name_tag()
        assert name_tag.startswith("u") and "jane" not in name_tag

    def test_a_shared_profile_uses_the_machine_salt(self, monkeypatch):
        from src.utils import paths

        monkeypatch.setattr(paths, "is_machine_scope", lambda: True)
        monkeypatch.setattr(account_tag, "_read_machine_salt", lambda: SALT)
        monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
        assert account_tag.current_tag() == account_tag.account_tag("CORP\\jane", salt=SALT)
        assert account_tag.log_name_tag() == account_tag.current_tag()

    def test_a_failing_salt_read_degrades_to_no_tag(self, monkeypatch):
        from src.utils import paths

        monkeypatch.setattr(paths, "is_machine_scope", lambda: True)

        def boom():
            raise OSError("denied")

        monkeypatch.setattr(account_tag, "_read_machine_salt", boom)
        assert account_tag.install_salt() is None


class TestTheRole:
    @pytest.mark.parametrize(
        ("argv", "role"),
        [
            (["--sis", "x", "--source", "scheduled"], "scheduled"),
            (["--source=scheduled"], "scheduled"),
            (["--source", "cli"], "interactive"),
            ([], "interactive"),
            (["--source"], "interactive"),
        ],
    )
    def test_the_argv_pre_scan(self, monkeypatch, argv, role):
        monkeypatch.delenv("DSYNC_SOURCE", raising=False)
        assert account_tag.process_role(argv) == role

    def test_the_environment_marker(self, monkeypatch):
        monkeypatch.setenv("DSYNC_SOURCE", "scheduled")
        assert account_tag.process_role([]) == "scheduled"


IDENTITY = ("LABCORP\\Zq_Person", "Zq_Person", "C:\\Users\\Zq_Person")


class TestRedact:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("C:\\Users\\Zq_Person\\Downloads\\gde", "C:\\Users\\<user>\\Downloads\\gde"),
            ("c:/users/someone.else/x.csv", "c:/users/<user>/x.csv"),
            ('"C:\\\\Users\\\\other\\\\in"', '"C:\\\\Users\\\\<user>\\\\in"'),  # json-escaped
            ("/home/someone/gde", "/home/<user>/gde"),
            ("ran as LABCORP\\zq_person today", "ran as <account> today"),
            ("user Zq_Person failed", "user <account> failed"),
            ("C:\\Users\\Public\\gde", "C:\\Users\\Public\\gde"),  # not a person
            ("Zq_Personal and xZq_Person stay", "Zq_Personal and xZq_Person stay"),  # whole words only
            ("C:\\ProgramData\\DistrictSync\\runs", "C:\\ProgramData\\DistrictSync\\runs"),
        ],
    )
    def test_it(self, text, expected):
        assert redact.redact(text, identity=IDENTITY) == expected

    def test_the_home_folder_is_redacted_wherever_it_lives(self):
        identity = ("", "zq", "D:\\Profiles\\zq_person")
        assert redact.redact("D:\\Profiles\\zq_person\\in", identity=identity) == "D:\\Profiles\\<user>\\in"

    def test_the_formatter_redacts_the_traceback_too(self, monkeypatch):
        monkeypatch.setattr(redact, "_identity", lambda: IDENTITY)
        formatter = redact.RedactingFormatter("%(message)s")
        try:
            raise FileNotFoundError("C:\\Users\\Zq_Person\\gde\\students.csv")
        except FileNotFoundError:
            import sys

            record = logging.LogRecord(
                "t", logging.ERROR, __file__, 1, "reading %s", ("C:\\Users\\Zq_Person",), sys.exc_info()
            )
        line = formatter.format(record)
        assert "Zq_Person" not in line and "<user>" in line and "FileNotFoundError" in line

    def test_the_file_sink_uses_it(self):
        import tempfile

        from src.utils.logger import _build_file_handler

        with tempfile.TemporaryDirectory() as tmp:
            handler = _build_file_handler(Path(tmp) / "x.log")
            try:
                assert isinstance(handler.formatter, redact.RedactingFormatter)
            finally:
                handler.close()


class TestRunHistoryComparesTags:
    def test_same_tag_is_this_account(self):
        assert run_history.run_as_display(None, current_account="", tag="abc", current_tag="abc") == "This account"

    def test_another_tag_is_another_account(self):
        assert run_history.run_as_display(None, current_account="", tag="abc", current_tag="def") == "Another account"

    def test_no_tag_for_this_process_is_not_established(self):
        assert run_history.run_as_display(None, current_account="CORP\\jane", tag="abc", current_tag=None) == ""

    def test_a_record_from_before_the_tag_still_reads(self):
        assert run_history.run_as_display("CORP\\jane", current_account="corp\\jane") == "This account"

    def test_the_rows_get_the_tag(self):
        rows = run_history.to_run_rows(
            [{"status": "success", "run_as_tag": "abc"}, {"status": "success", "run_as_tag": "def"}], current_tag="abc"
        )
        assert [r.run_as for r in rows] == ["This account", "Another account"]


def test_the_schedule_check_marker_carries_the_tag(tmp_path, monkeypatch):
    from src.scheduler import schedule_check

    monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
    marker = tmp_path / "marker.json"
    schedule_check._write_marker(marker, "tok")
    body = json.loads(marker.read_text(encoding="utf-8"))
    assert body["account"] == account_tag.log_name_tag()
    assert "jane" not in marker.read_text(encoding="utf-8")


NAME = "Zq_Distinctive_Person"


def test_a_scheduled_run_writes_no_file_that_names_the_account(isolated_user_profile, tmp_path, monkeypatch):
    """The scan (plan section 4): the account and its profile folder are named NAME; the run's input sits in that folder
    so the log carries paths. No file written under the profile may contain
    NAME. config.json (``schedule_run_as_user``, needed to re-register) is the one declared exception."""
    from src.etl.pipeline import run_pipeline
    from src.main import _configure_cli_logging
    from src.utils.accounts import process_account

    home = tmp_path / "Users" / NAME
    monkeypatch.setenv("USERDOMAIN", "LABCORP")
    monkeypatch.setenv("USERNAME", NAME)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert NAME in process_account() and NAME in str(Path.home())  # positive twin: the name is really in play

    gde = home / "Downloads" / "gde"
    gde.mkdir(parents=True)
    _write_myedbc_input(gde)
    out = home / "out"
    out.mkdir()

    _configure_cli_logging()
    logging.getLogger("src.etl.pipeline").warning("input folder is %s", gde)  # a path in a log line, as the ETL writes
    run_pipeline("myedbc", str(gde), str(out), source="scheduled")
    for handler in logging.getLogger().handlers:
        handler.flush()

    written = [p for p in isolated_user_profile.rglob("*") if p.is_file() and p.name != "config.json"]
    assert any(p.suffix == ".log" for p in written) and any(p.suffix == ".db" for p in written)  # the scan sees them
    log_text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in written if p.suffix == ".log")
    assert "__DISTRICTSYNC_RUN__" in log_text and "<user>" in log_text  # it scanned real lines with redacted paths
    offenders = [p.name for p in written if NAME.casefold().encode() in p.read_bytes().lower()]
    assert offenders == []
