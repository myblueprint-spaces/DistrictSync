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
    MSG_ACCOUNT_NOT_RECOGNIZED,
    MSG_COM_UNAVAILABLE,
    MSG_LOGON_FAILURE,
    MSG_NO_LOGON_SESSION,
    PrincipalKind,
    format_hresult,
)
from src.ui_flet.home_status import MACHINE_SCOPE_LINE_LEAD, machine_scope_line
from src.ui_flet.setup_errors import classify_schedule_error
from src.ui_flet.setup_flow import GMSA_IT_DOC_TITLE, GMSA_PREREQUISITES
from src.utils.diagnostics import SCOPE_PER_USER, SCOPE_SHARED

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TROUBLESHOOTING = "docs/partner/troubleshooting.md"
_HEADLESS = "docs/partner/headless-sftp-setup.md"
_INSTALLATION = "docs/partner/installation.md"
#: Plan 0049 S-4's hand-to-IT page. It exists so a district can send ONE page to whoever
#: administers their directory instead of reconstructing three prerequisites from a UI
#: checklist — which makes it a doc whose copy has to stay tied to the app's.
_GMSA = "docs/partner/managed-service-accounts.md"

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
        classify_schedule_error(MSG_LOGON_FAILURE, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    "elevated_refusal_headline": (
        "Something on this computer is likely blocking the schedule change even after the "
        "permission prompt was approved",
        classify_schedule_error(
            windows._MSG_ELEVATED_ACCESS_DENIED, False, account_is_current=True, kind=PrincipalKind.PASSWORD
        ),
    ),
    "account_info_headline": (
        "Windows is missing the nightly task's own saved account details",
        classify_schedule_error(MSG_ACCOUNT_INFO_NOT_SET, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    "com_unavailable_headline": (
        "This copy of DistrictSync can't reach Windows Task Scheduler",
        classify_schedule_error(MSG_COM_UNAVAILABLE, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    "policy_headline": (
        "Windows would not save the password for the nightly task",
        classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    "policy_setting_name": (
        "Network access: Do not allow storage of passwords and credentials for network authentication",
        classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    "policy_code": (
        _POLICY_CODE,
        classify_schedule_error(MSG_NO_LOGON_SESSION, False, account_is_current=True, kind=PrincipalKind.PASSWORD),
    ),
    # The doc must quote what the ADMIN SEES, not the engine constant that selects it:
    # `windows._MSG_DIFFERENT_ACCOUNT` is internal and never rendered, so pinning it tied the
    # doc to a string the classifier could stop producing without this test noticing. Pinning
    # the shipped lead WITH its producer is what makes the row bidirectional.
    "different_account_lead": (
        "The elevated step couldn't read the request DistrictSync prepared under your account",
        classify_schedule_error(
            windows._MSG_DIFFERENT_ACCOUNT, False, account_is_current=True, kind=PrincipalKind.PASSWORD
        ),
    ),
    "log_anchor": (_LOG_ANCHOR, None),
    # Plan 0049 S-4. The managed-service-account arm of MSG_ACCOUNT_NOT_RECOGNIZED — a FORK of
    # an existing branch rather than a new canonical, because 0x80070534 is where a mistyped
    # gMSA name already lands (measured) and the rest of that failure taxonomy is unmeasured.
    # Pinned on the LEAD, not on the checklist: the three prerequisites are proved separately
    # (see ``test_the_it_prerequisites_are_the_apps_own_list``) because one of them contains a
    # phrase ``_RETIRED_PHRASES`` still — correctly — bans from the failure page.
    "msa_headline": (
        "Windows would not schedule the task as that managed service account",
        classify_schedule_error(
            MSG_ACCOUNT_NOT_RECOGNIZED,
            False,
            account_is_current=False,
            kind=PrincipalKind.MANAGED_SERVICE_ACCOUNT,
        ),
    ),
    # The hand-to-IT page's own title, which the Settings disclosure tells an admin to ask for
    # by name. A document nobody can find under the name the app gave them is worse than no
    # reference at all, so the title is a shipped string and is pinned like one.
    "gmsa_doc_title": (GMSA_IT_DOC_TITLE, None),
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
    # Plan 0049 S-4: the hand-to-IT page opens with its own title, names the failure an admin
    # will see, and explains the storage policy that pushes a district here in the first place
    # — so it legitimately carries the policy family and the MSA lead, and nothing else.
    _GMSA: frozenset({"gmsa_doc_title", "msa_headline", "policy_headline", "policy_setting_name", "policy_code"}),
    # The release notes announce the line by name (but not the terminal report's two scope
    # words). Plan 0049 S-4 adds two: the entry names the hand-to-IT page so a district can
    # ask for it, and quotes the policy CODE so an admin can recognise their own situation
    # from the release notes. It still carries no failure sentence.
    "CHANGELOG.md": frozenset({"machine_scope_lead", "gmsa_doc_title", "policy_code"}),
    "docs/partner/faq.md": frozenset(),
    "docs/partner/help-centre-myedbc-districtsync-guide.md": frozenset(),
}

#: Retired with the PowerShell-era classifier branch (plan 0041 S1b) and with plan 0046's A7
#: finding that the coaching is wrong against a service account. AC A2.7: none may survive in
#: the partner page. Fixed literals, not constants — which is exactly why the doc had to be
#: checked by hand until now.
#:
#: **Plan 0049 S-4 narrowed one of them, and deliberately did not weaken this sweep.**
#: ``setup_flow.GMSA_PREREQUISITES`` now spells "Log on as a batch job" as a PREREQUISITE an
#: IT team must satisfy for a managed service account — a different claim from the retired
#: coaching, which told an admin on the PASSWORD path to go and grant it as if that were the
#: fix. The failure page still may not carry it: the prerequisites are single-sourced in the
#: Settings disclosure and in ``_GMSA``, and the failure page links to that page instead. So
#: the phrase stays banned here and the list stays proved THERE
#: (``test_the_it_prerequisites_are_the_apps_own_list``).
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


def test_the_it_prerequisites_are_the_apps_own_list() -> None:
    """The hand-to-IT page's three prerequisites must be the app's own, verbatim (plan 0049 S-4).

    They cannot ride ``_PINNED``: that table is declared present in ``_TROUBLESHOOTING``, and
    one of the three contains a phrase ``_RETIRED_PHRASES`` correctly bans from the failure
    page. So the list is proved against its own doc instead — and against the SOURCE constant,
    never a second copy, because an admin holding this page has to be reading the same three
    things the Settings disclosure shows and the failure message lists.
    """
    assert len(GMSA_PREREQUISITES) == 3, "the prerequisite list changed shape — the doc needs re-reading"
    text = _doc_text(_GMSA)
    for item in GMSA_PREREQUISITES:
        assert item in text, f"{_GMSA} no longer states the prerequisite verbatim — it should read {item!r}"


def test_the_untested_hedge_survives_in_both_docs() -> None:
    """The honesty constraint, pinned. Nothing may claim gMSA works, so both pages that offer
    it have to say it has not been tested — and a reword that quietly drops the hedge is the
    one change here that would mislead a district into treating it as a known fix."""
    for relative in (_GMSA, _TROUBLESHOOTING):
        assert "untested" in _doc_text(relative) or "not been tested" in _doc_text(relative), (
            f"{relative} no longer hedges the gMSA option"
        )
    assert "untested" in _doc_text("CHANGELOG.md"), "the release notes no longer say 'untested'"


@pytest.mark.parametrize("phrase", _RETIRED_PHRASES)
def test_the_retired_coaching_is_gone_from_the_partner_page(phrase: str) -> None:
    """AC A2.7. The app stopped saying these; the page a district reads must have too."""
    assert phrase not in _doc_text(_TROUBLESHOOTING), (
        f"docs/partner/troubleshooting.md still coaches {phrase!r} — retired at plan 0041 S1b / 0046 A7"
    )
