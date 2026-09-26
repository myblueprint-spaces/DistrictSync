"""Architecture fitness functions (plan 0053 S13a; ``docs/developer/failure-policy.md`` §11, P16).

Structural rules the layering depends on, checked by AST over the real tree — no new
dependency. Each rule has:

* a DECLARED target registry (paths / functions named below), and a registry entry whose
  file or function is missing fails LOUDLY — a renamed module can never turn a rule vacuous;
* a detector self-test on SYNTHETIC violating source, proving the detector fires;
* a positive twin on the REAL tree where one exists (the detector finds a known target).

The rules:

(a) ``src/etl``, ``src/config``, ``src/history`` and ``src/quality`` never import ``flet`` or
    ``src.ui_flet`` — directly or through any chain of ``src`` imports (package ``__init__``
    edges included);
(b) the explicit ``FLET_FREE_MODULES`` list never reaches ``flet`` the same way; every other
    non-screen ``src/ui_flet`` module is declared ``FLET_BOUND`` and really does import it, so
    a new module must be classified and a module that loses its flet import must move lists;
(c) nothing under ``src/etl/transformers`` reaches ``src.etl.pipeline`` (the orchestrator
    imports transformers, never the reverse — which is what let ``RunErrorCategory`` leave
    ``pipeline.py``);
(d) the one entity-scope boundary: exactly ONE broad handler in ``pipeline.run_transform`` (the
    bulkhead, ``Exception`` never ``BaseException``, CRITICAL branch a bare ``raise``) and none
    under ``src/etl/transformers`` outside the field-map engine — moved here from
    ``tests/test_pipeline_entity_isolation.py`` (S4) unchanged in strength;
(e) the exception CLASSIFIERS read no text: no ``str(``/``repr(``/``format(`` call, no
    ``.args``, no f-string, no string method and no membership test against string
    constants — moved here from ``tests/test_etl_errors.py`` (S1) and widened from one
    classifier to the three that exist;
(f) every broad handler in the four layers of (a) carries ``# noqa: BLE001 — <reason>`` with a
    reason from the CLOSED vocabulary ``BLE_REASONS``, which ``failure-policy.md`` §11's
    ``ble-reasons`` table mirrors (parity + doctored twin). ruff's ``BLE`` rule (selected in
    ``pyproject.toml`` for exactly these layers) cannot do this alone: it skips a handler
    that re-raises or logs, and it never reads the reason;
(g) new shared transformer behaviour goes in a COMPOSED module (``columns.py``, ``notes.py``,
    ``grades.py`` …), never a new ``BaseTransformer`` member (P10, failure-policy §9): the class
    body's member set is a declared, frozen registry — a new member is red, and so is a stale
    entry (a deletion updates the registry, which is how S9's ``resolve_column`` left).

Related pins that stay beside the code they protect (not duplicated here): the
``KeyError``/``MergeError`` handler pin (``tests/test_require_columns.py``, S10), the column
resolver pins (``tests/test_column_resolution_pin.py``, S9) and Convert's symmetric failure
sink (``tests/test_convert_failure_record.py``, S5 — ``src/ui_flet``, outside BLE scope).
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from tests.test_failure_policy_parity import _doc_text, _table

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"

# --------------------------------------------------------------------------- #
# Declared registries                                                           #
# --------------------------------------------------------------------------- #
#: (a) + (f): the layers that never import the UI and whose broad handlers are reasoned.
LAYER_ROOTS: tuple[str, ...] = ("src/etl", "src/config", "src/history", "src/quality")

#: (b): modules that must never reach flet. The pure (COUNTED) modules of the ETL taxonomy
#: and the UI's decision layer. NOT ``src/ui_flet/theme.py`` (imports flet) nor
#: ``src/ui_flet/job_runner.py`` (imports flet) — both are in ``FLET_BOUND``.
FLET_FREE_MODULES: tuple[str, ...] = (
    "src/etl/errors.py",
    "src/etl/outcomes.py",
    "src/etl/preflight.py",
    "src/etl/transformers/columns.py",
    "src/etl/transformers/notes.py",
    "src/ui_flet/about.py",
    "src/ui_flet/config_editor.py",
    "src/ui_flet/convert_output.py",
    "src/ui_flet/convert_result.py",
    "src/ui_flet/failure_copy.py",
    "src/ui_flet/geometry.py",
    "src/ui_flet/grant_access.py",
    "src/ui_flet/handover_result.py",
    "src/ui_flet/home_status.py",
    "src/ui_flet/humanize.py",
    "src/ui_flet/identity_gate.py",
    "src/ui_flet/mapping_catalog.py",
    "src/ui_flet/nav.py",
    "src/ui_flet/run_history.py",
    "src/ui_flet/schedule_probe.py",
    "src/ui_flet/schedule_status.py",
    "src/ui_flet/setup_errors.py",
    "src/ui_flet/setup_flow.py",
    "src/ui_flet/setup_gates.py",
    "src/ui_flet/sftp_copy.py",
    "src/ui_flet/tokens.py",
    "src/ui_flet/verdict.py",
)

#: (b): the non-screen ``src/ui_flet`` modules that DO import flet (``screens/`` is all view glue).
FLET_BOUND: tuple[str, ...] = (
    "src/ui_flet/components.py",
    "src/ui_flet/filepicker.py",
    "src/ui_flet/grant_window.py",
    "src/ui_flet/job_runner.py",
    "src/ui_flet/launcher.py",
    "src/ui_flet/nav_rail.py",
    "src/ui_flet/picker_field.py",
    "src/ui_flet/shell.py",
    "src/ui_flet/theme.py",
)

#: (c) + (d)
TRANSFORMERS_DIR = "src/etl/transformers"
PIPELINE = "src/etl/pipeline.py"
PIPELINE_MODULE = "src.etl.pipeline"
#: (d): the field-map engine's per-cell and per-column isolation, each RECORDED to data errors.
FIELD_MAP_ENGINE = frozenset({"apply_field_map", "_apply_transform_resilient"})
FIELD_MAP_ENGINE_FILE = "src/etl/transformers/base.py"

#: (e): every function that maps an exception to a category, reason or card.
CLASSIFIERS: tuple[tuple[str, str], ...] = (
    ("src/etl/errors.py", "classify_error_category"),
    ("src/etl/outcomes.py", "reason_for"),
    ("src/ui_flet/failure_copy.py", "error_card_copy"),
)

#: (f): the CLOSED vocabulary of reasons a broad handler in the four layers may give.
#: Mirrored by failure-policy.md §11's ``ble-reasons`` table (pinned below).
BLE_REASONS: Mapping[str, str] = {
    "entity bulkhead": "the ONE entity-scope boundary in run_transform (failure-policy §2)",
    "re-raised": "logs, records or rolls back, then re-raises the original with a bare raise",
    "total by contract": "a documented-total function; any failure degrades to a stated default",
    "advisory only": "source observation and labels; a raise may not change the run",
    "best-effort side effect": "recording or tidying that must never propagate or mask a failure",
    "field-map isolation": "per-cell / per-column isolation, recorded to the run's data errors",
    "failure returned as a result": "converted to a returned failure the caller surfaces (exit 3)",
}

_NOQA = re.compile(r"#\s*noqa:\s*BLE001 — (?P<reason>[^;,]+?)(?:[;,] \S.*)?$")


def _path(relative: str) -> Path:
    """A declared registry path — MISSING fails loudly, never skips."""
    path = _REPO / relative
    assert path.exists(), f"declared fitness target {relative!r} does not exist — update the registry"
    return path


def _py_files(root: str) -> list[Path]:
    files = [p for p in sorted(_path(root).rglob("*.py")) if "__pycache__" not in p.parts]
    assert files, f"declared fitness root {root!r} holds no Python files"
    return files


# --------------------------------------------------------------------------- #
# The import graph                                                              #
# --------------------------------------------------------------------------- #
def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports_of(source: str, module: str, *, is_package: bool = False) -> set[str]:
    """Every dotted name ``module`` imports ANYWHERE (function-local included), relative resolved.

    ``from a.b import c`` yields both ``a.b`` and ``a.b.c`` (``c`` may be a submodule).
    """
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            found.add(target)
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found


def _with_parents(names: set[str]) -> set[str]:
    """Importing ``a.b.c`` runs ``a/__init__`` and ``a/b/__init__`` first — those are edges too."""
    out = set(names)
    for name in names:
        parts = name.split(".")
        out.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return out


def _graph_from_sources(sources: Mapping[str, str], packages: frozenset[str] = frozenset()) -> dict[str, set[str]]:
    return {
        module: _with_parents(_imports_of(source, module, is_package=module in packages))
        for module, source in sources.items()
    }


def _src_graph() -> dict[str, set[str]]:
    files = [p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) > 50, "non-vacuity: the src tree was found"
    sources = {_module_name(p): p.read_text(encoding="utf-8") for p in files}
    packages = frozenset(_module_name(p) for p in files if p.name == "__init__.py")
    return _graph_from_sources(sources, packages)


def _chain_to(graph: Mapping[str, set[str]], start: str, forbidden: Callable[[str], bool]) -> list[str] | None:
    """The import chain from ``start`` to the first forbidden name, or ``None`` (breadth-first)."""
    parents: dict[str, str | None] = {start: None}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for name in sorted(graph.get(current, ())):
            if forbidden(name):
                chain = [name, current]
                while (up := parents[chain[-1]]) is not None:
                    chain.append(up)
                return chain[::-1]
            if name in graph and name not in parents:
                parents[name] = current
                queue.append(name)
    return None


def _is_flet(name: str) -> bool:
    return name == "flet" or name.startswith("flet.")


def _is_ui(name: str) -> bool:
    return _is_flet(name) or name == "src.ui_flet" or name.startswith("src.ui_flet.")


def _is_pipeline(name: str) -> bool:
    return name == PIPELINE_MODULE or name.startswith(f"{PIPELINE_MODULE}.")


@pytest.fixture(scope="module")
def src_graph() -> dict[str, set[str]]:
    return _src_graph()


class TestImportDetector:
    """The graph walk itself, on synthetic sources — every rule below trusts it."""

    def test_a_direct_import_is_found(self) -> None:
        graph = _graph_from_sources({"src.etl.x": "import flet as ft\n"})
        assert _chain_to(graph, "src.etl.x", _is_flet) == ["src.etl.x", "flet"]

    def test_a_transitive_import_is_found_with_its_chain(self) -> None:
        graph = _graph_from_sources(
            {
                "src.etl.x": "from src.etl.helper import thing\n",
                "src.etl.helper": "def f():\n    from src.ui_flet.components import card\n",
            }
        )
        chain = _chain_to(graph, "src.etl.x", _is_ui)
        assert chain is not None and chain[:2] == ["src.etl.x", "src.etl.helper"]
        assert chain[-1].startswith("src.ui_flet")

    def test_a_relative_import_resolves_to_the_pipeline(self) -> None:
        graph = _graph_from_sources({"src.etl.transformers.x": "from ..pipeline import run_transform\n"})
        assert _chain_to(graph, "src.etl.transformers.x", _is_pipeline) is not None

    def test_from_package_import_module_is_found(self) -> None:
        graph = _graph_from_sources({"src.etl.transformers.x": "from src.etl import pipeline\n"})
        assert _chain_to(graph, "src.etl.transformers.x", _is_pipeline) is not None

    def test_a_package_init_is_an_edge(self) -> None:
        """Importing ``src.etl.transformers.base`` runs ``src.etl/__init__`` — if THAT reached the
        orchestrator, every transformer would."""
        graph = _graph_from_sources(
            {
                "src.etl.transformers.x": "from src.etl.transformers.base import B\n",
                "src.etl": "from src.etl import pipeline\n",
            },
            packages=frozenset({"src.etl"}),
        )
        assert _chain_to(graph, "src.etl.transformers.x", _is_pipeline) == [
            "src.etl.transformers.x",
            "src.etl",
            "src.etl.pipeline",
        ]

    def test_a_clean_module_is_not_flagged(self) -> None:
        graph = _graph_from_sources({"src.etl.x": "import pandas as pd\nfrom src.etl.errors import EtlError\n"})
        assert _chain_to(graph, "src.etl.x", _is_ui) is None
        assert _chain_to(graph, "src.etl.x", _is_pipeline) is None


# --------------------------------------------------------------------------- #
# (a) The ETL/config/history/quality layers never import the UI                 #
# --------------------------------------------------------------------------- #
class TestRuleA_LayersNeverImportTheUi:
    def test_no_layer_module_reaches_flet_or_ui_flet(self, src_graph) -> None:
        modules = [_module_name(p) for root in LAYER_ROOTS for p in _py_files(root)]
        assert len(modules) > 40, "non-vacuity: the four layers were enumerated"
        offenders = {m: chain for m in modules if (chain := _chain_to(src_graph, m, _is_ui))}
        assert offenders == {}, "failure-policy §11: the ETL layers never import the UI"

    def test_positive_twin_the_real_graph_has_the_edges_it_should(self, src_graph) -> None:
        """Non-vacuity: the same walk sees a real UI module reach flet and a real layer
        module reach its dependencies."""
        assert _chain_to(src_graph, "src.ui_flet.shell", _is_flet) is not None
        assert _chain_to(src_graph, "src.etl.pipeline", lambda n: n == "src.etl.transformers.base") is not None


# --------------------------------------------------------------------------- #
# (b) The explicit FLET_FREE_MODULES list never imports flet                    #
# --------------------------------------------------------------------------- #
class TestRuleB_FletFreeModules:
    def test_every_listed_module_is_flet_free(self, src_graph) -> None:
        offenders = {
            rel: chain
            for rel in FLET_FREE_MODULES
            if (chain := _chain_to(src_graph, _module_name(_path(rel)), _is_flet))
        }
        assert offenders == {}, "a FLET_FREE module must be importable without a display"

    def test_every_bound_module_really_imports_flet(self, src_graph) -> None:
        """The ratchet's other half: a module that stops importing flet moves to FLET_FREE_MODULES."""
        free_now = [rel for rel in FLET_BOUND if _chain_to(src_graph, _module_name(_path(rel)), _is_flet) is None]
        assert free_now == [], f"these no longer import flet — move them to FLET_FREE_MODULES: {free_now}"

    def test_the_theme_and_job_runner_exclusions_are_measured_not_assumed(self, src_graph) -> None:
        for rel in ("src/ui_flet/theme.py", "src/ui_flet/job_runner.py"):
            assert rel not in FLET_FREE_MODULES
            assert _chain_to(src_graph, _module_name(_path(rel)), _is_flet) is not None

    def test_every_non_screen_ui_module_is_classified_exactly_once(self) -> None:
        ui = sorted(
            str(p.relative_to(_REPO).as_posix()) for p in _path("src/ui_flet").glob("*.py") if p.name != "__init__.py"
        )
        declared = [rel for rel in (*FLET_FREE_MODULES, *FLET_BOUND) if rel.startswith("src/ui_flet/")]
        assert len(declared) == len(set(declared)), "a module is listed twice"
        assert sorted(declared) == ui, "classify every src/ui_flet module as FLET_FREE or FLET_BOUND"

    def test_detector_self_test_a_listed_module_that_imports_flet_is_red(self) -> None:
        graph = _graph_from_sources(
            {"src.ui_flet.verdict": "from src.ui_flet.theme import x\n", "src.ui_flet.theme": "import flet\n"}
        )
        assert _chain_to(graph, "src.ui_flet.verdict", _is_flet) == [
            "src.ui_flet.verdict",
            "src.ui_flet.theme",
            "flet",
        ]


