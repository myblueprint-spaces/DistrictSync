"""Per-entity outcomes, the outcome ledger and declared criticality (plan 0053 S2).

``docs/developer/failure-policy.md`` §3 (criticality), §6 (the closed vocabularies) and §7
(one outcome per configured entity per run; additive record key; total reader). Every
refusal below has its legal twin, and every pin that asserts an absence has the positive
case that proves the mechanism fires:

* :class:`EntityOutcome` refuses every illegal state (and accepts each legal one);
* the criticality table is TOTAL over the registry and the record's count keys, unlisted
  means CRITICAL, and nothing ISOLATABLE is depended on;
* :class:`OutcomeLedger` refuses duplicates and unknown entities, completes on
  ``finalize_aborted`` and refuses an incomplete ``complete()``;
* :func:`outcomes_from_record` is TOTAL over garbage and lossless over what we write;
* ``run_transform`` records EMPTY/BUILT at its branches and FAILED + NOT_RUN on a raise,
  re-raising the SAME object;
* the new parameters are REQUIRED keyword-only with no default (signature pins);
* only CRITICAL entities' modules publish ``TransformContext`` state (AST fitness function).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.etl import outcomes, pipeline
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.outcomes import (
    DEPENDS_ON,
    ENTITY_CRITICALITY,
    OUTCOMES_RECORD_KEY,
    ROSTER_ANCHOR_ENTITY,
    VALID_REASONS,
    EntityCriticality,
    EntityOutcome,
    OutcomeKind,
    OutcomeLedger,
    OutcomeReason,
    apply_observation,
    criticality_of,
    outcomes_from_record,
    outcomes_to_record,
    reason_for,
)
from src.etl.pipeline import (
    PipelineResult,
    build_run_record,
    configured_entity_order,
    run_transform,
)
from src.etl.transformer import DataTransformer
from src.etl.transformers.registry import TRANSFORMER_REGISTRY

_REPO = Path(__file__).resolve().parents[1]
_TRANSFORMERS_DIR = _REPO / "src" / "etl" / "transformers"

_D1_ISOLATABLE = {"Family", "StudentAttendance", "CourseInfo", "StudentCourses"}
_D1_CRITICAL = {"Students", "Staff", "Classes", "Enrollments"}


# --------------------------------------------------------------------------- #
# EntityOutcome — illegal states are refused, never defaulted                   #
# --------------------------------------------------------------------------- #
class TestEntityOutcomeRefusesIllegalStates:
    @pytest.mark.parametrize(
        ("kind", "reason", "rows"),
        [
            (OutcomeKind.BUILT, OutcomeReason.NONE, 0),  # BUILT with no rows
            (OutcomeKind.BUILT, OutcomeReason.NO_ROWS_AFTER_TRANSFORM, 3),  # BUILT with a reason
            (OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY, 1),  # EMPTY with rows
            (OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR, 2),  # FAILED with rows
            (OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED, 4),  # NOT_RUN with rows
            (OutcomeKind.EMPTY, OutcomeReason.NONE, 0),  # reason not valid for the kind
            (OutcomeKind.FAILED, OutcomeReason.RUN_ABORTED, 0),
            (OutcomeKind.NOT_RUN, OutcomeReason.TRANSFORM_ERROR, 0),
            (OutcomeKind.BUILT, OutcomeReason.NONE, -1),  # negative rows
            (OutcomeKind.EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED, -1),
        ],
    )
    def test_an_illegal_state_is_refused(self, kind, reason, rows):
        with pytest.raises(ValueError):
            EntityOutcome("Family", kind, reason, rows)

    @pytest.mark.parametrize(
        ("kind", "reason", "rows"),
        [
            (OutcomeKind.BUILT, OutcomeReason.NONE, 1),
            (OutcomeKind.EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED, 0),
            (OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY, 0),
            (OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM, 0),
            (OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0),  # plan 0053 S6's refinement
            (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN, 0),
            (OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR, 0),
            (OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED, 0),
        ],
    )
    def test_the_twin_every_legal_state_constructs(self, kind, reason, rows):
        outcome = EntityOutcome("Family", kind, reason, rows)
        assert (outcome.kind, outcome.reason, outcome.rows) == (kind, reason, rows)

    def test_the_legal_table_is_exactly_the_one_the_twins_enumerate(self):
        legal = {(k, r) for k, reasons in VALID_REASONS.items() for r in reasons}
        assert set(VALID_REASONS) == set(OutcomeKind), "every kind has its reason set"
        assert {r for _, r in legal} == set(OutcomeReason), "every reason is valid for some kind"
        assert len(legal) == 8  # S6 added EMPTY/MISSING_SOURCE_COLUMN

    @pytest.mark.parametrize(
        "bad",
        [
            {"entity": "", "kind": OutcomeKind.BUILT, "reason": OutcomeReason.NONE, "rows": 1},
            {"entity": "  ", "kind": OutcomeKind.BUILT, "reason": OutcomeReason.NONE, "rows": 1},
            {"entity": 5, "kind": OutcomeKind.BUILT, "reason": OutcomeReason.NONE, "rows": 1},
            {"entity": None, "kind": OutcomeKind.BUILT, "reason": OutcomeReason.NONE, "rows": 1},
        ],
    )
    def test_a_blank_entity_is_refused(self, bad):
        with pytest.raises(ValueError):
            EntityOutcome(**bad)

    @pytest.mark.parametrize(
        ("kind", "reason", "rows"),
        [
            ("built", OutcomeReason.NONE, 1),  # a bare string is not a kind
            (OutcomeKind.BUILT, "none", 1),  # nor a reason
            (OutcomeKind.BUILT, OutcomeReason.NONE, True),  # a bool is not a count
            (OutcomeKind.BUILT, OutcomeReason.NONE, 1.0),  # nor a float
            (OutcomeKind.BUILT, OutcomeReason.NONE, "1"),  # nor a string
        ],
    )
    def test_a_wrongly_typed_field_is_refused(self, kind, reason, rows):
        with pytest.raises(TypeError):
            EntityOutcome("Family", kind, reason, rows)

    def test_it_is_frozen(self):
        outcome = EntityOutcome.built("Students", 3)
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.rows = 4  # type: ignore[misc]

    def test_the_named_constructors_build_the_legal_shapes(self):
        assert EntityOutcome.built("S", 2) == EntityOutcome("S", OutcomeKind.BUILT, OutcomeReason.NONE, 2)
        assert EntityOutcome.empty("S", OutcomeReason.SOURCE_FILES_EMPTY).rows == 0
        assert EntityOutcome.failed("S", OutcomeReason.TRANSFORM_ERROR).kind is OutcomeKind.FAILED
        assert EntityOutcome.not_run("S").reason is OutcomeReason.RUN_ABORTED


# --------------------------------------------------------------------------- #
# Criticality — declared once, total, restrictive by default                    #
# --------------------------------------------------------------------------- #
def _isolatable_dependencies(table, depends_on) -> list[str]:
    """Every ``(dependent -> dependency)`` pair whose dependency is not CRITICAL."""
    return sorted(
        f"{dependent} -> {dependency}"
        for dependent, dependencies in depends_on.items()
        for dependency in dependencies
        if table.get(dependency, EntityCriticality.CRITICAL) is not EntityCriticality.CRITICAL
    )


class TestCriticality:
    def test_the_table_is_total_over_the_registry_and_the_record_keys(self):
        assert set(ENTITY_CRITICALITY) == set(TRANSFORMER_REGISTRY) == set(pipeline._RECORD_ENTITY_KEYS)
        assert len(ENTITY_CRITICALITY) == 8  # non-vacuity: the three sets are not all empty

    def test_d1_isolatable_and_critical_sets(self):
        isolatable = {e for e, c in ENTITY_CRITICALITY.items() if c is EntityCriticality.ISOLATABLE}
        critical = {e for e, c in ENTITY_CRITICALITY.items() if c is EntityCriticality.CRITICAL}
        assert isolatable == _D1_ISOLATABLE
        assert critical == _D1_CRITICAL

    def test_an_unlisted_entity_is_critical(self):
        assert criticality_of("HandDropped") is EntityCriticality.CRITICAL
        assert criticality_of("") is EntityCriticality.CRITICAL

    def test_the_twin_a_listed_isolatable_entity_answers_isolatable(self):
        assert criticality_of("Family") is EntityCriticality.ISOLATABLE

    def test_the_roster_anchor_is_critical(self):
        assert ROSTER_ANCHOR_ENTITY == "Students"
        assert criticality_of(ROSTER_ANCHOR_ENTITY) is EntityCriticality.CRITICAL

    def test_the_anchor_moved_and_the_pipeline_uses_the_same_object(self):
        tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        assert "ROSTER_ANCHOR_ENTITY" not in assigned
        assert "_RECORD_ENTITY_KEYS" in assigned  # non-vacuity: the walk sees module assignments
        assert pipeline.ROSTER_ANCHOR_ENTITY is outcomes.ROSTER_ANCHOR_ENTITY

    def test_the_table_cannot_be_mutated(self):
        with pytest.raises(TypeError):
            ENTITY_CRITICALITY["Family"] = EntityCriticality.CRITICAL  # type: ignore[index]
        with pytest.raises(TypeError):
            DEPENDS_ON["Family"] = frozenset()  # type: ignore[index]

    def test_no_isolatable_entity_is_depended_on(self):
        assert _isolatable_dependencies(ENTITY_CRITICALITY, DEPENDS_ON) == []
        # Non-vacuity: DEPENDS_ON is not empty, so the sweep inspected real pairs.
        assert sum(len(v) for v in DEPENDS_ON.values()) >= 5

    def test_the_twin_a_dependency_on_an_isolatable_entity_is_caught(self):
        doctored = {**DEPENDS_ON, "Enrollments": frozenset({"Classes", "Family"})}
        assert _isolatable_dependencies(ENTITY_CRITICALITY, doctored) == ["Enrollments -> Family"]

    def test_depends_on_names_only_known_entities(self):
        named = set(DEPENDS_ON) | {d for deps in DEPENDS_ON.values() for d in deps}
        assert named <= set(ENTITY_CRITICALITY)

    def test_depends_on_is_the_code_dependency_set_of_the_plan(self):
        assert dict(DEPENDS_ON) == {
            "Enrollments": frozenset({"Classes", "Students"}),
            "Classes": frozenset({"Students"}),
            "Family": frozenset({"Students"}),
            "StudentCourses": frozenset({"Students"}),
        }


# --------------------------------------------------------------------------- #
# The ledger                                                                    #
# --------------------------------------------------------------------------- #
class TestOutcomeLedger:
    def test_a_duplicate_outcome_is_refused(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        ledger.record(EntityOutcome.built("Students", 2))
        with pytest.raises(ValueError, match="already has an outcome"):
            ledger.record(EntityOutcome.empty("Students", OutcomeReason.SOURCE_FILES_EMPTY))

    def test_an_unconfigured_entity_is_refused(self):
        ledger = OutcomeLedger(["Students"])
        with pytest.raises(ValueError, match="not an entity this run is configured"):
            ledger.record(EntityOutcome.built("Family", 1))

    def test_a_configured_list_with_a_duplicate_is_refused(self):
        with pytest.raises(ValueError, match="more than once"):
            OutcomeLedger(["Students", "Staff", "Students"])

    def test_complete_refuses_a_missing_outcome(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        ledger.record(EntityOutcome.built("Students", 2))
        with pytest.raises(RuntimeError, match="Staff"):
            ledger.complete()

    def test_the_twin_complete_returns_every_outcome_in_configured_order(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        ledger.record(EntityOutcome.empty("Staff", OutcomeReason.SOURCE_FILES_EMPTY))
        ledger.record(EntityOutcome.built("Students", 2))
        assert [o.entity for o in ledger.complete()] == ["Students", "Staff"]

    def test_finalize_aborted_marks_only_the_unrecorded_entities(self):
        ledger = OutcomeLedger(["Students", "Staff", "Family"])
        ledger.record(EntityOutcome.built("Students", 2))
        result = ledger.finalize_aborted()
        assert result == (
            EntityOutcome.built("Students", 2),
            EntityOutcome.not_run("Staff"),
            EntityOutcome.not_run("Family"),
        )
        assert ledger.complete() == result

    def test_finalize_aborted_is_idempotent(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        first = ledger.finalize_aborted()
        assert ledger.finalize_aborted() == first

    def test_finalize_aborted_on_an_untouched_ledger_is_never_empty(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        assert [o.kind for o in ledger.finalize_aborted()] == [OutcomeKind.NOT_RUN, OutcomeKind.NOT_RUN]

    def test_mark_not_run_refuses_an_entity_already_recorded(self):
        ledger = OutcomeLedger(["Students", "Staff"])
        ledger.record(EntityOutcome.built("Students", 1))
        with pytest.raises(ValueError):
            ledger.mark_not_run(["Students"])

    def test_outcomes_is_in_configured_order_while_incomplete(self):
        ledger = OutcomeLedger(["A", "B", "C"])
        ledger.record(EntityOutcome.built("C", 1))
        ledger.record(EntityOutcome.built("A", 1))
        assert [o.entity for o in ledger.outcomes] == ["A", "C"]


class TestReasonFor:
    def test_a_source_schema_error_is_a_missing_source_column(self):
        exc = SourceSchemaError("x", entity="Family", columns=("Parent Auth / Guardian",), guard=GuardKind.PII_SCOPE)
        assert reason_for(exc) is OutcomeReason.MISSING_SOURCE_COLUMN

    @pytest.mark.parametrize("exc", [ValueError("x"), KeyError("x"), RuntimeError("missing_source_column")])
    def test_the_twin_anything_else_is_a_transform_error_whatever_its_text(self, exc):
        assert reason_for(exc) is OutcomeReason.TRANSFORM_ERROR


# --------------------------------------------------------------------------- #
# The record key — written by one builder, read by one TOTAL reader             #
# --------------------------------------------------------------------------- #
_SAMPLE = (
    EntityOutcome.built("Students", 667),
    EntityOutcome.built("Staff", 46),
    EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN),
    EntityOutcome.not_run("Classes"),
    EntityOutcome.empty("Enrollments", OutcomeReason.NO_ROWS_AFTER_TRANSFORM),
)


class TestRecordRoundTrip:
    def test_the_stored_shape_is_plain_json(self):
        assert outcomes_to_record(_SAMPLE)["Family"] == {"kind": "failed", "reason": "missing_source_column", "rows": 0}
        assert list(outcomes_to_record(_SAMPLE)) == ["Students", "Staff", "Family", "Classes", "Enrollments"]

    def test_a_round_trip_is_lossless(self):
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: outcomes_to_record(_SAMPLE)}) == _SAMPLE

    def test_a_round_trip_through_json_is_lossless(self):
        import json

        record = json.loads(json.dumps({OUTCOMES_RECORD_KEY: outcomes_to_record(_SAMPLE)}))
        assert outcomes_from_record(record) == _SAMPLE

    def test_the_record_key_does_not_collide_with_any_flat_key(self):
        record = build_run_record(
            status="success",
            elapsed=0.0,
            entity_counts={"Students": 1},
            source="cli",
            sis_type="myedbc",
            error_category="none",
            entity_outcomes=_SAMPLE,
        )
        assert OUTCOMES_RECORD_KEY == "entity_outcomes"
        assert OUTCOMES_RECORD_KEY not in pipeline._RECORD_ENTITY_KEYS
        # The flat count keys keep their own meaning beside the new key.
        assert record["Students"] == 1 and record["Staff"] == 0
        assert record[OUTCOMES_RECORD_KEY]["Students"]["rows"] == 667

    def test_no_ledger_is_recorded_as_none_never_as_an_empty_success(self):
        record = build_run_record(
            status="failed",
            elapsed=0.0,
            entity_counts={},
            source="cli",
            sis_type="myedbc",
            error_category="config",
            entity_outcomes=None,
        )
        assert OUTCOMES_RECORD_KEY in record and record[OUTCOMES_RECORD_KEY] is None
        assert outcomes_from_record(record) == ()


class TestTheReaderIsTotal:
    @pytest.mark.parametrize(
        "record",
        [None, [], "entity_outcomes", 5, {}, {"other": 1}, {OUTCOMES_RECORD_KEY: None}],
        ids=["none", "list", "str", "int", "empty", "no-key", "null-value"],
    )
    def test_a_record_with_no_usable_outcomes_reads_empty(self, record):
        assert outcomes_from_record(record) == ()

    @pytest.mark.parametrize(
        "stored",
        [[], "built", 7, [["Students", "built"]]],
        ids=["list", "str", "int", "nested-list"],
    )
    def test_a_non_mapping_value_reads_empty(self, stored):
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == ()

    def test_a_blank_or_non_string_entity_key_is_dropped(self):
        good = {"kind": "built", "reason": "none", "rows": 1}
        stored = {"": good, "   ": good, "Students": good}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == (EntityOutcome.built("Students", 1),)
        # A non-string key can only arrive from a non-JSON source; the reader drops it too.
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: {5: good}}) == ()

    @pytest.mark.parametrize(
        "entry",
        [
            {"kind": "quarantined", "reason": "none", "rows": 0},  # a kind a newer build added
            {"kind": "failed", "reason": "vendor_rejected", "rows": 0},  # a reason a newer build added
            {"kind": None, "reason": None},
            {"rows": 3},
        ],
    )
    def test_an_unknown_kind_or_reason_reads_as_a_failure(self, entry):
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": entry}}) == (
            EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),
        )

    @pytest.mark.parametrize(
        "entry",
        [
            {"kind": "built", "reason": "none", "rows": -1},  # negative rows
            {"kind": "built", "reason": "run_aborted", "rows": 2},  # illegal combination
            {"kind": "empty", "reason": "none", "rows": 0},
            {"kind": "built", "reason": "none", "rows": True},  # a bool is not a count
            {"kind": "built", "reason": "none", "rows": "5"},
            {"kind": "failed", "reason": "transform_error"},  # rows missing
            "built",  # not a mapping
            None,
        ],
    )
    def test_a_known_but_impossible_entry_is_dropped(self, entry):
        stored = {"Family": entry, "Students": {"kind": "built", "reason": "none", "rows": 3}}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == (EntityOutcome.built("Students", 3),)

    def test_it_never_raises_over_a_garbage_sweep(self):
        garbage = [object(), float("nan"), {"a": object()}, {"kind": ["built"]}, {"kind": {}}, b"built"]
        for value in garbage:
            outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": value}})
            outcomes_from_record({OUTCOMES_RECORD_KEY: value})
            outcomes_from_record(value)


# --------------------------------------------------------------------------- #
# run_transform records at its existing branches (no behaviour change)          #
# --------------------------------------------------------------------------- #
_GC = {"academic_start_month_day": "09-01", "academic_end_month_day": "06-30"}


def _entity(source_file: str, field_map: dict) -> dict:
    return {"source_files": {"primary": source_file}, "field_map": field_map}


def _run(raw_data: dict, mappings: dict, global_config: dict = _GC):
    ledger = OutcomeLedger(configured_entity_order(mappings, global_config))
    return run_transform(raw_data, mappings, global_config, ledger=ledger), ledger


class TestRunTransformRecords:
    def test_every_branch_records_its_outcome(self, monkeypatch):
        mappings = {
            "Built": _entity("built.txt", {"Out": "in_col"}),
            "NoSources": {"source_files": {}, "field_map": {"Out": "in_col"}},
            "NoFileList": {"source_files": [], "field_map": {}},
            "EmptyFiles": _entity("missing.txt", {"Out": "in_col"}),
            "NoRows": _entity("norows.txt", {"Out": "in_col"}),
        }
        raw = {
            "built.txt": pd.DataFrame({"in_col": ["a", "b", "c"]}),
            "norows.txt": pd.DataFrame({"other": ["x"]}),
        }
        # "NoRows" has input but its transform keeps no row (as Family does when every contact
        # lacks an email): a stub scoped to that one entity returns the empty frame.
        original = DataTransformer.transform

        def _stub(self, df, mapping, entity, raw_data, global_config):
            if entity == "NoRows":
                return pd.DataFrame()
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _stub)
        result, _ledger = _run(raw, mappings)
        assert result.outcomes == (
            EntityOutcome.built("Built", 3),
            EntityOutcome.empty("NoSources", OutcomeReason.NO_SOURCE_FILES_DECLARED),
            EntityOutcome.empty("NoFileList", OutcomeReason.NO_SOURCE_FILES_DECLARED),
            EntityOutcome.empty("EmptyFiles", OutcomeReason.SOURCE_FILES_EMPTY),
            EntityOutcome.empty("NoRows", OutcomeReason.NO_ROWS_AFTER_TRANSFORM),
        )
        # BUILT rows == the rows that reached `outputs`; nothing else did.
        assert set(result.outputs) == {"Built"} and len(result.outputs["Built"]) == 3

    def test_an_entity_in_entity_order_with_no_mapping_is_recorded_not_skipped_silently(self):
        mappings = {"Built": _entity("built.txt", {"Out": "in_col"})}
        gc = {**_GC, "entity_order": ["Ghost", "Built"]}
        result, _ = _run({"built.txt": pd.DataFrame({"in_col": ["a"]})}, mappings, gc)
        assert result.outcomes[0] == EntityOutcome.empty("Ghost", OutcomeReason.NO_SOURCE_FILES_DECLARED)

    @pytest.mark.parametrize(
        ("exc", "reason"),
        [
            (
                SourceSchemaError("x", entity="Middle", columns=("col",), guard=GuardKind.PII_SCOPE),
                OutcomeReason.MISSING_SOURCE_COLUMN,
            ),
            (RuntimeError("an untyped fault"), OutcomeReason.TRANSFORM_ERROR),
        ],
    )
    def test_a_raise_records_failed_and_the_rest_not_run_then_reraises_the_same_object(self, monkeypatch, exc, reason):
        mappings = {name: _entity(f"{name}.txt", {"Out": "in_col"}) for name in ("First", "Middle", "Last")}
        raw = {f"{name}.txt": pd.DataFrame({"in_col": ["a"]}) for name in mappings}
        original = DataTransformer.transform

        def _stub(self, df, mapping, entity, raw_data, global_config):
            if entity == "Middle":
                raise exc
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _stub)
        ledger = OutcomeLedger(configured_entity_order(mappings, _GC))
        with pytest.raises(type(exc)) as raised:
            run_transform(raw, mappings, _GC, ledger=ledger)
        assert raised.value is exc, "an unlisted (so CRITICAL) entity's raise propagates the ORIGINAL object"
        assert ledger.complete() == (
            EntityOutcome.built("First", 1),
            EntityOutcome.failed("Middle", reason),
            EntityOutcome.not_run("Last"),
        )

    def test_a_base_exception_is_not_an_entity_outcome(self, monkeypatch):
        mappings = {"Only": _entity("only.txt", {"Out": "in_col"})}

        def _interrupt(*_a, **_k):
            raise KeyboardInterrupt

        monkeypatch.setattr(DataTransformer, "transform", _interrupt)
        ledger = OutcomeLedger(["Only"])
        with pytest.raises(KeyboardInterrupt):
            run_transform({"only.txt": pd.DataFrame({"in_col": ["a"]})}, mappings, _GC, ledger=ledger)
        assert ledger.outcomes == (), "BaseException passes through unrecorded"

    def test_a_ledger_built_for_another_entity_list_is_refused(self):
        mappings = {"A": _entity("a.txt", {"Out": "in_col"}), "B": _entity("b.txt", {"Out": "in_col"})}
        with pytest.raises(ValueError, match="configured_entity_order"):
            run_transform({}, mappings, _GC, ledger=OutcomeLedger(["B", "A"]))

    def test_the_twin_the_matching_ledger_runs(self):
        mappings = {"A": _entity("a.txt", {"Out": "in_col"}), "B": _entity("b.txt", {"Out": "in_col"})}
        result = run_transform({}, mappings, _GC, ledger=OutcomeLedger(["A", "B"]))
        assert [o.kind for o in result.outcomes] == [OutcomeKind.EMPTY, OutcomeKind.EMPTY]


class TestConfiguredEntityOrderIsUnique:
    def test_a_repeated_entity_appears_once_in_first_position(self):
        mappings = {"A": {}, "B": {}}
        assert configured_entity_order(mappings, {"entity_order": ["B", "A", "B"]}) == ["B", "A"]

    def test_the_twin_an_order_without_repeats_is_unchanged(self):
        mappings = {"A": {}, "B": {}, "C": {}}
        assert configured_entity_order(mappings, {"entity_order": ["C", "A", "B"]}) == ["C", "A", "B"]
        assert configured_entity_order(mappings, {"enabled_entities": ["B", "A"]}) == ["A", "B"]

    def test_a_repeated_entity_is_transformed_once(self, monkeypatch):
        mappings = {"A": _entity("a.txt", {"Out": "in_col"})}
        calls: list[str] = []
        original = DataTransformer.transform

        def _count(self, df, mapping, entity, raw_data, global_config):
            calls.append(entity)
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _count)
        result, _ = _run({"a.txt": pd.DataFrame({"in_col": ["x"]})}, mappings, {**_GC, "entity_order": ["A", "A"]})
        assert calls == ["A"]
        assert result.outcomes == (EntityOutcome.built("A", 1),)


class TestDataErrorsMark:
    def test_rollback_drops_only_what_was_recorded_after_the_mark(self):
        transformer = DataTransformer()
        transformer.data_errors.append({"entity": "Students", "field": "Grade", "failed_rows": 1})
        mark = transformer.data_errors_mark()
        transformer.data_errors.append({"entity": "Family", "field": "Email", "failed_rows": 4})
        transformer.rollback_data_errors(mark)
        assert transformer.data_errors == [{"entity": "Students", "field": "Grade", "failed_rows": 1}]

    def test_the_twin_rolling_back_to_the_current_mark_changes_nothing(self):
        transformer = DataTransformer()
        transformer.data_errors.append({"entity": "Students", "field": "Grade", "failed_rows": 1})
        transformer.rollback_data_errors(transformer.data_errors_mark())
        assert len(transformer.data_errors) == 1

    @pytest.mark.parametrize("mark", [-1, 2, True, "0", None])
    def test_a_mark_that_is_not_a_position_is_refused(self, mark):
        transformer = DataTransformer()
        transformer.data_errors.append({"entity": "Students", "field": "Grade", "failed_rows": 1})
        with pytest.raises(ValueError):
            transformer.rollback_data_errors(mark)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Required keyword-only, no default (P8)                                       #
# --------------------------------------------------------------------------- #
def _kw_only_required(func, name: str) -> None:
    param = inspect.signature(func).parameters[name]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, f"{func.__qualname__}({name}) must be keyword-only"
    assert param.default is inspect.Parameter.empty, f"{func.__qualname__}({name}) must have no default"


class TestSignatures:
    def test_run_transform_ledger(self):
        _kw_only_required(run_transform, "ledger")

    def test_build_run_record_entity_outcomes(self):
        _kw_only_required(build_run_record, "entity_outcomes")

    def test_record_manual_run_entity_outcomes(self):
        from src.ui_flet.screens.convert import _record_manual_run

        _kw_only_required(_record_manual_run, "entity_outcomes")

    @pytest.mark.parametrize("cls_path", ["pipeline", "convert"])
    def test_the_result_dataclasses_require_it_keyword_only(self, cls_path):
        from src.ui_flet.convert_result import ConvertResult, ConvertStatus

        cls = PipelineResult if cls_path == "pipeline" else ConvertResult
        field = next(f for f in dataclasses.fields(cls) if f.name == "entity_outcomes")
        assert field.kw_only is True
        assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
        with pytest.raises(TypeError, match="entity_outcomes"):
            cls() if cls is PipelineResult else cls(status=ConvertStatus.DELIVERED)  # type: ignore[call-arg]

    def test_the_twin_naming_it_constructs(self):
        from src.ui_flet.convert_result import ConvertResult, ConvertStatus

        assert PipelineResult(entity_outcomes=_SAMPLE).entity_outcomes == _SAMPLE
        assert (
            ConvertResult(
                delivery_requested=False, status=ConvertStatus.DELIVERED, entity_outcomes=None
            ).entity_outcomes
            is None
        )


# --------------------------------------------------------------------------- #
# Architecture fitness: only CRITICAL entities publish TransformContext state   #
# --------------------------------------------------------------------------- #
#: The per-run ledger every transformer APPENDS to — a method call on an existing list,
#: never a publication another entity reads.
_NOT_A_PUBLICATION = {"data_errors"}


def _context_assignments(source: str) -> list[tuple[str, int]]:
    """``(attr, line)`` for every ``context.<attr> = …`` / ``+=`` / annotated assignment.

    Only ASSIGNMENTS publish: a method CALL such as ``context.data_errors.append(...)`` or a
    future ``context.record_outcome_note(...)`` mutates state the context already owns.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for sub in ast.walk(target):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "context"
                    and sub.attr not in _NOT_A_PUBLICATION
                ):
                    found.append((sub.attr, sub.lineno))
    return found


