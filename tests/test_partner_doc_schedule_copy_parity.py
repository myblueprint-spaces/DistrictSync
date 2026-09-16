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
from src.ui_flet.setup_errors import classify_schedule_error

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TROUBLESHOOTING = "docs/partner/troubleshooting.md"

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
}

#: doc -> the strings that doc is DECLARED to quote. Anything not listed must be ABSENT.
#: The empty declarations are deliberate and load-bearing: they are what makes a NEW quote in
#: a doc that has none today go red instead of arriving unpinned.
_DOC_QUOTES: dict[str, frozenset[str]] = {
    _TROUBLESHOOTING: frozenset(_PINNED),
    # The harness docs describe the log line's shape, so they legitimately carry the anchor.
    "CLAUDE.md": frozenset({"log_anchor"}),
    "docs/claugentic-ARCHITECTURE_TREE.md": frozenset({"log_anchor"}),
    "docs/partner/installation.md": frozenset(),
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