# --------------------------------------------------------------------------- #
# (c) Transformers never import the orchestrator                                #
# --------------------------------------------------------------------------- #
class TestRuleC_TransformersNeverImportThePipeline:
    def test_no_transformer_reaches_the_pipeline(self, src_graph) -> None:
        _path(PIPELINE)
        modules = [_module_name(p) for p in _py_files(TRANSFORMERS_DIR)]
        assert len(modules) >= 10, "non-vacuity: the transformer package was enumerated"
        offenders = {m: chain for m in modules if (chain := _chain_to(src_graph, m, _is_pipeline))}
        assert offenders == {}, "the orchestrator imports transformers, never the reverse"

    def test_positive_twin_the_pipeline_is_a_node_the_walk_can_reach(self, src_graph) -> None:
        assert PIPELINE_MODULE in src_graph
        assert _chain_to(src_graph, "src.main", _is_pipeline) is not None

    def test_detector_self_test_a_transformer_importing_the_pipeline_is_red(self) -> None:
        graph = _graph_from_sources({"src.etl.transformers.family": "from src.etl.pipeline import RunErrorCategory\n"})
        assert _chain_to(graph, "src.etl.transformers.family", _is_pipeline) is not None


# --------------------------------------------------------------------------- #
# (d) The one entity-scope boundary (moved from test_pipeline_entity_isolation) #
# --------------------------------------------------------------------------- #
def _is_broad(handler: ast.ExceptHandler) -> bool:
    """A handler that catches everything an entity transform could raise (or more)."""
    if handler.type is None:
        return True
    names = list(handler.type.elts) if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in {"Exception", "BaseException"} for n in names)


