"""Every Convert attempt leaves exactly one run record (plan 0053 S5, failure-policy §7, P8).

Before S5 a ``convert_job`` whose build RAISED wrote nothing: its outcome ledger was right
(S2) but nothing persisted it, so Run History could not tell "it failed" from "nobody ran
it". Now the manual path has the pipeline's symmetric failure sink:

* a raise anywhere from ``to_raw_dict`` through the quality report records ONE ``failed``
  run — the SAME ``error_category`` ``run_pipeline`` records for that fault (by TYPE) and the
  completed ledger — and is re-raised as the SAME object, so ``JobRunner``'s ``on_error``
  words the card from it;
* a config that will not load is the typed ``ConfigLoadError`` and records ``config``; any
  OTHER config-load raise is recorded by its type too, exactly as ``run_pipeline`` records it;
* the four deliberate non-records (unset output folder, an unusable output folder,
  ``NO_INPUT``, ``NEEDS_ANOMALY_ACK``) still write nothing — each with a positive twin that
  records, so "nothing recorded" can never pass because recording is broken;
* recording is best-effort and can never mask the fault;
* ``_record_manual_run``'s ``status`` / ``error_category`` have no default.

Synthetic data only (the run-store fixture's two-pupil myedbc drop).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.config.app_config import AppConfig
from src.config.authoring import overlay_path
from src.config.models import MappingConfig
from src.etl import pipeline
from src.etl.errors import ConfigLoadError, GuardKind, OutputFolderUnsetError, SourceSchemaError
from src.etl.extractor import DataExtractor, ExtractionError
from src.etl.pipeline import run_pipeline
from src.etl.transformer import DataTransformer
from src.history.store import read_run_records
from src.ui_flet import job_runner
from src.ui_flet.convert_output import run_identity
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens import convert as convert_mod
from src.ui_flet.screens.convert import _record_manual_run, convert_job
from tests.test_pipeline_run_store import _write_myedbc_input

_REPO = Path(__file__).resolve().parents[1]
_CONVERT = _REPO / "src" / "ui_flet" / "screens" / "convert.py"
_SIS = "myedbc"
_ORDER = ["Students", "Staff", "Family", "Classes", "Enrollments"]


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_myedbc_input(d)
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture()
def configured(gde_input: Path, gde_output: Path) -> tuple[Path, Path]:
    AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type=_SIS).save()
    return gde_input, gde_output


def _records() -> list[dict]:
    records = read_run_records()
    assert records is not None, "the run store could not be read"
    return records


def _kinds(record: dict) -> dict[str, tuple[str, str]]:
    return {entity: (entry["kind"], entry["reason"]) for entity, entry in record["entity_outcomes"].items()}


def _raise_in(monkeypatch: pytest.MonkeyPatch, target: str, exc: BaseException) -> None:
    """Make ONE entity's transform raise ``exc``; every other entity runs for real."""
    original = DataTransformer.transform

    def _stub(self, df, mapping, entity, raw_data, global_config):  # noqa: ANN001, ANN202
        if entity == target:
            raise exc
        return original(self, df, mapping, entity, raw_data, global_config)

    monkeypatch.setattr(DataTransformer, "transform", _stub)


def _staff_schema_error() -> SourceSchemaError:
    return SourceSchemaError(
        "Staff source lacks a column", entity="Staff", columns=("Teaching Staff",), guard=GuardKind.PII_SCOPE
    )


