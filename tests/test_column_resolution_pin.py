"""AST pins for the ONE column resolver (plan 0053 S9 — ``failure-policy.md`` §9, P10).

Over every module in ``src/etl/transformers`` except ``columns.py`` itself:

1. **No ad-hoc resolve-with-default read** — no ``<x>.get(<str>, <str>)`` whose result is
   ``.lower()``-ed, directly or through ``str(...)`` / ``.strip()`` (the
   ``.get("Homeroom", "homeroom").lower()`` idiom that broke on a ``{column: …}`` entry
   and ignored the district's mapping).
2. **No read of a field_map entry's literal ``"column"``** — neither ``<x>["column"]``
   nor ``<x>.get("column", …)``. Allowlisted by (module, function) with a reason, each
   of which reads a DIFFERENT model's ``column`` key, never a field_map entry.
3. **The three retired resolvers stay deleted** (``resolve_column``,
   ``_student_number_col``, ``_field_map_source``).

Each pin is proven non-vacuous: the walker FINDS a violation in a doctored snippet, and
every allowlist entry must still match a real read (a stale entry is red).
"""

import ast
from pathlib import Path

import pytest

TRANSFORMERS = Path("src/etl/transformers")
RESOLVER_MODULE = TRANSFORMERS / "columns.py"
RETIRED_RESOLVERS = frozenset({"resolve_column", "_student_number_col", "_field_map_source"})

#: (module, enclosing function) → why its literal ``"column"`` read is not a field_map entry.
COLUMN_KEY_ALLOWLIST: dict[tuple[str, str], str] = {
    ("base.py", "apply_row_filters"): (
        "a `row_filters` entry (RowFilter.model_dump: {column, include}) — the filter's own "
        "schema, not a field_map entry"
    ),
    ("students.py", "_generate_emails"): (
        "an email `derived_dates` spec (EmailDerivedDate: {column, date_format}) — a date "
        "source inside the email template config, not a field_map entry"
    ),
}


def _modules() -> list[Path]:
    return sorted(p for p in TRANSFORMERS.glob("*.py") if p != RESOLVER_MODULE)


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _unwrap(node: ast.AST) -> ast.AST:
    """Peel ``str(x)`` and ``x.strip()`` wrappers off the receiver of a ``.lower()``."""
    while True:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "strip"
            and not node.args
        ):
            node = node.func.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
            node = node.args[0]
        else:
            return node