def _module_entities() -> dict[str, set[str]]:
    """Module file name → the registry entities whose transformer class it defines."""
    owners: dict[str, set[str]] = {}
    for entity, transformer in TRANSFORMER_REGISTRY.items():
        module = sys.modules[type(transformer).__module__]
        owners.setdefault(Path(module.__file__).name, set()).add(entity)
    return owners


def _publications_outside_critical_modules(sources: dict[str, str]) -> list[str]:
    owners = _module_entities()
    offenders: list[str] = []
    for filename, source in sources.items():
        for attr, line in _context_assignments(source):
            entities = owners.get(filename, set())
            if not entities or any(criticality_of(e) is not EntityCriticality.CRITICAL for e in entities):
                offenders.append(f"{filename}:{line} context.{attr} (module entities: {sorted(entities) or 'none'})")
    return offenders


def _transformer_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_TRANSFORMERS_DIR.glob("*.py"))}


class TestOnlyCriticalEntitiesPublishContextState:
    def test_no_module_outside_a_critical_entity_publishes(self):
        assert _publications_outside_critical_modules(_transformer_sources()) == []

    def test_positive_twin_the_sweep_finds_the_two_real_publishers(self):
        sources = _transformer_sources()
        found = {
            (filename, attr) for filename, source in sources.items() for attr, _line in _context_assignments(source)
        }
        assert found == {("students.py", "active_student_ids"), ("classes.py", "class_artifacts")}

    def test_the_isolatable_modules_publish_nothing(self):
        """Verify-before-implement (plan 0053): CourseInfo, StudentCourses, StudentAttendance
        and Family publish no ``TransformContext`` state."""
        sources = _transformer_sources()
        owners = _module_entities()
        isolatable_files = {f for f, ents in owners.items() if ents & _D1_ISOLATABLE}
        assert isolatable_files == {"family.py", "course_info.py", "student_courses.py", "student_attendance.py"}
        for filename in isolatable_files:
            assert _context_assignments(sources[filename]) == [], filename

    def test_negative_twin_a_publication_in_an_isolatable_module_is_caught(self):
        doctored = {"family.py": "def transform(self, df, mapping, context):\n    context.guardian_ids = {1}\n"}
        assert _publications_outside_critical_modules(doctored) == [
            "family.py:2 context.guardian_ids (module entities: ['Family'])"
        ]

    def test_negative_twin_a_publication_in_shared_code_is_caught(self):
        doctored = {"base.py": "def helper(context):\n    context.shared += 1\n"}
        assert _publications_outside_critical_modules(doctored) == ["base.py:2 context.shared (module entities: none)"]

    def test_a_method_call_is_not_a_publication(self):
        source = "def f(context):\n    context.data_errors.append(1)\n    context.record_outcome_note('x', 1)\n"
        assert _context_assignments(source) == []


