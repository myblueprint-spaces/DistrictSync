"""The entity bulkhead (plan 0053 S4, ``docs/developer/failure-policy.md`` §2, §3, §4, §11).

``pipeline.run_transform`` is the ONE entity-scope boundary in the product, shared by the CLI
and Convert. What it must do, each with the twin that proves the mechanism fires:

* **every entity, by its declared criticality** — parametrised over
  ``outcomes.ENTITY_CRITICALITY``: an ISOLATABLE entity's raise leaves every other entity
  written and delivered, its own previous CSV archived out of the glob, the run a
  ``success`` with that entity FAILED; a CRITICAL entity's raise propagates the SAME object
  (``is``), writes and uploads nothing and leaves the previous outputs byte-identical;
* **a dropped entity takes its data errors with it** — and only its own;
* **level-triggered** — a second consecutive night without the entity is still PARTIAL,
  although the vanished-entity anomaly (which needs the previous CSV the first night
  archived) has decayed: the G11 claim, measured here rather than code-read;
* **nothing built ⇒ the first isolated failure fails the run** — its own exception and
  category, never the vaguer ``no_output``; the two-entity twin completes PARTIAL;
* ``BaseException`` is never contained;
* **AST pins** — exactly one broad handler in ``run_transform`` (the bulkhead, reasoned
  ``noqa``), and no broad handler anywhere under ``src/etl/transformers`` outside the
  field-map engine (``apply_field_map`` / ``_apply_transform_resilient``) — a transformer
  RAISES, it never catches to continue (P2).

All data is synthetic (the ``tests/test_contract.py`` builders).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import traceback
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from src.etl import pipeline
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.outcomes import (
    ENTITY_CRITICALITY,
    EntityCriticality,
    EntityOutcome,
    OutcomeKind,
    OutcomeLedger,
    OutcomeReason,
    criticality_of,
)
from src.etl.pipeline import configured_entity_order, run_pipeline, run_transform
from src.etl.transformer import DataTransformer
from src.history.store import read_run_records
from src.ui_flet.home_status import LatestReason, build_record_for, classify_latest_reason
from tests.test_contract import (
    _create_mbp_all_inputs,
    _create_mbponly_inputs,
    _create_sd51_inputs,
    _create_sd51attendance_inputs,
    _create_unitychristian_inputs,
    _create_unitychristian_plain_report_inputs,
    _write_daily_absences,
    _write_period_absences,
)

_REPO = Path(__file__).resolve().parents[1]
_PIPELINE = _REPO / "src" / "etl" / "pipeline.py"
_TRANSFORMERS_DIR = _REPO / "src" / "etl" / "transformers"

#: The two functions of the field-map engine allowed a broad handler: per-cell and per-column
#: isolation, each RECORDED to the run's data errors (failure-policy §2 scope ladder).
_FIELD_MAP_ENGINE = frozenset({"apply_field_map", "_apply_transform_resilient"})


def _create_sd51_with_attendance_inputs(d: Path) -> None:
    """SD51's rostering files PLUS both absence bands, so its StudentAttendance BUILDS."""
    _create_sd51_inputs(d)
    _write_daily_absences(d)
    _write_period_absences(d)


#: entity -> (a bundled config that builds it, the fixture builder that makes it build).
#: TOTAL over ENTITY_CRITICALITY (pinned below), so a new entity cannot skip this sweep.
_SCENARIOS: dict[str, tuple[str, Callable[[Path], None]]] = {
    **{
        entity: ("mbp_all", _create_mbp_all_inputs)
        for entity in ("Students", "Staff", "Family", "Classes", "Enrollments", "CourseInfo", "StudentCourses")
    },
    "StudentAttendance": ("sd51myedbc", _create_sd51_with_attendance_inputs),
}


def _snapshot(directory: Path) -> dict[str, str]:
    """Every file under ``directory`` (archives included) → the sha256 of its bytes."""
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _archived(directory: Path, filename: str) -> list[Path]:
    return [p for p in directory.glob(f"archive_*/{filename}")]


def _kinds(record: dict) -> dict[str, tuple[str, str]]:
    return {entity: (entry["kind"], entry["reason"]) for entity, entry in record["entity_outcomes"].items()}