def lowered_literal_gets(tree: ast.AST) -> list[int]:
    """Line numbers of ``<x>.get(<str>, <str>)`` reads whose result is lower-cased."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "lower":
            receiver = _unwrap(node.func.value)
            if (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "get"
                and len(receiver.args) == 2
                and all(_is_str(arg) for arg in receiver.args)
            ):
                hits.append(node.lineno)
    return hits


def literal_column_reads(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of every ``<x>["column"]`` / ``<x>.get("column", …)``."""
    hits: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Subscript) and _is_str(node.slice) and node.slice.value == "column":
            hits.append((function, node.lineno))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and _is_str(node.args[0])
            and node.args[0].value == "column"
        ):
            hits.append((function, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return hits


def retired_definitions(tree: ast.AST) -> set[str]:
    """Names of the retired resolvers DEFINED (as a function or method) in ``tree``."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in RETIRED_RESOLVERS
    }


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestTheSweepReadsTheTransformers:
    def test_the_swept_modules_are_the_transformers_minus_the_resolver(self):
        """Non-vacuity of the file discovery itself: a wrong working directory or a moved
        package would glob NOTHING, and every sweep below would pass on zero files."""
        names = {p.name for p in _modules()}
        assert {"base.py", "blended.py", "classes.py", "context.py", "enrollments.py", "students.py"} <= names
        assert "columns.py" not in names
        assert RESOLVER_MODULE.is_file()


class TestNoAdHocResolveWithDefault:
    def test_no_transformer_lower_cases_a_literal_get(self):
        offenders = {p.name: lines for p in _modules() if (lines := lowered_literal_gets(_parse(p)))}
        assert not offenders, (
            f"Ad-hoc `.get(<key>, <default>).lower()` column reads {offenders} — resolve the column "
            f"through `columns.resolve_source_column` (failure-policy §9)."
        )

    @pytest.mark.parametrize(
        "snippet",
        [
            'homeroom_col = fm.get("Homeroom", "homeroom").lower()',
            'col = str(cfg.get("staff_id_col", "teacher id")).lower()',
            'col = cfg.get("course title", "title").strip().lower()',
            'col = str(cfg.get("column", "grade")).strip().lower()',
        ],
    )
    def test_twin_the_walker_catches_every_wrapped_form(self, snippet):
        assert lowered_literal_gets(ast.parse(snippet)) == [1]

    @pytest.mark.parametrize(
        "snippet",
        [
            'x = fm.get("Homeroom").lower()',  # one argument: not a resolve-with-default
            'x = fm.get(key, "homeroom").lower()',  # a variable key
            'x = fm.get("Homeroom", {})',  # never lower-cased
        ],
    )
    def test_twin_it_does_not_flag_what_is_not_the_idiom(self, snippet):
        assert lowered_literal_gets(ast.parse(snippet)) == []

    def test_the_walker_sees_the_resolver_modules_own_legacy_rule(self):
        """Positive case on REAL source: `columns._previous_answer` spells the retired
        resolver verbatim (`str(raw.get("column", default)).lower()`) — its default is a
        NAME, so the pin must not fire there; the literal-default form does fire."""
        assert lowered_literal_gets(_parse(RESOLVER_MODULE)) == []
        assert lowered_literal_gets(ast.parse('str(raw.get("column", "grade")).lower()')) == [1]


class TestNoLiteralColumnKeyOutsideTheResolver:
    def test_only_allowlisted_functions_read_a_literal_column_key(self):
        offenders = []
        for path in _modules():
            for function, line in literal_column_reads(_parse(path)):
                if (path.name, function) not in COLUMN_KEY_ALLOWLIST:
                    offenders.append(f"{path.name}:{line} ({function})")
        assert not offenders, (
            f"A field_map entry's `column` is read outside `columns.py`: {offenders}. Resolve it "
            f"through `columns.resolve_source_column`, or — if it is another model's `column` key "
            f"— add an allowlist entry with the reason."
        )

    def test_every_allowlist_entry_still_matches_a_real_read(self):
        """Non-vacuity on real source, and no stale entry."""
        found = {(p.name, function) for p in _modules() for function, _ in literal_column_reads(_parse(p))}
        assert set(COLUMN_KEY_ALLOWLIST) <= found, set(COLUMN_KEY_ALLOWLIST) - found
        assert all(reason.strip() for reason in COLUMN_KEY_ALLOWLIST.values())

    def test_the_resolver_module_is_where_the_key_is_read(self):
        """`columns.py` is excluded from the sweep, not blind to it: it does read the key."""
        assert literal_column_reads(_parse(RESOLVER_MODULE))

    @pytest.mark.parametrize(
        "snippet",
        [
            'def f(cfg):\n    return cfg["column"]\n',
            'def f(cfg):\n    return cfg.get("column", "grade")\n',
            'def f(cfg):\n    return str(cfg.get("column")).lower()\n',
        ],
    )
    def test_twin_the_walker_catches_a_doctored_read(self, snippet):
        assert literal_column_reads(ast.parse(snippet)) == [("f", 2)]


class TestTheRetiredResolversStayDeleted:
    def test_no_module_under_src_defines_them(self):
        sources = sorted(Path("src").rglob("*.py"))
        assert len(sources) > 50  # the sweep reads the real tree, not an empty glob
        defined = {(str(path), name) for path in sources for name in retired_definitions(_parse(path))}
        assert not defined, f"A retired resolver is back: {sorted(defined)} — use columns.resolve_source_column."

    def test_twin_the_sweep_finds_a_definition(self):
        tree = ast.parse(
            "class B:\n    @staticmethod\n    def resolve_column(fm, key, default):\n        return default\n"
            "def _student_number_col(fm):\n    return 'student number'\n"
        )
        assert retired_definitions(tree) == {"resolve_column", "_student_number_col"}