# --------------------------------------------------------------------------- #
# The source observation on the outcome (plan 0053 S6)                          #
# --------------------------------------------------------------------------- #
class TestMissingMappedOnTheOutcome:
    def test_any_kind_may_carry_it(self):
        for outcome in (
            EntityOutcome("Students", OutcomeKind.BUILT, OutcomeReason.NONE, 3, ("Next school code",)),
            EntityOutcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN, 0, ("Email Address",)),
            EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0, ("Email Address",)),
            EntityOutcome("Classes", OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED, 0, ("Course Title",)),
        ):
            assert outcome.missing_mapped

    def test_the_default_is_empty(self):
        assert EntityOutcome.built("Students", 1).missing_mapped == ()

    @pytest.mark.parametrize(
        ("value", "error"),
        [
            (["Email Address"], TypeError),  # a list, not a tuple
            (("",), ValueError),  # blank
            (("  Email Address",), ValueError),  # untrimmed
            (("Email\nAddress",), ValueError),  # not printable
            ((5,), ValueError),  # not a str
            (("Email Address", "Email Address"), ValueError),  # listed twice
        ],
    )
    def test_an_unusable_list_is_refused(self, value, error):
        with pytest.raises(error):
            EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM, 0, value)


class TestApplyObservation:
    def test_empty_no_rows_is_refined_to_missing_source_column(self):
        refined = apply_observation(
            EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM), ("Email Address",)
        )
        assert (refined.kind, refined.reason, refined.missing_mapped) == (
            OutcomeKind.EMPTY,
            OutcomeReason.MISSING_SOURCE_COLUMN,
            ("Email Address",),
        )

    def test_the_twin_nothing_observed_changes_nothing(self):
        outcome = EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM)
        assert apply_observation(outcome, ()) is outcome

    @pytest.mark.parametrize(
        "outcome",
        [
            EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN),
            EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),
            EntityOutcome.built("Family", 4),
            EntityOutcome.empty("Family", OutcomeReason.SOURCE_FILES_EMPTY),
            EntityOutcome.empty("Family", OutcomeReason.NO_SOURCE_FILES_DECLARED),
            EntityOutcome.not_run("Family"),
        ],
        ids=lambda o: f"{o.kind.value}-{o.reason.value}",
    )
    def test_every_other_kind_and_reason_is_kept_and_only_annotated(self, outcome):
        observed = apply_observation(outcome, ("Email Address",))
        assert (observed.kind, observed.reason, observed.rows) == (outcome.kind, outcome.reason, outcome.rows)
        assert observed.missing_mapped == ("Email Address",)

    def test_an_outcome_already_observed_is_not_rewritten(self):
        outcome = EntityOutcome("Family", OutcomeKind.BUILT, OutcomeReason.NONE, 1, ("First Name",))
        assert apply_observation(outcome, ("Email Address",)) is outcome


