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


def test_every_documented_column_row_carries_a_status_and_a_basis():
    """No verdict row may ship without provenance.

    The doc's entire premise is that a reader can tell a confirmed claim from an
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


def test_the_doc_carries_no_doc_wide_confirmation_stamp():
    """Confirmation is PER ROW. Only the two owner-confirmed columns may claim it.

    Plan 0038 flag 13 replaced the brief's doc-wide "confirmed against the live
    importer" stamp with per-row provenance; this pins that the confirmed marker
    cannot spread beyond the rows the owner actually confirmed
    (``Students.EnrollStatus`` and ``Students.SchoolCode``, 2026-07-27).
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


def test_the_three_open_owner_questions_are_stated_verbatim():
    """Q1/Q2/Q3 ship verbatim in the doc — they are the doc's open dependencies.

    Deleting a question without answering it would quietly convert a declared
    pending row into an unexplained one.
    """
    text = _doc_text()
    for marker in ("**Q1 — ", "**Q2 — ", "**Q3 — "):
        assert marker in text, f"{ORDER_AUTHORITY} no longer states the owner question {marker.strip('* —')}"


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