def _raise_for(monkeypatch: pytest.MonkeyPatch, target: str, exc: BaseException) -> None:
    """Make ONE entity's transform raise ``exc``; every other entity runs for real."""
    original = DataTransformer.transform

    def _stub(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
        if entity == target:
            raise exc
        return original(self, df, mapping, entity, raw_data, global_config)

    monkeypatch.setattr(DataTransformer, "transform", _stub)


@pytest.fixture()
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every delivery's manifest instead of connecting anywhere."""
    manifests: list[list[str]] = []

    def _capture(*_args, manifest, **_kwargs) -> bool:  # noqa: ANN002, ANN003
        manifests.append(sorted(manifest))
        return True

    monkeypatch.setattr(pipeline, "_sftp_upload", _capture)
    return manifests


# --------------------------------------------------------------------------- #
# Every entity, by its declared criticality                                     #
# --------------------------------------------------------------------------- #
def test_the_sweep_covers_every_declared_entity() -> None:
    assert set(_SCENARIOS) == set(ENTITY_CRITICALITY), "a new entity needs a scenario that builds it"


@pytest.mark.integration
@pytest.mark.parametrize("entity", sorted(ENTITY_CRITICALITY))
def test_a_raise_is_contained_or_propagated_by_the_entitys_criticality(
    entity: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploads: list[list[str]], caplog
) -> None:
    sis, build_inputs = _SCENARIOS[entity]
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    build_inputs(input_dir)

    # Night 1, clean — the baseline, and the non-vacuity: the target really BUILDS here.
    clean = run_pipeline(sis, str(input_dir), str(output_dir), sftp=True)
    clean_kinds = {o.entity: o.kind for o in clean.entity_outcomes}
    assert clean_kinds[entity] is OutcomeKind.BUILT, f"{sis} must build {entity} for this sweep to mean anything"
    assert (output_dir / f"{entity}.csv").is_file()
    before = _snapshot(output_dir)
    uploads.clear()

    # Night 2 — the target's transform raises.
    boom = RuntimeError(f"planted fault in {entity}")
    _raise_for(monkeypatch, entity, boom)
    configured = [o.entity for o in clean.entity_outcomes]

    if criticality_of(entity) is EntityCriticality.ISOLATABLE:
        with caplog.at_level(logging.ERROR, logger="src.etl.pipeline"):
            result = run_pipeline(sis, str(input_dir), str(output_dir), sftp=True)
        # The rest of the run went on: every other entity is exactly as it was on the clean night.
        assert {o.entity: o.kind for o in result.entity_outcomes} == {**clean_kinds, entity: OutcomeKind.FAILED}
        failed = next(o for o in result.entity_outcomes if o.entity == entity)
        assert failed == EntityOutcome.failed(entity, OutcomeReason.TRANSFORM_ERROR)
        # Never substituted: absent from the top level, its previous CSV archived, not shipped.
        assert not (output_dir / f"{entity}.csv").exists()
        assert _archived(output_dir, f"{entity}.csv")
        assert len(uploads) == 1 and f"{entity}.csv" not in uploads[0]
        expected_written = sorted(f"{name}.csv" for name, kind in clean_kinds.items() if kind is OutcomeKind.BUILT)
        assert uploads[0] == sorted(set(expected_written) - {f"{entity}.csv"})
        # Logged ONCE, at ERROR, with its traceback.
        lines = [r for r in caplog.records if r.getMessage().startswith(f"ENTITY NOT BUILT [{entity}]")]
        assert len(lines) == 1 and lines[0].levelno == logging.ERROR and lines[0].exc_info is not None
        records = read_run_records()
        assert records is not None
        assert records[0]["status"] == "success" and records[0]["error_category"] == "none"
        assert _kinds(records[0])[entity] == ("failed", "transform_error")
        assert classify_latest_reason(records[0], prior_build=build_record_for(records, 0)) is LatestReason.PARTIAL
    else:
        with pytest.raises(RuntimeError) as raised:
            run_pipeline(sis, str(input_dir), str(output_dir), sftp=True)
        assert raised.value is boom, "a CRITICAL entity's raise propagates the ORIGINAL object"
        assert uploads == [], "nothing is delivered"
        assert _snapshot(output_dir) == before, "the previous outputs are untouched"
        records = read_run_records()
        assert records is not None
        assert records[0]["status"] == "failed" and records[0]["error_category"] == "unknown"
        position = configured.index(entity)
        kinds = _kinds(records[0])
        assert kinds[entity] == ("failed", "transform_error")
        assert all(kinds[name] == ("not_run", "run_aborted") for name in configured[position + 1 :])
        assert all(kinds[name][0] in {"built", "empty"} for name in configured[:position])


# --------------------------------------------------------------------------- #
# Data errors: a dropped entity takes its own entries with it                   #
# --------------------------------------------------------------------------- #
_GC = {"academic_start_month_day": "09-01", "academic_end_month_day": "06-30"}


def _entity(source_file: str) -> dict:
    return {"source_files": {"primary": source_file}, "field_map": {"Out": "in_col"}}


def _stub_with_data_errors(monkeypatch: pytest.MonkeyPatch, *, raising: frozenset[str]) -> None:
    """Every entity records ONE data error; the ``raising`` ones then raise."""

    def _stub(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
        self.data_errors.append({"entity": entity, "field": "Out", "failed_rows": 1})
        if entity in raising:
            raise RuntimeError(f"{entity} failed after recording a data error")
        return pd.DataFrame({"Out": [entity]})

    monkeypatch.setattr(DataTransformer, "transform", _stub)


def _run(mappings: dict) -> tuple[pipeline.TransformOutputs, OutcomeLedger]:
    raw = {entity["source_files"]["primary"]: pd.DataFrame({"in_col": ["x"]}) for entity in mappings.values()}
    ledger = OutcomeLedger(configured_entity_order(mappings, _GC))
    return run_transform(raw, mappings, _GC, ledger=ledger), ledger


class TestDataErrorsRollBackWithTheEntity:
    _MAPPINGS = {"Students": _entity("s.txt"), "Family": _entity("f.txt"), "Classes": _entity("c.txt")}

    def test_an_isolated_entitys_data_errors_are_removed_and_only_its_own(self, monkeypatch) -> None:
        _stub_with_data_errors(monkeypatch, raising=frozenset({"Family"}))
        result, _ = _run(self._MAPPINGS)
        assert [e["entity"] for e in result.data_errors] == ["Students", "Classes"]

    def test_TWO_isolated_entities_both_roll_back_and_a_built_ones_entry_survives(self, monkeypatch) -> None:
        """The second isolated failure rolls back from its OWN mark, not the first one's."""
        _stub_with_data_errors(monkeypatch, raising=frozenset({"Family", "CourseInfo"}))
        result, ledger = _run(
            {"Students": _entity("s.txt"), "Family": _entity("f.txt"), "CourseInfo": _entity("c.txt")}
        )
        assert [(o.entity, o.kind) for o in ledger.complete()] == [
            ("Students", OutcomeKind.BUILT),
            ("Family", OutcomeKind.FAILED),
            ("CourseInfo", OutcomeKind.FAILED),
        ]
        assert [e["entity"] for e in result.data_errors] == ["Students"]

    def test_the_twin_when_nothing_is_isolated_every_entry_survives(self, monkeypatch) -> None:
        _stub_with_data_errors(monkeypatch, raising=frozenset())
        result, _ = _run(self._MAPPINGS)
        assert [e["entity"] for e in result.data_errors] == ["Students", "Family", "Classes"]


# --------------------------------------------------------------------------- #
# Level-triggered: the second night is still PARTIAL                            #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_two_consecutive_nights_without_family_are_both_partial(tmp_path: Path) -> None:
    """Night 0 builds Family; nights 1 and 2 get the PLAIN contact report.

    The vanished-entity anomaly needs the previous ``Family.csv`` — which night 1 archives —
    so it fires on night 1 and is GONE on night 2 (the one-night decay, G11, measured). The
    PARTIAL verdict is derived from each night's OWN record, so it does not decay (P7).
    """
    sis = "unitychristianmyedbc"
    enhanced, plain, out = tmp_path / "enhanced", tmp_path / "plain", tmp_path / "out"
    for d in (enhanced, plain, out):
        d.mkdir()
    _create_unitychristian_inputs(enhanced)
    _create_unitychristian_plain_report_inputs(plain)

    night0 = run_pipeline(sis, str(enhanced), str(out))
    assert "Family" in night0.entity_counts and (out / "Family.csv").is_file()

    night1 = run_pipeline(sis, str(plain), str(out))
    records = read_run_records()
    assert records is not None
    assert any("Family produced no output this run" in a for a in night1.anomalies)
    assert classify_latest_reason(records[0], prior_build=build_record_for(records, 0)) is LatestReason.PARTIAL
    assert not (out / "Family.csv").exists()
    first_archive = _archived(out, "Family.csv")
    assert len(first_archive) == 1

    night2 = run_pipeline(sis, str(plain), str(out))
    records = read_run_records()
    assert records is not None and len(records) == 3
    assert night2.anomalies == [], "the anomaly decays after one night — the gap this verdict closes"
    assert records[0]["anomalies"] == []
    assert _kinds(records[0])["Family"] == ("failed", "missing_source_column")
    assert classify_latest_reason(records[0], prior_build=build_record_for(records, 0)) is LatestReason.PARTIAL
    assert not (out / "Family.csv").exists()
    assert _archived(out, "Family.csv") == first_archive, "night 0's Family.csv stays where night 1 put it"


# --------------------------------------------------------------------------- #
# Nothing built: the first isolated failure fails the run                        #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
class TestNothingBuiltFailsTheRun:
    def test_a_single_entity_config_re_raises_its_own_exception(self, tmp_path: Path, monkeypatch) -> None:
        input_dir, output_dir = tmp_path / "in", tmp_path / "out"
        input_dir.mkdir()
        output_dir.mkdir()
        _create_sd51attendance_inputs(input_dir)
        boom = RuntimeError("planted attendance fault")
        _raise_for(monkeypatch, "StudentAttendance", boom)
        with pytest.raises(RuntimeError) as raised:
            run_pipeline("sd51attendance", str(input_dir), str(output_dir))
        assert raised.value is boom
        # Traceback preserved: the transform's own frame is still on it.
        assert any(frame.name == "_stub" for frame in traceback.extract_tb(raised.value.__traceback__))
        records = read_run_records()
        assert records is not None
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "unknown", "the exception's own category — never no_output"
        assert _kinds(records[0]) == {"StudentAttendance": ("failed", "transform_error")}
        assert not list(output_dir.glob("*.csv"))

    def test_the_real_unmapped_absence_code_fails_the_attendance_only_config(self, tmp_path: Path) -> None:
        """§5 #23 on a real transform: an unmapped (code, authorized) pair — ``data``, as before S4."""
        input_dir, output_dir = tmp_path / "in", tmp_path / "out"
        input_dir.mkdir()
        output_dir.mkdir()
        _create_sd51attendance_inputs(input_dir)
        daily = pd.read_csv(input_dir / "StudentDailyAbsences.txt", header=None, dtype=str, keep_default_na=False)
        daily.iloc[0, 14] = "Z"  # "Absent Code AM" — a code the category map does not list
        daily.to_csv(input_dir / "StudentDailyAbsences.txt", index=False, header=False)
        with pytest.raises(ValueError, match="no category mapping"):
            run_pipeline("sd51attendance", str(input_dir), str(output_dir))
        records = read_run_records()
        assert records is not None
        assert records[0]["error_category"] == "data"
        assert _kinds(records[0]) == {"StudentAttendance": ("failed", "transform_error")}

    def test_the_twin_a_two_entity_config_with_one_failure_is_partial(self, tmp_path: Path, monkeypatch) -> None:
        input_dir, output_dir = tmp_path / "in", tmp_path / "out"
        input_dir.mkdir()
        output_dir.mkdir()
        _create_mbponly_inputs(input_dir)
        _raise_for(monkeypatch, "CourseInfo", RuntimeError("planted course-catalog fault"))
        result = run_pipeline("mbponly", str(input_dir), str(output_dir))
        assert [(o.entity, o.kind) for o in result.entity_outcomes] == [
            ("CourseInfo", OutcomeKind.FAILED),
            ("StudentCourses", OutcomeKind.BUILT),
        ]
        assert sorted(p.name for p in output_dir.glob("*.csv")) == ["StudentCourses.csv"]
        records = read_run_records()
        assert records is not None and records[0]["status"] == "success"
        assert classify_latest_reason(records[0], prior_build=build_record_for(records, 0)) is LatestReason.PARTIAL

    def test_a_failure_beside_only_empty_entities_re_raises_too(self, monkeypatch) -> None:
        """Nothing BUILT is the rule, not "everything failed": with the rest EMPTY the run
        has nothing to deliver either, and the precise failure beats ``no_output``."""
        boom = SourceSchemaError(
            "planted", entity="Family", columns=("Parent Auth / Guardian",), guard=GuardKind.PII_SCOPE
        )
        mappings = {"Family": _entity("f.txt"), "CourseInfo": _entity("c.txt")}
        _raise_for(monkeypatch, "Family", boom)
        raw = {"f.txt": pd.DataFrame({"in_col": ["x"]}), "c.txt": pd.DataFrame()}
        ledger = OutcomeLedger(configured_entity_order(mappings, _GC))
        with pytest.raises(SourceSchemaError) as raised:
            run_transform(raw, mappings, _GC, ledger=ledger)
        assert raised.value is boom
        assert ledger.complete() == (
            EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN),
            EntityOutcome.empty("CourseInfo", OutcomeReason.SOURCE_FILES_EMPTY),
        )

    def test_with_TWO_isolated_failures_and_nothing_built_the_FIRST_is_re_raised(self, monkeypatch) -> None:
        """The rule is the FIRST contained failure (its own category and traceback) — never
        whichever happened to be last."""
        first, second = RuntimeError("Family fault"), ValueError("CourseInfo fault")
        planted = {"Family": first, "CourseInfo": second}

        def _stub(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
            self.data_errors.append({"entity": entity, "field": "Out", "failed_rows": 1})
            raise planted[entity]

        monkeypatch.setattr(DataTransformer, "transform", _stub)
        mappings = {"Family": _entity("f.txt"), "CourseInfo": _entity("c.txt")}
        raw = {"f.txt": pd.DataFrame({"in_col": ["x"]}), "c.txt": pd.DataFrame({"in_col": ["x"]})}
        ledger = OutcomeLedger(configured_entity_order(mappings, _GC))
        with pytest.raises(RuntimeError) as raised:
            run_transform(raw, mappings, _GC, ledger=ledger)
        assert raised.value is first, "the LAST isolated failure was re-raised, not the first"
        assert ledger.complete() == (
            EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),
            EntityOutcome.failed("CourseInfo", OutcomeReason.TRANSFORM_ERROR),
        )

    def test_the_twin_one_built_entity_is_enough_to_complete(self, monkeypatch) -> None:
        _stub_with_data_errors(monkeypatch, raising=frozenset({"Family"}))
        result, _ = _run({"Family": _entity("f.txt"), "Students": _entity("s.txt")})
        assert [(o.entity, o.kind) for o in result.outcomes] == [
            ("Family", OutcomeKind.FAILED),
            ("Students", OutcomeKind.BUILT),
        ]


def test_a_base_exception_in_an_isolatable_entity_is_never_contained(monkeypatch) -> None:
    mappings = {"Family": _entity("f.txt"), "Students": _entity("s.txt")}
    _raise_for(monkeypatch, "Family", KeyboardInterrupt())
    raw = {"f.txt": pd.DataFrame({"in_col": ["x"]}), "s.txt": pd.DataFrame({"in_col": ["x"]})}
    ledger = OutcomeLedger(configured_entity_order(mappings, _GC))
    with pytest.raises(KeyboardInterrupt):
        run_transform(raw, mappings, _GC, ledger=ledger)
    assert ledger.outcomes == (), "a Ctrl+C is not an entity outcome, and the loop did not go on"


# --------------------------------------------------------------------------- #
# The dry run prints a left-out entity in its own shape                          #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_a_dry_run_prints_the_left_out_entity_in_a_distinct_shape(tmp_path: Path, capsys) -> None:
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    _create_unitychristian_plain_report_inputs(input_dir)
    run_pipeline("unitychristianmyedbc", str(input_dir), str(output_dir), dry_run=True)
    out = capsys.readouterr().out
    assert "=== DRY RUN (no files written) ===" in out
    assert "  ! not built: Family (missing_source_column)" in out.splitlines()
    assert not any(line.startswith("  Family:") for line in out.splitlines())
    for entity in ("Students", "Staff", "Classes", "Enrollments"):
        assert any(line.startswith(f"  {entity}: ") and " rows, columns: " in line for line in out.splitlines())


# --------------------------------------------------------------------------- #
# AST pins                                                                      #
# --------------------------------------------------------------------------- #
def _is_broad(handler: ast.ExceptHandler) -> bool:
    """A handler that catches everything an entity transform could raise (or more)."""
    names: list[ast.expr] = []
    if handler.type is None:
        return True
    names = list(handler.type.elts) if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in {"Exception", "BaseException"} for n in names)