class TestLedgerHoldsTheObservation:
    def test_a_noted_observation_is_attached_when_the_outcome_is_recorded(self):
        ledger = OutcomeLedger(["Students", "Family"])
        ledger.note_missing_mapped("Family", ("Email Address",))
        ledger.record(EntityOutcome.built("Students", 2))
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        students, family = ledger.complete()
        assert students.missing_mapped == ()  # the twin: an unobserved entity is untouched
        assert (family.reason, family.missing_mapped) == (OutcomeReason.MISSING_SOURCE_COLUMN, ("Email Address",))

    def test_not_run_via_finalize_carries_it_too(self):
        ledger = OutcomeLedger(["Classes"])
        ledger.note_missing_mapped("Classes", ["Course Title"])
        (classes,) = ledger.finalize_aborted()
        assert (classes.kind, classes.missing_mapped) == (OutcomeKind.NOT_RUN, ("Course Title",))

    def test_an_empty_observation_holds_nothing(self):
        ledger = OutcomeLedger(["Family"])
        ledger.note_missing_mapped("Family", ())
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        assert ledger.complete()[0].reason is OutcomeReason.NO_ROWS_AFTER_TRANSFORM

    def test_an_unconfigured_entity_is_refused(self):
        with pytest.raises(ValueError):
            OutcomeLedger(["Students"]).note_missing_mapped("Family", ("Email Address",))

    def test_an_observation_after_the_outcome_is_refused(self):
        ledger = OutcomeLedger(["Family"])
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        with pytest.raises(ValueError):
            ledger.note_missing_mapped("Family", ("Email Address",))

    def test_a_second_observation_is_refused(self):
        ledger = OutcomeLedger(["Family"])
        ledger.note_missing_mapped("Family", ("Email Address",))
        with pytest.raises(ValueError):
            ledger.note_missing_mapped("Family", ("First Name",))

    def test_an_unusable_column_list_is_refused_and_nothing_is_held(self):
        ledger = OutcomeLedger(["Family"])
        with pytest.raises(ValueError):
            ledger.note_missing_mapped("Family", ("",))
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        assert ledger.complete()[0].missing_mapped == ()

    def test_a_bare_str_is_refused_rather_than_split_into_characters(self):
        # "Email" has no blank and no repeated letter, so split into characters it would pass
        # every other check and be held as ('E', 'm', 'a', 'i', 'l').
        ledger = OutcomeLedger(["Family"])
        with pytest.raises(TypeError, match="not a str"):
            ledger.note_missing_mapped("Family", "Email")
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        assert ledger.complete()[0].missing_mapped == ()

    def test_the_twin_a_list_of_names_is_held_as_a_tuple(self):
        ledger = OutcomeLedger(["Family"])
        ledger.note_missing_mapped("Family", ["Email"])
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        assert ledger.complete()[0].missing_mapped == ("Email",)


