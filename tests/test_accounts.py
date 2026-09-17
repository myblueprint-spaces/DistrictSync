"""Edge tables for ``src/utils/accounts.py`` — who is running, as a filename-safe name.

Two COUNTED primitives with no I/O beyond the environment read:

* ``process_account()`` — the account this process runs as, for a log-file name and
  (S-1a-ii) the ``run_as`` key on a run record. Never raises: it is called from a path
  resolver, so a surprise here would take down the log sink itself.
* ``sanitise_account_for_filename()`` — pure and TOTAL. It names a FILE, so it may never
  return ``""`` and may never return a path separator; a ``DOMAIN\\user`` that survived as
  ``domain\\user`` would silently redirect the machine-scope log into a subdirectory (or
  fail the open) on the very install that needs two writers not to contend.

Both semantics are DECIDED by the plan-0049 S-1a-i.2 spec, so the table is written
against the spec rather than derived from the implementation.
"""

from __future__ import annotations

import pytest

from src.utils.accounts import process_account, sanitise_account_for_filename


class TestProcessAccount:
    def test_domain_and_user_join_with_a_backslash(self, monkeypatch):
        monkeypatch.setenv("USERDOMAIN", "CORP")
        monkeypatch.setenv("USERNAME", "jane")
        assert process_account() == "CORP\\jane"

    @pytest.mark.parametrize("domain", ["", "   "])
    def test_blank_domain_falls_back_to_getpass(self, monkeypatch, domain):
        monkeypatch.setenv("USERDOMAIN", domain)
        monkeypatch.setenv("USERNAME", "jane")
        monkeypatch.setattr("src.utils.accounts.getpass.getuser", lambda: "jane")
        assert process_account() == "jane"

    def test_missing_username_falls_back_to_getpass(self, monkeypatch):
        monkeypatch.delenv("USERDOMAIN", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.setattr("src.utils.accounts.getpass.getuser", lambda: "someone")
        assert process_account() == "someone"

    def test_getpass_raising_yields_unknown_not_an_exception(self, monkeypatch):
        # A path resolver calls this; raising here would take the log sink with it.
        monkeypatch.delenv("USERDOMAIN", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)

        def _boom() -> str:
            raise OSError("no login name")

        monkeypatch.setattr("src.utils.accounts.getpass.getuser", _boom)
        assert process_account() == "unknown"

    def test_getpass_returning_blank_yields_unknown(self, monkeypatch):
        monkeypatch.delenv("USERDOMAIN", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.setattr("src.utils.accounts.getpass.getuser", lambda: "   ")
        assert process_account() == "unknown"

    def test_result_is_never_blank_in_the_real_environment(self):
        # The positive twin for the fallbacks above: with the real environment the
        # function still answers something usable (it is called unconditionally).
        assert process_account().strip() != ""


class TestSanitiseAccountForFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("CORP\\jane", "corp_jane"),
            ("corp/jane", "corp_jane"),
            ("jane", "jane"),
            ("Jane.Doe-2", "jane.doe-2"),
            ("CORP\\svc$", "corp_svc"),  # trailing separator run is stripped, never left dangling
            ("a  b", "a_b"),  # repeats collapse to ONE underscore
            ("CORP\\\\jane", "corp_jane"),
            ("héllo", "h_llo"),
            ("", "unknown"),
            ("   ", "unknown"),
            ("\\\\", "unknown"),
            ("$$$", "unknown"),
        ],
    )
    def test_table(self, raw, expected):
        assert sanitise_account_for_filename(raw) == expected

    def test_truncated_to_32(self):
        out = sanitise_account_for_filename("A" * 60)
        assert len(out) == 32
        assert out == "a" * 32

    def test_truncation_never_leaves_a_trailing_separator(self):
        # 31 legal chars then a run of illegal ones: the cut must not end in "_".
        out = sanitise_account_for_filename("a" * 31 + "\\\\\\\\jane")
        assert len(out) <= 32
        assert not out.endswith("_")

    @pytest.mark.parametrize(
        "hostile",
        [
            "..\\..\\..\\windows\\system32",
            "../../etc/passwd",
            "CON",
            "a:b|c*d?e",
            "\x00null",
            "\n\t",
            "ünïcödé\\ßtuff",
        ],
    )
    def test_total_over_hostile_input(self, hostile):
        out = sanitise_account_for_filename(hostile)
        assert out != ""
        assert "/" not in out and "\\" not in out
        assert set(out) <= set("abcdefghijklmnopqrstuvwxyz0123456789._-")
        assert len(out) <= 32

    def test_is_pure(self, monkeypatch):
        # No environment read: the caller decides WHOSE name this is.
        monkeypatch.setenv("USERNAME", "somebody-else")
        assert sanitise_account_for_filename("CORP\\jane") == "corp_jane"