def _broad_handlers_in(source: str, function: str) -> list[ast.ExceptHandler]:
    tree = ast.parse(source)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function]
    assert len(funcs) == 1, f"{function} not found exactly once — the pin would be vacuous"
    return [node for node in ast.walk(funcs[0]) if isinstance(node, ast.ExceptHandler) and _is_broad(node)]


def _broad_handlers_outside(source: str, allowed: frozenset[str]) -> list[tuple[str, int]]:
    """``(enclosing function, line)`` for every broad handler NOT inside an ``allowed`` function."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else enclosing
            if isinstance(child, ast.ExceptHandler) and _is_broad(child) and enclosing not in allowed:
                found.append((enclosing, child.lineno))
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


class TestRuleD_TheOneBoundary:
    def test_run_transform_has_exactly_one_broad_handler_and_it_is_the_bulkhead(self) -> None:
        source = _path(PIPELINE).read_text(encoding="utf-8")
        handlers = _broad_handlers_in(source, "run_transform")
        assert len(handlers) == 1, "failure-policy §11: exactly ONE entity-scope broad handler, in run_transform"
        (bulkhead,) = handlers
        assert isinstance(bulkhead.type, ast.Name) and bulkhead.type.id == "Exception", "never BaseException"
        line = source.splitlines()[bulkhead.lineno - 1]
        assert "noqa: BLE001 — entity bulkhead, failure-policy §2" in line
        # Its CRITICAL branch re-raises BARE — the same object, never a wrapped one.
        assert any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(bulkhead))

    def test_the_bulkhead_reason_is_used_nowhere_else(self) -> None:
        """``entity bulkhead`` names ONE handler; a second one claiming it is a second boundary."""
        uses = [
            f"{p.relative_to(_REPO)}:{n}"
            for root in LAYER_ROOTS
            for p in _py_files(root)
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if (m := _NOQA.search(line)) and m.group("reason") == "entity bulkhead"
        ]
        assert len(uses) == 1 and uses[0].replace("\\", "/").startswith(PIPELINE), uses

    def test_negative_twin_a_second_broad_handler_is_counted(self) -> None:
        source = _path(PIPELINE).read_text(encoding="utf-8")
        doctored = source.replace(
            "    transformer = DataTransformer()\n",
            "    try:\n        transformer = DataTransformer()\n    except Exception:\n        raise\n",
            1,
        )
        assert doctored != source, "non-vacuity: the doctoring anchor was found"
        assert len(_broad_handlers_in(doctored, "run_transform")) == 2

    def test_no_transformer_catches_broadly_outside_the_field_map_engine(self) -> None:
        offenders = {
            path.name: _broad_handlers_outside(path.read_text(encoding="utf-8"), FIELD_MAP_ENGINE)
            for path in _py_files(TRANSFORMERS_DIR)
        }
        assert {name: found for name, found in offenders.items() if found} == {}, (
            "failure-policy §2: a transformer RAISES; only run_transform decides entity scope"
        )

    def test_positive_twin_the_sweep_finds_the_engines_two_handlers(self) -> None:
        base = _path(FIELD_MAP_ENGINE_FILE).read_text(encoding="utf-8")
        in_engine = [fn for fn in FIELD_MAP_ENGINE if _broad_handlers_in(base, fn)]
        assert sorted(in_engine) == sorted(FIELD_MAP_ENGINE)
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
        assert _broad_handlers_outside(doctored, FIELD_MAP_ENGINE) == [("transform", 5)]

    def test_negative_twin_bare_and_base_exception_handlers_are_broad(self) -> None:
        for clause in ("except:", "except BaseException:", "except (ValueError, Exception):"):
            source = f"def f():\n    try:\n        pass\n    {clause}\n        pass\n"
            assert _broad_handlers_outside(source, frozenset()) == [("f", 4)], clause


# --------------------------------------------------------------------------- #
# (e) Classifiers read no text (moved from test_etl_errors, widened)            #
# --------------------------------------------------------------------------- #
_TEXT_CALLS = frozenset({"str", "repr", "format"})
_TEXT_METHODS = frozenset(
    {"startswith", "endswith", "find", "rfind", "index", "count", "lower", "upper", "casefold", "split", "strip"}
)


def _is_str_constant(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and any(_is_str_constant(e) for e in node.elts)


def _text_reading_nodes(source: str, name: str) -> list[str]:
    """Every way ``name``'s body could read an exception's TEXT rather than its type."""
    tree = ast.parse(textwrap.dedent(source))
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(funcs) == 1, f"{name} not found — the pin would be vacuous"
    hits: list[str] = []
    for node in ast.walk(funcs[0]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _TEXT_CALLS:
            hits.append(f"{node.func.id}( call at line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _TEXT_METHODS:
            hits.append(f"string method .{node.func.attr}( at line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "args":
            hits.append(f".args read at line {node.lineno}")
        if isinstance(node, ast.JoinedStr) and any(isinstance(v, ast.FormattedValue) for v in node.values):
            hits.append(f"f-string at line {node.lineno}")
        if (
            isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
            and any(_is_str_constant(o) for o in [node.left, *node.comparators])
        ):
            hits.append(f"string membership test at line {node.lineno}")
    return hits


class TestRuleE_ClassifiersReadNoText:
    @pytest.mark.parametrize(("relative", "function"), CLASSIFIERS, ids=[f for _, f in CLASSIFIERS])
    def test_the_classifier_reads_no_text(self, relative: str, function: str) -> None:
        assert _text_reading_nodes(_path(relative).read_text(encoding="utf-8"), function) == []

    def test_the_twin_the_retired_text_matching_classifier_is_caught(self) -> None:
        """Non-vacuity: the pre-S1 classifier's shape trips BOTH original detectors."""
        retired = """
        def classify_error_category(exc):
            if isinstance(exc, RuntimeError) and "No usable required input" in str(exc):
                return "no_input"
            return "unknown"
        """
        hits = _text_reading_nodes(retired, "classify_error_category")
        assert any(h.startswith("str(") for h in hits)
        assert any(h.startswith("string membership") for h in hits)

    def test_the_twin_every_widened_shape_is_caught(self) -> None:
        shapes = {
            "repr(": "    return repr(exc)\n",
            "string method": "    return exc.args[0].startswith('x')\n",
            ".args": "    return exc.args\n",
            "f-string": "    return f'{exc}'\n",
            "string membership": "    return type(exc).__name__ in ('KeyError', 'ValueError')\n",
        }
        for label, body in shapes.items():
            hits = _text_reading_nodes(f"def reason_for(exc):\n{body}", "reason_for")
            assert any(h.startswith(label) for h in hits), (label, hits)

    def test_the_twin_a_missing_classifier_is_loud_not_vacuous(self) -> None:
        with pytest.raises(AssertionError, match="not found"):
            _text_reading_nodes("def something_else(exc):\n    return 1\n", "reason_for")


