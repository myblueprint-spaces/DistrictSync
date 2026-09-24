"""``docs/developer/failure-policy.md`` ↔ code parity (plan 0053 P14; arrives in S2).

A table in the standard that mirrors a code constant is pinned here, so the doc cannot
drift from the code (or the code from the doc) without a red test that names the section:

* §3 (the ``criticality`` table) == :data:`src.etl.outcomes.ENTITY_CRITICALITY`, row for
  row, including the ``depends_on`` column == :data:`src.etl.outcomes.DEPENDS_ON`;
* §6 (the ``vocabularies`` table) == the members of every closed enum it lists
  (``RunErrorCategory``, ``OutcomeKind``, ``OutcomeReason``) — name AND persisted value.

Each pin has a non-vacuity assertion (the parser really found the rows) and a
doctored-doc negative twin (an edited copy of the real doc turns it red). Tables are found
by their ``<!-- failure-policy-table: <name> -->`` markers, never by line number.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest

from src.etl.errors import RunErrorCategory
from src.etl.outcomes import DEPENDS_ON, ENTITY_CRITICALITY, OutcomeKind, OutcomeReason

_DOC = Path(__file__).resolve().parents[1] / "docs" / "developer" / "failure-policy.md"

#: The enums §6's vocabulary table must list completely (a new member without a row is RED).
_VOCABULARY_ENUMS: dict[str, type[StrEnum]] = {
    "RunErrorCategory": RunErrorCategory,
    "OutcomeKind": OutcomeKind,
    "OutcomeReason": OutcomeReason,
}

_NO_DEPENDENCY = {"—", "-", ""}


def _doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _table(text: str, name: str) -> list[dict[str, str]]:
    """The rows of the markdown table directly under ``<!-- failure-policy-table: name -->``."""
    marker = f"<!-- failure-policy-table: {name} -->"
    if marker not in text:
        return []
    lines = text.split(marker, 1)[1].lstrip("\n").splitlines()
    table_lines: list[str] = []
    for line in lines:
        if not line.startswith("|"):
            break
        table_lines.append(line)
    if len(table_lines) < 2:
        return []

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    header = cells(table_lines[0])
    return [dict(zip(header, cells(line), strict=False)) for line in table_lines[2:]]


def _unticked(cell: str) -> str:
    return cell.replace("`", "").strip()


def _criticality_mismatches(text: str) -> list[str]:
    rows = _table(text, "criticality")
    problems: list[str] = []
    documented: dict[str, str] = {}
    for row in rows:
        entity = _unticked(row.get("entity", ""))
        if entity in documented:
            problems.append(f"failure-policy.md §3: {entity} has two rows")
        documented[entity] = _unticked(row.get("criticality", ""))
        doc_deps = {_unticked(d) for d in row.get("depends_on", "").split(",") if _unticked(d) not in _NO_DEPENDENCY}
        code_deps = set(DEPENDS_ON.get(entity, frozenset()))
        if doc_deps != code_deps:
            problems.append(
                f"failure-policy.md §3: {entity} depends_on is {sorted(doc_deps)} in the doc "
                f"but {sorted(code_deps)} in outcomes.DEPENDS_ON"
            )
    code = {entity: criticality.name for entity, criticality in ENTITY_CRITICALITY.items()}
    for entity in sorted(set(code) - set(documented)):
        problems.append(f"failure-policy.md §3: no row for {entity} (outcomes.ENTITY_CRITICALITY lists it)")
    for entity in sorted(set(documented) - set(code)):
        problems.append(f"failure-policy.md §3: row for {entity}, which outcomes.ENTITY_CRITICALITY does not list")
    for entity in sorted(set(code) & set(documented)):
        if documented[entity] != code[entity]:
            problems.append(
                f"failure-policy.md §3: {entity} is {documented[entity]} in the doc but {code[entity]} in the code"
            )
    for entity in sorted(set(DEPENDS_ON) - set(documented)):
        problems.append(f"failure-policy.md §3: outcomes.DEPENDS_ON has {entity}, which has no row")
    return problems


def _vocabulary_mismatches(text: str) -> list[str]:
    rows = _table(text, "vocabularies")
    documented = {
        (_unticked(r.get("enum", "")), _unticked(r.get("member", "")), _unticked(r.get("value", ""))) for r in rows
    }
    listed = {enum for enum, _member, _value in documented}
    code = {(name, member.name, member.value) for name, enum in _VOCABULARY_ENUMS.items() for member in enum}
    problems = [
        f"failure-policy.md §6: no row for {enum}.{member} = {value!r}"
        for enum, member, value in sorted(code - documented)
    ]
    problems += [
        f"failure-policy.md §6: row {enum}.{member} = {value!r} matches no enum member"
        for enum, member, value in sorted(documented - code)
        if enum in _VOCABULARY_ENUMS
    ]
    problems += [
        f"failure-policy.md §6: table lists {enum}, which this test does not pin — add it to _VOCABULARY_ENUMS"
        for enum in sorted(listed - set(_VOCABULARY_ENUMS))
    ]
    return problems


# --------------------------------------------------------------------------- #
# §3 — entity criticality                                                      #
# --------------------------------------------------------------------------- #
class TestSection3Criticality:
    def test_the_table_equals_the_code(self):
        assert _criticality_mismatches(_doc_text()) == []

    def test_non_vacuity_the_parser_found_every_row(self):
        rows = _table(_doc_text(), "criticality")
        assert len(rows) >= 8, "failure-policy.md §3: the criticality table parsed to fewer than 8 rows"
        assert {_unticked(r["entity"]) for r in rows} == set(ENTITY_CRITICALITY)
        # The depends_on column is really read: at least one row carries two dependencies.
        assert any("," in r["depends_on"] for r in rows)

    def test_doctored_a_flipped_criticality_is_red(self):
        doctored = _doc_text().replace("| Family | ISOLATABLE |", "| Family | CRITICAL |", 1)
        assert doctored != _doc_text(), "the doctoring must actually change the doc"
        assert _criticality_mismatches(doctored) == [
            "failure-policy.md §3: Family is CRITICAL in the doc but ISOLATABLE in the code"
        ]

    def test_doctored_a_missing_row_is_red(self):
        text = _doc_text()
        row = next(line for line in text.splitlines() if line.startswith("| StudentAttendance |"))
        doctored = text.replace(row + "\n", "", 1)
        assert _criticality_mismatches(doctored) == [
            "failure-policy.md §3: no row for StudentAttendance (outcomes.ENTITY_CRITICALITY lists it)"
        ]

    def test_doctored_a_changed_dependency_is_red(self):
        text = _doc_text()
        row = next(line for line in text.splitlines() if line.startswith("| Enrollments |"))
        doctored = text.replace(row, row.replace("| Classes, Students |", "| Students |"), 1)
        assert doctored != text
        assert _criticality_mismatches(doctored) == [
            "failure-policy.md §3: Enrollments depends_on is ['Students'] in the doc but "
            "['Classes', 'Students'] in outcomes.DEPENDS_ON"
        ]

    def test_doctored_a_missing_marker_is_red(self):
        doctored = _doc_text().replace("<!-- failure-policy-table: criticality -->", "")
        problems = _criticality_mismatches(doctored)
        for entity in ENTITY_CRITICALITY:
            assert f"failure-policy.md §3: no row for {entity} (outcomes.ENTITY_CRITICALITY lists it)" in problems


# --------------------------------------------------------------------------- #
# §6 — closed vocabularies                                                     #
# --------------------------------------------------------------------------- #
class TestSection6Vocabularies:
    def test_every_enum_member_has_exactly_its_row(self):
        assert _vocabulary_mismatches(_doc_text()) == []

    def test_non_vacuity_the_parser_found_every_row(self):
        rows = _table(_doc_text(), "vocabularies")
        expected = sum(len(enum) for enum in _VOCABULARY_ENUMS.values())
        assert expected >= 8
        assert len(rows) == expected, f"failure-policy.md §6: parsed {len(rows)} rows, expected {expected}"

    @pytest.mark.parametrize(
        ("old", "new", "expected"),
        [
            (
                "| OutcomeReason | RUN_ABORTED | `run_aborted` |",
                "| OutcomeReason | RUN_ABORTED | `aborted` |",
                [
                    "failure-policy.md §6: no row for OutcomeReason.RUN_ABORTED = 'run_aborted'",
                    "failure-policy.md §6: row OutcomeReason.RUN_ABORTED = 'aborted' matches no enum member",
                ],
            ),
            (
                "| OutcomeKind | NOT_RUN | `not_run` |",
                "| OutcomeKnd | NOT_RUN | `not_run` |",
                [
                    "failure-policy.md §6: no row for OutcomeKind.NOT_RUN = 'not_run'",
                    "failure-policy.md §6: table lists OutcomeKnd, which this test does not pin — "
                    "add it to _VOCABULARY_ENUMS",
                ],
            ),
        ],
        ids=["wrong-value", "unknown-enum"],
    )
    def test_doctored_a_drifted_row_is_red(self, old, new, expected):
        text = _doc_text()
        assert old in text, "the doctoring must target a real row"
        assert _vocabulary_mismatches(text.replace(old, new, 1)) == expected

    def test_doctored_a_missing_row_is_red(self):
        text = _doc_text()
        row = next(line for line in text.splitlines() if line.startswith("| RunErrorCategory | INPUT_UNREADABLE |"))
        assert _vocabulary_mismatches(text.replace(row + "\n", "", 1)) == [
            "failure-policy.md §6: no row for RunErrorCategory.INPUT_UNREADABLE = 'input_unreadable'"
        ]
