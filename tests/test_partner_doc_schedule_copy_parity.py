"""The partner troubleshooting page quotes the schedule classifier — this ties it back (plan 0047 A2).

CLAUDE.md's testing conventions: *any literal copied out of ``src/`` into a standalone
script/CI/doc needs a parity test tying it back*. `docs/partner/troubleshooting.md` tells a
district admin which on-screen sentence they are looking at and what to do about it — so a
sentence the app has since reworded sends them hunting for text that no longer exists. That
is not hypothetical here: the bullet this slice replaced reproduced the PIN / microsoft.com /
"Log on as a batch job" coaching from a classifier branch the PowerShell transport took with
it at plan 0041 S1b, and it sat there wrong for months with every gate green.

Pinned in BOTH directions, per the declared-gap discipline in
``docs/claugentic-standards/CANDIDATES.md`` and the shape of
``tests/test_creator_doc_copy_parity.py``:

* every string a doc is DECLARED to quote must be present in it verbatim — so rewording a
  classifier branch and not touching the docs is red;
* every string a doc is declared NOT to quote must be ABSENT from it — so quoting the copy in
  a new doc without registering it here is also red, instead of arriving unpinned.

**The one structural adaptation from the precedent:** that test diffs bound module-level
constants. The classifier's copy is built by f-string interpolation and concatenation INSIDE
branches, so there is no constant to import for most of it. Each pinned string is therefore
declared here and then proved against the PRODUCER — ``classify_schedule_error``'s own return
value for the canonical that produces it — so a declared quote can never drift away from the
app even though it is spelled in this file. Two rows are genuinely derived instead: the policy
HRESULT (``format_hresult(HR_NO_SUCH_LOGON_SESSION)``) and the log-line anchor (the ``[HRESULT
`` prefix of ``windows._FAIL_LOG_FORMAT``), which is the one thing the doc tells an admin to
grep ``etl_tool.log`` for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduler import windows
from src.scheduler.task_com import (
    HR_NO_SUCH_LOGON_SESSION,
    MSG_ACCOUNT_INFO_NOT_SET,
    MSG_COM_UNAVAILABLE,
    MSG_LOGON_FAILURE,
    MSG_NO_LOGON_SESSION,
    format_hresult,
)
from src.ui_flet.home_status import MACHINE_SCOPE_LINE_LEAD, machine_scope_line
from src.ui_flet.setup_errors import classify_schedule_error
from src.utils.diagnostics import SCOPE_PER_USER, SCOPE_SHARED

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TROUBLESHOOTING = "docs/partner/troubleshooting.md"
_HEADLESS = "docs/partner/headless-sftp-setup.md"
_INSTALLATION = "docs/partner/installation.md"

#: The ``[HRESULT `` grep anchor, DERIVED from the one failure-log format string rather than
#: retyped — the doc tells an admin to search ``etl_tool.log`` for exactly this.
_LOG_ANCHOR = "[" + windows._FAIL_LOG_FORMAT.split("[", 1)[1].split("%", 1)[0]

#: The policy code, DERIVED through the same formatter the classifier and the log line use.
_POLICY_CODE = format_hresult(HR_NO_SUCH_LOGON_SESSION)

#: name -> (the quoted string, the classifier output it must also appear in). ``None`` as the
#: producer means the string is its own producer (a module constant, or a derived value).
_PINNED: dict[str, tuple[str, str | None]] = {
    "credential_headline": (
        "Windows rejected the user name or password",
        classify_schedule_error(MSG_LOGON_FAILURE, False, account_is_current=True),
    ),
    "elevated_refusal_headline": (
        "Something on this computer is likely blocking the schedule change even after the "
        "permission prompt was approved",
        classify_schedule_error(windows._MSG_ELEVATED_ACCESS_DENIED, False, account_is_current=True),
    ),
    "account_info_headline": (
        "Windows is missing the nightly task's own saved account details",
        classify_schedule_error(MSG_ACCOUNT_INFO_NOT_SET, False, account_is_current=True),
    ),
    "com_unavailable_headline": (
        "This copy of DistrictSync can't reach Windows Task Scheduler",
        classify_schedule_error(MSG_COM_UNAVAILABLE, False, account_is_current=True),
    ),
    "policy_headline": (
        "Windows would not save the password for the nightly task",
        classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True),
    ),
    "policy_setting_name": (
        "Network access: Do not allow storage of passwords and credentials for network authentication",
        classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True),
    ),
    "policy_code": (_POLICY_CODE, classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True)),
    # The doc must quote what the ADMIN SEES, not the engine constant that selects it:
    # `windows._MSG_DIFFERENT_ACCOUNT` is internal and never rendered, so pinning it tied the
    # doc to a string the classifier could stop producing without this test noticing. Pinning
    # the shipped lead WITH its producer is what makes the row bidirectional.
    "different_account_lead": (
        "The elevated step couldn't read the request DistrictSync prepared under your account",
        classify_schedule_error(windows._MSG_DIFFERENT_ACCOUNT, False, account_is_current=True),
    ),
    "log_anchor": (_LOG_ANCHOR, None),
    # Plan 0049. The service-account guide now opens by telling an admin to READ these three
    # strings off the app to decide whether they still need the manual ``--sftp-configure``
    # step. That decision's wrong answer is SILENT — delivery simply stops and nothing alarms
    # — so a reworded string here is not cosmetic drift, it is a district losing its nightly
    # upload with no signal. Proved against the real renderer, not against a second copy.
    "machine_scope_lead": (
        MACHINE_SCOPE_LINE_LEAD,
        machine_scope_line(machine_scope=True, provisioned_by="", provisioned_at=""),
    ),
    "diagnose_scope_shared": (SCOPE_SHARED, None),
    "diagnose_scope_per_user": (SCOPE_PER_USER, None),
}

#: doc -> the strings that doc is DECLARED to quote. Anything not listed must be ABSENT.
#: The empty declarations are deliberate and load-bearing: they are what makes a NEW quote in
#: a doc that has none today go red instead of arriving unpinned.
#: The plan-0049 family: strings an admin READS OFF THE APP to decide which kind of install
#: they have. They belong to the two install/service-account guides, not to the failure page.
_SCOPE_PINS: frozenset[str] = frozenset({"machine_scope_lead", "diagnose_scope_shared", "diagnose_scope_per_user"})

_DOC_QUOTES: dict[str, frozenset[str]] = {
    # Everything EXCEPT the scope family: the troubleshooting page is about schedule FAILURES,
    # and this row deliberately stays "all of them" for that family so a new classifier quote
    # lands pinned by default rather than arriving unnoticed.
    _TROUBLESHOOTING: frozenset(_PINNED) - _SCOPE_PINS,
    # The harness docs describe the log line's shape, so they legitimately carry the anchor.
    "CLAUDE.md": frozenset({"log_anchor"}),
    "docs/claugentic-ARCHITECTURE_TREE.md": frozenset({"log_anchor"}),
    # Plan 0049 S-2b: the service-account guide leads with "which kind of install is this?",
    # and Step 4 of the install guide explains the line. Both quote the app verbatim.
    _HEADLESS: _SCOPE_PINS,
    _INSTALLATION: _SCOPE_PINS,
    # The release notes announce the line by name (but not the terminal report's two scope
    # words), so it declares the headline pin alone.
    "CHANGELOG.md": frozenset({"machine_scope_lead"}),
    "docs/partner/faq.md": frozenset(),
    "docs/partner/help-centre-myedbc-districtsync-guide.md": frozenset(),
}

#: Retired with the PowerShell-era classifier branch (plan 0041 S1b) and with plan 0046's A7
#: finding that the coaching is wrong against a service account. AC A2.7: none may survive in
#: the partner page. Fixed literals, not constants — nothing in ``src/`` spells them any more,
#: which is exactly why the doc had to be checked by hand until now.
_RETIRED_PHRASES: tuple[str, ...] = ("PIN", "microsoft.com", "Log on as a batch job")


def _doc_text(relative: str) -> str:
    path = _REPO_ROOT / relative
    assert path.is_file(), f"{relative} is missing — this pin is watching a file that moved"
    return path.read_text(encoding="utf-8")


def test_the_pinned_set_is_the_one_this_test_was_written_for() -> None:
    """Non-vacuity: every pinned string must be a usable literal, and every declaration must
    name a string this table holds. A renamed or emptied entry would silently drop out of the
    tables below and make every row underneath assert nothing at all."""
    assert _PINNED, "the pin table is empty — every row below would pass vacuously"
    for name, (quote, _producer) in _PINNED.items():
        assert isinstance(quote, str) and quote.strip(), f"{name} is not a usable copy literal"
    for relative, declared in _DOC_QUOTES.items():
        assert declared <= set(_PINNED), f"{relative} declares a string this pin does not hold"


def test_the_derived_rows_are_really_derived() -> None:
    """The two rows nothing hand-types: the log anchor and the policy code."""
    assert _LOG_ANCHOR == "[HRESULT ", f"the failure-log format moved — the anchor now reads {_LOG_ANCHOR!r}"
    assert _LOG_ANCHOR in windows._FAIL_LOG_FORMAT
    assert _POLICY_CODE == "0x80070520", f"HR_NO_SUCH_LOGON_SESSION now formats as {_POLICY_CODE}"


def test_the_scope_words_are_read_from_the_constant_not_retyped() -> None:
    """The anti-drift property for the two ``--diagnose`` scope words.

    Pinning them against themselves would be vacuous — the real risk is that the report keeps
    saying one thing while the constant this file imports says another, which happens the moment
    someone hand-types the word back into ``_profile_lines``. So the pin is that each literal
    appears EXACTLY ONCE in the module's source: at its own definition. A second occurrence means
    a copy exists that this test does not govern.
    """
    source = (_REPO_ROOT / "src" / "utils" / "diagnostics.py").read_text(encoding="utf-8")
    for word in (SCOPE_SHARED, SCOPE_PER_USER):
        assert source.count(f'"{word}"') == 1, (
            f"{word!r} is spelled more than once in diagnostics.py — the report and this pin can now disagree"
        )


def test_the_machine_scope_lead_really_leads_both_forms() -> None:
    """The doc quotes a PREFIX, so the prefix has to be one the app always renders. Both
    sentences are built from the constant, and this is the twin that proves the build survived
    an edit — a lead that stopped being a prefix would leave the guide describing a line no
    install shows."""
    from src.ui_flet.home_status import MACHINE_SCOPE_LINE_PLAIN, MACHINE_SCOPE_LINE_WITH_PROVENANCE

    assert MACHINE_SCOPE_LINE_WITH_PROVENANCE.startswith(MACHINE_SCOPE_LINE_LEAD)
    assert MACHINE_SCOPE_LINE_PLAIN.startswith(MACHINE_SCOPE_LINE_LEAD)


@pytest.mark.parametrize("name", sorted(_PINNED))
def test_every_pinned_quote_is_still_what_the_app_says(name: str) -> None:
    """The half that stops this file becoming a second copy of the copy: each quote must
    appear in the classifier output that produces it."""
    quote, producer = _PINNED[name]
    if producer is None:
        return  # a module constant / derived value — covered by the derived-rows test above
    assert quote in producer, f"{name} is no longer part of what the app says — it reads {producer!r}"


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_every_declared_quote_is_verbatim(relative: str) -> None:
    """Reword a classifier branch without changing the docs and this goes red."""
    text = _doc_text(relative)
    for name in sorted(_DOC_QUOTES[relative]):
        quote = _PINNED[name][0]
        assert quote in text, f"{relative} no longer quotes {name} verbatim — it should read {quote!r}"


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_an_undeclared_quote_is_absent(relative: str) -> None:
    """The other direction: a doc may not quote copy it has not registered here."""
    text = _doc_text(relative)
    for name in sorted(set(_PINNED) - _DOC_QUOTES[relative]):
        quote = _PINNED[name][0]
        assert quote not in text, f"{relative} quotes {name} but does not declare it — add it to _DOC_QUOTES"


@pytest.mark.parametrize("phrase", _RETIRED_PHRASES)
def test_the_retired_coaching_is_gone_from_the_partner_page(phrase: str) -> None:
    """AC A2.7. The app stopped saying these; the page a district reads must have too."""
    assert phrase not in _doc_text(_TROUBLESHOOTING), (
        f"docs/partner/troubleshooting.md still coaches {phrase!r} — retired at plan 0041 S1b / 0046 A7"
    )