# --------------------------------------------------------------------------- #
# (f) Every broad handler in the four layers gives a reason from the vocabulary #
# --------------------------------------------------------------------------- #
def _unreasoned_broad_handlers(source: str, filename: str) -> list[str]:
    """``file:line`` of every broad handler whose ``except`` line lacks a vocabulary reason."""
    lines = source.splitlines()
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler) and _is_broad(node):
            match = _NOQA.search(lines[node.lineno - 1])
            if match is None or match.group("reason") not in BLE_REASONS:
                offenders.append(f"{filename}:{node.lineno}")
    return offenders


def _layer_handlers() -> tuple[int, list[str]]:
    total = 0
    offenders: list[str] = []
    for root in LAYER_ROOTS:
        for path in _py_files(root):
            source = path.read_text(encoding="utf-8")
            total += sum(1 for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler) and _is_broad(n))
            offenders += _unreasoned_broad_handlers(source, str(path.relative_to(_REPO)))
    return total, offenders


class TestRuleF_ReasonedBroadExcepts:
    def test_every_broad_handler_in_the_layers_carries_a_vocabulary_reason(self) -> None:
        total, offenders = _layer_handlers()
        assert total >= 30, f"non-vacuity: the sweep saw only {total} broad handlers"
        assert offenders == [], f"annotate with `# noqa: BLE001 — <reason>` from BLE_REASONS: {offenders}"

    def test_every_vocabulary_reason_is_in_use(self) -> None:
        """A reason nothing uses is dead vocabulary — delete it rather than let it invite drift."""
        used = {
            m.group("reason")
            for root in LAYER_ROOTS
            for p in _py_files(root)
            for line in p.read_text(encoding="utf-8").splitlines()
            if (m := _NOQA.search(line))
        }
        assert set(BLE_REASONS) <= used, sorted(set(BLE_REASONS) - used)

    @pytest.mark.parametrize(
        "line",
        [
            "    except Exception:",
            "    except Exception:  # noqa: BLE001",
            "    except Exception:  # noqa: BLE001 - total by contract",
            "    except Exception:  # noqa: BLE001 — because I said so",
            "    except Exception:  # noqa: BLE001 — totally by contract",
            "    except BaseException:  # noqa: E722",
            "    except:",
        ],
    )
    def test_detector_self_test_an_unreasoned_handler_is_red(self, line: str) -> None:
        source = f"def f():\n    try:\n        pass\n{line}\n        pass\n"
        assert _unreasoned_broad_handlers(source, "x.py") == ["x.py:4"]

    @pytest.mark.parametrize(
        "line",
        [
            "    except Exception:  # noqa: BLE001 — total by contract",
            "    except Exception as exc:  # noqa: BLE001 — re-raised; logs which CSV failed first",
            "    except Exception as exc:  # noqa: BLE001 — entity bulkhead, failure-policy §2",
            "    except ValueError:",
        ],
    )
    def test_detector_twin_a_reasoned_or_narrow_handler_passes(self, line: str) -> None:
        source = f"def f():\n    try:\n        pass\n{line}\n        pass\n"
        assert _unreasoned_broad_handlers(source, "x.py") == []


