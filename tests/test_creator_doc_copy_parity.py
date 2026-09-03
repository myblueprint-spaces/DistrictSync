"""The self-service creator's copy is quoted in four docs — this ties it back (plan 0044 S7).

CLAUDE.md's testing conventions: *any literal copied out of ``src/`` into a standalone
script/CI/doc needs a parity test tying it back*. Plan 0044 put nine authored button
labels and step titles into the QA checklist, PRODUCT_SPEC (prose AND its machine-readable
AC block) and the partner installation guide, where a release-day tester and a district
admin are told to look for them word-for-word. Nothing connected them to the modules —
which is exactly how brief 0037's S6 inherited FOUR stale doc quotes of a string a blocker
had already deleted, every one of them green the whole time.

Shaped after ``tests/test_ui_flet_band_copy_parity.py`` and pinned in BOTH directions, per
the declared-gap discipline in ``docs/claugentic-standards/CANDIDATES.md``:

* every constant a doc is DECLARED to quote must be present in it verbatim — so renaming a
  button and not touching the docs is red;
* every constant a doc is declared NOT to quote must be ABSENT from it — so quoting a label
  in a new doc without registering it here is also red, instead of silently unpinned.

Plus **two DERIVED rows** rather than hand-listed ones, because a hand-copied list inside the
test that exists to stop hand-copying would be the same defect one level up:

* the overlay FILENAME shape both `installation.md` and `adding-district.md` promise a district
  (``sd<number>custom_mapping.yaml``) is derived from ``authoring.overlay_path`` itself;
* the emitted ROOT KEY table in `adding-district.md` is compared, IN ORDER, against
  ``authoring._ROOT_KEY_ORDER`` — that section's whole job is to freeze the shape a vendor
  tests base changes against, so a key list that drifted from the emitter would be worse
  than no list at all.

And one **cheap structural row**: the QA checklist's prose check-count against its own table
(ROADMAP gate item (3) — the count had drifted before).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config.authoring import _ROOT_KEY_ORDER, overlay_path
from src.ui_flet import mapping_catalog
from src.ui_flet.screens import creator, mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The pinned constants, read off their MODULES rather than re-typed here. Hand-listed by
# NAME (not harvested by prefix) because these nine span three modules and two prefix
# families — but every VALUE comes from the module, which is the half that matters.
_COPY_CONSTANTS: dict[str, str] = {
    "creator.CREATOR_ENTRY_LABEL": creator.CREATOR_ENTRY_LABEL,
    "creator.FILES_STEP_TITLE": creator.FILES_STEP_TITLE,
    "creator.FILES_SAVE_LABEL": creator.FILES_SAVE_LABEL,
    "creator.GATE_RUN_LABEL": creator.GATE_RUN_LABEL,
    "creator.GATE_CONFIRM_LABEL": creator.GATE_CONFIRM_LABEL,
    "mapping.MAPPING_CREATE_LABEL": mapping.MAPPING_CREATE_LABEL,
    "mapping.MAPPING_EDIT_LABEL": mapping.MAPPING_EDIT_LABEL,
    "mapping.MAPPING_EXPORT_LABEL": mapping.MAPPING_EXPORT_LABEL,
    "mapping_catalog.CUSTOM_ORIGIN_LABEL": mapping_catalog.CUSTOM_ORIGIN_LABEL,
}

_QA_ALL = frozenset(
    {
        "creator.CREATOR_ENTRY_LABEL",
        "creator.FILES_STEP_TITLE",
        "creator.FILES_SAVE_LABEL",
        "creator.GATE_RUN_LABEL",
        "creator.GATE_CONFIRM_LABEL",
        "mapping.MAPPING_CREATE_LABEL",
        "mapping.MAPPING_EDIT_LABEL",
        "mapping_catalog.CUSTOM_ORIGIN_LABEL",
    }
)

#: Quoted by the QA CHECKLIST alone (row 8g). PRODUCT_SPEC's AC block describes the export
#: reveal functionally, so the string must be ABSENT there — which the undeclared-quote
#: direction below is what enforces.
_QA_ONLY = frozenset({"mapping.MAPPING_EXPORT_LABEL"})

# doc -> the constants that doc is DECLARED to quote. Anything not listed must be absent.
# The empty declarations are deliberate and load-bearing: they are what makes a NEW quote
# in a doc that has none today go red instead of arriving unpinned.
_DOC_QUOTES: dict[str, frozenset[str]] = {
    "docs/developer/qa-checklist.md": _QA_ALL | _QA_ONLY,
    "docs/claugentic-PRODUCT_SPEC.md": _QA_ALL,
    "docs/partner/installation.md": frozenset({"creator.CREATOR_ENTRY_LABEL"}),
    "docs/developer/adding-district.md": frozenset(),
    "docs/claugentic-PRODUCT.md": frozenset(),
    "CLAUDE.md": frozenset(),
}

_QA_CHECKLIST = "docs/developer/qa-checklist.md"
_VENDOR_DOC = "docs/developer/adding-district.md"
_PARTNER_DOC = "docs/partner/installation.md"

#: The prose check-count words the checklist has used. Written out because the doc says
#: "thirty-four checks", not "34" — the count is prose, and prose is what drifts.
_COUNT_WORDS: dict[str, int] = {
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
    "thirty-one": 31,
    "thirty-two": 32,
    "thirty-three": 33,
    "thirty-four": 34,
    "thirty-five": 35,
    "thirty-six": 36,
    "thirty-seven": 37,
    "thirty-eight": 38,
    "thirty-nine": 39,
    "forty": 40,
}


def _doc_text(relative: str) -> str:
    path = _REPO_ROOT / relative
    assert path.is_file(), f"{relative} is missing — this pin is watching a file that moved"
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text of one ``##`` section, heading included, up to the next ``##``."""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return heading + (rest if end == -1 else rest[:end])