_OBSERVED_SAMPLE = (
    EntityOutcome("Students", OutcomeKind.BUILT, OutcomeReason.NONE, 667, ("Next school code",)),
    EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0, ("Email Address",)),
    EntityOutcome.built("Staff", 46),
)


class TestMissingMappedRoundTrip:
    def test_it_is_written_only_when_something_was_observed(self):
        stored = outcomes_to_record(_OBSERVED_SAMPLE)
        assert stored["Family"] == {
            "kind": "empty",
            "reason": "missing_source_column",
            "rows": 0,
            "missing_mapped": ["Email Address"],
        }
        # The twin: an entity with nothing observed is byte-identical to the pre-S6 entry.
        assert stored["Staff"] == {"kind": "built", "reason": "none", "rows": 46}

    def test_a_round_trip_through_json_is_lossless(self):
        import json

        record = json.loads(json.dumps({OUTCOMES_RECORD_KEY: outcomes_to_record(_OBSERVED_SAMPLE)}))
        assert outcomes_from_record(record) == _OBSERVED_SAMPLE

    @pytest.mark.parametrize(
        "garbage",
        ["Email Address", 5, None, {"a": 1}, [5], [""], ["  padded"], ["dup", "dup"], ["tab\there"], [None]],
    )
    def test_garbage_reads_as_empty_and_keeps_the_entry(self, garbage):
        stored = {"Family": {"kind": "empty", "reason": "missing_source_column", "rows": 0, "missing_mapped": garbage}}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == (
            EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0),
        )

    def test_an_unknown_kind_keeps_its_observation(self):
        stored = {"Family": {"kind": "quarantined", "reason": "none", "rows": 0, "missing_mapped": ["Email Address"]}}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == (
            EntityOutcome("Family", OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR, 0, ("Email Address",)),
        )


