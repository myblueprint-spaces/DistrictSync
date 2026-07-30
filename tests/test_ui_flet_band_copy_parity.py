"""The welcome-band literals are copied into docs — this ties them back (0038 S6).

CLAUDE.md's testing conventions: *any literal copied out of ``src/`` into a standalone
script/CI/doc needs a parity test tying it back*. Three durable docs quote the band lines
word-for-word, and nothing connected them to the module — which is exactly how this slice
inherited FOUR stale quotes of a string a blocker had deleted (CLAUDE.md, PRODUCT.md,
PRODUCT_SPEC.md's machine-readable AC block, and the QA checklist), every one of them
green the whole time.

Prior art for the shape: ``tests/test_ui_flet_pin.py`` reads ``docs/FLET_1.0_CONVENTIONS.md``.

Pinned in BOTH directions, per the declared-gap discipline in
``docs/claugentic-standards/CANDIDATES.md``:

* every constant a doc is DECLARED to quote must be present in it verbatim — so editing
  the constant and not the doc is red;
* every constant a doc is declared NOT to quote must be ABSENT from it — so quoting a new
  variant without registering it here is also red, instead of silently unpinned.

The one declared gap is deliberate and reviewed: ``WELCOME_RESUME_PLAIN`` is not in the QA
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

# The four band constants, read off the MODULE rather than re-typed here — a hand-copied
# list in the test that exists to stop hand-copying would be the same defect one level up.
_BAND_CONSTANTS: dict[str, str] = {
    name: value for name, value in vars(home_status).items() if name.startswith("WELCOME_") and isinstance(value, str)
}

# doc -> the constants that doc is DECLARED to quote. Anything not listed must be absent.
_DOC_QUOTES: dict[str, frozenset[str]] = {
    "docs/claugentic-PRODUCT.md": frozenset(
        {"WELCOME_FRESH", "WELCOME_RESUME_WITH_HISTORY", "WELCOME_RESUME_SETTINGS_ONLY", "WELCOME_RESUME_PLAIN"}
    ),
    "docs/claugentic-PRODUCT_SPEC.md": frozenset(
        {"WELCOME_FRESH", "WELCOME_RESUME_WITH_HISTORY", "WELCOME_RESUME_SETTINGS_ONLY", "WELCOME_RESUME_PLAIN"}
    ),
    "docs/developer/qa-checklist.md": frozenset(
        {"WELCOME_FRESH", "WELCOME_RESUME_WITH_HISTORY", "WELCOME_RESUME_SETTINGS_ONLY"}
    ),
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