# --------------------------------------------------------------------------- #
# (g) BaseTransformer grows no new members                                      #
# --------------------------------------------------------------------------- #
BASE_TRANSFORMER_FILE = "src/etl/transformers/base.py"

#: The frozen member set of ``BaseTransformer``'s class body (methods and class attributes),
#: as of plan 0053 S13a. Adding a name here needs the reason a composed module will not do.
BASE_TRANSFORMER_SURFACE: frozenset[str] = frozenset(
    {
        "ALLOWED_TRANSFORMS",
        "CEDS_MAPPING",
        "DEFAULT_ACTIVE_VALUES",
        "DEFAULT_STATUS_COLUMN_ALIASES",
        "DEFAULT_WITHDRAW_DATE_COLUMN",
        "NO_STAFF_ROLE",
        "SOURCE_COLUMN_ROLES",
        "STAFF_ROLES",
        "STAFF_ROLE_ADMINISTRATOR",
        "STAFF_ROLE_TEACHER",
        "_apply_transform_resilient",
        "_classify_withdraw",
        "_configured_but_blank",
        "_configured_status_label",
        "_enroll_status_block",
        "_parse_school_year_to_end",
        "_record_data_error",
        "_status_column_configured",
        "apply_field_map",
        "apply_row_filters",
        "assign_class_ids",
        "clean_course_code_flavor",
        "clean_invalid_ids",
        "compute_enroll_status",
        "decide_enroll_status",
        "derive_date_part",
        "determine_school_year",
        "determine_school_year_detailed",
        "early_grade_exclusion_pattern",
        "effective_course_code_patterns",
        "filter_excluded_course_code_patterns",
        "filter_excluded_course_codes",
        "filter_to_active",
        "format_date",
        "friendly_date_format_to_strftime",
        "generate_class_id",
        "generate_class_name",
        "generate_student_email",
        "generate_user_id",
        "generate_user_role",
        "get_source_file",
        "grade_to_ceds",
        "is_active_mask",
        "map_role",
        "normalize_columns",
        "normalize_iso_date",
        "normalize_source_config",
        "normalize_staff_role",
        "past_withdraw_date",
        "resolve_active_config",
        "resolve_date",
        "transform",
        "truncate_name",
    }
)


