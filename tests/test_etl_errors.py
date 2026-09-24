"""The typed ETL error taxonomy (plan 0053 S1; ``docs/developer/failure-policy.md`` §6, §8).

Pins, each with the twin that proves it can fail:

* every concrete ``EtlError`` leaf under ``src`` carries a bounded category (a recursive
  walk, so a new leaf without one is RED);
* classification is by TYPE only — a message that SAYS "No usable required input" is
  not ``no_input`` unless the exception is a ``NoUsableInputError`` — and the classifier
  body contains no ``str(`` call and no string-``in`` test (AST, non-vacuous);
* ``RunErrorCategory`` lives in ``src.etl.errors`` alone — no ``from src.etl.pipeline
  import RunErrorCategory`` anywhere (AST, because a grep misses a parenthesised block);
* the persisted value of every category is its plain ``.value`` in BOTH the store's
  ``runs.error_category`` column and the JSON record;
* the missing-column raise sites never put an OBSERVED header in a message or a log line
  (the sentinel sweep).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import pkgutil
import re
import sqlite3
import textwrap
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

import src.etl
import src.etl.extractor as extractor_module
import src.etl.pipeline as pipeline_module
from src.etl import errors
from src.etl.errors import (
    ConfigLoadError,
    EtlError,
    GuardKind,
    NoUsableInputError,
    RunErrorCategory,
    SourceSchemaError,
    available_columns_note,
    classify_error_category,
)
from src.etl.extractor import ExtractionError
from src.etl.pipeline import DeliveryIntegrityError, OutputWriteError, build_run_record
from src.etl.transformer import DataTransformer
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.grades import filter_to_grade_scope
from src.history.store import write_run_record
from src.utils import paths

#: Stands in for a pupil's name in the header row of a headerless file read without
#: its ``headers:`` block — the case that makes an observed header PII (§8).
SENTINEL_PII = "SENTINEL_PII_Zqxv_Pupilname"

_REPO = Path(__file__).resolve().parents[1]

#: The persisted vocabulary. The first eight values ride every ``history.db`` since
#: v3.5.0 and may NEVER change; the last two are S1's additions.
_PERSISTED = {
    "NONE": "none",
    "NO_INPUT": "no_input",
    "NO_OUTPUT": "no_output",
    "INCOMPLETE_ROSTER": "incomplete_roster",
    "CONFIG": "config",
    "DATA": "data",
    "OUTPUT": "output",
    "UNKNOWN": "unknown",
    "SOURCE_SCHEMA": "source_schema",
    "INPUT_UNREADABLE": "input_unreadable",
}


def _schema_error(**overrides: object) -> SourceSchemaError:
    kwargs: dict[str, object] = {
        "entity": "Family",
        "columns": ("Parent Auth / Guardian",),
        "guard": GuardKind.PII_SCOPE,
    }
    kwargs.update(overrides)
    return SourceSchemaError("missing", **kwargs)  # type: ignore[arg-type]


#: One representative instance per concrete leaf, built the way its raise site builds it.
_SAMPLES: dict[type[EtlError], Callable[[], EtlError]] = {
    SourceSchemaError: _schema_error,
    NoUsableInputError: lambda: NoUsableInputError("No usable required input was loaded"),
    ConfigLoadError: lambda: ConfigLoadError("bad mapping"),
    ExtractionError: lambda: ExtractionError("unparseable"),
    OutputWriteError: lambda: OutputWriteError("locked"),
    DeliveryIntegrityError: lambda: DeliveryIntegrityError("nothing produced", RunErrorCategory.NO_OUTPUT),
}

_EXPECTED_CATEGORY: dict[type[EtlError], RunErrorCategory] = {
    SourceSchemaError: RunErrorCategory.SOURCE_SCHEMA,
    NoUsableInputError: RunErrorCategory.NO_INPUT,
    ConfigLoadError: RunErrorCategory.CONFIG,
    ExtractionError: RunErrorCategory.INPUT_UNREADABLE,
    OutputWriteError: RunErrorCategory.OUTPUT,
    DeliveryIntegrityError: RunErrorCategory.NO_OUTPUT,
}


def _src_subclasses(root: type) -> set[type]:
    # ``__subclasses__`` only sees classes whose module has been IMPORTED, so import every
    # module of the ETL package first — a leaf in a module this file happens not to import
    # would otherwise be skipped and the ``walked == set(_SAMPLES)`` pin would stay green.
    for module_info in pkgutil.walk_packages(src.etl.__path__, "src.etl."):
        importlib.import_module(module_info.name)
    found: set[type] = set()
    stack = list(root.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls.__module__.startswith("src."):
            found.add(cls)
        stack.extend(cls.__subclasses__())
    return found


# --------------------------------------------------------------------------- #
# The vocabulary                                                                #
# --------------------------------------------------------------------------- #
class TestVocabulary:
    def test_the_persisted_values_are_exactly_the_pinned_set(self):
        assert {m.name: m.value for m in RunErrorCategory} == _PERSISTED

    @pytest.mark.parametrize("member", list(RunErrorCategory) + list(GuardKind))
    def test_str_of_every_member_is_its_value(self, member):
        """``StrEnum``, not ``(str, Enum)`` — on 3.13 the latter stringifies to
        ``"RunErrorCategory.NONE"``, and the store stringifies what it is handed."""
        assert str(member) == member.value
        assert f"{member}" == member.value

    def test_a_str_Enum_would_have_failed_the_pin_above(self):
        """Twin: the pin above is not vacuous — the shape it replaced DOES differ."""
        from enum import Enum

        class _Legacy(str, Enum):
            NONE = "none"

        assert str(_Legacy.NONE) != _Legacy.NONE.value

    def test_guard_kinds_spell_the_site_tags(self):
        assert {m.value for m in GuardKind} == {"pii_scope", "join_key"}


# --------------------------------------------------------------------------- #
# Every leaf carries a category                                                 #
# --------------------------------------------------------------------------- #
class TestEveryLeafHasACategory:
    def test_the_walk_finds_every_src_leaf_and_the_sample_table_covers_it(self):
        walked = _src_subclasses(EtlError)
        # Non-vacuity: the walk reaches the leaves defined in three different modules.
        assert {SourceSchemaError, ExtractionError, DeliveryIntegrityError, OutputWriteError} <= walked
        assert walked == set(_SAMPLES), (
            "a new EtlError leaf needs a representative sample and an expected category here"
        )

    @pytest.mark.parametrize("cls", list(_SAMPLES), ids=lambda c: c.__name__)
    def test_each_leaf_carries_its_bounded_non_unknown_category(self, cls):
        instance = _SAMPLES[cls]()
        assert isinstance(instance.category, RunErrorCategory)
        assert instance.category is _EXPECTED_CATEGORY[cls]
        assert instance.category is not RunErrorCategory.UNKNOWN

    def test_the_base_alone_is_unknown(self):
        assert EtlError("x").category is RunErrorCategory.UNKNOWN

    def test_the_backward_compatible_bases_are_kept(self):
        """Every pre-S1 ``except ValueError`` / ``except RuntimeError`` still catches."""
        assert isinstance(_schema_error(), ValueError)
        assert isinstance(ConfigLoadError("x"), ValueError)
        assert isinstance(NoUsableInputError("x"), RuntimeError)
        assert isinstance(OutputWriteError("x"), RuntimeError)
        assert isinstance(DeliveryIntegrityError("x", RunErrorCategory.NO_OUTPUT), RuntimeError)

    def test_the_available_columns_note_is_singular_for_one_column(self):
        assert available_columns_note(1) == "the source has 1 column; observed header names are not logged"
        assert available_columns_note(2) == "the source has 2 columns; observed header names are not logged"

    def test_a_legacy_string_category_is_coerced(self):
        err = EtlError("x", category="output")
        assert err.category is RunErrorCategory.OUTPUT

    def test_an_unknown_string_category_fails_loudly_at_construction(self):
        with pytest.raises(ValueError):
            EtlError("x", category="not_a_category")
        with pytest.raises(ValueError):
            DeliveryIntegrityError("x", "")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# SourceSchemaError's required facts                                            #
# --------------------------------------------------------------------------- #
class TestSourceSchemaError:
    @pytest.mark.parametrize("dropped", ["entity", "columns", "guard"])
    def test_every_fact_is_required(self, dropped):
        kwargs: dict[str, object] = {"entity": "Family", "columns": ("C",), "guard": GuardKind.PII_SCOPE}
        del kwargs[dropped]
        with pytest.raises(TypeError):
            SourceSchemaError("m", **kwargs)  # type: ignore[arg-type]

    def test_the_facts_are_keyword_only(self):
        with pytest.raises(TypeError):
            SourceSchemaError("m", "Family", ("C",), GuardKind.PII_SCOPE)  # type: ignore[misc]

    def test_empty_columns_are_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            _schema_error(columns=())

    def test_a_blank_entity_is_refused(self):
        with pytest.raises(ValueError, match="entity"):
            _schema_error(entity="  ")

    def test_an_unknown_guard_is_refused(self):
        with pytest.raises(ValueError):
            _schema_error(guard="whatever")

    def test_the_facts_are_carried(self):
        err = _schema_error(columns=["A", "B"], guard="join_key")
        assert err.columns == ("A", "B") and isinstance(err.columns, tuple)
        assert err.guard is GuardKind.JOIN_KEY
        assert err.entity == "Family"


# --------------------------------------------------------------------------- #
# Classification by TYPE                                                        #
# --------------------------------------------------------------------------- #
class TestClassifyByTypeOnly:
    def test_a_typed_no_input_error_is_no_input(self):
        assert classify_error_category(NoUsableInputError("anything at all")) is RunErrorCategory.NO_INPUT

    def test_the_twin_the_same_TEXT_in_an_untyped_error_is_unknown(self):
        exc = RuntimeError("No usable required input was loaded — every required file is missing")
        assert classify_error_category(exc) is RunErrorCategory.UNKNOWN

    def test_a_source_schema_error_is_source_schema_not_data(self):
        assert classify_error_category(_schema_error()) is RunErrorCategory.SOURCE_SCHEMA

    def test_the_twin_a_plain_value_error_is_data(self):
        assert classify_error_category(ValueError("missing column 'x'")) is RunErrorCategory.DATA

    def test_an_extraction_error_is_input_unreadable(self):
        assert classify_error_category(ExtractionError("broken.txt")) is RunErrorCategory.INPUT_UNREADABLE

    def test_file_not_found_is_config(self):
        assert classify_error_category(FileNotFoundError("x")) is RunErrorCategory.CONFIG

    def test_a_raw_key_error_is_unknown(self):
        assert classify_error_category(KeyError("grade")) is RunErrorCategory.UNKNOWN

    def test_carriers_answer_with_their_instance_category(self):
        fault = DeliveryIntegrityError("x", RunErrorCategory.INCOMPLETE_ROSTER)
        assert classify_error_category(fault) is RunErrorCategory.INCOMPLETE_ROSTER
        assert classify_error_category(OutputWriteError("x")) is RunErrorCategory.OUTPUT
        assert classify_error_category(ConfigLoadError("x")) is RunErrorCategory.CONFIG


def _text_reading_nodes(func_source: str, name: str) -> list[str]:
    """Every ``str(`` call and every ``<str const> in …`` / ``… in <str const>`` in ``name``."""
    tree = ast.parse(textwrap.dedent(func_source))
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(funcs) == 1, f"{name} not found — the pin would be vacuous"
    hits: list[str] = []
    for node in ast.walk(funcs[0]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
            hits.append(f"str( call at line {node.lineno}")
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            operands = [node.left, *node.comparators]
            if any(isinstance(o, ast.Constant) and isinstance(o.value, str) for o in operands):
                hits.append(f"string membership test at line {node.lineno}")
    return hits


class TestTheClassifierReadsNoText:
    def test_classify_error_category_has_no_str_call_and_no_substring_test(self):
        source = inspect.getsource(errors.classify_error_category)
        assert _text_reading_nodes(source, "classify_error_category") == []

    def test_the_twin_the_retired_text_matching_classifier_is_caught(self):
        """Non-vacuity: the pre-S1 classifier's shape trips BOTH detectors."""
        retired = """
        def classify_error_category(exc):
            if isinstance(exc, RuntimeError) and "No usable required input" in str(exc):
                return "no_input"
            return "unknown"
        """
        hits = _text_reading_nodes(retired, "classify_error_category")
        assert any(h.startswith("str(") for h in hits)
        assert any(h.startswith("string membership") for h in hits)


# --------------------------------------------------------------------------- #
# The move: no re-export, no stale importer                                     #
# --------------------------------------------------------------------------- #
_MOVED_NAMES = {"RunErrorCategory", "_classify_error_category", "classify_error_category"}


def _imports_of(tree: ast.AST, module: str) -> list[tuple[str, int]]:
    return [
        (alias.name, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    ]


def _python_files() -> list[Path]:
    return [p for base in ("src", "tests") for p in (_REPO / base).rglob("*.py") if "__pycache__" not in p.parts]


class TestTheTaxonomyMovedOutOfThePipeline:
    def test_pipeline_defines_neither_the_enum_nor_a_classifier(self):
        tree = ast.parse(Path(pipeline_module.__file__).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
        assert not defined & _MOVED_NAMES
        # Non-vacuity: the same walk does see the pipeline's own definitions.
        assert {"DeliveryIntegrityError", "build_run_record", "run_pipeline"} <= defined

    def test_no_module_imports_a_moved_name_from_the_pipeline(self):
        offenders: list[str] = []
        errors_importers = 0
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for name, line in _imports_of(tree, "src.etl.pipeline"):
                if name in _MOVED_NAMES:
                    offenders.append(f"{path.relative_to(_REPO)}:{line} imports {name}")
            errors_importers += sum(1 for name, _ in _imports_of(tree, "src.etl.errors") if name == "RunErrorCategory")
        assert offenders == []
        # Non-vacuity: the sweep DOES see ImportFrom nodes naming the enum — including
        # the parenthesised-block importers (convert.py) that a grep would miss.
        assert errors_importers >= 4

    def test_the_twin_a_parenthesised_stale_import_is_caught(self):
        stale = "from src.etl.pipeline import (\n    advisory_expected_files,\n    RunErrorCategory,\n)\n"
        names = [n for n, _ in _imports_of(ast.parse(stale), "src.etl.pipeline")]
        assert "RunErrorCategory" in names


# --------------------------------------------------------------------------- #
# One normalisation point: store column == record JSON                          #
# --------------------------------------------------------------------------- #
class TestPersistedCategoryParity:
    @pytest.mark.parametrize("member", list(RunErrorCategory))
    def test_store_column_equals_record_json_for_every_member(self, member):
        record = build_run_record(
            status="failed",
            elapsed=0.0,
            entity_counts={},
            source="cli",
            sis_type="myedbc",
            error_category=member,
            entity_outcomes=None,
        )
        assert type(record["error_category"]) is str, "normalised to a plain str, not the enum"
        assert write_run_record(record, source="cli") is True
        with sqlite3.connect(paths.user_history_db()) as conn:
            column, blob = conn.execute("SELECT error_category, record FROM runs").fetchone()
        assert column == json.loads(blob)["error_category"] == member.value

    def test_a_legacy_value_string_normalises_identically(self):
        record = build_run_record(
            status="failed",
            elapsed=0.0,
            entity_counts={},
            source="cli",
            sis_type="",
            error_category="output",
            entity_outcomes=None,
        )
        assert record["error_category"] == "output" and type(record["error_category"]) is str

    def test_an_unknown_category_string_is_refused_at_the_one_normalisation_point(self):
        with pytest.raises(ValueError):
            build_run_record(
                status="failed",
                elapsed=0.0,
                entity_counts={},
                source="cli",
                sis_type="",
                error_category="bogus",
                entity_outcomes=None,
            )


# --------------------------------------------------------------------------- #
# §8 sentinel sweep — the four S1 raise sites                                   #
# --------------------------------------------------------------------------- #
def _students(field_map_extra: dict, global_config_extra: dict, frame: pd.DataFrame) -> None:
    transformer = DataTransformer()
    transformer.set_school_year(2025, "08-25", "07-25")
    mapping = {
        "source_files": {"student_demographic": "Demo.txt"},
        "field_map": {
            "User ID": "Student Number",
            "First Name": "Legal First Name",
            "Last Name": "Legal Surname",
            "SchoolCode": "School Number",
            "EnrollStatus": None,
            **field_map_extra,
        },
    }
    gc = {"academic_start_month_day": "08-25", "academic_end_month_day": "07-25", **global_config_extra}
    transformer.transform(frame, mapping, "Students", {"Demo.txt": frame}, gc)


def _pupil_frame() -> pd.DataFrame:
    # The sentinel is the FIRST header, as it would be when row 1 is a pupil.
    return pd.DataFrame(
        {
            SENTINEL_PII.lower(): ["x"],
            "student number": ["S1"],
            "legal first name": ["A"],
            "legal surname": ["B"],
            "school number": ["100"],
            "enrolment status": ["Active"],
        }
    )


def _row_filters() -> None:
    BaseTransformer.apply_row_filters(
        _pupil_frame(), [{"column": "Parent Auth / Guardian", "include": ["Y"]}], "Family"
    )


def _cross_enrollment() -> None:
    _students({}, {"cross_enrollment": {"collapse": True, "home_school_column": "Home School Number"}}, _pupil_frame())


def _derived_dates() -> None:
    email = {
        "format": "{legal first name}{admission yy}@example.org",
        "derived_dates": {"admission yy": {"column": "Admission Date", "date_format": "yy"}},
    }
    _students({"Email Address": email}, {}, _pupil_frame())


def _grade_scope() -> None:
    filter_to_grade_scope(_pupil_frame(), "grade", {"KG"}, caller="Students")


_SITES: dict[str, tuple[Callable[[], None], str, GuardKind, tuple[str, ...]]] = {
    "row_filters": (_row_filters, "Family", GuardKind.PII_SCOPE, ("Parent Auth / Guardian",)),
    "cross_enrollment": (_cross_enrollment, "Students", GuardKind.JOIN_KEY, ("Home School Number",)),
    "derived_dates": (_derived_dates, "Students", GuardKind.JOIN_KEY, ("Admission Date",)),
    "grade_scope": (_grade_scope, "Students", GuardKind.PII_SCOPE, ("grade",)),
}


class TestNoObservedHeaderReachesAMessageOrALog:
    @pytest.mark.parametrize("site", list(_SITES))
    def test_the_site_raises_typed_and_never_echoes_the_sentinel(self, site, caplog):
        call, entity, guard, columns = _SITES[site]
        with caplog.at_level(logging.DEBUG), pytest.raises(SourceSchemaError) as exc:
            call()
        err = exc.value
        assert (err.entity, err.guard, err.columns) == (entity, guard, columns)
        assert err.category is RunErrorCategory.SOURCE_SCHEMA
        assert SENTINEL_PII.lower() not in str(err).lower()
        assert SENTINEL_PII.lower() not in caplog.text.lower()
        # The message carries the COUNT the policy allows instead (the frame the site
        # sees may have gained a derived column by then, so the number is not fixed).
        assert re.search(r"the source has \d+ columns", str(err))

    def test_the_twin_the_sentinel_detector_fires_on_the_retired_message_shape(self):
        """Non-vacuity: the pre-S1 message shape (``Available: {sorted(df.columns)}``)
        over the same frame WOULD contain the sentinel, so the asserts above can fail."""
        retired = f"row_filter column not found. Available: {sorted(_pupil_frame().columns)}"
        assert SENTINEL_PII.lower() in retired.lower()


def test_the_extractor_error_is_the_extractor_module_class():
    """``ExtractionError`` was re-parented IN PLACE (no second class, no alias)."""
    assert extractor_module.ExtractionError is ExtractionError
    assert ExtractionError.__module__ == "src.etl.extractor"