# --------------------------------------------------------------------------- #
# A CRITICAL entity raises → exactly one failed record; the SAME object escapes  #
# --------------------------------------------------------------------------- #
class TestACriticalRaiseIsRecordedOnce:
    def test_one_failed_record_with_the_category_and_the_ledger(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, gde_output = configured
        injected = _staff_schema_error()
        _raise_in(monkeypatch, "Staff", injected)

        with pytest.raises(SourceSchemaError) as excinfo:
            convert_job(_SIS, str(gde_input))

        assert excinfo.value is injected, "the sink must re-raise the ORIGINAL object"
        records = _records()
        assert len(records) == 1, "exactly one record per attempt"
        record = records[0]
        assert record["source"] == "manual"
        assert record["status"] == "failed"
        assert record["error_category"] == "source_schema"
        assert record["sis_type"] == _SIS
        assert "error" not in record  # privacy split: the free text never reaches the store
        assert list(record["entity_outcomes"]) == _ORDER
        assert _kinds(record) == {
            "Students": ("built", "none"),
            "Staff": ("failed", "missing_source_column"),
            "Family": ("not_run", "run_aborted"),
            "Classes": ("not_run", "run_aborted"),
            "Enrollments": ("not_run", "run_aborted"),
        }
        assert list(gde_output.glob("*.csv")) == []  # nothing was written

    def test_twin_a_clean_convert_writes_exactly_one_success_record(self, configured: tuple[Path, Path]) -> None:
        """The positive twin: the same drop, nothing injected — one record, and it is green."""
        gde_input, _ = configured
        result = convert_job(_SIS, str(gde_input))

        assert result.status is ConvertStatus.DELIVERED
        records = _records()
        assert len(records) == 1
        assert records[0]["status"] == "success"
        assert records[0]["error_category"] == "none"
        assert {kind for kind, _ in _kinds(records[0]).values()} == {"built"}

    def test_the_object_reaching_on_error_is_the_injected_one(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through the REAL routing seam ``JobRunner.run`` uses — not just ``pytest.raises``."""
        gde_input, _ = configured
        injected = _staff_schema_error()
        _raise_in(monkeypatch, "Staff", injected)
        seen: list[BaseException] = []
        done: list[object] = []

        job_runner.route(lambda: convert_job(_SIS, str(gde_input)), on_success=done.append, on_failure=seen.append)

        assert done == []
        assert seen == [injected] and seen[0] is injected
        assert len(_records()) == 1

    def test_a_raise_after_the_write_records_failed_though_the_files_exist(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The documented edge: the quality report raises AFTER ``save_all`` committed.

        The attempt did not complete, so the record says ``failed`` — while the files it wrote
        are on disk. The outcomes are the completed ledger (every entity BUILT) and the counts
        are what was built, the pipeline sink's ``_counts_from_outputs`` rule.
        """
        gde_input, gde_output = configured
        injected = RuntimeError("quality report blew up")

        def _boom(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            raise injected

        monkeypatch.setattr(convert_mod.DataQualityReport, "analyze", _boom)

        with pytest.raises(RuntimeError) as excinfo:
            convert_job(_SIS, str(gde_input))

        assert excinfo.value is injected
        assert (gde_output / "Students.csv").exists(), "the write committed before the raise"
        records = _records()
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "unknown"
        assert records[0]["Students"] == 2
        assert {kind for kind, _ in _kinds(records[0]).values()} == {"built"}

    def test_a_raise_before_the_ledger_exists_records_no_outcomes(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sink's ``ledger is None`` arm: ``to_raw_dict`` raises before ``OutcomeLedger`` is built."""
        gde_input, _ = configured
        injected = RuntimeError("to_raw_dict blew up")

        def _boom(*_a: object, **_kw: object) -> None:
            raise injected

        monkeypatch.setattr(MappingConfig, "to_raw_dict", _boom)

        with pytest.raises(RuntimeError) as excinfo:
            convert_job(_SIS, str(gde_input))

        assert excinfo.value is injected
        records = _records()
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "unknown"
        assert records[0]["entity_outcomes"] is None, "no ledger existed yet"

    def test_the_sink_ends_before_the_delivery_leg(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed upload is its OWN recorded result (BUILT_NOT_DELIVERED, status success) —
        never a second record from the failure sink, never an on_error card."""
        gde_input, _ = configured

        class _Failing:
            def __init__(self, **_kw: object) -> None:
                pass

            def upload_csvs(self, *_a: object, **_kw: object) -> None:
                raise ConnectionError("no route to host")

        monkeypatch.setattr(convert_mod, "SFTPUploader", _Failing)
        result = convert_job(_SIS, str(gde_input), sftp_requested=True)

        assert result.status is ConvertStatus.BUILT_NOT_DELIVERED
        records = _records()
        assert len(records) == 1
        assert records[0]["status"] == "success"
        assert records[0]["sftp_attempted"] is True and records[0]["sftp_ok"] is False


# --------------------------------------------------------------------------- #
# The config load: typed, recorded as `config`                                  #
# --------------------------------------------------------------------------- #
class TestAConfigThatWillNotLoad:
    def test_a_yaml_error_is_raised_as_config_load_error_and_recorded(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, _ = configured
        original = yaml.YAMLError("mapping values are not allowed here")

        def _torn(_name: str) -> None:
            raise original

        monkeypatch.setattr(convert_mod, "load_config", _torn)

        with pytest.raises(ConfigLoadError) as excinfo:
            convert_job(_SIS, str(gde_input))

        assert excinfo.value.__cause__ is original, "the loader's error rides along for the log"
        records = _records()
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "config"
        assert records[0]["entity_outcomes"] is None, "no ledger existed yet"

    @pytest.mark.parametrize(
        "raised", [FileNotFoundError("no such mapping"), ValueError("version gate")], ids=["not-found", "invalid"]
    )
    def test_the_other_two_loader_types_are_typed_the_same_way(
        self, raised: Exception, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, _ = configured

        def _fail(_name: str) -> None:
            raise raised

        monkeypatch.setattr(convert_mod, "load_config", _fail)
        with pytest.raises(ConfigLoadError) as excinfo:
            convert_job(_SIS, str(gde_input))
        assert excinfo.value.__cause__ is raised
        assert [r["error_category"] for r in _records()] == ["config"]

    def test_a_torn_overlay_records_config_on_BOTH_entry_points(self, configured: tuple[Path, Path]) -> None:
        """A REAL unparseable user-dir mapping, not a monkeypatch — the same file, both paths."""
        gde_input, gde_output = configured
        sis = "sd93custom"
        target = overlay_path(sis)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("_base: myedbc\ndistrict_name: [SD93 - torn\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            run_pipeline(sis, str(gde_input), str(gde_output), source="cli")
        with pytest.raises(ConfigLoadError) as excinfo:
            convert_job(sis, str(gde_input))

        assert isinstance(excinfo.value.__cause__, yaml.YAMLError)
        by_source = {r["source"]: r for r in _records()}
        assert set(by_source) == {"cli", "manual"}
        for record in by_source.values():
            assert record["status"] == "failed"
            assert record["error_category"] == "config"
            assert record["entity_outcomes"] is None

    def test_twin_a_loadable_config_records_success_not_config(self, configured: tuple[Path, Path]) -> None:
        gde_input, _ = configured
        convert_job(_SIS, str(gde_input))
        assert [r["error_category"] for r in _records()] == ["none"]

    @pytest.mark.parametrize(
        "raised",
        [PermissionError("denied opening the overlay"), RuntimeError("profile refused")],
        ids=["permission", "runtime"],
    )
    def test_an_untyped_config_load_raise_is_recorded_on_BOTH_entry_points(
        self, raised: Exception, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outside the loader's typed three: not a ``ConfigLoadError``, but still ONE record per
        attempt — ``unknown`` by TYPE, which is what ``run_pipeline``'s outer sink records."""
        gde_input, gde_output = configured

        def _fail(_name: str) -> None:
            raise raised

        monkeypatch.setattr(pipeline, "load_config", _fail)
        monkeypatch.setattr(convert_mod, "load_config", _fail)

        with pytest.raises(type(raised)):
            run_pipeline(_SIS, str(gde_input), str(gde_output), source="cli")
        with pytest.raises(type(raised)) as excinfo:
            convert_job(_SIS, str(gde_input))

        assert excinfo.value is raised, "the SAME object reaches on_error"
        by_source = {r["source"]: r for r in _records()}
        assert set(by_source) == {"cli", "manual"}
        for record in by_source.values():
            assert record["status"] == "failed"
            assert record["error_category"] == "unknown"
            assert record["entity_outcomes"] is None

    def test_twin_the_typed_three_still_become_config_load_error(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The outer catch-all must not swallow the typing: a loader type is still ``config``."""
        gde_input, _ = configured
        original = FileNotFoundError("no such mapping")

        def _fail(_name: str) -> None:
            raise original

        monkeypatch.setattr(convert_mod, "load_config", _fail)
        with pytest.raises(ConfigLoadError) as excinfo:
            convert_job(_SIS, str(gde_input))
        assert excinfo.value.__cause__ is original
        assert [(r["status"], r["error_category"]) for r in _records()] == [("failed", "config")]


# --------------------------------------------------------------------------- #
# Recording never masks the fault                                              #
# --------------------------------------------------------------------------- #
class TestRecordingNeverMasksTheFault:
    @pytest.mark.parametrize("seam", ["write_run_record", "build_run_record"])
    def test_a_broken_store_leaves_the_original_exception(
        self, seam: str, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, _ = configured
        injected = _staff_schema_error()
        _raise_in(monkeypatch, "Staff", injected)

        def _broken(*_a: object, **_kw: object) -> None:
            raise RuntimeError(f"{seam} is broken")

        monkeypatch.setattr(convert_mod, seam, _broken)

        with pytest.raises(SourceSchemaError) as excinfo:
            convert_job(_SIS, str(gde_input))
        assert excinfo.value is injected

    @pytest.mark.parametrize("seam", ["write_run_record", "build_run_record"])
    def test_a_broken_store_never_masks_a_config_error_either(
        self, seam: str, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``build_run_record`` is the seam that actually escapes ``_record_failed_attempt`` (the
        writer has its own suppress), so it is the one that proves the CALLER's suppress."""
        gde_input, _ = configured

        def _broken(*_a: object, **_kw: object) -> None:
            raise RuntimeError(f"{seam} is broken")

        monkeypatch.setattr(convert_mod, seam, _broken)
        original = yaml.YAMLError("torn")

        def _torn(_name: str) -> None:
            raise original

        monkeypatch.setattr(convert_mod, "load_config", _torn)
        with pytest.raises(ConfigLoadError) as excinfo:
            convert_job(_SIS, str(gde_input))
        assert excinfo.value.__cause__ is original

    def test_twin_a_healthy_store_does_record_the_same_fault(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, _ = configured
        _raise_in(monkeypatch, "Staff", _staff_schema_error())
        with pytest.raises(SourceSchemaError):
            convert_job(_SIS, str(gde_input))
        assert [r["status"] for r in _records()] == ["failed"]


# --------------------------------------------------------------------------- #
# The four deliberate non-records — each beside a twin that DOES record         #
# --------------------------------------------------------------------------- #
class TestTheFourNonRecords:
    def test_an_unset_output_folder_records_nothing(self, gde_input: Path) -> None:
        AppConfig(input_dir=str(gde_input), output_dir="", sis_type=_SIS).save()
        with pytest.raises(OutputFolderUnsetError):
            convert_job(_SIS, str(gde_input))
        assert _records() == []

    def test_an_unusable_output_folder_records_nothing(self, gde_input: Path, tmp_path: Path) -> None:
        squatter = tmp_path / "not_a_folder"
        squatter.write_text("a file, not a folder", encoding="utf-8")
        AppConfig(input_dir=str(gde_input), output_dir=str(squatter), sis_type=_SIS).save()
        result = convert_job(_SIS, str(gde_input))
        assert result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE
        assert _records() == []

    def test_a_write_oserror_records_nothing(
        self, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gde_input, _ = configured

        def _locked(*_a: object, **_kw: object) -> None:
            raise PermissionError("Students.csv is open in Excel")

        monkeypatch.setattr(convert_mod.DataLoader, "save_all", _locked)
        result = convert_job(_SIS, str(gde_input))
        assert result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE
        assert _records() == []

    def test_no_input_records_nothing(self, gde_output: Path, tmp_path: Path) -> None:
        empty = tmp_path / "empty_in"
        empty.mkdir()
        AppConfig(input_dir=str(empty), output_dir=str(gde_output), sis_type=_SIS).save()
        assert convert_job(_SIS, str(empty)).status is ConvertStatus.NO_INPUT
        assert _records() == []

    def test_a_pending_anomaly_question_records_nothing(self, configured: tuple[Path, Path]) -> None:
        gde_input, gde_output = configured
        assert convert_job(_SIS, str(gde_input)).status is ConvertStatus.DELIVERED  # the baseline
        assert len(_records()) == 1
        # The student export shrinks to one pupil — a >20% drop the admin must acknowledge.
        demographic = gde_input / "StudentDemographicInformation.txt"
        pd.read_csv(demographic, dtype=str).head(1).to_csv(demographic, index=False)

        pending = convert_job(_SIS, str(gde_input))

        assert pending.status is ConvertStatus.NEEDS_ANOMALY_ACK
        assert len(_records()) == 1, "a question is not an outcome"
        # Twin: the acknowledged re-run IS an outcome and is recorded.
        convert_job(_SIS, str(gde_input), anomaly_ack=run_identity(_SIS, str(gde_input)))
        assert len(_records()) == 2
        assert (gde_output / "Students.csv").exists()


# --------------------------------------------------------------------------- #
# Parity: the manual record == the pipeline record for the SAME injected fault  #
# --------------------------------------------------------------------------- #
def _inject_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    _raise_in(monkeypatch, "Staff", _staff_schema_error())


def _inject_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unreadable(self, *_a: object, **_kw: object) -> None:  # noqa: ANN001
        raise ExtractionError("StudentSchedule.txt could not be parsed")

    monkeypatch.setattr(DataExtractor, "load_data", _unreadable)


def _inject_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    def _torn(_name: str) -> None:
        raise yaml.YAMLError("torn")

    monkeypatch.setattr(pipeline, "load_config", _torn)
    monkeypatch.setattr(convert_mod, "load_config", _torn)


class TestParityWithThePipelineSink:
    @pytest.mark.parametrize(
        ("inject", "category"),
        [(_inject_staff, "source_schema"), (_inject_extraction, "input_unreadable"), (_inject_yaml, "config")],
        ids=["critical-source-schema", "extraction", "yaml"],
    )
    def test_both_entry_points_record_the_same_failure(
        self, inject, category: str, configured: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: ANN001
        gde_input, gde_output = configured
        inject(monkeypatch)

        with pytest.raises((SystemExit, Exception)):
            run_pipeline(_SIS, str(gde_input), str(gde_output), source="cli")
        with pytest.raises(Exception):  # noqa: B017 - the type is the parametrised fault's own
            convert_job(_SIS, str(gde_input))

        by_source = {r["source"]: r for r in _records()}
        assert set(by_source) == {"cli", "manual"}
        cli, manual = by_source["cli"], by_source["manual"]
        assert manual["status"] == cli["status"] == "failed"
        assert manual["error_category"] == cli["error_category"] == category
        assert manual["entity_outcomes"] == cli["entity_outcomes"]
        # The rest of the record's axes, too — a failed attempt's record is the same whichever
        # entry point wrote it (no anomalies, no data-errors summary, nothing delivered).
        for key in ("data_errors", "anomalies", "sftp_attempted", "sftp_ok", *_ORDER):
            assert manual[key] == cli[key], key


# --------------------------------------------------------------------------- #
# Signature + structure pins                                                   #
# --------------------------------------------------------------------------- #
def _is_required_kwonly(fn: object, name: str) -> bool:
    """The pin's ONE predicate: ``name`` is keyword-only AND has no default."""
    param = inspect.signature(fn).parameters[name]  # type: ignore[arg-type]
    return param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty


class TestSignature:
    @pytest.mark.parametrize("name", ["status", "error_category", "entity_outcomes"])
    def test_the_outcome_parameters_are_required_keyword_only(self, name: str) -> None:
        assert _is_required_kwonly(_record_manual_run, name), f"_record_manual_run({name}) must have no default"

    def test_twin_the_pin_rejects_a_default_or_a_positional(self) -> None:
        """Non-vacuity: the SAME predicate refuses a defaulted and a positional ``status``."""

        def _defaulted(result: object, *, status: str = "success") -> None: ...

        def _positional(result: object, status: str) -> None: ...

        assert not _is_required_kwonly(_defaulted, "status")
        assert not _is_required_kwonly(_positional, "status")

    def test_omitting_them_is_a_type_error(self) -> None:
        from src.ui_flet.convert_result import ConvertResult

        result = ConvertResult(status=ConvertStatus.DELIVERED, entity_outcomes=None, delivery_requested=False)
        with pytest.raises(TypeError, match="status|error_category"):
            _record_manual_run(result, sis_type=_SIS, elapsed=0.0, entity_outcomes=None)  # type: ignore[call-arg]


def _is_broad(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or (
        isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}
    )


def _convert_job_sink(source: str) -> tuple[ast.Try, ast.ExceptHandler]:
    """The ``try`` in ``convert_job`` whose handler is the reasoned failure sink."""
    tree = ast.parse(source)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "convert_job")
    lines = source.splitlines()
    found = [
        (node, handler)
        for node in ast.walk(func)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if _is_broad(handler) and "symmetric failure sink" in lines[handler.lineno - 1]
    ]
    assert len(found) == 1, f"expected exactly one symmetric failure sink in convert_job, found {len(found)}"
    return found[0]


def _names_in(nodes: list[ast.stmt]) -> set[str]:
    return {n.id for stmt in nodes for n in ast.walk(stmt) if isinstance(n, ast.Name)}


class TestTheSinkShape:
    """The handler is broad by design — so the pin is on what makes that safe."""

    def test_it_always_re_raises_and_closes_before_the_delivery(self) -> None:
        source = _CONVERT.read_text(encoding="utf-8")
        try_node, handler = _convert_job_sink(source)
        assert "noqa: BLE001" in source.splitlines()[handler.lineno - 1]
        last = handler.body[-1]
        assert isinstance(last, ast.Raise) and last.exc is None, "the sink must end in a BARE raise"
        body_names = _names_in(try_node.body)
        assert "run_transform" in body_names and "DataQualityReport" in body_names, "the sink covers the build"
        assert "SFTPUploader" not in body_names, "the sink must close BEFORE the SFTP leg"

    def test_twin_a_sink_that_swallows_is_caught(self) -> None:
        doctored = textwrap.dedent(
            """
            def convert_job():
                try:
                    run_transform()
                    DataQualityReport()
                except Exception as exc:  # noqa: BLE001 — symmetric failure sink
                    record(exc)
                    return None
            """
        )
        _, handler = _convert_job_sink(doctored)
        last = handler.body[-1]
        assert not (isinstance(last, ast.Raise) and last.exc is None)

    def test_twin_a_sink_wrapping_the_delivery_is_caught(self) -> None:
        doctored = textwrap.dedent(
            """
            def convert_job():
                try:
                    run_transform()
                    SFTPUploader()
                except Exception:  # noqa: BLE001 — symmetric failure sink
                    raise
            """
        )
        try_node, _ = _convert_job_sink(doctored)
        assert "SFTPUploader" in _names_in(try_node.body)