def _class_members(source: str, class_name: str) -> set[str]:
    classes = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef) and n.name == class_name]
    assert len(classes) == 1, f"{class_name} not found exactly once — the pin would be vacuous"
    members: set[str] = set()
    for node in classes[0].body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            members.add(node.name)
        elif isinstance(node, ast.Assign):
            members.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)
    return members


class TestRuleG_BaseTransformerGrowsNoMembers:
    def test_the_member_set_equals_the_frozen_registry(self) -> None:
        members = _class_members(_path(BASE_TRANSFORMER_FILE).read_text(encoding="utf-8"), "BaseTransformer")
        assert len(members) >= 40, "non-vacuity: the class body was read"
        assert sorted(members - BASE_TRANSFORMER_SURFACE) == [], "put new shared behaviour in a composed module"
        assert sorted(BASE_TRANSFORMER_SURFACE - members) == [], "a member was deleted — drop it from the registry"

    def test_detector_self_test_a_new_method_or_attribute_is_seen(self) -> None:
        source = (
            "class BaseTransformer:\n"
            "    LIMIT = 3\n"
            "    ratio: float = 0.5\n"
            "    def transform(self): ...\n"
            "    async def resolve_column(self): ...\n"
        )
        members = _class_members(source, "BaseTransformer")
        assert members - BASE_TRANSFORMER_SURFACE == {"LIMIT", "ratio", "resolve_column"}

    def test_the_twin_a_missing_class_is_loud(self) -> None:
        with pytest.raises(AssertionError, match="not found"):
            _class_members("class Other:\n    pass\n", "BaseTransformer")


