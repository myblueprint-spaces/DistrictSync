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

KEY = b"5f1c-machine-guid"


class TestTheAccountTag:
    def test_it_is_keyed_short_hex_and_never_the_name(self):
        tag = account_tag.account_tag("CORP\\jane.doe", key=KEY)
        assert tag is not None and len(tag) == account_tag.TAG_LENGTH and int(tag, 16) >= 0
        assert "jane" not in tag

    def test_spelling_and_case_do_not_split_one_account(self):
        assert account_tag.account_tag("CORP\\Jane", key=KEY) == account_tag.account_tag(" corp\\jane ", key=KEY)

    def test_positive_twin_two_accounts_and_two_computers_differ(self):
        assert account_tag.account_tag("CORP\\jane", key=KEY) != account_tag.account_tag("CORP\\svc", key=KEY)
        assert account_tag.account_tag("CORP\\jane", key=KEY) != account_tag.account_tag("CORP\\jane", key=b"other")

    @pytest.mark.parametrize("account", ["", "   ", "unknown"])
    def test_no_usable_account_has_no_tag(self, account):
        assert account_tag.account_tag(account, key=KEY) is None

    def test_on_windows_the_key_is_the_machine_guid_whatever_the_profile(self, monkeypatch):
        """One key per computer: a per-user profile and the shared one tag an account identically, so machine setup
        (which copies history.db into the shared profile) keeps "this account" on the earlier runs, and an install
        provisioned before N1 has tags at once."""
        from src.utils import paths

        monkeypatch.setattr(account_tag.sys, "platform", "win32")
        monkeypatch.setattr(account_tag, "_read_machine_guid", lambda: KEY)
        monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
        for shared in (False, True):
            monkeypatch.setattr(paths, "is_machine_scope", lambda shared=shared: shared)
            assert account_tag.current_tag() == account_tag.account_tag("CORP\\jane", key=KEY)

    def test_an_unreadable_key_has_no_tag_and_a_log_name_that_is_not_a_hash(self, monkeypatch):
        monkeypatch.setattr(account_tag.sys, "platform", "win32")

        def boom():
            raise OSError("denied")

        monkeypatch.setattr(account_tag, "_read_machine_guid", boom)
        monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
        assert account_tag.tag_key() is None and account_tag.current_tag() is None
        assert account_tag.log_name_tag() == account_tag.NO_KEY_FRAGMENT  # nothing a list of names could reverse

    def test_off_windows_the_profile_key_is_created_once_and_never_replaced(self, monkeypatch, isolated_user_profile):
        monkeypatch.setattr(account_tag.sys, "platform", "linux")
        first = account_tag.tag_key()
        assert first is not None and len(first) == account_tag.PROFILE_KEY_BYTES
        assert account_tag.tag_key() == first
        (isolated_user_profile / account_tag.PROFILE_KEY_FILE).write_bytes(b"short")
        assert account_tag.tag_key() == b"short"  # an existing key is used, never silently replaced


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
            # review of DSYNC-100: the text after a name survives, and a second path is still redacted
            (
                "Could not read C:\\Users\\Jane (PermissionError) retrying in 5 s",
                "Could not read C:\\Users\\<user> (PermissionError) retrying in 5 s",
            ),
            ("/Users/admin and C:\\Users\\Administrator\\x", "/Users/<user> and C:\\Users\\<user>\\x"),
            ("C:\\Users\\Jane Doe\\gde", "C:\\Users\\<user>\\gde"),  # a name with a space, inside a path
            # other people's home folders off C:\Users: a redirected share, a Profiles drive
            ("\\\\fileserver\\home\\jdoe\\gde\\students.csv", "\\\\fileserver\\home\\<user>\\gde\\students.csv"),
            ("\\\\fs01\\staff\\users\\jdoe\\gde", "\\\\fs01\\staff\\users\\<user>\\gde"),
            ("D:\\Profiles\\jdoe\\gde", "D:\\Profiles\\<user>\\gde"),
            ("ran as ZQ_PERSON ok", "ran as <account> ok"),  # account names are case-insensitive
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