# --------------------------------------------------------------------------- #
# Outcome notes (plan 0053 S10 — owner ruling 2026-09-25; the carrier S11 extends) #
# --------------------------------------------------------------------------- #
_COTEACHER_NOTE = outcomes.OutcomeNote.COTEACHER_SOURCE_UNUSABLE


class TestOutcomeNotesOnTheOutcome:
    def test_a_built_and_an_empty_outcome_may_carry_notes(self):
        built = EntityOutcome.built("Enrollments", 9, notes=((_COTEACHER_NOTE, 4),))
        empty = EntityOutcome.empty("Enrollments", OutcomeReason.NO_ROWS_AFTER_TRANSFORM, notes=((_COTEACHER_NOTE, 1),))
        assert built.notes == ((_COTEACHER_NOTE, 4),) and empty.notes == ((_COTEACHER_NOTE, 1),)
        # The twin: no note is the default, and the pre-S10 constructors are unchanged.
        assert EntityOutcome.built("Enrollments", 9).notes == ()

    @pytest.mark.parametrize(
        ("kind", "reason"),
        [(OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR), (OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED)],
    )
    def test_an_outcome_whose_transform_did_not_complete_carries_none(self, kind, reason):
        with pytest.raises(ValueError, match="carries no notes"):
            EntityOutcome("Enrollments", kind, reason, 0, notes=((_COTEACHER_NOTE, 1),))

    @pytest.mark.parametrize(
        "notes",
        [
            [(_COTEACHER_NOTE, 1)],  # not a tuple
            (("coteacher_source_unusable", 1),),  # a str, not the enum
            ((_COTEACHER_NOTE, 0),),  # a count below one
            ((_COTEACHER_NOTE, True),),  # a bool is not a count
            ((_COTEACHER_NOTE, 1.0),),  # nor a float
            ((_COTEACHER_NOTE,),),  # not a pair
            ((_COTEACHER_NOTE, 1), (_COTEACHER_NOTE, 2)),  # a note twice
        ],
    )
    def test_malformed_notes_are_refused(self, notes):
        with pytest.raises((TypeError, ValueError)):
            EntityOutcome.built("Enrollments", 9, notes=notes)

    def test_notes_are_written_as_a_code_to_count_map_only_when_present(self):
        stored = outcomes_to_record(
            [EntityOutcome.built("Enrollments", 9, notes=((_COTEACHER_NOTE, 4),)), EntityOutcome.built("Classes", 3)]
        )
        assert stored["Enrollments"] == {
            "kind": "built",
            "reason": "none",
            "rows": 9,
            "notes": {"coteacher_source_unusable": 4},
        }
        # The twin: an entity without a note is byte-identical to the pre-S10 entry.
        assert stored["Classes"] == {"kind": "built", "reason": "none", "rows": 3}

    def test_a_round_trip_through_json_is_lossless(self):
        import json

        sample = (EntityOutcome.built("Enrollments", 9, notes=((_COTEACHER_NOTE, 4),)),)
        record = json.loads(json.dumps({OUTCOMES_RECORD_KEY: outcomes_to_record(sample)}))
        assert outcomes_from_record(record) == sample

    @pytest.mark.parametrize(
        "garbage",
        [
            "coteacher_source_unusable",
            ["coteacher_source_unusable"],
            {"coteacher_source_unusable": 0},
            {"coteacher_source_unusable": True},
            {"coteacher_source_unusable": "4"},
            {"a_note_from_a_newer_build": 3},
            None,
        ],
    )
    def test_the_reader_drops_unusable_notes_and_keeps_the_entry(self, garbage):
        stored = {"Enrollments": {"kind": "built", "reason": "none", "rows": 9, "notes": garbage}}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: stored}) == (EntityOutcome.built("Enrollments", 9),)

    def test_the_reader_keeps_the_usable_pair_beside_an_unknown_one(self):
        """A newer build's unknown note is DROPPED (not read as a warning — DECISIONS 2026-09-25);
        a known one beside it survives."""
        notes = {"a_note_from_a_newer_build": 3, "coteacher_source_unusable": 2}
        stored = {"Enrollments": {"kind": "built", "reason": "none", "rows": 9, "notes": notes}}
        (outcome,) = outcomes_from_record({OUTCOMES_RECORD_KEY: stored})
        assert outcome.notes == ((_COTEACHER_NOTE, 2),)

    def test_a_note_on_a_failed_entry_is_dropped_never_the_entry(self):
        entry = {"kind": "failed", "reason": "transform_error", "rows": 0, "notes": {"coteacher_source_unusable": 1}}
        assert outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": entry}}) == (
            EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),
        )