# --------------------------------------------------------------------------- #
# failure-policy.md §11 ``ble-reasons`` table == BLE_REASONS                     #
# --------------------------------------------------------------------------- #
def _ble_reason_mismatches(text: str) -> list[str]:
    rows = _table(text, "ble-reasons")
    if not rows:
        return ["the ble-reasons table was not found"]
    documented = [row.get("reason", "").replace("`", "").strip() for row in rows]
    problems = [f"documented twice: {r}" for r in sorted({r for r in documented if documented.count(r) > 1})]
    problems += [f"not documented: {r}" for r in BLE_REASONS if r not in documented]
    problems += [f"not in BLE_REASONS: {r}" for r in documented if r not in BLE_REASONS]
    return problems


class TestTheReasonVocabularyIsDocumented:
    def test_the_doc_table_equals_the_vocabulary(self) -> None:
        text = _doc_text()
        assert len(_table(text, "ble-reasons")) == len(BLE_REASONS), "non-vacuity: every row parsed"
        assert _ble_reason_mismatches(text) == []

    def test_doctored_a_removed_row_is_red(self) -> None:
        text = _doc_text()
        doctored = re.sub(r"^\| `best-effort side effect` \|.*\n", "", text, count=1, flags=re.MULTILINE)
        assert doctored != text
        assert _ble_reason_mismatches(doctored) == ["not documented: best-effort side effect"]

    def test_doctored_an_invented_row_is_red(self) -> None:
        marker = "<!-- failure-policy-table: ble-reasons -->\n| reason | when it applies |\n|---|---|\n"
        text = _doc_text()
        assert marker in text
        doctored = text.replace(marker, marker + "| `because` | planted |\n", 1)
        assert _ble_reason_mismatches(doctored) == ["not in BLE_REASONS: because"]

    def test_doctored_a_missing_marker_is_red(self) -> None:
        doctored = _doc_text().replace("<!-- failure-policy-table: ble-reasons -->", "")
        assert _ble_reason_mismatches(doctored) == ["the ble-reasons table was not found"]


def test_every_declared_registry_path_exists() -> None:
    """One loud list of every registry target, so a rename names every rule it breaks."""
    declared = [
        *LAYER_ROOTS,
        *FLET_FREE_MODULES,
        *FLET_BOUND,
        TRANSFORMERS_DIR,
        PIPELINE,
        FIELD_MAP_ENGINE_FILE,
        BASE_TRANSFORMER_FILE,
        *(rel for rel, _ in CLASSIFIERS),
    ]
    missing = [rel for rel in declared if not (_REPO / rel).exists()]
    assert missing == []


def test_twin_a_missing_registry_path_fails_loudly() -> None:
    with pytest.raises(AssertionError, match="does not exist"):
        _path("src/etl/no_such_module.py")