def test_the_constant_set_is_the_one_this_pin_was_written_for() -> None:
    """Non-vacuity: every declared name must resolve to a non-empty string.

    A renamed or emptied constant would silently drop out of the tables below and make
    every row underneath assert nothing at all — the failure this row exists to make loud
    and singular.
    """
    for name, value in _COPY_CONSTANTS.items():
        assert isinstance(value, str) and value.strip(), f"{name} is not a usable copy literal"
    for relative, declared in _DOC_QUOTES.items():
        assert declared <= set(_COPY_CONSTANTS), f"{relative} declares a constant this pin does not hold"


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_every_declared_quote_is_verbatim(relative: str) -> None:
    """Change a creator/Mapping label without changing the docs and this goes red."""
    text = _doc_text(relative)
    for name in sorted(_DOC_QUOTES[relative]):
        assert _COPY_CONSTANTS[name] in text, (
            f"{relative} no longer quotes {name} verbatim — it should read {_COPY_CONSTANTS[name]!r}"
        )


@pytest.mark.parametrize("relative", sorted(_DOC_QUOTES))
def test_an_undeclared_quote_is_absent(relative: str) -> None:
    """The other direction: a doc may not quote a label it has not registered here."""
    text = _doc_text(relative)
    for name in sorted(set(_COPY_CONSTANTS) - _DOC_QUOTES[relative]):
        assert _COPY_CONSTANTS[name] not in text, (
            f"{relative} quotes {name} but does not declare it — add it to _DOC_QUOTES so it is pinned"
        )


# ---------------------------------------------------------------------------
# Derived rows — the shape claims, taken from the module rather than restated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "placeholder"),
    [(_PARTNER_DOC, "<number>"), (_VENDOR_DOC, "<num>")],
)
def test_the_overlay_filename_shape_the_docs_promise_is_the_real_one(relative: str, placeholder: str) -> None:
    """Both docs tell a district which file to look for. DERIVED from ``overlay_path``.

    The real name for a known id is turned into the doc's placeholder form, so renaming
    the emitted file (or the ``sd<num>custom`` namespace) goes red in the two places a
    district and a support engineer are told to look.
    """
    real = overlay_path("sd93custom").name
    assert real == "sd93custom_mapping.yaml", "the id→filename rule moved; both docs need re-reading"
    claimed = real.replace("93", placeholder)
    assert claimed in _doc_text(relative), f"{relative} no longer promises {claimed!r}"


def test_the_frozen_root_key_table_matches_the_emitter_in_order() -> None:
    """`adding-district.md`'s key table IS the frozen shape — pin it to the emitter.

    Compared IN ORDER against ``authoring._ROOT_KEY_ORDER``: emission order is content
    (identity first, mechanical overrides after), and a vendor reading a stale list to
    decide whether a base change is safe is worse off than one reading no list.
    """
    section = _section(_doc_text(_VENDOR_DOC), "## Self-service overlays (plan 0044)")
    rows = [match.group(1) for match in (re.match(r"^\| `([^`]+)` \|", line) for line in section.splitlines()) if match]
    assert rows == list(_ROOT_KEY_ORDER), (
        f"the frozen key table reads {rows} but authoring._ROOT_KEY_ORDER emits {list(_ROOT_KEY_ORDER)}"
    )


#: The two keys an overlay must NEVER emit (both inherited through ``_base``).
_NEVER_EMITTED_KEYS = ("version", "sis")


def test_the_absent_keys_are_named_as_absent() -> None:
    """The other half of the frozen shape: what an overlay must NEVER emit.

    Derived the only way it can be — from the emitter's own key order, which must not
    contain either — so a future emission of ``version:`` makes both this and the doc's
    claim fail together rather than leaving the doc quietly wrong.
    """
    section = _section(_doc_text(_VENDOR_DOC), "## Self-service overlays (plan 0044)")
    for key in _NEVER_EMITTED_KEYS:
        assert key not in _ROOT_KEY_ORDER, f"{key!r} is emitted now — adding-district.md says it never is"
        assert f"**`{key}:`**" in section, f"the vendor doc no longer names `{key}:` as deliberately absent"


# ---------------------------------------------------------------------------
# The QA checklist's own count (ROADMAP gate item (3))
# ---------------------------------------------------------------------------


def test_the_qa_checklist_prose_count_matches_its_table() -> None:
    """ "These thirty-four checks" must equal the number of table rows.

    The count drifted before (rows added, prose left alone), and it is the first thing a
    release-day tester reads to know whether they have the whole pass.
    """
    text = _doc_text(_QA_CHECKLIST)
    match = re.search(r"These ([a-z-]+) checks", text)
    assert match, "the checklist no longer states its own check count in the opening sentence"
    word = match.group(1)
    assert word in _COUNT_WORDS, f"unrecognised count word {word!r} — add it to _COUNT_WORDS"

    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and not line.startswith("| # ") and not line.startswith("|---")
    ]
    assert len(rows) == _COUNT_WORDS[word], (
        f"the checklist says {word} ({_COUNT_WORDS[word]}) checks but its table has {len(rows)} rows"
    )
