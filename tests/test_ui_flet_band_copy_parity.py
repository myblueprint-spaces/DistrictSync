"""``home_status``'s user-facing literals are copied into docs — this ties them back (0038 S6/S7).

CLAUDE.md's testing conventions: *any literal copied out of ``src/`` into a standalone
script/CI/doc needs a parity test tying it back*. Three durable docs quote these lines
word-for-word, and nothing connected them to the module — which is exactly how S6
inherited FOUR stale quotes of a string a blocker had deleted (CLAUDE.md, PRODUCT.md,
PRODUCT_SPEC.md's machine-readable AC block, and the QA checklist), every one of them
green the whole time.

Two families are harvested BY PREFIX (never hand-listed — a hand-copied list in the test that
exists to stop hand-copying would be the same defect one level up):

* ``WELCOME_*`` — S6's four welcome-band variants above Home's hosted wizard;
* ``EMPTY_*`` / ``SIZE_CLAUSE_*`` — S7's empty-store headlines, the shared no-automation
  sentence, and the roster-size clause's fixed opening. The empty-store pair is SHARED with
  Run History, so a doc quoting one of them is describing both surfaces at once. **They are
  not the only strings S7's AC-home rewrite quotes** — see the ``SIZE_NOUNS`` table below;
* ``QUICK_*`` — S7's quick-action strip labels. Added by S7's Stage-7 fix batch:
  ``QUICK_CONVERT_LABEL`` ("Convert now") is quoted word-for-word in FOUR places across the
  three docs below and was left un-pinned, which is an ABSORBED gap in the very pin S7
  extended — the file declares its gaps, so an undeclared one is the defect.

  **Two of the three ``QUICK_`` rows are weak by construction, and that is stated rather than
  hidden.** ``QUICK_RUN_HISTORY_LABEL`` and ``QUICK_SETTINGS_LABEL`` are the ordinary names of
  two destinations ("Run History", "Settings") and appear throughout all three docs as prose,
  so their forward assertion is close to trivially true and their reverse one has nothing to
  catch. They are harvested anyway because the alternative — hand-listing which members of a
  prefix family to pin — is the same hand-copying this file exists to end. The row that
  carries weight is ``QUICK_CONVERT_LABEL``: it is an authored button label, and renaming it
  without touching the docs is now RED.

Prior art for the shape: ``tests/test_ui_flet_pin.py`` reads ``docs/FLET_1.0_CONVENTIONS.md``.

Pinned in BOTH directions, per the declared-gap discipline in
``docs/claugentic-standards/CANDIDATES.md``:

* every constant a doc is DECLARED to quote must be present in it verbatim — so editing
  the constant and not the doc is red;
* every constant a doc is declared NOT to quote must be ABSENT from it — so quoting a new
  variant without registering it here is also red, instead of silently unpinned.

**The ``SIZE_NOUNS`` family and its DECLARED gap.** The prefix harvest above filters on
``isinstance(value, str)``, so ``SIZE_NOUNS`` — a ``dict`` of ``(singular, plural)`` tuples —
is structurally unreachable by it, while its plural forms ("students", "courses", "attendance
rows") are quoted word-for-word in all three docs, inside AC-home-1b itself and in QA rows
3a/3b/3c. That is the same absorbed-gap shape as ``QUICK_``, in the file whose own discipline
says an undeclared gap IS the defect — so the FORWARD direction is pinned below off
``SIZE_NOUNS`` itself.

Its REVERSE direction is a **declared gap, not an oversight**: these nouns are ordinary English
words that appear throughout the prose for reasons that have nothing to do with the clause
("the roster collapsed to zero students", "Convert lists every expected file"). An
"undeclared noun must be ABSENT" rule would be false for almost every entry and would pin
nothing, so it is not asserted. Renaming a noun a doc quotes is caught; *adding* a doc quote of
a noun not listed is not.

The other declared gap is deliberate and reviewed: ``WELCOME_RESUME_PLAIN`` is not in the QA
checklist. Its state needs a damaged ``history.db`` beside a blank-but-readable
``config.json``, which is not a thing a release-day tester can stage in ~30 minutes; it is
covered by AC-setup-5 (read, not walked) and by the unit rows in
``tests/test_ui_flet_home_status.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ui_flet import home_status

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The pinned constants, read off the MODULE by PREFIX rather than re-typed here.
_PINNED_PREFIXES = ("WELCOME_", "EMPTY_", "SIZE_CLAUSE_", "QUICK_")

_BAND_CONSTANTS: dict[str, str] = {
    name: value
    for name, value in vars(home_status).items()
    if name.startswith(_PINNED_PREFIXES) and isinstance(value, str)
}

_WELCOME_ALL = frozenset(
    {"WELCOME_FRESH", "WELCOME_RESUME_WITH_HISTORY", "WELCOME_RESUME_SETTINGS_ONLY", "WELCOME_RESUME_PLAIN"}
)
_S7_HEADLINES = frozenset({"EMPTY_FRESH_START_HEADLINE", "EMPTY_NO_RUNS_HEADLINE"})
_QUICK_ALL = frozenset({"QUICK_CONVERT_LABEL", "QUICK_RUN_HISTORY_LABEL", "QUICK_SETTINGS_LABEL"})

# doc -> the constants that doc is DECLARED to quote. Anything not listed must be absent.
_DOC_QUOTES: dict[str, frozenset[str]] = {
    "docs/claugentic-PRODUCT.md": _WELCOME_ALL | _S7_HEADLINES | _QUICK_ALL | frozenset({"SIZE_CLAUSE_LEAD"}),
    "docs/claugentic-PRODUCT_SPEC.md": _WELCOME_ALL | _S7_HEADLINES | _QUICK_ALL | frozenset({"SIZE_CLAUSE_LEAD"}),
    "docs/developer/qa-checklist.md": frozenset(
        {"WELCOME_FRESH", "WELCOME_RESUME_WITH_HISTORY", "WELCOME_RESUME_SETTINGS_ONLY"}
    )
    | _S7_HEADLINES
    | _QUICK_ALL
    | frozenset({"SIZE_CLAUSE_LEAD"}),
}


# doc -> the ``SIZE_NOUNS`` KEYS whose PLURAL form that doc quotes verbatim. The values are
# read off the module (never re-typed), so this table is data about the DOCS, exactly like
# ``_DOC_QUOTES``. Forward direction only — see the module docstring's declared gap.
_DOC_SIZE_NOUNS: dict[str, frozenset[str]] = {
    "docs/claugentic-PRODUCT.md": frozenset({"Students", "CourseInfo", "StudentAttendance"}),
    "docs/claugentic-PRODUCT_SPEC.md": frozenset({"Students", "CourseInfo", "StudentAttendance"}),
    "docs/developer/qa-checklist.md": frozenset({"Students", "CourseInfo", "StudentAttendance"}),
}


def _doc_text(relative: str) -> str:
    path = _REPO_ROOT / relative
    assert path.is_file(), f"{relative} is missing — this pin is watching a file that moved"
    return path.read_text(encoding="utf-8")


def test_the_constant_set_is_the_one_this_pin_was_written_for() -> None:
    """The reality-read has teeth only if it actually found the constants.

    An empty or shrunken ``_BAND_CONSTANTS`` would make every parametrized row below
    vacuous (nothing to check), and a renamed constant would silently drop out of the
    declared tables. Named here so that failure is loud and singular.
    """
    assert set(_BAND_CONSTANTS) == {
        "WELCOME_FRESH",
        "WELCOME_RESUME_WITH_HISTORY",
        "WELCOME_RESUME_SETTINGS_ONLY",
        "WELCOME_RESUME_PLAIN",
        "EMPTY_FRESH_START_HEADLINE",
        "EMPTY_NO_RUNS_HEADLINE",
        "EMPTY_NO_AUTO_SYNC_DETAIL",
        "SIZE_CLAUSE_LEAD",
        "QUICK_CONVERT_LABEL",
        "QUICK_RUN_HISTORY_LABEL",
        "QUICK_SETTINGS_LABEL",
    }
    for name, declared in _DOC_QUOTES.items():
        assert declared <= set(_BAND_CONSTANTS), f"{name} declares a constant that no longer exists"


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_every_declared_quote_is_verbatim(relative: str) -> None:
    """Change a band constant without changing the doc and this goes red."""
    text = _doc_text(relative)
    for name in sorted(_DOC_QUOTES[relative]):
        assert _BAND_CONSTANTS[name] in text, (
            f"{relative} no longer quotes {name} verbatim — it should read {_BAND_CONSTANTS[name]!r}"
        )


def test_the_size_noun_table_is_the_one_this_pin_was_written_for() -> None:
    """Non-vacuity + reality-read: every declared key must still exist in ``SIZE_NOUNS``.

    Without this a renamed/removed entity key would drop silently out of the declared table and
    the rows below would assert nothing — the same failure the constant-set row above prevents
    for the prefix families.
    """
    for relative, keys in _DOC_SIZE_NOUNS.items():
        assert keys, f"{relative} declares no size nouns — did the table lose its rows?"
        assert keys <= set(home_status.SIZE_NOUNS), f"{relative} declares a key SIZE_NOUNS no longer has"


@pytest.mark.parametrize("relative", sorted(_DOC_SIZE_NOUNS))
def test_every_declared_size_noun_is_verbatim(relative: str) -> None:
    """Rename a ``SIZE_NOUNS`` plural without changing the docs and this goes red.

    ``SIZE_NOUNS`` is a dict, so the prefix harvest cannot see it — yet AC-home-1b, the flow
    prose and three QA rows all quote its plurals word-for-word.
    """
    text = _doc_text(relative)
    for key in sorted(_DOC_SIZE_NOUNS[relative]):
        plural = home_status.SIZE_NOUNS[key][1]
        assert plural in text, f"{relative} no longer quotes SIZE_NOUNS[{key!r}] verbatim — it reads {plural!r}"


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_an_undeclared_quote_is_absent(relative: str) -> None:
    """The other direction: a doc may not quote a variant it has not registered here.

    Without this, adding a fifth variant to one doc leaves that copy unpinned — the exact
    state the whole file exists to end.
    """
    text = _doc_text(relative)
    for name in sorted(set(_BAND_CONSTANTS) - _DOC_QUOTES[relative]):
        assert _BAND_CONSTANTS[name] not in text, (
            f"{relative} quotes {name} but does not declare it — add it to _DOC_QUOTES so it is pinned"
        )
