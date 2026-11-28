"""DSYNC-111: after registering an unattended nightly, say whether its account can log on as a batch job.

The lab case: ``--schedule-register --principal password:<COMPUTER>\\svc_local`` printed "Scheduled" and exited 0, and the
nightly then never ran (Last Run Result 267011, event 4625 0xC000015B): the account lacked 'Log on as a batch job'.

What is pinned here is the pure decision (:func:`batch_right.assess`), the one reader seam it reuses
(``schedule_check.read_batch_right``, the DSYNC-33 LSA read), the moved prerequisite lines, the "no automatic grant"
source scan and the new exit code's documentation. The command's own wiring is in ``tests/test_schedule_headless.py``,
the window's in ``tests/test_ui_flet_service_account.py``.

GROUP GRANTS: the DSYNC-33 read sees only the account's OWN (direct) rights. A right that comes through a group or a
domain policy reads ``NOT_DIRECT``, the same as "nobody granted it", so ``NOT_DIRECT`` is INCONCLUSIVE, never a finding.
Only an explicit deny, or Windows itself refusing the logon type when it started the gMSA check run, is a positive
"not held".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.scheduler import batch_right, schedule_check
from src.scheduler.batch_right import BatchRight, assess
from src.scheduler.schedule_check import CheckVerdict, RightState, ScheduleCheck
from src.scheduler.task_com import PrincipalKind

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _check(verdict: CheckVerdict, state: RightState = RightState.UNKNOWN) -> ScheduleCheck:
    return ScheduleCheck(verdict=verdict, batch_right=state)


class TestAssess:
    """The decision table. Every NOT_HELD row has a non-NOT_HELD twin one fact away."""

    def test_an_explicit_deny_is_not_held(self):
        assert assess(RightState.DENIED) is BatchRight.NOT_HELD

    def test_a_direct_grant_is_held(self):
        assert assess(RightState.GRANTED) is BatchRight.HELD

    def test_not_granted_directly_is_inconclusive_never_not_held(self):
        """The group case: Administrators (or any group) holding the right reads NOT_DIRECT for its members."""
        assert assess(RightState.NOT_DIRECT) is BatchRight.INCONCLUSIVE

    def test_an_unreadable_policy_is_inconclusive(self):
        assert assess(RightState.UNKNOWN) is BatchRight.INCONCLUSIVE

    def test_windows_refusing_the_logon_type_in_the_check_run_is_not_held(self):
        """ERROR_LOGON_TYPE_NOT_GRANTED is Windows' own verdict over the whole token, groups included."""
        assert assess(RightState.NOT_DIRECT, check=_check(CheckVerdict.BATCH_LOGON)) is BatchRight.NOT_HELD

    def test_a_passing_check_run_is_held_even_without_a_direct_grant(self):
        """Positive twin of the row above: Windows DID start DistrictSync as the account, so it holds the right."""
        assert assess(RightState.NOT_DIRECT, check=_check(CheckVerdict.OK)) is BatchRight.HELD

    @pytest.mark.parametrize(
        "verdict", [v for v in CheckVerdict if v not in (CheckVerdict.OK, CheckVerdict.BATCH_LOGON)]
    )
    def test_any_other_check_verdict_does_not_decide_it(self, verdict):
        assert assess(RightState.NOT_DIRECT, check=_check(verdict)) is BatchRight.INCONCLUSIVE
        assert assess(RightState.GRANTED, check=_check(verdict)) is BatchRight.HELD
        assert assess(RightState.DENIED, check=_check(verdict)) is BatchRight.NOT_HELD

    def test_it_is_total(self):
        for state in RightState:
            for verdict in [None, *CheckVerdict]:
                check = None if verdict is None else _check(verdict)
                assert assess(state, check=check) in set(BatchRight)