def _broad_handlers_in(source: str, function: str) -> list[ast.ExceptHandler]:
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
    )
    return [node for node in ast.walk(func) if isinstance(node, ast.ExceptHandler) and _is_broad(node)]


def _broad_handlers_outside(source: str, allowed: frozenset[str]) -> list[tuple[str, int]]:
    """``(enclosing function, line)`` for every broad handler NOT inside an ``allowed`` function."""
    tree = ast.parse(source)
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else enclosing
            if isinstance(child, ast.ExceptHandler) and _is_broad(child) and enclosing not in allowed:
                found.append((enclosing, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return found


class TestTheOneBoundary:
    def test_run_transform_has_exactly_one_broad_handler_and_it_is_the_bulkhead(self) -> None:
        source = _PIPELINE.read_text(encoding="utf-8")
        handlers = _broad_handlers_in(source, "run_transform")
        assert len(handlers) == 1, "failure-policy §11: exactly ONE entity-scope broad handler, in run_transform"
        (bulkhead,) = handlers
        assert isinstance(bulkhead.type, ast.Name) and bulkhead.type.id == "Exception", "never BaseException"
        line = source.splitlines()[bulkhead.lineno - 1]
        assert "noqa: BLE001 — entity bulkhead, failure-policy §2" in line
        # Its CRITICAL branch re-raises BARE — the same object, never a wrapped one.
        assert any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(bulkhead))

    def test_negative_twin_a_second_broad_handler_is_counted(self) -> None:
        doctored = _PIPELINE.read_text(encoding="utf-8").replace(
            "    transformer = DataTransformer()\n",
            "    try:\n        transformer = DataTransformer()\n    except Exception:\n        raise\n",
            1,
        )
        assert len(_broad_handlers_in(doctored, "run_transform")) == 2

    def test_no_transformer_catches_broadly_outside_the_field_map_engine(self) -> None:
        offenders = {
            path.name: _broad_handlers_outside(path.read_text(encoding="utf-8"), _FIELD_MAP_ENGINE)
            for path in sorted(_TRANSFORMERS_DIR.glob("*.py"))
        }
        assert {name: found for name, found in offenders.items() if found} == {}, (
            "failure-policy §2: a transformer RAISES; only run_transform decides entity scope"
        )

    def test_positive_twin_the_sweep_finds_the_engines_two_handlers(self) -> None:
        base = (_TRANSFORMERS_DIR / "base.py").read_text(encoding="utf-8")
        in_engine = [fn for fn in _FIELD_MAP_ENGINE if _broad_handlers_in(base, fn)]
        assert sorted(in_engine) == sorted(_FIELD_MAP_ENGINE)
        assert _broad_handlers_outside(base, frozenset()) != [], "with no allowance the sweep sees them"

    def test_negative_twin_a_broad_handler_in_a_transformer_is_caught(self) -> None:
        doctored = (
            "class FamilyTransformer:\n"
            "    def transform(self, df):\n"
            "        try:\n"
            "            return df\n"
            "        except Exception:\n"
            "            return None\n"
        )
        assert _broad_handlers_outside(doctored, _FIELD_MAP_ENGINE) == [("transform", 5)]
