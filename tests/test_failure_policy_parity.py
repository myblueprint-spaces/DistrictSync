"""``docs/developer/failure-policy.md`` ↔ code parity (plan 0053 P14; arrives in S2).

A table in the standard that mirrors a code constant is pinned here, so the doc cannot
drift from the code (or the code from the doc) without a red test that names the section:

* §3 (the ``criticality`` table) == :data:`src.etl.outcomes.ENTITY_CRITICALITY`, row for
  row, including the ``depends_on`` column == :data:`src.etl.outcomes.DEPENDS_ON`;
* §6 (the ``vocabularies`` table) == the members of every closed enum it lists
  (``RunErrorCategory``, ``OutcomeKind``, ``OutcomeReason``) — name AND persisted value;
* §6's rule "every closed-enum member maps to copy, a verdict and a row here" (plan 0053
  S3): every documented ``RunErrorCategory`` except ``NONE`` has a
  ``failure_copy.FAILED_CATEGORY_COPY`` entry, every documented ``OutcomeKind`` an
  ``OUTCOME_TIER`` verdict for each of its valid reasons, and every documented ``OutcomeReason`` an
  ``outcome_sentence``;
* §7 (plan 0053 S8, owner decision D5): the ``may-be-empty`` table == ``outcomes.MAY_BE_EMPTY``,
  and the ``outcome-tier`` table == ``failure_copy.OUTCOME_TIER`` over every valid (kind, reason)
  for a ``MAY_BE_EMPTY`` member and for any other entity;
* §3 / P13 ↔ ``docs/partner/faq.md`` (plan 0053 S4): the FAQ's two criticality bullets name
  exactly the CRITICAL and ISOLATABLE sets (by ``failure_copy.entity_phrase``), and the
  ISOLATABLE bullet carries the "pending confirmation" clause exactly while
  ``output-contract.md`` says ``Q5-status: open``.

Each pin has a non-vacuity assertion (the parser really found the rows) and a
doctored-doc negative twin (an edited copy of the real doc turns it red). Tables are found
by their ``<!-- failure-policy-table: <name> -->`` markers, never by line number.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import pytest

from src.etl.errors import RunErrorCategory
from src.etl.outcomes import (
    DEPENDS_ON,
    ENTITY_CRITICALITY,
    MAY_BE_EMPTY,
    VALID_REASONS,
    EntityCriticality,
    OutcomeKind,
    OutcomeReason,
)
from src.ui_flet.failure_copy import FAILED_CATEGORY_COPY, OUTCOME_TIER, entity_phrase
from tests.test_output_contract_doc import _Q5_STATUS_RE  # the ONE spelling of the status line

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


# --------------------------------------------------------------------------- #
# §6 ↔ copy — every documented member is worded (plan 0053 S3)                 #
# --------------------------------------------------------------------------- #
def _copy_gaps(text: str, *, category_copy, tier, reasons_with_copy) -> list[str]:
    """Documented §6 members the copy layer cannot word (empty = every member is covered)."""
    problems: list[str] = []
    for row in _table(text, "vocabularies"):
        enum, member = _unticked(row.get("enum", "")), _unticked(row.get("member", ""))
        if enum == "RunErrorCategory" and member != "NONE" and RunErrorCategory[member] not in category_copy:
            problems.append(f"failure-policy.md §6: RunErrorCategory.{member} has no FAILED_CATEGORY_COPY entry")
        if enum == "OutcomeKind" and not _has_tier(tier, OutcomeKind[member]):
            problems.append(f"failure-policy.md §6: OutcomeKind.{member} has no OUTCOME_TIER verdict")
        if enum == "OutcomeReason" and OutcomeReason[member] not in reasons_with_copy:
            problems.append(f"failure-policy.md §6: OutcomeReason.{member} has no outcome_sentence")
    return problems


def _has_tier(tier, kind: OutcomeKind) -> bool:
    """Whether ``tier`` (``OUTCOME_TIER``'s shape) answers a Verdict for ``kind`` × every valid reason."""
    try:
        return all(tier("Family", kind, reason) is not None for reason in VALID_REASONS[kind])
    except (KeyError, ValueError):
        return False


def _reasons_with_copy() -> set[OutcomeReason]:
    from src.ui_flet import failure_copy

    return {reason for (_kind, reason) in failure_copy._OUTCOME_TEMPLATES}


class TestSection6CopyParity:
    def test_every_documented_member_is_worded(self):
        assert (
            _copy_gaps(
                _doc_text(),
                category_copy=FAILED_CATEGORY_COPY,
                tier=OUTCOME_TIER,
                reasons_with_copy=_reasons_with_copy(),
            )
            == []
        )

    def test_non_vacuity_the_rows_checked_are_the_real_ones(self):
        rows = _table(_doc_text(), "vocabularies")
        documented = {_unticked(r["member"]) for r in rows if _unticked(r["enum"]) == "RunErrorCategory"}
        assert documented == {m.name for m in RunErrorCategory}
        assert _reasons_with_copy() == {r for reasons in VALID_REASONS.values() for r in reasons}

    def test_doctored_a_category_without_copy_is_red(self):
        missing = {k: v for k, v in FAILED_CATEGORY_COPY.items() if k is not RunErrorCategory.SOURCE_SCHEMA}
        assert _copy_gaps(
            _doc_text(), category_copy=missing, tier=OUTCOME_TIER, reasons_with_copy=_reasons_with_copy()
        ) == ["failure-policy.md §6: RunErrorCategory.SOURCE_SCHEMA has no FAILED_CATEGORY_COPY entry"]

    def test_doctored_a_kind_without_a_tier_and_a_reason_without_copy_are_red(self):
        def tier(entity, kind, reason):
            if kind is OutcomeKind.NOT_RUN:
                raise KeyError(kind)  # a tier that forgot a kind
            return OUTCOME_TIER(entity, kind, reason)

        reasons = _reasons_with_copy() - {OutcomeReason.RUN_ABORTED}
        assert _copy_gaps(_doc_text(), category_copy=FAILED_CATEGORY_COPY, tier=tier, reasons_with_copy=reasons) == [
            "failure-policy.md §6: OutcomeKind.NOT_RUN has no OUTCOME_TIER verdict",
            "failure-policy.md §6: OutcomeReason.RUN_ABORTED has no outcome_sentence",
        ]


# --------------------------------------------------------------------------- #
# §7 — which EMPTY outcomes warn (plan 0053 S8, owner decision D5)              #
# --------------------------------------------------------------------------- #
#: A registry entity that is NOT in MAY_BE_EMPTY, to evaluate the "any other entity" column.
_NON_MEMBER = "Family"


def _may_be_empty_mismatches(text: str, *, members: frozenset[str]) -> list[str]:
    documented = {_unticked(r.get("entity", "")) for r in _table(text, "may-be-empty")}
    problems = [
        f"failure-policy.md §7: no may-be-empty row for {e} (outcomes.MAY_BE_EMPTY lists it)"
        for e in sorted(members - documented)
    ]
    problems += [
        f"failure-policy.md §7: may-be-empty row for {e}, which outcomes.MAY_BE_EMPTY does not list"
        for e in sorted(documented - members)
    ]
    return problems


def _tier_mismatches(text: str, *, tier) -> list[str]:
    member = next(iter(sorted(MAY_BE_EMPTY)))
    documented: dict[tuple[str, str], tuple[str, str]] = {}
    for row in _table(text, "outcome-tier"):
        key = (_unticked(row.get("kind", "")), _unticked(row.get("reason", "")))
        documented[key] = (_unticked(row.get("MAY_BE_EMPTY entity", "")), _unticked(row.get("any other entity", "")))
    problems: list[str] = []
    for kind, reasons in VALID_REASONS.items():
        for reason in sorted(reasons):
            key = (kind.name, reason.value)
            code = (tier(member, kind, reason).name, tier(_NON_MEMBER, kind, reason).name)
            if key not in documented:
                problems.append(f"failure-policy.md §7: no outcome-tier row for {kind.name}/{reason.value}")
            elif documented[key] != code:
                problems.append(
                    f"failure-policy.md §7: {kind.name}/{reason.value} is {documented[key]} in the doc "
                    f"but {code} in failure_copy.OUTCOME_TIER"
                )
    valid = {(k.name, r.value) for k, rs in VALID_REASONS.items() for r in rs}
    problems += [
        f"failure-policy.md §7: outcome-tier row {k}/{r} is not a valid pair"
        for k, r in documented
        if (k, r) not in valid
    ]
    return problems


class TestSection7EmptyTier:
    def test_the_may_be_empty_table_equals_the_code(self):
        assert _may_be_empty_mismatches(_doc_text(), members=MAY_BE_EMPTY) == []

    def test_the_outcome_tier_table_equals_the_code(self):
        assert _tier_mismatches(_doc_text(), tier=OUTCOME_TIER) == []

    def test_non_vacuity_the_parser_found_every_row(self):
        assert {_unticked(r["entity"]) for r in _table(_doc_text(), "may-be-empty")} == set(MAY_BE_EMPTY)
        assert MAY_BE_EMPTY, "the set is not empty, so the member column is really exercised"
        assert _NON_MEMBER not in MAY_BE_EMPTY and _NON_MEMBER in ENTITY_CRITICALITY
        rows = _table(_doc_text(), "outcome-tier")
        assert len(rows) == sum(len(r) for r in VALID_REASONS.values())
        # The two columns really differ somewhere — the whole point of D5's entity split.
        assert any(r["MAY_BE_EMPTY entity"] != r["any other entity"] for r in rows)

    def test_doctored_an_extra_member_in_the_code_is_red(self):
        assert _may_be_empty_mismatches(_doc_text(), members=MAY_BE_EMPTY | {"Family"}) == [
            "failure-policy.md §7: no may-be-empty row for Family (outcomes.MAY_BE_EMPTY lists it)"
        ]

    def test_doctored_a_removed_row_is_red(self):
        text = _doc_text()
        row = next(line for line in text.splitlines() if line.startswith("| StudentAttendance | Absence files"))
        doctored = text.replace(row + "\n", "", 1)
        assert doctored != text
        assert _may_be_empty_mismatches(doctored, members=MAY_BE_EMPTY) == [
            "failure-policy.md §7: no may-be-empty row for StudentAttendance (outcomes.MAY_BE_EMPTY lists it)"
        ]

    def test_doctored_a_quieter_doc_row_is_red(self):
        doctored = _doc_text().replace(
            "| EMPTY | no_rows_after_transform | WARNING | WARNING |",
            "| EMPTY | no_rows_after_transform | HEALTHY | WARNING |",
            1,
        )
        assert doctored != _doc_text()
        assert _tier_mismatches(doctored, tier=OUTCOME_TIER) == [
            "failure-policy.md §7: EMPTY/no_rows_after_transform is ('HEALTHY', 'WARNING') in the doc "
            "but ('WARNING', 'WARNING') in failure_copy.OUTCOME_TIER"
        ]

    def test_doctored_a_quieter_code_tier_is_red(self):
        from src.ui_flet.verdict import Verdict

        def muted(entity, kind, reason):
            if kind is OutcomeKind.EMPTY:
                return Verdict.HEALTHY  # the muting the standard forbids
            return OUTCOME_TIER(entity, kind, reason)

        problems = _tier_mismatches(_doc_text(), tier=muted)
        assert (
            "failure-policy.md §7: EMPTY/missing_source_column is ('WARNING', 'WARNING') in the doc but ('HEALTHY', 'HEALTHY') in failure_copy.OUTCOME_TIER"
            in problems
        )
        assert len(problems) == 4  # every EMPTY row that warns anywhere


# --------------------------------------------------------------------------- #
# §3 / §7 P13 ↔ the partner FAQ (plan 0053 S4)                                 #
# --------------------------------------------------------------------------- #
_FAQ = Path(__file__).resolve().parents[1] / "docs" / "partner" / "faq.md"
_CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "developer" / "output-contract.md"

#: The two bullets of the FAQ's "What happens if the GDE files are not present…" answer that
#: state the criticality rule to a district, found by their LEADS (never by line number).
_FAQ_LEADS: dict[EntityCriticality, tuple[str, str]] = {
    EntityCriticality.CRITICAL: ("**If the output that can't be built is ", ", the whole run stops.**"),
    EntityCriticality.ISOLATABLE: ("**If the output that can't be built is ", ", only that output is left out.**"),
}

#: P13: while SpacesEDU has not answered Q5, the isolatable bullet must SAY the consequence
#: for earlier-linked records is unconfirmed — and must stop saying so once it is answered.
FAQ_PENDING_CLAUSE = (
    "What SpacesEDU does with records linked by an earlier delivery of that file is pending confirmation."
)


def _q5_status(contract_text: str) -> str:
    statuses = _Q5_STATUS_RE.findall(contract_text)
    return statuses[0] if len(statuses) == 1 else ""


def _faq_bullet(faq_text: str, criticality: EntityCriticality) -> str:
    """The one FAQ line carrying ``criticality``'s lead, or ``""``."""
    lead, tail = _FAQ_LEADS[criticality]
    lines = [line for line in faq_text.splitlines() if lead in line and tail in line]
    return lines[0] if len(lines) == 1 else ""


def _named_phrases(bullet: str, criticality: EntityCriticality) -> set[str]:
    lead, tail = _FAQ_LEADS[criticality]
    listed = bullet.split(lead, 1)[1].split(tail, 1)[0]
    return {part.strip() for part in re.split(r",\s*|\s+or\s+", listed) if part.strip()}


def _faq_mismatches(faq_text: str, contract_text: str) -> list[str]:
    problems: list[str] = []
    for criticality in EntityCriticality:
        bullet = _faq_bullet(faq_text, criticality)
        if not bullet:
            problems.append(f"faq.md: no single {criticality.name} bullet (lead {_FAQ_LEADS[criticality][0]!r})")
            continue
        expected = {entity_phrase(e) for e, c in ENTITY_CRITICALITY.items() if c is criticality}
        named = _named_phrases(bullet, criticality)
        if named != expected:
            problems.append(
                f"faq.md: the {criticality.name} bullet names {sorted(named)}, "
                f"but outcomes.ENTITY_CRITICALITY's {criticality.name} set reads {sorted(expected)}"
            )
    status = _q5_status(contract_text)
    isolatable = _faq_bullet(faq_text, EntityCriticality.ISOLATABLE)
    if status not in {"open", "answered"}:
        problems.append(f"output-contract.md: Q5-status is {status!r}, not open/answered")
    elif isolatable and (FAQ_PENDING_CLAUSE in isolatable) != (status == "open"):
        problems.append(
            f"faq.md: the ISOLATABLE bullet {'lacks' if status == 'open' else 'still carries'} the pending "
            f"clause while Q5-status is {status!r} (P13)"
        )
    return problems


class TestTheFaqStatesTheCriticalityRule:
    def test_the_faq_names_exactly_the_declared_sets_and_the_pending_clause(self):
        assert _faq_mismatches(_FAQ.read_text(encoding="utf-8"), _CONTRACT.read_text(encoding="utf-8")) == []

    def test_non_vacuity_both_bullets_parse_and_q5_is_open_today(self):
        faq = _FAQ.read_text(encoding="utf-8")
        assert _named_phrases(_faq_bullet(faq, EntityCriticality.ISOLATABLE), EntityCriticality.ISOLATABLE) == {
            "family contacts",
            "courses",
            "student courses",
            "attendance rows",
        }
        assert len(_named_phrases(_faq_bullet(faq, EntityCriticality.CRITICAL), EntityCriticality.CRITICAL)) == 4
        assert _q5_status(_CONTRACT.read_text(encoding="utf-8")) == "open"
        assert FAQ_PENDING_CLAUSE in _faq_bullet(faq, EntityCriticality.ISOLATABLE)

    def test_doctored_a_dropped_isolatable_entity_is_red(self):
        faq = _FAQ.read_text(encoding="utf-8")
        doctored = faq.replace(
            "family contacts, courses, student courses or attendance rows", "family contacts or courses", 1
        )
        assert doctored != faq
        assert _faq_mismatches(doctored, _CONTRACT.read_text(encoding="utf-8")) == [
            "faq.md: the ISOLATABLE bullet names ['courses', 'family contacts'], but outcomes.ENTITY_CRITICALITY's "
            "ISOLATABLE set reads ['attendance rows', 'courses', 'family contacts', 'student courses']"
        ]

    def test_doctored_a_missing_pending_clause_is_red_while_q5_is_open(self):
        faq = _FAQ.read_text(encoding="utf-8")
        doctored = faq.replace(" " + FAQ_PENDING_CLAUSE, "", 1)
        assert doctored != faq
        assert _faq_mismatches(doctored, _CONTRACT.read_text(encoding="utf-8")) == [
            "faq.md: the ISOLATABLE bullet lacks the pending clause while Q5-status is 'open' (P13)"
        ]

    def test_doctored_an_answered_q5_makes_the_pending_clause_red(self):
        contract = _CONTRACT.read_text(encoding="utf-8")
        answered = contract.replace("Q5-status: open", "Q5-status: answered", 1)
        assert answered != contract
        assert _faq_mismatches(_FAQ.read_text(encoding="utf-8"), answered) == [
            "faq.md: the ISOLATABLE bullet still carries the pending clause while Q5-status is 'answered' (P13)"
        ]

    def test_doctored_a_missing_bullet_is_red(self):
        faq = _FAQ.read_text(encoding="utf-8")
        doctored = faq.replace(", the whole run stops.**", ", everything stops.**", 1)
        assert _faq_mismatches(doctored, _CONTRACT.read_text(encoding="utf-8")) == [
            'faq.md: no single CRITICAL bullet (lead "**If the output that can\'t be built is ")'
        ]