class TestAfterRegistration:
    def test_the_signed_in_account_is_never_checked(self, monkeypatch):
        monkeypatch.setattr(batch_right, "read_right", lambda account: pytest.fail("read for a signed-in task"))
        assert batch_right.after_registration(PrincipalKind.INTERACTIVE_TOKEN, "") is None

    @pytest.mark.parametrize("kind", [PrincipalKind.PASSWORD, PrincipalKind.MANAGED_SERVICE_ACCOUNT])
    def test_an_unattended_account_is_read_by_its_name(self, monkeypatch, kind):
        seen = []
        monkeypatch.setattr(batch_right, "read_right", lambda account: (seen.append(account), RightState.DENIED)[1])
        assert batch_right.after_registration(kind, "CORP\\svc") is BatchRight.NOT_HELD
        assert seen == ["CORP\\svc"]

    def test_the_elevated_checks_own_read_is_used_when_it_answered(self, monkeypatch):
        monkeypatch.setattr(batch_right, "read_right", lambda account: pytest.fail("read twice"))
        check = _check(CheckVerdict.TIMEOUT, RightState.DENIED)
        assert batch_right.after_registration(PrincipalKind.MANAGED_SERVICE_ACCOUNT, "CORP\\s$", check=check) is (
            BatchRight.NOT_HELD
        )

    def test_an_unanswered_elevated_read_falls_back_to_this_process(self, monkeypatch):
        monkeypatch.setattr(batch_right, "read_right", lambda account: RightState.GRANTED)
        check = _check(CheckVerdict.TIMEOUT, RightState.UNKNOWN)
        assert batch_right.after_registration(PrincipalKind.MANAGED_SERVICE_ACCOUNT, "CORP\\s$", check=check) is (
            BatchRight.HELD
        )

    def test_the_log_line_names_no_account(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(batch_right, "read_right", lambda account: RightState.DENIED)
        with caplog.at_level(logging.DEBUG):
            batch_right.after_registration(PrincipalKind.PASSWORD, "CORP\\svc_secretname")
        assert "not_held" in caplog.text  # positive twin: the line was written
        assert "svc_secretname" not in caplog.text


class TestTheReaderIsTheDsync33Read:
    """``schedule_check.read_batch_right`` is the elevated check's own read, factored out, never a second LSA path."""

    def test_a_direct_grant(self):
        state = schedule_check.read_batch_right(
            "CORP\\svc", account_sid=lambda n: "S-1-5-21-1", reader=lambda sid: ["SeBatchLogonRight"]
        )
        assert state is RightState.GRANTED

    def test_a_direct_deny_wins(self):
        state = schedule_check.read_batch_right(
            "CORP\\svc",
            account_sid=lambda n: "S-1-5-21-1",
            reader=lambda sid: ["SeBatchLogonRight", "SeDenyBatchLogonRight"],
        )
        assert state is RightState.DENIED

    def test_no_direct_rights_is_not_direct(self):
        def none(sid):
            raise schedule_check.NoRightsAssigned

        assert schedule_check.read_batch_right("CORP\\svc", account_sid=lambda n: "S-1", reader=none) is (
            RightState.NOT_DIRECT
        )

    def test_an_unresolvable_account_is_unknown(self):
        def boom(name):
            raise LookupError("no such account")

        assert schedule_check.read_batch_right("CORP\\svc", account_sid=boom, reader=lambda s: []) is (
            RightState.UNKNOWN
        )

    def test_a_reader_that_raises_is_unknown(self):
        def boom(sid):
            raise OSError("access denied")

        assert schedule_check.read_batch_right("CORP\\svc", account_sid=lambda n: "S-1", reader=boom) is (
            RightState.UNKNOWN
        )

    def test_the_live_seam_calls_it(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            schedule_check, "read_batch_right", lambda account, **kw: (seen.append(account), RightState.GRANTED)[1]
        )
        # The conftest guard replaces read_right itself; reach past it to the real seam body.
        assert batch_right._read_right_live("CORP\\svc") is RightState.GRANTED and seen == ["CORP\\svc"]


class TestThePrerequisiteLines:
    def test_the_window_reads_the_same_tuple(self):
        from src.ui_flet import setup_flow

        assert setup_flow.GMSA_PREREQUISITES is batch_right.GMSA_PREREQUISITES

    def test_the_batch_line_is_one_of_the_gmsa_lines(self):
        assert batch_right.BATCH_RIGHT_PREREQUISITE in batch_right.GMSA_PREREQUISITES

    def test_each_kind_gets_its_own(self):
        assert batch_right.prerequisites_for(PrincipalKind.MANAGED_SERVICE_ACCOUNT) == batch_right.GMSA_PREREQUISITES
        assert batch_right.prerequisites_for(PrincipalKind.PASSWORD) == (batch_right.BATCH_RIGHT_PREREQUISITE,)
        assert batch_right.prerequisites_for(PrincipalKind.INTERACTIVE_TOKEN) == ()

    def test_the_module_is_not_ui(self):
        text = (SRC / "scheduler" / "batch_right.py").read_text(encoding="utf-8")
        assert "ui_flet" not in text and "import flet" not in text


class TestTheNotHeldCopy:
    """What is missing, why the nightly will not run, and exactly how to grant it."""

    def test_it_says_what_is_missing_and_why(self):
        text = batch_right.NOT_HELD_DETAIL
        assert "'Log on as a batch job'" in text
        assert "won't start the nightly" in text

    def test_it_says_the_nightly_stays_scheduled(self):
        assert "stays scheduled" in batch_right.NOT_HELD_DETAIL
        assert "don't need to schedule it again" in batch_right.NOT_HELD_DETAIL

    def test_it_says_how_to_grant_it_locally_and_by_group_policy(self):
        text = batch_right.NOT_HELD_DETAIL
        assert "Local Security Policy > Local Policies > User Rights Assignment" in text
        assert "Group Policy" in text

    def test_it_says_a_deny_overrides_a_grant(self):
        assert "overrides any grant" in batch_right.NOT_HELD_DETAIL

    def test_the_inconclusive_line_is_an_advisory_not_a_finding(self):
        text = batch_right.INCONCLUSIVE_LINE
        assert "couldn't confirm" in text and "group" in text
        assert "won't run yet" not in text

    @pytest.mark.parametrize(
        "name", ["NOT_HELD_HEADLINE", "NOT_HELD_DETAIL", "INCONCLUSIVE_LINE", "HELD_LINE", "PREREQUISITES_LEAD"]
    )
    def test_the_copy_is_plain_ascii_for_any_console(self, name):
        getattr(batch_right, name).encode("ascii")


def _src_files():
    return sorted(SRC.rglob("*.py"))


#: The ways a program grants a user right: the LSA call, the resource-kit tool, a security template import, and the
#: PowerShell modules that wrap them. DSYNC-111 decision 4: granting user rights stays district IT's job.
_GRANT_MARKERS = re.compile(r"LsaAddAccountRights|ntrights|secedit|Grant-UserRight|Carbon", re.IGNORECASE)


class TestNoAutomaticGrant:
    def test_nothing_in_src_grants_a_user_right(self):
        hits = [
            f"{p.relative_to(ROOT)}:{n}"
            for p in _src_files()
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if _GRANT_MARKERS.search(line)
        ]
        assert hits == []

    def test_positive_twin_the_scan_sees_the_read(self):
        """The same scan finds the READ, so it is looking at the right files."""
        reads = [p.name for p in _src_files() if "LsaEnumerateAccountRights" in p.read_text(encoding="utf-8")]
        assert "schedule_check.py" in reads

    def test_positive_twin_the_pattern_has_teeth(self, tmp_path):
        planted = tmp_path / "x.py"
        planted.write_text("win32security.LsaAddAccountRights(policy, sid, ['SeBatchLogonRight'])\n", encoding="utf-8")
        assert _GRANT_MARKERS.search(planted.read_text(encoding="utf-8"))


class TestTheExitCode:
    def test_it_is_four_and_distinct(self):
        from src.scheduler import headless

        code = headless.EXIT_REGISTERED_WITHOUT_BATCH_RIGHT
        assert code == 4
        assert code not in {0, 1, 2, 3}
        exits = {v for k, v in vars(schedule_check).items() if k.startswith("CHECK_EXIT_")}
        assert code not in exits

    @pytest.mark.parametrize(
        "doc",
        [
            "CLAUDE.md",
            "docs/developer/architecture-notes.md",
            "docs/partner/it-deployment.md",
            "docs/partner/troubleshooting.md",
            "docs/developer/scheduler.md",
        ],
    )
    def test_every_written_contract_states_it(self, doc):
        text = re.sub(r"\s+", " ", (ROOT / doc).read_text(encoding="utf-8"))
        assert re.search(r"`4`|\*\*4\*\*|exits? (with )?(code )?4\b|\b4 registered", text), doc
        assert "batch job" in text, doc
