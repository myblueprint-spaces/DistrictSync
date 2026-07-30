"""The drift gate between the published output contract and its in-test mirror.

``docs/developer/output-contract.md`` is the MAINTAINED authority for what
DistrictSync emits; ``tests/contract_schema.py`` is the machine-readable mirror
the suite enforces. Both are hand-maintained — there is no generator — so this
module is what stops them diverging silently. Without it, the doc could say one
thing while every test happily enforced another, which is the worst possible
failure for a document whose whole value is being trustworthy.

Three tables in the doc are machine-read, each behind an explicit
``<!-- contract-table: <id> -->`` anchor so parsing is exact rather than
heuristic (a heading rename can't silently detach a table from its gate):

* ``<!-- contract-table: <Entity> -->`` (one per entity) — the ``Column`` cells,
  IN ORDER, must equal ``OUTPUT_SCHEMA[entity]``. Both directions are closed: an
  entity in the schema with no anchored table fails, and an anchored table for an
  entity absent from the schema fails.
* ``<!-- contract-table: bom-matrix -->`` — the per-entity encoding must agree
  with ``NO_BOM_ENTITIES`` (which ``test_contract`` separately ties to
  ``DataLoader.csv_encoding``, the code SSOT — so doc, contract data and writer
  are pinned in one chain).
* ``<!-- contract-table: expected-outputs -->`` — the "CSVs the contract sweep
  asserts" column must equal ``EXPECTED_ENTITIES``, and the "Entities enabled"
  column must equal each config's real ``active_entities()``.

A failure here means the doc and the enforced contract disagree. Fix whichever is
wrong — but note that changing the ENFORCED side is a partner-visible contract
change and hits the re-confirmation trigger named in the doc's Trust chain.

This module is deliberately free of pipeline fixtures: it reads a file and a
constants module, nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.config.loader import load_config
from tests.contract_schema import EXPECTED_ENTITIES, NO_BOM_ENTITIES, ORDER_AUTHORITY, OUTPUT_SCHEMA

DOC_PATH = Path(__file__).resolve().parents[1] / ORDER_AUTHORITY

_ANCHOR_RE = re.compile(r"<!--\s*contract-table:\s*(?P<id>[A-Za-z0-9_-]+)\s*-->")

#: Any dated confirmation stamp, ANYWHERE in the doc — not just inside an
#: anchored table. The table-scoped gate below cannot see a stamp added to the
#: front-matter or to a prose section, which is precisely how a doc-wide stamp
#: would come back.
_CONFIRMED_STAMP_RE = re.compile(r"confirmed \d{4}-\d{2}-\d{2}")

#: The literal sentence that makes the no-doc-wide-stamp policy visible to a
#: reader (front-matter status row).
_NO_DOC_WIDE_STAMP_SENTENCE = "there is no doc-wide confirmation stamp"

#: Today's stamp population, pinned by count and ENUMERATED here so a bump has to
#: name what it added:
#:   1. the legend row that DEFINES the value
#:   2. Students.EnrollStatus        (owner, 2026-07-27)
#:   3. Students.SchoolCode          (owner, 2026-07-27)
#:   4. attendance date format — the ISO default row      (owner, 2026-07-30, Q1a)
#:   5. attendance date format — the base config comment  (owner, 2026-07-30, Q1a)
#: Rows 4-5 sit in the attendance-knob table, which is NOT one of the anchored
#: per-entity tables, so ``test_the_confirmed_stamp_appears_on_exactly_the_two_owner_confirmed_columns``
#: (which parses only those) stays at the two Students columns. This count is the
#: doc-WIDE sweep and is the only place rows 4-5 are visible — raise it only with
#: an owner confirmation behind it, never to make an edit pass.
_EXPECTED_STAMP_COUNT = 5

#: The open owner questions, held VERBATIM. Marker-only matching (e.g.
#: `"**Q1 — " in text`) let the question BODY be rewritten while the test stayed
#: green — the exact failure "verbatim" exists to prevent.
#:
#: **Q1 was RETIRED on 2026-07-30 and replaced by Q1b.** The original asked two
#: things at once — date format and category vocabulary. The owner settled the
#: date half (Q1a: ISO is REQUIRED; `dd-MMM-yyyy` is NOT accepted), so the rows it
#: governed moved to `confirmed`/`REFUTED` and the question narrowed to the half
#: still open. Retiring it here rather than leaving Q1_TEXT unmatched is the
#: deliberate retirement this test's failure message asks for.
Q1B_TEXT = (
    "**Q1b — attendance category vocabulary: does the live importer IGNORE an unaccepted code, or "
    "REJECT the file?**\n"
    "> The published Docs list the categories `A`, `AD`, `A-E`, `A-E OffSite`, `AL`, `AL-E`, `L`, `L AUTH`, "
    "`L-E`. DistrictSync DERIVES only `A`, `A-E`, `L`, `L-E` for the K-7 daily band — that vocabulary is "
    "ours to promise — and PASSES THROUGH the district's own codes unfiltered for the 8-12 period band, "
    "including values the Docs never list (`OffSite`, `ISS`, …). That pass-through rests on an "
    "understanding recorded 2026-06-19 and never confirmed: that SpacesEDU ignores non-accepted codes "
    "rather than rejecting the file. Which of the Docs' values does the live importer actually accept "
    "today, and what does it do with one it does not — skip the row, or refuse the whole feed?"
)
Q2_TEXT = (
    "**Q2 — CourseInfo/StudentCourses header spellings: which spelling does the live importer canonically "
    "expect?**\n"
    "> The SD22 sample shows `CourseCode` / `SchoolID` / `Integration Id` where DistrictSync emits "
    "`Course Code` / `School ID` / `IntegrationId`. Are both accepted by the live importer, and if so which "
    "is canonical — i.e. should DistrictSync switch, or is our spelling the one to document as canonical in "
    "the internal spec?"
)
Q3_TEXT = (
    "**Q3 — line-ending tolerance: we emit CRLF on Windows and LF on the Mac/Linux artifacts — does the "
    "importer care?**\n"
    "> If it does not, we can leave `os.linesep` and record an accepted divergence. If it does, "
    "`DataLoader._write_csv` needs a `lineterminator` pin, which changes emitted bytes on two of the three "
    "build platforms and therefore needs its own snapshot-gated slice."
)

#: Q1 and Q2 each appear twice (inline beside the rows they govern, and again in
#: the collected "Open owner questions" section); Q3 appears once (the
#: line-endings section links to it rather than restating it). Pinning the COUNT
#: as well as the text stops a silent de-duplication that would strip a question
#: from the section a reader actually reaches.
_EXPECTED_QUESTION_COUNTS = {"Q1b": (Q1B_TEXT, 2), "Q2": (Q2_TEXT, 2), "Q3": (Q3_TEXT, 1)}

#: The RETIRED question text. Q1a was answered, so the two-part Q1 must not still
#: be posed anywhere — a doc that keeps asking a question the owner has settled
#: sends the next reader to re-litigate it. Pinned as an ABSENCE, with the
#: presence pin above as its positive twin (if the section were deleted wholesale,
#: this test would pass vacuously while the Q1b count test went red).
Q1_RETIRED_TEXT = (
    "**Q1 — attendance category vocabulary + date format: what is the live importer's verdict per value?**"
)


def _doc_text() -> str:
    assert DOC_PATH.is_file(), (
        f"The output contract doc is missing at {DOC_PATH}. It is the authority this suite mirrors "
        f"({ORDER_AUTHORITY}); deleting it does not relax the contract, it removes its provenance."
    )
    return DOC_PATH.read_text(encoding="utf-8")


def _clean_cell(cell: str) -> str:
    """Strip markdown emphasis/code fences so a bolded cell reads as its value."""
    return cell.replace("**", "").replace("`", "").strip()


def _table_after(text: str, anchor_id: str) -> list[dict[str, str]]:
    """Return the first markdown table following ``<!-- contract-table: id -->``.

    Rows come back as header->cell dicts with markdown emphasis stripped. A row
    whose cell count disagrees with the header fails loud rather than silently
    mis-aligning columns (a mis-aligned parse would compare the wrong cells and
    could green a real drift).
    """
    match = _ANCHOR_RE.search(text, 0)
    while match is not None and match.group("id") != anchor_id:
        match = _ANCHOR_RE.search(text, match.end())
    assert match is not None, (
        f"No '<!-- contract-table: {anchor_id} -->' anchor in {ORDER_AUTHORITY}. "
        f"The anchor is what binds that table to this gate — removing it disables the check."
    )

    lines = text[match.end() :].splitlines()
    block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            block.append(stripped)
        elif block:
            break  # table ended
        # else: blank/prose between the anchor and its table — keep looking
    assert len(block) >= 3, f"Anchor '{anchor_id}' in {ORDER_AUTHORITY} is not followed by a markdown table."

    def cells(row: str) -> list[str]:
        return [_clean_cell(c) for c in row.strip().strip("|").split("|")]

    header = cells(block[0])
    rows: list[dict[str, str]] = []
    for raw in block[2:]:  # block[1] is the |---|---| separator
        values = cells(raw)
        assert len(values) == len(header), (
            f"Table '{anchor_id}' in {ORDER_AUTHORITY} has a row with {len(values)} cells but "
            f"{len(header)} headers: {raw}"
        )
        rows.append(dict(zip(header, values)))
    return rows


def _anchored_ids(text: str) -> set[str]:
    return {m.group("id") for m in _ANCHOR_RE.finditer(text)}


def _ordered_anchor_ids(text: str) -> list[str]:
    """Anchor ids in DOCUMENT order, duplicates included."""
    return [m.group("id") for m in _ANCHOR_RE.finditer(text)]


#: Every anchored table, in the order the document must present them: the BOM
#: matrix, then the 8 entities in EMITTED order (the base myedbc `mappings` key
#: order), then the per-config expected-outputs table.
EXPECTED_ANCHOR_ORDER = [
    "bom-matrix",
    "Students",
    "Staff",
    "Family",
    "Classes",
    "Enrollments",
    "CourseInfo",
    "StudentCourses",
    "StudentAttendance",
    "expected-outputs",
]


def _split_entities(cell: str) -> set[str]:
    return {part.strip() for part in cell.split(",") if part.strip()}


# ---------------------------------------------------------------------------
# Per-entity column tables
# ---------------------------------------------------------------------------


def test_doc_documents_exactly_the_contract_entities():
    """Every entity in the contract has a documented column table, and vice versa.

    Closes both directions: an entity added to ``OUTPUT_SCHEMA`` without a doc
    section would ship undocumented, and a doc section for an entity we no longer
    emit would mislead the partner.
    """
    documented = _anchored_ids(_doc_text()) - {"expected-outputs", "bom-matrix"}
    assert documented == set(OUTPUT_SCHEMA), (
        f"{ORDER_AUTHORITY} documents entities {sorted(documented)}, the contract has "
        f"{sorted(OUTPUT_SCHEMA)} (undocumented: {sorted(set(OUTPUT_SCHEMA) - documented) or 'none'}; "
        f"documented but not emitted: {sorted(documented - set(OUTPUT_SCHEMA)) or 'none'})"
    )


def test_anchors_are_unique_and_in_the_documented_section_order():
    """The anchor list, in document order, is exactly ``EXPECTED_ANCHOR_ORDER``.

    ONE assertion closing two holes at once:

    * **Duplicates** — ``_table_after`` takes the FIRST matching anchor, so a
      second ``<!-- contract-table: Students -->`` (an easy copy-paste) would
      leave a whole table completely ungated while everything stayed green.
    * **Section order** — the doc claims its entities are "in emitted order".
      Nothing else checks that claim: the per-entity tests are parametrized by
      entity, so reordering the doc's SECTIONS could not turn any of them red.
    """
    actual = _ordered_anchor_ids(_doc_text())
    assert actual == EXPECTED_ANCHOR_ORDER, (
        f"{ORDER_AUTHORITY} anchor ids in document order are {actual}, expected "
        f"{EXPECTED_ANCHOR_ORDER}. A DUPLICATE id silently un-gates a table (the parser takes "
        f"the first match); a reordering breaks the doc's own 'in emitted order' claim."
    )


@pytest.mark.parametrize("entity", sorted(OUTPUT_SCHEMA))
def test_doc_column_table_matches_the_contract_in_order(entity):
    """The doc's per-entity column table equals ``OUTPUT_SCHEMA[entity]``, IN ORDER.

    Order is the point: emitted columns are positional, so a doc that lists the
    right columns in the wrong order documents a file we do not produce.
    """
    rows = _table_after(_doc_text(), entity)
    documented = [row["Column"] for row in rows]
    assert documented == OUTPUT_SCHEMA[entity], (
        f"{ORDER_AUTHORITY} -> '{entity}' column table has drifted from the enforced contract.\n"
        f"  doc:      {documented}\n"
        f"  contract: {OUTPUT_SCHEMA[entity]}\n"
        f"  Fix whichever is wrong — but changing the CONTRACT side is a partner-visible change "
        f"requiring importer re-confirmation (see the doc's Trust chain), not a test edit."
    )


def test_doc_column_tables_number_their_rows_consistently():
    """The doc's '#' column is 1..N — a hand-written table's cheapest self-check.

    Catches a duplicated or dropped row that happens to leave the column NAMES in
    a valid order (e.g. a copy-paste that repeats a row and shifts every number).
    """
    text = _doc_text()
    for entity in sorted(OUTPUT_SCHEMA):
        numbers = [row["#"] for row in _table_after(text, entity)]
        expected = [str(i) for i in range(1, len(OUTPUT_SCHEMA[entity]) + 1)]
        assert numbers == expected, f"{ORDER_AUTHORITY} -> '{entity}' rows are numbered {numbers}, expected {expected}"


def test_every_column_row_in_the_anchored_tables_carries_a_status_and_a_basis():
    """No verdict row may ship without provenance.

    SCOPE, stated honestly: this checks the rows **within the eight anchored
    per-entity tables**. Verdict-shaped rows elsewhere in the doc (the delivery
    envelope, the BOM matrix, the quoting table, the attendance knob table) are
    not parsed here — the anchored tables are the machine-read surface.

    The doc's premise is that a reader can tell a confirmed claim from an
    unconfirmed one. A blank Status or Basis cell silently reintroduces the
    doc-wide-stamp problem this format exists to prevent.
    """
    text = _doc_text()
    blanks: list[str] = []
    for entity in sorted(OUTPUT_SCHEMA):
        for row in _table_after(text, entity):
            if not row["Status"] or not row["Basis"]:
                blanks.append(f"{entity}.{row['Column']} (status={row['Status']!r}, basis={row['Basis']!r})")
    assert not blanks, f"{ORDER_AUTHORITY} has verdict rows missing Status or Basis: {blanks}"


def test_the_confirmed_stamp_appears_on_exactly_the_two_owner_confirmed_columns():
    """Confirmation is PER ROW. Only the two owner-confirmed columns may claim it.

    SCOPE: the eight anchored per-entity tables. The doc-WIDE half of this policy
    is enforced by ``test_no_dated_confirmation_stamp_escapes_the_two_owner_rows``
    below — this test alone cannot see a stamp added outside a table.
    """
    text = _doc_text()
    confirmed: set[str] = set()
    for entity in sorted(OUTPUT_SCHEMA):
        for row in _table_after(text, entity):
            if row["Status"].startswith("confirmed"):
                confirmed.add(f"{entity}.{row['Column']}")
    assert confirmed == {"Students.EnrollStatus", "Students.SchoolCode"}, (
        f"Columns marked confirmed-against-the-live-importer are {sorted(confirmed)}; only "
        f"Students.EnrollStatus and Students.SchoolCode were confirmed (2026-07-27). "
        f"A new confirmation needs an owner check against the live importer, not a doc edit."
    )


def test_no_dated_confirmation_stamp_escapes_the_two_owner_rows():
    """A dated stamp may not appear ANYWHERE outside the legend + the two rows.

    The table-scoped test above parses only the anchored tables, so re-stamping
    the FRONT MATTER — the exact shape of the doc-wide stamp plan 0038 flag 13
    removed — sailed straight through it. This is the text-level sweep that
    closes that hole: the policy sentence must still be stated, and the total
    population of dated stamps must stay at exactly the legend row that defines
    the value plus the two owner-confirmed Students rows.
    """
    text = _doc_text()
    assert _NO_DOC_WIDE_STAMP_SENTENCE in text, (
        f"{ORDER_AUTHORITY} no longer states {_NO_DOC_WIDE_STAMP_SENTENCE!r} in its front matter. "
        f"That sentence IS the policy — removing it is how a doc-wide stamp comes back."
    )
    stamps = _CONFIRMED_STAMP_RE.findall(text)
    assert len(stamps) == _EXPECTED_STAMP_COUNT, (
        f"{ORDER_AUTHORITY} carries {len(stamps)} dated 'confirmed <date>' stamps, expected "
        f"{_EXPECTED_STAMP_COUNT} (the Status-legend row + Students.EnrollStatus + "
        f"Students.SchoolCode). A stamp outside those three places is a doc-wide or "
        f"unearned confirmation — it needs an owner check against the live importer, not a "
        f"doc edit. If a genuinely new row was confirmed, raise this count deliberately."
    )


@pytest.mark.parametrize("label", sorted(_EXPECTED_QUESTION_COUNTS))
def test_the_open_owner_questions_are_stated_verbatim(label):
    """Q1/Q2/Q3 ship VERBATIM — full text, and the right number of times.

    Matching on a short marker (``"**Q1 — "``) proved worthless: the question
    BODY could be rewritten, narrowed or gutted with the test still green. The
    full text is held as a module constant and the occurrence count is pinned, so
    both a reword and a silent de-duplication go red.

    These questions are the doc's open dependencies — deleting or softening one
    without answering it would quietly convert a declared pending row into an
    unexplained one.
    """
    question, expected_count = _EXPECTED_QUESTION_COUNTS[label]
    actual = _doc_text().count(question)
    assert actual == expected_count, (
        f"{ORDER_AUTHORITY} states owner question {label} verbatim {actual} time(s), expected "
        f"{expected_count}. The question text is pinned in this module — if {label} was ANSWERED, "
        f"retire it deliberately (update the rows it governs, then this constant); if it was "
        f"reworded, the doc and this pin have diverged."
    )


def test_the_answered_question_is_no_longer_posed():
    """Q1a was settled (owner, 2026-07-30), so the two-part Q1 must not survive.

    A doc that keeps asking a question its owner has answered sends the next
    reader to re-litigate a closed decision — the same defect class as a stale
    `pending` row, pointing the other way. The positive twin is the Q1b count
    pin above: delete the section wholesale and this test still passes, but that
    one goes red.
    """
    assert Q1_RETIRED_TEXT not in _doc_text(), (
        f"{ORDER_AUTHORITY} still poses the retired two-part Q1. Its date half was answered on "
        f"2026-07-30 (ISO is REQUIRED; `dd-MMM-yyyy` is NOT accepted) and the open half is now Q1b. "
        f"If Q1a has been REOPENED, restore the question and move its rows back to pending — but do "
        f"not leave both the answer and the question standing."
    )


# ---------------------------------------------------------------------------
# BOM matrix
# ---------------------------------------------------------------------------


def test_doc_bom_matrix_matches_the_contract():
    """The doc's BOM matrix agrees with ``NO_BOM_ENTITIES`` for every entity.

    ``test_contract.test_loader_encoding_policy_matches_the_contract`` ties
    ``NO_BOM_ENTITIES`` to ``DataLoader.csv_encoding`` (the code SSOT), so
    together the two tests pin doc -> contract data -> writer.
    """
    rows = _table_after(_doc_text(), "bom-matrix")
    documented: dict[str, str] = {}
    for row in rows:
        # Cells read "utf-8-sig (BOM)" / "utf-8 (NO BOM)" once emphasis and code
        # fences are stripped — the encoding is the first token, the rest is prose.
        tokens = row["Encoding"].split()
        assert tokens, f"{ORDER_AUTHORITY} -> BOM matrix row {row['Entity']!r} has an empty Encoding cell"
        documented[row["Entity"]] = tokens[0]

    expected = {entity: ("utf-8" if entity in NO_BOM_ENTITIES else "utf-8-sig") for entity in OUTPUT_SCHEMA}
    assert documented == expected, (
        f"{ORDER_AUTHORITY} -> BOM matrix disagrees with the contract.\n"
        f"  doc:      {documented}\n"
        f"  contract: {expected}\n"
        f"  The per-entity BOM rule is a named re-confirmation trigger — a change needs importer "
        f"confirmation before merge."
    )


# ---------------------------------------------------------------------------
# Expected-outputs table
# ---------------------------------------------------------------------------


def _expected_outputs_rows() -> dict[str, dict[str, str]]:
    rows = _table_after(_doc_text(), "expected-outputs")
    by_config = {row["Config"]: row for row in rows}
    assert len(by_config) == len(rows), f"{ORDER_AUTHORITY} -> expected-outputs table lists a config twice"
    return by_config


def test_expected_outputs_table_covers_every_bundled_config():
    """The doc's table and ``EXPECTED_ENTITIES`` cover exactly the same configs.

    A newly bundled config lands in the published doc by turning this red, never
    by being silently undocumented.
    """
    documented = set(_expected_outputs_rows())
    assert documented == set(EXPECTED_ENTITIES), (
        f"{ORDER_AUTHORITY} -> expected-outputs documents {sorted(documented)}, the contract table "
        f"covers {sorted(EXPECTED_ENTITIES)}"
    )


@pytest.mark.parametrize("sis", sorted(EXPECTED_ENTITIES))
def test_expected_outputs_table_matches_the_contract_table(sis):
    """The doc's asserted-CSVs cell equals ``EXPECTED_ENTITIES[sis]``.

    Compared as SETS: the cell's left-to-right order is presentation (files land
    in a folder, not a sequence), whereas COLUMN order within a file is contract
    and is pinned separately above.
    """
    cell = _expected_outputs_rows()[sis]["CSVs the contract sweep asserts"]
    documented = _split_entities(cell)
    assert documented == set(EXPECTED_ENTITIES[sis]), (
        f"{ORDER_AUTHORITY} -> expected-outputs['{sis}'] says {sorted(documented)}, the contract "
        f"table says {sorted(EXPECTED_ENTITIES[sis])}"
    )


@pytest.mark.parametrize("sis", sorted(EXPECTED_ENTITIES))
def test_expected_outputs_table_enabled_column_matches_the_real_config(sis):
    """The doc's 'Entities enabled' cell equals the config's real ``active_entities()``.

    This is the column that makes the sd51myedbc row honest — the config enables
    StudentAttendance while the contract sweep asserts only the five rostering
    CSVs (its fixture withholds the absence GDEs on purpose). Gating the two
    columns against two different sources is what keeps that distinction from
    collapsing into a comfortable half-truth.
    """
    cell = _expected_outputs_rows()[sis]["Entities enabled"]
    documented = _split_entities(cell)
    active = set(load_config(sis).active_entities())
    assert documented == active, (
        f"{ORDER_AUTHORITY} -> expected-outputs['{sis}'] enabled column says {sorted(documented)}, "
        f"the config enables {sorted(active)}"
    )