class TestTheContextCarriesNotes:
    def test_one_note_per_entity_and_note_the_first_count_stands(self):
        from src.etl.transformers.context import TransformContext

        context = TransformContext()
        context.record_outcome_note("Enrollments", _COTEACHER_NOTE, 3)
        context.record_outcome_note("Enrollments", _COTEACHER_NOTE, 9)
        assert context.outcome_notes_for("Enrollments") == ((_COTEACHER_NOTE, 3),)
        assert context.outcome_notes_for("Classes") == ()

    @pytest.mark.parametrize(
        ("note", "count"), [("coteacher_source_unusable", 1), (_COTEACHER_NOTE, 0), (_COTEACHER_NOTE, True)]
    )
    def test_a_caller_bug_raises_at_the_call(self, note, count):
        from src.etl.transformers.context import TransformContext

        with pytest.raises((TypeError, ValueError)):
            TransformContext().record_outcome_note("Enrollments", note, count)


class TestRunTransformAttachesNotes:
    def test_a_built_and_an_empty_entity_carry_the_notes_their_transform_recorded(self, monkeypatch):
        mappings = {
            "Built": _entity("built.txt", {"Out": "in_col"}),
            "Quiet": _entity("quiet.txt", {"Out": "in_col"}),
            "NoRows": _entity("norows.txt", {"Out": "in_col"}),
        }
        raw = {name: pd.DataFrame({"in_col": ["a"]}) for name in ("built.txt", "quiet.txt", "norows.txt")}
        original = DataTransformer.transform

        def _stub(self, df, mapping, entity, raw_data, global_config):
            if entity in {"Built", "NoRows"}:
                self._context.record_outcome_note(entity, _COTEACHER_NOTE, 2)
            if entity == "NoRows":
                return pd.DataFrame()
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _stub)
        result, _ledger = _run(raw, mappings)
        assert result.outcomes == (
            EntityOutcome.built("Built", 1, notes=((_COTEACHER_NOTE, 2),)),
            EntityOutcome.built("Quiet", 1),  # the twin: no note recorded, none attached
            EntityOutcome.empty("NoRows", OutcomeReason.NO_ROWS_AFTER_TRANSFORM, notes=((_COTEACHER_NOTE, 2),)),
        )

    def test_an_isolated_failure_drops_its_notes_with_its_file(self, monkeypatch):
        """A FAILED outcome carries no notes (its file is left out whole); the run goes on."""
        mappings = {"Students": _entity("s.txt", {"Out": "in_col"}), "Family": _entity("f.txt", {"Out": "in_col"})}
        raw = {"s.txt": pd.DataFrame({"in_col": ["a"]}), "f.txt": pd.DataFrame({"in_col": ["b"]})}
        original = DataTransformer.transform

        def _stub(self, df, mapping, entity, raw_data, global_config):
            if entity == "Family":
                self._context.record_outcome_note(entity, _COTEACHER_NOTE, 1)
                raise ValueError("boom")
            return original(self, df, mapping, entity, raw_data, global_config)

        monkeypatch.setattr(DataTransformer, "transform", _stub)
        result, _ledger = _run(raw, mappings)
        family = next(o for o in result.outcomes if o.entity == "Family")
        assert family.kind is OutcomeKind.FAILED and family.notes == ()
